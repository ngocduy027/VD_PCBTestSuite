from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from threading import Event, Lock, Thread
from time import monotonic, sleep

try:
    import serial
    from serial import SerialException
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - exercised only in broken installs
    serial = None
    SerialException = OSError
    list_ports = None

from pcbt_app.protocol.mit_firmware import RS485_STRESS_DEFAULT_BAUD
from pcbt_app.transport.base import CanTransportError


RS485_FRAME_LENGTH = 10
RS485_FRAME_PATTERN = b"\xAA" * 5


class Rs485TransportError(CanTransportError):
    """Raised when the raw RS485 serial transport fails."""


def require_pyserial() -> None:
    if serial is None:
        raise Rs485TransportError("pyserial missing; install pyserial>=3.5 to use RS485 serial")


def list_rs485_com_ports() -> list[tuple[str, str]]:
    require_pyserial()
    assert list_ports is not None
    return [(port.device, port.description) for port in list_ports.comports()]


class FixedLengthRs485Parser:
    """Legacy fixed-size chunker retained for non-FCT callers."""

    def __init__(self, frame_length: int) -> None:
        if frame_length <= 0:
            raise ValueError("frame_length must be positive")
        self.frame_length = frame_length
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        frames: list[bytes] = []
        while len(self._buffer) >= self.frame_length:
            frames.append(bytes(self._buffer[: self.frame_length]))
            del self._buffer[: self.frame_length]
        return frames

    def clear(self) -> None:
        self._buffer.clear()


@dataclass(slots=True, frozen=True)
class Rs485ParserSnapshot:
    valid_frame_count: int
    malformed_count: int
    sequence_error_count: int
    first_sequence: int | None
    last_sequence: int | None
    expected_sequence: int
    first_sequence_error: tuple[int, int] | None
    buffer_length: int
    received_byte_count: int
    transport_error: str | None = None


class FctRs485StreamParser:
    """Streaming parser for the firmware's 10-byte FCT RS485 frame."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.reset()

    def reset(self) -> None:
        self._buffer.clear()
        self.valid_frame_count = 0
        self.malformed_count = 0
        self.sequence_error_count = 0
        self.first_sequence: int | None = None
        self.last_sequence: int | None = None
        self.expected_sequence = 0
        self.first_sequence_error: tuple[int, int] | None = None
        self.received_byte_count = 0

    def feed(self, data: bytes) -> None:
        self.received_byte_count += len(data)
        self._buffer.extend(data)
        cursor = 0
        while cursor < len(self._buffer):
            header_index = self._buffer.find(b"\x9A", cursor)
            if header_index < 0:
                self.malformed_count += 1
                cursor = len(self._buffer)
                break
            if header_index > cursor:
                self.malformed_count += 1
                cursor = header_index
            if len(self._buffer) - cursor < RS485_FRAME_LENGTH:
                break
            candidate = self._buffer[cursor : cursor + RS485_FRAME_LENGTH]
            if candidate[4:9] != RS485_FRAME_PATTERN or candidate[9] != _xor(candidate[:9]):
                self.malformed_count += 1
                cursor += 1
                continue
            sequence = int.from_bytes(candidate[1:4], "little")
            self.valid_frame_count += 1
            if self.first_sequence is None:
                self.first_sequence = sequence
            self.last_sequence = sequence
            if sequence != self.expected_sequence:
                self.sequence_error_count += 1
                if self.first_sequence_error is None:
                    self.first_sequence_error = (self.expected_sequence, sequence)
            self.expected_sequence = sequence + 1
            cursor += RS485_FRAME_LENGTH
        if cursor:
            del self._buffer[:cursor]

    def finalize(self) -> None:
        if self._buffer:
            self.malformed_count += 1
            self._buffer.clear()

    def snapshot(self, transport_error: str | None = None) -> Rs485ParserSnapshot:
        return Rs485ParserSnapshot(
            valid_frame_count=self.valid_frame_count,
            malformed_count=self.malformed_count,
            sequence_error_count=self.sequence_error_count,
            first_sequence=self.first_sequence,
            last_sequence=self.last_sequence,
            expected_sequence=self.expected_sequence,
            first_sequence_error=self.first_sequence_error,
            buffer_length=len(self._buffer),
            received_byte_count=self.received_byte_count,
            transport_error=transport_error,
        )


def _xor(data: bytes | bytearray) -> int:
    value = 0
    for byte in data:
        value ^= byte
    return value


@dataclass(slots=True)
class SerialRs485Transport:
    channel: str
    baudrate: int = RS485_STRESS_DEFAULT_BAUD
    frame_length: int = RS485_FRAME_LENGTH
    _serial: object | None = None
    _parser: FixedLengthRs485Parser = field(init=False)
    _stress_parser: FctRs485StreamParser = field(init=False)
    _pending_frames: deque[bytes] = field(default_factory=deque)
    _reader_stop: Event = field(default_factory=Event)
    _reader_thread: Thread | None = None
    _lock: Lock = field(default_factory=Lock)
    _capture_active: bool = False
    _reader_error: str | None = None

    def __post_init__(self) -> None:
        self._parser = FixedLengthRs485Parser(self.frame_length)
        self._stress_parser = FctRs485StreamParser()

    def connect(self) -> None:
        require_pyserial()
        if self._serial is not None:
            return
        if self.baudrate != RS485_STRESS_DEFAULT_BAUD:
            raise Rs485TransportError("FCT RS485 requires 2500000 baud")
        if self.channel not in [device for device, _ in list_rs485_com_ports()]:
            raise Rs485TransportError(f"RS485 COM port not found: {self.channel}")
        try:
            assert serial is not None
            self._serial = serial.Serial(
                port=self.channel,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0,
                write_timeout=1.0,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
            self._reader_stop.clear()
            self._reader_error = None
            self._reader_thread = Thread(target=self._reader_loop, name="rs485-reader", daemon=True)
            self._reader_thread.start()
        except SerialException as exc:
            self._serial = None
            message = str(exc)
            if "Access is denied" in message or "PermissionError" in message:
                raise Rs485TransportError(f"RS485 COM port already in use: {self.channel}") from exc
            raise Rs485TransportError(f"Failed to open RS485 serial on {self.channel}: {message}") from exc

    def disconnect(self) -> None:
        serial_port = self._serial
        if serial_port is None:
            return
        self._reader_stop.set()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1.0)
        try:
            serial_port.close()
        finally:
            with self._lock:
                self._serial = None
                self._reader_thread = None
                self._capture_active = False
                self._parser.clear()
                self._stress_parser.reset()
                self._pending_frames.clear()

    def is_connected(self) -> bool:
        return self._serial is not None and bool(getattr(self._serial, "is_open", False))

    def begin_capture(self) -> None:
        if not self.is_connected():
            raise Rs485TransportError("RS485 serial is not connected")
        with self._lock:
            assert self._serial is not None
            self._serial.reset_input_buffer()
            self._parser.clear()
            self._pending_frames.clear()
            self._stress_parser.reset()
            self._reader_error = None
            self._capture_active = True

    def capture_snapshot(self) -> Rs485ParserSnapshot:
        with self._lock:
            return self._stress_parser.snapshot(self._reader_error)

    def finish_capture(self) -> Rs485ParserSnapshot:
        with self._lock:
            self._capture_active = False
            self._stress_parser.finalize()
            return self._stress_parser.snapshot(self._reader_error)

    def recv(self, timeout_s: float = 0.0) -> bytes | None:
        if not self.is_connected():
            raise Rs485TransportError("RS485 serial is not connected")
        deadline = monotonic() + max(0.0, timeout_s)
        while True:
            with self._lock:
                if self._pending_frames:
                    return self._pending_frames.popleft()
                reader_error = self._reader_error
            if reader_error:
                raise Rs485TransportError(reader_error)
            if timeout_s <= 0 or monotonic() >= deadline:
                return None
            sleep(min(0.002, max(0.0, deadline - monotonic())))

    def drain(self) -> list[bytes]:
        drained: list[bytes] = []
        while True:
            frame = self.recv(timeout_s=0.0)
            if frame is None:
                return drained
            drained.append(frame)

    def _reader_loop(self) -> None:
        while not self._reader_stop.is_set():
            try:
                with self._lock:
                    serial_port = self._serial
                    if serial_port is None:
                        return
                    waiting = int(getattr(serial_port, "in_waiting", 0))
                    chunk = serial_port.read(max(1, waiting))
                    if chunk:
                        if self._capture_active:
                            self._stress_parser.feed(bytes(chunk))
            except (SerialException, OSError) as exc:
                with self._lock:
                    self._reader_error = f"Failed to receive RS485 serial data: {exc}"
                return
            if not chunk:
                sleep(0.001)
