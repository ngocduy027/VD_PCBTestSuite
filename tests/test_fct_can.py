from __future__ import annotations

from collections import deque
from threading import Thread
from time import sleep

import pytest

from pcbt_app.models import RawCanFrame
from pcbt_app.protocol.fct_can import (
    FCT_HOST_ID,
    FCT_REQUEST_FUNCTION,
    FCT_RESPONSE_FUNCTION,
    FctCommandResult,
    FctOpcode,
    FctProtocolError,
    FctState,
    FctTestId,
    build_fct_id,
    build_fct_simple,
    build_fct_start,
    parse_fct_id,
    parse_matching_fct_response,
)
from pcbt_app.services.fct_client import FctClient, FctResponseTimeout
from pcbt_app.transport.fake_bus import FakeCanTransport


DRIVER_ID = 0x7F


def response_frame(opcode, test_id, state=FctState.RUNNING, result=FctCommandResult.OK, *, passed=0, failed=0):
    assert 0 <= passed <= 0xFFFFF
    assert 0 <= failed <= 0xFFFFF
    counter_msb = ((failed >> 16) << 4) | (passed >> 16)
    return RawCanFrame(
        build_fct_id(FCT_RESPONSE_FUNCTION, opcode, DRIVER_ID, FCT_HOST_ID),
        bytes((result, test_id, state, counter_msb))
        + (passed & 0xFFFF).to_bytes(2, "little")
        + (failed & 0xFFFF).to_bytes(2, "little"),
    )


def test_openloop_start_matches_documented_frame() -> None:
    frame = build_fct_start(
        host_id=FCT_HOST_ID,
        driver_id=DRIVER_ID,
        test_id=FctTestId.OPEN_LOOP,
        period_ticks=0,
        duration_ms=3000,
        parameter_override=400,
    )
    assert frame.arbitration_id == 0x1E01FD7F
    assert frame.data == bytes.fromhex("01 00 B8 0B 00 90 01 00")
    assert frame.is_extended_id
    fields = parse_fct_id(0x1F017FFD)
    assert (fields.function, fields.opcode, fields.source_id, fields.target_id) == (0x1F, 1, 0x7F, 0xFD)


def test_gongwang_start_uses_one_20khz_tick() -> None:
    frame = build_fct_start(
        host_id=FCT_HOST_ID,
        driver_id=DRIVER_ID,
        test_id=FctTestId.GONGWANG_ENCODER,
        period_ticks=1,
        duration_ms=2000,
    )
    assert frame.arbitration_id == 0x1E01FD7F
    assert frame.data == bytes.fromhex("00 01 D0 07 00 00 00 00")


def test_rs485_start_matches_new_firmware_contract() -> None:
    frame = build_fct_start(
        host_id=FCT_HOST_ID,
        driver_id=DRIVER_ID,
        test_id=FctTestId.RS485,
        period_ticks=1,
        duration_ms=52_428,
    )
    assert frame.data == bytes.fromhex("03 01 CC CC 00 00 00 00")
    with pytest.raises(ValueError, match="period_ticks=1..255"):
        build_fct_start(
            host_id=FCT_HOST_ID,
            driver_id=DRIVER_ID,
            test_id=FctTestId.RS485,
            period_ticks=0,
            duration_ms=1,
        )
    with pytest.raises(ValueError, match="transmission-count limit"):
        build_fct_start(
            host_id=FCT_HOST_ID,
            driver_id=DRIVER_ID,
            test_id=FctTestId.RS485,
            period_ticks=1,
            duration_ms=52_429,
        )


@pytest.mark.parametrize("opcode", [FctOpcode.STOP, FctOpcode.STATUS])
def test_simple_requests_are_extended_dlc_8(opcode) -> None:
    frame = build_fct_simple(
        host_id=FCT_HOST_ID, driver_id=DRIVER_ID, opcode=opcode, test_id=FctTestId.CAN_STRESS
    )
    assert frame.is_extended_id
    assert len(frame.data) == 8
    assert frame.data == b"\x02" + b"\x00" * 7


def test_start_validation_is_test_specific() -> None:
    with pytest.raises(ValueError, match="nonzero tick period"):
        build_fct_start(host_id=FCT_HOST_ID, driver_id=1, test_id=FctTestId.GONGWANG_ENCODER, period_ticks=0, duration_ms=1)
    with pytest.raises(ValueError, match="period_ticks=0"):
        build_fct_start(host_id=FCT_HOST_ID, driver_id=1, test_id=FctTestId.OPEN_LOOP, period_ticks=1, duration_ms=1)
    with pytest.raises(ValueError, match="unsigned 24-bit"):
        build_fct_start(host_id=FCT_HOST_ID, driver_id=1, test_id=FctTestId.CAN_STRESS, period_ticks=0, duration_ms=0)
    with pytest.raises(ValueError, match="uint8"):
        build_fct_start(
            host_id=FCT_HOST_ID,
            driver_id=1,
            test_id=FctTestId.GONGWANG_ENCODER,
            period_ticks=256,
            duration_ms=1,
        )
    with pytest.raises(ValueError, match="unsigned 24-bit"):
        build_fct_start(
            host_id=FCT_HOST_ID,
            driver_id=1,
            test_id=FctTestId.CAN_STRESS,
            period_ticks=0,
            duration_ms=0x1000000,
        )
    with pytest.raises(ValueError, match="signed 24-bit"):
        build_fct_start(
            host_id=FCT_HOST_ID,
            driver_id=1,
            test_id=FctTestId.OPEN_LOOP,
            period_ticks=0,
            duration_ms=1,
            parameter_override=0x800000,
        )
    with pytest.raises(ValueError, match="-1570..1570"):
        build_fct_start(
            host_id=FCT_HOST_ID,
            driver_id=1,
            test_id=FctTestId.OPEN_LOOP,
            period_ticks=0,
            duration_ms=1,
            parameter_override=1571,
        )


@pytest.mark.parametrize(
    ("parameter_override", "expected_tail"),
    [
        (-1570, "DE F9 FF"),
        (0, "00 00 00"),
        (1570, "22 06 00"),
    ],
)
def test_openloop_override_uses_signed_24bit_little_endian(
    parameter_override: int, expected_tail: str
) -> None:
    frame = build_fct_start(
        host_id=FCT_HOST_ID,
        driver_id=DRIVER_ID,
        test_id=FctTestId.OPEN_LOOP,
        period_ticks=0,
        duration_ms=1000,
        parameter_override=parameter_override,
    )
    assert frame.data == bytes.fromhex(f"01 00 E8 03 00 {expected_tail}")


def test_matching_response_filters_every_id_field_and_decodes_20bit_counters() -> None:
    good = response_frame(
        FctOpcode.STATUS,
        FctTestId.OPEN_LOOP,
        FctState.PASS,
        passed=0x12345,
        failed=0xABCDE,
    )
    parsed = parse_matching_fct_response(
        good,
        expected_opcode=FctOpcode.STATUS,
        expected_test_id=FctTestId.OPEN_LOOP,
        host_id=FCT_HOST_ID,
        driver_id=DRIVER_ID,
    )
    assert parsed is not None
    assert (parsed.pass_count, parsed.fail_count) == (0x12345, 0xABCDE)

    wrong_ids = [
        build_fct_id(FCT_REQUEST_FUNCTION, FctOpcode.STATUS, DRIVER_ID, FCT_HOST_ID),
        build_fct_id(FCT_RESPONSE_FUNCTION, FctOpcode.START, DRIVER_ID, FCT_HOST_ID),
        build_fct_id(FCT_RESPONSE_FUNCTION, FctOpcode.STATUS, DRIVER_ID + 1, FCT_HOST_ID),
        build_fct_id(FCT_RESPONSE_FUNCTION, FctOpcode.STATUS, DRIVER_ID, FCT_HOST_ID - 1),
    ]
    for arbitration_id in wrong_ids:
        assert parse_matching_fct_response(
            RawCanFrame(arbitration_id, good.data),
            expected_opcode=FctOpcode.STATUS,
            expected_test_id=FctTestId.OPEN_LOOP,
            host_id=FCT_HOST_ID,
            driver_id=DRIVER_ID,
        ) is None
    assert parse_matching_fct_response(
        RawCanFrame(good.arbitration_id, bytes((0, FctTestId.CAN_STRESS, 2, 0, 0, 0, 0, 0))),
        expected_opcode=FctOpcode.STATUS,
        expected_test_id=FctTestId.OPEN_LOOP,
        host_id=FCT_HOST_ID,
        driver_id=DRIVER_ID,
    ) is None


def test_unknown_response_enum_has_protocol_context() -> None:
    frame = response_frame(FctOpcode.STATUS, FctTestId.OPEN_LOOP)
    frame = RawCanFrame(frame.arbitration_id, bytes((99, 1, 88, 0, 0, 0, 0, 0)))
    with pytest.raises(FctProtocolError, match="result=99 state=88"):
        parse_matching_fct_response(
            frame,
            expected_opcode=FctOpcode.STATUS,
            expected_test_id=FctTestId.OPEN_LOOP,
            host_id=FCT_HOST_ID,
            driver_id=DRIVER_ID,
        )


def test_client_skips_unrelated_frame_and_times_out_cleanly() -> None:
    transport = FakeCanTransport(connected=True)
    unrelated = RawCanFrame(0x02007FFD, b"\x00" * 8)
    transport.queued_frames.extend((unrelated, response_frame(FctOpcode.STATUS, FctTestId.OPEN_LOOP)))
    seen = []
    client = FctClient(transport, response_timeout_s=0.01)
    response = client.status(driver_id=DRIVER_ID, test_id=FctTestId.OPEN_LOOP, on_unrelated=seen.append)
    assert response.state is FctState.RUNNING
    assert seen == [unrelated]
    with pytest.raises(FctResponseTimeout):
        client.status(driver_id=DRIVER_ID, test_id=FctTestId.OPEN_LOOP)


class BlockingTransport(FakeCanTransport):
    def send(self, frame):
        super().send(frame)
        sleep(0.01)
        fields = parse_fct_id(frame.arbitration_id)
        self.queued_frames.append(response_frame(fields.opcode, frame.data[0]))


def test_clients_on_same_transport_never_overlap_transactions() -> None:
    transport = BlockingTransport(connected=True)
    clients = [FctClient(transport, response_timeout_s=0.1), FctClient(transport, response_timeout_s=0.1)]
    results = []
    threads = [
        Thread(target=lambda client=client: results.append(client.status(driver_id=DRIVER_ID, test_id=FctTestId.OPEN_LOOP)))
        for client in clients
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 2
    assert len(transport.sent_frames) == 2
