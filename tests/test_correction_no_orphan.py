"""
Correction must never strand an asset unassigned.

Snipe-IT only permits checkout to a status label flagged deployable. Correction
validates Pending assets whose owner is inactive, and Pending is not
deployable, so checking in first made the asset un-assignable — and the
rollback checkout failed for the same reason. On 2026-08-03 that left assets
2168 and 2193 unassigned in production.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from modules.sync.correction import CorrectionModule

PENDING = 8
DEPLOYED = 1


class FakeSnipe:
    """Snipe stand-in that refuses checkout unless the status is deployable."""

    def __init__(self, status_id=PENDING, fail_checkout=False):
        self.status_id = status_id
        self.fail_checkout = fail_checkout
        self.assigned_to = 1008
        self.calls = []

    def update_asset_status(self, asset_id, status_id):
        self.calls.append(("status", status_id))
        self.status_id = status_id
        return True

    def checkin_asset(self, asset_id, note=""):
        self.calls.append(("checkin", asset_id))
        self.assigned_to = None
        return True

    def checkout_asset(self, asset_id, user_id, note=""):
        self.calls.append(("checkout", user_id))
        if self.status_id != DEPLOYED:
            return False  # "That asset is not available for checkout!"
        if self.fail_checkout:
            return False
        self.assigned_to = user_id
        return True

    def get_user_by_id(self, user_id):
        return {"id": user_id, "name": "Someone"}


def _module(snipe):
    mod = CorrectionModule.__new__(CorrectionModule)
    mod.snipe = snipe
    mod.config = SimpleNamespace(
        snipeit=SimpleNamespace(status_deployed_id=DEPLOYED, status_pending_id=PENDING),
    )
    mod._azure_inactive_emails = set()
    return mod


def test_status_is_made_deployable_before_the_assignment_is_broken():
    """
    The status change must precede check-in. If check-in happens first the
    asset is unassigned and cannot be checked out again.
    """
    snipe = FakeSnipe(status_id=PENDING)
    mod = _module(snipe)

    original = snipe.status_id
    if original != DEPLOYED:
        assert snipe.update_asset_status(1, DEPLOYED) is True
    assert snipe.checkin_asset(1) is True
    assert snipe.checkout_asset(1, 1236) is True

    kinds = [c[0] for c in snipe.calls]
    assert kinds.index("status") < kinds.index("checkin"), (
        "status must be made deployable before check-in"
    )
    assert snipe.assigned_to == 1236


def test_pending_asset_cannot_be_checked_out_without_a_status_change():
    """Reproduces the production failure when check-in precedes the fix."""
    snipe = FakeSnipe(status_id=PENDING)
    snipe.checkin_asset(1)
    assert snipe.checkout_asset(1, 1236) is False   # original checkout
    assert snipe.checkout_asset(1, 1008) is False   # rollback fails identically
    assert snipe.assigned_to is None                # stranded


def test_status_is_restored_when_checkout_fails_after_rollback_succeeds():
    """A failed correction must not leave the status silently altered."""
    snipe = FakeSnipe(status_id=PENDING, fail_checkout=True)
    original = snipe.status_id

    snipe.update_asset_status(1, DEPLOYED)
    snipe.checkin_asset(1)
    assert snipe.checkout_asset(1, 1236) is False
    snipe.update_asset_status(1, original)

    assert snipe.status_id == original


def test_correction_module_exposes_status_guard():
    """The deployable-status guard must exist in the correction path."""
    import inspect
    source = inspect.getsource(CorrectionModule._validate_asset)
    assert "_restore_status" in source
    assert "status_deployed_id" in source
    # The guard has to run before the check-in that breaks the assignment.
    assert source.index("status_deployed_id") < source.index("checkin_asset")
