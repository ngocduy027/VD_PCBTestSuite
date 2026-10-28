# FCT RS485 PC App Requirements

Date: 2026-08-28

## 1. Purpose

The PC application controls the FCT RS485 test through CAN and receives the generated test messages through a USB-to-RS485 adapter.

The RS485 data path is one-way:

```text
Firmware -> RS485 transceiver -> USB-to-RS485 adapter -> PC application
```

The PC application must not transmit test messages back to the firmware over RS485.

## 2. Interfaces

The test uses two independent PC connections:

- CAN carries FCT START, STATUS, STOP, and the final firmware result.
- USB-to-RS485 receives the generated RS485 frames.

The serial receiver must remain active while the application sends CAN commands and polls CAN status.

## 3. Serial configuration

Configure the USB-to-RS485 adapter as follows:

| Setting | Value |
|---|---:|
| Baud rate | 2,500,000 baud |
| Data bits | 8 |
| Parity | None |
| Stop bits | 1 |
| Flow control | None |

Use a dedicated thread or asynchronous reader so CAN polling cannot delay serial reception. At `period_ticks = 1`, the application must sustain 20,000 frames per second, or 200,000 serial data bytes per second.

## 4. FCT START request

Before START:

1. Open and configure the serial port.
2. Clear stale serial input.
3. Clear the parser buffer and all test counters.
4. Set the expected sequence to `0x000000`.
5. Start continuous serial collection.
6. Send FCT START over CAN.

The existing FCT START payload is:

```text
Byte 0:     0x03                 FCT_TEST_RS485
Byte 1:     period_ticks
Bytes 2-4:  duration_ms, unsigned little-endian 24-bit
Bytes 5-7:  0x00 0x00 0x00      parameter override must be zero
```

Valid `period_ticks` is `1..255`. The requested transmission frequency is:

```text
frames_per_second = 20,000 / period_ticks
```

The requested duration must satisfy:

```text
duration_ms <= floor(0xFFFFF * period_ticks / 20)
```

At `period_ticks = 1`, the maximum accepted duration is 52,428 ms.

## 5. RS485 frame format

Every RS485 frame is exactly 10 bytes:

```text
Byte 0:     0x9A
Bytes 1-3:  sequence number, unsigned little-endian 24-bit
Bytes 4-8:  0xAA 0xAA 0xAA 0xAA 0xAA
Byte 9:     XOR of bytes 0-8
```

Example frames:

```text
Sequence 0x000000: 9A 00 00 00 AA AA AA AA AA 30
Sequence 0x000001: 9A 01 00 00 AA AA AA AA AA 31
Sequence 0x123456: 9A 56 34 12 AA AA AA AA AA 40
```

The first expected sequence is `0x000000`. Every subsequent structurally valid frame must contain the previous sequence plus one.

## 6. Streaming parser

The parser must support:

- A frame split across multiple serial reads.
- Multiple frames in one serial read.
- Leading or inter-frame garbage.
- Resynchronization after an invalid candidate.
- Continuous high-rate input without blocking CAN operations.

Use the following parser behavior:

1. Append newly received data to a persistent byte buffer.
2. Search for the next `0x9A` header.
3. If bytes precede the header, discard them and increment `malformed_count`.
4. If fewer than 10 bytes remain, wait for the next serial read.
5. Check that bytes 4 through 8 are all `0xAA`.
6. Calculate the XOR of bytes 0 through 8 and compare it with byte 9.
7. If the candidate is invalid, increment `malformed_count`, remove only its first byte, and search again.
8. If the candidate is structurally valid, remove all 10 bytes, increment `valid_frame_count`, and validate its sequence.

For a structurally valid frame:

```text
received_sequence = byte1 | (byte2 << 8) | (byte3 << 16)
```

If `received_sequence` does not equal `expected_sequence`:

- Increment `sequence_error_count`.
- Record the expected and received values for diagnostics.
- Continue collecting remaining frames because the final verdict is already FAIL.

After processing a structurally valid frame, advance the expected sequence using the received value so the parser can continue diagnosing the remaining stream:

```text
expected_sequence = received_sequence + 1
```

Valid test duration prevents 24-bit sequence wrap.

## 7. CAN status handling

Poll FCT STATUS over CAN while serial collection remains active.

The status response provides:

- Firmware state.
- `pass_count`: number of completed UART transmissions.
- `fail_count`: number of local busy, DMA-start, or UART transmission failures.

When firmware reaches `PASS`, `FAIL`, or `ABORTED`:

1. Keep the serial receiver active for a short bounded drain interval. Use 100 ms initially.
2. Stop serial collection after the drain interval.
3. Process all remaining received bytes.
4. Treat any incomplete bytes left in the parser buffer as malformed data.
5. Compare the PC counters with the final firmware counters.

If the PC deadline expires before firmware becomes terminal:

1. Send FCT STOP over CAN.
2. Keep receiving while firmware drains any in-flight frame.
3. Poll STATUS until firmware reports `ABORTED` or another terminal state.
4. Perform the same bounded serial drain and final comparison.

## 8. Strict verdict

The PC application reports PASS only when all conditions are true:

```text
firmware state == PASS
firmware fail_count == 0
malformed_count == 0
sequence_error_count == 0
valid_frame_count == firmware pass_count
valid_frame_count > 0
parser buffer is empty after the final drain
```

Any of the following produces FAIL:

- Duplicate sequence.
- Missing sequence.
- Out-of-order sequence.
- Unexpected command byte.
- Unexpected fixed-pattern byte.
- Invalid XOR checksum.
- Garbage or malformed serial data during the test.
- Additional received frame not included in the firmware count.
- PC and firmware message-count mismatch.
- Firmware local send failure.
- Firmware terminal state other than PASS.

## 9. Result data

Display and save at least the following values:

- Requested duration in milliseconds.
- `period_ticks`.
- Requested transmission frequency.
- Firmware terminal state.
- Firmware completed-message count.
- Firmware local send-failure count.
- PC structurally valid frame count.
- PC malformed-data count.
- PC sequence-error count.
- First sequence received.
- Last sequence received.
- Final PASS or FAIL verdict.
- Failure reason.

## 10. Required PC tests

Add automated tests for:

- One complete valid frame.
- A frame divided across several serial reads.
- Several frames in one serial read.
- Leading and inter-frame garbage.
- Incorrect command byte.
- Incorrect fixed pattern.
- Incorrect XOR checksum.
- Duplicate sequence.
- Missing sequence.
- Out-of-order sequence.
- Additional frame after the expected stream.
- Incomplete final frame.
- PC and firmware count mismatch.
- Firmware send failure.
- Firmware FAIL and ABORTED states.
- Serial drain after firmware becomes terminal.
- PC timeout followed by FCT STOP and terminal-status reconciliation.
