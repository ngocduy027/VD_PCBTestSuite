from __future__ import annotations

from time import monotonic

from pcbt_app.protocol.mit_firmware import build_get_id_frame, build_scan_id_frame
from pcbt_app.transport.base import CanTransportError


def run_can_test(service, timeout_ms: int):
    self = service
    start = monotonic()
    events: list[str] = []
    found_ids: set[int] = set()

    drain_result = self._drain_before_command("CAN Test")
    if drain_result is not None:
        return drain_result

    deadline = start + (timeout_ms / 1000.0)
    try:
        self._send(build_get_id_frame(), "MIT_Get_ID")
        guide_collect_until = min(deadline, monotonic() + max(0.100, self.scan_interval_s))
        self._collect_scan_responses(guide_collect_until, found_ids, events)

        if not found_ids and monotonic() < deadline:
            events.append("no response to ID 0; falling back to VinD-style ID scan")
            for target_id in range(256):
                if monotonic() >= deadline:
                    break
                frame = build_scan_id_frame(target_id)
                self._send(frame, f"scan ID {target_id}")
                collect_until = min(deadline, monotonic() + self.scan_interval_s)
                self._collect_scan_responses(collect_until, found_ids, events)

            if self.scan_interval_s > 0 and monotonic() < deadline:
                self._collect_scan_responses(deadline, found_ids, events)
    except CanTransportError as exc:
        return self._result("CAN Test", False, str(exc), start, events)

    if len(found_ids) == 1:
        driver_id = next(iter(found_ids))
        events.append(f"found driver ID {driver_id}")
        return self._result("CAN Test", True, "PASS", start, events, returned_driver_id=driver_id)

    if not found_ids:
        return self._result("CAN Test", False, "No Motor Found", start, events)

    ids = ", ".join(str(item) for item in sorted(found_ids))
    return self._result("CAN Test", False, f"Multiple motor IDs found: {ids}", start, events)
