from __future__ import annotations

from time import monotonic, sleep

from pcbt_app.protocol.fct_can import (
    FCT_COUNT_MAX,
    FctCommandResult,
    FctResponse,
    FctState,
    FctTestId,
)
from pcbt_app.protocol.mit_firmware import (
    RS485_STRESS_DEFAULT_DURATION_MS,
    RS485_STRESS_DEFAULT_PERIOD_TICKS,
)
from pcbt_app.services.fct_client import FctResponseTimeout
from pcbt_app.services.fct_workflows import _poll_terminal, _stop_and_reconcile
from pcbt_app.transport.base import CanTransportError
from pcbt_app.transport.rs485_serial import Rs485ParserSnapshot, SerialRs485Transport


RS485_FINAL_DRAIN_S = 0.100
RS485_STOP_RECONCILE_S = 2.0


def run_rs485_stress_test(
    service,
    app_timeout_ms: int,
    driver_id: int | None = None,
    rs485_transport: SerialRs485Transport | None = None,
    duration_ms: int = RS485_STRESS_DEFAULT_DURATION_MS,
    period_ticks: int = RS485_STRESS_DEFAULT_PERIOD_TICKS,
    response_count_callback=None,
):
    start = monotonic()
    events: list[str] = []
    test_name = "RS485 Stress Test"
    if driver_id is None:
        return service._result(test_name, False, "Driver ID is required; run Detect ID first", start, events)
    if not 0 <= driver_id <= 0xFF:
        return service._result(test_name, False, f"Driver ID out of range: {driver_id}", start, events)
    if rs485_transport is None or not rs485_transport.is_connected():
        return service._result(test_name, False, "RS485 serial is not connected", start, events)
    if not 1 <= period_ticks <= 0xFF:
        return service._result(test_name, False, "RS485 period_ticks must be 1..255", start, events)
    max_duration_ms = (FCT_COUNT_MAX * period_ticks) // 20
    if not 1 <= duration_ms <= max_duration_ms:
        return service._result(
            test_name,
            False,
            f"RS485 duration must be 1..{max_duration_ms} ms for period_ticks={period_ticks}",
            start,
            events,
        )
    if app_timeout_ms < 1:
        return service._result(test_name, False, "RS485 app timeout must be positive", start, events)

    drain_result = service._drain_before_command(test_name)
    if drain_result is not None:
        return drain_result

    client = service.fct_client
    if client is None:
        return service._result(test_name, False, "FCT mode is required for RS485 stress", start, events)
    response: FctResponse | None = None
    failure_reason: str | None = None
    capture_started = False
    last_reported_count = -1
    last_progress_at = 0.0
    deadline = start + app_timeout_ms / 1000.0

    def publish_received(count: int | None = None, *, force: bool = False) -> None:
        nonlocal last_reported_count, last_progress_at
        if response_count_callback is None:
            return
        if count is None:
            count = rs485_transport.capture_snapshot().valid_frame_count
        if count == last_reported_count:
            return
        now = monotonic()
        if not force and now - last_progress_at < 0.050:
            return
        response_count_callback(count)
        last_reported_count = count
        last_progress_at = now

    try:
        rs485_transport.begin_capture()
        capture_started = True
        publish_received(0, force=True)
        events.append("RS485 collection started; expected sequence=0x000000")
        response = client.start(
            driver_id=driver_id,
            test_id=FctTestId.RS485,
            period_ticks=period_ticks,
            duration_ms=duration_ms,
            parameter_override=0,
        )
        events.append(
            f"FCT START RS485: result={response.result.name} state={response.state.name}"
        )
        if response.result is not FctCommandResult.OK:
            failure_reason = (
                f"FCT START rejected: {response.result.name}; state={response.state.name}"
            )
        elif not response.state.is_terminal:
            response = _poll_terminal(
                service,
                client,
                driver_id=driver_id,
                test_id=FctTestId.RS485,
                deadline=deadline,
                events=events,
                on_poll=publish_received,
            )
            if response is None:
                failure_reason = "App timeout expired; RS485 test was stopped"
                response = _stop_and_reconcile(
                    service, client, driver_id, FctTestId.RS485, events
                )
    except (CanTransportError, FctResponseTimeout, ValueError) as exc:
        failure_reason = str(exc)
        response = _stop_and_reconcile(
            service, client, driver_id, FctTestId.RS485, events
        )
    finally:
        if capture_started:
            sleep(RS485_FINAL_DRAIN_S)
            snapshot = rs485_transport.finish_capture()
            publish_received(snapshot.valid_frame_count, force=True)
            events.append(
                f"RS485 drain complete: valid={snapshot.valid_frame_count} "
                f"malformed={snapshot.malformed_count} sequence_errors={snapshot.sequence_error_count}"
            )
        else:
            snapshot = Rs485ParserSnapshot(0, 0, 0, None, None, 0, None, 0, 0)

    return _finish_rs485_result(
        service,
        start=start,
        events=events,
        driver_id=driver_id,
        response=response,
        snapshot=snapshot,
        duration_ms=duration_ms,
        period_ticks=period_ticks,
        failure_reason=failure_reason,
    )


def _finish_rs485_result(
    service,
    *,
    start: float,
    events: list[str],
    driver_id: int,
    response: FctResponse | None,
    snapshot: Rs485ParserSnapshot,
    duration_ms: int,
    period_ticks: int,
    failure_reason: str | None,
):
    reasons: list[str] = []
    if response is None:
        reasons.append("No terminal FCT status received")
        firmware_count = 0
    else:
        firmware_count = response.pass_count
        if response.result is not FctCommandResult.OK:
            reasons.append(f"FCT {response.opcode.name} result is {response.result.name}")
        if response.state is not FctState.PASS:
            reasons.append(f"firmware state is {response.state.name}")
        if response.fail_count != 0:
            reasons.append(f"firmware fail_count={response.fail_count}")

    if failure_reason and reasons:
        reasons.insert(0, failure_reason)

    pc_diagnostics: list[str] = []
    if snapshot.transport_error:
        pc_diagnostics.append(snapshot.transport_error)
    if snapshot.malformed_count:
        pc_diagnostics.append(f"malformed RS485 data={snapshot.malformed_count}")
    if snapshot.sequence_error_count:
        detail = ""
        if snapshot.first_sequence_error is not None:
            expected, received = snapshot.first_sequence_error
            detail = f" (first expected=0x{expected:06X}, received=0x{received:06X})"
        pc_diagnostics.append(f"sequence errors={snapshot.sequence_error_count}{detail}")
    if snapshot.valid_frame_count != firmware_count:
        pc_diagnostics.append(
            f"PC/firmware count mismatch: PC={snapshot.valid_frame_count}, firmware={firmware_count}"
        )
    if snapshot.valid_frame_count == 0:
        pc_diagnostics.append("no valid RS485 frames received")
    if snapshot.buffer_length:
        pc_diagnostics.append(f"parser buffer has {snapshot.buffer_length} incomplete bytes")
    if pc_diagnostics:
        events.append(
            "PC RS485 diagnostics (informational only): "
            + "; ".join(dict.fromkeys(pc_diagnostics))
        )

    passed = not reasons
    requested_hz = 20_000.0 / period_ticks
    receive_rate_hz = snapshot.valid_frame_count * 1000.0 / duration_ms
    missing_count = max(0, firmware_count - snapshot.valid_frame_count)
    success_rate = (
        snapshot.valid_frame_count * 100.0 / firmware_count if firmware_count else 0.0
    )
    reason = (
        f"PASS: firmware reported PASS; PC received {snapshot.valid_frame_count} valid RS485 frames"
        if passed
        else "; ".join(dict.fromkeys(reasons))
    )
    return service._result(
        "RS485 Stress Test",
        passed,
        reason,
        start,
        events,
        returned_driver_id=driver_id,
        fct_state=None if response is None else response.state,
        fct_pass_count=None if response is None else response.pass_count,
        fct_fail_count=None if response is None else response.fail_count,
        received_message_count=snapshot.valid_frame_count,
        expected_message_count=None if response is None else response.pass_count,
        missing_message_count=missing_count,
        duplicate_message_count=snapshot.sequence_error_count,
        success_rate_percent=success_rate,
        receive_rate_hz=receive_rate_hz,
        received_byte_count=snapshot.received_byte_count,
        bytes_per_message=10,
        requested_duration_ms=duration_ms,
        period_ticks=period_ticks,
        requested_frequency_hz=requested_hz,
        malformed_count=snapshot.malformed_count,
        sequence_error_count=snapshot.sequence_error_count,
        first_sequence=snapshot.first_sequence,
        last_sequence=snapshot.last_sequence,
        parser_buffer_length=snapshot.buffer_length,
    )
