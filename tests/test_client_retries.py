"""Retry-count and terminal-diagnostic regressions for API clients."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from clients.azure import AzureClient
from clients.hibob import HiBobClient


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def request(self, *args, **kwargs):
        self.calls += 1
        return self.response


def test_azure_terminal_server_error_keeps_http_context(monkeypatch):
    client = AzureClient.__new__(AzureClient)
    response = SimpleNamespace(status_code=503, headers={}, text="upstream unavailable")
    client.session = FakeSession(response)
    client.max_retries = 2
    client.retry_delay = 0
    client.timeout = 1
    client._get_headers = lambda: {}
    monkeypatch.setattr("clients.azure.time.sleep", lambda delay: None)

    with pytest.raises(RuntimeError, match="HTTP 503: upstream unavailable"):
        client._request_with_retry("GET", "https://graph.invalid/users")

    assert client.session.calls == 2


def test_hibob_max_retries_is_total_attempt_count(monkeypatch):
    client = HiBobClient.__new__(HiBobClient)
    response = SimpleNamespace(status_code=503)
    client.session = FakeSession(response)
    client.api_base = "https://hibob.invalid/v1"
    client.max_retries = 2
    client.retry_delay = 0
    client.timeout = 1
    monkeypatch.setattr("clients.hibob.time.sleep", lambda delay: None)

    assert client._request("GET", "/people") is response
    assert client.session.calls == 2