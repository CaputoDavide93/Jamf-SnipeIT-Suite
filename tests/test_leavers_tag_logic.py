"""Tests for [Disabled] tag refinement."""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from modules.lifecycle.leavers import LeaversModule


def test_should_tag_disabled_accountEnabled_false():
    assert LeaversModule._should_tag_disabled({"accountEnabled": False}) is True


def test_should_not_tag_still_active_no_leave_date():
    assert LeaversModule._should_tag_disabled({"accountEnabled": True}) is False


def test_should_tag_leave_date_passed():
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert LeaversModule._should_tag_disabled({
        "accountEnabled": True,
        "employeeLeaveDateTime": past,
    }) is True


def test_should_not_tag_future_leave_date():
    """User still working notice period — don't tag yet."""
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    assert LeaversModule._should_tag_disabled({
        "accountEnabled": True,
        "employeeLeaveDateTime": future,
    }) is False


def test_should_handle_azure_Z_suffix():
    past = "2020-01-01T00:00:00Z"
    assert LeaversModule._should_tag_disabled({
        "accountEnabled": True,
        "employeeLeaveDateTime": past,
    }) is True


def test_should_handle_invalid_date():
    assert LeaversModule._should_tag_disabled({
        "accountEnabled": True,
        "employeeLeaveDateTime": "not-a-date",
    }) is False
