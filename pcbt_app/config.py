from __future__ import annotations

from dataclasses import dataclass, field


DEFAULT_CAN_TEST_TIMEOUT_MS = 10000
DEFAULT_OPENLOOP_TIMEOUT_MS = 15000
DEFAULT_BITRATE = 1_000_000
DEFAULT_SERIAL_BAUD = 2_000_000
USB_CAN_ADAPTER_MODEL = "A"  # First-run default; the GUI supports both models.
WAVESHARE_BACKEND = "waveshare-usbcan-a"
WAVESHARE_MODEL_B_BACKEND = "waveshare-usbcanfd-b"
DEFAULT_WAVESHARE_CHANNEL = "COM1"
DEFAULT_MODEL_B_CHANNEL = "USB-CAN-FD-B Device 0 / CAN2"
DEFAULT_PCAN_CHANNEL = "PCAN_USBBUS1"


def backend_for_adapter_model(model: str) -> str:
    normalized = model.strip().upper()
    if normalized == "A":
        return WAVESHARE_BACKEND
    if normalized == "B":
        return WAVESHARE_MODEL_B_BACKEND
    raise ValueError('USB_CAN_ADAPTER_MODEL must be either "A" or "B"')


DEFAULT_BACKEND = backend_for_adapter_model(USB_CAN_ADAPTER_MODEL)
DEFAULT_CHANNEL = DEFAULT_WAVESHARE_CHANNEL if DEFAULT_BACKEND == WAVESHARE_BACKEND else DEFAULT_MODEL_B_CHANNEL
DEFAULT_COMMAND_ID = 0x1B000000
EXPECTED_HOST_ID = 0xFE
PCB_TEST_COMMAND_FUNCTION = 0x1B
PCB_TEST_RESULT_FUNCTION = 0x1C
MOTOR_ID_RESPONSE_FUNCTION = 0x00
TEST_ID_OPENLOOP = 0x01
TEST_ID_CAN = 0x02
TEST_ID_GONGWANG = 0x04


@dataclass(slots=True)
class TransportConfig:
    backend: str = DEFAULT_BACKEND
    channel: str = DEFAULT_CHANNEL
    bitrate: int = DEFAULT_BITRATE
    serial_baud: int = DEFAULT_SERIAL_BAUD


@dataclass(slots=True)
class AppSettings:
    transport: TransportConfig = field(default_factory=TransportConfig)
    can_test_timeout_ms: int = DEFAULT_CAN_TEST_TIMEOUT_MS
    openloop_timeout_ms: int = DEFAULT_OPENLOOP_TIMEOUT_MS
    command_id: int = DEFAULT_COMMAND_ID
