"""Create the transport implementation for a backend name."""

from __future__ import annotations

from pcbt_app.config import WAVESHARE_BACKEND, WAVESHARE_MODEL_B_BACKEND
from pcbt_app.transport.base import AbstractCanTransport
from pcbt_app.transport.python_can_bus import PythonCanTransport
from pcbt_app.transport.waveshare_usbcan_a import WaveshareUsbCanATransport


def create_transport(
    backend: str,
    channel: str,
    bitrate: int,
    serial_baud: int = 2_000_000,
) -> AbstractCanTransport:
    """Return an unconnected transport for ``backend``; unknown names are passed to python-can as its interface name."""
    if backend == WAVESHARE_BACKEND:
        return WaveshareUsbCanATransport(channel=channel, can_bitrate=bitrate, serial_baud=serial_baud)
    if backend == WAVESHARE_MODEL_B_BACKEND:
        from pcbt_app.transport.waveshare_usbcanfd_b import WaveshareUsbCanFdBTransport

        return WaveshareUsbCanFdBTransport(channel=channel, can_bitrate=bitrate)
    return PythonCanTransport(backend=backend, channel=channel, bitrate=bitrate)
