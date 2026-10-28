from __future__ import annotations

from pcbt_app.config import TEST_ID_CAN, TEST_ID_GONGWANG, TEST_ID_OPENLOOP
from pcbt_app.models import FailureDetail, RawCanFrame

FAILURE_BITS: tuple[FailureDetail, ...] = (
    FailureDetail(0x01, "invalid_duration", "Invalid open-loop duration reported by firmware"),
    FailureDetail(0x02, "bus_voltage_low", "Bus voltage low"),
    FailureDetail(0x04, "overcurrent", "Overcurrent detected"),
    FailureDetail(0x08, "gate_driver_fault", "Gate-driver fault"),
    FailureDetail(0x10, "can_tx_queue_failure", "Board-side CAN transmit queue failure"),
)


def build_openloop_command(command_id: int) -> RawCanFrame:
    return RawCanFrame(arbitration_id=command_id, data=bytes([0x01, 0, 0, 0, 0, 0, 0, 0]))


def build_can_test_command(command_id: int) -> RawCanFrame:
    return RawCanFrame(arbitration_id=command_id, data=bytes([0x02, 0, 0, 0, 0, 0, 0, 0]))


def decode_failure_mask(mask: int) -> tuple[list[FailureDetail], int]:
    matched = [detail for detail in FAILURE_BITS if mask & detail.bit]
    known_mask = 0
    for detail in FAILURE_BITS:
        known_mask |= detail.bit
    unknown_mask = mask & ~known_mask
    return matched, unknown_mask


def describe_test_id(test_id: int) -> str:
    if test_id == TEST_ID_OPENLOOP:
        return "open-loop"
    if test_id == TEST_ID_CAN:
        return "can"
    if test_id == TEST_ID_GONGWANG:
        return "gongwang"
    return f"unknown({test_id})"
