"""Unit tests for UserMatcher priority chain."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from matching.user_matcher import UserMatcher, pick_primary_local_identity


# ----- fixtures -----

SNIPE_USERS = [
    {"id": 1, "name": "Peter Grant", "email": "peter.grant@company.com", "username": "peter.grant"},
    {"id": 2, "name": "Paul Grant", "email": "paul.grant@company.com", "username": "paul.grant"},
    {"id": 3, "name": "Jane Sommers", "email": "jane.sommers@company.com", "username": "jane.winters"},  # old username, new name
    {"id": 4, "name": "Alex Morgan", "email": "alex.morgan@company.com", "username": "alex.morgan"},
    {"id": 5, "name": "Alex Morgan", "email": "alex.morgan1@company.com", "username": "alex.morgan1"},
    {"id": 6, "name": "[Disabled] Chris Hale", "email": "chris.hale@company.com", "username": "chris.hale"},
    {"id": 7, "name": "Sam Sample", "email": "sam.sample@company.com", "username": "sam.sample"},
]


def make_matcher(overrides=None):
    return UserMatcher(
        users=SNIPE_USERS,
        email_domain="company.com",
        overrides=overrides,
    )


# ----- priority 0: override -----

def test_override_wins_everything():
    m = make_matcher(overrides={
        "chris": {"snipe_user_id": 7, "snipe_user_name": "Sam", "reason": "reassigned"},
    })
    match, _ = m.best_match(full_name_hint="Chris", username="chris")
    assert match["id"] == 7  # override -> Sam, not Chris Hale


def test_override_normalised_key():
    """Override key should be case/separator insensitive."""
    m = make_matcher(overrides={
        "mattpersonal": {"snipe_user_id": 1, "snipe_user_name": "Peter Grant", "reason": "test"},
    })
    for variant in ["matt-personal", "matt.personal", "MATTPERSONAL", "matt_personal"]:
        match, _ = m.best_match(username=variant)
        assert match is not None, f"Variant {variant} should match override"
        assert match["id"] == 1


# ----- priority 1: full name -----

def test_full_name_exact():
    m = make_matcher()
    match, _ = m.best_match(full_name_hint="Peter Grant", username="peter.grant")
    assert match["id"] == 1


def test_full_name_ambiguous_disambiguate_by_email_prefix():
    """Two 'Alex Morgan' — use username to pick the right email."""
    m = make_matcher()
    # Username alexmorgan matches email prefix alex.morgan → pick id=4
    match, _ = m.best_match(full_name_hint="Alex Morgan", username="alexmorgan")
    assert match["id"] == 4

    # Username alexmorgan1 matches id=5's email prefix
    match, _ = m.best_match(full_name_hint="Alex Morgan", username="alexmorgan1")
    assert match["id"] == 5


# ----- priority 4b: normalised username (surname change) -----

def test_normalised_username_matches_across_surname_change():
    """Local account janewinters matches Snipe-IT user with username jane.winters
    even though Snipe-IT name is now 'Jane Sommers'."""
    m = make_matcher()
    # full_name_hint = "Jane Winters" (from Jamf local)
    # Snipe-IT has no name "Jane Winters"; full-name match fails
    # BUT: username jane.winters exists → normalised-username hit
    match, _ = m.best_match(full_name_hint="Jane Winters", username="janewinters")
    assert match["id"] == 3
    assert match["name"] == "Jane Sommers"


# ----- priority 1 disambiguation vs disabled -----

def test_disabled_users_not_filtered_out_of_pool():
    """[Disabled] users are in the pool — matching shouldn't ignore them
    because Correction may need to detect 'was assigned to disabled user'."""
    m = make_matcher()
    match, _ = m.best_match(full_name_hint="Chris Hale", username="chris.hale")
    assert match is not None
    assert match["name"].startswith("[Disabled]")


# ----- pick_primary_local_identity -----

def test_pick_primary_skips_system_accounts():
    local = [
        {"name": "root", "realname": "root"},
        {"name": "admin", "realname": "Admin"},
        {"name": "jdoe", "realname": "John Doe"},
        {"name": "xdesign", "realname": "xdesign"},
    ]
    uname, fullname, _ = pick_primary_local_identity(local)
    assert uname == "jdoe"
    assert fullname == "John Doe"


def test_pick_primary_prefers_person_name():
    """Higher score = person-like realname."""
    local = [
        {"name": "user1", "realname": "Generic"},
        {"name": "user2", "realname": "Jane Smith"},  # 2-word name = higher score
    ]
    uname, fullname, _ = pick_primary_local_identity(local)
    assert uname == "user2"


def test_pick_primary_ignores_config_skip():
    local = [
        {"name": "shared", "realname": "Shared"},
        {"name": "jdoe", "realname": "John Doe"},
    ]
    uname, fullname, _ = pick_primary_local_identity(local, skip_usernames=["shared"])
    assert uname == "jdoe"


def test_pick_primary_empty():
    assert pick_primary_local_identity([]) == (None, None, None)
    assert pick_primary_local_identity([{"name": "admin"}]) == (None, None, None)


def test_pick_primary_strips_email_domain():
    """Local account 'lucy.grey@acme.com' -> username 'lucy.grey'."""
    local = [{"name": "lucy.grey@acme.com", "realname": "Lucy Grey"}]
    uname, fullname, _ = pick_primary_local_identity(local)
    assert uname == "lucy.grey"
    assert fullname == "Lucy Grey"


def test_best_match_strips_email_username():
    """best_match called with 'lucy.grey@acme.com' should strip
    domain before matching (prevents fuzzy from matching 'Acme' org)."""
    USERS = [
        {"id": 1, "name": "Lucy Grey", "email": "lucy.grey@company.com", "username": "lucy.grey"},
        {"id": 2, "name": "Acme Receivables", "email": "receivables@company.com", "username": "receivables"},
    ]
    m = UserMatcher(users=USERS, email_domain="company.com")
    match, debug = m.best_match(
        full_name_hint="Lucy.grey@acme.com",
        username="lucy.grey@acme.com",
    )
    assert match is not None
    assert match["id"] == 1
    # Should hit email/email-prefix, not fuzzy
    assert not debug.get("rejected_reason")
