# VD_PCBTestSuite_V1.2

Windows desktop application and CLI for running PCB production tests over CAN.

Current scope:

- CAN-ID test
- Open-loop test
- GongWang encoder CAN test
- PC-side PASS/FAIL verdict
- raw frame logging
- Waveshare USB-CAN-A CH340/CH341 serial transport
- Waveshare USB-CAN-FD Model B native DLL transport
- PEAK PCAN and other `python-can` transports

## Python

This project targets `Python 3.14`.

Use the installed launcher-based interpreter:

```powershell
py -3.14 --version
```

## Development setup

```powershell
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
py -3.14 -m pip install -e .[dev]
```

## CLI

```powershell
py -3.14 -m pcbt_app.cli can-test --backend waveshare-usbcan-a --channel COM13 --bitrate 1000000 --serial-baud 2000000
py -3.14 -m pcbt_app.cli openloop-test --backend waveshare-usbcan-a --channel COM13 --bitrate 1000000 --serial-baud 2000000
```

For PEAK PCAN:

```powershell
py -3.14 -m pcbt_app.cli can-test --backend pcan --channel PCAN_USBBUS1 --bitrate 500000
```

## GUI

```powershell
py -3.14 -m pcbt_app.app
```

The GUI follows the production flow:

1. Click `Refresh COM`
2. Choose the detected adapter port, for example `COM13 - USB-SERIAL CH340`
3. Leave serial baudrate at `2000000`
4. Leave CAN baudrate at `1M` unless the PCB-test firmware is configured differently
5. Click `OPEN`

## Select USB-CAN Model A or Model B

One executable supports both models. While disconnected, choose **Adapter**
in **CAN Port**. The app remembers the last selection across restarts.
Model A uses the COM-port workflow. For Model B, **Refresh Devices** scans
available adapters and lists their device index and serial number under **Port**.
Choose a detected adapter and **CAN1** or **CAN2** while disconnected. The app
remembers the preferred device index and channel. OPEN is disabled when no
available adapter is found. Serial baud is unused by the native USB DLL.

The vendor DLL has no enumeration API. Scanning probes indices 0 through 63,
checks online status, reads device information, then closes each handle without
initializing or starting CAN. Devices held open by another application may be
unavailable. Scanning runs in the worker thread while disconnected.
No rebuild is needed to switch models. `USB_CAN_ADAPTER_MODEL` in
`pcbt_app/config.py` only sets the first-run default.

**OPEN** configures and starts either adapter automatically. **Set and Start**
reapplies configuration manually. Model B allows bitrate changes while connected
and idle; its normal mode, extended frames, and full-range filter are fixed for
PCB testing. Close the CAN connection before changing adapters.

Model B requires the x64 `ControlCANFD.dll` in
`pcbt_app/transport/ControlCANFD.dll`; the Windows build bundles this DLL.
The current application protocol remains classic CAN with extended IDs and
payloads of up to eight bytes.
6. Click `Detect ID`
7. Run `Open-loop Test` after ID detection passes

The `GongWang Encoder Test` uses firmware diagnostic test ID `4`. Set its
duration (default `5000 ms`) and start the test after driver-ID detection. The
firmware owns UART7 for the duration of the test, performs the 20 kHz GongWang
request/receive process, validates and decodes responses onboard, and sends one
asynchronous function-`0x1B` completion report. The app then reads the complete
`0x7240..0x7262` result schema one index at a time and presents metadata,
throughput, error/timing diagnostics, and the latest valid position. Function
`0x1D` raw sample streaming is not used.

Waveshare USB-CAN-A is not an SLCAN device for this app. Do not select `slcan` for this adapter.

## Waveshare USB-CAN-A Setup

Install the CH340/CH341 driver first. In Windows Device Manager the adapter should appear as a USB serial device such as `USB-SERIAL CH340 (COM7)` or `USB-SERIAL CH341 (COM7)`.

The app uses Waveshare's variable-length serial-to-CAN protocol:

- USB serial baud: `2000000`
- CAN frame format: Classic CAN, extended ID, 8-byte payload
- CAN-side bitrate: default `1000000`
- CAN config: extended frame, filter ID `00000000`, mask ID `00000000`

## Packaging

```powershell
.\build_windows.ps1
```

The GUI exposes an open-loop result timeout, not a board-side duration control. The current PCB-test command payload reserves bytes `1..7`, so the PC app does not attempt to configure firmware duration.
