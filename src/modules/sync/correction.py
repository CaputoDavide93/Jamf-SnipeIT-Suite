"""
Jamf-SnipeIT Suite - Self-Healing Correction Module
Detects and fixes wrong matches/assignments from previous runs.

Runs BEFORE User Match on each execution to:
1. Fetch all checked-out Snipe-IT assets
2. For each, look up the Jamf computer by serial and re-compute the expected user
3. Compare the current Snipe-IT assignment against the fresh match
4. If they disagree, log the mismatch and optionally correct it
5. Produce a detailed audit CSV of all corrections
"""
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from core.config import Config
from core.client_factory import create_jamf_client, create_snipeit_client, create_slack_client, load_user_overrides
from infra.audit_csv import AuditCSV
from infra.progress import ProgressTracker
from infra.helpers import rate_limit_delay
from matching.user_matcher import UserMatcher, pick_primary_local_identity
from matching.ai_resolver import AIResolver


logger = logging.getLogger(__name__)


class CorrectionModule:
    """
    Self-healing module that validates existing Snipe-IT asset assignments
    against freshly computed user matches and corrects mismatches.
    """

    def __init__(self, config: Config):
        self.config = config
        self.settings = config.modules.get("correction", {})
        self.batch_size = self.settings.get("batch_size", 50)
        self.batch_delay = self.settings.get("batch_delay_seconds", 2)

        # Clients (centralised factory)
        self.jamf = create_jamf_client(config)
        self.snipe = create_snipeit_client(config)

        self._user_matcher: Optional[UserMatcher] = None

        self.slack = create_slack_client(config)
        # Cache for Jamf computer details (serial → dict), populated lazily
        self._jamf_cache: Dict[str, Optional[Dict]] = {}
        # Azure leaver/disabled emails (loaded lazily on first validation)
        self._azure_inactive_emails: Optional[set] = None

    def _load_azure_inactive(self) -> set:
        """Load set of emails for leavers+disabled Azure users."""
        if self._azure_inactive_emails is not None:
            return self._azure_inactive_emails
        emails = set()
        try:
            from clients.azure import AzureClient
            az = AzureClient(
                tenant_id=self.config.azure.tenant_id,
                client_id=self.config.azure.client_id,
                client_secret=self.config.azure.client_secret,
                scope=self.config.azure.scope,
                timeout=self.config.api.timeout_seconds,
            )
            for gid in (self.config.azure.leavers_group_id, self.config.azure.disabled_group_id):
                if not gid:
                    continue
                for u in az.get_group_members(gid):
                    e = AzureClient.extract_email(u)
                    if e:
                        emails.add(e.lower())
            az.close()
        except Exception as e:
            logger.warning(f"Could not load Azure inactive groups: {e}")
        self._azure_inactive_emails = emails
        return emails

    def _is_inactive_user(self, user: Dict[str, Any]) -> bool:
        """User is inactive if [Disabled] prefix OR in Azure leaver/disabled groups."""
        if not isinstance(user, dict):
            return False
        name = user.get("name") or ""
        if name.startswith("[Disabled]"):
            return True
        email = (user.get("email") or "").lower()
        return email in self._load_azure_inactive()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Run the self-healing correction pass.

        Args:
            dry_run: If True, report mismatches but make no changes.

        Returns:
            Results dictionary with statistics and details.
        """
        logger.info(f"Starting Self-Healing Correction: dry_run={dry_run}")

        results: Dict[str, Any] = {
            "total_assets_checked": 0,
            "correct_assignments": 0,
            "mismatches_found": 0,
            "corrections_made": 0,
            "unassigned_skipped": 0,
            "no_jamf_device": 0,
            "no_fresh_match": 0,
            "pending_skipped": 0,
            "errors": 0,
            "details": [],  # list of per-asset dicts
        }

        # 1. Fetch ALL Snipe-IT assets (one bulk call)
        logger.debug("Fetching all Snipe-IT assets...")
        all_assets = self.snipe.get_all_assets()
        logger.debug(f"Retrieved {len(all_assets)} assets")

        # 2. Filter to only checked-out / deployed assets
        checked_out_assets = []
        pending_id = self.config.snipeit.status_pending_id
        for asset in all_assets:
            assigned_user_id = self.snipe.get_assigned_user_id(asset)
            if not assigned_user_id:
                results["unassigned_skipped"] += 1
                continue

            # Pending assets: normally skip (set by Leavers, manual workflow).
            # EXCEPTION: if current assignee is [Disabled], the machine was probably
            # reassigned to someone else — let validation run so we can fix it.
            status_label = asset.get("status_label")
            status_id = None
            if isinstance(status_label, dict):
                status_id = status_label.get("id")
            if status_id and status_id == pending_id:
                assigned_to = asset.get("assigned_to") or {}
                if not self._is_inactive_user(assigned_to):
                    # Pending + active assignee — genuine manual hold, skip
                    results["pending_skipped"] += 1
                    continue
                # else: Pending + inactive (disabled/leaver) → validate

            checked_out_assets.append((asset, assigned_user_id))

        logger.info(
            f"Found {len(checked_out_assets)} checked-out assets to validate "
            f"({results['unassigned_skipped']} unassigned, "
            f"{results['pending_skipped']} pending — skipped)"
        )

        if not checked_out_assets:
            self._print_summary(results, dry_run)
            return results

        # 3. Prepare audit CSV
        audit = AuditCSV(
            log_dir=self.config.logging.dir,
            module_name="correction",
            enabled=self.config.logging.audit_csv,
            headers=[
                "timestamp",
                "serial",
                "asset_id",
                "asset_name",
                "current_user_id",
                "current_user_name",
                "expected_user_id",
                "expected_user_name",
                "jamf_username",
                "match_reason",
                "action",
                "result",
                "notes",
            ],
        )

        progress = ProgressTracker("Correction", total=len(checked_out_assets), log_every=50)
        
        try:
            for i, (asset, current_uid) in enumerate(checked_out_assets, 1):
                results["total_assets_checked"] += 1

                try:
                    self._validate_asset(
                        asset, current_uid, dry_run, results, audit
                    )
                except Exception as e:
                    logger.exception(
                        f"Error validating asset {asset.get('id')}: {e}"
                    )
                    results["errors"] += 1
                    audit.write(
                        serial=asset.get("serial", ""),
                        asset_id=str(asset.get("id", "")),
                        action="error",
                        result="error",
                        notes=str(e),
                    )

                progress.advance()

                # Batch delay
                if i % self.batch_size == 0 and i < len(checked_out_assets):
                    batch_num = i // self.batch_size
                    total_batches = (
                        len(checked_out_assets) + self.batch_size - 1
                    ) // self.batch_size
                    rate_limit_delay(
                        self.batch_delay, "Correction", batch_num, total_batches
                    )
        finally:
            audit.close()
        
        progress.finish(extra=f"mismatches={results['mismatches_found']}, corrected={results['corrections_made']}, errors={results['errors']}")

        self._print_summary(results, dry_run)

        # Send Slack notification for mismatches needing investigation
        if results.get("details"):
            mismatch_items = [
                d for d in results["details"] if d.get("type") == "mismatch"
            ]
            if mismatch_items:
                self.slack.notify_investigation_needed(
                    channel_id=self.config.slack.channel_id,
                    title="Correction - Assignment Mismatches Need Investigation",
                    items=mismatch_items,
                )

        return results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_user_matcher(self) -> UserMatcher:
        """Lazy-initialise the UserMatcher with Snipe-IT + Azure AD users."""
        if self._user_matcher is None:
            logger.debug("Loading Snipe-IT users for correction matching...")
            users = self.snipe.get_all_users()

            # Load Azure AD for cross-platform matching
            azure_users = []
            try:
                from clients.azure import AzureClient
                azure = AzureClient(
                    tenant_id=self.config.azure.tenant_id,
                    client_id=self.config.azure.client_id,
                    client_secret=self.config.azure.client_secret,
                    scope=self.config.azure.scope,
                    timeout=self.config.api.timeout_seconds,
                )
                if self.config.azure.starters_group_id:
                    azure_users = azure.get_group_members(self.config.azure.starters_group_id)
                azure.close()
            except Exception as e:
                logger.warning(f"Could not load Azure AD users: {e}")

            ai_api_key = getattr(self.config, 'ai_api_key', '') or os.environ.get('AI_API_KEY', '')
            ai_resolver = AIResolver(api_key=ai_api_key, slack=self.slack) if ai_api_key else None

            self._user_matcher = UserMatcher(
                users=users,
                email_domain=self.config.matching.email_domain,
                min_score=self.config.matching.min_score,
                weight_lcs=self.config.matching.weight_lcs,
                weight_char_overlap=self.config.matching.weight_char_overlap,
                weight_bigram_dice=self.config.matching.weight_bigram_dice,
                use_bigram_dice=self.config.matching.use_bigram_dice,
                ai_resolver=ai_resolver,
                azure_users=azure_users,
                overrides=load_user_overrides(),
            )
            logger.debug(f"Loaded {len(users)} Snipe-IT + {len(azure_users)} Azure AD users")
        return self._user_matcher

    def _validate_asset(
        self,
        asset: Dict[str, Any],
        current_uid: int,
        dry_run: bool,
        results: Dict[str, Any],
        audit: AuditCSV,
    ) -> None:
        """Validate a single checked-out asset against fresh Jamf data."""

        asset_id = asset.get("id")
        serial = (asset.get("serial") or "").strip()
        asset_name = asset.get("name") or asset.get("asset_tag") or str(asset_id)

        # Resolve current assigned user name for logging
        assigned_to = asset.get("assigned_to")
        current_user_name = ""
        if isinstance(assigned_to, dict):
            current_user_name = assigned_to.get("name", "")

        if not serial:
            logger.debug(f"Asset {asset_id} has no serial, skipping correction")
            return

        # ---- Look up the Jamf computer by serial (with cache) ----
        if serial.upper() not in self._jamf_cache:
            self._jamf_cache[serial.upper()] = self.jamf.get_computer_by_serial(serial)
        jamf_comp = self._jamf_cache[serial.upper()]
        if not jamf_comp:
            # Asset exists in Snipe-IT but not in Jamf — can't validate
            results["no_jamf_device"] += 1
            logger.debug(f"Asset {asset_id} ({serial}) '{asset_name}': no matching Jamf computer — skipping")
            return

        # Extract local user from Jamf (same logic as User Match)
        groups_accounts = jamf_comp.get("groups_accounts", {}) or {}
        local_users = (
            groups_accounts.get("local_accounts")
            or groups_accounts.get("local_users")
            or groups_accounts.get("users")
            or []
        )
        if isinstance(local_users, dict) and "user" in local_users:
            local_users = local_users["user"]
        elif isinstance(local_users, dict):
            local_users = [local_users]
        elif not isinstance(local_users, list):
            local_users = []

        primary_username, full_name_hint, original_email = pick_primary_local_identity(
            local_users,
            skip_usernames=self.config.matching.skip_usernames,
            location=jamf_comp.get("location"),
        )

        if not primary_username:
            # No primary user in Jamf — nothing to match against
            results["no_fresh_match"] += 1
            return

        # Skip generic/shared usernames
        skip_usernames = self.config.matching.skip_usernames  # already lowercase
        if primary_username.lower() in skip_usernames:
            results["no_fresh_match"] += 1
            return

        # ---- Re-compute the correct user match ----
        matcher = self._get_user_matcher()
        fresh_match, debug_info = matcher.best_match(
            full_name_hint=full_name_hint or "",
            username=primary_username,
            original_email=original_email or "",
        )

        # Rejected due to ambiguity — can't validate
        if debug_info.get("rejected_reason"):
            results["no_fresh_match"] += 1
            return

        if not fresh_match:
            # No confident match today — leave the existing assignment alone
            results["no_fresh_match"] += 1
            return

        expected_uid = int(fresh_match.get("id", 0))
        expected_user_name = fresh_match.get("name", "")
        match_reason = debug_info.get("exact_hit_reason", "fuzzy")

        # ---- Compare ----
        if current_uid == expected_uid:
            results["correct_assignments"] += 1
            logger.debug(
                f"Asset {asset_id} ({serial}): correctly assigned to "
                f"user {current_uid} ({current_user_name})"
            )
            return

        # ---- Safety: never reassign FROM an active user TO a disabled user ----
        current_is_disabled = current_user_name.strip().startswith("[Disabled]")
        expected_is_disabled = expected_user_name.strip().startswith("[Disabled]")

        # Jamf local account is source of truth — always trust it.
        # If local matches a [Disabled] user, the machine is still theirs
        # (notice period, returning it, etc.). Reassign regardless.

        # ---- Safety: only auto-correct on EXACT matches ----
        # Fuzzy and AI matches can be wrong (e.g. Jane Winters -> Jane Porter
        # when it should be Jane Sommers who changed surname).
        # If current assignment is to an active user and the match is fuzzy/AI,
        # DON'T auto-correct — just report to Slack for investigation.
        is_exact_match = match_reason and match_reason.startswith(("full_name=", "email=", "email_prefix=", "username=", "username_normalized=", "override"))
        if not is_exact_match and not current_is_disabled:
            logger.info(
                f"Asset {asset_id} ({serial}): fuzzy/AI match suggests "
                f"'{expected_user_name}' but currently assigned to active user "
                f"'{current_user_name}' — keeping current, sending to Slack"
            )
            results["details"].append({
                "type": "mismatch",
                "description": (
                    f"`{serial}` *{asset_name}*\n"
                    f"      Currently: *{current_user_name}* (active)\n"
                    f"      Local account `{primary_username}` fuzzy-matches: *{expected_user_name}*\n"
                    f"      Match type: {match_reason}\n"
                    f"      Action: Kept current assignment — needs manual review"
                ),
            })
            results["correct_assignments"] += 1
            return

        # ---- Mismatch detected (exact match disagrees with assignment) ----
        # Auto-correctable cases DON'T go to Slack — Slack only for manual-review.
        # Will be appended to results["details"] only if correction fails below.
        results["mismatches_found"] += 1
        mismatch_desc = (
            f"`{serial}` *{asset_name}*\n"
            f"      Currently: *{current_user_name}*\n"
            f"      Local account `{primary_username}` matches: *{expected_user_name}*\n"
            f"      Match type: {match_reason}"
        )
        logger.warning(f"MISMATCH: {mismatch_desc}")

        if dry_run:
            audit.write(
                serial=serial,
                asset_id=str(asset_id),
                asset_name=asset_name,
                current_user_id=str(current_uid),
                current_user_name=current_user_name,
                expected_user_id=str(expected_uid),
                expected_user_name=expected_user_name,
                jamf_username=primary_username,
                match_reason=match_reason,
                action="mismatch_detected",
                result="dry_run",
                notes="Would reassign",
            )
            return

        # ---- Correct the assignment ----
        # Check-in first, then checkout to the correct user
        checkin_ok = self.snipe.checkin_asset(
            asset_id, note="Self-healing correction: wrong user assignment detected"
        )
        if not checkin_ok:
            logger.error(f"Correction failed: could not check-in asset {asset_id}")
            results["errors"] += 1
            results["details"].append({
                "type": "mismatch",
                "description": f"{mismatch_desc}\n      *Action failed:* Check-in failed — manual fix needed",
            })
            audit.write(
                serial=serial,
                asset_id=str(asset_id),
                asset_name=asset_name,
                current_user_id=str(current_uid),
                current_user_name=current_user_name,
                expected_user_id=str(expected_uid),
                expected_user_name=expected_user_name,
                jamf_username=primary_username,
                match_reason=match_reason,
                action="correct",
                result="error",
                notes="Check-in failed",
            )
            return

        checkout_ok = self.snipe.checkout_asset(
            asset_id,
            expected_uid,
            note=f"Self-healing correction: reassigned from user {current_uid} to {expected_uid}",
        )
        if checkout_ok:
            results["corrections_made"] += 1
            # If asset was Pending + disabled previous owner, clear to Checked Out
            # since new active user now has it
            asset_status = asset.get("status_label") or {}
            if isinstance(asset_status, dict) and asset_status.get("id") == self.config.snipeit.status_pending_id:
                prev_assignee = asset.get("assigned_to") or {}
                if self._is_inactive_user(prev_assignee):
                    self.snipe.update_asset_status(asset_id, self.config.snipeit.status_deployed_id)
                    logger.info(f"Cleared Pending status on asset {asset_id} (now active user)")
            logger.info(
                f"CORRECTED: Asset {asset_id} ({serial}) reassigned "
                f"from user {current_uid} ({current_user_name}) "
                f"to user {expected_uid} ({expected_user_name})"
            )
            audit.write(
                serial=serial,
                asset_id=str(asset_id),
                asset_name=asset_name,
                current_user_id=str(current_uid),
                current_user_name=current_user_name,
                expected_user_id=str(expected_uid),
                expected_user_name=expected_user_name,
                jamf_username=primary_username,
                match_reason=match_reason,
                action="correct",
                result="ok",
                notes="Reassigned successfully",
            )
        else:
            # Rollback: re-checkout to the original user to avoid orphaned asset
            logger.warning(
                f"Checkout to user {expected_uid} failed — rolling back "
                f"to original user {current_uid}"
            )
            rollback_ok = self.snipe.checkout_asset(
                asset_id, current_uid,
                note=f"Rollback: checkout to {expected_uid} failed, restoring original assignment",
            )
            if rollback_ok:
                logger.info(f"Rollback successful: asset {asset_id} back to user {current_uid}")
                results["details"].append({
                    "type": "mismatch",
                    "description": f"{mismatch_desc}\n      *Action failed:* Checkout to expected user failed, restored original",
                })
            else:
                logger.error(f"ROLLBACK FAILED: asset {asset_id} is now unassigned!")
                results["details"].append({
                    "type": "mismatch",
                    "description": f"{mismatch_desc}\n      *Action failed:* Checkout AND rollback failed — asset unassigned, manual fix needed",
                })

            results["errors"] += 1
            audit.write(
                serial=serial,
                asset_id=str(asset_id),
                asset_name=asset_name,
                current_user_id=str(current_uid),
                current_user_name=current_user_name,
                expected_user_id=str(expected_uid),
                expected_user_name=expected_user_name,
                jamf_username=primary_username,
                match_reason=match_reason,
                action="correct",
                result="error",
                notes="Checkout failed after check-in — asset unassigned!",
            )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _print_summary(self, results: Dict[str, Any], dry_run: bool) -> None:
        mode = "DRY RUN" if dry_run else "LIVE RUN"

        logger.info(
            f"Correction ({mode}): {results['total_assets_checked']} checked, "
            f"{results['correct_assignments']} correct, "
            f"{results['mismatches_found']} mismatches, "
            f"{results['corrections_made']} corrected, "
            f"{results['errors']} errors"
        )

    def close(self) -> None:
        """Clean up resources."""
        self.jamf.close()
        self.snipe.close()


# ======================================================================
# Convenience function
# ======================================================================


def run_correction(
    config: Config, dry_run: bool = False
) -> Dict[str, Any]:
    """
    Convenience function to run the self-healing correction module.

    Args:
        config: Suite configuration
        dry_run: If True, don't make changes

    Returns:
        Results dictionary
    """
    module = CorrectionModule(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()
