from __future__ import annotations

from collections import deque

import pytest

from pcbt_app.config import (
    WAVESHARE_BACKEND,
    WAVESHARE_MODEL_B_BACKEND,
    backend_for_adapter_model,
)
from pcbt_app.models import RawCanFrame
from pcbt_app.transport.base import CanTransportError
from pcbt_app.transport.factory import create_transport
from pcbt_app.transport.waveshare_usbcan_a import (
    build_waveshare_config_packet,
    encode_waveshare_can_frame,
)
from pcbt_app.transport.waveshare_usbcanfd_b import (
    MODEL_B_CHANNEL,
    WaveshareUsbCanFdBTransport,
)


class FakeModelBProxy:
    def __init__(self) -> None:
        self.opened = False
        self.last_write_ok = True
        self.last_error = ""
        self.writes: list[bytes] = []
        self.rx_packets: deque[bytes] = deque()

    def begin_operation(self) -> None:
        self.last_write_ok = True
        self.last_error = ""

    def open(self, mode=None) -> bool:
        del mode
        self.opened = True
        return True

    def close(self) -> None:
        self.opened = False

    def isOpen(self) -> bool:
        return self.opened

    def errorString(self) -> str:
        return self.last_error

    def write(self, data: bytes) -> int:
        self.writes.append(bytes(data))
        return len(data)

    def bytesAvailable(self) -> int:
        return sum(len(packet) for packet in self.rx_packets)

    def waitForReadyRead(self, timeout_ms: int) -> bool:
        del timeout_ms
        return bool(self.rx_packets)

    def readAll(self) -> bytes:
        return self.rx_packets.popleft() if self.rx_packets else b""

    def clear(self, direction=None) -> bool:
        del direction
        self.rx_packets.clear()
        return True


def make_transport() -> tuple[WaveshareUsbCanFdBTransport, FakeModelBProxy]:
    proxy = FakeModelBProxy()
    transport = WaveshareUsbCanFdBTransport(
        channel=MODEL_B_CHANNEL,
        can_bitrate=1_000_000,
        proxy_factory=lambda **_kwargs: proxy,
    )
    return transport, proxy


def test_compile_time_adapter_model_mapping() -> None:
    assert backend_for_adapter_model("A") == WAVESHARE_BACKEND
    assert backend_for_adapter_model("b") == WAVESHARE_MODEL_B_BACKEND
    with pytest.raises(ValueError, match="either"):
        backend_for_adapter_model("C")


def test_factory_creates_model_b_transport() -> None:
    transport = create_transport(WAVESHARE_MODEL_B_BACKEND, MODEL_B_CHANNEL, 1_000_000)

    assert isinstance(transport, WaveshareUsbCanFdBTransport)
    assert transport.channel == MODEL_B_CHANNEL
    assert transport.can_bitrate == 1_000_000


def test_model_b_transport_configures_sends_and_receives_with_existing_codec() -> None:
    transport, proxy = make_transport()
    tx_frame = RawCanFrame(arbitration_id=0x1B0000FE, data=bytes(range(8)))
    rx_frame = RawCanFrame(arbitration_id=0x1C00FE01, data=b"\x02\x00\x02\x01\x00\x00\x00\x00")

    transport.connect()
    assert transport.is_connected()
    transport.send(tx_frame)
    proxy.rx_packets.append(encode_waveshare_can_frame(rx_frame))

    received = transport.recv(timeout_s=0.01)

    assert proxy.writes == [build_waveshare_config_packet(1_000_000), encode_waveshare_can_frame(tx_frame)]
    assert received is not None
    assert received.arbitration_id == rx_frame.arbitration_id
    assert received.data == rx_frame.data
    assert received.is_extended_id is True

    transport.disconnect()
    assert not transport.is_connected()


def test_model_b_reconnect_and_manual_configuration() -> None:
    transport, proxy = make_transport()
    packet = build_waveshare_config_packet(1_000_000)
    transport.connect()
    transport.connect()
    assert proxy.writes == [packet]
    assert transport.configure_adapter() == packet
    transport.disconnect()
    transport.connect()
    assert proxy.writes == [packet, packet, packet]
    transport.disconnect()


def test_model_b_failed_start_closes_device() -> None:
    transport, proxy = make_transport()
    transport.can_bitrate = 123
    with pytest.raises(CanTransportError):
        transport.connect()
    assert not proxy.opened
    assert not transport.is_connected()


def test_model_b_transport_rejects_operations_before_connect() -> None:
    transport, _proxy = make_transport()

    with pytest.raises(CanTransportError, match="not connected"):
        transport.configure_adapter()

def test_model_b_selection_is_passed_to_native_proxy():
    captured = {}
    proxy = FakeModelBProxy()
    def factory(**kwargs):
        captured.update(kwargs)
        return proxy
    transport = WaveshareUsbCanFdBTransport(channel="USB-CAN-FD-B Device 3 / CAN1", proxy_factory=factory)
    transport.connect()
    assert captured['device_index'] == 3
    assert captured['channel_index'] == 0
    transport.disconnect()

def test_scan_model_b_reports_only_online_devices_and_closes_handles(monkeypatch):
    import pcbt_app.transport.waveshare_usbcanfd_b as module
    from pcbt_app.transport.Communication_CAN_CANFD_ModelB import ZCAN_DEVICE_INFO
    import ctypes
    class Dll:
        def __init__(self):
            self.closed = []
        def ZCAN_OpenDevice(self, kind, index, reserved):
            return index + 100 if index in (0, 2) else 0
        def ZCAN_IsDeviceOnLine(self, handle):
            return 2 if handle == 102 else 3
        def ZCAN_GetDeviceInf(self, handle, ptr):
            info = ctypes.cast(ptr, ctypes.POINTER(ZCAN_DEVICE_INFO)).contents
            info.str_Serial_Num[:4] = b'TEST'
            return 1
        def ZCAN_CloseDevice(self, handle):
            self.closed.append(handle)
            return 1
    dll = Dll()
    class Proxy:
        _dll = dll
        def _load_library(self): pass
        def _valid_handle(self, handle): return bool(handle)
        def _check_status(self, status, operation): assert status == 1
    monkeypatch.setattr(module, 'ModelBSerialProxy', Proxy)
    assert module.scan_model_b_devices() == [(2, 'Device 2 / SN TEST')]
    assert dll.closed == [100, 102]
