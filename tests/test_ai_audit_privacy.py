"""Privacy regressions for external AI audit payloads."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from modules.maintenance.ai_audit import AIAuditModule


class FakeMessages:
    def __init__(self):
        self.prompt = ""

    def create(self, **kwargs):
        self.prompt = kwargs["messages"][0]["content"]
        block = SimpleNamespace(type="text", text="[]")
        return SimpleNamespace(content=[block])


def test_ai_audit_tokenizes_external_identifiers_by_default():
    module = AIAuditModule.__new__(AIAuditModule)
    module.config = SimpleNamespace(modules={"ai_audit": {"allow_external_pii": False}})
    messages = FakeMessages()
    module._llm = SimpleNamespace(messages=messages)

    profiles = [{
        "snipe_id": 42,
        "name": "Alice Secret",
        "email": "alice.secret@example.com",
        "is_disabled_azure": True,
        "is_leaver_azure": True,
        "is_disabled_snipe": False,
        "asset_count": 3,
        "snipe_assets": [{"serial": "ASSET-SECRET", "status": "Deployed"}],
    }]
    data = {
        "jamf_devices": [{"id": 999, "serial_number": "JAMF-SECRET"}],
        "snipe_assets": [{
            "id": 100,
            "serial": "ASSET-SECRET",
            "name": "Alice MacBook",
            "assigned_to": None,
            "status_label": {"name": "Deployed"},
        }],
        "azure_disabled": [{}],
        "azure_leavers": [{}],
    }

    assert module._run_ai_analysis(profiles, data) == []
    prompt = messages.prompt
    for secret in (
        "Alice Secret",
        "alice.secret@example.com",
        "ASSET-SECRET",
        "JAMF-SECRET",
        "Alice MacBook",
        '"id": 999',
    ):
        assert secret not in prompt
    assert "user-0001" in prompt
    assert "device-0001" in prompt


def test_missing_llm_is_a_skip_not_a_run_failure():
    """
    An absent AI_API_KEY is a deployment state, not an error: returning an
    "error" key made the whole housekeeping run-group exit non-zero.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from infra.helpers import result_error_count

    module = AIAuditModule.__new__(AIAuditModule)
    module._llm = None

    results = module.run()
    assert results == {"skipped": True, "reason": "llm_not_configured"}
    assert result_error_count(results) == 0
