"""Application-wide defaults for adapter selection and test timeouts.

Protocol constants (CAN function codes, parameter indices, firmware test IDs)
live in :mod:`pcbt_app.protocol.mit_firmware` and :mod:`pcbt_app.protocol.fct_can`.
"""

from __future__ import annotations

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
# Minimum interval between live progress callbacks so the GUI is not flooded.
PROGRESS_THROTTLE_S = 0.050


def backend_for_adapter_model(model: str) -> str:
    """Map a Waveshare adapter model letter (``"A"``/``"B"``) to its backend name."""
    normalized = model.strip().upper()
    if normalized == "A":
        return WAVESHARE_BACKEND
    if normalized == "B":
        return WAVESHARE_MODEL_B_BACKEND
    raise ValueError('USB_CAN_ADAPTER_MODEL must be either "A" or "B"')


DEFAULT_BACKEND = backend_for_adapter_model(USB_CAN_ADAPTER_MODEL)
