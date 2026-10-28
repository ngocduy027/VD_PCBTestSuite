from __future__ import annotations

from threading import Lock

from pcbt_app.services.pcb_test_service import ServiceResult


class TestRunner:
    def __init__(self) -> None:
        self._lock = Lock()
        self._active: set[str] = set()

    def run(self, test_key: str, func, *args, **kwargs) -> ServiceResult:
        with self._lock:
            if test_key in self._active:
                raise RuntimeError(f"{test_key} is already running")
            self._active.add(test_key)
        try:
            return func(*args, **kwargs)
        finally:
            with self._lock:
                self._active.discard(test_key)
