"""Pack/unpack the 29-bit extended CAN ID: ``function | state | driver_id | host_id``."""

from __future__ import annotations

from pcbt_app.models import CanIdFields


def parse_extended_id(arbitration_id: int) -> CanIdFields:
    """Split a 29-bit CAN ID into function, state, driver and host bytes."""
    if not 0 <= arbitration_id <= 0x1FFFFFFF:
        raise ValueError(f"CAN arbitration ID out of 29-bit range: 0x{arbitration_id:X}")
    return CanIdFields(
        raw_id=arbitration_id,
        function_code=(arbitration_id >> 24) & 0xFF,
        state_byte=(arbitration_id >> 16) & 0xFF,
        driver_id=(arbitration_id >> 8) & 0xFF,
        host_id=arbitration_id & 0xFF,
    )


def build_extended_id(function_code: int, state_byte: int, driver_id: int, host_id: int) -> int:
    """Inverse of :func:`parse_extended_id`; every field must fit in one byte."""
    for name, value in {
        "function_code": function_code,
        "state_byte": state_byte,
        "driver_id": driver_id,
        "host_id": host_id,
    }.items():
        if not 0 <= value <= 0xFF:
            raise ValueError(f"{name} must fit in one byte")
    return (
        ((function_code & 0xFF) << 24)
        | ((state_byte & 0xFF) << 16)
        | ((driver_id & 0xFF) << 8)
        | (host_id & 0xFF)
    )
