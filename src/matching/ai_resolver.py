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
