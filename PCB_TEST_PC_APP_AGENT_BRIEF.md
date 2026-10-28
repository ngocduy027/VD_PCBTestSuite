# PCB Test PC App Standalone Brief

> **Historical document.** This is the original brief for the first version of
> the app. Its `0x1B` command / `0x1C` result protocol is no longer
> implemented; the app now uses the FCT and GongWang protocols described in
> `docs/HANDOVER.md`. Keep this file for background only.

## Purpose

Build a Windows PC application for PCB production and bench testing of a motor driver board over CAN.

The app must:

- trigger board self-tests over CAN
- collect and parse board responses
- decide pass/fail at the PC side
- show results clearly to an operator
- log enough information for debugging and traceability

This document is intentionally standalone. It assumes no prior knowledge of any firmware repository or existing source tree.

---

## System Overview

There are two components in the system:

1. A motor driver board running a dedicated PCB-test firmware
2. A Windows PC test application connected through a CAN adapter

The board firmware supports a limited test mode intended for fast hardware screening. The PC app is the operator-facing tool and the final judge of test success.

The firmware can:

- wait for test commands over CAN
- run an open-loop electrical motor test
- send back the configured motor/driver ID
- send a dedicated test result frame with diagnostics

The PC app must:

- connect to a CAN adapter
- send the correct command frames
- wait for and parse the reply frames
- determine whether the test truly passed end-to-end
- present the result simply and reliably

---

## What The App Needs To Support

The first version only needs to support two board tests:

1. CAN-ID test
2. Open-loop test

### CAN-ID test

Purpose:

- prove that the PC can send a command to the board
- prove that the board can receive the command
- prove that the board can transmit a reply back to the PC
- retrieve the actual driver ID currently stored on the board

Expected board behavior:

- board receives a dedicated CAN test command
- board sends a motor-ID response frame
- board sends a test-result frame for the CAN test

### Open-loop test

Purpose:

- quickly screen for obvious electrical hardware faults on the power stage
- run a short open-loop drive routine without requiring an encoder

Expected board behavior:

- board receives a dedicated open-loop test command
- board runs open-loop drive for a configured duration
- board stops automatically
- board sends a test-result frame

Important limitation:

- this test does not prove mechanical rotation
- it only proves that the board completed the configured electrical routine without the fault conditions defined below

---

## PC App Is The Final Source Of Truth

The board firmware can report whether it believes it queued or executed a test, but the PC app must be the final authority for PASS/FAIL.

Reason:

- firmware can know whether it accepted a command
- firmware can know whether it attempted to queue a transmit frame
- firmware cannot by itself prove that the PC actually received the frame
- firmware cannot distinguish all physical-bus failure cases, such as:
  - bad CAN transceiver
  - broken cable
  - missing termination
  - wrong bitrate
  - defective PC adapter path

Therefore:

- CAN test PASS must only be decided by the PC app after it receives the expected reply frames
- open-loop test PASS must only be decided by the PC app after it receives the expected result frame

---

## CAN Protocol Contract

This section defines the protocol the PC app must implement.

### CAN frame type

- Extended CAN ID
- Classic CAN
- 8-byte payload

### Command function code

The top byte of the extended arbitration ID is used as the function code.

PCB test command function code:

- `0x1B`

PCB test result function code:

- `0x1C`

Motor-ID response function code:

- `0x00`

### Command frame format

Any extended CAN ID whose top byte is `0x1B` is treated by the board as a PCB-test command.

Safe default command ID for the PC app:

- `0x1B000000`

Command payload:

- byte `0` = command
- bytes `1..7` = reserved, set to `0`

Commands:

- `0x01` = start open-loop test
- `0x02` = run CAN-ID test

Recommended payloads:

- open-loop command: `01 00 00 00 00 00 00 00`
- CAN-ID command: `02 00 00 00 00 00 00 00`

### PCB test result frame format

The board sends a dedicated result frame with:

- top byte of extended ID = `0x1C`
- bits `15:8` of ID = actual driver ID
- low byte of ID = `0xFE`

Result payload:

- byte `0`: test ID
- byte `1`: result enum
- byte `2`: failure mask
- byte `3`: actual returned driver ID
- byte `4`: fault status register 1 low byte
- byte `5`: fault status register 1 high byte
- byte `6`: fault status register 2 low byte
- byte `7`: fault status register 2 high byte

Test IDs:

- `1` = open-loop test
- `2` = CAN test

Result enum:

- `0` = not run
- `1` = running
- `2` = pass
- `3` = fail

Failure mask bits:

- `0x01` = invalid duration
- `0x02` = bus voltage low
- `0x04` = overcurrent
- `0x08` = gate-driver fault
- `0x10` = CAN transmit queue failure on board side

### Motor-ID response frame format

The board also sends a normal motor-ID response frame during the CAN-ID test.

This frame is identified by:

- top byte of extended ID = `0x00`
- bits `15:8` of ID = returned driver ID
- low byte of ID = `0xFE`

Important:

- the returned driver ID is encoded in the arbitration ID, not in a dedicated payload byte
- the payload is a generic 8-byte board response payload and should be treated as opaque unless a later requirement says otherwise

For the PCB-test app, the main value of this frame is:

- proving that the board transmitted a valid reply
- extracting the current driver ID from the arbitration ID

---

## Arbitration ID Parsing Rules

Parse the extended CAN ID as four logical bytes:

- bits `31:24`: function code
- bits `23:16`: state or auxiliary field
- bits `15:8`: driver ID
- bits `7:0`: host or destination byte

The parser should expose:

- `function_code = (id >> 24) & 0xFF`
- `state_byte = (id >> 16) & 0xFF`
- `driver_id = (id >> 8) & 0xFF`
- `host_id = id & 0xFF`

For this app:

- PCB-test command function code = `0x1B`
- PCB-test result function code = `0x1C`
- motor-ID response function code = `0x00`
- expected host byte in returned frames = `0xFE`

---

## Required PC-Side Pass/Fail Logic

### CAN test PASS rule

Mark the CAN test PASS only if all are true:

1. the PC app successfully sent the CAN-ID test command
2. a motor-ID response frame was received before timeout
3. that frame has function code `0x00`
4. that frame has host byte `0xFE`
5. a PCB-test result frame was received before timeout
6. that result frame has function code `0x1C`
7. result payload byte `0` is `2` for CAN test
8. result payload byte `1` is `2` for PASS
9. the driver ID extracted from the motor-ID response matches byte `3` of the test-result payload

Recommended default timeout:

- `500 ms`

Make the timeout configurable.

### CAN test FAIL cases

At minimum detect these:

- timeout waiting for any reply
- received result frame but no motor-ID response
- received motor-ID response but no result frame
- wrong function code
- wrong test ID
- result payload says FAIL
- inconsistent returned driver ID
- CAN adapter error or bus error

### Open-loop PASS rule

Mark the open-loop test PASS only if all are true:

1. the PC app successfully sent the open-loop command
2. a PCB-test result frame was received before timeout
3. result payload byte `0` is `1` for open-loop test
4. result payload byte `1` is `2` for PASS

Recommended timeout:

- `openloop_duration_ms + 1000 ms`

Recommended defaults:

- board-side open-loop duration default: `5000 ms`
- PC-side open-loop timeout default: `6500 ms`

### Open-loop FAIL cases

At minimum detect these:

- timeout waiting for result frame
- result payload says FAIL
- failure mask indicates undervoltage, overcurrent, gate-driver fault, invalid duration, or board-side CAN TX issue

---

## What The Open-Loop Test Actually Means

The open-loop test is an electrical screening test, not a motion-verification test.

A PASS means:

- command was accepted
- the board ran the configured open-loop routine for the required duration
- no configured internal abort condition occurred
- the board transmitted a PASS result frame

It does not mean:

- rotor definitely spun
- torque was correct
- phase order is correct
- motor is mechanically healthy

If motion verification is needed later, that requires additional sensing or a fixture-level observer outside the current protocol.

---

## Recommended App Platform And Stack

### Recommended first implementation

- OS target: Windows
- language: Python 3.11+
- CAN library: `python-can`
- GUI: `PySide6`
- packaging: `PyInstaller`

### Why

- Python is the fastest path for a bench-test tool
- `python-can` supports multiple common CAN adapters behind one API
- `PySide6` is suitable for a production test GUI
- `PyInstaller` is the practical path to a standalone `.exe`

Official references:

- `python-can`: https://python-can.readthedocs.io/en/stable/
- `PySide6`: https://doc.qt.io/qtforpython-6/
- `PyInstaller`: https://pyinstaller.org/en/stable/

### Adapters

Do not tightly bind the design to one adapter unless manufacturing already standardized on one.

The transport layer should be configurable for backends such as:

- `pcan`
- `vector`
- `ixxat`
- `slcan`

The app should allow configuration of:

- interface/backend
- channel/device
- bitrate

---

## Recommended Development Approach

Build the app in stages.

### Stage 1: CLI first

Create a simple console tool that can:

- open the CAN adapter
- send open-loop and CAN-ID commands
- receive and parse replies
- print structured PASS/FAIL output

Do not build the GUI first.

### Stage 2: Extract reusable core

Refactor the CLI code into reusable modules:

- transport
- protocol parser/encoder
- test runner
- logging

### Stage 3: Build GUI

Add a Windows desktop GUI on top of the tested core library.

### Stage 4: Package

Generate a distributable `.exe`.

---

## Recommended App Architecture

Keep transport, protocol logic, and UI separate.

### Suggested module layout

```text
pcbt_app/
  app.py
  config.py
  models.py
  transport/
    can_bus.py
  protocol/
    ids.py
    frames.py
    parser.py
  services/
    pcb_test_service.py
    test_runner.py
  ui/
    main_window.py
    widgets.py
  logging/
    event_log.py
  tests/
    test_parser.py
    test_protocol.py
```

### Module responsibilities

`transport/can_bus.py`

- open and close CAN interface
- send frames
- receive frames
- abstract adapter-specific settings

`protocol/ids.py`

- build and parse extended IDs
- expose function code, state byte, driver ID, host byte

`protocol/frames.py`

- build PCB-test command frames
- parse PCB-test result payloads
- classify incoming messages

`protocol/parser.py`

- convert raw CAN messages into typed application objects

`services/pcb_test_service.py`

- expose high-level calls such as:
  - `run_can_test()`
  - `run_openloop_test(duration_ms=None)`
- handle timeouts and response collection
- decide final verdict

`services/test_runner.py`

- ensure only one test runs at a time
- manage current state for the UI

`models.py`

- define typed data models for parsed frames and verdicts

### Threading model

Recommended:

- GUI thread for user interaction only
- worker thread for CAN receive loop and test execution

The UI must not block while waiting for CAN replies.

---

## Suggested Python Data Models

```python
from dataclasses import dataclass
from typing import Optional


@dataclass
class CanIdFields:
    raw_id: int
    function_code: int
    state_byte: int
    driver_id: int
    host_id: int


@dataclass
class PcbTestResultFrame:
    id_fields: CanIdFields
    test_id: int
    result: int
    fail_mask: int
    actual_return_id: int
    fsr1: int
    fsr2: int


@dataclass
class CanIdResponseFrame:
    id_fields: CanIdFields
    data: bytes


@dataclass
class TestVerdict:
    test_name: str
    passed: bool
    reason: str
    returned_driver_id: Optional[int] = None
    fail_mask: Optional[int] = None
    fsr1: Optional[int] = None
    fsr2: Optional[int] = None
```

---

## Minimum GUI Requirements

The first usable GUI should provide:

- CAN adapter backend selector
- channel/device selector
- bitrate field
- connect/disconnect button
- connection state indicator
- `Run CAN Test` button
- `Run Openloop Test` button
- open-loop duration field
- returned driver ID display
- parsed test-result display
- failure-mask decode into readable text
- raw CAN frame log with timestamps
- overall PASS/FAIL indicator

Optional later additions:

- operator name
- fixture slot
- serial number input
- CSV or JSON export
- one-click full test sequence
- persistent settings

---

## Suggested Operator Workflow

1. Select CAN backend, channel, and bitrate
2. Connect to the adapter
3. Run `CAN Test`
4. App waits for both expected reply frames
5. App displays PASS/FAIL and returned driver ID
6. Run `Openloop Test`
7. App waits for the result frame
8. App displays PASS/FAIL and decoded diagnostics
9. App stores results if traceability is required

Later, a single `Run Full PCB Test` action can sequence:

1. CAN test
2. Open-loop test
3. overall verdict

Do not run multiple tests concurrently because the current protocol has no nonce or correlation token.

---

## Correlation Strategy

The current protocol does not include sequence numbers or request tokens.

Therefore the app must:

- send only one test command at a time
- use short time windows
- classify incoming frames by function code
- ignore unrelated traffic

Do not pipeline commands.

If the protocol is extended in a later revision, the highest-value improvement would be adding a request nonce echoed by the board in its replies.

---

## Known Limitations

These are expected system limits:

- no request/response nonce
- board-side CAN PASS alone is not trustworthy enough for final judgment
- open-loop PASS does not prove actual motor movement
- motor-ID payload is not needed for the test app beyond frame recognition unless a future requirement says otherwise
- manual debug-trigger methods are not valid substitutes for end-to-end CAN testing

---

## Acceptance Criteria For Version 1

The first acceptable version of the PC app should:

1. run on Windows
2. connect to at least one real CAN adapter through `python-can`
3. send extended-ID PCB-test commands
4. parse motor-ID response frames
5. parse PCB-test result frames
6. display returned driver ID
7. decode the failure mask into readable text
8. correctly mark PASS/FAIL for both supported tests
9. log raw frames with timestamps
10. remain responsive while waiting for replies

---

## Recommended Build Order

Implement in this order:

1. a small Python CLI using `python-can`
2. arbitration-ID parser
3. PCB-test result-frame parser
4. `run_can_test()` flow
5. `run_openloop_test()` flow
6. unit tests for parsing and verdict logic
7. `PySide6` GUI using the already-tested core library
8. packaging into a Windows executable

Do not start with the GUI. Prove the protocol first.
