"""GongWang live sin/cos sample request (``0x1C``) and response (``0x1D``) frames."""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot

from pcbt_app.models import RawCanFrame
from pcbt_app.protocol.fct_can import FCT_HOST_ID, build_fct_id, parse_fct_id

GW_SINCOS_REQUEST_FUNCTION = 0x1C
GW_SINCOS_RESPONSE_FUNCTION = 0x1D
GW_SINCOS_REQUEST_OPCODE = 0x01
GW_MOTOR_OPCODE = 0x54
GW_OUTPUT_OPCODE = 0x55
GW_MOTOR_RESOLUTION = 131_072
GW_OUTPUT_RESOLUTION = 524_288
GW_SINCOS_DLC = 8
# Encoder sin/cos signal scale: 3.657 V per 8192 counts (shared with calibration records).
GW_SIGNAL_SCALE_V = 3.657 / 8192.0
GW_SIGNAL_SCALE_MV = GW_SIGNAL_SCALE_V * 1000.0


@dataclass(slots=True, frozen=True)
class GwSinCosSample:
    """One live sin/cos reading from an encoder ring.

    Response payload: sin (i16 LE), cos (i16 LE), position count (u24 LE) and an
    XOR byte; the XOR also covers the opcode, which identifies the ring
    (``0x54`` motor, ``0x55`` output). Millivolt values use
    ``GW_SIGNAL_SCALE_MV``; ``amplitude_mv`` is ``hypot(sin, cos)``.
    """

    raw_frame: RawCanFrame
    opcode: int
    resolution: int
    sin_raw: int
    cos_raw: int
    count: int
    xor_byte: int
    xor_valid: bool
    sin_mv: float
    cos_mv: float
    amplitude_mv: float
    angle_deg: float

    @property
    def ring_key(self) -> str:
        """``"motor"`` or ``"output"``; used as the key into the GUI's per-ring widgets."""
        return "motor" if self.opcode == GW_MOTOR_OPCODE else "output"


def build_gw_sincos_request(*, driver_id: int, host_id: int = FCT_HOST_ID) -> RawCanFrame:
    """Ask the driver for one sample from each encoder ring."""
    return RawCanFrame(
        arbitration_id=build_fct_id(
            GW_SINCOS_REQUEST_FUNCTION,
            GW_SINCOS_REQUEST_OPCODE,
            host_id,
            driver_id,
        ),
        data=b"\x00" * GW_SINCOS_DLC,
        is_extended_id=True,
    )


def parse_gw_sincos_response(
    frame: RawCanFrame,
    *,
    driver_id: int,
    host_id: int = FCT_HOST_ID,
) -> GwSinCosSample | None:
    """Decode a sample for ``driver_id``; ``None`` if the frame is unrelated. Check ``xor_valid`` before using values."""
    if not frame.is_extended_id or len(frame.data) != GW_SINCOS_DLC:
        return None
    fields = parse_fct_id(frame.arbitration_id)
    if (
        fields.function != GW_SINCOS_RESPONSE_FUNCTION
        or fields.opcode not in {GW_MOTOR_OPCODE, GW_OUTPUT_OPCODE}
        or fields.source_id != driver_id
        or fields.target_id != host_id
    ):
        return None

    resolution = GW_MOTOR_RESOLUTION if fields.opcode == GW_MOTOR_OPCODE else GW_OUTPUT_RESOLUTION
    sin_raw = int.from_bytes(frame.data[0:2], "little", signed=True)
    cos_raw = int.from_bytes(frame.data[2:4], "little", signed=True)
    count = int.from_bytes(frame.data[4:7], "little", signed=False)
    sin_mv = sin_raw * GW_SIGNAL_SCALE_MV
    cos_mv = cos_raw * GW_SIGNAL_SCALE_MV
    xor_value = fields.opcode
    for value in frame.data:
        xor_value ^= value
    return GwSinCosSample(
        raw_frame=frame,
        opcode=fields.opcode,
        resolution=resolution,
        sin_raw=sin_raw,
        cos_raw=cos_raw,
        count=count,
        xor_byte=frame.data[7],
        xor_valid=xor_value == 0,
        sin_mv=sin_mv,
        cos_mv=cos_mv,
        amplitude_mv=hypot(sin_mv, cos_mv),
        angle_deg=count * 360.0 / resolution,
    )
