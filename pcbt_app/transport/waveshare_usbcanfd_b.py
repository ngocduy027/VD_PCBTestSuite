"""Waveshare USB-CAN-FD Model B transport over the vendor ``ControlCANFD.dll``."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from math import ceil
from pathlib import Path
from time import monotonic
from typing import Any, Protocol

from pcbt_app.config import DEFAULT_MODEL_B_CHANNEL
from pcbt_app.models import RawCanFrame
from pcbt_app.transport.base import AbstractCanTransport, CanTransportError
from pcbt_app.transport.Communication_CAN_CANFD_ModelB import ModelBSerialProxy
from pcbt_app.transport.waveshare_usbcan_a import (
    WaveshareStreamParser,
    build_waveshare_config_packet,
    encode_waveshare_can_frame,
)

MODEL_B_CHANNEL = DEFAULT_MODEL_B_CHANNEL


def scan_model_b_devices() -> list[tuple[int, str]]:
    """List online Model B adapters as ``(device_index, label)`` without starting CAN."""
    return ModelBSerialProxy().scan_online_devices()


class ModelBProxy(Protocol):
    """QSerialPort-like interface of ``ModelBSerialProxy``; lets tests inject a fake device."""

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
    def readAll(self) -> Any: ...  # QByteArray-compatible buffer
    def clear(self, direction: object | None = None) -> bool: ...


ProxyFactory = Callable[..., ModelBProxy]


@dataclass(slots=True, weakref_slot=True)
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
        """Parse ``channel`` (``USB-CAN-FD-B Device N / CANx``), open the device and configure CAN."""
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
        """Send the Model A format config packet; the proxy translates it into DLL calls."""
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
        """Clear the parser, queued frames and the device receive buffer."""
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
