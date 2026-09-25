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
        text = f"{module_name} failed: {error}"
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": ":rotating_light:  Module Failure"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Module:*\n`{module_name}`"},
                {"type": "mrkdwn", "text": f"*Status:*\nFailed"},
            ]},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Error:*\n```{error[:1500]}```"}},
            {"type": "context", "elements": [
                {"type": "mrkdwn", "text": ":robot_face:  _Sent by Jamf-SnipeIT Suite_"}
            ]},
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

        if total_errors == 0:
            return True  # No news is good news

        duration = summary.get("duration_seconds", 0)

        blocks: List[Dict] = [
            {"type": "header", "text": {"type": "plain_text", "text": ":warning:  Run Completed With Errors"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Duration:*\n{duration}s"},
                {"type": "mrkdwn", "text": f"*Errors:*\n{total_errors}"},
            ]},
            {"type": "divider"},
        ]

        for name, m in summary.get("modules", {}).items():
            errored = m.get("errored", 0)
            if errored > 0:
                dur = m.get("duration_s", 0)
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": (
                        f":x:  *{name}*  —  {dur:.0f}s, {errored} error(s)"
                    )},
                })

        blocks.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": ":robot_face:  _Sent by Jamf-SnipeIT Suite_"}
        ]})

        return self._post(f"Run completed with {total_errors} error(s)", blocks)

    def notify_matching_warnings(self, warnings: List[Dict]) -> bool:
        """Send a summary of ambiguous user-matching results so admins can fix duplicates."""
        if not warnings:
            return True

        # De-duplicate
        seen = set()
        unique: List[Dict] = []
        for w in warnings:
            key = (w.get("type", ""), w.get("query", ""))
            if key not in seen:
                seen.add(key)
                unique.append(w)

        blocks: List[Dict] = [
            {"type": "header", "text": {"type": "plain_text", "text": ":warning:  Duplicate Users in Snipe-IT"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": (
                f"*{len(unique)}* Snipe-IT lookups matched multiple users.\n"
                "Fix duplicates in Snipe-IT so matching is unambiguous."
            )}},
            {"type": "divider"},
        ]

        for w in unique[:15]:
            query = w.get("query", "?")
            wtype = w.get("type", "unknown")
            candidates = w.get("candidates", [])
            label = "Name" if "name" in wtype else "Email prefix"

            candidate_lines = "\n".join(f"  `{c}`" for c in candidates[:4])
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": (
                    f":bust_in_silhouette:  *{query}*  ({label} match)\n"
                    f"{candidate_lines}"
                )},
            })

        if len(unique) > 15:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"_...and {len(unique) - 15} more_"}})

        return self._post(f"{len(unique)} ambiguous user matches need attention", blocks)

    def send(self, text: str) -> bool:
        """Send a plain-text message."""
        return self._post(text)

    def post_to_channel(self, channel_id: str, text: str, blocks: Optional[List[Dict]] = None) -> bool:
        """Post a message to a specific channel (overrides default)."""
        if not self.bot_token:
            return False
        payload: Dict[str, Any] = {"channel": channel_id, "text": text}
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
                logger.warning(f"Slack API error ({channel_id}): {data.get('error', 'unknown')}")
                return False
            return True
        except Exception as exc:
            logger.warning(f"Slack send to {channel_id} failed: {exc}")
            return False

    def notify_investigation_needed(
        self,
        channel_id: str,
        title: str,
        items: List[Dict[str, str]],
    ) -> bool:
        """Send a formatted notification about items needing manual investigation."""
        if not items:
            return True

        blocks: List[Dict] = [
            {"type": "header", "text": {"type": "plain_text", "text": f":mag:  {title}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": (
                f"*{len(items)}* item(s) need manual review."
            )}},
            {"type": "divider"},
        ]

        for item in items[:20]:
            desc = item.get("description", "?")
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f":clipboard:  {desc}"},
            })

        if len(items) > 20:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"_...and {len(items) - 20} more_"}})

        blocks.append({"type": "divider"})
        blocks.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": ":robot_face:  _Sent by Jamf-SnipeIT Suite_"}
        ]})

        return self.post_to_channel(channel_id, f"{title} ({len(items)} items)", blocks)
