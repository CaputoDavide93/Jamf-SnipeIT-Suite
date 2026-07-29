"""
AI-powered user matching for ambiguous cases.

When the fuzzy matcher can't confidently pick between two or more candidates
(margin < 20%), this module sends all available context to an LLM and asks
it to reason about which Snipe-IT user is the correct match for a Jamf
local account.

Includes a persistent cache so repeat queries (same user/candidates) don't
re-call the AI — critical for staying under API rate limits.

Requires: AI_API_KEY environment variable or config setting.
"""
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import anthropic
    _LLM_AVAILABLE = True
except ImportError:
    anthropic = None  # type: ignore
    _LLM_AVAILABLE = False

# Model to use for resolution — configurable without a brittle static allowlist
_MODEL_DEFAULT = "claude-haiku-4-5-20251001"
# AI_RESOLVER_MODEL_ID lets this per-match resolver stay on a cheap model even
# when AI_MODEL_ID points the AI audit at a larger one. Falls back to the
# shared variable so existing deployments are unaffected.
_MODEL_ID = (
    os.environ.get("AI_RESOLVER_MODEL_ID")
    or os.environ.get("AI_MODEL_ID", _MODEL_DEFAULT)
).strip() or _MODEL_DEFAULT
# Cache file (resolutions persist across runs)
_CACHE_PATH = Path(os.environ.get("AI_CACHE_PATH", "/app/output/ai_cache.json"))
# S3 cache bucket/key — when set, cache syncs to S3 (survives Fargate restarts)
_CACHE_S3_BUCKET = os.environ.get("AI_CACHE_S3_BUCKET", "")
_CACHE_S3_KEY = os.environ.get("AI_CACHE_S3_KEY", "ai-resolver-cache.json")
# Cache TTL: re-ask AI every 30 days
_CACHE_TTL_DAYS = 30


def _extract_text(response) -> str:
    """
    Join the text blocks of an Anthropic response.

    Models with extended thinking return one or more ThinkingBlocks before the
    TextBlock, so ``content[0].text`` raises AttributeError. AI_MODEL_ID is
    shared with the AI audit module, so a thinking-capable model configured
    there silently killed AI matching here too.
    """
    return "".join(
        b.text for b in getattr(response, "content", []) or []
        if getattr(b, "type", None) == "text"
    ).strip()


class AIResolver:
    """Resolve ambiguous user matches using an LLM — with persistent cache."""

    def __init__(
        self,
        api_key: str = "",
        enabled: bool = True,
        slack=None,
        persist_cache: bool = True,
    ):
        self.api_key = api_key or os.environ.get("AI_API_KEY", "")
        self._model_id = _MODEL_ID
        self._persist_cache = persist_cache
        self.enabled, self.disabled_reason = self._compute_enablement(enabled)
        self._client = None
        self._rate_limited = False  # Set True when API returns rate-limit error
        self._rate_limit_warned = False
        self._slack = slack  # SlackClient for rate-limit alerts
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._load_cache()

        if self.enabled:
            logger.info(
                "AI resolver enabled with model %s (cache: %d entries)",
                self._model_id,
                len(self._cache),
            )
        else:
            logger.info(f"AI resolver disabled: {self.disabled_reason}")

    def _compute_enablement(self, enabled_flag: bool) -> tuple:
        """Return (enabled, reason) pair."""
        if not enabled_flag:
            return False, "disabled via constructor"
        if not _LLM_AVAILABLE:
            return False, "anthropic SDK not installed"
        if not self.api_key:
            return False, "AI_API_KEY missing"
        return True, "enabled"

    def _notify_rate_limit(self, error: Exception) -> None:
        """Post Slack alert when AI hits rate limit (once per run)."""
        if self._rate_limit_warned:
            return
        self._rate_limit_warned = True
        if not self._slack:
            return
        try:
            channel = getattr(self._slack, "channel_id", None)
            if not channel:
                return
            msg = str(error)[:400]
            blocks = [
                {"type": "header", "text": {"type": "plain_text", "text": ":warning:  AI Resolver rate-limited"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": (
                    f"AI matching disabled for rest of run. Fuzzy-only fallback active.\n\n"
                    f"*Error:* ```{msg}```"
                )}},
            ]
            self._slack.post_to_channel(channel, "AI resolver rate-limited", blocks)
        except Exception as e:
            logger.debug(f"Rate-limit alert post failed: {e}")

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def _load_cache(self) -> None:
        """Load cache from disk (and S3 if configured)."""
        # Try S3 first (survives Fargate restarts)
        if _CACHE_S3_BUCKET:
            try:
                import boto3
                s3 = boto3.client("s3")
                _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                s3.download_file(_CACHE_S3_BUCKET, _CACHE_S3_KEY, str(_CACHE_PATH))
                logger.debug(f"AI cache: downloaded from s3://{_CACHE_S3_BUCKET}/{_CACHE_S3_KEY}")
            except Exception as e:
                logger.debug(f"AI cache: no S3 cache yet or download failed: {e}")

        try:
            if _CACHE_PATH.exists():
                with open(_CACHE_PATH, "r") as f:
                    self._cache = json.load(f)
        except Exception as e:
            logger.debug(f"AI cache load failed: {e}")
            self._cache = {}

    def _save_cache(self) -> None:
        """Persist cache to disk (and S3 if configured)."""
        try:
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_CACHE_PATH, "w") as f:
                json.dump(self._cache, f)
        except Exception as e:
            logger.debug(f"AI cache save failed: {e}")
            return

        # Sync to S3 (only saves when cache changes, so not every call is an upload)
        if _CACHE_S3_BUCKET:
            try:
                import boto3
                s3 = boto3.client("s3")
                s3.upload_file(str(_CACHE_PATH), _CACHE_S3_BUCKET, _CACHE_S3_KEY)
            except Exception as e:
                logger.debug(f"AI cache: S3 upload failed: {e}")

    def _cache_key(self, username: str, fullname: str, candidate_ids: List[int]) -> str:
        """Build a stable cache key from inputs."""
        payload = f"{username.lower()}|{fullname.lower()}|{sorted(candidate_ids)}"
        return hashlib.md5(payload.encode()).hexdigest()

    def _get_cached(self, key: str) -> Optional[Dict[str, Any]]:
        """Return cached entry if fresh, else None."""
        import time
        entry = self._cache.get(key)
        if not entry:
            return None
        ts = entry.get("_ts", 0)
        if time.time() - ts > _CACHE_TTL_DAYS * 86400:
            return None  # expired
        return entry

    def _set_cached(self, key: str, result: Optional[Dict[str, Any]]) -> None:
        """Store result in cache (including None for rejected matches)."""
        import time
        self._cache[key] = {
            "_ts": time.time(),
            "result_id": result.get("id") if result else None,
            "keep_current": result.get("_keep_current") if result else None,
        }
        if self._persist_cache:
            self._save_cache()

    def _is_rate_limit_error(self, err: Exception) -> bool:
        """Detect Anthropic API rate limit errors."""
        msg = str(err).lower()
        return (
            "usage limit" in msg
            or "rate limit" in msg
            or "429" in msg
            or "too many requests" in msg
        )

    def _get_client(self):
        if self._client is None and self.enabled:
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def resolve_ambiguous_match(
        self,
        local_username: str,
        local_fullname: str,
        candidates: List[Dict[str, Any]],
        device_hostname: str = "",
        serial: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Ask the LLM to pick the best match from ambiguous candidates.

        Args:
            local_username: Jamf local account username (e.g. "mikeym")
            local_fullname: Jamf local account full name (e.g. "Mikey M")
            candidates: Top Snipe-IT user candidates with scores
            device_hostname: Optional device hostname for context
            serial: Optional serial number for context

        Returns:
            The chosen candidate dict, or None if AI can't decide.
        """
        if not self.enabled or not candidates or self._rate_limited:
            return None

        # Check cache first — avoids re-calling for same user/candidates
        cand_ids = [int(c.get("id", 0)) for c in candidates[:5]]
        cache_key = self._cache_key(local_username, local_fullname, cand_ids)
        cached = self._get_cached(cache_key)
        if cached is not None:
            cached_id = cached.get("result_id")
            if cached_id is None:
                logger.debug(f"AI cache hit (no match): '{local_username}'")
                return None
            for c in candidates:
                if c.get("id") == cached_id:
                    logger.debug(f"AI cache hit: '{local_username}' -> {c.get('name')}")
                    return c
            return None

        # Build the prompt with all available context
        candidate_lines = []
        for i, c in enumerate(candidates[:5], 1):
            name = c.get("name", "?")
            email = c.get("email", "?")
            score = c.get("score", 0)
            disabled = " [DISABLED USER]" if name.strip().startswith("[Disabled]") else ""
            candidate_lines.append(
                f"  {i}. Name: {name}, Email: {email}, "
                f"Fuzzy score: {score}{disabled}"
            )

        candidates_text = "\n".join(candidate_lines)

        prompt = f"""You are matching a macOS local user account to a company employee in an asset management system.

## Local account on the machine:
- Username: {local_username}
- Full name: {local_fullname}
- Device: {device_hostname or 'unknown'} (serial: {serial or 'unknown'})

## Candidate employees (from Snipe-IT):
{candidates_text}

## Rules:
- The local username is often firstname+lastname with no separator (e.g. "mikeym" = "Mikey M")
- Disabled users have left the company. If a disabled user's old account is still on a machine, the machine was likely reassigned to someone else.
- If one candidate is DISABLED and another is ACTIVE with a similar name, strongly prefer the ACTIVE user.
- If the local fullname clearly matches one candidate's name, pick that one.
- "Tom" = "Thomas", "Mike" = "Michael", "Matt" = "Matthew", "Dan" = "Daniel", "Jon"/"Jonny" = "Jonathan", "Rich" = "Richard", "Dave" = "David", "Chris" = "Christopher", "Ben" = "Benjamin", "Rob" = "Robert", "Alex" = "Alexander"

## Response:
Reply with ONLY a JSON object:
{{"match": <candidate_number 1-5>, "confidence": "high"|"medium"|"low", "reason": "<brief explanation>"}}

If you truly cannot determine the correct match, respond:
{{"match": null, "confidence": "none", "reason": "<why>"}}"""

        try:
            client = self._get_client()
            if not client:
                return None

            response = client.messages.create(
                model=self._model_id,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )

            text = _extract_text(response)
            # Strip markdown code blocks if present (```json ... ```)
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]  # remove first line (```json)
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
            # Parse JSON response
            result = json.loads(text)

            match_idx = result.get("match")
            confidence = result.get("confidence", "none")
            reason = result.get("reason", "")

            logger.info(
                f"AI resolver: '{local_username}' -> "
                f"candidate {match_idx} ({confidence}): {reason}"
            )

            if match_idx is None or confidence == "none":
                self._set_cached(cache_key, None)
                return None

            # Only accept high/medium confidence
            if confidence not in ("high", "medium"):
                logger.info(f"AI resolver: low confidence, skipping")
                self._set_cached(cache_key, None)
                return None

            # Return the chosen candidate (1-indexed)
            idx = int(match_idx) - 1
            if 0 <= idx < len(candidates):
                picked = candidates[idx]
                self._set_cached(cache_key, picked)
                return picked

            self._set_cached(cache_key, None)
            return None

        except json.JSONDecodeError as e:
            logger.warning(f"AI resolver: could not parse response: {e}")
            return None
        except Exception as e:
            if self._is_rate_limit_error(e):
                if not self._rate_limited:
                    logger.warning(f"AI resolver rate-limited, disabling for rest of run: {e}")
                    self._rate_limited = True
                    self._notify_rate_limit(e)
                return None
            logger.warning(f"AI resolver error: {e}")
            return None

    def resolve_cross_platform(
        self,
        local_username: str,
        local_fullname: str,
        candidates: List[Dict[str, Any]],
        azure_users: List[Dict[str, Any]] = None,
        current_assignment: Optional[Dict[str, Any]] = None,
        device_hostname: str = "",
        serial: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Match a local account using data from ALL platforms.

        This is the enhanced version of resolve_ambiguous_match that
        cross-references Azure AD data to find matches that Snipe-IT
        alone can't resolve (e.g. surname changes, aliases, transfers).

        Args:
            local_username: Jamf local account username
            local_fullname: Jamf local account full name
            candidates: Top Snipe-IT user candidates with scores
            azure_users: Matching Azure AD users (searched by name/email)
            current_assignment: Current Snipe-IT assignment (if any)
            device_hostname: Device hostname
            serial: Device serial

        Returns:
            The chosen Snipe-IT candidate dict, or None.
        """
        if not self.enabled or self._rate_limited:
            return None

        # Check cache
        cand_ids = [int(c.get("id", 0)) for c in candidates[:8]]
        cache_key = self._cache_key(local_username, local_fullname, cand_ids)
        cached = self._get_cached(cache_key)
        if cached is not None:
            if cached.get("keep_current"):
                logger.debug(f"AI cache hit (keep_current): '{local_username}'")
                return {"_keep_current": True}
            cached_id = cached.get("result_id")
            if cached_id is None:
                logger.debug(f"AI cache hit (no match): '{local_username}'")
                return None
            for c in candidates:
                if c.get("id") == cached_id:
                    logger.debug(f"AI cache hit: '{local_username}' -> {c.get('name')}")
                    return c
            return None

        # Build candidate info
        candidate_lines = []
        for i, c in enumerate(candidates[:8], 1):
            name = c.get("name", "?")
            email = c.get("email", "?")
            score = c.get("score", 0)
            username = c.get("username", "?")
            disabled = " [DISABLED]" if name.strip().startswith("[Disabled]") else ""
            candidate_lines.append(
                f"  {i}. Name: {name}, Email: {email}, "
                f"Username: {username}, Score: {score}{disabled}"
            )
        candidates_text = "\n".join(candidate_lines) if candidate_lines else "  (none)"

        # Build Azure AD context
        azure_lines = []
        for u in (azure_users or [])[:5]:
            display = u.get("displayName", "?")
            email = u.get("mail") or u.get("userPrincipalName", "?")
            upn = u.get("userPrincipalName", "?")
            job = u.get("jobTitle", "")
            dept = u.get("department", "")
            enabled = u.get("accountEnabled", True)
            status = "" if enabled else " [DISABLED]"
            # Check for previous names in proxyAddresses
            proxies = u.get("proxyAddresses", [])
            alias_str = ""
            if proxies:
                aliases = [p.replace("SMTP:", "").replace("smtp:", "") for p in proxies if "@" in str(p)]
                if aliases:
                    alias_str = f", Aliases: {', '.join(aliases[:3])}"
            azure_lines.append(
                f"  - {display} ({email}), UPN: {upn}, "
                f"Title: {job}, Dept: {dept}{status}{alias_str}"
            )
        azure_text = "\n".join(azure_lines) if azure_lines else "  (no Azure data)"

        # Build current assignment context
        current_text = "Not assigned"
        if current_assignment:
            current_text = (
                f"Currently assigned to: {current_assignment.get('name', '?')} "
                f"(id={current_assignment.get('id', '?')}, "
                f"email={current_assignment.get('email', '?')})"
            )

        prompt = f"""You are matching a macOS local user account to a company employee.
You have data from THREE systems to cross-reference.

## Local account on the machine:
- Username: {local_username}
- Full name: {local_fullname}
- Device: {device_hostname or 'unknown'} (serial: {serial or 'unknown'})

## Current Snipe-IT assignment:
{current_text}

## Snipe-IT candidate employees:
{candidates_text}

## Azure AD employees (may have additional info):
{azure_text}

## Rules:
- Cross-reference ALL data sources. An Azure AD user might have a different display name than Snipe-IT.
- Check for surname changes: if local account is "janewinters" but Azure shows "Jane Sommers" with alias "jane.winters@...", that's the match.
- Check Azure proxyAddresses/aliases for previous email addresses that match the local username.
- If the current Snipe-IT assignment is to an ACTIVE user, strongly prefer keeping it unless there's clear evidence it's wrong.
- Disabled users have left the company. Their old local accounts may still be on machines.
- Nicknames: Tom=Thomas, Mike=Michael, Matt=Matthew, Dan=Daniel, Jon/Jonny=Jonathan, Rich=Richard, Dave=David, Chris=Christopher, Ben=Benjamin, Rob=Robert, Alex=Alexander

## Response:
Reply with ONLY a JSON object:
{{"match": <candidate_number 1-8 or null>, "confidence": "high"|"medium"|"low"|"none", "reason": "<explanation>", "keep_current": true|false}}

Set "keep_current": true if the current Snipe-IT assignment should be preserved (even if the local account name doesn't match).
Set "match" to null and "confidence" to "none" if no confident match can be made."""

        try:
            client = self._get_client()
            if not client:
                return None

            response = client.messages.create(
                model=self._model_id,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )

            text = _extract_text(response)
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            result = json.loads(text)

            match_idx = result.get("match")
            confidence = result.get("confidence", "none")
            reason = result.get("reason", "")
            keep_current = result.get("keep_current", False)

            logger.info(
                f"AI cross-platform: '{local_username}' -> "
                f"candidate {match_idx} ({confidence}), "
                f"keep_current={keep_current}: {reason}"
            )

            if keep_current and current_assignment:
                # AI says keep current assignment — return a special marker
                result_marker = {"_keep_current": True, "id": current_assignment.get("id")}
                self._set_cached(cache_key, result_marker)
                return result_marker

            if match_idx is None or confidence == "none":
                self._set_cached(cache_key, None)
                return None

            if confidence not in ("high", "medium"):
                self._set_cached(cache_key, None)
                return None

            idx = int(match_idx) - 1
            if 0 <= idx < len(candidates):
                picked = candidates[idx]
                self._set_cached(cache_key, picked)
                return picked

            self._set_cached(cache_key, None)
            return None

        except json.JSONDecodeError as e:
            logger.warning(f"AI cross-platform: could not parse response: {e}")
            return None
        except Exception as e:
            if self._is_rate_limit_error(e):
                if not self._rate_limited:
                    logger.warning(f"AI rate-limited — disabling AI for rest of run: {e}")
                    self._rate_limited = True
                    self._notify_rate_limit(e)
                return None
            logger.warning(f"AI cross-platform error: {e}")
            return None
