"""
Monthly digest — aggregates findings from health check and correction modules,
then sends a single Slack report with all items needing manual attention.
Runs silently (dry_run=True) — no remediations triggered.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List

from clients.slack import SlackClient
from core.config import Config
from modules.maintenance.health_check import HealthCheckModule
from modules.sync.correction import CorrectionModule
from modules.sync.user_match import UserMatchModule

logger = logging.getLogger(__name__)


class MonthlyDigestModule:
    def __init__(self, config: Config):
        self.config = config
        self.slack = SlackClient(
            bot_token=config.slack.bot_token,
            channel_id=config.slack.channel_id,
            enabled=config.slack.enabled,
        )

    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        logger.info("=== Monthly Digest: collecting findings ===")
        sections: Dict[str, Any] = {}

        # Health check (read-only by nature)
        logger.info("[1/3] Running health check scan...")
        hc = HealthCheckModule(self.config)
        try:
            sections["health"] = hc.run(dry_run=True)
        except Exception as e:
            logger.error(f"Health check scan failed: {e}")
            sections["health"] = {}
        finally:
            hc.close()

        # Correction scan (dry_run=True → reports mismatches, no changes)
        logger.info("[2/3] Running correction scan...")
        corr = CorrectionModule(self.config)
        try:
            sections["correction"] = corr.run(dry_run=True)
        except Exception as e:
            logger.error(f"Correction scan failed: {e}")
            sections["correction"] = {}
        finally:
            corr.close()

        # User match scan (dry_run=True → finds unmatched devices, no changes)
        logger.info("[3/3] Running user match scan...")
        um = UserMatchModule(self.config)
        try:
            sections["user_match"] = um.run(dry_run=True)
        except Exception as e:
            logger.error(f"User match scan failed: {e}")
            sections["user_match"] = {}
        finally:
            um.close()

        if not dry_run:
            self._send_digest(sections)
        else:
            logger.info("[DRY-RUN] Would send monthly digest Slack message")

        logger.info("=== Monthly Digest complete ===")
        return sections

    def _send_digest(self, sections: Dict[str, Any]) -> None:
        health = sections.get("health", {})
        corr = sections.get("correction", {})
        user_match = sections.get("user_match", {})
        month = datetime.now().strftime("%B %Y")

        health_categories = [
            (
                "Stuck Pending (leaver's machine, active user assigned)",
                "stuck_pending",
                lambda x: f"`{x['serial']}` — currently *{x.get('current', '?')}*, should be *{x.get('target', '?')}*",
            ),
            (
                "Checked Out to Disabled/Leaver User",
                "checked_out_to_disabled",
                lambda x: f"`{x['serial']}` → *{x['assigned']}* ({x.get('email', '?')})",
            ),
            (
                "Pending > 30 Days (stale)",
                "stale_pending",
                lambda x: f"`{x['serial']}` → *{x['assigned']}* (since {x['since']})",
            ),
            (
                "Orphan Users (active, has Jamf device, 0 Snipe-IT assets)",
                "orphan_users",
                lambda x: f"*{x['name']}* ({x['email']})",
            ),
            (
                "Invalid State: In Stock + Assigned",
                "invalid_in_stock",
                lambda x: f"`{x['serial']}` → *{x['assigned']}*",
            ),
        ]

        mismatch_items = [
            d for d in corr.get("details", []) if d.get("type") == "mismatch"
        ]
        unmatched_devices = user_match.get("unmatched_devices", [])

        total_health = sum(len(health.get(k, [])) for _, k, _ in health_categories)
        grand_total = total_health + len(mismatch_items) + len(unmatched_devices)

        blocks: List[Dict] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f":calendar:  Monthly Asset Digest — {month}"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{grand_total} item(s) need manual attention.*\n"
                        "Automated remediations ran silently throughout the month — "
                        "the items below could not be auto-resolved."
                    ),
                },
            },
            {"type": "divider"},
        ]

        has_items = False

        for title, key, fmt in health_categories:
            items = health.get(key, [])
            if not items:
                continue
            has_items = True
            lines = "\n".join(f":clipboard:  {fmt(it)}" for it in items[:10])
            if len(items) > 10:
                lines += f"\n_...and {len(items) - 10} more_"
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{title}* — {len(items)} item(s)\n{lines}"},
            })

        if mismatch_items:
            has_items = True
            lines = "\n".join(
                f":clipboard:  {m.get('description', '?')}" for m in mismatch_items[:10]
            )
            if len(mismatch_items) > 10:
                lines += f"\n_...and {len(mismatch_items) - 10} more_"
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Assignment Mismatches (auto-correction failed)* — {len(mismatch_items)} item(s)\n{lines}",
                },
            })

        if unmatched_devices:
            has_items = True
            lines = "\n".join(
                f":clipboard:  {d.get('description', '?')}" for d in unmatched_devices[:10]
            )
            if len(unmatched_devices) > 10:
                lines += f"\n_...and {len(unmatched_devices) - 10} more_"
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Unmatched Devices (no Snipe-IT user found)* — {len(unmatched_devices)} item(s)\n{lines}",
                },
            })

        if not has_items:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ":white_check_mark:  No issues found — everything looks clean!",
                },
            })

        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f":robot_face:  _Monthly Digest | {datetime.now().strftime('%Y-%m-%d %H:%M')} | Jamf-SnipeIT Suite_",
                }
            ],
        })

        self.slack.post_to_channel(
            self.config.slack.channel_id,
            f"Monthly Digest ({month}): {grand_total} item(s) need attention",
            blocks,
        )

    def close(self) -> None:
        pass


def run_monthly_digest(config: Config, dry_run: bool = False) -> Dict[str, Any]:
    m = MonthlyDigestModule(config)
    try:
        return m.run(dry_run=dry_run)
    finally:
        m.close()
