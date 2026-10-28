"""Rate-limited progress reporting from worker-thread tests to the GUI."""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic

from pcbt_app.config import PROGRESS_THROTTLE_S


class ThrottledProgress:
    """Forward a running count to ``callback`` at most once per ``PROGRESS_THROTTLE_S``.

    Unchanged counts are never re-sent. ``force=True`` bypasses the rate limit so
    the first and final values always reach the GUI.
    """

    def __init__(self, callback: Callable[[int], None] | None) -> None:
        self._callback = callback
        self._last_count = -1
        self._last_at = 0.0

    @property
    def enabled(self) -> bool:
        """False when no callback was given (callers can skip computing the count)."""
        return self._callback is not None

    def publish(self, count: int, *, force: bool = False) -> None:
        """Report ``count`` unless it is unchanged or the last report was too recent."""
        if self._callback is None or count == self._last_count:
            return
        now = monotonic()
        if not force and now - self._last_at < PROGRESS_THROTTLE_S:
            return
        self._callback(count)
        self._last_count = count
        self._last_at = now
