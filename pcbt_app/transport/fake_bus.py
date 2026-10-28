"""In-memory transport for tests: records sent frames and replays queued ones."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from pcbt_app.models import RawCanFrame
from pcbt_app.transport.base import AbstractCanTransport, CanTransportError


@dataclass(slots=True, weakref_slot=True)
class FakeCanTransport(AbstractCanTransport):
    """Scriptable transport for unit tests.

    ``stale_frames`` are returned by :meth:`drain` (frames left from before a
    test), ``queued_frames`` by :meth:`recv` in order, and everything sent is
    appended to ``sent_frames``. ``fail_on_send`` injects a send error.
    """

    stale_frames: deque[RawCanFrame] = field(default_factory=deque)
    queued_frames: deque[RawCanFrame] = field(default_factory=deque)
    sent_frames: list[RawCanFrame] = field(default_factory=list)
    fail_on_send: bool = False
    connected: bool = False

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected

    def send(self, frame: RawCanFrame) -> None:
        if not self.connected:
            raise CanTransportError("CAN adapter is not connected")
        if self.fail_on_send:
            raise CanTransportError("Injected send failure")
        self.sent_frames.append(frame)

    def recv(self, timeout_s: float) -> RawCanFrame | None:
        if not self.connected:
            raise CanTransportError("CAN adapter is not connected")
        if self.queued_frames:
            return self.queued_frames.popleft()
        return None

    def drain(self) -> list[RawCanFrame]:
        drained = list(self.stale_frames)
        self.stale_frames.clear()
        return drained
