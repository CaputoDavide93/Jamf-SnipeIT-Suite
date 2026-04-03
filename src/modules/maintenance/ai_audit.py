"""
AI-Powered Cross-Platform Audit Module

Collects data from all 4 platforms (Jamf, Snipe-IT, Azure AD, HiBob),
builds a unified view of every user and device, then asks an LLM to
identify anomalies, inconsistencies, and actionable recommendations.

Runs weekly and sends a structured Slack report.
"""
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.config import Config
from core.client_factory import create_jamf_client, create_snipeit_client, create_slack_client
from clients.azure import AzureClient

logger = logging.getLogger(__name__)

try:
    import anthropic
    _LLM_AVAILABLE = True
except ImportError:
    anthropic = None
    _LLM_AVAILABLE = False

_MODEL_ID = os.environ.get("AI_MODEL_ID", "claude-sonnet-4-20250514")
_SLACK_CHANNEL = None  # Set from config


class AIAuditModule:
    """Cross-platform AI audit — finds what humans miss."""

    def __init__(self, config: Config):
        self.config = config
        self.jamf = create_jamf_client(config)
        self.snipe = create_snipeit_client(config)
        self.slack = create_slack_client(config)

        self.azure = AzureClient(
            tenant_id=config.azure.tenant_id,
            client_id=config.azure.client_id,
            client_secret=config.azure.client_secret,
            scope=config.azure.scope,
            timeout=config.api.timeout_seconds,
        )

        api_key = getattr(config, 'ai_api_key', '') or os.environ.get('AI_API_KEY', '')
        self._llm = anthropic.Anthropic(api_key=api_key) if _LLM_AVAILABLE and api_key else None

        if not self._llm:
            logger.warning("AI Audit: LLM not available — module will not run")

    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        """Run the full cross-platform AI audit."""
        if not self._llm:
            return {"error": "LLM not configured"}

        logger.info("=== AI Cross-Platform Audit ===")
        results = {
            "findings": [],
            "recommendations": [],
            "stats": {},
        }

        # Step 1: Collect data from all platforms
        logger.info("[1/4] Collecting data from all platforms...")
        platform_data = self._collect_all_data()
        results["stats"] = {
            "jamf_devices": len(platform_data.get("jamf_devices", [])),
            "snipe_assets": len(platform_data.get("snipe_assets", [])),
            "snipe_users": len(platform_data.get("snipe_users", [])),
            "azure_users": len(platform_data.get("azure_users", [])),
            "azure_disabled": len(platform_data.get("azure_disabled", [])),
            "azure_leavers": len(platform_data.get("azure_leavers", [])),
        }
        logger.info(f"  Jamf: {results['stats']['jamf_devices']} devices")
        logger.info(f"  Snipe-IT: {results['stats']['snipe_assets']} assets, {results['stats']['snipe_users']} users")
        logger.info(f"  Azure AD: {results['stats']['azure_users']} users, {results['stats']['azure_disabled']} disabled, {results['stats']['azure_leavers']} leavers")

        # Step 2: Build unified profiles
        logger.info("[2/4] Building unified user profiles...")
        profiles = self._build_unified_profiles(platform_data)
        logger.info(f"  {len(profiles)} unified profiles")

        # Step 3: Run AI analysis in batches
        logger.info("[3/4] Running AI analysis...")
        findings = self._run_ai_analysis(profiles, platform_data)
        results["findings"] = findings
        logger.info(f"  {len(findings)} findings")

        # Step 4: Send Slack report
        logger.info("[4/4] Sending report...")
        if not dry_run and findings:
            self._send_slack_report(findings, results["stats"])
        elif dry_run and findings:
            logger.info("[DRY-RUN] Would send Slack report with findings:")
            for f in findings[:10]:
                logger.info(f"  [{f.get('severity', '?')}] {f.get('title', '?')}")

        logger.info(f"=== AI Audit complete: {len(findings)} findings ===")
        return results

    # ------------------------------------------------------------------
    # Data Collection
    # ------------------------------------------------------------------

    def _collect_all_data(self) -> Dict[str, Any]:
        """Fetch data from all platforms."""
        data = {}

        # Jamf — all devices with basic info
        logger.info("  Fetching Jamf devices...")
        data["jamf_devices"] = self.jamf.get_all_computers_basic()

        # Snipe-IT — all assets and users
        logger.info("  Fetching Snipe-IT assets...")
        data["snipe_assets"] = self.snipe.get_all_assets()
        logger.info("  Fetching Snipe-IT users...")
        data["snipe_users"] = self.snipe.get_all_users()

        # Azure AD — active users + disabled/leavers groups
        logger.info("  Fetching Azure AD users...")
        data["azure_users"] = self.azure.get_group_members(
            self.config.azure.starters_group_id
        ) if self.config.azure.starters_group_id else []

        data["azure_disabled"] = self.azure.get_group_members(
            self.config.azure.disabled_group_id
        ) if self.config.azure.disabled_group_id else []

        data["azure_leavers"] = self.azure.get_group_members(
            self.config.azure.leavers_group_id
        ) if self.config.azure.leavers_group_id else []

        return data

    # ------------------------------------------------------------------
    # Unified Profiles
    # ------------------------------------------------------------------

    def _build_unified_profiles(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Build a unified view of each user across all platforms."""

        # Index Snipe-IT users by email
        snipe_by_email = {}
        for u in data.get("snipe_users", []):
            email = (u.get("email") or "").lower().strip()
            if email:
                snipe_by_email[email] = u

        # Index Snipe-IT assets by serial
        snipe_assets_by_serial = {}
        for a in data.get("snipe_assets", []):
            serial = (a.get("serial") or "").upper().strip()
            if serial:
                snipe_assets_by_serial[serial] = a

        # Index Snipe-IT assets by assigned user
        snipe_assets_by_user = {}
        for a in data.get("snipe_assets", []):
            assigned = a.get("assigned_to")
            if isinstance(assigned, dict) and assigned.get("id"):
                uid = assigned["id"]
                snipe_assets_by_user.setdefault(uid, []).append(a)

        # Index Azure disabled/leavers by email
        azure_disabled_emails = set()
        for u in data.get("azure_disabled", []):
            email = AzureClient.extract_email(u)
            if email:
                azure_disabled_emails.add(email.lower())

        azure_leaver_emails = set()
        for u in data.get("azure_leavers", []):
            email = AzureClient.extract_email(u)
            if email:
                azure_leaver_emails.add(email.lower())

        # Build profiles from Snipe-IT users (most complete source)
        profiles = []
        for user in data.get("snipe_users", []):
            email = (user.get("email") or "").lower().strip()
            name = user.get("name", "")
            uid = user.get("id")

            profile = {
                "snipe_id": uid,
                "name": name,
                "email": email,
                "snipe_username": user.get("username", ""),
                "is_disabled_azure": email in azure_disabled_emails,
                "is_leaver_azure": email in azure_leaver_emails,
                "is_disabled_snipe": name.strip().startswith("[Disabled]"),
                "snipe_assets": [],
                "asset_count": 0,
            }

            # Add assigned assets
            assets = snipe_assets_by_user.get(uid, [])
            for a in assets:
                status = a.get("status_label", {})
                status_name = status.get("name", "?") if isinstance(status, dict) else str(status)
                profile["snipe_assets"].append({
                    "serial": a.get("serial", "?"),
                    "name": a.get("name", "?"),
                    "status": status_name,
                    "model": a.get("model", {}).get("name", "?") if isinstance(a.get("model"), dict) else "?",
                })
            profile["asset_count"] = len(assets)

            profiles.append(profile)

        return profiles

    # ------------------------------------------------------------------
    # AI Analysis
    # ------------------------------------------------------------------

    def _run_ai_analysis(self, profiles: List[Dict], data: Dict) -> List[Dict]:
        """Send data to LLM for cross-platform analysis."""

        # Build summary stats for context
        total_snipe_users = len(profiles)
        users_with_assets = sum(1 for p in profiles if p["asset_count"] > 0)
        multi_asset_users = [p for p in profiles if p["asset_count"] > 2]
        disabled_with_assets = [p for p in profiles if p["is_disabled_azure"] and p["asset_count"] > 0 and not any(a["status"] == "Pending" for a in p["snipe_assets"])]
        leavers_not_disabled = [p for p in profiles if p["is_leaver_azure"] and not p["is_disabled_snipe"]]
        disabled_azure_not_snipe = [p for p in profiles if p["is_disabled_azure"] and not p["is_disabled_snipe"]]
        orphan_assets = [a for a in data.get("snipe_assets", []) if not a.get("assigned_to") and a.get("status_label", {}).get("name") != "Pending"]

        # Jamf devices not in Snipe-IT
        snipe_serials = {(a.get("serial") or "").upper() for a in data.get("snipe_assets", [])}
        jamf_only = [d for d in data.get("jamf_devices", []) if (d.get("serial_number") or "").upper() not in snipe_serials]

        # Build the prompt with real data
        prompt = f"""You are an IT asset management auditor. Analyse the following cross-platform data and identify issues that need attention.

## Platform Summary
- Snipe-IT: {total_snipe_users} users, {users_with_assets} with assets
- Jamf: {len(data.get('jamf_devices', []))} managed devices
- Azure AD: {len(data.get('azure_disabled', []))} disabled, {len(data.get('azure_leavers', []))} leavers

## Issues Found (pre-computed)

### Users with 3+ assets ({len(multi_asset_users)})
{json.dumps([{"name": p["name"], "email": p["email"], "assets": p["asset_count"], "devices": [a["serial"] + " (" + a["status"] + ")" for a in p["snipe_assets"]]} for p in multi_asset_users[:15]], indent=2)}

### Disabled in Azure but NOT marked [Disabled] in Snipe-IT ({len(disabled_azure_not_snipe)})
{json.dumps([{"name": p["name"], "email": p["email"], "assets": p["asset_count"]} for p in disabled_azure_not_snipe[:20]], indent=2)}

### Disabled in Azure but assets NOT Pending ({len(disabled_with_assets)})
{json.dumps([{"name": p["name"], "email": p["email"], "assets": [a["serial"] + " (" + a["status"] + ")" for a in p["snipe_assets"]]} for p in disabled_with_assets[:15]], indent=2)}

### Leavers in Azure but not tagged in Snipe-IT ({len(leavers_not_disabled)})
{json.dumps([{"name": p["name"], "email": p["email"]} for p in leavers_not_disabled[:20]], indent=2)}

### Devices in Jamf but NOT in Snipe-IT ({len(jamf_only)})
{json.dumps([{"serial": d.get("serial_number", "?"), "id": d.get("id")} for d in jamf_only[:20]], indent=2)}

### Unassigned assets (not Pending) ({len(orphan_assets)})
{json.dumps([{"serial": a.get("serial", "?"), "name": a.get("name", "?"), "status": a.get("status_label", {}).get("name", "?")} for a in orphan_assets[:15]], indent=2)}

## Instructions
Analyse ALL the issues above and produce a JSON array of findings. For each finding:
- "severity": "critical" | "high" | "medium" | "low"
- "category": "security" | "compliance" | "data_integrity" | "operational"
- "title": short description (max 80 chars)
- "detail": what the issue is and who is affected
- "recommendation": specific action to take
- "affected_count": number of items affected

Focus on:
1. Security risks (disabled users with active deployed assets)
2. Data inconsistencies between platforms
3. Users with unusually many devices
4. Assets that should be tracked but aren't
5. Process gaps (leavers not properly offboarded)

Reply with ONLY a JSON array of findings, ordered by severity (critical first).
Do NOT include any explanation outside the JSON."""

        try:
            response = self._llm.messages.create(
                model=_MODEL_ID,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()

            # Strip markdown code blocks
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            findings = json.loads(text)
            if isinstance(findings, list):
                return findings
            return []

        except json.JSONDecodeError as e:
            logger.warning(f"AI audit: could not parse response: {e}")
            return []
        except Exception as e:
            logger.error(f"AI audit error: {e}")
            return []

    # ------------------------------------------------------------------
    # Slack Report
    # ------------------------------------------------------------------

    def _send_slack_report(self, findings: List[Dict], stats: Dict) -> None:
        """Send a formatted Slack report with AI findings."""
        channel = self.config.slack.channel_id

        severity_emoji = {
            "critical": ":rotating_light:",
            "high": ":warning:",
            "medium": ":large_blue_diamond:",
            "low": ":white_circle:",
        }

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": ":mag:  AI Cross-Platform Audit Report"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Jamf Devices:* {stats.get('jamf_devices', 0)}"},
                {"type": "mrkdwn", "text": f"*Snipe-IT Assets:* {stats.get('snipe_assets', 0)}"},
                {"type": "mrkdwn", "text": f"*Azure Users:* {stats.get('azure_users', 0)}"},
                {"type": "mrkdwn", "text": f"*Findings:* {len(findings)}"},
            ]},
            {"type": "divider"},
        ]

        for f in findings[:15]:
            severity = f.get("severity", "low")
            emoji = severity_emoji.get(severity, ":white_circle:")
            title = f.get("title", "?")
            detail = f.get("detail", "")
            rec = f.get("recommendation", "")
            count = f.get("affected_count", 0)

            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": (
                    f"{emoji}  *[{severity.upper()}] {title}*\n"
                    f"{detail}\n"
                    f"_Recommendation:_ {rec}\n"
                    f"_Affected:_ {count} item(s)"
                )},
            })

        if len(findings) > 15:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"_...and {len(findings) - 15} more findings_"}})

        blocks.append({"type": "divider"})
        blocks.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": f":robot_face:  _AI Audit | {datetime.now().strftime('%Y-%m-%d %H:%M')} | Jamf-SnipeIT Suite_"}
        ]})

        self.slack.post_to_channel(
            channel,
            f"AI Audit: {len(findings)} findings",
            blocks,
        )

    # ------------------------------------------------------------------

    def close(self) -> None:
        self.jamf.close()
        self.snipe.close()
        self.azure.close()


def run_ai_audit(config: Config, dry_run: bool = False) -> Dict[str, Any]:
    """Convenience function to run the AI audit."""
    module = AIAuditModule(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()
