"""
AI-powered user matching for ambiguous cases.

When the fuzzy matcher can't confidently pick between two or more candidates
(margin < 20%), this module sends all available context to an LLM and asks
it to reason about which Snipe-IT user is the correct match for a Jamf
local account.

Requires: AI_API_KEY environment variable or config setting.
"""
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import anthropic
    _LLM_AVAILABLE = True
except ImportError:
    anthropic = None  # type: ignore
    _LLM_AVAILABLE = False

# Model to use for resolution — fast and cheap
_MODEL_ID = os.environ.get("AI_MODEL_ID", "claude-haiku-4-5-20251001")


class AIResolver:
    """Resolve ambiguous user matches using an LLM."""

    def __init__(self, api_key: str = "", enabled: bool = True):
        self.api_key = api_key or os.environ.get("AI_API_KEY", "")
        self.enabled = enabled and _LLM_AVAILABLE and bool(self.api_key)
        self._client = None

        if self.enabled:
            logger.info("AI resolver: enabled")
        elif enabled and not _LLM_AVAILABLE:
            logger.debug("AI resolver: LLM package not installed")
        elif enabled and not self.api_key:
            logger.debug("AI resolver: no API key configured")

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
        if not self.enabled or not candidates:
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
                model=_MODEL_ID,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text.strip()
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
                return None

            # Only accept high/medium confidence
            if confidence not in ("high", "medium"):
                logger.info(f"AI resolver: low confidence, skipping")
                return None

            # Return the chosen candidate (1-indexed)
            idx = int(match_idx) - 1
            if 0 <= idx < len(candidates):
                return candidates[idx]

            return None

        except json.JSONDecodeError as e:
            logger.warning(f"AI resolver: could not parse response: {e}")
            return None
        except Exception as e:
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
        if not self.enabled:
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
                model=_MODEL_ID,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text.strip()
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
                return {"_keep_current": True, "id": current_assignment.get("id")}

            if match_idx is None or confidence == "none":
                return None

            if confidence not in ("high", "medium"):
                return None

            idx = int(match_idx) - 1
            if 0 <= idx < len(candidates):
                return candidates[idx]

            return None

        except json.JSONDecodeError as e:
            logger.warning(f"AI cross-platform: could not parse response: {e}")
            return None
        except Exception as e:
            logger.warning(f"AI cross-platform error: {e}")
            return None
