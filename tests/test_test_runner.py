from threading import Barrier, Event, Thread

from pcbt_app.models import TestVerdict as Verdict
from pcbt_app.services.pcb_test_service import ServiceResult
from pcbt_app.services.test_runner import TestRunner as Runner


def _passed(name: str) -> ServiceResult:
    return ServiceResult(Verdict(test_name=name, passed=True, reason="PASS"))


def test_runner_allows_different_fct_tests_to_overlap() -> None:
    runner = Runner()
    entered = Barrier(3)
    release = Event()
    results = []

    def task(name: str) -> ServiceResult:
        entered.wait(timeout=1)
        release.wait(timeout=1)
        return _passed(name)

    threads = [
        Thread(target=lambda: results.append(runner.run("openloop", task, "Open-loop Test"))),
        Thread(target=lambda: results.append(runner.run("gongwang", task, "GongWang Encoder Test"))),
    ]
    for thread in threads:
        thread.start()
    entered.wait(timeout=1)
    release.set()
    for thread in threads:
        thread.join(timeout=1)

    assert len(results) == 2


def test_runner_rejects_only_a_duplicate_test() -> None:
    runner = Runner()
    entered = Event()
    release = Event()

    def blocking_task() -> ServiceResult:
        entered.set()
        release.wait(timeout=1)
        return _passed("Open-loop Test")

    thread = Thread(target=lambda: runner.run("openloop", blocking_task))
    thread.start()
    assert entered.wait(timeout=1)
    try:
        runner.run("openloop", lambda: _passed("Open-loop Test"))
    except RuntimeError as exc:
        assert str(exc) == "openloop is already running"
    else:
        raise AssertionError("duplicate test was allowed")
    finally:
        release.set()
        thread.join(timeout=1)
