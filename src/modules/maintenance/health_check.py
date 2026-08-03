"""
Health Check Module
Daily scan for stuck/inconsistent states that silently break the sync.

Detects:
  - Pending + inactive owner + active local user on machine (stuck reassignment)
  - Checked Out + assigned to [Disabled] user (leaver workflow missed)
  - Pending for > 30 days (stale — IT never collected)
  - Snipe-IT user with 0 assets + active in Azure + has Jamf machine (orphan)
  - In Stock + assigned (invalid state)

Posts a single Slack summary. Silent if no issues.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from core.config import Config
from core.client_factory import (
    create_jamf_client,
    create_snipeit_client,
    create_slack_client,
)
from clients.azure import AzureClient

logger = logging.getLogger(__name__)

SKIP_LOCAL_USERS = {
    "admin", "administrator", "root", "daemon", "nobody", "xdesign",
    "createfuture", "_mbsetupuser", "_spotlight", "guest", "jamfadmin",
}


class HealthCheckModule:
    """Scan for stuck/inconsistent states."""

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

    def close(self) -> None:
        self.jamf.close()
        self.snipe.close()
        self.azure.close()

    # ------------------------------------------------------------------
    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        logger.info("=== Health Check ===")

        results: Dict[str, Any] = {
            "stuck_pending": [],
            "checked_out_to_disabled": [],
            "stale_pending": [],
            "orphan_users": [],
            "invalid_in_stock": [],
            "scan_errors": [],
            "errors": 0,
        }

        # Data load
        logger.info("[1/5] Loading platform data...")
        all_assets = self.snipe.get_all_assets()
        all_users = self.snipe.get_all_users()
        inactive_emails = self._load_azure_inactive()
        logger.info(
            f"  Snipe: {len(all_assets)} assets, {len(all_users)} users; "
            f"Azure inactive: {len(inactive_emails)}"
        )

        # Build normalised username lookup (active only)
        by_norm = {}
        for u in all_users:
            if (u.get("name") or "").startswith("[Disabled]"):
                continue
            un = (u.get("username") or "").lower().split("@")[0]
            un = un.replace(".", "").replace("-", "").replace("_", "")
            if un:
                by_norm.setdefault(un, []).append(u)

        jamf_by_serial, jamf_local_norms, scan_errors, scanned_devices = (
            self._build_jamf_indexes()
        )
        results["scan_errors"] = scan_errors
        error_ratio = len(scan_errors) / max(1, scanned_devices)
        threshold = float(
            self.config.modules.get("health_check", {}).get(
                "scan_error_ratio_threshold", 0.1
            )
        )
        if scan_errors and error_ratio >= threshold:
            results["errors"] = len(scan_errors)
            logger.error(
                "Jamf health index degraded: %d/%d detail fetches failed",
                len(scan_errors),
                scanned_devices,
            )

        # ----- 1. In Stock + assigned (invalid) -----
        logger.info("[2/5] Scanning invalid states...")
        for a in all_assets:
            sl = a.get("status_label") or {}
            if isinstance(sl, dict) and sl.get("name") == "In Stock":
                at = a.get("assigned_to")
                if isinstance(at, dict) and at.get("id"):
                    results["invalid_in_stock"].append({
                        "asset_id": a.get("id"),
                        "serial": a.get("serial"),
                        "assigned": at.get("name"),
                    })

        # ----- 2. Checked Out + [Disabled] assignee (leaver missed) -----
        for a in all_assets:
            sl = a.get("status_label") or {}
            if not isinstance(sl, dict) or sl.get("name") != "Checked Out":
                continue
            at = a.get("assigned_to") or {}
            if not isinstance(at, dict):
                continue
            name = at.get("name", "")
            email = (at.get("email") or "").lower()
            if name.startswith("[Disabled]") or email in inactive_emails:
                results["checked_out_to_disabled"].append({
                    "asset_id": a.get("id"),
                    "serial": a.get("serial"),
                    "assigned": name,
                    "email": email,
                })

        # ----- 3. Stale Pending (> 30 days) -----
        logger.info("[3/5] Scanning stale Pending...")
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        for a in all_assets:
            sl = a.get("status_label") or {}
            if not isinstance(sl, dict) or sl.get("name") != "Pending":
                continue
            updated = a.get("updated_at")
            if isinstance(updated, dict):
                updated_str = updated.get("datetime", "")
            else:
                updated_str = str(updated) if updated else ""
            try:
                # Snipe returns "YYYY-MM-DD HH:MM:SS" UTC
                u_dt = datetime.strptime(updated_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                if u_dt < cutoff:
                    at = a.get("assigned_to") or {}
                    results["stale_pending"].append({
                        "asset_id": a.get("id"),
                        "serial": a.get("serial"),
                        "since": updated_str,
                        "assigned": at.get("name", "UNASSIGNED") if isinstance(at, dict) else "UNASSIGNED",
                    })
            except (ValueError, TypeError):
                pass

        # ----- 4. Stuck Pending (inactive owner + active local user) -----
        logger.info("[4/5] Scanning stuck Pending assets...")
        for a in all_assets:
            sl = a.get("status_label") or {}
            if not isinstance(sl, dict) or sl.get("name") != "Pending":
                continue
            at = a.get("assigned_to") or {}
            if not isinstance(at, dict):
                at = {}
            cur_name = at.get("name", "")
            cur_email = (at.get("email") or "").lower()
            cur_inactive = (
                not cur_name
                or cur_name.startswith("[Disabled]")
                or cur_email in inactive_emails
            )
            if not cur_inactive:
                continue
            serial = (a.get("serial") or "").strip()
            if not serial:
                continue
            jc = jamf_by_serial.get(serial.upper())
            if not jc:
                continue
            ga = jc.get("groups_accounts", {}) or {}
            lu = ga.get("local_accounts") or ga.get("local_users") or []
            if isinstance(lu, dict) and "user" in lu:
                lu = lu["user"]
            elif isinstance(lu, dict):
                lu = [lu]
            if not isinstance(lu, list):
                continue
            for u in lu:
                un = (u.get("name") or u.get("username") or "").strip().lower()
                if not un or un in SKIP_LOCAL_USERS or un.startswith("_"):
                    continue
                norm = un.replace(".", "").replace("-", "").replace("_", "")
                for m in by_norm.get(norm, []):
                    m_email = (m.get("email") or "").lower()
                    if m_email in inactive_emails:
                        continue
                    results["stuck_pending"].append({
                        "asset_id": a.get("id"),
                        "serial": serial,
                        "current": cur_name or "UNASSIGNED",
                        "local": un,
                        "target": m.get("name"),
                        "target_id": m.get("id"),
                    })
                    break
                else:
                    continue
                break

        # ----- 5. Orphan Snipe users (active, 0 assets, has Jamf machine) -----
        logger.info("[5/5] Scanning orphan users (can take a while)...")
        # Count assets per user
        assets_per_user: Dict[int, int] = {}
        for a in all_assets:
            at = a.get("assigned_to") or {}
            if isinstance(at, dict) and at.get("id"):
                assets_per_user[at["id"]] = assets_per_user.get(at["id"], 0) + 1

        # For each active user with 0 assets, does their normalised username appear as a Jamf local account?
        # We can't scan every Jamf device here (too slow) — use the reverse lookup:
        # load all Jamf computers' local accounts once, build set of uname_norm values.
        zero_asset_users = [
            user for user in all_users
            if user.get("id") not in assets_per_user
            and not (user.get("name") or "").startswith("[Disabled]")
            and (user.get("email") or "").lower() not in inactive_emails
            and user.get("email")
        ]
        logger.info(f"  {len(zero_asset_users)} users with 0 assets to check")
        for user in zero_asset_users:
            username = (user.get("username") or "").lower().split("@")[0]
            username_norm = username.replace(".", "").replace("-", "").replace("_", "")
            if username_norm and username_norm in jamf_local_norms:
                results["orphan_users"].append({
                    "user_id": user.get("id"),
                    "name": user.get("name"),
                    "email": user.get("email"),
                })

        # ----- Summary -----
        issue_keys = (
            "stuck_pending", "checked_out_to_disabled", "stale_pending",
            "orphan_users", "invalid_in_stock",
        )
        total = sum(len(results[key]) for key in issue_keys)
        logger.info(
            f"=== Health check complete: {total} issues found "
            f"({len(results['stuck_pending'])} stuck, "
            f"{len(results['checked_out_to_disabled'])} disabled-owner, "
            f"{len(results['stale_pending'])} stale, "
            f"{len(results['orphan_users'])} orphan, "
            f"{len(results['invalid_in_stock'])} invalid) ==="
        )

        if (total > 0 or scan_errors) and not dry_run and self.config.slack.notify_inline:
            self._send_slack(results)

        return results

    def _build_jamf_indexes(self) -> tuple[Dict[str, Dict], Set[str], List[Dict], int]:
        """Fetch Jamf details once and build serial and local-account indexes."""
        computers = self.jamf.get_all_computers_basic()
        settings = self.config.modules.get("health_check", {})
        max_workers = max(1, min(20, int(settings.get("max_workers", 8))))

        def fetch(comp: Dict[str, Any]):
            computer_id = comp.get("id")
            if not computer_id:
                return comp, None, "missing computer id"
            try:
                detail = self.jamf.get_computer_by_id(
                    computer_id,
                    subsets=["GroupsAccounts"],
                )
                return comp, detail, None
            except Exception as exc:
                return comp, None, str(exc)

        by_serial: Dict[str, Dict] = {}
        local_norms: Set[str] = set()
        errors: List[Dict] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            fetched = executor.map(fetch, computers)
            for basic, detail, error in fetched:
                if error:
                    errors.append({"computer_id": basic.get("id"), "error": error})
                    continue
                if not detail:
                    errors.append({
                        "computer_id": basic.get("id"),
                        "error": "empty detail response",
                    })
                    continue
                computer = detail.get("computer", detail) or {}
                serial = (
                    basic.get("serial_number")
                    or basic.get("serial")
                    or computer.get("hardware", {}).get("serial_number")
                    or ""
                )
                if serial:
                    by_serial[str(serial).upper()] = computer

                groups = computer.get("groups_accounts", {}) or {}
                local_users = groups.get("local_accounts") or groups.get("local_users") or []
                if isinstance(local_users, dict) and "user" in local_users:
                    local_users = local_users["user"]
                elif isinstance(local_users, dict):
                    local_users = [local_users]
                if not isinstance(local_users, list):
                    continue
                for local_user in local_users:
                    username = (
                        local_user.get("name") or local_user.get("username") or ""
                    ).strip().lower()
                    if (
                        not username
                        or username in SKIP_LOCAL_USERS
                        or username.startswith("_")
                    ):
                        continue
                    normalized = username.replace(".", "").replace("-", "").replace("_", "")
                    if normalized:
                        local_norms.add(normalized)

        logger.info(
            "  Jamf index: %d devices, %d local users, %d fetch errors",
            len(by_serial),
            len(local_norms),
            len(errors),
        )
        return by_serial, local_norms, errors, len(computers)

    # ------------------------------------------------------------------
    def _load_azure_inactive(self) -> Set[str]:
        emails: Set[str] = set()
        for gid in (
            self.config.azure.leavers_group_id,
            self.config.azure.disabled_group_id,
        ):
            if not gid:
                continue
            try:
                for u in self.azure.get_group_members(gid):
                    e = AzureClient.extract_email(u)
                    if e:
                        emails.add(e.lower())
            except Exception as e:
                logger.warning(f"Could not load Azure group {gid}: {e}")
        return emails

    # ------------------------------------------------------------------
    def _send_slack(self, r: Dict[str, Any]) -> None:
        channel = self.config.slack.channel_id
        blocks: List[Dict[str, Any]] = [
            {"type": "header", "text": {"type": "plain_text", "text": ":hospital:  Health Check — Issues Found"}},
        ]

        sections = [
            ("Stuck Pending (leaver's machine used by active user)", "stuck_pending",
             lambda x: f"`{x['serial']}` was {x['current']} -> should be *{x['target']}*"),
            ("Checked Out to disabled/leaver user", "checked_out_to_disabled",
             lambda x: f"`{x['serial']}` -> *{x['assigned']}* ({x.get('email', '?')})"),
            ("Pending > 30 days", "stale_pending",
             lambda x: f"`{x['serial']}` -> *{x['assigned']}* (since {x['since']})"),
            ("Orphan users (active, has Jamf machine, 0 Snipe-IT assets)", "orphan_users",
             lambda x: f"*{x['name']}* ({x['email']})"),
            ("Invalid state: In Stock + assigned", "invalid_in_stock",
             lambda x: f"`{x['serial']}` -> *{x['assigned']}*"),
              ("Jamf devices not scanned", "scan_errors",
               lambda x: f"ID `{x.get('computer_id', '?')}`: {x.get('error', 'unknown error')}"),
        ]

        for title, key, fmt in sections:
            items = r.get(key, [])
            if not items:
                continue
            lines = [fmt(it) for it in items[:15]]
            body = "\n".join(f":clipboard:  {line}" for line in lines)
            if len(items) > 15:
                body += f"\n_...and {len(items) - 15} more_"
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{title}* — {len(items)} item(s)\n{body}"},
            })

        blocks.append({"type": "divider"})
        blocks.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": f":robot_face:  _Health Check | {datetime.now().strftime('%Y-%m-%d %H:%M')}_"}
        ]})

        total = sum(len(r.get(k, [])) for k in ("stuck_pending", "checked_out_to_disabled", "stale_pending", "orphan_users", "invalid_in_stock"))
        self.slack.post_to_channel(channel, f"Health check: {total} issues", blocks)


def run_health_check(config: Config, dry_run: bool = False) -> Dict[str, Any]:
    m = HealthCheckModule(config)
    try:
        return m.run(dry_run=dry_run)
    finally:
        m.close()
