"""Runs GongWang calibration (or reads the stored result) and streams progress updates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from time import monotonic

from pcbt_app.protocol.fct_can import FCT_HOST_ID
from pcbt_app.protocol.gw_calibration import (
    GwCalibrationAssembler,
    GwCalibrationOutcome,
    GwCalibrationProtocolError,
    GwCalibrationResult,
    GwCalibrationStartResult,
    build_gw_calibration_read_result,
    build_gw_calibration_start,
    parse_gw_calibration_fragment,
    parse_gw_calibration_progress,
    parse_gw_calibration_start_ack,
)
from pcbt_app.services.fct_client import FctClient, FctResponseTimeout

GW_CALIBRATION_START_TIMEOUT_S = 0.5
GW_CALIBRATION_RESULT_FRAGMENT_TIMEOUT_S = 1.0
GW_CALIBRATION_READ_RESULT_TIMEOUT_S = 1.0
GW_CALIBRATION_RECEIVE_SLICE_S = 0.2


class GwCalibrationCancelled(RuntimeError):
    """The operator stopped the display; the firmware calibration itself keeps running."""

    pass


@dataclass(slots=True, frozen=True)
class GwCalibrationUpdate:
    """Progress/result message streamed to the GUI; ``terminal`` marks the last one."""

    state: str
    message: str
    step: int | None = None
    encoder_status: int | None = None
    result: GwCalibrationResult | None = None
    terminal: bool = False
    passed: bool = False


class GwCalibrationRunner:
    """Drives the on-board GongWang calibration over the FCT IDs."""

    def __init__(self, transport, *, event_log=None, host_id: int = FCT_HOST_ID) -> None:
        self.client = FctClient(transport, host_id=host_id, event_log=event_log)

    def run(
        self,
        driver_id: int,
        *,
        on_update: Callable[[GwCalibrationUpdate], None],
        stop_event: Event | None = None,
    ) -> GwCalibrationResult:
        """Start calibration and stream updates until the result is complete.

        There is no overall timeout while the encoder is calibrating (it can take a
        while); only the START ack and gaps between result fragments are time-limited.
        Use ``stop_event`` to stop waiting. Raises
        :class:`~pcbt_app.protocol.gw_calibration.GwCalibrationProtocolError` on a
        rejected START or a broken result.
        """
        stop_event = stop_event or Event()
        request = build_gw_calibration_start(driver_id=driver_id, host_id=self.client.host_id)
        assembler = GwCalibrationAssembler()
        fragment_deadline: float | None = None

        with self.client._transaction_lock:
            self.client.transport.send(request)
            self.client._log("tx", request, "GW calibration START")
            ack_deadline = monotonic() + GW_CALIBRATION_START_TIMEOUT_S
            ack = None
            while ack is None:
                self._raise_if_cancelled(stop_event)
                remaining = ack_deadline - monotonic()
                if remaining <= 0:
                    raise FctResponseTimeout("no GW calibration START acknowledgement")
                frame = self.client.transport.recv(min(remaining, GW_CALIBRATION_RECEIVE_SLICE_S))
                if frame is None:
                    continue
                self.client._log("rx", frame)
                ack = parse_gw_calibration_start_ack(
                    frame,
                    driver_id=driver_id,
                    host_id=self.client.host_id,
                )
                if ack is None:
                    self.client._log("rx", frame, "ignored while waiting for GW calibration START")

            if ack.result is not GwCalibrationStartResult.ACCEPTED:
                reason = {
                    GwCalibrationStartResult.BUSY: "Calibration START rejected: firmware is busy",
                    GwCalibrationStartResult.NOT_INITIALIZED: "Calibration START rejected: library is not initialized",
                }[ack.result]
                raise GwCalibrationProtocolError(reason)

            on_update(
                GwCalibrationUpdate(
                    "STARTING",
                    f"START accepted (firmware state 0x{ack.firmware_state:02X})",
                )
            )

            while True:
                self._raise_if_cancelled(stop_event)
                timeout_s = GW_CALIBRATION_RECEIVE_SLICE_S
                if fragment_deadline is not None:
                    remaining = fragment_deadline - monotonic()
                    if remaining <= 0:
                        raise GwCalibrationProtocolError("Incomplete calibration result")
                    timeout_s = min(timeout_s, remaining)
                frame = self.client.transport.recv(timeout_s)
                if frame is None:
                    continue
                self.client._log("rx", frame)

                progress = parse_gw_calibration_progress(
                    frame,
                    driver_id=driver_id,
                    host_id=self.client.host_id,
                )
                if progress is not None:
                    if progress.request == 0x00:
                        if progress.encoder_status == 0:
                            state, message = "RUNNING", "Encoder accepted calibration"
                        else:
                            state, message = "READING ERROR", "Encoder rejected calibration; waiting for result"
                    elif progress.step < 0x11:
                        state, message = "RUNNING", f"Calibration step 0x{progress.step:02X}"
                    elif progress.step == 0x11:
                        state, message = "FINISHING", "Encoder reports success; reading final result"
                    else:
                        state, message = "READING ERROR", "Encoder reports failure; reading final result"
                    on_update(
                        GwCalibrationUpdate(
                            state,
                            message,
                            step=progress.step,
                            encoder_status=progress.encoder_status,
                        )
                    )
                    continue

                fragment = parse_gw_calibration_fragment(
                    frame,
                    driver_id=driver_id,
                    host_id=self.client.host_id,
                )
                if fragment is None:
                    self.client._log("rx", frame, "ignored while receiving GW calibration")
                    continue
                fragment_deadline = monotonic() + GW_CALIBRATION_RESULT_FRAGMENT_TIMEOUT_S
                result = assembler.add(fragment)
                if result is None:
                    on_update(GwCalibrationUpdate("FINISHING", "Receiving final calibration result"))
                    continue

                if result.outcome is GwCalibrationOutcome.PASS:
                    update = GwCalibrationUpdate(
                        "PASS", "Calibration completed successfully", result=result, terminal=True, passed=True
                    )
                elif result.outcome is GwCalibrationOutcome.ENCODER_FAILURE:
                    update = GwCalibrationUpdate(
                        "FAIL", "Encoder reported calibration failure", result=result, terminal=True
                    )
                elif result.outcome is GwCalibrationOutcome.UART_FAILURE:
                    update = GwCalibrationUpdate("FAIL", "Firmware reported a UART failure", result=result, terminal=True)
                else:
                    update = GwCalibrationUpdate(
                        "FAIL", "Firmware could not start OpenLoop", result=result, terminal=True
                    )
                on_update(update)
                return result

    def read_result(
        self,
        driver_id: int,
        *,
        on_update: Callable[[GwCalibrationUpdate], None],
        stop_event: Event | None = None,
    ) -> GwCalibrationResult:
        """Read the result stored from the last calibration (no new run)."""
        stop_event = stop_event or Event()
        request = build_gw_calibration_read_result(driver_id=driver_id, host_id=self.client.host_id)
        assembler = GwCalibrationAssembler()
        response_deadline = monotonic() + GW_CALIBRATION_READ_RESULT_TIMEOUT_S

        with self.client._transaction_lock:
            self.client.transport.send(request)
            self.client._log("tx", request, "GW calibration READ RESULT")
            on_update(GwCalibrationUpdate("READING", "Reading stored calibration result"))

            while True:
                self._raise_if_cancelled(stop_event)
                remaining = response_deadline - monotonic()
                if remaining <= 0:
                    message = "Incomplete calibration result" if assembler.started else "No calibration result response"
                    raise GwCalibrationProtocolError(message)

                frame = self.client.transport.recv(min(GW_CALIBRATION_RECEIVE_SLICE_S, remaining))
                if frame is None:
                    continue
                self.client._log("rx", frame)
                fragment = parse_gw_calibration_fragment(
                    frame,
                    driver_id=driver_id,
                    host_id=self.client.host_id,
                )
                if fragment is None:
                    self.client._log("rx", frame, "ignored while reading GW calibration result")
                    continue

                response_deadline = monotonic() + GW_CALIBRATION_RESULT_FRAGMENT_TIMEOUT_S
                result = assembler.add(fragment)
                if result is None:
                    on_update(GwCalibrationUpdate("READING", "Receiving stored calibration result"))
                    continue

                if result.outcome is GwCalibrationOutcome.PASS:
                    update = GwCalibrationUpdate(
                        "PASS", "Stored calibration result read successfully", result=result, terminal=True, passed=True
                    )
                elif result.outcome is GwCalibrationOutcome.ENCODER_FAILURE:
                    update = GwCalibrationUpdate(
                        "FAIL", "Stored encoder calibration failure read successfully", result=result, terminal=True
                    )
                elif result.outcome is GwCalibrationOutcome.UART_FAILURE:
                    update = GwCalibrationUpdate("FAIL", "Firmware reported a UART failure", result=result, terminal=True)
                else:
                    update = GwCalibrationUpdate(
                        "FAIL", "Firmware could not start OpenLoop", result=result, terminal=True
                    )
                on_update(update)
                return result

    @staticmethod
    def _raise_if_cancelled(stop_event: Event) -> None:
        if stop_event.is_set():
            raise GwCalibrationCancelled("Calibration display stopped; firmware calibration was not stopped")
