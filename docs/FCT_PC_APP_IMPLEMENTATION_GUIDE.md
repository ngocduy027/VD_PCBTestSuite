# PC App Implementation Guide for the Temporary FCT CAN Protocol

## 1. Purpose and source of truth

This guide describes how the PC application should implement the current
factory-test (FCT) CAN interface in this firmware repository.

Use the implementation as the final source of truth:

- CAN adapter: `lib/test/fct_can_command.c` and `.h`
- FCT lifecycle: `lib/test/fct.c` and `.h`
- CAN routing and response retry: `Core/Src/main.c`
- Protocol design: `docs/FCT_CAN_COMMAND_TEMPORARY_DESIGN.md`
- Workflow diagram: `docs/FCT_IMPLEMENTATION_WORKFLOW.drawio`

The current `AppTestPCB` transport layer can already send and receive classic
CAN frames through `RawCanFrame`. The Waveshare USB-CAN-A backend also already
supports 29-bit extended IDs and eight-byte payloads. Keep that transport code.
Add a separate FCT protocol layer and a stop-and-wait FCT client above it.

## 2. Important migration decisions

### 2.1 Do not reuse the old diagnostic test enum

The existing PC app has legacy test IDs such as `GONGWANG_ENCODER = 4`. The new
FCT protocol uses different IDs:

| FCT test | ID |
|---|---:|
| GongWang encoder | `0x00` |
| Motor open-loop | `0x01` |
| CAN stress observer | `0x02` |

Create a new `FctTestId` enum. Do not change the legacy enum until all old
diagnostic users have been migrated.

### 2.2 Do not treat an ID byte as permanently "driver" or "host"

The 29-bit FCT identifier contains `source_id` and `target_id`. Their positions
swap in the response. The existing `protocol/ids.py` helper uses names that are
convenient for older messages but can be misleading for FCT requests.

Create dedicated `build_fct_id()` and `parse_fct_id()` helpers with
`source_id`/`target_id` names.

### 2.3 Tests may run concurrently, but commands must be stop-and-wait

The firmware keeps independent state for compatible tests, so the app may
start tests 0, 1, and 2 sequentially and then let them run concurrently.

However, the response-retry implementation has only one pending-response slot
and the wire protocol has no transaction sequence number. The PC app must:

1. send one FCT request;
2. wait for its matching response or timeout;
3. only then send the next FCT request.

Use one FCT transaction lock per physical CAN transport. Do not issue parallel
START/STOP/STATUS requests from different GUI workers.

## 3. Transport configuration

Use classic CAN with a 29-bit extended identifier:

| Setting | Value |
|---|---|
| CAN mode | Normal |
| CAN frame | Extended data frame |
| CAN bitrate | `1,000,000 bit/s` for the current setup |
| CAN FD | Disabled |
| Payload length | Exactly 8 bytes |

For the Waveshare USB-CAN-A used by `AppTestPCB`, the normal settings are:

- backend: `waveshare-usbcan-a`
- serial baud: `2,000,000`
- CAN bitrate: `1,000,000`
- fixed 20-byte Waveshare serial protocol
- extended-frame mode

The adapter's serial packet format is a transport detail. The FCT protocol
code should only create and consume `RawCanFrame` objects.

## 4. CAN identifier contract

```text
Bits 28..24   23..16    15..8       7..0
+-----------+----------+-----------+-----------+
| Function  | Opcode   | Source ID | Target ID |
+-----------+----------+-----------+-----------+
```

| Field | Request | Response |
|---|---:|---:|
| Function | `0x1E` | `0x1F` |
| Opcode | requested opcode | echoed opcode |
| Source ID | PC host ID | driver ID |
| Target ID | driver ID | PC host ID |

Build the IDs as follows:

```python
request_id = (
    (0x1E << 24)
    | (opcode << 16)
    | (host_id << 8)
    | driver_id
)

expected_response_id = (
    (0x1F << 24)
    | (opcode << 16)
    | (driver_id << 8)
    | host_id
)
```

All IDs and node IDs must be range checked:

- CAN identifier: `0x00000000..0x1FFFFFFF`
- opcode/source/target: `0x00..0xFF`
- function: `0x00..0x1F`

## 5. Opcodes and payloads

| Name | Opcode |
|---|---:|
| START | `0x01` |
| STOP | `0x02` |
| STATUS | `0x03` |

### 5.1 START request

```text
Byte 0      test_id
Byte 1      period_ticks, uint8 (one tick = 50 us at 20 kHz)
Bytes 2..4  duration_ms, unsigned 24-bit little-endian
Bytes 5..7  parameter_override, signed 24-bit little-endian
```

The firmware uses the period field directly as a 20 kHz tick divider. Validate
values before sending:

- `period_ticks`: `0..255`
- `duration_ms`: `1..16777215`
- GongWang test: `period_ticks > 0`; use `1` for a 20 kHz request rate
- Open-loop test: `period_ticks == 0`
- CAN stress test: `period_ticks == 0`

For motor open-loop, `parameter_override` is the signed mechanical theta step
in microradians per 20 kHz control tick. Zero selects the firmware default of
`-830` microradians per tick. Nonzero values must be from `-1570` through
`+1570`. GongWang and CAN stress do not use this field and should send zero.

The current firmware's open-loop minimum duration is one 20 kHz tick after
conversion, so any positive millisecond duration is accepted.

### 5.2 STOP and STATUS requests

```text
Byte 0      test_id
Bytes 1..7  reserved = 0
```

The current firmware ignores reserved bytes. The PC app should still transmit
them as zero to keep frames deterministic.

## 6. Universal response

Every accepted FCT request is answered with the same eight-byte layout:

```text
Byte 0      command_result
Byte 1      test_id
Byte 2      test_state
Byte 3[3:0] pass_count bits 19..16
Byte 3[7:4] fail_count bits 19..16
Bytes 4..5  pass_count bits 15..0, little-endian
Bytes 6..7  fail_count bits 15..0, little-endian
```

Both counters saturate at `0xFFFFF` (1,048,575). Decode them as:

```python
pass_count = int.from_bytes(data[4:6], "little") | ((data[3] & 0x0F) << 16)
fail_count = int.from_bytes(data[6:8], "little") | ((data[3] >> 4) << 16)
```

### 6.1 Command results

| Value | Name | App action |
|---:|---|---|
| `0` | `OK` | Command was accepted; inspect state. |
| `1` | `INVALID_OPCODE` | PC protocol bug or incompatible firmware. |
| `2` | `INVALID_TEST` | PC used an unsupported FCT test ID. |
| `3` | `INVALID_PARAMETER` | Correct period, duration, or DLC. |
| `4` | `BUSY` | Same test is active or an exclusive resource conflicts. |
| `5` | `NOT_RUNNING` | STOP was requested for an inactive test. |
| `6` | `SAFETY_BLOCKED` | Board conditions reject START. |
| `7` | `INTERNAL_ERROR` | Firmware could not complete the operation. |

### 6.2 Test states

| Value | Name | Terminal? |
|---:|---|---|
| `0` | `IDLE` | No |
| `1` | `RUNNING` | No |
| `2` | `PASS` | Yes |
| `3` | `FAIL` | Yes |
| `4` | `TIMEOUT` | Yes |
| `5` | `ABORTED` | Yes |

Treat `command_result` and `test_state` as separate fields. A valid test ID can
return its current state even when the command result is an opcode/DLC error.
An invalid test ID returns the default `IDLE`, zero-counter status.

## 7. Recommended PC-side data model

Add a module such as `pcbt_app/protocol/fct_can.py`:

```python
from dataclasses import dataclass
from enum import IntEnum

from pcbt_app.models import RawCanFrame


FCT_REQUEST_FUNCTION = 0x1E
FCT_RESPONSE_FUNCTION = 0x1F
FCT_DLC = 8


class FctOpcode(IntEnum):
    START = 0x01
    STOP = 0x02
    STATUS = 0x03


class FctTestId(IntEnum):
    GONGWANG_ENCODER = 0x00
    OPEN_LOOP = 0x01
    CAN_STRESS = 0x02


class FctCommandResult(IntEnum):
    OK = 0
    INVALID_OPCODE = 1
    INVALID_TEST = 2
    INVALID_PARAMETER = 3
    BUSY = 4
    NOT_RUNNING = 5
    SAFETY_BLOCKED = 6
    INTERNAL_ERROR = 7


class FctState(IntEnum):
    IDLE = 0
    RUNNING = 1
    PASS = 2
    FAIL = 3
    TIMEOUT = 4
    ABORTED = 5

    @property
    def is_terminal(self) -> bool:
        return self in {
            FctState.PASS,
            FctState.FAIL,
            FctState.TIMEOUT,
            FctState.ABORTED,
        }


@dataclass(slots=True, frozen=True)
class FctIdFields:
    function: int
    opcode: int
    source_id: int
    target_id: int


@dataclass(slots=True, frozen=True)
class FctResponse:
    raw_frame: RawCanFrame
    opcode: FctOpcode
    source_id: int
    target_id: int
    result: FctCommandResult
    test_id: int
    state: FctState
    pass_count: int
    fail_count: int
```

If unknown enum values are received, report a protocol-version error rather
than allowing `IntEnum(...)` to crash a GUI worker without context.

## 8. Frame builders

```python
def _byte(name: str, value: int) -> int:
    if not 0 <= value <= 0xFF:
        raise ValueError(f"{name} must be 0..255")
    return value


def build_fct_id(function: int, opcode: int, source_id: int, target_id: int) -> int:
    if not 0 <= function <= 0x1F:
        raise ValueError("function must be 0..31")
    opcode = _byte("opcode", opcode)
    source_id = _byte("source_id", source_id)
    target_id = _byte("target_id", target_id)
    return (function << 24) | (opcode << 16) | (source_id << 8) | target_id


def build_fct_start(
    *, host_id: int, driver_id: int, test_id: FctTestId,
    period_ticks: int, duration_ms: int, parameter_override: int = 0,
) -> RawCanFrame:
    if not 0 <= period_ticks <= 0xFF:
        raise ValueError("period_ticks must fit uint8")
    if not 1 <= duration_ms <= 0xFFFFFF:
        raise ValueError("duration_ms must fit unsigned 24-bit")
    if not -0x800000 <= parameter_override <= 0x7FFFFF:
        raise ValueError("parameter_override must fit signed 24-bit")
    if test_id == FctTestId.GONGWANG_ENCODER and period_ticks == 0:
        raise ValueError("GongWang requires a nonzero tick period")
    if test_id in {FctTestId.OPEN_LOOP, FctTestId.CAN_STRESS} and period_ticks != 0:
        raise ValueError("Open-loop and CAN stress require period_ticks=0")

    data = (
        bytes((int(test_id), period_ticks))
        + duration_ms.to_bytes(3, "little")
        + parameter_override.to_bytes(3, "little", signed=True)
    )
    return RawCanFrame(
        arbitration_id=build_fct_id(
            FCT_REQUEST_FUNCTION, FctOpcode.START, host_id, driver_id
        ),
        data=data,
        is_extended_id=True,
    )


def build_fct_simple(
    *, host_id: int, driver_id: int, opcode: FctOpcode, test_id: FctTestId,
) -> RawCanFrame:
    if opcode not in {FctOpcode.STOP, FctOpcode.STATUS}:
        raise ValueError("build_fct_simple only supports STOP and STATUS")
    return RawCanFrame(
        arbitration_id=build_fct_id(
            FCT_REQUEST_FUNCTION, opcode, host_id, driver_id
        ),
        data=bytes((int(test_id), 0, 0, 0, 0, 0, 0, 0)),
        is_extended_id=True,
    )
```

## 9. Response filtering and parsing

Do not accept a frame merely because its function is `0x1F`. A response matches
one request only when all of these are true:

1. it is an extended data frame;
2. DLC is exactly 8;
3. function is `0x1F`;
4. opcode equals the request opcode;
5. source ID equals the requested driver ID;
6. target ID equals the PC host ID;
7. response byte 1 equals the requested test ID.

Unrelated legacy CAN traffic is expected, especially during CAN stress. Ignore
or dispatch unrelated frames; do not treat the first received frame as the FCT
response.

```python
def parse_fct_id(arbitration_id: int) -> FctIdFields:
    if not 0 <= arbitration_id <= 0x1FFFFFFF:
        raise ValueError("not a 29-bit CAN identifier")
    return FctIdFields(
        function=(arbitration_id >> 24) & 0x1F,
        opcode=(arbitration_id >> 16) & 0xFF,
        source_id=(arbitration_id >> 8) & 0xFF,
        target_id=arbitration_id & 0xFF,
    )


def parse_matching_fct_response(
    frame: RawCanFrame, *, expected_opcode: FctOpcode,
    expected_test_id: FctTestId, host_id: int, driver_id: int,
) -> FctResponse | None:
    if not frame.is_extended_id or len(frame.data) != FCT_DLC:
        return None
    fields = parse_fct_id(frame.arbitration_id)
    if (
        fields.function != FCT_RESPONSE_FUNCTION
        or fields.opcode != int(expected_opcode)
        or fields.source_id != driver_id
        or fields.target_id != host_id
        or frame.data[1] != int(expected_test_id)
    ):
        return None

    try:
        result = FctCommandResult(frame.data[0])
        state = FctState(frame.data[2])
        opcode = FctOpcode(fields.opcode)
    except ValueError as exc:
        raise ValueError("unsupported FCT enum value in firmware response") from exc

    return FctResponse(
        raw_frame=frame,
        opcode=opcode,
        source_id=fields.source_id,
        target_id=fields.target_id,
        result=result,
        test_id=frame.data[1],
        state=state,
        pass_count=(
            int.from_bytes(frame.data[4:6], "little")
            | ((frame.data[3] & 0x0F) << 16)
        ),
        fail_count=(
            int.from_bytes(frame.data[6:8], "little")
            | ((frame.data[3] >> 4) << 16)
        ),
    )
```

## 10. Stop-and-wait transaction client

Add a service such as `pcbt_app/services/fct_client.py`. It should own a lock
shared by all FCT workflows using the same transport.

There must also be only one owner of `transport.recv()`. If multiple workers
read the serial/CAN transport independently, one worker can steal another
worker's FCT response. Prefer a central receive dispatcher:

```text
transport.recv()
    -> FCT response matcher / transaction queue
    -> legacy diagnostic parser
    -> CAN-stress traffic tracker
    -> raw-frame event log
```

The simple example below is valid when `FctClient` is that sole receive owner.
If `AppTestPCB` later uses a background receive thread, `transact()` should wait
on a dispatcher-provided queue or condition instead of calling `recv()` itself.

```python
from threading import Lock
from time import monotonic


class FctResponseTimeout(TimeoutError):
    pass


class FctClient:
    def __init__(self, transport, *, host_id: int, response_timeout_s: float = 0.5):
        self.transport = transport
        self.host_id = host_id
        self.response_timeout_s = response_timeout_s
        self._transaction_lock = Lock()

    def transact(self, request, *, opcode, test_id, driver_id) -> FctResponse:
        with self._transaction_lock:
            self.transport.send(request)
            deadline = monotonic() + self.response_timeout_s

            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise FctResponseTimeout(
                        f"no FCT {opcode.name} response from driver 0x{driver_id:02X}"
                    )
                frame = self.transport.recv(remaining)
                if frame is None:
                    continue
                response = parse_matching_fct_response(
                    frame,
                    expected_opcode=opcode,
                    expected_test_id=test_id,
                    host_id=self.host_id,
                    driver_id=driver_id,
                )
                if response is not None:
                    return response
                self.dispatch_or_log_unrelated_frame(frame)
```

Use the app's existing event log for every transmitted frame, received frame,
ignored frame, timeout, and parsed FCT response.

Drain stale transport input once before starting the first high-level workflow,
and only when no other workflow or receive consumer is active. Do not drain
when adding another parallel FCT test, or while waiting, because that can discard
another workflow's traffic or the response being matched.

## 11. High-level test workflow

### 11.1 START and polling

For one test:

1. validate PC inputs;
2. drain stale frames once;
3. send START and wait for the matching START response;
4. require `command_result == OK`;
5. normally require state `RUNNING` (a very short test may already be terminal);
6. poll STATUS using stop-and-wait transactions;
7. finish when a terminal state is received;
8. if the app deadline expires, send STOP and reconcile final status.

Suggested STATUS polling interval: `100 ms`. Do not poll continuously at the
adapter's maximum rate.

```python
while monotonic() < operation_deadline:
    response = client.status(driver_id=driver_id, test_id=test_id)
    if response.result is not FctCommandResult.OK:
        fail_with_command_result(response)
    if response.state.is_terminal:
        return make_verdict(response)
    sleep(0.100)

client.stop(driver_id=driver_id, test_id=test_id)
return wait_for_terminal_status_after_stop()
```

### 11.2 Separate firmware duration from PC deadline

`duration_ms` in START controls the firmware test duration. The PC operation
deadline controls how long the app is willing to wait for responses and safe
shutdown. They are not the same setting.

For open-loop, the PC deadline must include:

- up to 3 seconds for driver readiness;
- 1.5 seconds for alignment;
- requested RUN duration;
- at least 200 ms for driver shutdown;
- CAN/host scheduling margin.

A practical initial deadline is `duration_ms + 6000 ms`.

### 11.3 STOP behavior

- GongWang and CAN stress normally become `ABORTED` in the STOP response.
- Open-loop shutdown is asynchronous. Its STOP response may remain `RUNNING`.
  Continue polling STATUS until `ABORTED`, `FAIL`, or another terminal state.
- `NOT_RUNNING` means the selected test was already inactive. Query STATUS to
  discover the stored terminal state before deciding the UI verdict.

### 11.4 Response timeout recovery

Do not blindly retry non-idempotent commands:

- START may have succeeded even if its response was lost; a duplicate can
  return `BUSY`.
- STOP may have succeeded; a duplicate can return `NOT_RUNNING`.
- STATUS is read-only and is safe to retry.

After a START or STOP response timeout, use STATUS to reconcile the actual test
state. Filter out any late response whose opcode does not match the STATUS
transaction.

## 12. Per-test behavior

### 12.1 GongWang encoder (`test_id = 0`)

- START requires `period_ticks > 0`; the app uses `1` tick for 20 kHz.
- Firmware sends one UART request whenever its 20 kHz divider expires.
- At duration timeout, PASS requires:
  - at least one successful request;
  - no request enqueue errors;
  - no response errors;
  - received response count plus one equals successful request count.
- `pass_count` is the good-response count.
- `fail_count` is the response-error count.
- Counters are copied into FCT status at finish or explicit STOP; do not depend
  on STATUS returning live counters while the test is RUNNING.

### 12.2 Motor open-loop (`test_id = 1`)

- START requires `period_ticks = 0`.
- Firmware state remains `RUNNING` through driver wait, alignment, RUN, and safe
  shutdown.
- Requested duration counts only during the RUN phase.
- A normal duration timeout requests PASS, then safe shutdown completes before
  the state becomes `PASS`.
- Explicit STOP requests `ABORTED` and also waits for safe shutdown.
- Safety, driver-readiness, or VBUS failure produces `FAIL`.
- A pass sets `pass_count = 1`; a fail sets `fail_count = 1`.
- The new temporary command still carries RUN duration, but it does not carry
  theta step or voltage settings. Those are firmware constants. Keep the
  duration input; hide, disable, or mark the old theta/voltage fields as legacy
  when this FCT path is selected.

### 12.3 CAN stress observer (`test_id = 2`)

- START requires `period_ticks = 0`.
- The firmware does not generate stress traffic. It passively observes accepted
  legacy `MIT_Passive_Reports` requests and successful response enqueues.
- The PC must generate that legacy request traffic during the FCT duration.
- If the PC sends no accepted passive-report requests, the test fails.
- At finish:
  - `pass_count = valid_response_count`;
  - `fail_count = abs(valid_request_count - valid_response_count)`;
  - PASS requires at least one request and `fail_count == 0`.
- FCT status counters are stored when the test finishes or stops; do not expect
  live counters during `RUNNING`.

A CAN-stress workflow therefore has two coordinated activities:

1. an FCT client starts and later polls test 2;
2. the existing legacy traffic generator sends `MIT_Passive_Reports` frames.

The FCT command lock only serializes FCT requests. Legacy stress frames may be
sent between STATUS transactions, but the receive path must dispatch their
responses separately from FCT response matching.

The firmware observer duration must be longer than the planned traffic window.
For the existing UI's `interval_ms` and `total_messages` inputs, an initial rule
can be:

```python
traffic_window_ms = interval_ms * total_messages
fct_duration_ms = traffic_window_ms + 500  # scheduling margin
pc_deadline_ms = fct_duration_ms + 1500     # response/polling margin
```

Validate the multiplication before building the uint32 duration. Start the FCT
observer and receive its `OK` response before sending the first passive-report
request. Stop traffic generation if the observer reaches a terminal state early.

## 13. Parallel-test workflow

To run compatible tests concurrently:

1. START test 0 and wait for its response;
2. START test 1 and wait for its response;
3. START test 2 and wait for its response;
4. schedule STATUS transactions for each active test, one transaction at a time;
5. keep independent UI state and deadlines for each `(driver_id, test_id)`;
6. STOP only the selected test when the user cancels it.

Do not use one global `active_test_id`. A useful key is:

```python
(driver_id, FctTestId)
```

The current three test resources are compatible with each other. Still handle
`BUSY` because the same test may already be running and future tests may add
real resource conflicts.

## 14. Concrete frame examples

Assume host ID `0xFD` and driver ID `0x7F`.

### Start open-loop for 3000 ms

```text
TX ID:   0x1E01FD7F
TX data: 01 00 00 00 B8 0B 00 00

RX ID:   0x1F017FFD
RX data: 00 01 01 00 00 00 00 00
         OK test=1 RUNNING pass=0 fail=0
```

### Read open-loop status

```text
TX ID:   0x1E03FD7F
TX data: 01 00 00 00 00 00 00 00

RX ID:   0x1F037FFD
```

### Stop open-loop

```text
TX ID:   0x1E02FD7F
TX data: 01 00 00 00 00 00 00 00

RX ID:   0x1F027FFD
```

### Start GongWang every 20 kHz tick for 2000 ms

```text
TX ID:   0x1E01FD7F
TX data: 00 00 01 00 D0 07 00 00
```

### Start CAN stress observer for 2000 ms

```text
TX ID:   0x1E01FD7F
TX data: 02 00 00 00 D0 07 00 00
```

## 15. Suggested AppTestPCB file changes

Implement the migration additively:

1. Add `pcbt_app/protocol/fct_can.py` for constants, enums, builders, parser,
   response model, and response matcher.
2. Add `pcbt_app/services/fct_client.py` for transaction locking, timeouts, and
   START/STOP/STATUS helpers.
3. Add a reusable FCT polling runner instead of duplicating the transaction loop
   in every test service.
4. Migrate open-loop, GongWang, and CAN stress services one at a time.
5. Keep the old diagnostic builders temporarily for tests not yet migrated.
6. Preserve the existing `RawCanFrame` and `AbstractCanTransport` interfaces.
7. Keep UI work separate from protocol correctness; first prove the protocol
   and service layer with a fake transport.

## 16. Required tests

Add at least these unit tests:

### Protocol tests

- every request builder produces an extended frame with exactly eight bytes;
- open-loop example builds ID `0x1E01FD7F` and the documented payload;
- expected response ID is `0x1F017FFD`;
- all integer fields are little-endian;
- invalid periods and durations fail before transmission;
- response parsing decodes both packed 20-bit counters correctly;
- wrong function/opcode/source/target/test ID is rejected as unrelated;
- unknown result/state values produce a clear protocol error.

### Transaction tests

- unrelated legacy frames are skipped until the matching FCT response arrives;
- a response timeout releases the transaction lock;
- two simultaneous callers never send two outstanding FCT requests;
- a late START response is not accepted as a STATUS response;
- STATUS timeout can be retried;
- START/STOP timeout recovery uses STATUS rather than blind retransmission.

### Workflow tests

- START `OK + RUNNING` leads to STATUS polling;
- terminal PASS/FAIL/TIMEOUT/ABORTED ends polling correctly;
- START `BUSY`, `INVALID_PARAMETER`, and `SAFETY_BLOCKED` are shown distinctly;
- open-loop STOP continues polling through `RUNNING` until terminal;
- concurrent tests keep separate state and serialize only their CAN commands;
- CAN stress generates legacy passive-report traffic while FCT test 2 runs;
- CAN stress with zero observed requests fails;
- running-status zero counters are not interpreted as final failure.

Run the complete AppTestPCB suite after migration, not only the new tests.

## 17. Implementation completion checklist

- [ ] Dedicated FCT enums use test IDs `0`, `1`, and `2`.
- [ ] Requests use function `0x1E`; responses require function `0x1F`.
- [ ] Source and target IDs swap in the response.
- [ ] Every request and response uses classic extended CAN with DLC 8.
- [ ] Multi-byte values use little-endian encoding.
- [ ] Reserved transmit bytes are zero; received reserved bytes are ignored.
- [ ] Only one FCT request is outstanding per physical transport.
- [ ] Response matching checks opcode, IDs, and test ID.
- [ ] Unrelated CAN frames are preserved or dispatched, not mistaken for FCT.
- [ ] START/STOP timeouts reconcile through STATUS.
- [ ] Polling stops only on a terminal state or PC operation deadline.
- [ ] Open-loop PC deadline includes wait, align, run, and shutdown.
- [ ] CAN stress sends legacy passive-report traffic while the observer is active.
- [ ] UI state is independent per driver and test.
- [ ] Protocol, transaction, workflow, and full regression tests pass.
