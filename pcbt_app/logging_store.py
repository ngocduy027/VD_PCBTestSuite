"""Thread-safe ring buffer of raw TX/RX CAN frames shown in the GUI console."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from threading import Lock

from pcbt_app.models import RawCanFrame


@dataclass(slots=True, frozen=True)
class RawFrameEvent:
    """One logged frame: direction (``tx``/``rx``), wall-clock time, frame and optional note."""

    direction: str
    timestamp: datetime
    frame: RawCanFrame
    note: str = ""

    def format_line(self) -> str:
        """Fixed-width console line: ``time dir id payload note``."""
        payload = " ".join(f"{byte:02X}" for byte in self.frame.data)
        return (
            f"{self.timestamp.strftime('%H:%M:%S.%f')[:-3]:<12} "
            f"{self.direction.upper():<3} "
            f"0x{self.frame.arbitration_id:08X} "
            f"{payload:<23} "
            f"{self.note}".rstrip()
        )


class EventLog:
    """Bounded, thread-safe frame log; the oldest entries drop off after ``max_entries``."""

    def __init__(self, max_entries: int = 10_000) -> None:
        self._items: deque[RawFrameEvent] = deque(maxlen=max_entries)
        self._lock = Lock()

    def add(self, event: RawFrameEvent) -> None:
        """Append an event (callable from any thread)."""
        with self._lock:
            self._items.append(event)

    def clear(self) -> None:
        """Remove all events."""
        with self._lock:
            self._items.clear()

    def snapshot(self) -> list[RawFrameEvent]:
        """Copy of the current events, oldest first."""
        with self._lock:
            return list(self._items)
