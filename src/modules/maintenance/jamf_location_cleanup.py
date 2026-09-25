"""
Jamf Location Cleanup Module

When a machine returns to stock or is retired, its previous user must not linger
in Jamf's Location (user_and_location) tab: at re-enrollment the configuration
profile would otherwise re-apply the old user's identity. We keep the ownership
history in Snipe-IT, so Jamf's location should be blanked and left for user-match
to re-populate from Snipe once the machine is re-assigned.

For every Snipe-IT asset whose status is "In Stock" or "Retired", this module
clears the Jamf Location fields (username, real name, email, position, room) —
but ONLY when Jamf currently holds user data (idempotent; silent otherwise).
The SnipeIT_Asset_ID extension attribute is left untouched.
"""
import logging
from typing import Any, Dict, List, Set

from core.config import Config
from core.client_factory import create_snipeit_client, create_jamf_client, create_slack_client

logger = logging.getLogger(__name__)

TARGET_STATUSES = {"In Stock", "Retired"}


class JamfLocationCleanupModule:
    """Blank Jamf location for stock/retired machines."""

    def __init__(self, config: Config):
        self.config = config
        self.snipe = create_snipeit_client(config)
        self.jamf = create_jamf_client(config)
        self.slack = create_slack_client(config)

    def close(self) -> None:
        self.snipe.close()
        self.jamf.close()

    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        mode = "DRY RUN" if dry_run else "LIVE RUN"
        logger.info(f"Starting Jamf Location Cleanup ({mode})")
        results: Dict[str, Any] = {
            "targets": 0, "in_jamf": 0, "cleared": 0,
            "already_blank": 0, "not_in_jamf": 0, "failures": 0, "details": [],
        }

        assets = self.snipe.get_all_assets()
        targets = [
            a for a in assets
            if isinstance(a.get("status_label"), dict)
            and a["status_label"].get("name") in TARGET_STATUSES
            and (a.get("serial") or "").strip()
        ]
        results["targets"] = len(targets)

        # serial -> jamf id (single bulk fetch)
        jid: Dict[str, int] = {}
        for c in self.jamf.get_all_computers_basic():
            s = (c.get("serial_number") or "").strip().upper()
            if s:
                jid[s] = c.get("id")

        logger.info(f"{len(targets)} In-Stock/Retired assets; {len(jid)} Jamf computers")

        for a in targets:
            serial = (a.get("serial") or "").strip().upper()
            cid = jid.get(serial)
            if not cid:
                results["not_in_jamf"] += 1
                continue
            results["in_jamf"] += 1
            d = self.jamf.get_computer_by_id(cid, subsets=["Location"]) or {}
            loc = (d.get("computer", {}) or {}).get("location", {}) or {}
            has_user = any(
                (loc.get(f) or "").strip()
                for f in ("username", "real_name", "realname", "email_address", "position")
            )
            if not has_user:
                results["already_blank"] += 1
                continue
            label = f"{serial} (was {loc.get('real_name') or loc.get('username')})"
            results["details"].append({
                "serial": serial, "status": a["status_label"].get("name"),
                "cleared_user": loc.get("real_name") or loc.get("username"),
            })
            if dry_run:
                logger.info(f"[DRY-RUN] Would clear Jamf location on {label}")
                results["cleared"] += 1
                continue
            if self.jamf.clear_computer_location(cid):
                logger.info(f"Cleared Jamf location on {label}")
                results["cleared"] += 1
            else:
                logger.error(f"Failed to clear Jamf location on {serial}")
                results["failures"] += 1

        self._summarise(results, dry_run)
        return results

    def _summarise(self, r: Dict[str, Any], dry_run: bool) -> None:
        mode = "DRY RUN" if dry_run else "LIVE RUN"
        logger.info(
            f"Jamf Location Cleanup ({mode}): {r['targets']} stock/retired, "
            f"{r['in_jamf']} in Jamf, {r['cleared']} cleared, "
            f"{r['already_blank']} already blank, {r['not_in_jamf']} not in Jamf, "
            f"{r['failures']} failures"
        )
        if self.slack and not dry_run and r["cleared"]:
            self.slack.send(
                f"*Jamf Location Cleanup*: blanked stale user data on {r['cleared']} "
                f"stock/retired machine(s) so re-enrollment waits for Snipe-IT."
            )


def run_jamf_location_cleanup(config: Config, dry_run: bool = False) -> Dict[str, Any]:
    module = JamfLocationCleanupModule(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()
