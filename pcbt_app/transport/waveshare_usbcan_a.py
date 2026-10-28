from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from time import monotonic, sleep

try:
    import serial
    from serial import SerialException
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - exercised only in broken installs
    serial = None
    SerialException = OSError
    list_ports = None

from pcbt_app.config import DEFAULT_SERIAL_BAUD
from pcbt_app.models import RawCanFrame
from pcbt_app.transport.base import AbstractCanTransport, CanTransportError


WAVESHARE_FIXED_LENGTH_PROTOCOL = 0x02
WAVESHARE_EXTENDED_FRAME = 0x02
WAVESHARE_NORMAL_MODE = 0x00
WAVESHARE_DISABLE_AUTO_RETRANSMIT = 0x01

WAVESHARE_CAN_BITRATE_CODES = {
    1_000_000: 0x01,
    800_000: 0x02,
    500_000: 0x03,
    400_000: 0x04,
    250_000: 0x05,
    200_000: 0x06,
    125_000: 0x07,
    100_000: 0x08,
    50_000: 0x09,
    20_000: 0x0A,
    10_000: 0x0B,
    5_000: 0x0C,
}


class WaveshareProtocolError(CanTransportError):
    """Raised for malformed Waveshare serial protocol data."""


@dataclass(slots=True, frozen=True)
class WaveshareParserStats:
    valid_packets: int = 0
    decoded_frames: int = 0
    malformed_candidates: int = 0
    discarded_bytes: int = 0
    checksum_errors: int = 0
    framing_errors: int = 0
    invalid_dlc_errors: int = 0
    unsupported_frames: int = 0
    max_consecutive_malformed: int = 0

    def summary(self) -> str | None:
        if self.malformed_candidates == 0:
            return None
        return (
            "Waveshare stream recovered: "
            f"{self.malformed_candidates} malformed candidates, "
            f"{self.discarded_bytes} bytes discarded, "
            f"checksum={self.checksum_errors}, framing={self.framing_errors}, "
            f"invalid_dlc={self.invalid_dlc_errors}, "
            f"unsupported={self.unsupported_frames}, "
            f"valid_packets={self.valid_packets}, decoded_frames={self.decoded_frames}"
        )


def require_pyserial() -> None:
    if serial is None:
        raise CanTransportError("pyserial missing; install pyserial>=3.5 to use Waveshare USB-CAN-A")


def supported_bitrates_text() -> str:
    return ", ".join(str(rate) for rate in sorted(WAVESHARE_CAN_BITRATE_CODES, reverse=True))


def build_waveshare_config_packet(can_bitrate: int) -> bytes:
    try:
        bitrate_code = WAVESHARE_CAN_BITRATE_CODES[can_bitrate]
    except KeyError as exc:
        raise CanTransportError(
            f"Unsupported CAN bitrate {can_bitrate} for Waveshare USB-CAN-A. "
            f"Supported values: {supported_bitrates_text()}"
        ) from exc

    packet = bytearray(20)
    packet[0] = 0xAA
    packet[1] = 0x55
    packet[2] = WAVESHARE_FIXED_LENGTH_PROTOCOL
    packet[3] = bitrate_code
    packet[4] = WAVESHARE_EXTENDED_FRAME
    packet[13] = WAVESHARE_NORMAL_MODE
    packet[14] = WAVESHARE_DISABLE_AUTO_RETRANSMIT
    packet[19] = sum(packet[2:19]) & 0xFF
    return bytes(packet)


def format_waveshare_packet(packet: bytes) -> str:
    return packet.hex().upper()


def encode_waveshare_can_frame(frame: RawCanFrame) -> bytes:
    if len(frame.data) > 8:
        raise WaveshareProtocolError("Waveshare USB-CAN-A can encode at most 8 payload bytes")
    if not frame.is_extended_id:
        raise WaveshareProtocolError("PCB test commands require extended CAN frames")
    if not 0 <= frame.arbitration_id <= 0x1FFFFFFF:
        raise WaveshareProtocolError(f"Extended arbitration ID out of range: 0x{frame.arbitration_id:X}")

    packet = bytearray(20)
    packet[0] = 0xAA
    packet[1] = 0x55
    packet[2] = 0x01
    packet[3] = WAVESHARE_EXTENDED_FRAME
    packet[4] = 0x01
    packet[5:9] = frame.arbitration_id.to_bytes(4, "little")
    packet[9] = 0x08
    packet[10 : 10 + len(frame.data)] = frame.data
    packet[18] = 0x00
    packet[19] = sum(packet[2:19]) & 0xFF
    return bytes(packet)


class WaveshareStreamParser:
    def __init__(self) -> None:
        self._buffer = bytearray()
        self._stats = WaveshareParserStats()
        self._consecutive_malformed = 0

    def reset(self) -> None:
        self._buffer.clear()
        self._stats = WaveshareParserStats()
        self._consecutive_malformed = 0

    def take_stats(self) -> WaveshareParserStats:
        stats = self._stats
        self._stats = WaveshareParserStats()
        self._consecutive_malformed = 0
        return stats

    def feed(self, data: bytes) -> list[RawCanFrame]:
        self._buffer.extend(data)
        frames: list[RawCanFrame] = []

        while True:
            start = self._buffer.find(0xAA)
            if start < 0:
                if self._buffer:
                    self._mark_malformed("framing", len(self._buffer))
                    self._buffer.clear()
                return frames
            if start:
                del self._buffer[:start]
                self._mark_malformed("framing", start)

            if len(self._buffer) < 2:
                return frames

            if self._buffer[0] == 0xAA and self._buffer[1] == 0x55:
                if len(self._buffer) < 20:
                    return frames
                packet = bytes(self._buffer[:20])
                if (sum(packet[2:19]) & 0xFF) != packet[19]:
                    self._mark_malformed("checksum", 1)
                    del self._buffer[:1]
                    continue
                if packet[3] != WAVESHARE_EXTENDED_FRAME:
                    del self._buffer[:20]
                    self._record_unsupported_packet()
                    continue
                dlc = packet[9]
                if dlc > 8:
                    self._mark_malformed("invalid_dlc", 1)
                    del self._buffer[:1]
                    continue
                del self._buffer[:20]
                raw_id = int.from_bytes(packet[5:9], "little") & 0x1FFFFFFF
                payload = packet[10 : 10 + dlc]
                frames.append(RawCanFrame(arbitration_id=raw_id, data=payload, is_extended_id=True))
                self._record_valid_packet(decoded=True)
                continue

            frame_type = self._buffer[1]
            if (frame_type & 0xC0) != 0xC0:
                self._mark_malformed("framing", 1)
                del self._buffer[0]
                continue

            dlc = frame_type & 0x0F
            if dlc > 8:
                self._mark_malformed("invalid_dlc", 1)
                del self._buffer[0]
                continue

            is_extended = bool(frame_type & 0x20)
            is_remote = bool(frame_type & 0x10)
            id_len = 4 if is_extended else 2
            packet_len = 2 + id_len + dlc + 1
            if len(self._buffer) < packet_len:
                return frames

            packet = bytes(self._buffer[:packet_len])

            if packet[-1] != 0x55:
                self._mark_malformed("framing", 1)
                del self._buffer[:1]
                continue
            del self._buffer[:packet_len]
            if is_remote:
                self._record_valid_packet()
                continue

            raw_id = int.from_bytes(packet[2 : 2 + id_len], "little")
            if is_extended:
                raw_id &= 0x1FFFFFFF
            else:
                raw_id &= 0x7FF
            payload = packet[2 + id_len : -1]
            frames.append(RawCanFrame(arbitration_id=raw_id, data=payload, is_extended_id=is_extended))
            self._record_valid_packet(decoded=True)

    def _mark_malformed(self, kind: str, discarded_bytes: int = 0) -> None:
        stats = self._stats
        counters = {
            "valid_packets": stats.valid_packets,
            "decoded_frames": stats.decoded_frames,
            "malformed_candidates": stats.malformed_candidates + 1,
            "discarded_bytes": stats.discarded_bytes + discarded_bytes,
            "checksum_errors": stats.checksum_errors + int(kind == "checksum"),
            "framing_errors": stats.framing_errors + int(kind == "framing"),
            "invalid_dlc_errors": stats.invalid_dlc_errors + int(kind == "invalid_dlc"),
            "unsupported_frames": stats.unsupported_frames,
            "max_consecutive_malformed": max(stats.max_consecutive_malformed, self._consecutive_malformed + 1),
        }
        self._stats = WaveshareParserStats(**counters)
        self._consecutive_malformed += 1

    def _record_valid_packet(self, decoded: bool = False) -> None:
        stats = self._stats
        self._stats = WaveshareParserStats(
            valid_packets=stats.valid_packets + 1,
            decoded_frames=stats.decoded_frames + int(decoded),
            malformed_candidates=stats.malformed_candidates,
            discarded_bytes=stats.discarded_bytes,
            checksum_errors=stats.checksum_errors,
            framing_errors=stats.framing_errors,
            invalid_dlc_errors=stats.invalid_dlc_errors,
            unsupported_frames=stats.unsupported_frames,
            max_consecutive_malformed=stats.max_consecutive_malformed,
        )
        self._consecutive_malformed = 0

    def _record_unsupported_packet(self) -> None:
        stats = self._stats
        self._stats = WaveshareParserStats(
            valid_packets=stats.valid_packets + 1,
            decoded_frames=stats.decoded_frames,
            malformed_candidates=stats.malformed_candidates,
            discarded_bytes=stats.discarded_bytes,
            checksum_errors=stats.checksum_errors,
            framing_errors=stats.framing_errors,
            invalid_dlc_errors=stats.invalid_dlc_errors,
            unsupported_frames=stats.unsupported_frames + 1,
            max_consecutive_malformed=stats.max_consecutive_malformed,
        )
        self._consecutive_malformed = 0


def list_waveshare_com_ports() -> list[tuple[str, str]]:
    require_pyserial()
    assert list_ports is not None
    return [(port.device, port.description) for port in list_ports.comports()]


@dataclass(slots=True)
class WaveshareUsbCanATransport(AbstractCanTransport):
    channel: str
    can_bitrate: int
    serial_baud: int = DEFAULT_SERIAL_BAUD
    _serial: object | None = None
    _parser: WaveshareStreamParser = field(default_factory=WaveshareStreamParser)
    _pending_frames: deque[RawCanFrame] = field(default_factory=deque)

    def connect(self) -> None:
        require_pyserial()
        if self._serial is not None:
            return

        if self.channel not in [device for device, _ in list_waveshare_com_ports()]:
            raise CanTransportError(f"COM port not found: {self.channel}")

        try:
            assert serial is not None
            self._serial = serial.Serial(
                port=self.channel,
                baudrate=self.serial_baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0,
                write_timeout=1.0,
            )
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
            self.reset_stream()
            self.configure_adapter()
        except SerialException as exc:
            self._serial = None
            message = str(exc)
            if "Access is denied" in message or "PermissionError" in message:
                raise CanTransportError(f"COM port already in use: {self.channel}") from exc
            raise CanTransportError(f"Failed to open Waveshare USB-CAN-A on {self.channel}: {message}") from exc

    def configure_adapter(self) -> bytes:
        if self._serial is None:
            raise CanTransportError("CAN adapter is not connected")
        config_packet = build_waveshare_config_packet(self.can_bitrate)
        try:
            self._serial.write(config_packet)
            self._serial.flush()
        except SerialException as exc:
            raise CanTransportError(f"Failed to configure Waveshare USB-CAN-A: {exc}") from exc
        return config_packet

    def disconnect(self) -> None:
        if self._serial is None:
            self.reset_stream()
            return
        try:
            self._serial.close()
        finally:
            self._serial = None
            self.reset_stream()

    def reset_stream(self) -> None:
        self._parser.reset()
        self._pending_frames.clear()

    def take_parser_stats(self) -> WaveshareParserStats:
        return self._parser.take_stats()

    def is_connected(self) -> bool:
        return self._serial is not None and bool(getattr(self._serial, "is_open", False))

    def send(self, frame: RawCanFrame) -> None:
        if self._serial is None:
            raise CanTransportError("CAN adapter is not connected")
        try:
            self._serial.write(encode_waveshare_can_frame(frame))
            self._serial.flush()
        except SerialException as exc:
            raise CanTransportError(f"Failed to send Waveshare USB-CAN-A frame: {exc}") from exc

    def recv(self, timeout_s: float) -> RawCanFrame | None:
        if self._serial is None:
            raise CanTransportError("CAN adapter is not connected")
        if self._pending_frames:
            return self._pending_frames.popleft()

        deadline = monotonic() + max(0.0, timeout_s)
        while True:
            waiting = int(getattr(self._serial, "in_waiting", 0))
            read_size = max(1, waiting)
            try:
                chunk = self._serial.read(read_size)
            except SerialException as exc:
                raise CanTransportError(f"Failed to receive Waveshare USB-CAN-A data: {exc}") from exc

            if chunk:
                frames = self._parser.feed(bytes(chunk))
                self._pending_frames.extend(frames)
                if self._pending_frames:
                    return self._pending_frames.popleft()

            if timeout_s <= 0 or monotonic() >= deadline:
                return None
            sleep(min(0.005, max(0.0, deadline - monotonic())))

    def drain(self) -> list[RawCanFrame]:
        drained: list[RawCanFrame] = []
        while True:
            frame = self.recv(timeout_s=0.0)
            if frame is None:
                return drained
            drained.append(frame)
