# Live Sine/Cosine Reading

EncoderExplorer reads the instantaneous sine and cosine signals from each
encoder ring with one-byte requests at 2,500,000 baud, 8-N-1 UART:

| Ring | Request | Resolution in `Calib4` |
|---|---:|---:|
| Motor/input ring | `54` | 131,072 counts/revolution (17 bit) |
| Retarder/output ring | `55` | 524,288 counts/revolution (19 bit) |

The request is only the command byte; it has no request checksum. Each
response is nine bytes:

```text
CMD SIN_LO SIN_HI COS_LO COS_HI COUNT_LO COUNT_MID COUNT_HI XOR
```

Validate that `RX[0]` equals the requested command and that XOR of all nine
bytes is zero. Decode the signal values as signed little-endian 16-bit
integers (two's complement):

```c
int16_t sin_raw = (int16_t)((uint16_t)rx[1] | ((uint16_t)rx[2] << 8));
int16_t cos_raw = (int16_t)((uint16_t)rx[3] | ((uint16_t)rx[4] << 8));
uint32_t count = (uint32_t)rx[5]
              | ((uint32_t)rx[6] << 8)
              | ((uint32_t)rx[7] << 16);
```

Convert raw signal codes to millivolts using the application scale:

```text
sin_mV = sin_raw * 3657 / 8192
cos_mV = cos_raw * 3657 / 8192
amplitude_mV = sqrt(sin_mV² + cos_mV²)
angle_deg = count * 360 / ring_resolution
```

Example captured motor-ring response:

```text
RX: 54 95 01 C4 F3 52 75 01 D1
sin_raw = 0x0195 = 405       sin ≈ +180.80 mV
cos_raw = 0xF3C4 = -3132     cos ≈ -1398.07 mV
count   = 0x017552 = 95570   angle ≈ 262.49°
```

The original implementation is in `EncoderExplorer/MainForm.cs` methods
`EncoderRead_0x54` and `EncoderRead_0x55`. The `0x56` command is a secondary
resolution read in dual-encoder mode, not the live second-ring waveform read.
