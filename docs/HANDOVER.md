# VD_PCBTestSuite — Handover Guide

Start here if you are taking over this project. It covers what the app does,
how the code is organised, how each test decides PASS/FAIL, how to build and
release, and what is known to be unfinished. Code-level detail lives in the
module/class docstrings; this file is the map.

## 1. What the app is

A Windows desktop app (plus a small CLI) that drives production tests of the
VinD motor-driver PCB over CAN, and calibrates/inspects its GongWang absolute
encoder. The board runs dedicated test firmware; the app sends commands, reads
results and shows PASS/FAIL to an operator.

Typical users (from the operator guide, see §10):

| User | Adapter | What they run |
| --- | --- | --- |
| PCB assembler | Waveshare USB-CAN-FD **Model B** (+ USB-RS485 for board C1) | FCT tests: Detect ID, open-loop, CAN stress, RS485 stress, GongWang |
| Motor assembler | Waveshare USB-CAN **Model A** | Detect ID, GongWang live amplitude and calibration |

One executable supports both adapters; the adapter is selected in the GUI and
remembered.

## 2. Architecture

```text
 GUI thread                     Worker QThread                 Per-test threads
 ┌────────────────────┐ signals ┌─────────────────────┐ spawns ┌──────────────────────┐
 │ MainWindow         │────────▶│ CanWorker           │───────▶│ PcbTestService.run_* │
 │  + AdapterPanel    │◀────────│  owns transports    │        │ GwLiveReader loop    │
 │  + GwEncoderPanel  │ signals │  TestRunner guard   │        │ GwCalibrationRunner  │
 └────────────────────┘         └─────────────────────┘        └──────────┬───────────┘
                                                                           │
            protocol/  (frame builders + parsers, pure functions)  ◀───────┤
            transport/ (AbstractCanTransport: Model A, Model B, python-can, RS485)
```

* **`pcbt_app/transport/`**: one class per adapter, all implementing
  `AbstractCanTransport` (`connect/disconnect/send/recv/drain`). Model B
  emulates Model A's `AA 55` byte stream (`Communication_CAN_CANFD_ModelB.py`)
  so everything above the transport is adapter-agnostic.
* **`pcbt_app/protocol/`**: no I/O. Builds and parses CAN frames.
* **`pcbt_app/services/`**: test workflows. `PcbTestService` is the facade;
  each workflow module returns a `ServiceResult(TestVerdict)`.
* **`pcbt_app/ui/`**: PySide6. `layout.py` creates widgets as `MainWindow`
  attributes; `main_window.py` orchestrates tests and the UI state machine
  (`_set_ui_state`); `worker.py` does all hardware I/O off the GUI thread.

Concurrency rules:

* All CAN I/O happens off the GUI thread. The GUI only emits `request_*`
  signals and reacts to worker signals.
* Several FCT tests may run at the same time. `TestRunner` blocks starting the
  same test twice, and `FctClient` holds a **per-transport lock** for each
  request/response so concurrent users never steal each other's replies.
* GongWang live polling and calibration both use the encoder UART on the
  board, so they are mutually exclusive; calibration also refuses to start
  while any test is running. Live polling itself is not blocked by tests.

## 3. CAN protocols in use

All frames are classic CAN, 29-bit extended ID, 8-byte payload, little-endian
values. Host IDs: **`0xFD`** for FCT/GongWang, **`0xFE`** for legacy MIT replies.

| Protocol | ID layout | Function codes | Code |
| --- | --- | --- | --- |
| Legacy MIT diagnostic | `cmd \| b2 \| b1 \| target` | `0x00` Get ID, `0x11/0x12` read/write param, `0x1B` test report, `0x1C` stress frame | `protocol/mit_firmware.py` |
| FCT | `func(5b) \| opcode \| source \| target` | `0x1E` request / `0x1F` response; opcodes START/STOP/STATUS | `protocol/fct_can.py` |
| GongWang sin/cos | FCT layout | `0x1C` request / `0x1D` response (opcode `0x54` motor, `0x55` output) | `protocol/gw_sincos.py` |
| GongWang calibration | FCT layout | `0x1E/0x1F`, opcodes `0x10` START, `0x12` progress, `0x13` result | `protocol/gw_calibration.py` |

Function code `0x1C` means different things in the legacy protocol (board →
PC stress frame) and in GongWang sin/cos (PC → board request); they are told
apart by direction and host ID.

**Which protocol the app actually uses:** the GUI and CLI create
`PcbTestService(use_fct=True)`. Detect ID always uses the MIT Get ID frame;
open-loop, CAN stress, RS485 and GongWang use **FCT**. The legacy MIT
workflows (`services/openloop_test.py`, `can_stress_test.py`,
`gongwang_encoder_test.py`) are kept for older firmware but are only exercised
by unit tests today (see §9).

Firmware-side specifications: `docs/FCT_PC_APP_IMPLEMENTATION_GUIDE.md`,
`docs/FCT_RS485_PC_APP_REQUIREMENTS.md`, `docs/GW_SINCOS_PC_TASK.md`,
`docs/GW_CALIB_PC_TASK.md`. They reference paths in the firmware repository,
which is the source of truth for the board side.

## 4. Tests and PASS criteria (production / FCT path)

| Test | GUI key | PASS when | Code |
| --- | --- | --- | --- |
| Detect ID | `can` | Exactly one driver answers Get ID (broadcast, then a 0..255 scan fallback) | `services/can_id_test.py` |
| Open-loop | `openloop` | FCT result OK and final state `PASS` | `services/fct_workflows.py` |
| CAN stress | `can_stress` | FCT state `PASS` (board counts the PC's passive-report requests) | `services/fct_workflows.py` |
| RS485 stress | `rs485_stress` | FCT result OK, state `PASS`, `fail_count == 0`. PC-side capture problems are **informational only** | `services/rs485_stress_test.py` |
| GongWang encoder | `gongwang` | FCT state `PASS` | `services/fct_workflows.py` |
| GW calibration | (tab) | Outcome `PASS` with motor and output records | `services/gw_calibration.py` |

For FCT tests the **board** decides PASS/FAIL; the PC enforces timeouts,
validates inputs, sends STOP on timeout/error and reconciles the final state.
Legacy-path criteria (95 % CAN stress threshold, GongWang rate checks) are in
the docstrings of those modules.

Input limits shown in the GUI come from `protocol/fct_can.py`
(`FCT_MAX_DURATION_MS`, theta-step range, `rs485_max_duration_ms`), and the
defaults come from `protocol/mit_firmware.py` and `ui/presets.py`.

## 5. Development

```powershell
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .[dev]

.\check.ps1                          # pytest (223 tests) + ruff + mypy, no hardware needed
python -m pcbt_app.app               # run the GUI
```

* After activating `.venv`, call `python`: `py -3.14` ignores an activated
  venv and would use the system interpreter.
* Unit tests use `transport/fake_bus.py` (`FakeCanTransport`) or small fakes;
  GUI tests use `pytest-qt`. No test talks to real hardware.
* If pytest fails with a permission error on `%TEMP%\pytest-of-*`, pass
  `--basetemp=<writable dir>`.
* `ui/layout.py` widget attribute names are a contract shared by
  `main_window.py`, the panel mixins and `tests/test_gui.py`.

### Adding a new test

1. Protocol: add builders/parsers in `protocol/` (pure functions + unit tests).
2. Workflow: add a `run_*` function in `services/` returning
   `service._result(...)`. Put new metrics on `TestVerdict` (`models.py`).
3. Facade: add a `PcbTestService.run_*` method.
4. Worker: add a `@Slot` that calls `_run_test("<key>", ...)` and handle the
   key in `_execute_test`.
5. GUI: add a request signal and `_run_<key>` in `MainWindow`, widgets in
   `layout.py`, a card spec in `ui/result_report.py`, and enable rules in
   `_set_ui_state`.
6. Tests for each layer.

## 6. Build and release

```powershell
.\build_windows.ps1        # install deps, run check.ps1, then PyInstaller pcbt_app.spec
```

* Output: `dist\VD_PCBTestSuite_V<major>.<minor>.exe` (single-file, windowed).
* The spec bundles `pcbt_app/transport/ControlCANFD.dll` (x64 vendor DLL for
  Model B) and filters out DLLs from the Codex runtime path that break
  QtWidgets in frozen builds.
* **Version**: bump `__version__` in `pcbt_app/__init__.py` only. The package
  version, window title and exe name are derived from it.
* Previous release binaries and firmware `.hex` images are kept in `archive/`,
  which is **git-ignored**. Store releases somewhere shared (file server, Git
  LFS or GitHub releases) if they are needed elsewhere.

Target PC requirements: Windows x64, CH340/CH341 driver for Model A,
Waveshare driver and the Microsoft Visual C++ 2008 runtime for Model B, any
USB-RS485 adapter capable of 2.5 Mbaud for the RS485 test.

## 7. Configuration, persistence, logs

* `pcbt_app/config.py`: backend names, default channels and timeouts.
  `USB_CAN_ADAPTER_MODEL` only sets the first-run default.
* QSettings (registry `HKCU\Software\VinD\VD_PCBTestSuite`):
  `adapter/backend`, `adapter/device_index`, `adapter/channel_index`,
  `encoder/amplitude/{yellow_start,green_start,green_end}`.
* Log file: `%LOCALAPPDATA%\VinD\VD_PCBTestSuite_V<major>.<minor>\logs\pcbt_app.log`
  (rotating, 5 × 1 MB). Ask operators for it when they report an issue. The
  GUI console additionally shows every raw TX/RX frame for the session.

## 8. CLI

```powershell
python -m pcbt_app.cli can-test --backend waveshare-usbcan-a --channel COM13
python -m pcbt_app.cli openloop-test --backend waveshare-usbcanfd-b --channel "USB-CAN-FD-B Device 0 / CAN2" --json
```

Exit codes: `0` PASS, `2` FAIL, `3` transport error.

## 9. Known issues and technical debt

| Item | Where | Notes |
| --- | --- | --- |
Open items that need a decision from the team (not code-quality issues):

| Item | Where | Notes |
| --- | --- | --- |
| Legacy MIT workflows not used in production | `services/openloop_test.py`, `can_stress_test.py`, `gongwang_encoder_test.py`, `use_fct=False` path | Remove once no legacy firmware is in the field, or expose a switch. |
| RS485 timeout reason can be lost | `services/rs485_stress_test.py::_finish_rs485_result` | If the app times out but the board reports PASS after STOP, the timeout reason is dropped and the verdict is PASS. Confirm the intended behaviour with the firmware team. |
| Mixed-language operator messages | encoder error and fault texts are Vietnamese; the rest is English | Decide on one language or add translations. |
| No hosted CI | — | The repo has no remote yet. `check.ps1` runs all checks locally and `build_windows.ps1` refuses to build if they fail; wire `check.ps1` into CI once the repo is hosted. |
| No automated hardware test | — | Do a bench run with real adapters and a known-good board before every release. |

Deliberate design choices to keep in mind:

| Item | Where | Notes |
| --- | --- | --- |
| Shared Model B file naming | `transport/Communication_CAN_CANFD_ModelB.py` | Name and the `USB_CANFD_B` wrapper are kept because other VinD apps copy this file. |
| mypy `attr-defined` off for `pcbt_app.ui.*` | `pyproject.toml` | Widgets are attached dynamically by `layout.py`; the rest of mypy (including untyped-function bodies) is enforced everywhere. |

## 10. Document index

| Document | Status |
| --- | --- |
| `README.md` | Current: setup, adapter usage, layout overview |
| `docs/HANDOVER.md` | Current: this file |
| `docs/FCT_PC_APP_IMPLEMENTATION_GUIDE.md` | Firmware-side FCT protocol spec (current) |
| `docs/FCT_RS485_PC_APP_REQUIREMENTS.md` | Firmware-side RS485 test spec (current) |
| `docs/GW_SINCOS_PC_TASK.md`, `docs/SIN_COS_READING.md` | GongWang live sin/cos spec |
| `docs/GW_CALIB_PC_TASK.md` | GongWang calibration spec |
| `docs/Quy trình Calibration cho encoder GongWang.docx` | Calibration procedure (Vietnamese) |
| `docs/USB-CAN-FD-B-API-Manual.pdf`, `docs/RS06User Manual260428.pdf` | Vendor manuals |
| `PCB_TEST_PC_APP_AGENT_BRIEF.md` | **Historical**: original brief for the first `0x1B`/`0x1C` protocol, which is no longer implemented |
| `docs/Huong_dan_su_dung_app_VD_PCBTestSuite.docx` | Operator guide (Vietnamese, app V1.2). Its source script and screenshots stay in the git-ignored `output/` folder |
| `docs/images/` | GUI screenshots (dual adapter selector, Model B configuration) |
