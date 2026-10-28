from __future__ import annotations

from time import monotonic, sleep

from pcbt_app.models import GongwangResultSet, RawCanFrame
from pcbt_app.protocol.fct_can import (
    FCT_MAX_DURATION_MS,
    FCT_OPENLOOP_MAX_THETA_STEP_RAD,
    FCT_OPENLOOP_MIN_THETA_STEP_RAD,
    FctCommandResult,
    FctResponse,
    FctState,
    FctTestId,
)
from pcbt_app.protocol.ids import parse_extended_id
from pcbt_app.protocol.mit_firmware import MitCommand, build_passive_report_frame
from pcbt_app.services.fct_client import FctClient, FctResponseTimeout
from pcbt_app.transport.base import CanTransportError


GONGWANG_DEFAULT_PERIOD_TICKS = 1
OPENLOOP_PRE_RUN_MS = 4_500
OPENLOOP_SHUTDOWN_MS = 200


def _wait_before_final_status(
    *, expected_finish: float, deadline: float, test_id: FctTestId, events: list[str]
) -> None:
    wait_until = min(expected_finish, deadline)
    wait_s = max(0.0, wait_until - monotonic())
    if wait_s > 0:
        events.append(
            f"Waiting {wait_s:.3f}s before final FCT STATUS {test_id.name} polling"
        )
        sleep(wait_s)


def _command_failure(response: FctResponse) -> str | None:
    if response.result is FctCommandResult.OK:
        return None
    return (
        f"FCT {response.opcode.name} rejected: {response.result.name}; "
        f"state={response.state.name}"
    )


def _poll_terminal(
    service,
    client: FctClient,
    *,
    driver_id: int,
    test_id: FctTestId,
    deadline: float,
    events: list[str],
    on_unrelated=None,
    on_poll=None,
) -> FctResponse | None:
    while monotonic() < deadline:
        try:
            response = client.status(
                driver_id=driver_id, test_id=test_id, on_unrelated=on_unrelated
            )
        except FctResponseTimeout as exc:
            events.append(
                f"FCT STATUS {test_id.name} response timeout; retrying until app timeout: {exc}"
            )
            if on_poll is not None:
                on_poll()
            if service.poll_interval_s > 0:
                sleep(min(service.poll_interval_s, max(0.0, deadline - monotonic())))
            continue
        events.append(
            f"FCT STATUS {test_id.name}: result={response.result.name} state={response.state.name} "
            f"pass={response.pass_count} fail={response.fail_count}"
        )
        if on_poll is not None:
            on_poll()
        if response.result is not FctCommandResult.OK:
            return response
        if response.state.is_terminal:
            return response
        if service.poll_interval_s > 0:
            sleep(min(service.poll_interval_s, max(0.0, deadline - monotonic())))
    return None


def _stop_and_reconcile(service, client, driver_id, test_id, events, on_unrelated=None):
    try:
        response = client.stop(
            driver_id=driver_id, test_id=test_id, on_unrelated=on_unrelated
        )
        events.append(
            f"FCT STOP {test_id.name}: result={response.result.name} state={response.state.name}"
        )
        if response.state.is_terminal:
            return response
        return _poll_terminal(
            service,
            client,
            driver_id=driver_id,
            test_id=test_id,
            deadline=monotonic() + 2.0,
            events=events,
            on_unrelated=on_unrelated,
        ) or response
    except (CanTransportError, FctResponseTimeout, ValueError) as exc:
        events.append(f"failed to reconcile FCT stop: {exc}")
        return None


def _basic_result(service, test_name, start, events, driver_id, response, failure_reason=None, **kwargs):
    if response is None:
        return service._result(
            test_name,
            False,
            failure_reason or "No terminal FCT status received",
            start,
            events,
            returned_driver_id=driver_id,
            **kwargs,
        )
    command_failure = _command_failure(response)
    passed = command_failure is None and response.state is FctState.PASS
    reason = failure_reason or command_failure or (
        f"{response.state.name}: pass_count={response.pass_count}, fail_count={response.fail_count}"
    )
    return service._result(
        test_name,
        passed,
        reason,
        start,
        events,
        returned_driver_id=driver_id,
        fct_state=response.state,
        fct_pass_count=response.pass_count,
        fct_fail_count=response.fail_count,
        **kwargs,
    )


def run_fct_openloop_test(service, timeout_ms, driver_id, theta_step, duration_ms):
    start = monotonic()
    events: list[str] = []
    if driver_id is None:
        return service._result("Open-loop Test", False, "Driver ID is required; run Detect ID first", start, events)
    if not 0 <= driver_id <= 0xFF:
        return service._result("Open-loop Test", False, f"Driver ID out of range: {driver_id}", start, events)
    if not 1 <= duration_ms <= FCT_MAX_DURATION_MS:
        return service._result("Open-loop Test", False, "Open-loop duration is outside the FCT range", start, events)
    if not FCT_OPENLOOP_MIN_THETA_STEP_RAD <= theta_step <= FCT_OPENLOOP_MAX_THETA_STEP_RAD:
        return service._result(
            "Open-loop Test",
            False,
            "Open-loop theta step must be -0.00157..0.00157 rad/tick",
            start,
            events,
        )
    parameter_override = round(theta_step * 1_000_000)
    events.append(
        f"theta step {theta_step} rad/tick -> override {parameter_override} microrad/tick"
    )
    client = service.fct_client
    deadline = start + timeout_ms / 1000.0
    try:
        response = client.start(
            driver_id=driver_id,
            test_id=FctTestId.OPEN_LOOP,
            period_ticks=0,
            duration_ms=duration_ms,
            parameter_override=parameter_override,
        )
        events.append(f"FCT START OPEN_LOOP: result={response.result.name} state={response.state.name}")
        failure = _command_failure(response)
        if failure:
            return _basic_result(service, "Open-loop Test", start, events, driver_id, response, failure)
        if not response.state.is_terminal:
            _wait_before_final_status(
                expected_finish=start
                + (OPENLOOP_PRE_RUN_MS + duration_ms + OPENLOOP_SHUTDOWN_MS) / 1000.0,
                deadline=deadline,
                test_id=FctTestId.OPEN_LOOP,
                events=events,
            )
            response = _poll_terminal(
                service, client, driver_id=driver_id, test_id=FctTestId.OPEN_LOOP, deadline=deadline, events=events
            )
        if response is None:
            response = _stop_and_reconcile(service, client, driver_id, FctTestId.OPEN_LOOP, events)
            return _basic_result(
                service, "Open-loop Test", start, events, driver_id, response, "App timeout expired; open-loop was stopped"
            )
        return _basic_result(service, "Open-loop Test", start, events, driver_id, response)
    except (CanTransportError, FctResponseTimeout, ValueError) as exc:
        response = _stop_and_reconcile(service, client, driver_id, FctTestId.OPEN_LOOP, events)
        return _basic_result(service, "Open-loop Test", start, events, driver_id, response, str(exc))


def run_fct_gongwang_test(
    service,
    duration_ms,
    driver_id,
    period_ticks=GONGWANG_DEFAULT_PERIOD_TICKS,
    app_timeout_ms=None,
):
    start = monotonic()
    events: list[str] = []
    if driver_id is None:
        return service._result("GongWang Encoder Test", False, "Driver ID is required; run Detect ID first", start, events)
    if not 1 <= duration_ms <= FCT_MAX_DURATION_MS:
        return service._result("GongWang Encoder Test", False, "GongWang duration is outside the FCT range", start, events)
    if not 1 <= period_ticks <= 0xFF:
        return service._result("GongWang Encoder Test", False, "GongWang period must be 1..255 ticks", start, events)
    if app_timeout_ms is None:
        app_timeout_ms = duration_ms + 1500
    if app_timeout_ms < 1:
        return service._result("GongWang Encoder Test", False, "GongWang app timeout must be positive", start, events)
    client = service.fct_client
    deadline = start + app_timeout_ms / 1000.0
    try:
        response = client.start(
            driver_id=driver_id,
            test_id=FctTestId.GONGWANG_ENCODER,
            period_ticks=period_ticks,
            duration_ms=duration_ms,
        )
        events.append(f"FCT START GONGWANG_ENCODER: result={response.result.name} state={response.state.name}")
        failure = _command_failure(response)
        if failure:
            return _basic_result(service, "GongWang Encoder Test", start, events, driver_id, response, failure)
        if not response.state.is_terminal:
            _wait_before_final_status(
                expected_finish=start + duration_ms / 1000.0,
                deadline=deadline,
                test_id=FctTestId.GONGWANG_ENCODER,
                events=events,
            )
            response = _poll_terminal(
                service,
                client,
                driver_id=driver_id,
                test_id=FctTestId.GONGWANG_ENCODER,
                deadline=deadline,
                events=events,
            )
        if response is None:
            response = _stop_and_reconcile(service, client, driver_id, FctTestId.GONGWANG_ENCODER, events)
            failure = "App timeout expired; GongWang test was stopped"
        else:
            failure = None
        result_set = GongwangResultSet(
            duration_ms=duration_ms,
            response_count=None if response is None else response.pass_count,
            valid_count=None if response is None else response.pass_count,
        )
        return _basic_result(
            service,
            "GongWang Encoder Test",
            start,
            events,
            driver_id,
            response,
            failure,
            success_rate_percent=(None if response is None else (100.0 if response.state is FctState.PASS else 0.0)),
            gongwang_results=result_set,
        )
    except (CanTransportError, FctResponseTimeout, ValueError) as exc:
        response = _stop_and_reconcile(service, client, driver_id, FctTestId.GONGWANG_ENCODER, events)
        return _basic_result(service, "GongWang Encoder Test", start, events, driver_id, response, str(exc))


def _is_passive_feedback(frame: RawCanFrame, driver_id: int, host_id: int) -> bool:
    if not frame.is_extended_id:
        return False
    fields = parse_extended_id(frame.arbitration_id)
    return (
        fields.function_code == MitCommand.FEEDBACK
        and fields.driver_id == driver_id
        and fields.host_id == host_id
    )


def run_fct_can_stress_test(
    service,
    timeout_ms,
    driver_id,
    interval_ms,
    total_messages,
    response_count_callback=None,
):
    start = monotonic()
    events: list[str] = []
    if driver_id is None:
        return service._result("CAN Stress Test", False, "Driver ID is required; run Detect ID first", start, events)
    if not 1 <= interval_ms <= 1000:
        return service._result("CAN Stress Test", False, "CAN stress interval must be 1..1000 ms", start, events)
    if not 1 <= total_messages <= 1_000_000:
        return service._result("CAN Stress Test", False, "CAN stress total messages must be 1..1000000", start, events)
    traffic_window_ms = interval_ms * total_messages
    duration_ms = traffic_window_ms + 500
    if duration_ms > FCT_MAX_DURATION_MS:
        return service._result("CAN Stress Test", False, "CAN stress duration is outside the FCT range", start, events)
    client = service.fct_client
    received = 0
    last_reported_received = -1
    last_progress_at = 0.0
    deadline = start + timeout_ms / 1000.0

    def publish_received(*, force: bool = False) -> None:
        nonlocal last_reported_received, last_progress_at
        if response_count_callback is None or received == last_reported_received:
            return
        now = monotonic()
        if not force and now - last_progress_at < 0.050:
            return
        response_count_callback(received)
        last_reported_received = received
        last_progress_at = now

    def on_unrelated(frame: RawCanFrame) -> None:
        nonlocal received
        if _is_passive_feedback(frame, driver_id, client.host_id):
            received += 1
            publish_received()

    try:
        publish_received(force=True)
        response = client.start(
            driver_id=driver_id,
            test_id=FctTestId.CAN_STRESS,
            period_ticks=0,
            duration_ms=duration_ms,
            on_unrelated=on_unrelated,
        )
        events.append(f"FCT START CAN_STRESS: result={response.result.name} state={response.state.name}")
        failure = _command_failure(response)
        if failure:
            return _basic_result(service, "CAN Stress Test", start, events, driver_id, response, failure)

        sent = 0
        next_send = monotonic()
        while (
            sent < total_messages
            and not response.state.is_terminal
            and monotonic() < deadline
        ):
            now = monotonic()
            if now < next_send:
                sleep(next_send - now)
            frame = build_passive_report_frame(driver_id)
            client.send_unrelated_and_receive_available(
                frame,
                f"CAN stress passive request {sent + 1}/{total_messages}",
                on_received=on_unrelated,
            )
            sent += 1
            next_send += interval_ms / 1000.0

        if not response.state.is_terminal:
            _wait_before_final_status(
                expected_finish=start + duration_ms / 1000.0,
                deadline=deadline,
                test_id=FctTestId.CAN_STRESS,
                events=events,
            )
            response = _poll_terminal(
                service,
                client,
                driver_id=driver_id,
                test_id=FctTestId.CAN_STRESS,
                deadline=deadline,
                events=events,
                on_unrelated=on_unrelated,
            )
        if response is None:
            response = _stop_and_reconcile(
                service, client, driver_id, FctTestId.CAN_STRESS, events, on_unrelated
            )
            failure = "App timeout expired; CAN stress observer was stopped"
        else:
            failure = None
        publish_received(force=True)
        pass_count = 0 if response is None else response.pass_count
        fail_count = 0 if response is None else response.fail_count
        return _basic_result(
            service,
            "CAN Stress Test",
            start,
            events,
            driver_id,
            response,
            failure,
            tx_attempt_count=sent,
            tx_success_count=sent,
            tx_fail_count=0,
            received_message_count=pass_count,
            actual_response_count=received,
            expected_message_count=sent,
            missing_message_count=fail_count,
            duplicate_message_count=0,
            success_rate_percent=(pass_count * 100.0 / sent if sent else 0.0),
            receive_rate_hz=(pass_count * 1000.0 / traffic_window_ms if traffic_window_ms else 0.0),
        )
    except (CanTransportError, FctResponseTimeout, ValueError) as exc:
        response = _stop_and_reconcile(service, client, driver_id, FctTestId.CAN_STRESS, events, on_unrelated)
        publish_received(force=True)
        return _basic_result(
            service,
            "CAN Stress Test",
            start,
            events,
            driver_id,
            response,
            str(exc),
            actual_response_count=received,
        )
