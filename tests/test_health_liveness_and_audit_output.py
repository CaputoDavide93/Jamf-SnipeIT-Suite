"""Liveness-probe isolation, AI-audit output readability, and Jamf token locking."""
import sys
import time
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from clients.jamf import JamfClient
from infra.health import HealthCheckServer
from infra.helpers import result_error_count
from modules.maintenance.ai_audit import AIAuditModule


# ---------------------------------------------------------------------------
# /healthz must stay a pure liveness signal
# ---------------------------------------------------------------------------

def test_aggregate_health_reflects_component_failure():
    server = HealthCheckServer()
    server.status.snipe_healthy = False
    assert server._get_status_dict()["status"] != "healthy"


def test_failed_runs_make_aggregate_status_unhealthy():
    """The condition that must NOT be allowed to kill the container."""
    server = HealthCheckServer()
    server.record_run(success=False, module_name="Cleanup")
    server.record_run(success=False, module_name="Leavers")
    assert server._get_status_dict()["status"] == "unhealthy"


def test_healthz_is_not_derived_from_run_outcomes():
    """
    /healthz is the ECS/Docker health check. It must not consult run state,
    or an early module reporting errors would have ECS kill a task that is
    still working through the rest of the chain.
    """
    import inspect
    from infra import health as health_module

    source = inspect.getsource(health_module.HealthCheckServer._create_handler)
    healthz_branch = source.split("'/healthz'", 1)[1].split("elif", 1)[0]
    assert "_get_status_dict" not in healthz_branch
    assert "200" in healthz_branch


# ---------------------------------------------------------------------------
# Tokenised prompts must still yield actionable internal reports
# ---------------------------------------------------------------------------

def test_findings_are_detokenized_for_internal_consumers():
    lookup = {"user-0001": "alice@example.com", "device-0001": "SERIAL123"}
    finding = {
        "severity": "high",
        "title": "user-0001 holds too many devices",
        "detail": "user-0001 has device-0001 deployed",
        "affected_count": 1,
    }
    restored = AIAuditModule._detokenize_finding(finding, lookup)
    assert restored["title"] == "alice@example.com holds too many devices"
    assert restored["detail"] == "alice@example.com has SERIAL123 deployed"
    assert restored["affected_count"] == 1


def test_detokenize_prefers_longest_token_match():
    """user-0001 must not be corrupted by a shorter user-1 token."""
    lookup = {"user-1": "bob@example.com", "user-10": "carol@example.com"}
    finding = {"detail": "user-10 and user-1 share a device"}
    restored = AIAuditModule._detokenize_finding(finding, lookup)
    assert restored["detail"] == "carol@example.com and bob@example.com share a device"


def test_detokenize_is_a_noop_when_pii_was_allowed():
    finding = {"detail": "alice@example.com has 3 assets"}
    assert AIAuditModule._detokenize_finding(finding, {}) == finding


# ---------------------------------------------------------------------------
# Failure conventions
# ---------------------------------------------------------------------------

def test_failures_key_counts_towards_error_total():
    """pending-reconciliation / jamf-location-cleanup report 'failures'."""
    assert result_error_count({"restored": 2, "failures": 3}) == 3
    assert result_error_count({"cleared": 1, "failures": 0}) == 0
    assert result_error_count({"errors": 1, "failures": 2}) == 3


# ---------------------------------------------------------------------------
# Jamf token acquisition is serialised for the threaded health scan
# ---------------------------------------------------------------------------

def test_concurrent_token_fetches_issue_one_http_request():
    """
    Eight threads (the health-check pool size) must trigger exactly one token
    request: the first thread fetches under the lock, the rest see the cache.
    """
    posts = []
    post_lock = threading.Lock()

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"token": "tok", "expires_in": 1800}

    class FakeSession:
        def post(self, url, **kwargs):
            with post_lock:
                posts.append(url)
            time.sleep(0.01)  # widen the race window
            return FakeResponse()

    client = JamfClient.__new__(JamfClient)
    client.base_url = "https://jamf.example.com"
    client.username = "u"
    client.password = "p"
    client.client_id = ""
    client.client_secret = ""
    client.timeout = 5
    client.session = FakeSession()
    client._token = None
    client._token_exp = 0
    client._token_lock = threading.Lock()

    results = []
    result_lock = threading.Lock()

    def grab():
        token = client._get_token()
        with result_lock:
            results.append(token)

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(posts) == 1, f"expected one token fetch, got {len(posts)}"
    assert results == ["tok"] * 8


def test_refresh_skips_when_another_thread_already_rotated():
    client = JamfClient.__new__(JamfClient)
    client._token = "fresh-token"
    client._token_exp = 9_999_999_999
    client._token_lock = threading.Lock()
    client._get_token_locked = lambda: (_ for _ in ()).throw(
        AssertionError("must not refetch when token already rotated")
    )

    assert client._refresh_token("stale-token") == "fresh-token"
