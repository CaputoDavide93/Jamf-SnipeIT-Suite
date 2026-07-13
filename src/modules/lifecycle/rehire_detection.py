"""
Jamf-SnipeIT Suite - Rehire Detection Module

Reverses the one-way [Disabled] tagging performed by the Leavers module.

Leavers adds a "[Disabled]" prefix to the Snipe-IT user's first name and
marks their assets Pending when they leave — but nothing ever undoes this
when a person is re-hired (or was disabled by mistake). This module scans
every Snipe-IT user tagged [Disabled] and, when their Azure AD account is
demonstrably active again, restores them:

  REHIRE (fix automatically) — ALL of:
    - Azure AD accountEnabled == true
    - NOT a member of the leavers group  (azure.leavers_group_id)
    - NOT a member of the disabled group (azure.disabled_group_id)
    - employeeLeaveDateTime absent or in the future
    -> strip "[Disabled] " prefix, restore their Pending assets to Deployed

  AMBIGUOUS (report only, never touch) — AAD enabled but still in the
    leavers/disabled group, or leave date already passed. A human must
    resolve the group membership first (e.g. the Daria Szafulska case).

Fail-safe rules:
  - If the leavers/disabled group memberships cannot be loaded, the run
    aborts — without the exclusion lists a genuine rehire cannot be proven.
  - If Snipe-IT returns zero users, the run aborts (fetch failure).
  - An unparseable employeeLeaveDateTime counts as "leave date passed"
    (do NOT auto-restore).
  - Renames are verified by re-reading the user, because Snipe-IT can
    return HTTP 200 with a JSON error body.

MUST run BEFORE Leavers in any pipeline so a returning employee is
un-tagged before Leavers re-evaluates.
"""
import logging
from typing import Any, Dict, Optional, Set, Tuple

from core.config import Config
from clients.azure import AzureClient
from clients.snipeit import SnipeITClient
from clients.slack import SlackClient
from infra.helpers import leave_date_passed

logger = logging.getLogger(__name__)

DISABLED_PREFIX = "[Disabled]"


class RehireDetectionModule:
    """Detect re-hired users still tagged [Disabled] in Snipe-IT and restore them."""

    def __init__(self, config: Config):
        self.config = config
        self.settings = config.modules.get("rehire_detection", {})

        self.azure = AzureClient(
            tenant_id=config.azure.tenant_id,
            client_id=config.azure.client_id,
            client_secret=config.azure.client_secret,
            scope=config.azure.scope,
            timeout=config.api.timeout_seconds,
            max_retries=config.api.max_retries,
            retry_delay=config.api.retry_delay_seconds,
        )
        self.snipe = SnipeITClient(
            base_url=config.snipeit.base_url,
            api_token=config.snipeit.api_token,
            timeout=config.api.timeout_seconds,
            max_retries=config.api.max_retries,
            retry_delay=config.api.retry_delay_seconds,
            rate_limit_wait=config.api.rate_limit_wait_seconds,
        )
        self.slack = SlackClient(
            bot_token=config.slack.bot_token,
            channel_id=config.slack.channel_id,
            enabled=config.slack.enabled and config.slack.notify_inline,
        )

    # ------------------------------------------------------------------
    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        # Config-level dry_run acts as a safety latch: a caller can only
        # force dry-run ON, never off. First scheduled runs stay read-only
        # until modules.rehire_detection.dry_run is flipped to false.
        if self.settings.get("dry_run", True):
            if not dry_run:
                logger.info(
                    "rehire_detection.dry_run is true in config — forcing DRY RUN "
                    "(set modules.rehire_detection.dry_run: false to go live)"
                )
            dry_run = True

        mode = "DRY RUN" if dry_run else "LIVE RUN"
        logger.info(f"Starting Rehire Detection ({mode})")

        results: Dict[str, Any] = {
            "disabled_tagged_users": 0,
            "rehires_detected": 0,
            "names_restored": 0,
            "assets_restored": 0,
            "asset_restore_failures": 0,
            "ambiguous": [],       # AAD enabled but still in leavers/disabled group
            "rehired_users": [],   # {name, email, snipe_id, assets_restored}
            "errors": [],
        }

        # ---- Load data ------------------------------------------------
        snipe_users = self.snipe.get_all_users()
        if not snipe_users:
            # Fetch-integrity guard: production Snipe-IT is never empty.
            logger.error("Snipe-IT returned 0 users — aborting (fetch likely failed)")
            results["errors"].append("Snipe-IT user fetch returned 0 users — aborted")
            return results

        tagged = [
            u for u in snipe_users
            if str(u.get("first_name") or u.get("name") or "").startswith(DISABLED_PREFIX)
        ]
        results["disabled_tagged_users"] = len(tagged)
        logger.info(f"Snipe-IT: {len(snipe_users)} users, {len(tagged)} tagged {DISABLED_PREFIX}")

        if not tagged:
            logger.info("No [Disabled]-tagged users — nothing to do")
            return results

        try:
            active_by_email = self._load_active_azure_users()
            leaver_ids, disabled_ids = self._load_exclusion_groups()
        except Exception as e:
            # Fail SAFE: without AAD state and the exclusion lists a genuine
            # rehire cannot be proven, so restore nobody.
            logger.error(f"Could not load Azure AD data — aborting: {e}")
            results["errors"].append(f"azure: {e}")
            return results

        # HiBob is the HR source of truth (read-only — we only ever call the
        # search endpoint, never write). AAD leave dates are unpopulated in
        # this tenant, so a rehire is only auto-restored when HiBob also
        # lists the person as an active employee.
        hibob_active: Optional[Set[str]] = None
        hibob_unavailable = False
        if self.settings.get("hibob_confirmation", True):
            try:
                hibob_active = self._load_hibob_active_emails()
            except Exception as e:
                # Continue classification, but never restore without HR confirmation.
                logger.error(f"HiBob fetch failed — marking all as ambiguous: {e}")
                results["errors"].append(f"hibob: {e}")
                hibob_unavailable = True

        # ---- Classify + restore ---------------------------------------
        for su in tagged:
            try:
                self._process_tagged_user(
                    su, active_by_email, leaver_ids, disabled_ids, dry_run, results,
                    hibob_active=hibob_active,
                    hibob_unavailable=hibob_unavailable,
                )
            except Exception as e:
                name = su.get("name", su.get("id"))
                logger.error(f"Error processing {name}: {e}")
                results["errors"].append(f"{name}: {e}")

        self._print_summary(results, dry_run)

        if not dry_run and (results["rehired_users"] or results["ambiguous"]):
            self._send_slack(results)

        return results

    # ------------------------------------------------------------------
    def _load_active_azure_users(self) -> Dict[str, Dict[str, Any]]:
        """All enabled AAD users keyed by lowercase email, incl. leave date."""
        users = self.azure.get_all_active_users(include_leave_date=True)
        by_email: Dict[str, Dict[str, Any]] = {}
        for u in users:
            email = AzureClient.extract_email(u)
            if email:
                by_email[email] = u
        logger.debug(f"Azure AD: {len(by_email)} enabled users indexed by email")
        return by_email

    def _load_exclusion_groups(self) -> Tuple[Set[str], Set[str]]:
        """AAD object-id sets for the leavers and disabled groups.

        Raises on any failure — the caller aborts the run (fail safe).
        """
        leaver_ids: Set[str] = set()
        disabled_ids: Set[str] = set()
        for gid, bucket, label in (
            (self.config.azure.leavers_group_id, leaver_ids, "leavers"),
            (self.config.azure.disabled_group_id, disabled_ids, "disabled"),
        ):
            if not gid:
                raise RuntimeError(
                    f"azure.{label}_group_id not configured — cannot prove rehires"
                )
            bucket.update(m["id"] for m in self.azure.get_group_members(gid))
        logger.debug(f"Exclusions: {len(leaver_ids)} leavers, {len(disabled_ids)} disabled")
        return leaver_ids, disabled_ids

    def _load_hibob_active_emails(self) -> Optional[Set[str]]:
        """Lowercase emails of employees HiBob lists as ACTIVE.

        STRICTLY READ-ONLY: only /people/search (a read-only search POST)
        is called — HiBob data is never modified.

        Raises if confirmation cannot be performed so the caller can fail safe.
        """
        hb = self.config.hibob
        if not (hb.service_user_id and hb.service_user_token):
            raise RuntimeError(
                "HiBob confirmation enabled but credentials are not configured"
            )

        from clients.hibob import HiBobClient
        client = HiBobClient(
            service_user_id=hb.service_user_id,
            service_user_token=hb.service_user_token,
            timeout=self.config.api.timeout_seconds,
            max_retries=self.config.api.max_retries,
            retry_delay=self.config.api.retry_delay_seconds,
        )
        try:
            employees = client.search_employees(
                fields_to_fetch=["root.id", "root.email", "work.email"],
                show_inactive=False,  # active employees only
            )
            emails: Set[str] = set()
            for emp in employees:
                for key in ("/work/email", "/root/email"):
                    v = emp.get(key)
                    if isinstance(v, dict):
                        v = v.get("value")
                    if v:
                        emails.add(str(v).lower().strip())
                v = emp.get("email")
                if v:
                    emails.add(str(v).lower().strip())
            if not emails:
                raise RuntimeError("HiBob returned 0 active employee emails")
            logger.debug(f"HiBob: {len(emails)} active employee emails")
            return emails
        finally:
            client.close()

    # ------------------------------------------------------------------
    @staticmethod
    def _classify(
        azure_user: Optional[Dict[str, Any]],
        leaver_ids: Set[str],
        disabled_ids: Set[str],
        email: str = "",
        hibob_active: Optional[Set[str]] = None,
        hibob_unavailable: bool = False,
    ) -> Optional[str]:
        """Classify a [Disabled]-tagged Snipe user against AAD + HiBob state.

        Returns:
            None       — tag is correct (AAD account disabled/deleted)
            "rehire"   — safe to restore automatically
            "ambiguous: <reason>" — report only, never touch
        """
        if not azure_user:
            return None
        if azure_user.get("id") in leaver_ids:
            return "ambiguous: still in leavers group"
        if azure_user.get("id") in disabled_ids:
            return "ambiguous: still in disabled group"
        # Unparseable leave date -> treat as passed (do NOT auto-restore)
        if leave_date_passed(azure_user, default_on_invalid=True):
            return "ambiguous: leave date passed"
        if hibob_unavailable:
            return "ambiguous: HiBob unavailable; HR confirmation required"
        # HR source of truth must agree before an automatic restore.
        if hibob_active is not None and email and email not in hibob_active:
            return "ambiguous: not active in HiBob (HR source of truth)"
        return "rehire"

    def _process_tagged_user(
        self,
        snipe_user: Dict[str, Any],
        active_by_email: Dict[str, Dict[str, Any]],
        leaver_ids: Set[str],
        disabled_ids: Set[str],
        dry_run: bool,
        results: Dict[str, Any],
        hibob_active: Optional[Set[str]] = None,
        hibob_unavailable: bool = False,
    ) -> None:
        email = (snipe_user.get("email") or "").lower().strip()
        if not email:
            return  # cannot correlate without an email

        verdict = self._classify(
            active_by_email.get(email), leaver_ids, disabled_ids,
            email=email, hibob_active=hibob_active,
            hibob_unavailable=hibob_unavailable,
        )
        if verdict is None:
            return  # AAD account disabled or deleted -> tag is correct

        display = snipe_user.get("name", email)

        if verdict.startswith("ambiguous"):
            reason = verdict.split(": ", 1)[1]
            logger.warning(f"AMBIGUOUS rehire (not touching): {display} ({email}) — {reason}")
            results["ambiguous"].append({
                "snipe_id": snipe_user.get("id"),
                "name": display,
                "email": email,
                "reason": reason,
            })
            return

        # ---- Genuine rehire -------------------------------------------
        results["rehires_detected"] += 1
        logger.info(f"REHIRE detected: {display} ({email}) — AAD enabled, no leaver signals")

        if self._restore_user_name(snipe_user, dry_run):
            results["names_restored"] += 1
        else:
            results["errors"].append(
                f"user {snipe_user.get('id')}: failed to restore disabled name"
            )

        restored, restore_failures = self._restore_pending_assets(snipe_user, dry_run)
        results["assets_restored"] += restored
        results["asset_restore_failures"] += restore_failures

        if restore_failures:
            results["ambiguous"].append({
                "snipe_id": snipe_user.get("id"),
                "name": display,
                "email": email,
                "reason": f"{restore_failures} Pending asset restore(s) failed",
            })
            results["errors"].append(
                f"user {snipe_user.get('id')}: {restore_failures} asset restore failures"
            )

        results["rehired_users"].append({
            "snipe_id": snipe_user.get("id"),
            "name": display,
            "email": email,
            "assets_restored": restored,
        })

    # ------------------------------------------------------------------
    def _restore_user_name(self, snipe_user: Dict[str, Any], dry_run: bool) -> bool:
        """Strip the [Disabled] prefix from the field Leavers wrote it to."""
        user_id = snipe_user.get("id")
        field = (
            "first_name"
            if str(snipe_user.get("first_name") or "").startswith(DISABLED_PREFIX)
            else "name"
        )
        current = str(snipe_user.get(field) or "")
        if not current.startswith(DISABLED_PREFIX):
            return False
        restored = current[len(DISABLED_PREFIX):].lstrip()

        if dry_run:
            logger.info(f"[DRY-RUN] Would rename user {user_id}: {current!r} -> {restored!r}")
            return True

        if not self.snipe.update_user(user_id, {field: restored}):
            logger.error(f"Failed to rename user {user_id}")
            return False

        # Belt-and-braces: verify the prefix is actually gone (update_user
        # now checks the JSON status, but a re-read costs one GET and makes
        # the restore provable in the logs).
        refreshed = self.snipe.get_user_by_id(user_id) or {}
        if str(refreshed.get(field) or "").startswith(DISABLED_PREFIX):
            logger.error(f"Rename silently failed for user {user_id} — prefix still present")
            return False

        logger.info(f"Renamed user {user_id}: {current!r} -> {restored!r}")
        return True

    def _restore_pending_assets(
        self,
        snipe_user: Dict[str, Any],
        dry_run: bool,
    ) -> Tuple[int, int]:
        """Set the user's Pending assets (still assigned to them) back to Deployed."""
        user_id = snipe_user.get("id")
        if not user_id:
            return 0, 1
        pending_id = self.config.snipeit.status_pending_id
        deployed_id = self.config.snipeit.status_deployed_id
        restored = 0
        failures = 0

        for asset in self.snipe.get_user_assets(user_id):
            asset_id = asset.get("id")
            asset_name = asset.get("name") or asset.get("asset_tag") or asset_id

            # Only touch assets Leavers parked at Pending
            status = asset.get("status_label")
            status_id = status.get("id") if isinstance(status, dict) else status
            try:
                status_id = int(status_id) if status_id is not None else None
            except (TypeError, ValueError):
                status_id = None
            if status_id != pending_id:
                logger.debug(f"Asset {asset_name} not Pending (status={status_id}) — skipping")
                continue

            # Verify still assigned to this user (mirror of Leavers' check)
            current = self.snipe.get_asset_by_id(asset_id)
            if not current:
                logger.error("Could not verify asset %s before rehire restore", asset_id)
                failures += 1
                continue
            assigned_id = self.snipe.get_assigned_user_id(current)
            if assigned_id != int(user_id):
                logger.debug(f"Asset {asset_name} now assigned elsewhere — skipping")
                continue
            current_status = current.get("status_label")
            current_status_id = (
                current_status.get("id")
                if isinstance(current_status, dict)
                else current_status
            )
            try:
                current_status_id = int(current_status_id)
            except (TypeError, ValueError):
                current_status_id = None
            if current_status_id != pending_id:
                logger.debug(
                    "Asset %s no longer Pending after verification — skipping",
                    asset_name,
                )
                continue

            if dry_run:
                logger.info(f"[DRY-RUN] Would restore asset {asset_name}: Pending -> Deployed")
                restored += 1
                continue

            if self.snipe.update_asset_status(asset_id, deployed_id):
                logger.info(f"Restored asset {asset_name}: Pending -> Deployed")
                restored += 1
            else:
                logger.error(f"Failed to restore asset {asset_name} for user {user_id}")
                failures += 1

        return restored, failures

    # ------------------------------------------------------------------
    def _print_summary(self, results: Dict[str, Any], dry_run: bool) -> None:
        mode = "DRY RUN" if dry_run else "LIVE RUN"
        parts = [
            f"Rehire Detection ({mode}): {results['disabled_tagged_users']} tagged users",
            f"{results['rehires_detected']} rehires",
            f"{results['names_restored']} names restored",
            f"{results['assets_restored']} assets restored",
            f"{results['asset_restore_failures']} asset restore failures",
            f"{len(results['ambiguous'])} ambiguous",
        ]
        if results["errors"]:
            parts.append(f"{len(results['errors'])} errors")
        logger.info(", ".join(parts))

        for amb in results["ambiguous"]:
            logger.warning(
                f"  NEEDS HUMAN: {amb['name']} ({amb['email']}) — {amb['reason']}"
            )

    def _send_slack(self, results: Dict[str, Any]) -> None:
        try:
            lines = ["*Rehire Detection*"]
            for u in results["rehired_users"]:
                lines.append(
                    f"• Restored `{u['email']}` ({u['assets_restored']} asset(s) re-deployed)"
                )
            for a in results["ambiguous"]:
                lines.append(f"• Needs review: `{a['email']}` — {a['reason']}")
            self.slack.send("\n".join(lines))
        except Exception as e:
            logger.warning(f"Slack notification failed: {e}")

    def close(self) -> None:
        """Clean up resources."""
        self.azure.close()
        self.snipe.close()


def run_rehire_detection(config: Config, dry_run: bool = False) -> Dict[str, Any]:
    """
    Convenience function to run the Rehire Detection module.

    Args:
        config: Suite configuration
        dry_run: If True, don't make changes (config dry_run can also force this on)

    Returns:
        Results dictionary
    """
    module = RehireDetectionModule(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()
