"""Tests for AzureStartersModule — the module that creates real Snipe-IT
login credentials for new starters. Previously untested (code review,
2026-08-05): this is the one write path that generates and submits actual
passwords, so it gets priority coverage first.
"""
import sys
import string
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from modules.lifecycle.azure_starters import AzureStartersModule


def _make_module(dry_run=False, update_job_titles=True):
    """Build an AzureStartersModule without running __init__ (no real
    Azure/Snipe-IT clients constructed), matching the existing
    `Module.__new__(Module)` convention used across the test suite.
    """
    module = AzureStartersModule.__new__(AzureStartersModule)
    module.dry_run = dry_run
    module.update_job_titles = update_job_titles
    module._generate_password = lambda: "Fake-Pw1!"
    return module


class FakeSnipe:
    """Records create_user/update_user calls instead of hitting the network."""

    def __init__(self):
        self.created = []
        self.updated = []

    def create_user(self, user_data):
        self.created.append(user_data)
        return {"id": 999, **user_data}

    def update_user(self, user_id, fields):
        self.updated.append((user_id, fields))
        return True


# ---------------------------------------------------------------------------
# Username convention — the exact bug class this suite has hit twice now
# (SAML login broke on the Snipe-IT server because of a username/email
# mismatch; user_match.py separately created users with a different
# convention than this module). Pin the behavior down explicitly.
# ---------------------------------------------------------------------------

def test_create_new_user_username_is_email_prefix_not_full_email():
    module = _make_module()
    module.snipe = FakeSnipe()
    results = {"users_created": 0, "created_users": []}

    module._create_new_user(
        email="Jane.Doe@company.com",
        username="jane.doe",
        first_name="Jane",
        last_name="Doe",
        job_title="Engineer",
        results=results,
    )

    assert len(module.snipe.created) == 1
    sent = module.snipe.created[0]
    assert sent["username"] == "jane.doe"
    assert sent["email"] == "Jane.Doe@company.com"
    assert results["users_created"] == 1


def test_create_new_user_dry_run_does_not_call_snipe():
    module = _make_module(dry_run=True)
    module.snipe = FakeSnipe()
    results = {"users_created": 0, "created_users": []}

    module._create_new_user(
        email="jane.doe@company.com",
        username="jane.doe",
        first_name="Jane",
        last_name="Doe",
        job_title="Engineer",
        results=results,
    )

    assert module.snipe.created == []
    assert results["users_created"] == 1
    assert results["created_users"][0]["email"] == "jane.doe@company.com"


def test_create_new_user_missing_last_name_falls_back_to_first_name():
    """Snipe-IT requires a last name; azure_starters.py substitutes
    first_name when Azure has none (e.g. mononym or incomplete profile)."""
    module = _make_module()
    module.snipe = FakeSnipe()
    results = {"users_created": 0, "created_users": []}

    module._create_new_user(
        email="cher@company.com",
        username="cher",
        first_name="Cher",
        last_name="",
        job_title="",
        results=results,
    )

    sent = module.snipe.created[0]
    assert sent["last_name"] == "Cher"


def test_create_new_user_password_is_confirmed_and_never_logged_in_results():
    """password/password_confirmation must match, and results (which get
    printed in the run summary) must never carry the raw password."""
    module = _make_module()
    module.snipe = FakeSnipe()
    results = {"users_created": 0, "created_users": []}

    module._create_new_user(
        email="jane.doe@company.com",
        username="jane.doe",
        first_name="Jane",
        last_name="Doe",
        job_title="Engineer",
        results=results,
    )

    sent = module.snipe.created[0]
    assert sent["password"] == sent["password_confirmation"] == "Fake-Pw1!"
    assert "password" not in results["created_users"][0]


def test_create_new_user_snipe_failure_does_not_increment_created_count():
    module = _make_module()

    class FailingSnipe:
        def create_user(self, user_data):
            return None  # Snipe-IT rejected the write

    module.snipe = FailingSnipe()
    results = {"users_created": 0, "created_users": [], "errors": []}

    module._create_new_user(
        email="jane.doe@company.com",
        username="jane.doe",
        first_name="Jane",
        last_name="Doe",
        job_title="Engineer",
        results=results,
    )

    assert results["users_created"] == 0
    assert results["created_users"] == []
    assert results["errors"] == ["Create failed: Jane Doe"]


# ---------------------------------------------------------------------------
# _process_single_user routing: create vs. update vs. skip
# ---------------------------------------------------------------------------

def test_process_single_user_creates_when_no_existing_snipe_match():
    module = _make_module()
    module.snipe = FakeSnipe()
    azure_user = {
        "mail": "new.starter@company.com",
        "displayName": "New Starter",
        "givenName": "New",
        "surname": "Starter",
        "jobTitle": "Engineer",
        "accountEnabled": True,
    }
    results = {
        "users_created": 0, "users_updated": 0, "already_exists": 0,
        "skipped": 0, "skipped_former_employees": 0, "created_users": [],
        "updated_users": [],
    }

    module._process_single_user(azure_user, snipe_users_by_email={}, results=results)

    assert results["users_created"] == 1
    assert module.snipe.created[0]["username"] == "new.starter"


def test_process_single_user_skips_user_without_email():
    module = _make_module()
    module.snipe = FakeSnipe()
    azure_user = {"displayName": "No Email User", "accountEnabled": True}
    results = {
        "users_created": 0, "users_updated": 0, "already_exists": 0,
        "skipped": 0, "skipped_former_employees": 0, "created_users": [],
        "updated_users": [],
    }

    module._process_single_user(azure_user, snipe_users_by_email={}, results=results)

    assert results["skipped"] == 1
    assert module.snipe.created == []


def test_process_single_user_updates_job_title_for_existing_user():
    module = _make_module(update_job_titles=True)
    module.snipe = FakeSnipe()
    azure_user = {
        "mail": "existing@company.com",
        "displayName": "Existing User",
        "jobTitle": "Senior Engineer",
        "accountEnabled": True,
    }
    existing = {"id": 42, "jobtitle": "Engineer"}
    results = {
        "users_created": 0, "users_updated": 0, "already_exists": 0,
        "skipped": 0, "skipped_former_employees": 0, "created_users": [],
        "updated_users": [],
        "errors": [],
    }

    module._process_single_user(
        azure_user,
        snipe_users_by_email={"existing@company.com": existing},
        results=results,
    )

    assert module.snipe.created == []  # never creates a duplicate
    assert module.snipe.updated == [(42, {"jobtitle": "Senior Engineer"})]
    assert results["users_updated"] == 1


def test_process_single_user_skips_definite_former_employee():
    """A leave date in the past must never trigger account creation,
    even though former employees are otherwise still processed for
    pre-provisioning (see _is_former_employee's docstring)."""
    module = _make_module()
    module.snipe = FakeSnipe()
    azure_user = {
        "mail": "left.already@company.com",
        "displayName": "Left Already",
        "accountEnabled": False,
        "employeeLeaveDateTime": "2020-01-01T00:00:00Z",
    }
    results = {
        "users_created": 0, "users_updated": 0, "already_exists": 0,
        "skipped": 0, "skipped_former_employees": 0, "created_users": [],
        "updated_users": [],
    }

    module._process_single_user(azure_user, snipe_users_by_email={}, results=results)

    assert results["skipped_former_employees"] == 1
    assert module.snipe.created == []


# ---------------------------------------------------------------------------
# Password generator — CSPRNG usage and composition guarantees
# (SECURITY.md claims this; pin it down rather than trust the claim).
# ---------------------------------------------------------------------------

def test_password_generator_meets_composition_requirements():
    generate = AzureStartersModule._make_password_generator()
    for _ in range(20):
        pw = generate()
        assert len(pw) == 24
        assert any(c.isupper() for c in pw)
        assert any(c.islower() for c in pw)
        assert any(c.isdigit() for c in pw)
        assert any(c in "!@#$%^&*" for c in pw)
        assert all(c in string.ascii_letters + string.digits + "!@#$%^&*" for c in pw)


def test_password_generator_produces_distinct_passwords():
    generate = AzureStartersModule._make_password_generator()
    passwords = {generate() for _ in range(50)}
    assert len(passwords) == 50  # no collisions in 50 draws from a CSPRNG
