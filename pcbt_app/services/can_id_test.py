"""Detect ID: find the single driver on the bus.

Broadcasts ``Get ID`` first; if nobody answers, falls back to probing IDs
0..255 one by one. PASS requires exactly one responding driver.
"""

from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING

from pcbt_app.protocol.mit_firmware import build_get_id_frame, build_scan_id_frame
from pcbt_app.transport.base import CanTransportError

if TYPE_CHECKING:
    from pcbt_app.services.pcb_test_service import PcbTestService, ServiceResult


def run_can_test(service: PcbTestService, timeout_ms: int) -> ServiceResult:
    """Find the driver ID; PASS only if exactly one driver answers within ``timeout_ms``."""
    start = monotonic()
    events: list[str] = []
    found_ids: set[int] = set()

    drain_result = service._drain_before_command("CAN Test")
    if drain_result is not None:
        return drain_result

    deadline = start + (timeout_ms / 1000.0)
    try:
        service._send(build_get_id_frame(), "MIT_Get_ID")
        guide_collect_until = min(deadline, monotonic() + max(0.100, service.scan_interval_s))
        service._collect_scan_responses(guide_collect_until, found_ids, events)

        if not found_ids and monotonic() < deadline:
            events.append("no response to ID 0; falling back to VinD-style ID scan")
            for target_id in range(256):
                if monotonic() >= deadline:
                    break
                frame = build_scan_id_frame(target_id)
                service._send(frame, f"scan ID {target_id}")
                collect_until = min(deadline, monotonic() + service.scan_interval_s)
                service._collect_scan_responses(collect_until, found_ids, events)

            if service.scan_interval_s > 0 and monotonic() < deadline:
                service._collect_scan_responses(deadline, found_ids, events)
    except CanTransportError as exc:
        return service._result("CAN Test", False, str(exc), start, events)

    if len(found_ids) == 1:
        driver_id = next(iter(found_ids))
        events.append(f"found driver ID {driver_id}")
        return service._result("CAN Test", True, "PASS", start, events, returned_driver_id=driver_id)

    if not found_ids:
        return service._result("CAN Test", False, "No Motor Found", start, events)

    ids = ", ".join(str(item) for item in sorted(found_ids))
    return service._result("CAN Test", False, f"Multiple motor IDs found: {ids}", start, events)
