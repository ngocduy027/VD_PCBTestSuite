"""Legacy MIT-style diagnostic protocol of the motor-driver firmware.

Parameters are read/written by 16-bit index (``0x72xx`` diagnostic block) with
single-read/write frames; a test is selected by writing ``MotorTestId`` to
``CAN_INDEX_TEST_ACTIVE`` after the unlock key. All multi-byte values are
little-endian.
"""

from __future__ import annotations

import struct
from enum import IntEnum

from pcbt_app.models import CanIdFields, CanStressReportFrame, DiagnosticTestReportFrame, RawCanFrame
from pcbt_app.protocol.ids import parse_extended_id


class MitCommand(IntEnum):
    """Function code in bits 24..31 of the MIT CAN ID."""

    GET_ID = 0x00
    FEEDBACK = 0x02
    ENABLE = 0x03
    DISABLE = 0x04
    PASSIVE_REPORT = 0x0F
    SINGLE_READ = 0x11
    SINGLE_WRITE = 0x12
    DIAG_TEST_REPORT = 0x1B
    CAN_STRESS_FRAME = 0x1C


class MotorTestStatus(IntEnum):
    """Value of ``CAN_INDEX_TEST_STATUS`` while a legacy diagnostic test runs."""

    IDLE = 0
    REQUESTED = 1
    WAITING_DRIVER = 2
    STARTUP_GUARD = 3
    RUNNING = 4
    FINISHED = 5
    STOPPED = 6
    BLOCKED_FAULT = 7


# Statuses meaning the firmware accepted the test and has not finished yet.
MOTOR_TEST_ACTIVE_STATUSES = frozenset(
    {
        MotorTestStatus.REQUESTED,
        MotorTestStatus.WAITING_DRIVER,
        MotorTestStatus.STARTUP_GUARD,
        MotorTestStatus.RUNNING,
    }
)


class MotorTestStopReason(IntEnum):
    """Value of ``CAN_INDEX_TEST_STOP_REASON``; only ``COMPLETED`` counts as a clean finish."""

    NONE = 0
    COMPLETED = 1
    CAN_STOP = 2
    LIVE_STOP = 3
    MIT_DISABLE = 4
    CAN_TIMEOUT = 5
    FAULT_SAFETY = 6
    INVALID_PARAM = 7
    LOCKED = 8
    BUSY = 9


CAN_INDEX_TEST_ACTIVE = 0x7200
CAN_INDEX_TEST_STATUS = 0x7201
CAN_INDEX_TEST_STOP_REASON = 0x7202
CAN_INDEX_TEST_ELAPSED_MS = 0x7203
CAN_INDEX_TEST_UNLOCK = 0x7204
CAN_INDEX_TEST_OPEN_LOOP_VD = 0x7210
CAN_INDEX_TEST_OPEN_LOOP_VQ = 0x7211
CAN_INDEX_TEST_OPEN_LOOP_THETA_STEP = 0x7212
CAN_INDEX_TEST_OPEN_LOOP_DURATION_MS = 0x7213
CAN_INDEX_TEST_CAN_INTERVAL_MS = 0x7220
CAN_INDEX_TEST_CAN_TOTAL_MESSAGES = 0x7221
CAN_INDEX_TEST_CAN_DURATION_MS = CAN_INDEX_TEST_CAN_TOTAL_MESSAGES
CAN_INDEX_TEST_CAN_TX_ATTEMPT_COUNT = 0x7222
CAN_INDEX_TEST_CAN_TX_SUCCESS_COUNT = 0x7223
CAN_INDEX_TEST_CAN_TX_FAIL_COUNT = 0x7224
CAN_INDEX_TEST_CAN_LAST_SEQUENCE = 0x7225
CAN_INDEX_TEST_CAN_LATE_COUNT = 0x7226
CAN_INDEX_TEST_GONGWANG_DURATION_MS = 0x7240
CAN_INDEX_TEST_GONGWANG_SCHEMA_VERSION = 0x7241
CAN_INDEX_TEST_GONGWANG_TARGET_HZ = 0x7242
CAN_INDEX_TEST_GONGWANG_RX_MODE = 0x7243
CAN_INDEX_TEST_GONGWANG_COMMAND = 0x7244
CAN_INDEX_TEST_GONGWANG_RESPONSE_LENGTH = 0x7245
CAN_INDEX_TEST_GONGWANG_SCHEDULED_COUNT = 0x7246
CAN_INDEX_TEST_GONGWANG_STARTED_COUNT = 0x7247
CAN_INDEX_TEST_GONGWANG_RESPONSE_COUNT = 0x7248
CAN_INDEX_TEST_GONGWANG_VALID_COUNT = 0x7249
CAN_INDEX_TEST_GONGWANG_ACTUAL_REQUEST_HZ = 0x724A
CAN_INDEX_TEST_GONGWANG_ACTUAL_RESPONSE_HZ = 0x724B
CAN_INDEX_TEST_GONGWANG_VALID_RESPONSE_HZ = 0x724C
CAN_INDEX_TEST_GONGWANG_SUCCESS_PERCENT = 0x724D
CAN_INDEX_TEST_GONGWANG_RECEIVED_BYTE_COUNT = 0x724E
CAN_INDEX_TEST_GONGWANG_RECEIVED_BYTE_HZ = 0x724F
CAN_INDEX_TEST_GONGWANG_DISCARDED_BYTE_COUNT = 0x7250
CAN_INDEX_TEST_GONGWANG_RING_OVERRUN_COUNT = 0x7251
CAN_INDEX_TEST_GONGWANG_MISSED_TX_SLOT_COUNT = 0x7252
CAN_INDEX_TEST_GONGWANG_BUSY_DEADLINE_COUNT = 0x7253
CAN_INDEX_TEST_GONGWANG_TIMEOUT_COUNT = 0x7254
CAN_INDEX_TEST_GONGWANG_CRC_ERROR_COUNT = 0x7255
CAN_INDEX_TEST_GONGWANG_HEADER_ERROR_COUNT = 0x7256
CAN_INDEX_TEST_GONGWANG_UART_ERROR_COUNT = 0x7257
CAN_INDEX_TEST_GONGWANG_DECODE_QUEUE_OVERFLOW_COUNT = 0x7258
CAN_INDEX_TEST_GONGWANG_FAULT_RESPONSE_COUNT = 0x7259
CAN_INDEX_TEST_GONGWANG_LATEST_STATUS = 0x725A
CAN_INDEX_TEST_GONGWANG_INPUT_SINGLE_TURN_RAW = 0x725B
CAN_INDEX_TEST_GONGWANG_OUTPUT_SINGLE_TURN_RAW = 0x725C
CAN_INDEX_TEST_GONGWANG_OUTPUT_MULTI_TURN_RAW = 0x725D
CAN_INDEX_TEST_GONGWANG_FAULT_CODE = 0x725E
CAN_INDEX_TEST_GONGWANG_INPUT_SINGLE_TURN_DEG = 0x725F
CAN_INDEX_TEST_GONGWANG_OUTPUT_SINGLE_TURN_DEG = 0x7260
CAN_INDEX_TEST_GONGWANG_OUTPUT_TOTAL_TURNS = 0x7261
CAN_INDEX_TEST_GONGWANG_OUTPUT_TOTAL_DEG = 0x7262

class MotorTestId(IntEnum):
    """Value written to ``CAN_INDEX_TEST_ACTIVE`` to select a legacy diagnostic test."""

    NONE = 0
    OPEN_LOOP = 1
    CAN_STRESS = 2
    GONGWANG_ENCODER = 4


MOTOR_TEST_NONE = MotorTestId.NONE
MOTOR_TEST_OPEN_LOOP = MotorTestId.OPEN_LOOP
MOTOR_TEST_CAN_STRESS = MotorTestId.CAN_STRESS
MOTOR_TEST_GONGWANG_ENCODER = MotorTestId.GONGWANG_ENCODER
# Test IDs whose function-0x1B completion reports are consumed (and not treated
# as unrelated traffic) while waiting for another response. ID 3 has no legacy
# workflow in this app but is still recognised so its reports are not dropped.
DIAG_REPORT_TEST_IDS = frozenset({1, 2, 3, 4})
DIAG_TEST_UNLOCK_KEY = 0x54455354
HOST_ID_SCAN_RESPONSE = 0xFE

OPEN_LOOP_DEFAULT_VD = 2.0
OPEN_LOOP_DEFAULT_VQ = 0.0
OPEN_LOOP_DEFAULT_THETA_STEP = -0.00083
OPEN_LOOP_DEFAULT_DURATION_MS = 10_000
OPEN_LOOP_MAX_ABS_THETA_STEP = 0.002  # rad per control tick, accepted by legacy firmware
OPEN_LOOP_MIN_DURATION_MS = 100
OPEN_LOOP_MAX_DURATION_MS = 60_000
OPEN_LOOP_MAX_ABS_THETA_STEP = 0.002  # rad per control tick, accepted by legacy firmware
CAN_STRESS_DEFAULT_INTERVAL_MS = 1
CAN_STRESS_DEFAULT_TOTAL_MESSAGES = 8_000
CAN_STRESS_DEFAULT_DURATION_MS = CAN_STRESS_DEFAULT_TOTAL_MESSAGES
# A CAN stress run passes when strictly more than this share of frames arrives.
CAN_STRESS_MIN_SUCCESS_PERCENT = 95.0
RS485_STRESS_DEFAULT_PERIOD_TICKS = 1
RS485_STRESS_DEFAULT_DURATION_MS = 8_000
RS485_STRESS_DEFAULT_BAUD = 2_500_000
GONGWANG_DEFAULT_DURATION_MS = 8_000
GONGWANG_TIMEOUT_MARGIN_MS = 5_000
GONGWANG_MIN_DURATION_MS = 100
GONGWANG_MAX_DURATION_MS = 60_000
GONGWANG_TIMER_HZ = 10_000_000
GONGWANG_EXPECTED_SCHEMA_VERSION = 1
GONGWANG_EXPECTED_TARGET_HZ = 20_000
GONGWANG_EXPECTED_RX_MODE = 1
GONGWANG_EXPECTED_COMMAND = 0x9A
GONGWANG_EXPECTED_RESPONSE_LENGTH = 10
GONGWANG_MIN_SUCCESS_PERCENT = 80.0
GONGWANG_MIN_RATE_PERCENT = 80.0


def validate_byte(name: str, value: int) -> int:
    """Return ``value`` unchanged, or raise ``ValueError`` if it does not fit in one byte."""
    if not 0 <= value <= 0xFF:
        raise ValueError(f"{name} must fit in one byte")
    return value


def build_mit_arbitration_id(command: int, target_id: int, byte2: int = 0, byte1: int = 0) -> int:
    """Pack an MIT CAN ID: ``command | byte2 | byte1 | target_id`` (one byte each, MSB first)."""
    validate_byte("command", command)
    validate_byte("target_id", target_id)
    validate_byte("byte2", byte2)
    validate_byte("byte1", byte1)
    return ((command & 0xFF) << 24) | ((byte2 & 0xFF) << 16) | ((byte1 & 0xFF) << 8) | (target_id & 0xFF)


def build_get_id_frame() -> RawCanFrame:
    """Broadcast ``Get ID`` (target 0); every driver on the bus should answer with its ID."""
    return RawCanFrame(
        arbitration_id=build_mit_arbitration_id(MitCommand.GET_ID, 0),
        data=b"\x00",
    )


def build_scan_id_frame(target_id: int) -> RawCanFrame:
    """``Get ID`` addressed to one ``target_id``; used by the fallback 0..255 scan."""
    return RawCanFrame(
        arbitration_id=build_mit_arbitration_id(MitCommand.GET_ID, target_id),
        data=b"\x00",
    )


def build_enable_frame(driver_id: int) -> RawCanFrame:
    """Enable the motor driver output stage."""
    return RawCanFrame(
        arbitration_id=build_mit_arbitration_id(MitCommand.ENABLE, driver_id),
        data=b"\x00" * 8,
    )


def build_disable_frame(driver_id: int) -> RawCanFrame:
    """Disable the motor driver output stage."""
    return RawCanFrame(
        arbitration_id=build_mit_arbitration_id(MitCommand.DISABLE, driver_id),
        data=b"\x00" * 8,
    )


def build_passive_report_frame(driver_id: int) -> RawCanFrame:
    """Ask the driver for one feedback frame; used as stress traffic by the FCT CAN stress test."""
    return RawCanFrame(
        arbitration_id=build_mit_arbitration_id(MitCommand.PASSIVE_REPORT, driver_id),
        data=b"\x00" * 8,
    )


def build_single_read_frame(driver_id: int, index: int) -> RawCanFrame:
    """Read the parameter at ``index``; the reply echoes the index with the value in bytes 4..7."""
    return RawCanFrame(
        arbitration_id=build_mit_arbitration_id(MitCommand.SINGLE_READ, driver_id),
        data=pack_index_payload(index, b"\x00\x00\x00\x00"),
    )


def build_single_write_u32_frame(driver_id: int, index: int, value: int) -> RawCanFrame:
    """Write an unsigned 32-bit parameter (little-endian)."""
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("value must fit in uint32")
    return RawCanFrame(
        arbitration_id=build_mit_arbitration_id(MitCommand.SINGLE_WRITE, driver_id),
        data=pack_index_payload(index, value.to_bytes(4, "little")),
    )


def build_single_write_float_frame(driver_id: int, index: int, value: float) -> RawCanFrame:
    """Write a float32 parameter (little-endian IEEE-754)."""
    return RawCanFrame(
        arbitration_id=build_mit_arbitration_id(MitCommand.SINGLE_WRITE, driver_id),
        data=pack_index_payload(index, struct.pack("<f", value)),
    )


def build_openloop_default_write_frames(
    driver_id: int,
    theta_step: float = OPEN_LOOP_DEFAULT_THETA_STEP,
    duration_ms: int = OPEN_LOOP_DEFAULT_DURATION_MS,
) -> list[RawCanFrame]:
    """Frames configuring open-loop: fixed Vd/Vq, the given theta step (rad/tick) and duration."""
    return [
        build_single_write_float_frame(driver_id, CAN_INDEX_TEST_OPEN_LOOP_VD, OPEN_LOOP_DEFAULT_VD),
        build_single_write_float_frame(driver_id, CAN_INDEX_TEST_OPEN_LOOP_VQ, OPEN_LOOP_DEFAULT_VQ),
        build_single_write_float_frame(driver_id, CAN_INDEX_TEST_OPEN_LOOP_THETA_STEP, theta_step),
        build_single_write_u32_frame(driver_id, CAN_INDEX_TEST_OPEN_LOOP_DURATION_MS, duration_ms),
    ]


def build_openloop_start_frame(driver_id: int) -> RawCanFrame:
    """Select the open-loop test; requires the unlock frame to have been sent first."""
    return build_single_write_u32_frame(driver_id, CAN_INDEX_TEST_ACTIVE, MOTOR_TEST_OPEN_LOOP)


def build_openloop_unlock_frame(driver_id: int) -> RawCanFrame:
    """Write ``DIAG_TEST_UNLOCK_KEY`` (ASCII ``TEST``); the firmware refuses to start a test without it."""
    return build_single_write_u32_frame(driver_id, CAN_INDEX_TEST_UNLOCK, DIAG_TEST_UNLOCK_KEY)


def build_openloop_stop_frame(driver_id: int) -> RawCanFrame:
    """Stop any active diagnostic test (writes ``MotorTestId.NONE``)."""
    return build_single_write_u32_frame(driver_id, CAN_INDEX_TEST_ACTIVE, MOTOR_TEST_NONE)


def build_can_stress_config_frames(
    driver_id: int,
    interval_ms: int = CAN_STRESS_DEFAULT_INTERVAL_MS,
    total_messages: int = CAN_STRESS_DEFAULT_TOTAL_MESSAGES,
) -> list[RawCanFrame]:
    """Frames configuring the CAN stress interval and message count."""
    return [
        build_single_write_u32_frame(driver_id, CAN_INDEX_TEST_CAN_INTERVAL_MS, interval_ms),
        build_single_write_u32_frame(driver_id, CAN_INDEX_TEST_CAN_TOTAL_MESSAGES, total_messages),
    ]


def build_can_stress_start_frame(driver_id: int) -> RawCanFrame:
    """Select the CAN stress test; requires the unlock frame first."""
    return build_single_write_u32_frame(driver_id, CAN_INDEX_TEST_ACTIVE, MOTOR_TEST_CAN_STRESS)


def build_can_stress_stop_frame(driver_id: int) -> RawCanFrame:
    """Stop any active diagnostic test (writes ``MotorTestId.NONE``)."""
    return build_single_write_u32_frame(driver_id, CAN_INDEX_TEST_ACTIVE, MOTOR_TEST_NONE)


def build_gongwang_config_frames(
    driver_id: int,
    duration_ms: int = GONGWANG_DEFAULT_DURATION_MS,
) -> list[RawCanFrame]:
    """Frames configuring the GongWang test duration."""
    return [build_single_write_u32_frame(driver_id, CAN_INDEX_TEST_GONGWANG_DURATION_MS, duration_ms)]


def build_gongwang_start_frame(driver_id: int) -> RawCanFrame:
    """Select the GongWang encoder test; requires the unlock frame first."""
    return build_single_write_u32_frame(driver_id, CAN_INDEX_TEST_ACTIVE, MOTOR_TEST_GONGWANG_ENCODER)


def build_gongwang_stop_frame(driver_id: int) -> RawCanFrame:
    """Stop any active diagnostic test (writes ``MotorTestId.NONE``)."""
    return build_single_write_u32_frame(driver_id, CAN_INDEX_TEST_ACTIVE, MOTOR_TEST_NONE)


def pack_index_payload(index: int, value_le_4: bytes) -> bytes:
    """8-byte read/write payload: ``index`` (u16 LE), two zero bytes, then the 4-byte LE value."""
    if not 0 <= index <= 0xFFFF:
        raise ValueError("index must fit in uint16")
    if len(value_le_4) != 4:
        raise ValueError("value payload must be exactly 4 bytes")
    return bytes([index & 0xFF, (index >> 8) & 0xFF, 0x00, 0x00]) + value_le_4


def parse_mit_id(frame: RawCanFrame) -> CanIdFields | None:
    """Decode the CAN ID of an extended frame; ``None`` for standard-ID frames."""
    if not frame.is_extended_id:
        return None
    return parse_extended_id(frame.arbitration_id)


def parse_scan_response(frame: RawCanFrame) -> int | None:
    """Driver ID from a ``Get ID`` reply (host ID ``0xFE``), else ``None``."""
    fields = parse_mit_id(frame)
    if fields is None:
        return None
    if fields.function_code == MitCommand.GET_ID and fields.host_id == HOST_ID_SCAN_RESPONSE:
        return fields.driver_id
    return None


def is_feedback_response(frame: RawCanFrame, driver_id: int) -> bool:
    """True if ``frame`` is a feedback frame from ``driver_id``."""
    fields = parse_mit_id(frame)
    return fields is not None and fields.function_code == MitCommand.FEEDBACK and fields.driver_id == driver_id


def read_param_value_bytes(frame: RawCanFrame, driver_id: int) -> bytes | None:
    """The 4 value bytes of a single-read reply from ``driver_id``, else ``None``."""
    fields = parse_mit_id(frame)
    if fields is None:
        return None
    if fields.function_code != MitCommand.SINGLE_READ or fields.driver_id != driver_id:
        return None
    if len(frame.data) < 8:
        return None
    return bytes(frame.data[4:8])


def read_param_value_u32(frame: RawCanFrame, driver_id: int) -> int | None:
    """Single-read reply value as uint32, or ``None`` if the frame is not a matching reply."""
    value = read_param_value_bytes(frame, driver_id)
    return None if value is None else int.from_bytes(value, "little")


def read_param_value_float(frame: RawCanFrame, driver_id: int) -> float | None:
    """Single-read reply value as float32, or ``None`` if the frame is not a matching reply."""
    value = read_param_value_bytes(frame, driver_id)
    return None if value is None else struct.unpack("<f", value)[0]


def parse_can_stress_frame(frame: RawCanFrame, driver_id: int | None = None) -> CanStressReportFrame | None:
    """Decode a stress frame (function ``0x1C``): sequence (u32 LE) and board elapsed ms (u32 LE)."""
    fields = parse_mit_id(frame)
    if fields is None:
        return None
    if fields.function_code != MitCommand.CAN_STRESS_FRAME:
        return None
    if driver_id is not None and fields.driver_id != driver_id:
        return None
    if len(frame.data) < 8:
        return None
    return CanStressReportFrame(
        raw_frame=frame,
        sequence=int.from_bytes(frame.data[0:4], "little"),
        elapsed_ms=int.from_bytes(frame.data[4:8], "little"),
    )


def parse_diagnostic_test_report(
    frame: RawCanFrame,
    driver_id: int | None = None,
    test_id: int | None = None,
) -> DiagnosticTestReportFrame | None:
    """Decode a test completion report (function ``0x1B``).

    Payload: test ID (u16 LE), status, stop reason, elapsed ms (u32 LE).
    ``driver_id``/``test_id`` filter the frame when given.
    """
    fields = parse_mit_id(frame)
    if fields is None or fields.function_code != MitCommand.DIAG_TEST_REPORT:
        return None
    if driver_id is not None and fields.driver_id != driver_id:
        return None
    if len(frame.data) < 8:
        return None
    report_test_id = int.from_bytes(frame.data[0:2], "little")
    if test_id is not None and report_test_id != test_id:
        return None
    return DiagnosticTestReportFrame(
        raw_frame=frame,
        test_id=report_test_id,
        status=frame.data[2],
        stop_reason=frame.data[3],
        elapsed_ms=int.from_bytes(frame.data[4:8], "little"),
    )


def parse_can_stress_report(frame: RawCanFrame, driver_id: int | None = None) -> CanStressReportFrame | None:
    """Decode a function-``0x1B`` frame as a CAN stress report (sequence + elapsed ms).

    Shares the function code with :func:`parse_diagnostic_test_report`; callers
    try that parser first.
    """
    fields = parse_mit_id(frame)
    if fields is None:
        return None
    if fields.function_code != MitCommand.DIAG_TEST_REPORT:
        return None
    if driver_id is not None and fields.driver_id != driver_id:
        return None
    if len(frame.data) < 8:
        return None
    return CanStressReportFrame(
        raw_frame=frame,
        sequence=int.from_bytes(frame.data[0:4], "little"),
        elapsed_ms=int.from_bytes(frame.data[4:8], "little"),
    )


def status_label(value: int | None) -> str:
    """Display name of a :class:`MotorTestStatus` value, ``-`` for ``None``."""
    if value is None:
        return "-"
    try:
        return MotorTestStatus(value).name
    except ValueError:
        return f"UNKNOWN({value})"


def stop_reason_label(value: int | None) -> str:
    """Display name of a :class:`MotorTestStopReason` value, ``-`` for ``None``."""
    if value is None:
        return "-"
    try:
        return MotorTestStopReason(value).name
    except ValueError:
        return f"UNKNOWN({value})"
