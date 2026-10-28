"""Hardware worker living on its own ``QThread``.

Owns the CAN and RS485 transports. Slots are invoked from the GUI via queued
signals; each test then runs on its own daemon thread (so several FCT tests
can run at once, guarded per test by :class:`TestRunner`), as do the GW live
reader and calibration. Results reach the GUI thread only through Qt signals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Event, Thread, current_thread
from time import monotonic

from PySide6.QtCore import QObject, Signal, Slot

from pcbt_app.logging_store import EventLog
from pcbt_app.protocol.mit_firmware import (
    CAN_STRESS_DEFAULT_INTERVAL_MS,
    CAN_STRESS_DEFAULT_TOTAL_MESSAGES,
    GONGWANG_DEFAULT_DURATION_MS,
    OPEN_LOOP_DEFAULT_DURATION_MS,
    OPEN_LOOP_DEFAULT_THETA_STEP,
    RS485_STRESS_DEFAULT_DURATION_MS,
    RS485_STRESS_DEFAULT_PERIOD_TICKS,
)
from pcbt_app.services.gw_calibration import (
    GwCalibrationCancelled,
    GwCalibrationRunner,
    GwCalibrationUpdate,
)
from pcbt_app.services.gw_live_reader import GwLiveReader
from pcbt_app.services.pcb_test_service import PcbTestService
from pcbt_app.services.test_runner import TestRunner
from pcbt_app.transport.base import AbstractCanTransport
from pcbt_app.transport.factory import create_transport
from pcbt_app.transport.rs485_serial import SerialRs485Transport

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ConnectionParams:
    """CAN adapter selection sent from the GUI to :meth:`CanWorker.connect_bus`."""

    backend: str
    channel: str
    bitrate: int
    serial_baud: int


@dataclass(slots=True)
class Rs485ConnectionParams:
    """RS485 port selection sent to :meth:`CanWorker.connect_rs485`."""

    channel: str
    baudrate: int


@dataclass(slots=True)
class TestOutcome:
    """Result of one test run: ``payload`` is a ``ServiceResult`` or the exception that aborted it."""

    test_key: str
    payload: object


class CanWorker(QObject):
    """Hardware-side counterpart of :class:`~pcbt_app.ui.main_window.MainWindow` (see module docstring)."""

    model_b_devices_scanned = Signal(object, str)
    connection_changed = Signal(bool, str)
    rs485_connection_changed = Signal(bool, str)
    adapter_configured = Signal(bool, str)
    test_finished = Signal(object)
    test_progress = Signal(str, int)
    log_updated = Signal(list)
    gw_live_updated = Signal(object)
    gw_live_progress = Signal(int, int)
    gw_live_state_changed = Signal(bool, str)
    gw_calibration_updated = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._transport: AbstractCanTransport | None = None
        self._rs485_transport: SerialRs485Transport | None = None
        self._log = EventLog()
        self._runner = TestRunner()
        self._detected_driver_id: int | None = None
        self._gw_live_stop = Event()
        self._gw_live_thread: Thread | None = None
        self._gw_calibration_stop = Event()
        self._gw_calibration_thread: Thread | None = None

    @Slot()
    def scan_model_b_devices(self) -> None:
        """List available Model B adapters; refused while CAN is open (the scan opens devices)."""
        if self._transport is not None and self._transport.is_connected():
            self.model_b_devices_scanned.emit([], "Close CAN before scanning devices")
            return
        from pcbt_app.transport.waveshare_usbcanfd_b import scan_model_b_devices
        try:
            self.model_b_devices_scanned.emit(scan_model_b_devices(), "")
        except Exception as exc:
            logger.exception("scan_model_b_devices failed")
            self.model_b_devices_scanned.emit([], str(exc))

    @Slot(object)
    def connect_bus(self, params: ConnectionParams) -> None:
        """Stop live/calibration work, replace the transport and connect; emits ``connection_changed``."""
        self._stop_gw_live()
        self._stop_gw_calibration()
        self._detected_driver_id = None
        if self._transport is not None:
            self._transport.disconnect()
        self._transport = create_transport(
            params.backend,
            params.channel,
            params.bitrate,
            serial_baud=params.serial_baud,
        )
        try:
            self._transport.connect()
        except Exception as exc:
            logger.exception("connect_bus failed")
            self.connection_changed.emit(False, str(exc))
            return
        self.connection_changed.emit(True, "Connected")

    @Slot()
    def disconnect_bus(self) -> None:
        """Stop live/calibration work and close the CAN adapter."""
        self._stop_gw_live()
        self._stop_gw_calibration()
        if self._transport is not None:
            self._transport.disconnect()
        self._detected_driver_id = None
        self.connection_changed.emit(False, "Disconnected")

    @Slot(object)
    def connect_rs485(self, params: Rs485ConnectionParams) -> None:
        """Open the RS485 port used by the RS485 stress test."""
        if self._rs485_transport is not None:
            self._rs485_transport.disconnect()
        self._rs485_transport = SerialRs485Transport(params.channel, params.baudrate)
        try:
            self._rs485_transport.connect()
        except Exception as exc:
            logger.exception("connect_rs485 failed")
            self.rs485_connection_changed.emit(False, str(exc))
            return
        self.rs485_connection_changed.emit(True, "Connected")

    @Slot()
    def disconnect_rs485(self) -> None:
        """Close the RS485 port."""
        if self._rs485_transport is not None:
            self._rs485_transport.disconnect()
        self.rs485_connection_changed.emit(False, "Disconnected")

    @Slot(int)
    def configure_adapter(self, bitrate: int = 0) -> None:
        """Re-apply adapter configuration (optionally a new bitrate); emits the packet hex on success."""
        if self._transport is None or not self._transport.is_connected():
            self.adapter_configured.emit(False, "CAN adapter is not connected")
            return

        configure = getattr(self._transport, "configure_adapter", None)
        if configure is None:
            self.adapter_configured.emit(False, "Selected backend does not support Set and Start")
            return

        try:
            if bitrate and hasattr(self._transport, "can_bitrate"):
                self._transport.can_bitrate = bitrate
            packet = configure()
        except Exception as exc:
            logger.exception("configure_adapter failed")
            self.adapter_configured.emit(False, str(exc))
            return
        self.adapter_configured.emit(True, packet.hex().upper())

    @Slot(int)
    def run_can_test(self, timeout_ms: int) -> None:
        """Start Detect ID; the detected driver ID is remembered for later tests."""
        self._run_test("can", timeout_ms)

    @Slot(int, float, int)
    def run_openloop_test(
        self,
        app_timeout_ms: int,
        theta_step: float = OPEN_LOOP_DEFAULT_THETA_STEP,
        duration_ms: int = OPEN_LOOP_DEFAULT_DURATION_MS,
    ) -> None:
        """Start the open-loop test on the detected driver."""
        self._run_test(
            "openloop",
            app_timeout_ms,
            theta_step=theta_step,
            duration_ms=duration_ms,
        )

    @Slot(int, int, int)
    def run_can_stress_test(
        self, app_timeout_ms: int, interval_ms: int, total_messages: int
    ) -> None:
        """Start the CAN stress test; live counts arrive via ``test_progress``."""
        self._run_test(
            "can_stress",
            app_timeout_ms,
            interval_ms,
            total_messages,
        )

    @Slot(int, int, int)
    def run_rs485_stress_test(self, timeout_ms: int, duration_ms: int, period_ticks: int) -> None:
        """Start the RS485 stress test; requires the RS485 port to be open."""
        self._run_test(
            "rs485_stress", timeout_ms, duration_ms=duration_ms, period_ticks=period_ticks
        )

    @Slot(int, int, int)
    def run_gongwang_test(
        self,
        app_timeout_ms: int,
        duration_ms: int = GONGWANG_DEFAULT_DURATION_MS,
        period_ticks: int = 1,
    ) -> None:
        """Start the GongWang encoder test."""
        self._run_test("gongwang", app_timeout_ms, period_ticks=period_ticks, duration_ms=duration_ms)

    @Slot(int)
    def start_gw_live(self, frequency_hz: int) -> None:
        """Start continuous sin/cos polling at ``frequency_hz`` (1..50)."""
        self._start_gw_live(frequency_hz)

    @Slot(int, int)
    def read_gw_live_samples(self, frequency_hz: int, sample_count: int) -> None:
        """Poll until ``sample_count`` complete, valid samples have been received."""
        if sample_count < 1:
            self.gw_live_state_changed.emit(False, "Sample count must be positive")
            return
        self._start_gw_live(frequency_hz, target_samples=sample_count)

    def _start_gw_live(self, frequency_hz: int, target_samples: int | None = None) -> None:
        """Validate preconditions and start the live polling thread."""
        if self._transport is None or not self._transport.is_connected():
            self.gw_live_state_changed.emit(False, "CAN adapter is not connected")
            return
        if self._detected_driver_id is None:
            self.gw_live_state_changed.emit(False, "Detect a driver ID before starting")
            return
        if not 1 <= frequency_hz <= 50:
            self.gw_live_state_changed.emit(False, "Polling frequency must be 1..50 Hz")
            return
        if self._gw_calibration_thread is not None and self._gw_calibration_thread.is_alive():
            self.gw_live_state_changed.emit(False, "Calibration is active")
            return
        if self._gw_live_thread is not None and self._gw_live_thread.is_alive():
            self.gw_live_state_changed.emit(True, "Running")
            return

        self._gw_live_stop.clear()
        self._gw_live_thread = Thread(
            target=self._gw_live_loop,
            args=(self._detected_driver_id, frequency_hz, target_samples),
            daemon=True,
            name="gw-live-encoder",
        )
        self._gw_live_thread.start()
        state = "Running" if target_samples is None else f"Reading 0/{target_samples}"
        self.gw_live_state_changed.emit(True, state)

    @Slot()
    def stop_gw_live(self) -> None:
        """Stop live polling."""
        self._stop_gw_live()

    def _stop_gw_live(self) -> None:
        thread = self._gw_live_thread
        if thread is None:
            return
        self._gw_live_stop.set()
        if thread is not current_thread():
            thread.join(timeout=0.1)

    def _gw_live_loop(
        self, driver_id: int, frequency_hz: int, target_samples: int | None = None
    ) -> None:
        """Poll at ``frequency_hz`` until stopped (or ``target_samples`` valid cycles), emitting each cycle."""
        assert self._transport is not None
        reader = GwLiveReader(self._transport, event_log=self._log)
        period_s = 1.0 / frequency_hz
        state_message = "Stopped"
        received_samples = 0
        try:
            while not self._gw_live_stop.is_set():
                cycle_started = monotonic()
                cycle = reader.poll_once(driver_id)
                if target_samples is None:
                    self.gw_live_updated.emit(cycle)
                elif cycle.is_complete_valid:
                    received_samples += 1
                    self.gw_live_updated.emit(cycle)
                    self.gw_live_progress.emit(received_samples, target_samples)
                    if received_samples >= target_samples:
                        state_message = f"Completed {received_samples}/{target_samples}"
                        break
                remaining = period_s - (monotonic() - cycle_started)
                self._gw_live_stop.wait(remaining if remaining > 0 else 0.001)
        except Exception as exc:
            logger.exception("GW live reader failed")
            state_message = str(exc)
        finally:
            self._gw_live_stop.set()
            self._gw_live_thread = None
            self.gw_live_state_changed.emit(False, state_message)

    @Slot()
    def start_gw_calibration(self) -> None:
        """Run encoder calibration on a background thread."""
        self._start_gw_calibration_operation(read_only=False)

    @Slot()
    def read_gw_calibration_result(self) -> None:
        """Read the stored calibration result on a background thread."""
        self._start_gw_calibration_operation(read_only=True)

    def _start_gw_calibration_operation(self, *, read_only: bool) -> None:
        """Validate preconditions and start the calibration (or read-result) thread."""
        if self._transport is None or not self._transport.is_connected():
            self.gw_calibration_updated.emit(
                GwCalibrationUpdate("FAIL", "CAN adapter is not connected", terminal=True)
            )
            return
        if self._detected_driver_id is None:
            self.gw_calibration_updated.emit(
                GwCalibrationUpdate("FAIL", "Detect a driver ID before starting", terminal=True)
            )
            return
        if self._gw_live_thread is not None and self._gw_live_thread.is_alive():
            self.gw_calibration_updated.emit(
                GwCalibrationUpdate("FAIL", "Stop Live Amplitude before using calibration", terminal=True)
            )
            return
        if self._gw_calibration_thread is not None and self._gw_calibration_thread.is_alive():
            self.gw_calibration_updated.emit(
                GwCalibrationUpdate("STARTING", "Calibration is already active")
            )
            return

        self._gw_calibration_stop.clear()
        self._gw_calibration_thread = Thread(
            target=self._gw_calibration_loop,
            args=(self._detected_driver_id, read_only),
            daemon=True,
            name="gw-calibration-read" if read_only else "gw-calibration",
        )
        self._gw_calibration_thread.start()
        if read_only:
            self.gw_calibration_updated.emit(GwCalibrationUpdate("READING", "Sending calibration READ RESULT"))
        else:
            self.gw_calibration_updated.emit(GwCalibrationUpdate("STARTING", "Sending calibration START"))

    def _stop_gw_calibration(self) -> None:
        thread = self._gw_calibration_thread
        if thread is None:
            return
        self._gw_calibration_stop.set()
        if thread is not current_thread():
            thread.join(timeout=0.3)

    def _gw_calibration_loop(self, driver_id: int, read_only: bool = False) -> None:
        """Run calibration and forward updates; always ends with a terminal update."""
        assert self._transport is not None
        runner = GwCalibrationRunner(self._transport, event_log=self._log)
        terminal_emitted = False

        def publish(update: GwCalibrationUpdate) -> None:
            nonlocal terminal_emitted
            terminal_emitted = terminal_emitted or update.terminal
            self.gw_calibration_updated.emit(update)

        try:
            operation = runner.read_result if read_only else runner.run
            operation(driver_id, on_update=publish, stop_event=self._gw_calibration_stop)
        except GwCalibrationCancelled as exc:
            if not terminal_emitted:
                publish(GwCalibrationUpdate("STOPPED", str(exc), terminal=True))
        except Exception as exc:
            logger.exception("GW calibration failed")
            if not terminal_emitted:
                publish(GwCalibrationUpdate("FAIL", str(exc), terminal=True))
        finally:
            self._gw_calibration_stop.set()
            self._gw_calibration_thread = None

    def _run_test(
        self,
        which: str,
        timeout_ms: int,
        interval_ms: int | None = None,
        total_messages: int | None = None,
        theta_step: float | None = None,
        duration_ms: int | None = None,
        period_ticks: int | None = None,
    ) -> None:
        """Start a test on its own daemon thread (fails immediately if CAN is not connected)."""
        if self._transport is None or not self._transport.is_connected():
            self.test_finished.emit(
                TestOutcome(which, RuntimeError("CAN adapter is not connected"))
            )
            return

        Thread(
            target=self._execute_test,
            args=(which, timeout_ms, interval_ms, total_messages, theta_step, duration_ms, period_ticks),
            daemon=True,
            name=f"fct-{which}",
        ).start()

    def _execute_test(
        self,
        which: str,
        timeout_ms: int,
        interval_ms: int | None,
        total_messages: int | None,
        theta_step: float | None,
        duration_ms: int | None,
        period_ticks: int | None,
    ) -> None:

        """Thread body: run the requested test through :class:`TestRunner` and emit its outcome and log."""
        if self._transport is None:
            self.test_finished.emit(TestOutcome(which, RuntimeError("CAN adapter is not connected")))
            return
        service = PcbTestService(self._transport, self._log, use_fct=True)
        try:
            if which == "can":
                result = self._runner.run(which, service.run_can_test, timeout_ms)
                if result.verdict.passed and result.verdict.returned_driver_id is not None:
                    self._detected_driver_id = result.verdict.returned_driver_id
                else:
                    self._detected_driver_id = None
            elif which == "can_stress":
                result = self._runner.run(
                    which,
                    service.run_can_stress_test,
                    timeout_ms,
                    self._detected_driver_id,
                    CAN_STRESS_DEFAULT_INTERVAL_MS if interval_ms is None else interval_ms,
                    CAN_STRESS_DEFAULT_TOTAL_MESSAGES if total_messages is None else total_messages,
                    lambda count: self.test_progress.emit("can_stress", count),
                )
            elif which == "rs485_stress":
                result = self._runner.run(
                    which,
                    service.run_rs485_stress_test,
                    timeout_ms,
                    self._detected_driver_id,
                    self._rs485_transport,
                    RS485_STRESS_DEFAULT_DURATION_MS if duration_ms is None else duration_ms,
                    RS485_STRESS_DEFAULT_PERIOD_TICKS if period_ticks is None else period_ticks,
                    lambda count: self.test_progress.emit("rs485_stress", count),
                )
            elif which == "gongwang":
                result = self._runner.run(
                    which,
                    service.run_gongwang_encoder_test,
                    GONGWANG_DEFAULT_DURATION_MS if duration_ms is None else duration_ms,
                    self._detected_driver_id,
                    1 if period_ticks is None else period_ticks,
                    timeout_ms,
                )
            else:
                result = self._runner.run(
                    which,
                    service.run_openloop_test,
                    timeout_ms,
                    self._detected_driver_id,
                    OPEN_LOOP_DEFAULT_THETA_STEP if theta_step is None else theta_step,
                    OPEN_LOOP_DEFAULT_DURATION_MS if duration_ms is None else duration_ms,
                )
        except Exception as exc:
            logger.exception("Test %s failed with an exception", which)
            self.test_finished.emit(TestOutcome(which, exc))
            return

        self.log_updated.emit([item.format_line() for item in self._log.snapshot()])
        self.test_finished.emit(TestOutcome(which, result))
