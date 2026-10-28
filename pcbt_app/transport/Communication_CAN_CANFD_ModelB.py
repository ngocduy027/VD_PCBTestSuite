"""Waveshare USB-CAN-FD-B compatibility module for VinD applications.

The VinD application talks to :class:`USB_CANFD_B` through the same in-memory
``AA 55`` packets used by ``USB_CAN_A``.  Those packets are decoded by this
module and are never sent to the Model B hardware.  Hardware access uses the
vendor's native ``ControlCANFD.dll`` API directly.

Integration in an existing VinD ``main.py``::

    from Communication_CAN_CANFD_ModelB import USB_CANFD_B

    self.vUSB_CAN_A = USB_CANFD_B(self.serial, self.ui)
    self.serial = self.vUSB_CAN_A.serial

Default hardware selection: device index 0, CAN2 (channel index 1). Put the x64
``ControlCANFD.dll`` beside this module for a source run, or beside the frozen
executable for a packaged run.  Only classic CAN frames with at most eight data
bytes are supported.
"""

from __future__ import annotations

import ctypes
import inspect
import logging
import struct
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QObject, Signal, Slot

logger = logging.getLogger(__name__)

try:
    from Communication_CAN import USB_CAN_A
except ImportError:
    class USB_CAN_A(QObject):  # type: ignore[no-redef]
        """Placeholder used when the legacy VinD Model A module is absent."""

        def __init__(self, *args, **kwargs):
            del args, kwargs
            raise RuntimeError(
                "USB_CANFD_B's legacy VinD wrapper requires Communication_CAN.py. "
                "AppTestPCB should use ModelBSerialProxy through its Model B transport."
            )


UINT = ctypes.c_uint32
UINT64 = ctypes.c_uint64
BYTE = ctypes.c_uint8
USHORT = ctypes.c_uint16

STATUS_OK = 1
STATUS_ONLINE = 2
STATUS_OFFLINE = 3
STATUS_UNSUPPORTED = 4

ZCAN_USBCANFD_200U = 41
TYPE_CAN = 0
TYPE_CANFD = 1

# The vendor DLL has no enumeration API; device indices 0..N-1 are probed.
MAX_SCAN_DEVICES = 64

CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_ERR_FLAG = 0x20000000
CAN_EFF_MASK = 0x1FFFFFFF
CAN_SFF_MASK = 0x7FF


class ZCAN_CAN_FRAME(ctypes.Structure):
    """ctypes mirror of the vendor ``ZCAN_CAN_FRAME`` struct (see docs/USB-CAN-FD-B-API-Manual.pdf)."""

    _fields_ = [
        ("can_id", UINT),
        ("can_dlc", BYTE),
        ("__pad", BYTE),
        ("__res0", BYTE),
        ("__res1", BYTE),
        ("data", BYTE * 8),
    ]


class ZCAN_CANFD_FRAME(ctypes.Structure):
    """ctypes mirror of the vendor ``ZCAN_CANFD_FRAME`` struct."""

    _fields_ = [
        ("can_id", UINT),
        ("len", BYTE),
        ("flags", BYTE),
        ("__res0", BYTE),
        ("__res1", BYTE),
        ("data", BYTE * 64),
    ]


class ZCAN_CAN_INIT_CONFIG(ctypes.Structure):
    """ctypes mirror of the vendor classic-CAN init config."""

    _fields_ = [
        ("acc_code", UINT),
        ("acc_mask", UINT),
        ("reserved", UINT),
        ("filter", BYTE),
        ("timing0", BYTE),
        ("timing1", BYTE),
        ("mode", BYTE),
    ]


class ZCAN_CANFD_INIT_CONFIG(ctypes.Structure):
    """ctypes mirror of the vendor CAN-FD init config."""

    _fields_ = [
        ("acc_code", UINT),
        ("acc_mask", UINT),
        ("abit_timing", UINT),
        ("dbit_timing", UINT),
        ("brp", UINT),
        ("filter", BYTE),
        ("mode", BYTE),
        ("pad", USHORT),
        ("reserved", UINT),
    ]


class ZCAN_INIT_CONFIG_UNION(ctypes.Union):
    """Union of the classic and FD init configs, as in the vendor header."""

    _fields_ = [
        ("can", ZCAN_CAN_INIT_CONFIG),
        ("canfd", ZCAN_CANFD_INIT_CONFIG),
    ]


class ZCAN_CHANNEL_INIT_CONFIG(ctypes.Structure):
    """ctypes mirror of ``ZCAN_CHANNEL_INIT_CONFIG`` (``can_type`` selects the union member)."""

    _anonymous_ = ("config",)
    _fields_ = [
        ("can_type", UINT),
        ("config", ZCAN_INIT_CONFIG_UNION),
    ]


class ZCAN_TRANSMIT_DATA(ctypes.Structure):
    """ctypes mirror of ``ZCAN_Transmit_Data``."""

    _fields_ = [
        ("frame", ZCAN_CAN_FRAME),
        ("transmit_type", UINT),
    ]


class ZCAN_RECEIVE_DATA(ctypes.Structure):
    """ctypes mirror of ``ZCAN_Receive_Data``."""

    _fields_ = [
        ("frame", ZCAN_CAN_FRAME),
        ("timestamp", UINT64),
    ]


class ZCAN_TRANSMIT_FD_DATA(ctypes.Structure):
    """ctypes mirror of ``ZCAN_TransmitFD_Data``."""

    _fields_ = [
        ("frame", ZCAN_CANFD_FRAME),
        ("transmit_type", UINT),
    ]


class ZCAN_RECEIVE_FD_DATA(ctypes.Structure):
    """ctypes mirror of ``ZCAN_ReceiveFD_Data``."""

    _fields_ = [
        ("frame", ZCAN_CANFD_FRAME),
        ("timestamp", UINT64),
    ]


class ZCAN_DEVICE_INFO(ctypes.Structure):
    """ctypes mirror of ``ZCAN_DEVICE_INFO`` (serial number is a NUL-terminated byte string)."""

    _fields_ = [
        ("hw_Version", USHORT),
        ("fw_Version", USHORT),
        ("dr_Version", USHORT),
        ("in_Version", USHORT),
        ("irq_Num", USHORT),
        ("can_Num", BYTE),
        ("str_Serial_Num", BYTE * 20),
        ("str_hw_Type", BYTE * 40),
        ("reserved", USHORT * 4),
    ]


EXPECTED_ABI_SIZES = {
    ZCAN_CAN_FRAME: 16,
    ZCAN_CANFD_FRAME: 72,
    ZCAN_CHANNEL_INIT_CONFIG: 32,
    ZCAN_TRANSMIT_DATA: 20,
    ZCAN_RECEIVE_DATA: 24,
    ZCAN_TRANSMIT_FD_DATA: 76,
    ZCAN_RECEIVE_FD_DATA: 80,
}


def validate_abi_sizes():
    """Fail early if Python's layout differs from the vendor x64 ABI."""

    for structure, expected in EXPECTED_ABI_SIZES.items():
        actual = ctypes.sizeof(structure)
        if actual != expected:
            raise RuntimeError(
                f"ControlCANFD ABI mismatch for {structure.__name__}: "
                f"expected {expected} bytes, got {actual}."
            )


validate_abi_sizes()


class ModelBError(RuntimeError):
    """Internal error translated to QSerialPort-style return values."""


class _ReceivedFrame:
    """Classic CAN frame pulled from the DLL before it is re-encoded as an ``AA 55`` packet."""

    __slots__ = ("arbitration_id", "data", "is_extended", "timestamp_us")

    def __init__(self, arbitration_id, data, is_extended, timestamp_us=0):
        self.arbitration_id = arbitration_id
        self.data = data
        self.is_extended = is_extended
        self.timestamp_us = timestamp_us


class _ModelBSerialProxy(QObject):
    """QSerialPort-compatible facade backed by ControlCANFD.dll."""

    readyRead = Signal()
    modelBError = Signal(str)
    disconnected = Signal(str)

    DEVICE_INDEX = 0
    CHANNEL_INDEX = 1
    RECEIVE_BATCH_SIZE = 256
    RECEIVE_IDLE_SECONDS = 0.005
    ONLINE_CHECK_SECONDS = 1.0
    PACKET_SIZE = 20

    HEADER = b"\xAA\x55"
    FIXED_LENGTH = 0x02
    VARIABLE_LENGTH = 0x12
    DATA_PACKET = 0x01
    STANDARD_FRAME = 0x01
    EXTENDED_FRAME = 0x02
    NORMAL_MODE = 0x00

    BITRATE_BY_CODE = {
        0x01: 1_000_000,
        0x02: 800_000,
        0x03: 500_000,
        0x05: 250_000,
        0x07: 125_000,
        0x08: 100_000,
    }

    def __init__(
        self,
        parent=None,
        *,
        dll_path: str | Path | None = None,
        library_loader=None,
        device_index=0,
        channel_index=1,
    ):
        super().__init__(parent)
        if not isinstance(device_index, int) or not 0 <= device_index <= 0xFFFFFFFF:
            raise ModelBError("Device index must be an unsigned 32-bit integer")
        if channel_index not in (0, 1):
            raise ModelBError("CAN channel index must be 0 (CAN1) or 1 (CAN2)")
        self.DEVICE_INDEX = device_index
        self.CHANNEL_INDEX = channel_index
        self._explicit_dll_path = Path(dll_path) if dll_path else None
        self._library_loader = library_loader
        self._dll: Any = None  # ctypes.WinDLL once loaded
        self._dll_path: Path | None = None
        self._device_handle = None
        self._channel_handle = None
        self._device_info: ZCAN_DEVICE_INFO | None = None

        self._is_open = False
        self._is_started = False
        self._active_is_extended = True
        self._last_error = ""
        self._last_write_ok = True

        self._port_name = f"USB-CAN-FD-B Device {device_index} / CAN{channel_index + 1}"
        self._serial_baud = None
        self._data_bits = None
        self._parity = None
        self._stop_bits = None
        self._flow_control = None

        self._rx_packets: deque[bytes] = deque()
        self._rx_condition = threading.Condition()
        self._tx_buffer = bytearray()
        self._tx_buffer_lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()

        self._receive_stop = threading.Event()
        self._receive_thread: threading.Thread | None = None

    @staticmethod
    def resolve_dll_path() -> Path:
        """Locate ``ControlCANFD.dll``: beside the exe or in the PyInstaller bundle when frozen, else beside this module."""
        if getattr(sys, "frozen", False):
            beside_executable = Path(sys.executable).resolve().parent / "ControlCANFD.dll"
            if beside_executable.is_file():
                return beside_executable
            bundle_root = getattr(sys, "_MEIPASS", None)
            if bundle_root:
                bundled = Path(bundle_root) / "ControlCANFD.dll"
                if bundled.is_file():
                    return bundled
            return beside_executable
        return Path(__file__).resolve().parent / "ControlCANFD.dll"

    @staticmethod
    def _handle_value(handle) -> int:
        if handle is None:
            return 0
        if isinstance(handle, ctypes.c_void_p):
            return int(handle.value or 0)
        return int(handle)

    @classmethod
    def _valid_handle(cls, handle) -> bool:
        return cls._handle_value(handle) != 0

    @staticmethod
    def _check_status(result, operation):
        status = int(result)
        if status != STATUS_OK:
            raise ModelBError(f"{operation}: ControlCANFD returned status {status}.")

    @staticmethod
    def _check_online_status(result, operation):
        status = int(result)
        if status == STATUS_ONLINE:
            return
        if status == STATUS_OFFLINE:
            detail = "USB-CAN-FD-B is offline."
        elif status == STATUS_UNSUPPORTED:
            detail = "The device does not support online-state detection."
        else:
            detail = f"ControlCANFD returned unexpected online status {status}."
        raise ModelBError(f"{operation}: {detail}")

    def _set_error(self, message, *, emit=True):
        self._last_error = str(message)
        self._last_write_ok = False
        logger.warning("USB-CAN-FD-B: %s", self._last_error)
        if emit:
            self.modelBError.emit(self._last_error)

    def scan_online_devices(self, max_devices=MAX_SCAN_DEVICES):
        """Return ``(index, label)`` for each online USB-CAN-FD-B adapter.

        The vendor DLL has no enumeration API, so each index is opened, checked
        for online status, queried for its serial number and closed again. No
        CAN channel is initialized or started, and this proxy is not opened.
        """
        self._load_library()
        found = []
        for index in range(max_devices):
            handle = self._dll.ZCAN_OpenDevice(ZCAN_USBCANFD_200U, index, 0)
            if not self._valid_handle(handle):
                continue
            try:
                if int(self._dll.ZCAN_IsDeviceOnLine(handle)) != STATUS_ONLINE:
                    continue
                info = ZCAN_DEVICE_INFO()
                self._check_status(
                    self._dll.ZCAN_GetDeviceInf(handle, ctypes.byref(info)),
                    "Read device information",
                )
                serial = bytes(info.str_Serial_Num).split(b"\0", 1)[0].decode("ascii", errors="replace")
                found.append((index, f"Device {index}" + (f" / SN {serial}" if serial else "")))
            finally:
                self._check_status(self._dll.ZCAN_CloseDevice(handle), "Close scanned device")
        return found

    def begin_operation(self):
        """Reset the error state before a write so ``last_write_ok`` reflects only that call."""
        self._last_write_ok = True
        self._last_error = ""

    @property
    def last_write_ok(self):
        """False if the most recent operation reported an error."""
        return self._last_write_ok

    def _load_library(self):
        """Load the x64 ``ControlCANFD.dll`` once and bind the API; raises ``ModelBError`` with setup hints."""
        if self._dll is not None:
            return
        if struct.calcsize("P") * 8 != 64:
            raise ModelBError(
                "USB-CAN-FD-B requires a 64-bit Python process."
            )

        path = self._explicit_dll_path or self.resolve_dll_path()
        if not path.is_file():
            raise ModelBError(
                f"ControlCANFD.dll was not found at {path}. "
                "Place the x64 DLL beside the module or executable."
            )

        loader = self._library_loader or getattr(ctypes, "WinDLL", None)
        if loader is None:
            raise ModelBError("ctypes.WinDLL is unavailable on this platform.")
        try:
            self._dll = loader(str(path))
        except OSError as exc:
            raise ModelBError(
                f"Cannot load {path}: {exc}. Check the x64 DLL, Waveshare "
                "driver, and Microsoft Visual C++ 2008 runtime."
            ) from exc

        self._dll_path = path
        try:
            self._bind_api()
        except Exception:
            self._dll = None
            self._dll_path = None
            raise

    def _bind(self, name, restype, *argtypes):
        try:
            function = getattr(self._dll, name)
        except AttributeError as exc:
            raise ModelBError(f"ControlCANFD.dll export {name} is missing.") from exc
        function.restype = restype
        function.argtypes = list(argtypes)

    def _bind_api(self):
        """Declare argument/return types for every DLL function used."""
        handle = ctypes.c_void_p
        self._bind("ZCAN_OpenDevice", handle, UINT, UINT, UINT)
        self._bind("ZCAN_CloseDevice", UINT, handle)
        self._bind("ZCAN_GetDeviceInf", UINT, handle, ctypes.POINTER(ZCAN_DEVICE_INFO))
        self._bind("ZCAN_IsDeviceOnLine", UINT, handle)
        self._bind(
            "ZCAN_InitCAN",
            handle,
            handle,
            UINT,
            ctypes.POINTER(ZCAN_CHANNEL_INIT_CONFIG),
        )
        self._bind("ZCAN_StartCAN", UINT, handle)
        self._bind("ZCAN_ResetCAN", UINT, handle)
        self._bind("ZCAN_ClearBuffer", UINT, handle)
        self._bind("ZCAN_GetReceiveNum", UINT, handle, BYTE)
        self._bind(
            "ZCAN_Transmit",
            UINT,
            handle,
            ctypes.POINTER(ZCAN_TRANSMIT_DATA),
            UINT,
        )
        self._bind(
            "ZCAN_Receive",
            UINT,
            handle,
            ctypes.POINTER(ZCAN_RECEIVE_DATA),
            UINT,
            ctypes.c_int,
        )
        self._bind("ZCAN_SetAbitBaud", UINT, handle, UINT, UINT)
        self._bind("ZCAN_SetDbitBaud", UINT, handle, UINT, UINT)
        self._bind("ZCAN_SetCANFDStandard", UINT, handle, UINT, UINT)
        self._bind("ZCAN_ClearFilter", UINT, handle)
        self._bind("ZCAN_SetFilterMode", UINT, handle, UINT)
        self._bind("ZCAN_SetFilterStartID", UINT, handle, UINT)
        self._bind("ZCAN_SetFilterEndID", UINT, handle, UINT)
        self._bind("ZCAN_AckFilter", UINT, handle)

    def _require_device(self):
        if not self._is_open or not self._valid_handle(self._device_handle):
            raise ModelBError("Open USB-CAN-FD-B before configuring CAN.")

    def _require_channel(self):
        if not self._is_started or not self._valid_handle(self._channel_handle):
            raise ModelBError("Press Set and Start before sending CAN frames.")

    # QSerialPort-compatible configuration methods.  Model B does not use them.
    def setPortName(self, value):
        self._port_name = str(value)

    def portName(self):
        return self._port_name

    def setBaudRate(self, value):
        self._serial_baud = value
        return True

    def setDataBits(self, value):
        self._data_bits = value
        return True

    def setParity(self, value):
        self._parity = value
        return True

    def setStopBits(self, value):
        self._stop_bits = value
        return True

    def setFlowControl(self, value):
        self._flow_control = value
        return True

    def errorString(self):
        return self._last_error

    def isOpen(self):
        return self._is_open

    def open(self, mode=None):
        """Open and verify the device (online, info readable); CAN starts when a config packet is written."""
        del mode
        if self._is_open:
            return True

        self.begin_operation()
        handle = None
        try:
            self._load_library()
            handle = self._dll.ZCAN_OpenDevice(
                ZCAN_USBCANFD_200U, self.DEVICE_INDEX, 0
            )
            if not self._valid_handle(handle):
                raise ModelBError(
                    f"Cannot open USB-CAN-FD-B device index {self.DEVICE_INDEX}."
                )
            self._device_handle = handle
            self._check_online_status(
                self._dll.ZCAN_IsDeviceOnLine(handle),
                "Check Model B online state",
            )
            info = ZCAN_DEVICE_INFO()
            self._check_status(
                self._dll.ZCAN_GetDeviceInf(handle, ctypes.byref(info)),
                "Read Model B device information",
            )
            self._device_info = info
            self._is_open = True
            self._last_write_ok = True
            return True
        except Exception as exc:
            if self._dll is not None and self._valid_handle(handle):
                try:
                    self._dll.ZCAN_CloseDevice(handle)
                except Exception:
                    pass
            self._device_handle = None
            self._device_info = None
            self._is_open = False
            self._set_error(exc)
            return False

    @staticmethod
    def _checksum(packet):
        return sum(packet[2:19]) & 0xFF

    @classmethod
    def _validate_packet(cls, packet):
        if len(packet) != cls.PACKET_SIZE:
            raise ModelBError(
                f"A fixed-length compatibility packet must be {cls.PACKET_SIZE} bytes."
            )
        if packet[:2] != cls.HEADER:
            raise ModelBError("Invalid compatibility packet header.")
        if packet[19] != cls._checksum(packet):
            raise ModelBError("Invalid compatibility packet checksum.")

    def _configure_from_packet(self, packet):
        """Apply a Model A configuration packet to the Model B channel.

        Supports fixed-length mode, the bitrates in ``BITRATE_BY_CODE`` and normal
        mode only. Resets the channel, sets arbitration and data bitrate to the same
        value (ISO CAN-FD controller, classic frames only), opens the full ID filter,
        starts CAN and the receive thread.
        """
        if packet[2] == self.VARIABLE_LENGTH:
            raise ModelBError("Variable-length Model A packets are not supported.")
        if packet[2] != self.FIXED_LENGTH:
            raise ModelBError(f"Unsupported compatibility length mode 0x{packet[2]:02X}.")

        bitrate = self.BITRATE_BY_CODE.get(packet[3])
        if bitrate is None:
            raise ModelBError(
                "Unsupported Model B bitrate. Supported values: "
                "1M, 800k, 500k, 250k, 125k, 100k."
            )

        frame_type = packet[4]
        if frame_type not in (self.STANDARD_FRAME, self.EXTENDED_FRAME):
            raise ModelBError(f"Unsupported CAN frame type 0x{frame_type:02X}.")
        if packet[13] != self.NORMAL_MODE:
            raise ModelBError("Model B supports normal CAN mode only.")

        is_extended = frame_type == self.EXTENDED_FRAME
        filter_end = CAN_EFF_MASK if is_extended else CAN_SFF_MASK

        self.stop_receive()
        with self._lifecycle_lock:
            self._require_device()
            if self._valid_handle(self._channel_handle):
                try:
                    self._dll.ZCAN_ResetCAN(self._channel_handle)
                finally:
                    self._channel_handle = None
                    self._is_started = False

            channel = None
            try:
                device = self._device_handle
                self._check_status(
                    self._dll.ZCAN_SetAbitBaud(
                        device, self.CHANNEL_INDEX, bitrate
                    ),
                    "Set Model B arbitration bitrate",
                )
                self._check_status(
                    self._dll.ZCAN_SetDbitBaud(
                        device, self.CHANNEL_INDEX, bitrate
                    ),
                    "Set Model B data bitrate",
                )
                self._check_status(
                    self._dll.ZCAN_SetCANFDStandard(
                        device, self.CHANNEL_INDEX, 0
                    ),
                    "Set Model B ISO CAN-FD standard",
                )

                init_config = ZCAN_CHANNEL_INIT_CONFIG()
                init_config.can_type = TYPE_CANFD
                init_config.canfd.acc_code = 0
                init_config.canfd.acc_mask = 0xFFFFFFFF
                init_config.canfd.abit_timing = 0
                init_config.canfd.dbit_timing = 0
                init_config.canfd.brp = 0
                init_config.canfd.filter = 0
                init_config.canfd.mode = 0
                init_config.canfd.pad = 0
                init_config.canfd.reserved = 0

                channel = self._dll.ZCAN_InitCAN(
                    device, self.CHANNEL_INDEX, ctypes.byref(init_config)
                )
                if not self._valid_handle(channel):
                    raise ModelBError(f"Cannot initialize Model B CAN{self.CHANNEL_INDEX + 1}.")
                self._channel_handle = channel

                filter_mode = 1 if is_extended else 0
                self._check_status(
                    self._dll.ZCAN_ClearFilter(channel),
                    "Clear Model B filters",
                )
                self._check_status(
                    self._dll.ZCAN_SetFilterMode(channel, filter_mode),
                    "Set Model B filter mode",
                )
                self._check_status(
                    self._dll.ZCAN_SetFilterStartID(channel, 0),
                    "Set Model B filter start ID",
                )
                self._check_status(
                    self._dll.ZCAN_SetFilterEndID(channel, filter_end),
                    "Set Model B filter end ID",
                )
                self._check_status(
                    self._dll.ZCAN_AckFilter(channel),
                    "Acknowledge Model B filters",
                )
                self._check_status(
                    self._dll.ZCAN_StartCAN(channel),
                    f"Start Model B CAN{self.CHANNEL_INDEX + 1}",
                )
                self._check_status(
                    self._dll.ZCAN_ClearBuffer(channel),
                    "Clear Model B receive buffer",
                )
            except Exception:
                if self._valid_handle(channel):
                    try:
                        self._dll.ZCAN_ResetCAN(channel)
                    finally:
                        self._channel_handle = None
                self._is_started = False
                raise

            self._active_is_extended = is_extended
            self._is_started = True

        self.start_receive()

    def _send_data_packet(self, packet):
        """Transmit the CAN frame encoded in a Model A data packet."""
        if packet[2] != self.DATA_PACKET or packet[4] != self.DATA_PACKET:
            raise ModelBError("Unsupported compatibility data-packet type.")

        frame_type = packet[3]
        if frame_type not in (self.STANDARD_FRAME, self.EXTENDED_FRAME):
            raise ModelBError(f"Unsupported CAN frame type 0x{frame_type:02X}.")
        is_extended = frame_type == self.EXTENDED_FRAME
        if is_extended != self._active_is_extended:
            raise ModelBError("CAN frame type differs from the active configuration.")

        arbitration_id = int.from_bytes(packet[5:9], byteorder="little")
        max_id = CAN_EFF_MASK if is_extended else CAN_SFF_MASK
        if arbitration_id > max_id:
            raise ModelBError(
                f"CAN ID 0x{arbitration_id:X} exceeds the "
                f"{'extended' if is_extended else 'standard'} limit."
            )

        dlc = int(packet[9])
        if dlc > 8:
            raise ModelBError(f"Classic CAN DLC {dlc} exceeds eight bytes.")

        transmit = ZCAN_TRANSMIT_DATA()
        transmit.frame.can_id = arbitration_id | (CAN_EFF_FLAG if is_extended else 0)
        transmit.frame.can_dlc = dlc
        for index in range(dlc):
            transmit.frame.data[index] = packet[10 + index]
        transmit.transmit_type = 0

        with self._lifecycle_lock:
            self._require_channel()
            sent = self._dll.ZCAN_Transmit(
                self._channel_handle, ctypes.byref(transmit), 1
            )
        if int(sent) != 1:
            raise ModelBError(
                f"ControlCANFD transmitted {int(sent)} of 1 classic CAN frame."
            )

    def _process_packet(self, packet):
        self._validate_packet(packet)
        if packet[2] in (self.FIXED_LENGTH, self.VARIABLE_LENGTH):
            self._configure_from_packet(packet)
        elif packet[2] == self.DATA_PACKET:
            self._send_data_packet(packet)
        else:
            raise ModelBError(f"Unknown compatibility packet type 0x{packet[2]:02X}.")

    def write(self, data):
        """Accept Model A ``AA 55`` packets and execute them.

        Configuration packets (re)initialise and start the CAN channel; data packets
        are transmitted as classic CAN frames. Returns ``len(data)`` on success or
        ``-1`` on error (QSerialPort convention).
        """
        raw = bytes(data)
        if not raw:
            return 0

        packets = []
        malformed_error = None
        with self._tx_buffer_lock:
            self._tx_buffer.extend(raw)
            while True:
                header_index = self._tx_buffer.find(self.HEADER)
                if header_index < 0:
                    if self._tx_buffer[-1:] == self.HEADER[:1]:
                        discarded = len(self._tx_buffer) - 1
                        if discarded:
                            del self._tx_buffer[:discarded]
                    else:
                        discarded = len(self._tx_buffer)
                        self._tx_buffer.clear()
                    if discarded:
                        malformed_error = (
                            f"Discarded {discarded} byte(s) before an AA 55 packet."
                        )
                    break

                if header_index:
                    del self._tx_buffer[:header_index]
                    malformed_error = (
                        f"Discarded {header_index} byte(s) before an AA 55 packet."
                    )

                if len(self._tx_buffer) < self.PACKET_SIZE:
                    break

                candidate = bytes(self._tx_buffer[: self.PACKET_SIZE])
                if candidate[19] != self._checksum(candidate):
                    malformed_error = "Invalid compatibility packet checksum."
                    del self._tx_buffer[0]
                    continue

                packets.append(candidate)
                del self._tx_buffer[: self.PACKET_SIZE]

        if malformed_error:
            self._set_error(malformed_error)
            if not packets:
                return -1

        try:
            for packet in packets:
                self._process_packet(packet)
            self._last_write_ok = True
            return len(raw)
        except Exception as exc:
            self._set_error(exc)
            return -1

    @classmethod
    def _frame_to_packet(cls, frame):
        packet = bytearray(cls.PACKET_SIZE)
        packet[0:2] = cls.HEADER
        packet[2] = cls.DATA_PACKET
        packet[3] = cls.EXTENDED_FRAME if frame.is_extended else cls.STANDARD_FRAME
        packet[4] = cls.DATA_PACKET
        packet[5:9] = int(frame.arbitration_id).to_bytes(4, byteorder="little")
        packet[9] = len(frame.data)
        packet[10 : 10 + len(frame.data)] = frame.data
        packet[18] = 0
        packet[19] = cls._checksum(packet)
        return bytes(packet)

    def _receive_available(self):
        """Read up to ``RECEIVE_BATCH_SIZE`` frames currently queued in the DLL."""
        with self._lifecycle_lock:
            self._require_channel()
            available = int(
                self._dll.ZCAN_GetReceiveNum(self._channel_handle, TYPE_CAN)
            )
            if available == 0:
                return []
            if available == 0xFFFFFFFF:
                raise ModelBError("ControlCANFD failed to query the receive queue.")

            requested = min(available, self.RECEIVE_BATCH_SIZE)
            buffer_type = ZCAN_RECEIVE_DATA * requested
            buffer = buffer_type()
            count = int(
                self._dll.ZCAN_Receive(
                    self._channel_handle, buffer, requested, 10
                )
            )

        if count == 0xFFFFFFFF or count > requested:
            raise ModelBError(
                f"ControlCANFD returned invalid receive count {count}."
            )

        frames = []
        for index in range(count):
            received = buffer[index]
            raw_id = int(received.frame.can_id)
            if raw_id & CAN_ERR_FLAG:
                self._set_error("Dropped a native CAN error frame.")
                continue
            if raw_id & CAN_RTR_FLAG:
                self._set_error("Dropped an unsupported remote CAN frame.")
                continue

            is_extended = bool(raw_id & CAN_EFF_FLAG)
            arbitration_id = raw_id & (CAN_EFF_MASK if is_extended else CAN_SFF_MASK)
            dlc = int(received.frame.can_dlc)
            if dlc > 8:
                self._set_error(f"Dropped classic CAN frame with invalid DLC {dlc}.")
                continue
            frames.append(
                _ReceivedFrame(
                    arbitration_id,
                    bytes(received.frame.data[:dlc]),
                    is_extended,
                    int(received.timestamp),
                )
            )
        return frames

    def _queue_packet(self, packet):
        with self._rx_condition:
            self._rx_packets.append(packet)
            self._rx_condition.notify_all()
        self.readyRead.emit()

    def _check_device_online(self):
        with self._lifecycle_lock:
            if not self._valid_handle(self._device_handle):
                raise ModelBError("The Model B device handle is invalid.")
            result = self._dll.ZCAN_IsDeviceOnLine(self._device_handle)
        self._check_online_status(result, "Receive Model B")

    def _receive_loop(self):
        """Background thread: poll the DLL, queue frames as packets, and report a lost device."""
        last_online_check = 0.0
        try:
            while not self._receive_stop.is_set():
                now = time.monotonic()
                if now - last_online_check >= self.ONLINE_CHECK_SECONDS:
                    last_online_check = now
                    self._check_device_online()

                frames = self._receive_available()
                if frames:
                    for frame in frames:
                        self._queue_packet(self._frame_to_packet(frame))
                else:
                    self._receive_stop.wait(self.RECEIVE_IDLE_SECONDS)
        except Exception as exc:
            if not self._receive_stop.is_set():
                self._is_started = False
                self._is_open = False
                self._set_error(exc)
                with self._rx_condition:
                    self._rx_condition.notify_all()
                self.disconnected.emit(str(exc))

    def start_receive(self):
        """Start the background thread that polls the DLL and queues received packets."""
        if not self._is_started:
            return
        if self._receive_thread is not None and self._receive_thread.is_alive():
            return
        self._receive_stop.clear()
        self._receive_thread = threading.Thread(
            target=self._receive_loop,
            name="WaveshareCANFDBReceive",
            daemon=True,
        )
        self._receive_thread.start()

    def stop_receive(self):
        """Stop and join the receive thread."""
        self._receive_stop.set()
        thread = self._receive_thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=1.0)
        self._receive_thread = None

    def readAll(self):
        """Return the next queued received packet (one per call), or an empty ``QByteArray``."""
        with self._rx_condition:
            if not self._rx_packets:
                return QByteArray()
            packet = self._rx_packets.popleft()
        return QByteArray(packet)

    def bytesAvailable(self):
        """Total bytes queued for :meth:`readAll`."""
        with self._rx_condition:
            return sum(len(packet) for packet in self._rx_packets)

    def waitForReadyRead(self, timeout_ms):
        """Block until a packet is queued or ``timeout_ms`` elapses."""
        timeout = max(0, int(timeout_ms)) / 1000.0
        deadline = time.monotonic() + timeout
        with self._rx_condition:
            while not self._rx_packets:
                if not self._is_open:
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._rx_condition.wait(remaining)
            return True

    def clear(self, direction=None):
        """Drop queued RX/TX bytes and clear the device receive buffer."""
        del direction
        with self._rx_condition:
            self._rx_packets.clear()
        with self._tx_buffer_lock:
            self._tx_buffer.clear()

        if self._is_started and self._valid_handle(self._channel_handle):
            try:
                with self._lifecycle_lock:
                    self._check_status(
                        self._dll.ZCAN_ClearBuffer(self._channel_handle),
                        "Clear Model B receive buffer",
                    )
            except Exception as exc:
                self._set_error(exc)
                return False
        return True

    def close(self):
        """Stop receiving, reset the channel and close the device; errors are reported, not raised."""
        self.stop_receive()
        with self._lifecycle_lock:
            if self._dll is not None and self._valid_handle(self._channel_handle):
                try:
                    self._dll.ZCAN_ResetCAN(self._channel_handle)
                except Exception as exc:
                    self._set_error(f"Reset Model B CAN{self.CHANNEL_INDEX + 1} during close: {exc}")
                finally:
                    self._channel_handle = None

            if self._dll is not None and self._valid_handle(self._device_handle):
                try:
                    result = self._dll.ZCAN_CloseDevice(self._device_handle)
                    if int(result) != STATUS_OK:
                        self._set_error(
                            f"Close Model B device: ControlCANFD returned status {int(result)}."
                        )
                except Exception as exc:
                    self._set_error(f"Close Model B device: {exc}")
                finally:
                    self._device_handle = None

            self._device_info = None
            self._is_started = False
            self._is_open = False

        with self._rx_condition:
            self._rx_packets.clear()
            self._rx_condition.notify_all()
        with self._tx_buffer_lock:
            self._tx_buffer.clear()


class USB_CANFD_B(USB_CAN_A):
    """Drop-in Model B adapter using the active app version's Model A codec."""

    def __init__(self, serial=None, ui=None, parent=None):
        del serial  # Model B does not use the QSerialPort created by the app.
        proxy = _ModelBSerialProxy()

        parameters = inspect.signature(USB_CAN_A.__init__).parameters
        kwargs = {}
        if "serial" in parameters:
            kwargs["serial"] = proxy
        if "ui" in parameters:
            kwargs["ui"] = ui
        if "parent" in parameters:
            kwargs["parent"] = parent

        if kwargs:
            USB_CAN_A.__init__(self, **kwargs)
        else:
            USB_CAN_A.__init__(self, proxy, ui)

        proxy.setParent(self)
        self.serial = proxy
        self.ui = ui
        proxy.modelBError.connect(self._handle_model_b_error)

    @Slot(str)
    def _handle_model_b_error(self, message):
        log_widget = getattr(self.ui, "plainTextEdit_log", None) if self.ui else None
        if log_widget is not None:
            log_widget.appendPlainText(f"> USB-CAN-FD-B: {message}")

    def Send_Config(
        self,
        typeLength="FIXED_LENGTH",
        typeFrame="Extended Frame",
        baudrate="1M",
        filter_id=0,
        mask_id=0,
        modeCAN="Normal",
    ):
        """Legacy VinD API: send a configuration packet; returns the packet or ``None`` on failure."""
        self.serial.begin_operation()
        packet = super().Send_Config(
            typeLength,
            typeFrame,
            baudrate,
            filter_id,
            mask_id,
            modeCAN,
        )
        if packet is None or not self.serial.last_write_ok:
            return None
        return packet

    def Send_Packet(self, frameID: bytes, frameData: bytes, lenDataField=8):
        """Legacy VinD API: send one CAN packet; returns the packet or ``None`` on failure."""
        self.serial.begin_operation()
        packet = super().Send_Packet(frameID, frameData, lenDataField)
        if packet is None or not self.serial.last_write_ok:
            return None
        return packet

    def Send_Data(self, Frame_ID="00000000", data_bytes=None, data_length=8):
        """Compatibility with older VinD versions that use ``Send_Data``."""

        try:
            arbitration_id = int(str(Frame_ID), 16)
        except (TypeError, ValueError):
            self.serial._set_error(f"Invalid CAN ID {Frame_ID!r}.")
            return None
        if arbitration_id < 0 or arbitration_id > CAN_EFF_MASK:
            self.serial._set_error(f"Invalid CAN ID 0x{arbitration_id:X}.")
            return None

        payload = bytes(data_bytes or b"")
        if data_length < 0 or data_length > 8 or len(payload) < data_length:
            self.serial._set_error("Classic CAN payload length must be 0 through 8.")
            return None
        payload = payload[:data_length].ljust(8, b"\x00")
        frame_id = arbitration_id.to_bytes(4, byteorder="big")
        return self.Send_Packet(frame_id, payload, 8)


ModelBSerialProxy = _ModelBSerialProxy


__all__ = [
    "USB_CANFD_B",
    "ModelBSerialProxy",
    "ModelBError",
    "ZCAN_CAN_FRAME",
    "ZCAN_CANFD_FRAME",
    "ZCAN_CHANNEL_INIT_CONFIG",
    "ZCAN_TRANSMIT_DATA",
    "ZCAN_RECEIVE_DATA",
    "ZCAN_TRANSMIT_FD_DATA",
    "ZCAN_RECEIVE_FD_DATA",
    "validate_abi_sizes",
]
