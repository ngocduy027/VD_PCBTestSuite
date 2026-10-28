"""Transport backed by ``python-can`` (PEAK PCAN and other supported interfaces)."""

from __future__ import annotations

from dataclasses import dataclass

import can

from pcbt_app.models import RawCanFrame
from pcbt_app.transport.base import AbstractCanTransport, CanTransportError


@dataclass(slots=True, weakref_slot=True)
class PythonCanTransport(AbstractCanTransport):
    """Wraps a ``can.Bus`` (``backend`` is the python-can interface name, e.g. ``pcan``)."""

    backend: str
    channel: str
    bitrate: int
    _bus: can.BusABC | None = None

    def connect(self) -> None:
        if self._bus is not None:
            return
        try:
            self._bus = can.Bus(interface=self.backend, channel=self.channel, bitrate=self.bitrate)
        except Exception as exc:  # pragma: no cover
            raise CanTransportError(f"Failed to connect CAN adapter: {exc}") from exc

    def disconnect(self) -> None:
        if self._bus is None:
            return
        try:
            self._bus.shutdown()
        finally:
            self._bus = None

    def is_connected(self) -> bool:
        return self._bus is not None

    def send(self, frame: RawCanFrame) -> None:
        if self._bus is None:
            raise CanTransportError("CAN adapter is not connected")
        message = can.Message(
            arbitration_id=frame.arbitration_id,
            data=frame.data,
            is_extended_id=frame.is_extended_id,
        )
        try:
            self._bus.send(message)
        except Exception as exc:
            raise CanTransportError(f"Failed to send CAN frame: {exc}") from exc

    def recv(self, timeout_s: float) -> RawCanFrame | None:
        if self._bus is None:
            raise CanTransportError("CAN adapter is not connected")
        try:
            message = self._bus.recv(timeout=timeout_s)
        except Exception as exc:
            raise CanTransportError(f"Failed to receive CAN frame: {exc}") from exc
        if message is None:
            return None
        data = bytes(message.data)
        if len(data) > 8:
            raise CanTransportError(f"Expected Classic CAN payload length 0..8, got {len(data)}")
        return RawCanFrame(
            arbitration_id=message.arbitration_id,
            data=data,
            is_extended_id=message.is_extended_id,
            timestamp=message.timestamp or 0.0,
            channel=str(message.channel) if message.channel is not None else None,
        )

    def drain(self) -> list[RawCanFrame]:
        drained: list[RawCanFrame] = []
        while True:
            item = self.recv(timeout_s=0.0)
            if item is None:
                return drained
            drained.append(item)
