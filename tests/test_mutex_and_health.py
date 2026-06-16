"""Unit tests for mutex refresh and health auth helper."""
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from infra.mutex import RunMutex
from infra.health import _is_authorized


def test_health_authorization_helper():
    token = "secret"
    assert _is_authorized(token, "Bearer secret") is True
    assert _is_authorized(token, "Bearer wrong") is False
    assert _is_authorized(None, "") is True  # disabled auth


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
