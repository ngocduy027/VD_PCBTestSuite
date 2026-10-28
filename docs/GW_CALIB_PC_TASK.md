# GongWang Encoder Calibration PC Task

## Objective

Add PC-app support for starting the firmware-owned GongWang encoder calibration, displaying live progress, reassembling the final raw encoder records, decoding those records, and showing the final result.

The PC does not control OpenLoop, poll the encoder, restart OpenLoop, or request the final records. After one CAN START command, the firmware owns the complete calibration sequence and pushes progress and the terminal result to the PC.

This document describes the current implementation in `Core/Src/main.c` and `lib/calib/gw_calib.c/.h`. It replaces the older PC workflow in `GW_CALIBRATION_PLAN.md`.

## PC Responsibilities

1. Prevent calibration from overlapping any other UART7 operation:
   - GongWang FCT encoder test.
   - RS485 FCT test.
   - Live GongWang Sin/Cos reading.
2. Send one calibration START request.
3. Receive and validate the immediate START acknowledgement.
4. Receive raw START/progress responses and display the current calibration step.
5. Receive and reassemble final result fragments.
6. Decode the raw records only after the complete record has arrived.
7. Display PASS, encoder failure, UART failure, or OpenLoop failure.
8. Re-enable the UART7-related controls only after the final result is complete or START is rejected.

The PC must not send another calibration START while one calibration or final-result transfer is active.

## CAN Identifier Layout

All calibration messages use 29-bit extended Classic CAN frames with DLC 8.

```text
Bits 28..24: Function
Bits 23..16: Opcode
Bits 15..8:  Source ID
Bits 7..0:   Target ID
```

Do not hard-code one PC ID. Use the PC/host ID configured by the application and accept responses whose target byte matches that ID.

## Start Calibration

### Request

```text
Function:  0x1E
Opcode:    0x10
Source ID: PC ID
Target ID: Driver ID
DLC:       8
Data:      00 00 00 00 00 00 00 00
```

Request identifier:

```text
(0x1E << 24) | (0x10 << 16) | (PC_ID << 8) | DRIVER_ID
```

The request payload is unused and should be zero-filled.

### Immediate acknowledgement

```text
Function:  0x1F
Opcode:    0x10
Source ID: Driver ID
Target ID: PC ID
DLC:       8
```

| Byte | Meaning |
|---:|---|
| 0 | Start operation result |
| 1 | Current firmware calibration state |
| 2-7 | Zero |

Start operation result:

| Value | Meaning |
|---:|---|
| `0x00` | Accepted |
| `0x01` | Busy |
| `0x02` | Calibration library not initialized |

On an accepted START, state byte 1 is normally `0x01` (`REQUEST`). The acknowledgement means that the firmware accepted the command; it does not mean that the encoder accepted calibration or that calibration passed.

If START is rejected, show the reason, return the PC workflow to idle, and re-enable the UART7-related controls.

## Raw Start and Progress Responses

The firmware sends these messages automatically. The PC does not send a progress/status polling command.

```text
Function:  0x1F
Opcode:    0x12
Source ID: Driver ID
Target ID: PC ID
DLC:       8
```

| Byte | Raw encoder field |
|---:|---|
| 0 | Command echo, expected `0x76` |
| 1 | Encoder request: `0x00` START or `0x01` PROGRESS |
| 2 | STEP/reserved byte |
| 3 | Encoder status |
| 4 | XOR byte |
| 5-7 | CAN padding, ignore |

Validate the raw five-byte response:

```python
raw = data[0:5]
xor_valid = raw[0] ^ raw[1] ^ raw[2] ^ raw[3] ^ raw[4] == 0
```

Also require:

```text
raw[0] == 0x76
raw[1] is 0x00 or 0x01
```

### START response

For `raw[1] == 0x00`, display that the encoder START response was received. `raw[3] == 0` means the encoder accepted calibration. A nonzero status causes the firmware to stop and produce a final encoder-failure record; the PC should wait for opcode `0x13` rather than ending immediately.

### PROGRESS response

For `raw[1] == 0x01`, decode `STEP = raw[2]`:

| STEP | Meaning | PC display state |
|---:|---|---|
| `< 0x11` | Calibration running | Show current step |
| `0x11` | Encoder reports success | Show `Finishing`; wait for final result |
| `> 0x11` | Encoder reports failure | Show `Reading error`; wait for final result |

Progress normally arrives approximately every 500 ms. Missing one progress frame is not a terminal failure because the next frame contains the latest step.

## Final Result Frames

The firmware pushes final frames automatically using:

```text
Function:  0x1F
Opcode:    0x13
Source ID: Driver ID
Target ID: PC ID
DLC:       8
```

Payload:

| Byte | Meaning |
|---:|---|
| 0 | Firmware outcome |
| 1 | Record type |
| 2 | Fragment index |
| 3-7 | Five raw encoder bytes |

Firmware outcomes:

| Value | Meaning |
|---:|---|
| `0x01` | PASS |
| `0x02` | Encoder-reported failure |
| `0x03` | UART transmit, receive, echo, or XOR failure |
| `0x04` | OpenLoop could not start |

Record types:

| Value | Meaning | Raw length | Expected fragments |
|---:|---|---:|---:|
| `0x00` | No raw encoder record | 0 | 1 status frame |
| `0x01` | Motor-side result | 22 | 5 |
| `0x02` | Output-side result | 22 | 5 |
| `0x03` | Encoder error response | 9 | 2 |

## Fragment Reassembly

For each record, require fragment indexes starting at zero and increasing without gaps.

Each fragment contributes bytes 3 through 7:

```python
record.extend(data[3:8])
```

Trim the assembled buffer to the known raw record length:

```python
motor_raw = motor_buffer[:22]
output_raw = output_buffer[:22]
error_raw = error_buffer[:9]
```

The unused bytes in the last fragment are zero padding and are not part of the encoder response.

Expected terminal sequences:

### PASS

```text
Outcome 0x01, record 0x01, fragments 0..4
Outcome 0x01, record 0x02, fragments 0..4
```

Do not display final PASS until both complete 22-byte records have passed raw validation.

### Encoder failure

```text
Outcome 0x02, record 0x03, fragments 0..1
```

Do not display the decoded encoder error until the complete nine-byte record has passed raw validation.

### UART or OpenLoop failure

```text
Outcome 0x03 or 0x04
Record  0x00
Fragment 0
```

There is no raw encoder record for these failures. Display the firmware outcome directly.

If a required final fragment is missing or out of order, report `Incomplete calibration result`. Do not decode a partially assembled record, and do not automatically send another START.

## Raw Result Validation

For every reconstructed record:

```python
xor_valid = 0
for value in raw_record:
    xor_valid ^= value
xor_valid = xor_valid == 0
```

Successful result records must also have:

```text
Motor:  raw[0] == 0x76 and raw[1] == 0x02 and length == 22
Output: raw[0] == 0x76 and raw[1] == 0x04 and length == 22
```

The error record must have:

```text
raw[0] == 0x76 and raw[1] == 0x0A and length == 9
```

The firmware already validates these fields, but the PC should validate the reassembled CAN data before decoding or displaying it.

## Successful Result Decoding

Both the motor-side and output-side 22-byte records use the same layout:

| Bytes | Type | Field |
|---:|---|---|
| 0 | `uint8` | Command `0x76` |
| 1 | `uint8` | Request `0x02` motor or `0x04` output |
| 2-3 | `int16 LE` | Master sin amplitude |
| 4-5 | `int16 LE` | Master sin center |
| 6-7 | `int16 LE` | Master cos amplitude |
| 8-9 | `int16 LE` | Master cos center |
| 10-11 | `int16 LE` | Slave sin amplitude |
| 12-13 | `int16 LE` | Slave sin center |
| 14-15 | `int16 LE` | Slave cos amplitude |
| 16-17 | `int16 LE` | Slave cos center |
| 18-19 | `int16 LE` | Relative positional deviation |
| 20 | `uint8` | Encoder status |
| 21 | `uint8` | XOR byte |

Python decoding example:

```python
def le_i16(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset:offset + 2], "little", signed=True)

decoded = {
    "master_sin_amplitude": le_i16(raw, 2),
    "master_sin_center": le_i16(raw, 4),
    "master_cos_amplitude": le_i16(raw, 6),
    "master_cos_center": le_i16(raw, 8),
    "slave_sin_amplitude": le_i16(raw, 10),
    "slave_sin_center": le_i16(raw, 12),
    "slave_cos_amplitude": le_i16(raw, 14),
    "slave_cos_center": le_i16(raw, 16),
    "relative_deviation": le_i16(raw, 18),
    "status": raw[20],
}
```

## Display Calculations

Convert amplitude and center values to volts:

```python
amplitude_v = amplitude_raw * 0.5 * 3.657 / 8192.0
center_v = center_raw * 3.657 / 8192.0
```

Calculate the smaller-to-larger amplitude ratio for each Sin/Cos pair:

```python
larger = max(abs(sin_amplitude), abs(cos_amplitude))
sin_cos_percent = 0.0 if larger == 0 else (
    min(abs(sin_amplitude), abs(cos_amplitude)) / larger * 100.0
)
```

Convert relative positional deviation:

```python
motor_deviation_deg = motor_deviation_raw * 360.0 / 131072.0
output_deviation_deg = output_deviation_raw * 360.0 / 524288.0
```

Use 17-bit resolution for the motor-side record and 19-bit resolution for the output-side record.

## Encoder Error Decoding

The nine-byte error record is:

| Bytes | Type | Field |
|---:|---|---|
| 0 | `uint8` | Command `0x76` |
| 1 | `uint8` | Request `0x0A` |
| 2 | `uint8` | Motor-side error code |
| 3-4 | `uint16 LE` | Motor-side error value |
| 5 | `uint8` | Output-side error code |
| 6-7 | `uint16 LE` | Output-side error value |
| 8 | `uint8` | XOR byte |

```python
motor_error_code = raw[2]
motor_error_value = int.from_bytes(raw[3:5], "little", signed=False)
output_error_code = raw[5]
output_error_value = int.from_bytes(raw[6:8], "little", signed=False)
```

Known error codes:

| Code | Meaning |
|---:|---|
| `0x00` | No error |
| `0xE1` | Calibration distance too narrow |
| `0xE2` | Calibration distance too wide |
| `0xE3` | Sin/Cos amplitude ratio too low |
| `0xE4` | Relative offset too high |
| `0xE5` | Direction mismatch |
| `0xE6` | Direction not locked |
| `0xE7` | Encoder calibration timeout |

Display both motor-side and output-side errors because either side may contain the useful failure information.

## PC Workflow State

A minimal PC workflow is:

```text
IDLE
  -> STARTING after sending opcode 0x10
  -> RUNNING after accepted START/raw encoder response
  -> FINISHING after STEP reaches or passes 0x11
  -> PASS after both result records are complete and valid
  -> FAIL after a complete error record or internal firmware failure
```

While the workflow is active:

- Disable the calibration START button.
- Disable every other UART7 test control.
- Continue receiving CAN frames without sending progress/status requests.
- Keep the latest STEP and encoder status visible.
- Do not impose a normal 120-second completion deadline; the firmware automatically starts another 120-second OpenLoop run when calibration is unfinished.

The firmware has no calibration STOP command. Closing the UI or losing the PC connection does not stop firmware calibration. The PC should make this clear if it offers a local `Cancel display` action.

## Acceptance Checks

- START uses extended ID function `0x1E`, opcode `0x10`, correct source/target IDs, DLC 8, and eight zero bytes.
- The immediate opcode `0x10` acknowledgement is decoded correctly.
- Raw opcode `0x12` responses pass command, request, and XOR validation.
- STEP values below, equal to, and above `0x11` produce the correct UI state.
- PASS reassembles motor fragments `0..4` followed by output fragments `0..4`.
- Encoder failure reassembles error fragments `0..1`.
- The last-fragment CAN padding is excluded from raw XOR validation.
- Signed negative `int16` result values decode correctly.
- Motor and output deviation use 17-bit and 19-bit resolutions respectively.
- UART and OpenLoop failures display without waiting for a raw encoder record.
- Missing or out-of-order final fragments never produce a false PASS.
- UART7-related test controls remain disabled until the terminal result is complete.

