"""Unit tests for Snipe-IT write verification, error scrubbing and mutex claims."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from clients.snipeit import SnipeITClient
from infra.mutex import RunMutex


# ---------------------------------------------------------------------------
# Snipe-IT returns HTTP 200 with a JSON error body on validation failures
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _client() -> SnipeITClient:
    return SnipeITClient.__new__(SnipeITClient)


def test_write_ok_rejects_200_with_error_status():
    c = _client()
    resp = _Resp(200, {"status": "error", "messages": "Asset is checked out"})
    assert c._write_ok(resp, "checkout_asset 1") is False


def test_write_ok_accepts_200_success():
    c = _client()
    assert c._write_ok(_Resp(200, {"status": "success"}), "update_asset 1") is True


def test_write_ok_accepts_200_with_non_json_body():
    """A 200 with an empty/non-JSON body has nothing contradicting success."""
    c = _client()
    assert c._write_ok(_Resp(200, None), "checkin_asset 1") is True


def test_write_ok_rejects_none_and_non_2xx():
    c = _client()
    assert c._write_ok(None, "update_asset 1") is False
    assert c._write_ok(_Resp(500, {"status": "error"}), "update_asset 1") is False


# ---------------------------------------------------------------------------
# Error bodies must stay diagnosable without leaking credentials
# ---------------------------------------------------------------------------

def test_safe_error_body_keeps_messages_and_redacts_secrets():
    c = _client()
    body = c._safe_error_body(
        _Resp(422, {"messages": {"serial": ["already taken"]}, "api_token": "sekrit"})
    )
    assert "already taken" in body
    assert "sekrit" not in body
    assert "[REDACTED]" in body


def test_safe_error_body_falls_back_to_text():
    c = _client()
    assert c._safe_error_body(_Resp(502, None, text="gateway down")) == "gateway down"


# ---------------------------------------------------------------------------
# Mutex must not let two runs hold the lock at once
# ---------------------------------------------------------------------------

class _AlreadyExists(Exception):
    pass


class _NotFound(Exception):
    pass


class FakeSSM:
    """Minimal SSM stand-in enforcing Overwrite=False semantics."""

    def __init__(self, store=None):
        self.store = dict(store or {})
        self.deleted = []
        self.exceptions = type(
            "E",
            (),
            {"ParameterAlreadyExists": _AlreadyExists, "ParameterNotFound": _NotFound},
        )

    def put_parameter(self, Name, Value, Type, Overwrite):
        if Name in self.store and not Overwrite:
            raise _AlreadyExists()
        self.store[Name] = Value

    def get_parameter(self, Name):
        if Name not in self.store:
            raise _NotFound()
        return {"Parameter": {"Value": self.store[Name]}}

    def delete_parameter(self, Name):
        self.deleted.append(Name)
        self.store.pop(Name, None)


def _mutex(ssm) -> RunMutex:
    m = RunMutex(param_name="/test/mutex")
    m._ssm = ssm
    return m


def test_second_acquirer_is_refused_while_lock_is_live():
    ssm = FakeSSM()
    first = _mutex(ssm)
    assert first.acquire() is True

    second = _mutex(ssm)
    second._owner = "other-host-999"
    assert second.acquire() is False


def test_expired_lock_is_reclaimed():
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    ssm = FakeSSM({"/test/mutex": f"dead-host-1|{past}"})

    m = _mutex(ssm)
    assert m.acquire() is True
    assert ssm.store["/test/mutex"].startswith(m._owner)


def test_malformed_lock_value_is_treated_as_expired():
    ssm = FakeSSM({"/test/mutex": "garbage-no-pipe"})
    m = _mutex(ssm)
    assert m.acquire() is True


def test_release_does_not_delete_a_lock_owned_by_someone_else():
    """If our TTL lapsed and another run reclaimed, we must not strip its lock."""
    ssm = FakeSSM()
    m = _mutex(ssm)
    assert m.acquire() is True

    future = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    ssm.store["/test/mutex"] = f"someone-else-42|{future}"

    m.release()
    assert ssm.deleted == []
    assert ssm.store["/test/mutex"].startswith("someone-else-42")


def test_release_deletes_our_own_lock():
    ssm = FakeSSM()
    m = _mutex(ssm)
    assert m.acquire() is True
    m.release()
    assert "/test/mutex" not in ssm.store
