"""
Jamf-SnipeIT Suite - Slack Notification Client
Sends messages to a Slack channel via the Web API (chat.postMessage).
Requires a Bot OAuth token (xoxb-...) with chat:write scope.
"""
import json
import logging
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

SLACK_POST_URL = "https://slack.com/api/chat.postMessage"


class SlackClient:
    """Lightweight Slack client using the Web API (no SDK dependency)."""

    def __init__(
        self,
        bot_token: str = "",
        channel_id: str = "",
        enabled: bool = False,
    ):
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.enabled = enabled and bool(bot_token) and bool(channel_id)

        if self.enabled:
            logger.info(f"Slack notifications enabled → channel {channel_id}")
        else:
            logger.debug("Slack notifications disabled")

    # ------------------------------------------------------------------
    # Low-level
    # ------------------------------------------------------------------

    def _post(self, text: str, blocks: Optional[List[Dict]] = None) -> bool:
        """Post a message to the configured channel.  Returns True on success."""
        if not self.enabled:
            return False

        payload: Dict[str, Any] = {
            "channel": self.channel_id,
            "text": text,  # fallback for notifications / accessibility
        }
        if blocks:
            payload["blocks"] = blocks

        try:
            resp = requests.post(
                SLACK_POST_URL,
                headers={"Authorization": f"Bearer {self.bot_token}"},
                json=payload,
                timeout=10,
            )
            data = resp.json()
            if not data.get("ok"):
                logger.warning(f"Slack API error: {data.get('error', 'unknown')}")
                return False
            return True
        except Exception as exc:
            logger.warning(f"Slack send failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # High-level helpers
    # ------------------------------------------------------------------

    def notify_error(self, module_name: str, error: str) -> bool:
        """Send an error notification."""
        text = f":rotating_light: *{module_name}* failed: {error}"
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":rotating_light: *Module failure*\n"
                        f"*Module:* `{module_name}`\n"
                        f"*Error:* ```{error[:1500]}```"
                    ),
                },
            }
        ]
        return self._post(text, blocks)

    def notify_disabled_with_assets(
        self,
        user_name: str,
        email: str,
        asset_count: int,
        assets: Optional[List[str]] = None,
    ) -> bool:
        """Notify that a disabled user still has assets assigned."""
        asset_list = "\n".join(f"• {a}" for a in (assets or [])[:10])
        text = (
            f":warning: Disabled user *{user_name}* ({email}) "
            f"has {asset_count} asset(s) marked pending"
        )
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":warning: *Disabled user with assets*\n"
                        f"*User:* {user_name} ({email})\n"
                        f"*Assets marked pending:* {asset_count}\n"
                        f"{asset_list}"
                    ),
                },
            }
        ]
        return self._post(text, blocks)

    def notify_run_summary(self, summary: Dict[str, Any]) -> bool:
        """Send a run summary only if there were errors or warnings."""
        total_errors = summary.get("total_errors", 0)
        
        # Only notify on errors
        if total_errors == 0:
            return True  # Silently skip — no news is good news
        
        duration = summary.get("duration_seconds", 0)
        
        # Build list of failed modules only
        failed_text = ""
        for name, m in summary.get("modules", {}).items():
            errored = m.get("errored", 0)
            if errored > 0:
                failed_text += (
                    f":x: *{name}* — "
                    f"{m.get('duration_s', 0)}s, "
                    f"{errored} error(s)\n"
                )
        
        text = f":warning: Run completed with {total_errors} error(s) in {duration}s"
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":warning: *Jamf-SnipeIT Suite — Run completed with errors*\n"
                        f"*Duration:* {duration}s\n"
                        f"*Total errors:* {total_errors}\n\n"
                        f"{failed_text}"
                    ),
                },
            }
        ]
        return self._post(text, blocks)

    def notify_matching_warnings(self, warnings: List[Dict]) -> bool:
        """Send a summary of ambiguous user-matching results so admins can fix duplicates."""
        if not warnings:
            return True  # nothing to report

        # De-duplicate (same query may fire more than once across devices)
        seen = set()
        unique: List[Dict] = []
        for w in warnings:
            key = (w.get("type", ""), w.get("query", ""))
            if key not in seen:
                seen.add(key)
                unique.append(w)

        lines: List[str] = []
        for w in unique:
            query = w.get("query", "?")
            wtype = w.get("type", "unknown")
            candidates = w.get("candidates", [])
            label = "name" if "name" in wtype else "email prefix"
            lines.append(f"• *{query}* ({label}) → {len(candidates)} matches:")
            for c in candidates:
                lines.append(f"    ◦ {c}")

        body = "\n".join(lines)
        text = f":warning: {len(unique)} ambiguous user match(es) need attention"
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":warning: *User Match — Ambiguous Matches*\n"
                        f"{len(unique)} lookup(s) matched multiple Snipe-IT users.\n"
                        f"Fix duplicates in Snipe-IT so the match is unambiguous.\n\n"
                        f"{body}"
                    ),
                },
            }
        ]
        return self._post(text, blocks)

    def send(self, text: str) -> bool:
        """Send a plain-text message."""
        return self._post(text)
