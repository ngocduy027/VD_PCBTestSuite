"""FCT (functional circuit test) CAN protocol: request ``0x1E`` / response ``0x1F``.

Frames are 8-byte extended CAN. The 29-bit ID packs function, opcode, source
and target IDs; START payloads carry the test ID, tick period, a 24-bit
duration and a signed 24-bit parameter override.
"""

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
FCT_COUNT_MAX = 0xFFFFF  # 20-bit pass/fail counters in FCT STATUS responses
# The firmware's FCT scheduler runs at 20 kHz, i.e. 20 ticks per millisecond.
FCT_TICKS_PER_MS = 20


def rs485_max_duration_ms(period_ticks: int) -> int:
    """Longest RS485 run whose frame count still fits the 20-bit counter."""
    return (FCT_COUNT_MAX * period_ticks) // FCT_TICKS_PER_MS


class FctOpcode(IntEnum):
    """FCT request opcode, carried in bits 16..23 of the CAN ID."""

    START = 0x01
    STOP = 0x02
    STATUS = 0x03


class FctTestId(IntEnum):
    """Board test selected by an FCT request (byte 0 of the payload).

    Note these IDs differ from the legacy :class:`~pcbt_app.protocol.mit_firmware.MotorTestId`.
    """

    GONGWANG_ENCODER = 0x00
    OPEN_LOOP = 0x01
    CAN_STRESS = 0x02
    RS485 = 0x03


class FctCommandResult(IntEnum):
    """Whether the firmware accepted the request (response byte 0); independent of the test state."""

    OK = 0
    INVALID_OPCODE = 1
    INVALID_TEST = 2
    INVALID_PARAMETER = 3
    BUSY = 4
    NOT_RUNNING = 5
    SAFETY_BLOCKED = 6
    INTERNAL_ERROR = 7


class FctState(IntEnum):
    """Progress of the FCT test on the board (response byte 2)."""

    IDLE = 0
    RUNNING = 1
    PASS = 2
    FAIL = 3
    TIMEOUT = 4
    ABORTED = 5

    @property
    def is_terminal(self) -> bool:
        """True once the test has ended and its counters are final."""
        return self in {FctState.PASS, FctState.FAIL, FctState.TIMEOUT, FctState.ABORTED}


@dataclass(slots=True, frozen=True)
class FctIdFields:
    """Decoded FCT CAN ID: ``function[28:24] | opcode[23:16] | source[15:8] | target[7:0]``."""

    function: int
    opcode: int
    source_id: int
    target_id: int


@dataclass(slots=True, frozen=True)
class FctResponse:
    """Decoded FCT response frame.

    Payload layout (8 bytes)::

        0  command result     (FctCommandResult)
        1  test ID            (echo of the request)
        2  test state         (FctState)
        3  high nibbles: bits 3..0 = pass_count[19:16], bits 7..4 = fail_count[19:16]
        4-5 pass_count[15:0]  (little-endian)
        6-7 fail_count[15:0]  (little-endian)
    """

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
    """A frame matched an FCT response but carried an unknown enum value."""

    pass


def _byte(name: str, value: int) -> int:
    if not 0 <= value <= 0xFF:
        raise ValueError(f"{name} must be 0..255")
    return value


def build_fct_id(function: int, opcode: int, source_id: int, target_id: int) -> int:
    """Pack a 29-bit FCT CAN ID; ``function`` is 5 bits, the rest one byte each."""
    if not 0 <= function <= 0x1F:
        raise ValueError("function must be 0..31")
    return (
        (function << 24)
        | (_byte("opcode", opcode) << 16)
        | (_byte("source_id", source_id) << 8)
        | _byte("target_id", target_id)
    )


def parse_fct_id(arbitration_id: int) -> FctIdFields:
    """Split a 29-bit CAN ID into :class:`FctIdFields`."""
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
    """Build an FCT START request.

    Payload: ``test_id``, ``period_ticks``, ``duration_ms`` (24-bit LE) and
    ``parameter_override`` (signed 24-bit LE). Per-test rules enforced here mirror
    the firmware's validation so bad input fails on the PC with a clear message:

    * GongWang and RS485 need ``period_ticks >= 1``; open-loop and CAN stress need 0.
    * RS485 duration is capped by :func:`rs485_max_duration_ms`.
    * Only open-loop uses ``parameter_override`` (theta step in microrad/tick).

    Raises ``ValueError`` on any out-of-range argument.
    """
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
    if test_id is FctTestId.RS485 and duration_ms > rs485_max_duration_ms(period_ticks):
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
    """Build a STOP or STATUS request; the payload is just the test ID."""
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
    """Return the response if ``frame`` answers this request, else ``None``.

    A frame matches when it is an 8-byte FCT response with the expected opcode,
    test ID and source/target IDs. Raises :class:`FctProtocolError` when it
    matches but contains an unknown result/state value.
    """
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
    """Display name of an FCT state, ``-`` for ``None`` or ``UNKNOWN(n)``."""
    if value is None:
        return "-"
    try:
        return FctState(value).name
    except ValueError:
        return f"UNKNOWN({value})"
