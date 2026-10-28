from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from pcbt_app.models import RawCanFrame
from pcbt_app.protocol.fct_can import FCT_HOST_ID, build_fct_id, parse_fct_id


GW_CALIBRATION_REQUEST_FUNCTION = 0x1E
GW_CALIBRATION_RESPONSE_FUNCTION = 0x1F
GW_CALIBRATION_START_OPCODE = 0x10
GW_CALIBRATION_PROGRESS_OPCODE = 0x12
GW_CALIBRATION_RESULT_OPCODE = 0x13
GW_CALIBRATION_DLC = 8
GW_CALIBRATION_COMMAND = 0x76
GW_CALIBRATION_SUCCESS_STEP = 0x11
GW_MOTOR_RESOLUTION = 131_072
GW_OUTPUT_RESOLUTION = 524_288


class GwCalibrationStartResult(IntEnum):
    ACCEPTED = 0x00
    BUSY = 0x01
    NOT_INITIALIZED = 0x02


class GwCalibrationOutcome(IntEnum):
    PASS = 0x01
    ENCODER_FAILURE = 0x02
    UART_FAILURE = 0x03
    OPENLOOP_FAILURE = 0x04


class GwCalibrationRecordType(IntEnum):
    NONE = 0x00
    MOTOR = 0x01
    OUTPUT = 0x02
    ERROR = 0x03


class GwCalibrationProtocolError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class GwCalibrationStartAck:
    result: GwCalibrationStartResult
    firmware_state: int


@dataclass(slots=True, frozen=True)
class GwCalibrationProgress:
    request: int
    step: int
    encoder_status: int


@dataclass(slots=True, frozen=True)
class GwCalibrationFragment:
    outcome: GwCalibrationOutcome
    record_type: GwCalibrationRecordType
    index: int
    payload: bytes


@dataclass(slots=True, frozen=True)
class GwCalibrationResultRecord:
    master_sin_amplitude_v: float
    master_sin_center_v: float
    master_cos_amplitude_v: float
    master_cos_center_v: float
    master_sin_cos_percent: float
    slave_sin_amplitude_v: float
    slave_sin_center_v: float
    slave_cos_amplitude_v: float
    slave_cos_center_v: float
    slave_sin_cos_percent: float
    relative_deviation_deg: float
    status: int


@dataclass(slots=True, frozen=True)
class GwCalibrationEncoderError:
    motor_code: int
    motor_value: int
    output_code: int
    output_value: int


@dataclass(slots=True, frozen=True)
class GwCalibrationResult:
    outcome: GwCalibrationOutcome
    motor: GwCalibrationResultRecord | None = None
    output: GwCalibrationResultRecord | None = None
    encoder_error: GwCalibrationEncoderError | None = None


ERROR_CODE_MESSAGES = {
    0x00: "Không có lỗi",
    0xE1: "Khoảng cách quá hẹp",
    0xE2: "Khoảng cách quá rộng",
    0xE3: "Tỉ lệ biên độ Sin/Cos quá thấp",
    0xE4: "Độ lệch tương đối giữa kênh chính và kênh phụ quá lớn",
    0xE5: "Kênh chính và kênh phụ ngược chiều nhau",
    0xE6: "Chiều quay không được khóa",
    0xE7: "Calibration bị timeout",
}


def build_gw_calibration_start(*, driver_id: int, host_id: int = FCT_HOST_ID) -> RawCanFrame:
    return RawCanFrame(
        build_fct_id(
            GW_CALIBRATION_REQUEST_FUNCTION,
            GW_CALIBRATION_START_OPCODE,
            host_id,
            driver_id,
        ),
        b"\x00" * GW_CALIBRATION_DLC,
        is_extended_id=True,
    )


def build_gw_calibration_read_result(*, driver_id: int, host_id: int = FCT_HOST_ID) -> RawCanFrame:
    return RawCanFrame(
        build_fct_id(
            GW_CALIBRATION_REQUEST_FUNCTION,
            GW_CALIBRATION_RESULT_OPCODE,
            host_id,
            driver_id,
        ),
        b"\x00" * GW_CALIBRATION_DLC,
        is_extended_id=True,
    )


def _matching_payload(
    frame: RawCanFrame,
    *,
    opcode: int,
    driver_id: int,
    host_id: int,
) -> bytes | None:
    if not frame.is_extended_id or len(frame.data) != GW_CALIBRATION_DLC:
        return None
    fields = parse_fct_id(frame.arbitration_id)
    if (
        fields.function != GW_CALIBRATION_RESPONSE_FUNCTION
        or fields.opcode != opcode
        or fields.source_id != driver_id
        or fields.target_id != host_id
    ):
        return None
    return frame.data


def parse_gw_calibration_start_ack(
    frame: RawCanFrame,
    *,
    driver_id: int,
    host_id: int = FCT_HOST_ID,
) -> GwCalibrationStartAck | None:
    data = _matching_payload(
        frame,
        opcode=GW_CALIBRATION_START_OPCODE,
        driver_id=driver_id,
        host_id=host_id,
    )
    if data is None:
        return None
    try:
        result = GwCalibrationStartResult(data[0])
    except ValueError as exc:
        raise GwCalibrationProtocolError(f"unknown calibration START result 0x{data[0]:02X}") from exc
    if any(data[2:]):
        raise GwCalibrationProtocolError("calibration START acknowledgement padding is not zero")
    return GwCalibrationStartAck(result=result, firmware_state=data[1])


def parse_gw_calibration_progress(
    frame: RawCanFrame,
    *,
    driver_id: int,
    host_id: int = FCT_HOST_ID,
) -> GwCalibrationProgress | None:
    data = _matching_payload(
        frame,
        opcode=GW_CALIBRATION_PROGRESS_OPCODE,
        driver_id=driver_id,
        host_id=host_id,
    )
    if data is None:
        return None
    raw = data[:5]
    if raw[0] != GW_CALIBRATION_COMMAND:
        raise GwCalibrationProtocolError("invalid calibration progress command echo")
    if raw[1] not in {0x00, 0x01}:
        raise GwCalibrationProtocolError("invalid calibration progress request byte")
    if _xor(raw) != 0:
        raise GwCalibrationProtocolError("invalid calibration progress XOR")
    return GwCalibrationProgress(request=raw[1], step=raw[2], encoder_status=raw[3])


def parse_gw_calibration_fragment(
    frame: RawCanFrame,
    *,
    driver_id: int,
    host_id: int = FCT_HOST_ID,
) -> GwCalibrationFragment | None:
    data = _matching_payload(
        frame,
        opcode=GW_CALIBRATION_RESULT_OPCODE,
        driver_id=driver_id,
        host_id=host_id,
    )
    if data is None:
        return None
    try:
        outcome = GwCalibrationOutcome(data[0])
        record_type = GwCalibrationRecordType(data[1])
    except ValueError as exc:
        raise GwCalibrationProtocolError("unknown calibration result metadata") from exc
    return GwCalibrationFragment(outcome, record_type, data[2], data[3:8])


def decode_gw_calibration_record(raw: bytes, *, resolution: int) -> GwCalibrationResultRecord:
    expected_request = 0x02 if resolution == GW_MOTOR_RESOLUTION else 0x04
    if len(raw) != 22 or raw[0] != GW_CALIBRATION_COMMAND or raw[1] != expected_request:
        raise GwCalibrationProtocolError("invalid calibration result record header or length")
    if _xor(raw) != 0:
        raise GwCalibrationProtocolError("invalid calibration result record XOR")

    values = [int.from_bytes(raw[offset : offset + 2], "little", signed=True) for offset in range(2, 20, 2)]
    master_sin_amp, master_sin_center, master_cos_amp, master_cos_center = values[:4]
    slave_sin_amp, slave_sin_center, slave_cos_amp, slave_cos_center, deviation = values[4:]
    return GwCalibrationResultRecord(
        master_sin_amplitude_v=_amplitude_v(master_sin_amp),
        master_sin_center_v=_center_v(master_sin_center),
        master_cos_amplitude_v=_amplitude_v(master_cos_amp),
        master_cos_center_v=_center_v(master_cos_center),
        master_sin_cos_percent=_ratio_percent(master_sin_amp, master_cos_amp),
        slave_sin_amplitude_v=_amplitude_v(slave_sin_amp),
        slave_sin_center_v=_center_v(slave_sin_center),
        slave_cos_amplitude_v=_amplitude_v(slave_cos_amp),
        slave_cos_center_v=_center_v(slave_cos_center),
        slave_sin_cos_percent=_ratio_percent(slave_sin_amp, slave_cos_amp),
        relative_deviation_deg=deviation * 360.0 / resolution,
        status=raw[20],
    )


def decode_gw_calibration_error(raw: bytes) -> GwCalibrationEncoderError:
    if len(raw) != 9 or raw[0] != GW_CALIBRATION_COMMAND or raw[1] != 0x0A:
        raise GwCalibrationProtocolError("invalid calibration error record header or length")
    if _xor(raw) != 0:
        raise GwCalibrationProtocolError("invalid calibration error record XOR")
    return GwCalibrationEncoderError(
        motor_code=raw[2],
        motor_value=int.from_bytes(raw[3:5], "little"),
        output_code=raw[5],
        output_value=int.from_bytes(raw[6:8], "little"),
    )


class GwCalibrationAssembler:
    def __init__(self) -> None:
        self._outcome: GwCalibrationOutcome | None = None
        self._record_type: GwCalibrationRecordType | None = None
        self._next_index = 0
        self._buffers: dict[GwCalibrationRecordType, bytearray] = {}

    @property
    def started(self) -> bool:
        return self._outcome is not None

    def add(self, fragment: GwCalibrationFragment) -> GwCalibrationResult | None:
        if self._outcome is None:
            self._outcome = fragment.outcome
        elif fragment.outcome != self._outcome:
            raise GwCalibrationProtocolError("Incomplete calibration result: outcome changed")

        expected_types = {
            GwCalibrationOutcome.PASS: (GwCalibrationRecordType.MOTOR, GwCalibrationRecordType.OUTPUT),
            GwCalibrationOutcome.ENCODER_FAILURE: (GwCalibrationRecordType.ERROR,),
            GwCalibrationOutcome.UART_FAILURE: (GwCalibrationRecordType.NONE,),
            GwCalibrationOutcome.OPENLOOP_FAILURE: (GwCalibrationRecordType.NONE,),
        }[self._outcome]
        if fragment.record_type not in expected_types:
            raise GwCalibrationProtocolError("Incomplete calibration result: unexpected record type")

        if fragment.record_type != self._record_type:
            expected_position = len(self._buffers)
            if expected_position >= len(expected_types) or fragment.record_type != expected_types[expected_position]:
                raise GwCalibrationProtocolError("Incomplete calibration result: records are out of order")
            self._record_type = fragment.record_type
            self._next_index = 0
            self._buffers[fragment.record_type] = bytearray()
        if fragment.index != self._next_index:
            raise GwCalibrationProtocolError("Incomplete calibration result: fragment index is missing or out of order")
        self._buffers[fragment.record_type].extend(fragment.payload)
        self._next_index += 1

        required_fragments = {
            GwCalibrationRecordType.NONE: 1,
            GwCalibrationRecordType.MOTOR: 5,
            GwCalibrationRecordType.OUTPUT: 5,
            GwCalibrationRecordType.ERROR: 2,
        }[fragment.record_type]
        if self._next_index < required_fragments:
            return None

        if self._outcome is GwCalibrationOutcome.PASS:
            if fragment.record_type is GwCalibrationRecordType.MOTOR:
                return None
            motor_raw = bytes(self._buffers[GwCalibrationRecordType.MOTOR][:22])
            output_raw = bytes(self._buffers[GwCalibrationRecordType.OUTPUT][:22])
            return GwCalibrationResult(
                self._outcome,
                motor=decode_gw_calibration_record(motor_raw, resolution=GW_MOTOR_RESOLUTION),
                output=decode_gw_calibration_record(output_raw, resolution=GW_OUTPUT_RESOLUTION),
            )
        if self._outcome is GwCalibrationOutcome.ENCODER_FAILURE:
            raw = bytes(self._buffers[GwCalibrationRecordType.ERROR][:9])
            return GwCalibrationResult(self._outcome, encoder_error=decode_gw_calibration_error(raw))
        return GwCalibrationResult(self._outcome)


def describe_encoder_error(code: int, value: int) -> str:
    message = ERROR_CODE_MESSAGES.get(code)
    if message is None:
        return f"Lỗi không xác định 0x{code:02X} (giá trị raw: {value})"
    if code == 0x00:
        return f"{message} (0x00)"
    if code in {0xE1, 0xE2}:
        amplitude_mv = _amplitude_v(value) * 1000.0
        converted = f"biên độ: {amplitude_mv:.2f} mV"
    elif code == 0xE3:
        converted = f"tỉ lệ: {value}%"
    elif code == 0xE4:
        converted = f"độ lệch: {value / 1000.0:.3f}°"
    elif code in {0xE5, 0xE6}:
        converted = "chưa rõ cách quy đổi"
    else:
        converted = f"thời gian: {value} ms"
    return f"{message} (0x{code:02X}, {converted}, raw: {value})"


def _xor(data: bytes) -> int:
    value = 0
    for item in data:
        value ^= item
    return value


def _amplitude_v(raw: int) -> float:
    return raw * 0.5 * 3.657 / 8192.0


def _center_v(raw: int) -> float:
    return raw * 3.657 / 8192.0


def _ratio_percent(sin_amplitude: int, cos_amplitude: int) -> float:
    larger = max(abs(sin_amplitude), abs(cos_amplitude))
    return 0.0 if larger == 0 else min(abs(sin_amplitude), abs(cos_amplitude)) / larger * 100.0
