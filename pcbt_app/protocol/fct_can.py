from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from pcbt_app.models import RawCanFrame


FCT_REQUEST_FUNCTION = 0x1E
FCT_RESPONSE_FUNCTION = 0x1F
FCT_DLC = 8
FCT_HOST_ID = 0xFD
FCT_MAX_PERIOD_TICKS = 0xFF
FCT_MAX_DURATION_MS = 0xFFFFFF
FCT_MIN_PARAMETER_OVERRIDE = -0x800000
FCT_MAX_PARAMETER_OVERRIDE = 0x7FFFFF
FCT_OPENLOOP_MIN_THETA_STEP_URAD = -1570
FCT_OPENLOOP_MAX_THETA_STEP_URAD = 1570
FCT_OPENLOOP_MIN_THETA_STEP_RAD = FCT_OPENLOOP_MIN_THETA_STEP_URAD / 1_000_000
FCT_OPENLOOP_MAX_THETA_STEP_RAD = FCT_OPENLOOP_MAX_THETA_STEP_URAD / 1_000_000
FCT_COUNT_MAX = 0xFFFFF


class FctOpcode(IntEnum):
    START = 0x01
    STOP = 0x02
    STATUS = 0x03


class FctTestId(IntEnum):
    GONGWANG_ENCODER = 0x00
    OPEN_LOOP = 0x01
    CAN_STRESS = 0x02
    RS485 = 0x03


class FctCommandResult(IntEnum):
    OK = 0
    INVALID_OPCODE = 1
    INVALID_TEST = 2
    INVALID_PARAMETER = 3
    BUSY = 4
    NOT_RUNNING = 5
    SAFETY_BLOCKED = 6
    INTERNAL_ERROR = 7


class FctState(IntEnum):
    IDLE = 0
    RUNNING = 1
    PASS = 2
    FAIL = 3
    TIMEOUT = 4
    ABORTED = 5

    @property
    def is_terminal(self) -> bool:
        return self in {FctState.PASS, FctState.FAIL, FctState.TIMEOUT, FctState.ABORTED}


@dataclass(slots=True, frozen=True)
class FctIdFields:
    function: int
    opcode: int
    source_id: int
    target_id: int


@dataclass(slots=True, frozen=True)
class FctResponse:
    raw_frame: RawCanFrame
    opcode: FctOpcode
    source_id: int
    target_id: int
    result: FctCommandResult
    test_id: int
    state: FctState
    pass_count: int
    fail_count: int


class FctProtocolError(ValueError):
    pass


def _byte(name: str, value: int) -> int:
    if not 0 <= value <= 0xFF:
        raise ValueError(f"{name} must be 0..255")
    return value


def build_fct_id(function: int, opcode: int, source_id: int, target_id: int) -> int:
    if not 0 <= function <= 0x1F:
        raise ValueError("function must be 0..31")
    return (
        (function << 24)
        | (_byte("opcode", opcode) << 16)
        | (_byte("source_id", source_id) << 8)
        | _byte("target_id", target_id)
    )


def parse_fct_id(arbitration_id: int) -> FctIdFields:
    if not 0 <= arbitration_id <= 0x1FFFFFFF:
        raise ValueError("not a 29-bit CAN identifier")
    return FctIdFields(
        function=(arbitration_id >> 24) & 0x1F,
        opcode=(arbitration_id >> 16) & 0xFF,
        source_id=(arbitration_id >> 8) & 0xFF,
        target_id=arbitration_id & 0xFF,
    )


def build_fct_start(
    *,
    host_id: int,
    driver_id: int,
    test_id: FctTestId,
    period_ticks: int,
    duration_ms: int,
    parameter_override: int = 0,
) -> RawCanFrame:
    test_id = FctTestId(test_id)
    if not 0 <= period_ticks <= FCT_MAX_PERIOD_TICKS:
        raise ValueError("period_ticks must fit uint8")
    if not 1 <= duration_ms <= FCT_MAX_DURATION_MS:
        raise ValueError("duration_ms must fit unsigned 24-bit")
    if not FCT_MIN_PARAMETER_OVERRIDE <= parameter_override <= FCT_MAX_PARAMETER_OVERRIDE:
        raise ValueError("parameter_override must fit signed 24-bit")
    if test_id is FctTestId.GONGWANG_ENCODER and period_ticks == 0:
        raise ValueError("GongWang requires a nonzero tick period")
    if test_id in {FctTestId.OPEN_LOOP, FctTestId.CAN_STRESS} and period_ticks != 0:
        raise ValueError("Open-loop and CAN stress require period_ticks=0")
    if test_id is FctTestId.RS485 and period_ticks == 0:
        raise ValueError("RS485 requires period_ticks=1..255")
    if test_id is FctTestId.RS485 and duration_ms > (FCT_COUNT_MAX * period_ticks) // 20:
        raise ValueError("RS485 duration exceeds the 20-bit transmission-count limit")
    if (
        test_id is FctTestId.OPEN_LOOP
        and parameter_override != 0
        and not FCT_OPENLOOP_MIN_THETA_STEP_URAD
        <= parameter_override
        <= FCT_OPENLOOP_MAX_THETA_STEP_URAD
    ):
        raise ValueError("Open-loop parameter_override must be -1570..1570")
    if test_id is not FctTestId.OPEN_LOOP and parameter_override != 0:
        raise ValueError("GongWang, CAN stress, and RS485 require parameter_override=0")
    data = (
        bytes((int(test_id), period_ticks))
        + duration_ms.to_bytes(3, "little")
        + parameter_override.to_bytes(3, "little", signed=True)
    )
    return RawCanFrame(
        arbitration_id=build_fct_id(FCT_REQUEST_FUNCTION, FctOpcode.START, host_id, driver_id),
        data=data,
        is_extended_id=True,
    )


def build_fct_simple(
    *, host_id: int, driver_id: int, opcode: FctOpcode, test_id: FctTestId
) -> RawCanFrame:
    opcode = FctOpcode(opcode)
    test_id = FctTestId(test_id)
    if opcode not in {FctOpcode.STOP, FctOpcode.STATUS}:
        raise ValueError("build_fct_simple only supports STOP and STATUS")
    return RawCanFrame(
        arbitration_id=build_fct_id(FCT_REQUEST_FUNCTION, opcode, host_id, driver_id),
        data=bytes((int(test_id), 0, 0, 0, 0, 0, 0, 0)),
        is_extended_id=True,
    )


def parse_matching_fct_response(
    frame: RawCanFrame,
    *,
    expected_opcode: FctOpcode,
    expected_test_id: FctTestId,
    host_id: int,
    driver_id: int,
) -> FctResponse | None:
    if not frame.is_extended_id or len(frame.data) != FCT_DLC:
        return None
    fields = parse_fct_id(frame.arbitration_id)
    if (
        fields.function != FCT_RESPONSE_FUNCTION
        or fields.opcode != int(expected_opcode)
        or fields.source_id != driver_id
        or fields.target_id != host_id
        or frame.data[1] != int(expected_test_id)
    ):
        return None
    try:
        result = FctCommandResult(frame.data[0])
        state = FctState(frame.data[2])
        opcode = FctOpcode(fields.opcode)
    except ValueError as exc:
        raise FctProtocolError(
            f"unsupported FCT enum value: result={frame.data[0]} state={frame.data[2]} opcode={fields.opcode}"
        ) from exc
    pass_count = int.from_bytes(frame.data[4:6], "little") | ((frame.data[3] & 0x0F) << 16)
    fail_count = int.from_bytes(frame.data[6:8], "little") | ((frame.data[3] >> 4) << 16)
    return FctResponse(
        raw_frame=frame,
        opcode=opcode,
        source_id=fields.source_id,
        target_id=fields.target_id,
        result=result,
        test_id=frame.data[1],
        state=state,
        pass_count=pass_count,
        fail_count=fail_count,
    )


def fct_state_label(value: int | FctState | None) -> str:
    if value is None:
        return "-"
    try:
        return FctState(value).name
    except ValueError:
        return f"UNKNOWN({value})"
