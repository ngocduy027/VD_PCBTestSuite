"""Command-line runner for headless/bench use (``pcbt-cli``).

Exit codes: ``0`` PASS, ``2`` FAIL verdict, ``3`` transport/adapter error.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from pcbt_app.config import (
    DEFAULT_BACKEND,
    DEFAULT_BITRATE,
    DEFAULT_CAN_TEST_TIMEOUT_MS,
    DEFAULT_MODEL_B_CHANNEL,
    DEFAULT_OPENLOOP_TIMEOUT_MS,
    DEFAULT_PCAN_CHANNEL,
    DEFAULT_SERIAL_BAUD,
    DEFAULT_WAVESHARE_CHANNEL,
    WAVESHARE_BACKEND,
    WAVESHARE_MODEL_B_BACKEND,
)
from pcbt_app.logging_store import EventLog
from pcbt_app.services.pcb_test_service import PcbTestService
from pcbt_app.transport.factory import create_transport
from pcbt_app.transport.waveshare_usbcan_a import list_waveshare_com_ports

logger = logging.getLogger(__name__)


def default_channel_for_backend(backend: str) -> str:
    """Default ``--channel`` for a backend: first detected COM port for Model A, else a fixed default."""
    if backend == WAVESHARE_MODEL_B_BACKEND:
        return DEFAULT_MODEL_B_CHANNEL
    if backend == WAVESHARE_BACKEND:
        try:
            ports = list_waveshare_com_ports()
        except Exception:
            logger.debug("Waveshare COM port listing failed", exc_info=True)
            ports = []
        return ports[0][0] if ports else DEFAULT_WAVESHARE_CHANNEL
    return DEFAULT_PCAN_CHANNEL


def build_parser() -> argparse.ArgumentParser:
    """Argument parser for the ``can-test`` and ``openloop-test`` sub-commands."""
    parser = argparse.ArgumentParser(prog="pcbt-cli", description="Run PCB production tests over CAN")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("can-test", "openloop-test"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--backend", default=DEFAULT_BACKEND)
        sub.add_argument("--channel", default=None)
        sub.add_argument("--bitrate", type=int, default=DEFAULT_BITRATE)
        sub.add_argument("--serial-baud", type=int, default=DEFAULT_SERIAL_BAUD)
        sub.add_argument(
            "--timeout-ms",
            type=int,
            default=DEFAULT_CAN_TEST_TIMEOUT_MS if name == "can-test" else DEFAULT_OPENLOOP_TIMEOUT_MS,
        )
        if name == "openloop-test":
            sub.add_argument("--driver-id", type=int, default=None, help="Detected driver ID; scan first if omitted")
        sub.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one test and print the verdict (``--json`` for machine-readable output); returns the exit code."""
    args = build_parser().parse_args(argv)
    channel = args.channel or default_channel_for_backend(args.backend)
    transport = create_transport(args.backend, channel, args.bitrate, serial_baud=args.serial_baud)
    log = EventLog()

    try:
        transport.connect()
        service = PcbTestService(transport=transport, event_log=log, use_fct=True)
        if args.command == "can-test":
            result = service.run_can_test(args.timeout_ms)
        else:
            driver_id = args.driver_id
            if driver_id is None:
                scan_result = service.run_can_test(DEFAULT_CAN_TEST_TIMEOUT_MS)
                if not scan_result.verdict.passed:
                    result = scan_result
                else:
                    driver_id = scan_result.verdict.returned_driver_id
                    result = service.run_openloop_test(args.timeout_ms, driver_id)
            else:
                result = service.run_openloop_test(args.timeout_ms, driver_id)
    except Exception as exc:
        logger.debug("CLI test aborted", exc_info=True)
        print(f"Transport error: {exc}", file=sys.stderr)
        return 3
    finally:
        transport.disconnect()

    verdict = result.verdict
    payload = {
        "test_name": verdict.test_name,
        "passed": verdict.passed,
        "status": verdict.status_text,
        "reason": verdict.reason,
        "returned_driver_id": verdict.returned_driver_id,
        "firmware_status": verdict.firmware_status,
        "stop_reason": verdict.stop_reason,
        "elapsed_ms": verdict.elapsed_ms,
        "events": verdict.events,
        "transport_warning": result.transport_warning,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"{verdict.test_name}: {verdict.status_text}")
        print(f"Reason: {verdict.reason}")
        print(f"Returned driver ID: {verdict.returned_driver_id}")
        print(f"Firmware status: {verdict.firmware_status}")
        print(f"Stop reason: {verdict.stop_reason}")
        print(f"Elapsed: {verdict.elapsed_ms} ms")
        if result.transport_warning:
            print(f"Transport warning: {result.transport_warning}")
        if verdict.events:
            print("Events:")
            for event in verdict.events:
                print(f"- {event}")
    return 0 if verdict.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
