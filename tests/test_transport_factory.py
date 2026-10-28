from pcbt_app.config import WAVESHARE_BACKEND
from pcbt_app.transport.factory import create_transport
from pcbt_app.transport.python_can_bus import PythonCanTransport
from pcbt_app.transport.waveshare_usbcan_a import WaveshareUsbCanATransport


def test_factory_creates_waveshare_transport() -> None:
    transport = create_transport(WAVESHARE_BACKEND, "COM5", 1_000_000, serial_baud=2_000_000)
    assert isinstance(transport, WaveshareUsbCanATransport)
    assert transport.channel == "COM5"
    assert transport.can_bitrate == 1_000_000
    assert transport.serial_baud == 2_000_000


def test_factory_preserves_python_can_backends() -> None:
    transport = create_transport("pcan", "PCAN_USBBUS1", 500_000)
    assert isinstance(transport, PythonCanTransport)
    assert transport.backend == "pcan"
    assert transport.channel == "PCAN_USBBUS1"
