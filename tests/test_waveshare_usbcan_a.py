import random

import pytest

from pcbt_app.models import RawCanFrame
from pcbt_app.protocol.mit_firmware import build_scan_id_frame
from pcbt_app.transport.base import CanTransportError
from pcbt_app.transport.waveshare_usbcan_a import (
    WaveshareStreamParser,
    build_waveshare_config_packet,
    encode_waveshare_can_frame,
)


def waveshare_packet(arbitration_id: int, data: bytes) -> bytes:
    packet = bytearray(20)
    packet[0] = 0xAA
    packet[1] = 0x55
    packet[2] = 0x01
    packet[3] = 0x02
    packet[4] = 0x01
    packet[5:9] = arbitration_id.to_bytes(4, "little")
    packet[9] = len(data)
    packet[10 : 10 + len(data)] = data
    packet[19] = sum(packet[2:19]) & 0xFF
    return bytes(packet)


def variable_waveshare_packet(arbitration_id: int, data: bytes, *, end: int = 0x55) -> bytes:
    frame_type = 0xC0 | 0x20 | len(data)
    return b"\xAA" + bytes([frame_type]) + arbitration_id.to_bytes(4, "little") + data + bytes([end])


def test_config_packet_500k_checksum() -> None:
    packet = build_waveshare_config_packet(500_000)
    assert packet == bytes.fromhex("AA 55 02 03 02 00 00 00 00 00 00 00 00 00 01 00 00 00 00 08")


def test_config_packet_1m_checksum() -> None:
    packet = build_waveshare_config_packet(1_000_000)
    assert packet == bytes.fromhex("AA 55 02 01 02 00 00 00 00 00 00 00 00 00 01 00 00 00 00 06")


def test_config_packet_keeps_zero_filter_and_mask() -> None:
    packet = build_waveshare_config_packet(1_000_000)
    assert packet[5:9] == b"\x00\x00\x00\x00"
    assert packet[9:13] == b"\x00\x00\x00\x00"


def test_unsupported_bitrate_rejected() -> None:
    with pytest.raises(CanTransportError, match="Unsupported CAN bitrate"):
        build_waveshare_config_packet(333_000)


def test_encode_scan_id_command() -> None:
    frame = build_scan_id_frame(52)
    packet = encode_waveshare_can_frame(frame)
    assert packet == bytes.fromhex("AA 55 01 02 01 34 00 00 00 08 00 00 00 00 00 00 00 00 00 40")


def test_decode_motor_id_response() -> None:
    parser = WaveshareStreamParser()
    frames = parser.feed(waveshare_packet(0x000034FE, b"\x01\x02\x03\x04\x05\x06\x07\x08"))
    assert frames == [
        RawCanFrame(
            arbitration_id=0x000034FE,
            data=b"\x01\x02\x03\x04\x05\x06\x07\x08",
            is_extended_id=True,
        )
    ]


def test_decode_short_motor_id_response() -> None:
    parser = WaveshareStreamParser()
    frames = parser.feed(waveshare_packet(0x000034FE, b"\x00"))
    assert frames == [
        RawCanFrame(
            arbitration_id=0x000034FE,
            data=b"\x00",
            is_extended_id=True,
        )
    ]


def test_decode_result_response_across_partial_chunks() -> None:
    parser = WaveshareStreamParser()
    packet = waveshare_packet(0x110034FD, bytes([0, 0, 0, 0, 4, 0, 0, 0]))
    assert parser.feed(packet[:5]) == []
    frames = parser.feed(packet[5:])
    assert len(frames) == 1
    assert frames[0].arbitration_id == 0x110034FD
    assert frames[0].data == bytes([0, 0, 0, 0, 4, 0, 0, 0])


def test_decode_multiple_frames_in_one_read() -> None:
    parser = WaveshareStreamParser()
    packet = (
        waveshare_packet(0x000034FE, b"\x00" * 8)
        + waveshare_packet(0x110034FD, bytes([0, 0, 0, 0, 4, 0, 0, 0]))
    )
    frames = parser.feed(packet)
    assert [frame.arbitration_id for frame in frames] == [0x000034FE, 0x110034FD]


def test_parser_resyncs_after_noise() -> None:
    parser = WaveshareStreamParser()
    frames = parser.feed(b"\x00\x01\x02" + waveshare_packet(0x000034FE, b"\x00" * 8))
    assert len(frames) == 1
    assert frames[0].arbitration_id == 0x000034FE


def test_parser_recovers_after_bad_checksum_without_losing_next_frame() -> None:
    parser = WaveshareStreamParser()
    bad_packet = bytearray(waveshare_packet(0x000034FE, b"\x01\x02"))
    bad_packet[19] ^= 0x01
    good_packet = waveshare_packet(0x000034FD, b"\x03\x04")

    frames = parser.feed(bytes(bad_packet) + good_packet)

    assert [frame.arbitration_id for frame in frames] == [0x000034FD]
    stats = parser.take_stats()
    assert stats.checksum_errors == 1
    assert stats.decoded_frames == 1
    assert stats.malformed_candidates >= 1


def test_parser_recovers_after_invalid_fixed_packet_dlc() -> None:
    parser = WaveshareStreamParser()
    bad_packet = bytearray(waveshare_packet(0x000034FE, b"\x01"))
    bad_packet[9] = 9
    bad_packet[19] = sum(bad_packet[2:19]) & 0xFF
    good_packet = waveshare_packet(0x000034FD, b"\x02")

    frames = parser.feed(bytes(bad_packet) + good_packet)

    assert [frame.arbitration_id for frame in frames] == [0x000034FD]
    stats = parser.take_stats()
    assert stats.invalid_dlc_errors >= 1
    assert stats.decoded_frames == 1


def test_parser_recovers_after_bad_variable_frame_terminator() -> None:
    parser = WaveshareStreamParser()
    bad_packet = variable_waveshare_packet(0x123456, b"\x01\x02", end=0x00)
    good_packet = variable_waveshare_packet(0x123457, b"\x03\x04")

    frames = parser.feed(bad_packet + good_packet)

    assert [frame.arbitration_id for frame in frames] == [0x123457]
    stats = parser.take_stats()
    assert stats.framing_errors >= 1
    assert stats.decoded_frames == 1


def test_parser_drops_repeated_noise_until_a_valid_frame_arrives() -> None:
    parser = WaveshareStreamParser()

    for _ in range(8):
        assert parser.feed(b"\x01\x02") == []

    frames = parser.feed(waveshare_packet(0x000034FE, b"\x00"))

    assert len(frames) == 1
    stats = parser.take_stats()
    assert stats.malformed_candidates == 8
    assert stats.discarded_bytes == 16
    assert stats.max_consecutive_malformed == 8
    assert stats.summary() is not None
    assert parser.take_stats().malformed_candidates == 0


def test_parser_reset_discards_partial_stream_and_statistics() -> None:
    parser = WaveshareStreamParser()
    packet = waveshare_packet(0x000034FE, b"\x01")
    parser.feed(b"\x01\x02" + packet[:7])

    parser.reset()

    stats = parser.take_stats()
    assert stats == stats.__class__()
    assert parser.feed(packet) == [
        RawCanFrame(arbitration_id=0x000034FE, data=b"\x01", is_extended_id=True)
    ]


def test_parser_handles_arbitrary_serial_chunk_boundaries() -> None:
    parser = WaveshareStreamParser()
    packets = [waveshare_packet(0x00003400 + index, bytes([index & 0xFF])) for index in range(64)]
    stream = b"".join(packets)
    rng = random.Random(42)
    frames: list[RawCanFrame] = []
    position = 0

    while position < len(stream):
        chunk_length = min(rng.randint(1, 31), len(stream) - position)
        frames.extend(parser.feed(stream[position : position + chunk_length]))
        position += chunk_length

    assert [frame.arbitration_id for frame in frames] == [0x00003400 + index for index in range(64)]
    assert parser.take_stats().malformed_candidates == 0
