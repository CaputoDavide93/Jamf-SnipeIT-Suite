"""
Pending Reconciliation Module

Self-heals assets left stuck in Pending status whose owner is demonstrably
still active. This happens when a user is un-ghosted (rehired) but their asset
was never flipped back from Pending — e.g. rehires processed before the
asset-restore feature existed, or manual un-tagging. Because rehire-detection
only scans users currently tagged [Disabled], these already-active users are
invisible to it and their machines sit in Pending indefinitely.

An asset is restored (Pending -> Deployed) only when its assigned Snipe-IT
user is BOTH:
  - not tagged [Disabled] in Snipe-IT, and
  - active in Azure AD (by email).

Requiring the Azure AD confirmation means a genuine leaver is never restored;
owners who cannot be confirmed active are left untouched for manual review.

Posts a single Slack summary. Silent if nothing to do.
"""
import logging
from typing import Any, Dict, List, Optional, Set

from core.config import Config
from core.client_factory import create_snipeit_client, create_slack_client, create_azure_client

logger = logging.getLogger(__name__)

DISABLED_PREFIX = "[Disabled]"


class PendingReconciliationModule:
    """Restore Pending assets whose owner is still active."""

    def __init__(self, config: Config):
        self.config = config
        self.snipe = create_snipeit_client(config)
        self.slack = create_slack_client(config)
        self.azure = create_azure_client(config)

    def close(self) -> None:
        self.snipe.close()
        self.azure.close()

    # ------------------------------------------------------------------
    def _active_azure_emails(self) -> Set[str]:
        emails: Set[str] = set()
        for u in self.azure.get_all_active_users():
            for key in ("mail", "userPrincipalName"):
                v = (u.get(key) or "").lower().strip()
                if v:
                    emails.add(v)
        return emails

    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        mode = "DRY RUN" if dry_run else "LIVE RUN"
        logger.info(f"Starting Pending Reconciliation ({mode})")

        results: Dict[str, Any] = {
            "pending_total": 0,
            "candidates": 0,
            "restored": 0,
            "failures": 0,
            "skipped_disabled": 0,
            "skipped_unconfirmed": 0,
            "details": [],
        }

        pending_id = self.config.snipeit.status_pending_id
        deployed_id = self.config.snipeit.status_deployed_id

        all_assets = self.snipe.get_all_assets()
        pending = [
            a for a in all_assets
            if isinstance(a.get("status_label"), dict)
            and a["status_label"].get("name") == "Pending"
        ]
        results["pending_total"] = len(pending)

        active_emails = self._active_azure_emails()
        logger.info(
            f"{len(pending)} Pending assets; {len(active_emails)} active Azure AD users"
        )

        for asset in pending:
            at = asset.get("assigned_to")
            if not isinstance(at, dict) or not at.get("id"):
                continue  # orphan Pending — not our concern here
            name = at.get("name") or ""
            email = (at.get("email") or "").lower().strip()

            if name.startswith(DISABLED_PREFIX):
                results["skipped_disabled"] += 1
                continue
            if not email or email not in active_emails:
                results["skipped_unconfirmed"] += 1
                logger.info(
                    f"  Skipping asset {asset.get('id')} ({asset.get('serial')}) — "
                    f"owner '{name}' not confirmed active in Azure AD"
                )
                continue

            results["candidates"] += 1
            outcome = self._restore(asset, at, pending_id, deployed_id, dry_run, results)
            if outcome is None:
                continue  # re-verification said there was nothing to do
            if outcome:
                results["restored"] += 1
            else:
                results["failures"] += 1

        self._summarise(results, dry_run)
        return results

    # ------------------------------------------------------------------
    def _restore(self, asset, assigned, pending_id, deployed_id, dry_run, results) -> Optional[bool]:
        """Re-verify then flip Pending -> Deployed (assignment is preserved).

        Returns True on restore, False on failure, and None when
        re-verification found nothing to do (neither restored nor failed).
        """
        asset_id = asset.get("id")
        label = asset.get("serial") or asset.get("asset_tag") or asset_id
        owner_id = assigned.get("id")
        owner_name = assigned.get("name")

        # Re-fetch and re-verify to avoid acting on stale list data
        current = self.snipe.get_asset_by_id(asset_id)
        if not current:
            logger.error(f"Could not verify asset {label} before restore")
            return False
        status = current.get("status_label")
        status_id = status.get("id") if isinstance(status, dict) else status
        try:
            status_id = int(status_id)
        except (TypeError, ValueError):
            status_id = None
        if status_id != pending_id:
            logger.info(f"  Asset {label} no longer Pending — skipping")
            results["candidates"] -= 1
            return None  # not a failure; nothing to do
        if self.snipe.get_assigned_user_id(current) != int(owner_id):
            logger.info(f"  Asset {label} now assigned elsewhere — skipping")
            results["candidates"] -= 1
            return None

        detail = {"asset_id": asset_id, "serial": asset.get("serial"), "owner": owner_name}
        results["details"].append(detail)

        if dry_run:
            logger.info(f"[DRY-RUN] Would restore asset {label} ({owner_name}): Pending -> Deployed")
            return True

        if self.snipe.update_asset_status(asset_id, deployed_id):
            logger.info(f"Restored asset {label} ({owner_name}): Pending -> Deployed")
            return True
        logger.error(f"Failed to restore asset {label} ({owner_name})")
        return False

    # ------------------------------------------------------------------
    def _summarise(self, results: Dict[str, Any], dry_run: bool) -> None:
        mode = "DRY RUN" if dry_run else "LIVE RUN"
        logger.info(
            f"Pending Reconciliation ({mode}): {results['pending_total']} Pending, "
            f"{results['candidates']} candidates, {results['restored']} restored, "
            f"{results['failures']} failures, {results['skipped_disabled']} disabled-owner, "
            f"{results['skipped_unconfirmed']} unconfirmed"
        )
        if self.slack and not dry_run and results["restored"]:
            lines = [
                f"• {d['serial']} → {d['owner']}" for d in results["details"]
            ]
            self.slack.send(
                f"*Pending Reconciliation*: restored {results['restored']} asset(s) "
                f"whose owner is active again:\n" + "\n".join(lines)
            )


def run_pending_reconciliation(config: Config, dry_run: bool = False) -> Dict[str, Any]:
    module = PendingReconciliationModule(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()
