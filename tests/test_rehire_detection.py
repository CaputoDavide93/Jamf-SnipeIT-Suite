"""Tests for Rehire Detection classification and the shared leave-date helper."""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from infra.helpers import leave_date_passed
from modules.lifecycle.rehire_detection import RehireDetectionModule
from modules.lifecycle.azure_starters import AzureStartersModule
from modules.lifecycle.leavers import LeaversModule

PAST = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
FUTURE = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def test_leave_date_absent():
    assert leave_date_passed({}) is False
    assert leave_date_passed({"employeeLeaveDateTime": None}) is False


def test_leave_date_past_and_future():
    assert leave_date_passed({"employeeLeaveDateTime": PAST}) is True
    assert leave_date_passed({"employeeLeaveDateTime": FUTURE}) is False


def test_leave_date_z_suffix():
    assert leave_date_passed({"employeeLeaveDateTime": "2020-01-01T00:00:00Z"}) is True


def test_leave_date_invalid_respects_fail_safe_direction():
    bad = {"employeeLeaveDateTime": "not-a-date"}
    assert leave_date_passed(bad, default_on_invalid=False) is False
    assert leave_date_passed(bad, default_on_invalid=True) is True


# ---------------------------------------------------------------------------
# Rehire classification
# ---------------------------------------------------------------------------

def test_classify_no_azure_user_means_tag_is_correct():
    assert RehireDetectionModule._classify(None, set(), set()) is None


def test_classify_genuine_rehire():
    user = {"id": "u1", "accountEnabled": True}
    assert RehireDetectionModule._classify(user, set(), set()) == "rehire"


def test_classify_still_in_leavers_group_is_ambiguous():
    user = {"id": "u1", "accountEnabled": True}
    verdict = RehireDetectionModule._classify(user, {"u1"}, set())
    assert verdict == "ambiguous: still in leavers group"


def test_classify_still_in_disabled_group_is_ambiguous():
    user = {"id": "u1", "accountEnabled": True}
    verdict = RehireDetectionModule._classify(user, set(), {"u1"})
    assert verdict == "ambiguous: still in disabled group"


def test_classify_leave_date_passed_is_ambiguous():
    user = {"id": "u1", "accountEnabled": True, "employeeLeaveDateTime": PAST}
    verdict = RehireDetectionModule._classify(user, set(), set())
    assert verdict == "ambiguous: leave date passed"


def test_classify_future_leave_date_is_rehire():
    user = {"id": "u1", "accountEnabled": True, "employeeLeaveDateTime": FUTURE}
    assert RehireDetectionModule._classify(user, set(), set()) == "rehire"


def test_classify_unparseable_leave_date_fails_safe_to_ambiguous():
    """Never auto-restore on garbage data."""
    user = {"id": "u1", "accountEnabled": True, "employeeLeaveDateTime": "garbage"}
    verdict = RehireDetectionModule._classify(user, set(), set())
    assert verdict == "ambiguous: leave date passed"


def test_classify_hibob_active_confirms_rehire():
    user = {"id": "u1", "accountEnabled": True}
    verdict = RehireDetectionModule._classify(
        user, set(), set(), email="a@b.com", hibob_active={"a@b.com"}
    )
    assert verdict == "rehire"


def test_classify_hibob_inactive_is_ambiguous():
    """AAD says active but the HR source of truth does not — never touch."""
    user = {"id": "u1", "accountEnabled": True}
    verdict = RehireDetectionModule._classify(
        user, set(), set(), email="a@b.com", hibob_active={"other@b.com"}
    )
    assert verdict == "ambiguous: not active in HiBob (HR source of truth)"


def test_classify_no_hibob_data_skips_confirmation():
    """hibob_active=None means confirmation disabled/unconfigured — AAD-only."""
    user = {"id": "u1", "accountEnabled": True}
    verdict = RehireDetectionModule._classify(
        user, set(), set(), email="a@b.com", hibob_active=None
    )
    assert verdict == "rehire"


# ---------------------------------------------------------------------------
# Starters skip-guard: only a passed leave date skips, never accountEnabled
# ---------------------------------------------------------------------------

def test_starters_skips_former_employee():
    assert AzureStartersModule._is_former_employee(
        {"accountEnabled": False, "employeeLeaveDateTime": PAST}
    ) is True


def test_starters_does_not_skip_preprovisioned_disabled_account():
    """New hires are pre-provisioned with accountEnabled=false — must NOT skip."""
    assert AzureStartersModule._is_former_employee({"accountEnabled": False}) is False


def test_starters_does_not_skip_on_missing_accountEnabled():
    """Graph permission gap (accountEnabled=null) must not no-op the module."""
    assert AzureStartersModule._is_former_employee({"accountEnabled": None}) is False
    assert AzureStartersModule._is_former_employee({}) is False


def test_starters_does_not_skip_notice_period():
    assert AzureStartersModule._is_former_employee(
        {"accountEnabled": True, "employeeLeaveDateTime": FUTURE}
    ) is False


# ---------------------------------------------------------------------------
# Leavers and Starters stay mirrored via the shared helper
# ---------------------------------------------------------------------------

def test_leavers_and_rehire_agree_on_leave_dates():
    for value, expected_tag in ((PAST, True), (FUTURE, False)):
        user = {"accountEnabled": True, "employeeLeaveDateTime": value}
        assert LeaversModule._should_tag_disabled(user) is expected_tag
        # A user Leavers would tag must never classify as a plain rehire
        verdict = RehireDetectionModule._classify({"id": "x", **user}, set(), set())
        assert (verdict == "rehire") is (not expected_tag)
