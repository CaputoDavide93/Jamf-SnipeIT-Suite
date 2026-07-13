"""Regression tests for automated assignment and lifecycle safety rules."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from matching.user_matcher import can_auto_reassign
from modules.lifecycle.leavers import LeaversModule
from modules.lifecycle.rehire_detection import RehireDetectionModule
from modules.maintenance.cleanup import CleanupModule
from modules.sync.correction import CorrectionModule
from modules.sync.model_sync import ModelSyncModule
from modules.sync.user_match import UserMatchModule


class FakeAudit:
    def __init__(self):
        self.rows = []

    def write(self, **kwargs):
        self.rows.append(kwargs)


def test_shared_reassignment_policy():
    assert can_auto_reassign(
        "email=user@example.com",
        current_inactive=False,
        target_inactive=False,
    )
    assert can_auto_reassign(
        "ai_resolved (id=2)",
        current_inactive=True,
        target_inactive=False,
    )
    assert not can_auto_reassign(
        "ai_resolved (id=2)",
        current_inactive=False,
        target_inactive=False,
    )
    assert not can_auto_reassign(
        "fuzzy",
        current_inactive=True,
        target_inactive=False,
    )
    assert not can_auto_reassign(
        "email=user@example.com",
        current_inactive=False,
        target_inactive=True,
    )


def test_leavers_does_not_touch_active_group_member():
    module = LeaversModule.__new__(LeaversModule)

    class FakeSnipe:
        def find_user_by_email(self, email):
            return {"id": 10, "first_name": "Active", "email": email}

        def get_user_assets(self, user_id):
            raise AssertionError("active user assets must not be fetched")

    module.snipe = FakeSnipe()
    results = {
        "matched_users": 0,
        "updated_assets": 0,
        "updated_user_names": 0,
        "errors": [],
    }

    module._process_single_user(
        {
            "id": "aad-1",
            "displayName": "Active User",
            "mail": "active@example.com",
            "accountEnabled": True,
        },
        "leavers",
        False,
        8,
        results,
    )

    assert results["updated_assets"] == 0
    assert results["updated_user_names"] == 0
    assert results["errors"] == []


def test_hibob_outage_never_authorizes_rehire():
    verdict = RehireDetectionModule._classify(
        {"id": "aad-1", "accountEnabled": True},
        set(),
        set(),
        email="user@example.com",
        hibob_unavailable=True,
    )
    assert verdict == "ambiguous: HiBob unavailable; HR confirmation required"


def test_rehire_asset_restore_requires_fresh_assignment_verification():
    module = RehireDetectionModule.__new__(RehireDetectionModule)
    module.config = SimpleNamespace(
        snipeit=SimpleNamespace(status_pending_id=8, status_deployed_id=1)
    )

    class FakeSnipe:
        def get_user_assets(self, user_id):
            return [{"id": 5, "name": "Mac", "status_label": {"id": 8}}]

        def get_asset_by_id(self, asset_id):
            return None

    module.snipe = FakeSnipe()
    assert module._restore_pending_assets({"id": 10}, dry_run=True) == (0, 1)


def test_user_creation_dry_run_never_calls_create_user():
    module = UserMatchModule.__new__(UserMatchModule)
    azure_user = {
        "id": "aad-1",
        "accountEnabled": True,
        "displayName": "New User",
        "givenName": "New",
        "surname": "User",
        "mail": "new.user@example.com",
        "userPrincipalName": "new.user@example.com",
    }
    module._azure_users_by_prefix = {"newuser": azure_user}
    module._azure_users_by_upn = {"new.user@example.com": azure_user}
    module._dry_run_created_users_by_email = {}

    class FakeSnipe:
        def find_user_by_email(self, email):
            return None

        def create_user(self, data):
            raise AssertionError("dry run must not create a Snipe-IT user")

    module.snipe = FakeSnipe()
    result = module._try_create_from_azure(
        "newuser",
        "New User",
        "SERIAL1",
        "MAC1",
        dry_run=True,
    )
    assert result["_dry_run_created"] is True
    assert result["email"] == "new.user@example.com"


def test_jamf_update_is_skipped_when_values_are_identical():
    assert not UserMatchModule._jamf_update_needed(
        {
            "username": "user",
            "real_name": "User Name",
            "email_address": "user@example.com",
            "position": "Engineer",
        },
        [{"name": "SnipeIT_Asset_ID", "value": "123"}],
        username="user",
        realname="User Name",
        email="user@example.com",
        position="Engineer",
        ea_name="SnipeIT_Asset_ID",
        ea_value="123",
    )


def test_jamf_update_detects_changed_extension_attribute():
    assert UserMatchModule._jamf_update_needed(
        {},
        [{"name": "SnipeIT_Asset_ID", "value": "122"}],
        username="",
        realname="",
        email="",
        position="",
        ea_name="SnipeIT_Asset_ID",
        ea_value="123",
    )


def test_successful_correction_does_not_enter_rollback_path():
    module = CorrectionModule.__new__(CorrectionModule)
    module.config = SimpleNamespace(
        matching=SimpleNamespace(skip_usernames=[]),
        snipeit=SimpleNamespace(status_pending_id=8, status_deployed_id=1),
    )
    module._jamf_cache = {}
    module.jamf = SimpleNamespace(
        get_computer_by_serial=lambda serial: {
            "groups_accounts": {
                "local_accounts": [
                    {"name": "new.user", "realname": "New User", "uid": "501"}
                ]
            },
            "location": {},
        }
    )

    class FakeMatcher:
        def best_match(self, **kwargs):
            return (
                {"id": 2, "name": "New User", "email": "new@example.com"},
                {"exact_hit_reason": "email=new@example.com"},
            )

    module._get_user_matcher = lambda: FakeMatcher()
    module._is_inactive_user = lambda user: False

    class FakeSnipe:
        def checkin_asset(self, asset_id, note=""):
            return True

        def checkout_asset(self, asset_id, user_id, note=""):
            assert user_id == 2
            return True

        def get_user_by_id(self, user_id):
            return {"id": user_id, "name": "Old User"}

    module.snipe = FakeSnipe()
    results = {
        "correct_assignments": 0,
        "mismatches_found": 0,
        "corrections_made": 0,
        "no_jamf_device": 0,
        "no_fresh_match": 0,
        "manual_review": 0,
        "corrections_planned": 0,
        "errors": 0,
        "details": [],
    }
    module._validate_asset(
        {
            "id": 7,
            "serial": "SERIAL1",
            "name": "Mac",
            "status_label": {"id": 1},
            "assigned_to": {"id": 1, "name": "Old User"},
        },
        current_uid=1,
        dry_run=False,
        results=results,
        audit=FakeAudit(),
    )
    assert results["corrections_made"] == 1
    assert results["errors"] == 0


def test_cleanup_only_groups_duplicate_emails():
    module = CleanupModule.__new__(CleanupModule)
    users = [
        {"id": 1, "name": "Same Name", "email": "one@example.com"},
        {"id": 2, "name": "Same Name", "email": "two@example.com"},
        {"id": 3, "name": "Other", "email": "one@example.com"},
    ]
    groups = module.find_duplicates(users)
    assert [[user["id"] for user in group] for group in groups] == [[1, 3]]


def test_cleanup_does_not_delete_user_after_failed_asset_transfer(tmp_path):
    module = CleanupModule.__new__(CleanupModule)
    module.config = SimpleNamespace(
        logging=SimpleNamespace(dir=str(tmp_path), audit_csv=False)
    )

    class FakeSnipe:
        delete_called = False

        def get_all_users(self):
            return [
                {
                    "id": 2,
                    "name": "Keeper",
                    "email": "same@example.com",
                    "assets_count": 2,
                },
                {
                    "id": 1,
                    "name": "Loser",
                    "email": "same@example.com",
                    "assets_count": 1,
                },
            ]

        def get_user_assets(self, user_id):
            return [{"id": 7, "name": "Mac"}]

        def get_asset_by_id(self, asset_id):
            return {"id": asset_id, "assigned_to": {"id": 1}}

        @staticmethod
        def get_assigned_user_id(asset):
            return asset["assigned_to"]["id"]

        def checkin_asset(self, asset_id, note=""):
            return True

        def checkout_asset(self, asset_id, user_id, note=""):
            return user_id == 1

        def _request(self, *args, **kwargs):
            self.delete_called = True
            raise AssertionError("loser must not be deleted")

    module.snipe = FakeSnipe()
    results = module.run(dry_run=False)
    assert module.snipe.delete_called is False
    assert results["users_deleted"] == 0
    assert results["users_merged"] == 0
    assert results["errors"] == 1


def test_model_provisioning_uses_report_models_not_report_keys():
    module = ModelSyncModule.__new__(ModelSyncModule)
    module.auto_create_models = True
    module.auto_create_manufacturers = True
    module.default_category_id = 1
    module._model_map = {}
    module._manufacturer_map = {"apple": 1}
    module.check_models = lambda: {
        "total_jamf_models": 1,
        "missing_models": ["MacBook Pro"],
        "existing_models": [],
    }
    module._get_model_map = lambda: module._model_map
    module._get_manufacturer_map = lambda: module._manufacturer_map

    results = module.provision_models(dry_run=True)
    assert results["models_checked"] == 1
    assert results["models_created"] == 1
    assert "macbook pro" in module._model_map
    assert "missing_models" not in module._model_map


def _pending_asset(asset_id, serial, owner_id, owner_name, owner_email):
    return {
        "id": asset_id,
        "serial": serial,
        "status_label": {"id": 8, "name": "Pending"},
        "assigned_to": {"id": owner_id, "name": owner_name, "email": owner_email},
    }


def _build_pending_module(assets, active_emails, update_ok=True):
    from modules.maintenance.pending_reconciliation import PendingReconciliationModule
    module = PendingReconciliationModule.__new__(PendingReconciliationModule)
    module.config = SimpleNamespace(
        snipeit=SimpleNamespace(status_pending_id=8, status_deployed_id=1)
    )

    class FakeSnipe:
        def __init__(self):
            self.status_updates = []

        def get_all_assets(self):
            return assets

        def get_asset_by_id(self, asset_id):
            return next((a for a in assets if a["id"] == asset_id), None)

        def get_assigned_user_id(self, asset):
            at = asset.get("assigned_to") or {}
            return int(at["id"]) if at.get("id") else None

        def update_asset_status(self, asset_id, status_id):
            self.status_updates.append((asset_id, status_id))
            return update_ok

    class FakeAzure:
        def get_all_active_users(self):
            return [{"mail": e} for e in active_emails]

    module.snipe = FakeSnipe()
    module.azure = FakeAzure()
    module.slack = None
    return module


def test_pending_recon_restores_active_owner():
    assets = [_pending_asset(5, "ABC123", 10, "Active User", "active@example.com")]
    module = _build_pending_module(assets, {"active@example.com"})
    results = module.run(dry_run=False)
    assert results["restored"] == 1
    assert module.snipe.status_updates == [(5, 1)]


def test_pending_recon_skips_disabled_owner():
    assets = [_pending_asset(6, "DEF456", 11, "[Disabled] Gone User", "gone@example.com")]
    module = _build_pending_module(assets, {"gone@example.com"})
    results = module.run(dry_run=False)
    assert results["restored"] == 0
    assert results["skipped_disabled"] == 1
    assert module.snipe.status_updates == []


def test_pending_recon_skips_owner_not_active_in_azure():
    assets = [_pending_asset(7, "GHI789", 12, "Maybe Leaver", "maybe@example.com")]
    module = _build_pending_module(assets, set())  # nobody active in AAD
    results = module.run(dry_run=False)
    assert results["restored"] == 0
    assert results["skipped_unconfirmed"] == 1
    assert module.snipe.status_updates == []


def test_pending_recon_dry_run_never_writes():
    assets = [_pending_asset(8, "JKL012", 13, "Active User", "active@example.com")]
    module = _build_pending_module(assets, {"active@example.com"})
    results = module.run(dry_run=True)
    assert results["candidates"] == 1
    assert module.snipe.status_updates == []
