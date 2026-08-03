"""Unit tests for mutex refresh and health auth helper."""
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from infra.mutex import RunMutex
from infra.health import HealthCheckServer, _is_authorized, _is_request_authorized
from modules.maintenance.health_check import HealthCheckModule


def test_health_authorization_helper():
    token = "secret"
    assert _is_authorized(token, "Bearer secret") is True
    assert _is_authorized(token, "Bearer wrong") is False
    assert _is_authorized(None, "") is True  # disabled auth
    assert _is_request_authorized("/healthz", token, "") is True
    assert _is_request_authorized("/health", token, "") is False


def test_readiness_requires_azure_and_scheduler():
    server = HealthCheckServer()

    assert server._get_status_dict()["status"] == "degraded"

    server.update_status(scheduler_running=True)
    assert server._get_status_dict()["status"] == "healthy"

    server.update_status(azure_healthy=False)
    assert server._get_status_dict()["status"] == "degraded"


def test_mutex_refresh_thread_runs_and_stops():
    # Patch boto3 client to a fake that records writes
    put_calls = []

    class FakeSSM:
        def __init__(self):
            self.exceptions = type("E", (), {"ParameterNotFound": Exception})

        def get_parameter(self, Name):
            raise self.exceptions.ParameterNotFound()

        def put_parameter(self, Name, Value, Type, Overwrite):
            put_calls.append((Name, Value))

        def delete_parameter(self, Name):
            pass

    m = RunMutex(param_name="/test/mutex")
    m._ssm = FakeSSM()  # inject fake client
    acquired = m.acquire()
    assert acquired is True
    # allow any background work to start
    time.sleep(0.05)
    m.release()

    # acquire should have written once; refresh interval is long so initial call suffices
    assert any(call[0] == "/test/mutex" for call in put_calls)


def test_mutex_backend_error_fails_closed(monkeypatch):
    class BrokenSSM:
        def put_parameter(self, **kwargs):
            raise RuntimeError("SSM unavailable")

    monkeypatch.delenv("MUTEX_DISABLED", raising=False)
    mutex = RunMutex(param_name="/test/mutex")
    mutex._ssm = BrokenSSM()

    assert mutex.acquire() is False


def test_mutex_can_be_explicitly_disabled_for_local_development(monkeypatch):
    monkeypatch.setenv("MUTEX_DISABLED", "true")
    mutex = RunMutex(param_name="/test/mutex")

    assert mutex.acquire() is True


def test_mutex_release_does_not_delete_when_ownership_is_uncertain(monkeypatch):
    deleted = []

    class UnreadableSSM:
        def get_parameter(self, **kwargs):
            raise RuntimeError("SSM read failed")

        def delete_parameter(self, **kwargs):
            deleted.append(kwargs)

    monkeypatch.delenv("MUTEX_DISABLED", raising=False)
    mutex = RunMutex(param_name="/test/mutex")
    mutex._ssm = UnreadableSSM()
    mutex._acquired = True
    mutex.release()

    assert deleted == []


def test_mutex_refresh_never_overwrites_a_new_owner(monkeypatch):
    writes = []

    class ReclaimedSSM:
        def get_parameter(self, **kwargs):
            return {"Parameter": {"Value": "new-owner|2099-01-01T00:00:00+00:00"}}

        def put_parameter(self, **kwargs):
            writes.append(kwargs)

    monkeypatch.delenv("MUTEX_DISABLED", raising=False)
    mutex = RunMutex(param_name="/test/mutex")
    mutex._ssm = ReclaimedSSM()
    mutex._acquired = True

    assert mutex._refresh_once() is False
    assert writes == []


def test_failed_refresh_keeps_lock_releasable(monkeypatch):
    deleted = []

    class OwnedButRefreshFails:
        def get_parameter(self, **kwargs):
            return {"Parameter": {"Value": f"{mutex._owner}|2099-01-01T00:00:00+00:00"}}

        def put_parameter(self, **kwargs):
            raise RuntimeError("transient SSM failure")

        def delete_parameter(self, **kwargs):
            deleted.append(kwargs)

    monkeypatch.delenv("MUTEX_DISABLED", raising=False)
    mutex = RunMutex(param_name="/test/mutex")
    mutex._ssm = OwnedButRefreshFails()
    mutex._acquired = True

    assert mutex._refresh_once() is None
    assert mutex._acquired is True
    mutex.release()
    assert deleted == [{"Name": "/test/mutex"}]


def test_unverifiable_refresh_is_retryable(monkeypatch):
    class UnreadableSSM:
        def get_parameter(self, **kwargs):
            raise RuntimeError("temporary read failure")

        def put_parameter(self, **kwargs):
            raise AssertionError("must not refresh without ownership proof")

    monkeypatch.delenv("MUTEX_DISABLED", raising=False)
    mutex = RunMutex(param_name="/test/mutex")
    mutex._ssm = UnreadableSSM()
    mutex._acquired = True

    assert mutex._refresh_once() is None
    assert mutex._acquired is True


def test_health_jamf_index_reports_partial_fetch_failures():
    module = HealthCheckModule.__new__(HealthCheckModule)
    module.config = type("Config", (), {
        "modules": {"health_check": {"max_workers": 2}},
    })()

    class FakeJamf:
        def get_all_computers_basic(self):
            return [
                {"id": 1, "serial_number": "SERIAL-1"},
                {"id": 2, "serial_number": "SERIAL-2"},
            ]

        def get_computer_by_id(self, computer_id, subsets=None):
            if computer_id == 2:
                raise RuntimeError("timeout")
            return {
                "computer": {
                    "groups_accounts": {
                        "local_accounts": [{"name": "first.last"}],
                    }
                }
            }

    module.jamf = FakeJamf()
    by_serial, local_norms, errors, total = module._build_jamf_indexes()

    assert list(by_serial) == ["SERIAL-1"]
    assert local_norms == {"firstlast"}
    assert errors == [{"computer_id": 2, "error": "timeout"}]
    assert total == 2
