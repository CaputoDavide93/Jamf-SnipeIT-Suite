"""
Jamf-SnipeIT Suite - User Matching Utilities
Fuzzy matching algorithms and the UserMatcher class.
"""
import logging
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# String-similarity helpers
# =============================================================================

def longest_common_subsequence(s1: str, s2: str) -> int:
    """Length of the longest common subsequence (space-optimized DP)."""
    m, n = len(s1), len(s2)
    if m == 0 or n == 0:
        return 0

    prev = [0] * (n + 1)
    curr = [0] * (n + 1)

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i - 1] == s2[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev, curr = curr, prev

    return prev[n]


def char_overlap(s1: str, s2: str) -> int:
    """Number of overlapping characters (bag intersection)."""
    c1 = Counter(s1.lower())
    c2 = Counter(s2.lower())
    return sum((c1 & c2).values())


def bigram_dice_coefficient(s1: str, s2: str) -> float:
    """Dice coefficient based on character bigrams (0.0 – 1.0)."""
    def _bigrams(s: str) -> set:
        s = s.lower().replace(" ", "")
        return set(s[i:i + 2] for i in range(len(s) - 1)) if len(s) > 1 else set()

    b1 = _bigrams(s1)
    b2 = _bigrams(s2)
    if not b1 or not b2:
        return 0.0
    return (2.0 * len(b1 & b2)) / (len(b1) + len(b2))


def normalize_name(name: str) -> str:
    """Normalize a name for comparison (lowercase, strip disabled tags)."""
    if not name:
        return ""
    normalized = name.lower().strip()
    for noise in ["[disabled]", "(disabled)", "-disabled"]:
        normalized = normalized.replace(noise, "")
    return normalized.strip()


# =============================================================================
# UserMatcher
# =============================================================================

class UserMatcher:
    """Match users between systems using fuzzy matching."""

    def __init__(
        self,
        users: List[Dict[str, Any]],
        email_domain: str = "",
        min_score: float = 14,
        weight_lcs: float = 1.0,
        weight_char_overlap: float = 0.3,
        weight_bigram_dice: float = 2.0,
        use_bigram_dice: bool = True,
        ai_resolver=None,
    ):
        self.email_domain = email_domain.lower().lstrip("@")
        self.ai_resolver = ai_resolver

        # Collect ambiguous / rejected matches so callers can report them
        self.warnings: List[Dict[str, Any]] = []
        self.min_score = min_score
        self.weight_lcs = weight_lcs
        self.weight_char_overlap = weight_char_overlap
        self.weight_bigram_dice = weight_bigram_dice
        self.use_bigram_dice = use_bigram_dice

        # Include ALL users (including [Disabled] — their machines may still be in stock)
        self.users = list(users)
        _disabled = sum(1 for u in users if (u.get("name") or "").strip().startswith("[Disabled]"))
        if _disabled:
            logger.debug(f"UserMatcher: {_disabled} [Disabled] users included in matching pool")

        # Build lookup indexes
        self._by_email: Dict[str, Dict] = {}
        self._by_username: Dict[str, Dict] = {}
        self._by_username_norm: Dict[str, List[Dict]] = {}  # normalised username lookup
        self._by_name: Dict[str, List[Dict]] = {}
        self._by_email_prefix: Dict[str, List[Dict]] = {}

        for user in self.users:
            email = (user.get("email") or "").lower().strip()
            username = (user.get("username") or "").lower().strip()
            name = normalize_name(user.get("name") or "")  # strips [Disabled] tag

            if email:
                self._by_email[email] = user
                prefix = email.split("@")[0]
                prefix_norm = prefix.replace(".", "").replace("-", "").replace("_", "")
                if prefix_norm:
                    self._by_email_prefix.setdefault(prefix_norm, []).append(user)
            if username:
                self._by_username[username] = user
                # Normalised username (strip @domain, dots, dashes, underscores)
                uname_clean = username.split("@")[0].replace(".", "").replace("-", "").replace("_", "")
                if uname_clean:
                    self._by_username_norm.setdefault(uname_clean, []).append(user)
            if name:
                self._by_name.setdefault(name, []).append(user)

    # ----- exact lookups -----

    def find_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        return self._by_email.get(email.lower().strip())

    def find_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        return self._by_username.get(username.lower().strip())

    def find_by_name(self, full_name: str, username_hint: str = "") -> Optional[Dict[str, Any]]:
        matches = self._by_name.get(normalize_name(full_name), [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # Try to disambiguate using the Jamf username as email prefix
            # e.g. two "Ivaylo Dimitrov" but username "ivaylodimitrov" matches
            # ivaylo.dimitrov@ (not ivaylo.dimitrov1@)
            if username_hint:
                hint_norm = username_hint.lower().replace(".", "").replace("-", "").replace("_", "")
                for m in matches:
                    m_email = (m.get("email") or "").lower()
                    m_prefix = m_email.split("@")[0].replace(".", "").replace("-", "").replace("_", "")
                    if m_prefix == hint_norm:
                        logger.debug(f"Disambiguated '{full_name}' via email prefix: {m.get('email')}")
                        return m

            details = [
                f"{m.get('name', '?')} (id={m.get('id')}, email={m.get('email', '?')})"
                for m in matches
            ]
            logger.warning(f"Ambiguous name match for '{full_name}': {len(matches)} users")
            self.warnings.append({
                "type": "ambiguous_name",
                "query": full_name,
                "count": len(matches),
                "candidates": details,
            })
            return None
        return None

    def find_by_email_prefix(self, username: str) -> Optional[Dict[str, Any]]:
        norm = username.lower().strip().replace(".", "").replace("-", "").replace("_", "")
        matches = self._by_email_prefix.get(norm, [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            details = [
                f"{m.get('name', '?')} (id={m.get('id')}, email={m.get('email', '?')})"
                for m in matches
            ]
            logger.warning(f"Ambiguous email prefix match for '{username}': {len(matches)} users")
            self.warnings.append({
                "type": "ambiguous_email_prefix",
                "query": username,
                "count": len(matches),
                "candidates": details,
            })
            return None
        return None

    def find_by_username_normalized(self, username: str) -> Optional[Dict[str, Any]]:
        """Find user by normalised username (strip @domain, dots, dashes).
        Catches cases like janewinters -> jane.winters@company.com
        even when the user changed their surname."""
        norm = username.lower().strip().replace(".", "").replace("-", "").replace("_", "")
        matches = self._by_username_norm.get(norm, [])
        if len(matches) == 1:
            return matches[0]
        return None  # ambiguous or not found

    # ----- best match -----

    def best_match(
        self,
        full_name_hint: str = "",
        username: str = "",
        original_email: str = "",
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """
        Priority: full name → email → email prefix → username → fuzzy.
        Full name from Jamf local user accounts is the most accurate signal.
        Returns (user | None, debug_info).
        """
        debug_info: Dict[str, Any] = {"exact_hit_reason": None, "top_candidates": []}

        # PRIORITY 1: exact full name (from Jamf local user — most accurate)
        # Pass username as hint to disambiguate same-name users via email
        if full_name_hint and " " in full_name_hint.strip():
            exact = self.find_by_name(full_name_hint, username_hint=username)
            if exact:
                debug_info["exact_hit_reason"] = f"full_name={full_name_hint}"
                return exact, debug_info

        # PRIORITY 2: exact email — prefer the original Jamf location email
        # (e.g. "jane.doe@company.com") over reconstructing from the
        # dot-stripped username ("janedoe@company.com") which never matches.
        guessed_email = ""
        if original_email:
            guessed_email = original_email.lower().strip()
        elif username and self.email_domain:
            guessed_email = f"{username.lower().strip()}@{self.email_domain}"

        if guessed_email:
            exact = self.find_by_email(guessed_email)
            if exact:
                if full_name_hint and not self._names_compatible(full_name_hint, exact.get("name", "")):
                    logger.warning(f"Email match {guessed_email} → '{exact.get('name')}' incompatible with '{full_name_hint}'")
                else:
                    debug_info["exact_hit_reason"] = f"email={guessed_email}"
                    return exact, debug_info

        # PRIORITY 3: email prefix
        if username:
            prefix_match = self.find_by_email_prefix(username)
            if prefix_match:
                if full_name_hint and not self._names_compatible(full_name_hint, prefix_match.get("name", "")):
                    pass  # skip
                else:
                    debug_info["exact_hit_reason"] = f"email_prefix={username}"
                    return prefix_match, debug_info

        # PRIORITY 3b: single-word full name (less reliable, try after email)
        if full_name_hint and " " not in full_name_hint.strip():
            exact = self.find_by_name(full_name_hint, username_hint=username)
            if exact:
                debug_info["exact_hit_reason"] = f"full_name={full_name_hint}"
                return exact, debug_info

        # PRIORITY 4: exact username
        if username:
            exact = self.find_by_username(username)
            if exact:
                if full_name_hint and not self._names_compatible(full_name_hint, exact.get("name", "")):
                    logger.warning(f"Username match '{username}' → '{exact.get('name')}' incompatible with '{full_name_hint}'")
                else:
                    debug_info["exact_hit_reason"] = f"username={username}"
                    return exact, debug_info

        # PRIORITY 4b: normalised username (catches surname changes, e.g.
        # Jamf local account "janewinters" -> Snipe-IT user "jane.winters@company.com"
        # even though Snipe-IT name is now "Jane Sommers")
        if username:
            norm_match = self.find_by_username_normalized(username)
            if norm_match:
                debug_info["exact_hit_reason"] = f"username_normalized={username}"
                return norm_match, debug_info

        # PRIORITY 5: fuzzy
        if not full_name_hint:
            return None, debug_info

        normalized_hint = normalize_name(full_name_hint)
        if len(normalized_hint) < 3:
            return None, debug_info

        hint_parts = normalized_hint.split()
        hint_surname = hint_parts[-1] if len(hint_parts) >= 2 else ""

        candidates: List[Tuple[float, Dict]] = []
        for user in self.users:
            user_name = normalize_name(user.get("name", ""))
            if not user_name or len(user_name) < 3:
                continue

            score = 0.0
            lcs_len = longest_common_subsequence(normalized_hint, user_name)
            score += lcs_len * self.weight_lcs
            overlap = char_overlap(normalized_hint, user_name)
            score += overlap * self.weight_char_overlap

            if self.use_bigram_dice:
                dice = bigram_dice_coefficient(normalized_hint, user_name)
                score += dice * self.weight_bigram_dice * 10

            if hint_surname and len(hint_surname) >= 3:
                user_parts = user_name.split()
                user_surname = user_parts[-1] if len(user_parts) >= 2 else ""
                if user_surname and hint_surname == user_surname:
                    score += 8.0

            if score > 0:
                candidates.append((score, user))

        candidates.sort(key=lambda x: x[0], reverse=True)

        debug_info["top_candidates"] = [
            {"email": u.get("email"), "name": u.get("name"), "score": round(s, 2)}
            for s, u in candidates[:5]
        ]

        if candidates and candidates[0][0] >= self.min_score:
            best_score = candidates[0][0]

            if len(candidates) > 1:
                second_score = candidates[1][0]
                margin = (best_score - second_score) / best_score if best_score > 0 else 0
                if margin < 0.20:
                    logger.warning(
                        f"Fuzzy match ambiguous for '{full_name_hint}': "
                        f"top='{candidates[0][1].get('name')}' ({best_score:.1f}), "
                        f"second='{candidates[1][1].get('name')}' ({second_score:.1f}), "
                        f"margin={margin:.1%} < 20%"
                    )

                    # Try AI resolver before giving up
                    if self.ai_resolver:
                        ai_candidates = [
                            {"name": u.get("name"), "email": u.get("email"),
                             "score": round(s, 2), "id": u.get("id")}
                            for s, u in candidates[:5]
                        ]
                        ai_pick = self.ai_resolver.resolve_ambiguous_match(
                            local_username=username,
                            local_fullname=full_name_hint,
                            candidates=ai_candidates,
                        )
                        if ai_pick:
                            # Find the full user dict for the AI pick
                            picked_id = ai_pick.get("id")
                            for _s, u in candidates:
                                if u.get("id") == picked_id:
                                    debug_info["exact_hit_reason"] = f"ai_resolved (id={picked_id})"
                                    return u, debug_info

                    debug_info["rejected_reason"] = "ambiguous_margin"
                    return None, debug_info

            return candidates[0][1], debug_info

        return None, debug_info

    @staticmethod
    def _names_compatible(name_a: str, name_b: str) -> bool:
        na = normalize_name(name_a)
        nb = normalize_name(name_b)
        if not na or not nb:
            return True
        if na == nb:
            return True
        words_a = na.split()
        words_b = nb.split()
        # Require surname (last word) overlap — a shared first name alone
        # (e.g. "John Tough" vs "John Smith") is not enough to be compatible.
        if len(words_a) >= 2 and len(words_b) >= 2:
            if words_a[-1] == words_b[-1]:
                return True
            # Full-name match: both first AND last name present in the other
            set_a = set(words_a)
            set_b = set(words_b)
            if len(set_a & set_b) >= 2:
                return True
        elif len(words_a) == 1 or len(words_b) == 1:
            # Single-word name — allow if it matches any word in the other
            set_a = set(words_a)
            set_b = set(words_b)
            if set_a & set_b:
                return True
        return bigram_dice_coefficient(na, nb) >= 0.65


# =============================================================================
# Local User Extraction (from Jamf)
# =============================================================================

def pick_primary_local_identity(
    local_users: List[Dict[str, Any]],
    skip_usernames: Optional[List[str]] = None,
    location: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Pick the primary local user from Jamf local accounts.

    IMPORTANT: Jamf "User and Location" fields are NOT trusted — they may
    contain stale data from old feedback loops.  The only reliable identity
    source is the Local User Accounts list on the machine itself.

    We skip well-known admin / service accounts (Admin, and any configured
    in skip_usernames) and score the remaining accounts:

      +15  realname looks like a person name (>= 2 words, differs from username)
      +10  realname is present (even if single word)
      +5   home directory under /Users/
      -3   administrator flag set

    The `location` parameter is accepted for API compatibility but is ignored.

    Returns (username, full_name, None).
    The third element is always None (no email from local accounts).
    """
    if not local_users:
        return None, None, None

    # ---- Accounts to always skip ----
    system_skip = {
        "root", "daemon", "nobody", "guest",
        "_spotlight", "_mbsetupuser",
        "admin", "administrator", "jamfadmin",
        # Additional org-specific accounts are added via skip_usernames config
    }
    # Merge config-level skip usernames
    extra_skip = {u.lower() for u in (skip_usernames or [])}
    all_skip = system_skip | extra_skip

    candidates = []
    for user in local_users:
        username = (user.get("name") or user.get("username") or "").strip()
        full_name = (user.get("realname") or user.get("real_name") or "").strip()

        if not username:
            continue
        if username.lower() in all_skip or username.lower().startswith("_"):
            continue

        score = 0

        # Person-name heuristic: real person name scores higher than org name
        if full_name and " " in full_name and full_name.lower() != username.lower():
            score += 15
        elif full_name:
            score += 10

        # Has a home directory under /Users/ (real interactive user)
        if user.get("home") and "/Users/" in str(user.get("home", "")):
            score += 5

        # Penalise administrator / managed accounts
        if user.get("administrator") or user.get("admin"):
            score -= 3

        candidates.append((score, username, full_name))

    if not candidates:
        return None, None, None

    candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0]
    logger.debug(f"Primary local account: username={best[1]}, name={best[2]}, score={best[0]}")
    return best[1], best[2], None
