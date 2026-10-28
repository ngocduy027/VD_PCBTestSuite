from __future__ import annotations

from threading import Event, Thread
from time import sleep

import pytest

from pcbt_app.models import RawCanFrame
from pcbt_app.protocol.fct_can import FCT_HOST_ID, FctTestId, build_fct_id
from pcbt_app.protocol.gw_sincos import (
    GW_MOTOR_OPCODE,
    GW_MOTOR_RESOLUTION,
    GW_OUTPUT_OPCODE,
    GW_OUTPUT_RESOLUTION,
    build_gw_sincos_request,
    parse_gw_sincos_response,
)
from pcbt_app.services.fct_client import FctClient
from pcbt_app.services.gw_live_reader import GwLiveCycle, GwLiveReader
from pcbt_app.transport.fake_bus import FakeCanTransport
from pcbt_app.ui.worker import CanWorker

DRIVER_ID = 0x01


def response(opcode: int, payload_without_xor: bytes) -> RawCanFrame:
    xor_byte = opcode
    for value in payload_without_xor:
        xor_byte ^= value
    return RawCanFrame(
        build_fct_id(0x1D, opcode, DRIVER_ID, FCT_HOST_ID),
        payload_without_xor + bytes((xor_byte,)),
        is_extended_id=True,
    )


def test_builds_documented_extended_poll_request() -> None:
    frame = build_gw_sincos_request(driver_id=DRIVER_ID)
    assert frame.arbitration_id == 0x1C01FD01
    assert frame.data == b"\x00" * 8
    assert frame.is_extended_id


def test_decodes_documented_motor_example() -> None:
    frame = RawCanFrame(
        build_fct_id(0x1D, GW_MOTOR_OPCODE, DRIVER_ID, FCT_HOST_ID),
        bytes.fromhex("95 01 C4 F3 52 75 01 D1"),
    )
    sample = parse_gw_sincos_response(frame, driver_id=DRIVER_ID)
    assert sample is not None
    assert sample.sin_raw == 405
    assert sample.cos_raw == -3132
    assert sample.count == 95570
    assert sample.resolution == GW_MOTOR_RESOLUTION
    assert sample.sin_mv == pytest.approx(180.80, abs=0.01)
    assert sample.cos_mv == pytest.approx(-1398.16, abs=0.01)
    assert sample.amplitude_mv == pytest.approx(1409.80, abs=0.01)
    assert sample.angle_deg == pytest.approx(262.49, abs=0.01)
    assert sample.xor_byte == 0xD1
    assert sample.xor_valid


def test_output_resolution_xor_and_strict_frame_filtering() -> None:
    good = response(GW_OUTPUT_OPCODE, bytes.fromhex("00 80 FF 7F FF FF 07"))
    sample = parse_gw_sincos_response(good, driver_id=DRIVER_ID)
    assert sample is not None
    assert (sample.sin_raw, sample.cos_raw, sample.count) == (-32768, 32767, 0x7FFFF)
    assert sample.resolution == GW_OUTPUT_RESOLUTION
    assert sample.angle_deg == pytest.approx(359.9993133544922)

    bad_xor = RawCanFrame(good.arbitration_id, good.data[:-1] + bytes((good.data[-1] ^ 1,)))
    assert parse_gw_sincos_response(bad_xor, driver_id=DRIVER_ID).xor_valid is False
    assert parse_gw_sincos_response(RawCanFrame(good.arbitration_id, good.data[:7]), driver_id=DRIVER_ID) is None
    assert parse_gw_sincos_response(RawCanFrame(good.arbitration_id, good.data, False), driver_id=DRIVER_ID) is None
    assert parse_gw_sincos_response(good, driver_id=DRIVER_ID + 1) is None
    wrong_target = RawCanFrame(build_fct_id(0x1D, GW_OUTPUT_OPCODE, DRIVER_ID, FCT_HOST_ID - 1), good.data)
    assert parse_gw_sincos_response(wrong_target, driver_id=DRIVER_ID) is None


def test_poll_collects_both_rings_in_either_order() -> None:
    transport = FakeCanTransport(connected=True)
    output = response(GW_OUTPUT_OPCODE, bytes.fromhex("01 00 02 00 03 00 00"))
    motor = response(GW_MOTOR_OPCODE, bytes.fromhex("04 00 05 00 06 00 00"))
    transport.queued_frames.extend((output, motor))

    cycle = GwLiveReader(transport).poll_once(DRIVER_ID, timeout_s=0.005)

    assert cycle.motor is not None and cycle.motor.sin_raw == 4
    assert cycle.output is not None and cycle.output.sin_raw == 1
    assert len(transport.sent_frames) == 1


def test_poll_returns_valid_ring_when_other_ring_is_missing() -> None:
    transport = FakeCanTransport(connected=True)
    transport.queued_frames.append(response(GW_MOTOR_OPCODE, bytes.fromhex("01 00 02 00 03 00 00")))
    cycle = GwLiveReader(transport).poll_once(DRIVER_ID, timeout_s=0.001)
    assert cycle.motor is not None
    assert cycle.output is None


def test_complete_live_sample_requires_two_valid_received_rings() -> None:
    motor = parse_gw_sincos_response(
        response(GW_MOTOR_OPCODE, bytes.fromhex("01 00 02 00 03 00 00")),
        driver_id=DRIVER_ID,
    )
    output = parse_gw_sincos_response(
        response(GW_OUTPUT_OPCODE, bytes.fromhex("04 00 05 00 06 00 00")),
        driver_id=DRIVER_ID,
    )
    assert motor is not None and output is not None
    assert GwLiveCycle(motor=motor, output=output).is_complete_valid
    assert not GwLiveCycle(motor=motor).is_complete_valid

    bad_frame = RawCanFrame(output.raw_frame.arbitration_id, output.raw_frame.data[:-1] + b"\x00")
    bad_output = parse_gw_sincos_response(bad_frame, driver_id=DRIVER_ID)
    assert bad_output is not None and not bad_output.xor_valid
    assert not GwLiveCycle(motor=motor, output=bad_output).is_complete_valid


class BlockingLiveTransport(FakeCanTransport):
    def __init__(self) -> None:
        super().__init__(connected=True)
        self.live_entered = Event()
        self.release_live = Event()

    def recv(self, timeout_s: float):
        if self.sent_frames and self.sent_frames[-1].arbitration_id == 0x1C01FD01:
            self.live_entered.set()
            self.release_live.wait(timeout=0.2)
            return None
        return super().recv(timeout_s)


def test_live_poll_and_fct_transaction_share_one_transport_lock() -> None:
    transport = BlockingLiveTransport()
    reader = GwLiveReader(transport)
    poll_thread = Thread(target=lambda: reader.poll_once(DRIVER_ID, timeout_s=0.05))
    poll_thread.start()
    assert transport.live_entered.wait(timeout=0.2)

    fct_client = FctClient(transport, response_timeout_s=0.005)
    fct_thread = Thread(
        target=lambda: pytest.raises(TimeoutError, fct_client.status, driver_id=DRIVER_ID, test_id=FctTestId.OPEN_LOOP)
    )
    fct_thread.start()
    sleep(0.005)
    assert len(transport.sent_frames) == 1
    transport.release_live.set()
    poll_thread.join(timeout=0.2)
    fct_thread.join(timeout=0.2)
    assert len(transport.sent_frames) == 2


def test_worker_live_loop_stops_promptly(qtbot) -> None:
    worker = CanWorker()
    worker._transport = FakeCanTransport(connected=True)
    worker._detected_driver_id = DRIVER_ID
    states = []
    worker.gw_live_state_changed.connect(lambda running, message: states.append((running, message)))

    worker.start_gw_live(50)
    sleep(0.005)
    assert worker._gw_live_thread is not None
    worker.stop_gw_live()

    qtbot.waitUntil(lambda: worker._gw_live_thread is None, timeout=200)
    qtbot.waitUntil(lambda: len(states) >= 2, timeout=200)
    assert states[0] == (True, "Running")
    assert states[-1] == (False, "Stopped")


def test_worker_bounded_live_counts_only_complete_valid_pairs(qtbot, monkeypatch) -> None:
    motor = parse_gw_sincos_response(
        response(GW_MOTOR_OPCODE, bytes.fromhex("01 00 02 00 03 00 00")),
        driver_id=DRIVER_ID,
    )
    output = parse_gw_sincos_response(
        response(GW_OUTPUT_OPCODE, bytes.fromhex("04 00 05 00 06 00 00")),
        driver_id=DRIVER_ID,
    )
    assert motor is not None and output is not None
    cycles = iter(
        (
            GwLiveCycle(motor=motor),
            GwLiveCycle(output=output),
            GwLiveCycle(motor=motor, output=output),
            GwLiveCycle(motor=motor, output=output),
        )
    )

    class StubReader:
        def __init__(self, transport, *, event_log=None) -> None:
            pass

        def poll_once(self, driver_id: int) -> GwLiveCycle:
            return next(cycles)

    monkeypatch.setattr("pcbt_app.ui.worker.GwLiveReader", StubReader)
    worker = CanWorker()
    worker._transport = FakeCanTransport(connected=True)
    worker._detected_driver_id = DRIVER_ID
    progress = []
    updates = []
    states = []
    worker.gw_live_progress.connect(lambda count, target: progress.append((count, target)))
    worker.gw_live_updated.connect(updates.append)
    worker.gw_live_state_changed.connect(lambda running, message: states.append((running, message)))

    worker.read_gw_live_samples(50, 2)

    qtbot.waitUntil(lambda: worker._gw_live_thread is None, timeout=500)
    qtbot.waitUntil(lambda: len(states) >= 2, timeout=500)
    assert progress == [(1, 2), (2, 2)]
    assert len(updates) == 2
    assert all(cycle.is_complete_valid for cycle in updates)
    assert states[0] == (True, "Reading 0/2")
    assert states[-1] == (False, "Completed 2/2")
