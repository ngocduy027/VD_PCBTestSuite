"""Polls live sin/cos samples from both GongWang encoder rings for the amplitude view."""

from __future__ import annotations

from dataclasses import dataclass

from pcbt_app.protocol.fct_can import FCT_HOST_ID
from pcbt_app.protocol.gw_sincos import (
    GW_MOTOR_OPCODE,
    GW_OUTPUT_OPCODE,
    GwSinCosSample,
    build_gw_sincos_request,
    parse_gw_sincos_response,
)
from pcbt_app.services.fct_client import FctClient

GW_LIVE_RESPONSE_TIMEOUT_S = 0.020


@dataclass(slots=True, frozen=True)
class GwLiveCycle:
    """One poll's samples for both encoder rings (either may be missing)."""

    motor: GwSinCosSample | None = None
    output: GwSinCosSample | None = None

    @property
    def is_complete_valid(self) -> bool:
        """True when both rings answered with a valid XOR."""
        return (
            self.motor is not None
            and self.output is not None
            and self.motor.xor_valid
            and self.output.xor_valid
        )


class GwLiveReader:
    """Polls live sin/cos samples; shares the FCT transaction lock with tests."""

    def __init__(self, transport, *, event_log=None, host_id: int = FCT_HOST_ID) -> None:
        self.client = FctClient(transport, host_id=host_id, event_log=event_log)

    def poll_once(
        self,
        driver_id: int,
        *,
        timeout_s: float = GW_LIVE_RESPONSE_TIMEOUT_S,
    ) -> GwLiveCycle:
        """Send one request and collect the motor and output samples until both arrive or ``timeout_s``."""
        request = build_gw_sincos_request(driver_id=driver_id, host_id=self.client.host_id)

        def parse(frame):
            return parse_gw_sincos_response(
                frame,
                driver_id=driver_id,
                host_id=self.client.host_id,
            )

        samples = self.client.collect_matching(
            request,
            timeout_s=timeout_s,
            matcher=parse,
            is_complete=lambda items: {item.opcode for item in items}
            >= {GW_MOTOR_OPCODE, GW_OUTPUT_OPCODE},
            note="GW live sine/cosine poll",
        )
        by_opcode = {sample.opcode: sample for sample in samples}
        return GwLiveCycle(
            motor=by_opcode.get(GW_MOTOR_OPCODE),
            output=by_opcode.get(GW_OUTPUT_OPCODE),
        )
