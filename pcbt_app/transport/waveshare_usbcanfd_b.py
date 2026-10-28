from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from math import ceil
import re
from pathlib import Path
from time import monotonic
from typing import Callable, Protocol

from pcbt_app.models import RawCanFrame
from pcbt_app.transport.Communication_CAN_CANFD_ModelB import ModelBSerialProxy
from pcbt_app.transport.base import AbstractCanTransport, CanTransportError
from pcbt_app.transport.waveshare_usbcan_a import (
    WaveshareStreamParser,
    build_waveshare_config_packet,
    encode_waveshare_can_frame,
)


MODEL_B_CHANNEL = "USB-CAN-FD-B Device 0 / CAN2"


def scan_model_b_devices() -> list[tuple[int, str]]:
    """Probe indices 0..63; never initialize or start a CAN channel."""
    import ctypes
    from pcbt_app.transport.Communication_CAN_CANFD_ModelB import ZCAN_DEVICE_INFO, ZCAN_USBCANFD_200U
    proxy = ModelBSerialProxy()
    proxy._load_library()
    found = []
    for index in range(64):
        handle = proxy._dll.ZCAN_OpenDevice(ZCAN_USBCANFD_200U, index, 0)
        if not proxy._valid_handle(handle):
            continue
        try:
            if int(proxy._dll.ZCAN_IsDeviceOnLine(handle)) != 2:
                continue
            info = ZCAN_DEVICE_INFO()
            proxy._check_status(proxy._dll.ZCAN_GetDeviceInf(handle, ctypes.byref(info)), "Read device information")
            serial = bytes(info.str_Serial_Num).split(b"\0", 1)[0].decode("ascii", errors="replace")
            found.append((index, f"Device {index}" + (f" / SN {serial}" if serial else "")))
        finally:
            proxy._check_status(proxy._dll.ZCAN_CloseDevice(handle), "Close scanned device")
    return found


class ModelBProxy(Protocol):
    @property
    def last_write_ok(self) -> bool: ...

    def begin_operation(self) -> None: ...
    def open(self, mode: object | None = None) -> bool: ...
    def close(self) -> None: ...
    def isOpen(self) -> bool: ...
    def errorString(self) -> str: ...
    def write(self, data: bytes) -> int: ...
    def bytesAvailable(self) -> int: ...
    def waitForReadyRead(self, timeout_ms: int) -> bool: ...
    def readAll(self) -> object: ...
    def clear(self, direction: object | None = None) -> bool: ...


ProxyFactory = Callable[..., ModelBProxy]


@dataclass(slots=True)
class WaveshareUsbCanFdBTransport(AbstractCanTransport):
    """AppTestPCB transport for Waveshare USB-CAN-FD Model B.

    The vendor device is opened through ``ControlCANFD.dll``. The existing
    Model A packet codec is retained so the application's CAN protocol and
    service layers do not need adapter-specific branches.
    """

    channel: str = MODEL_B_CHANNEL
    can_bitrate: int = 1_000_000
    dll_path: str | Path | None = None
    proxy_factory: ProxyFactory = field(default=ModelBSerialProxy, repr=False)
    _device: ModelBProxy | None = field(default=None, init=False, repr=False)
    _parser: WaveshareStreamParser = field(default_factory=WaveshareStreamParser, init=False, repr=False)
    _pending_frames: deque[RawCanFrame] = field(default_factory=deque, init=False, repr=False)

    def connect(self) -> None:
        if self.is_connected():
            return

        selection = re.fullmatch(r"USB-CAN-FD-B Device (\d+) / CAN([12])", self.channel)
        if selection is None:
            raise CanTransportError("Select a Model B device index and CAN1 or CAN2")
        device_index, can_number = map(int, selection.groups())
        if device_index > 0xFFFFFFFF:
            raise CanTransportError("Model B device index exceeds uint32 range")
        device = self.proxy_factory(dll_path=self.dll_path, device_index=device_index, channel_index=can_number - 1)
        try:
            if not device.open():
                detail = device.errorString() or "unknown ControlCANFD error"
                raise CanTransportError(f"Failed to open Waveshare USB-CAN-FD-B: {detail}")
            self._device = device
            self._parser.reset()
            self._pending_frames.clear()
            self.configure_adapter()
        except CanTransportError:
            self._device = None
            device.close()
            raise
        except Exception as exc:
            self._device = None
            device.close()
            raise CanTransportError(f"Failed to open Waveshare USB-CAN-FD-B: {exc}") from exc

    def configure_adapter(self) -> bytes:
        packet = build_waveshare_config_packet(self.can_bitrate)
        self._write_packet(packet, "configure")
        return packet

    def disconnect(self) -> None:
        device = self._device
        self._device = None
        try:
            if device is not None:
                device.close()
        finally:
            self._parser.reset()
            self._pending_frames.clear()

    def reset_stream(self) -> None:
        self._parser.reset()
        self._pending_frames.clear()
        device = self._device
        if device is not None and device.isOpen():
            try:
                if not device.clear():
                    detail = device.errorString() or "unknown ControlCANFD error"
                    raise CanTransportError(f"Failed to clear USB-CAN-FD-B receive buffer: {detail}")
            except CanTransportError:
                raise
            except Exception as exc:
                raise CanTransportError(f"Failed to clear USB-CAN-FD-B receive buffer: {exc}") from exc

    def is_connected(self) -> bool:
        return self._device is not None and self._device.isOpen()

    def send(self, frame: RawCanFrame) -> None:
        self._write_packet(encode_waveshare_can_frame(frame), "send CAN frame")

    def recv(self, timeout_s: float) -> RawCanFrame | None:
        device = self._require_device()
        if self._pending_frames:
            return self._pending_frames.popleft()

        timeout_s = max(0.0, timeout_s)
        deadline = monotonic() + timeout_s
        while True:
            if device.bytesAvailable() <= 0:
                remaining = max(0.0, deadline - monotonic())
                if remaining <= 0.0 or not device.waitForReadyRead(ceil(remaining * 1000)):
                    return None

            raw = bytes(device.readAll())
            if raw:
                self._pending_frames.extend(self._parser.feed(raw))
                if self._pending_frames:
                    return self._pending_frames.popleft()

            if timeout_s <= 0.0 or monotonic() >= deadline:
                return None

    def drain(self) -> list[RawCanFrame]:
        drained: list[RawCanFrame] = []
        while True:
            frame = self.recv(timeout_s=0.0)
            if frame is None:
                return drained
            drained.append(frame)

    def _require_device(self) -> ModelBProxy:
        if not self.is_connected():
            raise CanTransportError("USB-CAN-FD-B adapter is not connected")
        assert self._device is not None
        return self._device

    def _write_packet(self, packet: bytes, operation: str) -> None:
        device = self._require_device()
        try:
            device.begin_operation()
            written = device.write(packet)
        except Exception as exc:
            raise CanTransportError(f"Failed to {operation} with USB-CAN-FD-B: {exc}") from exc
        if written != len(packet) or not device.last_write_ok:
            detail = device.errorString() or f"wrote {written} of {len(packet)} bytes"
            raise CanTransportError(f"Failed to {operation} with USB-CAN-FD-B: {detail}")
