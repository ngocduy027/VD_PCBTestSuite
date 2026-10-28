from __future__ import annotations

from pcbt_app.transport.waveshare_usbcan_a import build_waveshare_config_packet


BITRATE_PRESETS: tuple[tuple[str, int], ...] = (
    ("1M", 1_000_000),
    ("800K", 800_000),
    ("500K", 500_000),
    ("400K", 400_000),
    ("250K", 250_000),
    ("200K", 200_000),
    ("125K", 125_000),
    ("100K", 100_000),
    ("50K", 50_000),
    ("20K", 20_000),
    ("10K", 10_000),
    ("5K", 5_000),
)

SERIAL_BAUD_PRESETS: tuple[tuple[str, int], ...] = (("2000000", 2_000_000),)
RS485_BAUD_PRESETS: tuple[tuple[str, int], ...] = (("2500000", 2_500_000),)
DETECT_ID_DEFAULT_TIMEOUT_MS = 3_000
OPENLOOP_APP_TIMEOUT_DEFAULT_MS = 15_000
CAN_STRESS_APP_TIMEOUT_DEFAULT_MS = 10_000
GONGWANG_APP_TIMEOUT_DEFAULT_MS = 10_000
RS485_STRESS_DEFAULT_TIMEOUT_MS = 10_000
MAX_TEST_TIMEOUT_MS = 2_000_000_000


def bitrate_value_from_label(label: str) -> int:
    for preset_label, value in BITRATE_PRESETS:
        if preset_label == label:
            return value
    raise ValueError(f"Unsupported bitrate label: {label}")


def serial_baud_value_from_label(label: str) -> int:
    for preset_label, value in SERIAL_BAUD_PRESETS:
        if preset_label == label:
            return value
    raise ValueError(f"Unsupported serial baud label: {label}")


def format_waveshare_config_packet(can_bitrate: int) -> str:
    return build_waveshare_config_packet(can_bitrate).hex().upper()
