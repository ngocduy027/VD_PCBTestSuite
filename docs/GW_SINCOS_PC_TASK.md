# GongWang Live Sin/Cos PC Task

## Objective

Add PC-app support for polling the firmware for the live sine, cosine, and position values of both GongWang encoder rings.

The firmware performs the UART communication automatically. The PC app only sends one CAN polling request and decodes two CAN responses.

## CAN Polling Request

Use a 29-bit extended CAN data frame.

```text
Function:  0x1C
Opcode:    0x01
Source ID: PC ID
Target ID: Driver ID
DLC:       8
Data:      00 00 00 00 00 00 00 00
```

Request ID construction:

```text
(0x1C << 24) | (0x01 << 16) | (PC_ID << 8) | DRIVER_ID
```

Example for PC ID `0xFE` and driver ID `0x01`:

```text
ID:   0x1C01FE01
Data: 00 00 00 00 00 00 00 00
```

## CAN Responses

One polling request can produce two 8-byte extended CAN responses.

### Motor/Input Ring

```text
Function:  0x1D
Opcode:    0x54
Source ID: Driver ID
Target ID: PC ID
```

Response ID:

```text
(0x1D << 24) | (0x54 << 16) | (DRIVER_ID << 8) | PC_ID
```

Resolution:

```text
131072 counts/revolution (17 bit)
```

### Output/Retarder Ring

```text
Function:  0x1D
Opcode:    0x55
Source ID: Driver ID
Target ID: PC ID
```

Response ID:

```text
(0x1D << 24) | (0x55 << 16) | (DRIVER_ID << 8) | PC_ID
```

Resolution:

```text
524288 counts/revolution (19 bit)
```

The output-ring response normally arrives approximately 15 ms after the motor-ring response.

## Response Payload

Both responses use the same payload format:

| Byte | Field |
|---:|---|
| 0 | `SIN_LO` |
| 1 | `SIN_HI` |
| 2 | `COS_LO` |
| 3 | `COS_HI` |
| 4 | `COUNT_LO` |
| 5 | `COUNT_MID` |
| 6 | `COUNT_HI` |
| 7 | Encoder XOR byte |

The original UART command echo is removed from the payload. It is represented by CAN opcode `0x54` or `0x55`.

## Raw Decoding

Decode sine and cosine as signed little-endian 16-bit values:

```python
sin_raw = int.from_bytes(data[0:2], byteorder="little", signed=True)
cos_raw = int.from_bytes(data[2:4], byteorder="little", signed=True)
count = int.from_bytes(data[4:7], byteorder="little", signed=False)
```

Select the resolution from the response opcode:

```python
resolution = 131072 if opcode == 0x54 else 524288
```

Convert the values for display:

```python
sin_mv = sin_raw * 3657.0 / 8192.0
cos_mv = cos_raw * 3657.0 / 8192.0
amplitude_mv = (sin_mv**2 + cos_mv**2) ** 0.5
angle_deg = count * 360.0 / resolution
```

## Optional XOR Check

The PC can reconstruct the original nine-byte encoder XOR check using the CAN opcode as the removed command byte:

```python
xor_value = opcode
for value in data:
    xor_value ^= value

xor_valid = xor_value == 0
```

The firmware already checks the command echo and XOR. If a UART transaction fails, that response is silently omitted.

## Polling Behavior

1. Send one `0x1C/0x01` polling request.
2. Wait for response opcode `0x54` and response opcode `0x55`.
3. Match source and target IDs to the requested driver and PC IDs.
4. Decode and display each response independently.
5. Do not treat a missing response as a firmware error frame; the firmware intentionally sends no error response.
6. Start the next polling cycle only after both responses arrive or after the PC-side timeout expires.

A starting polling interval of 50 ms or slower is recommended so the previous two-response transaction finishes before the next request.

## Acceptance Checks

- The PC transmits a 29-bit extended request with the correct source and target IDs.
- One request produces a `0x54` response and a `0x55` response.
- Both responses have DLC 8.
- Signed negative sine/cosine values decode correctly.
- Motor count uses 17-bit resolution.
- Output count uses 19-bit resolution.
- The application tolerates either response being absent.
- Repeated polling updates the displayed values without overlapping requests.

## Reference Example

```text
Opcode: 0x54
Data:   95 01 C4 F3 52 75 01 D1
```

Expected decoding:

```text
sin_raw = 405
cos_raw = -3132
count   = 95570
angle   = approximately 262.49 degrees
```
