from __future__ import annotations

from pcbt_app.config import EXPECTED_HOST_ID, MOTOR_ID_RESPONSE_FUNCTION, PCB_TEST_RESULT_FUNCTION
from pcbt_app.models import CanIdResponseFrame, ParsedFrame, PcbTestResultFrame, RawCanFrame
from pcbt_app.protocol.ids import parse_extended_id


def parse_frame(frame: RawCanFrame) -> ParsedFrame:
    if not frame.is_extended_id:
        return ParsedFrame(raw_frame=frame, kind="ignored")

    id_fields = parse_extended_id(frame.arbitration_id)

    if id_fields.function_code == MOTOR_ID_RESPONSE_FUNCTION and id_fields.host_id == EXPECTED_HOST_ID:
        can_response = CanIdResponseFrame(raw_frame=frame, id_fields=id_fields, data=frame.data)
        return ParsedFrame(
            raw_frame=frame,
            kind="can_id_response",
            can_id_response=can_response,
            id_fields=id_fields,
        )

    if id_fields.function_code == PCB_TEST_RESULT_FUNCTION and id_fields.host_id == EXPECTED_HOST_ID:
        result = PcbTestResultFrame(
            raw_frame=frame,
            id_fields=id_fields,
            test_id=frame.data[0],
            result=frame.data[1],
            fail_mask=frame.data[2],
            actual_return_id=frame.data[3],
            fsr1=frame.data[4] | (frame.data[5] << 8),
            fsr2=frame.data[6] | (frame.data[7] << 8),
        )
        return ParsedFrame(
            raw_frame=frame,
            kind="pcb_test_result",
            pcb_result=result,
            id_fields=id_fields,
        )

    return ParsedFrame(raw_frame=frame, kind="unrelated", id_fields=id_fields)
