"""Regression tests for scheduler safety boundaries."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import docker_scheduler
from infra.helpers import result_error_count
from main import _module_outcome
from scheduler import ScheduledTaskRunner


class FakeConfig:
    scheduler = {
        "timezone": "UTC",
        "jitter_seconds": 0,
        "jobs": {"leavers": {"enabled": True, "cron": "0 0 * * *"}},
    }

    def __init__(self, enabled=True, dry_run=False):
        self.settings = SimpleNamespace(enabled=enabled, dry_run=dry_run)

    def get_module_settings(self, module_name):
        return self.settings


def test_scheduler_propagates_global_dry_run(monkeypatch):
    calls = []
    monkeypatch.setattr(
        docker_scheduler,
        "run_scheduled",
        lambda name, runner, dry_run=False: calls.append((name, dry_run)),
    )

    scheduler = docker_scheduler.create_scheduler(FakeConfig(), dry_run=True)
    scheduler.get_job("leavers").func()

    assert calls == [("Leavers", True)]


def test_module_result_errors_are_recorded_as_failure(monkeypatch):
    state_calls = []
    health_calls = []
    monkeypatch.setattr(docker_scheduler, "config", FakeConfig())
    monkeypatch.setattr(
        docker_scheduler,
        "sync_state",
        SimpleNamespace(set_last_run=lambda name: state_calls.append(name)),
    )
    monkeypatch.setattr(
        docker_scheduler,
        "get_health_server",
        lambda: SimpleNamespace(record_run=lambda **kwargs: health_calls.append(kwargs)),
    )
    monkeypatch.setattr(docker_scheduler, "slack", None)

    outcome = docker_scheduler.run_module_safe(
        "User Match",
        lambda dry_run=False: {"errors": 2},
    )

    assert outcome["success"] is False
    assert state_calls == []
    assert health_calls == [{"success": False, "module_name": "User Match"}]


def test_disabled_scheduler_module_does_not_construct_or_run(monkeypatch):
    monkeypatch.setattr(docker_scheduler, "config", FakeConfig(enabled=False))

    def runner(dry_run=False):
        raise AssertionError("disabled module must not run")

    outcome = docker_scheduler.run_module_safe("Cleanup", runner)

    assert outcome == {
        "success": True,
        "error": None,
        "results": {"skipped": True, "reason": "disabled"},
    }


def test_on_demand_alias_uses_canonical_module_controls(monkeypatch):
    monkeypatch.setattr(docker_scheduler, "config", FakeConfig(enabled=False))

    outcome = docker_scheduler.run_module_safe(
        "Cleanup (DRY RUN)",
        lambda dry_run=False: (_ for _ in ()).throw(AssertionError("must not run")),
        dry_run=True,
        module_key="cleanup",
    )

    assert outcome["results"]["skipped"] is True


def test_result_error_count_handles_all_module_conventions():
    assert result_error_count({"errors": ["one", "two"]}) == 2
    assert result_error_count({"errors": 3}) == 3
    assert result_error_count({"error": "LLM not configured"}) == 1
    assert result_error_count({"errors": 0}) == 0
    assert _module_outcome({"errors": 2}) == (1, {"errors": 2})


def test_legacy_scheduler_honors_module_controls():
    runner = ScheduledTaskRunner(FakeConfig(enabled=False), dry_run=False)
    assert runner._effective_dry_run("cleanup") is None

    runner = ScheduledTaskRunner(FakeConfig(enabled=True, dry_run=True), dry_run=False)
    assert runner._effective_dry_run("cleanup") is True


def test_startup_aborts_before_modules_when_preflight_fails(monkeypatch):
    released = []

    class FakeMutex:
        def acquire(self):
            return True

        def release(self):
            released.append(True)

    monkeypatch.setattr("infra.mutex.RunMutex", FakeMutex)
    monkeypatch.setattr(docker_scheduler, "pre_flight_check", lambda: False)
    monkeypatch.setattr(
        docker_scheduler,
        "run_module_safe",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("modules must not run after failed pre-flight")
        ),
    )

    assert docker_scheduler.run_all_modules_startup() == {
        "aborted": True,
        "reason": "preflight_failed",
    }
    assert released == [True]


def test_scheduled_mutex_skip_raises_and_records_failure(monkeypatch):
    health_calls = []

    class RefusedMutex:
        def acquire(self):
            return False

    monkeypatch.setattr("infra.mutex.RunMutex", RefusedMutex)
    monkeypatch.setattr(
        docker_scheduler,
        "get_health_server",
        lambda: SimpleNamespace(record_run=lambda **kwargs: health_calls.append(kwargs)),
    )

    with pytest.raises(RuntimeError, match="mutex unavailable or already held"):
        docker_scheduler.run_scheduled("Cleanup", lambda dry_run=False: {})

    assert health_calls == [{"success": False, "module_name": "Cleanup"}]


def test_scheduled_module_failure_raises_for_apscheduler(monkeypatch):
    released = []

    class FakeMutex:
        def acquire(self):
            return True

        def release(self):
            released.append(True)

    monkeypatch.setattr("infra.mutex.RunMutex", FakeMutex)
    monkeypatch.setattr(
        docker_scheduler,
        "run_module_safe",
        lambda *args, **kwargs: {
            "success": False,
            "error": "module_errors:2",
            "results": {"errors": 2},
        },
    )

    with pytest.raises(RuntimeError, match="module_errors:2"):
        docker_scheduler.run_scheduled("User Match", lambda dry_run=False: {})

    assert released == [True]
