from __future__ import annotations

from datetime import datetime
from threading import Lock
from time import monotonic
from typing import Callable, TypeVar

from pcbt_app.logging_store import EventLog, RawFrameEvent
from pcbt_app.models import RawCanFrame
from pcbt_app.protocol.fct_can import (
    FCT_HOST_ID,
    FctOpcode,
    FctResponse,
    FctState,
    FctTestId,
    build_fct_simple,
    build_fct_start,
    parse_matching_fct_response,
)


class FctResponseTimeout(TimeoutError):
    pass


T = TypeVar("T")


_transport_locks: dict[int, tuple[object, Lock]] = {}
_transport_locks_guard = Lock()


def _lock_for_transport(transport: object) -> Lock:
    key = id(transport)
    with _transport_locks_guard:
        current = _transport_locks.get(key)
        if current is None or current[0] is not transport:
            current = (transport, Lock())
            _transport_locks[key] = current
        return current[1]


class FctClient:
    def __init__(
        self,
        transport,
        *,
        host_id: int = FCT_HOST_ID,
        response_timeout_s: float = 0.5,
        event_log: EventLog | None = None,
    ) -> None:
        self.transport = transport
        self.host_id = host_id
        self.response_timeout_s = response_timeout_s
        self.event_log = event_log
        self._transaction_lock = _lock_for_transport(transport)

    def _log(self, direction: str, frame: RawCanFrame, note: str = "") -> None:
        if self.event_log is not None:
            self.event_log.add(RawFrameEvent(direction, datetime.now(), frame, note=note))

    def transact(
        self,
        request: RawCanFrame,
        *,
        opcode: FctOpcode,
        test_id: FctTestId,
        driver_id: int,
        on_unrelated: Callable[[RawCanFrame], None] | None = None,
    ) -> FctResponse:
        with self._transaction_lock:
            self.transport.send(request)
            self._log("tx", request, f"FCT {opcode.name} test={int(test_id)}")
            deadline = monotonic() + self.response_timeout_s
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise FctResponseTimeout(
                        f"no FCT {opcode.name} response from driver 0x{driver_id:02X}"
                    )
                frame = self.transport.recv(remaining)
                if frame is None:
                    continue
                self._log("rx", frame)
                response = parse_matching_fct_response(
                    frame,
                    expected_opcode=opcode,
                    expected_test_id=test_id,
                    host_id=self.host_id,
                    driver_id=driver_id,
                )
                if response is not None:
                    return response
                self._log("rx", frame, "ignored while waiting for FCT response")
                if on_unrelated is not None:
                    on_unrelated(frame)

    def send_unrelated(self, frame: RawCanFrame, note: str = "") -> None:
        """Send non-FCT traffic without overlapping an FCT transaction."""
        with self._transaction_lock:
            self.transport.send(frame)
            self._log("tx", frame, note)

    def send_unrelated_and_receive_available(
        self,
        frame: RawCanFrame,
        note: str = "",
        *,
        on_received: Callable[[RawCanFrame], None] | None = None,
    ) -> None:
        """Send non-FCT traffic and consume frames already available afterward.

        The shared transaction lock prevents this diagnostic receive pass from
        overlapping another test's FCT request/response transaction.
        """
        with self._transaction_lock:
            self.transport.send(frame)
            self._log("tx", frame, note)
            while True:
                received = self.transport.recv(0.0)
                if received is None:
                    return
                self._log("rx", received)
                if on_received is not None:
                    on_received(received)

    def collect_matching(
        self,
        request: RawCanFrame,
        *,
        timeout_s: float,
        matcher: Callable[[RawCanFrame], T | None],
        is_complete: Callable[[list[T]], bool],
        note: str = "",
        on_unrelated: Callable[[RawCanFrame], None] | None = None,
    ) -> tuple[T, ...]:
        """Send non-FCT traffic and collect matching frames in one lock-safe transaction."""
        matched: list[T] = []
        with self._transaction_lock:
            self.transport.send(request)
            self._log("tx", request, note)
            deadline = monotonic() + max(0.0, timeout_s)
            while not is_complete(matched):
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                frame = self.transport.recv(remaining)
                if frame is None:
                    continue
                self._log("rx", frame)
                item = matcher(frame)
                if item is not None:
                    matched.append(item)
                    continue
                self._log("rx", frame, "ignored while waiting for non-FCT response")
                if on_unrelated is not None:
                    on_unrelated(frame)
        return tuple(matched)

    def start(
        self,
        *,
        driver_id: int,
        test_id: FctTestId,
        period_ticks: int,
        duration_ms: int,
        parameter_override: int = 0,
        on_unrelated: Callable[[RawCanFrame], None] | None = None,
    ) -> FctResponse:
        request = build_fct_start(
            host_id=self.host_id,
            driver_id=driver_id,
            test_id=test_id,
            period_ticks=period_ticks,
            duration_ms=duration_ms,
            parameter_override=parameter_override,
        )
        try:
            return self.transact(
                request, opcode=FctOpcode.START, test_id=test_id, driver_id=driver_id, on_unrelated=on_unrelated
            )
        except FctResponseTimeout as start_timeout:
            response = self.status(driver_id=driver_id, test_id=test_id, on_unrelated=on_unrelated)
            if response.state is FctState.IDLE:
                raise start_timeout
            return response

    def stop(
        self,
        *,
        driver_id: int,
        test_id: FctTestId,
        on_unrelated: Callable[[RawCanFrame], None] | None = None,
    ) -> FctResponse:
        request = build_fct_simple(
            host_id=self.host_id, driver_id=driver_id, opcode=FctOpcode.STOP, test_id=test_id
        )
        try:
            return self.transact(
                request, opcode=FctOpcode.STOP, test_id=test_id, driver_id=driver_id, on_unrelated=on_unrelated
            )
        except FctResponseTimeout:
            return self.status(driver_id=driver_id, test_id=test_id, on_unrelated=on_unrelated)

    def status(
        self,
        *,
        driver_id: int,
        test_id: FctTestId,
        on_unrelated: Callable[[RawCanFrame], None] | None = None,
    ) -> FctResponse:
        request = build_fct_simple(
            host_id=self.host_id, driver_id=driver_id, opcode=FctOpcode.STATUS, test_id=test_id
        )
        return self.transact(
            request, opcode=FctOpcode.STATUS, test_id=test_id, driver_id=driver_id, on_unrelated=on_unrelated
        )
