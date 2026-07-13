"""
Jamf-SnipeIT Suite - Cleanup Module
Detects and merges duplicate Snipe-IT users.

Strategy for each duplicate pair:
1. Identify the "keeper" (has assets, or higher ID if neither has assets)
2. Reassign any assets from the "loser" to the "keeper"
3. Delete the "loser" account

Also cleans up junk accounts (package_*, system accounts).
"""
import logging
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from core.config import Config
from clients.snipeit import SnipeITClient
from infra.audit_csv import AuditCSV

logger = logging.getLogger(__name__)


class CleanupModule:
    """Detect and merge duplicate Snipe-IT users."""

    def __init__(self, config: Config):
        self.config = config
        self.snipe = SnipeITClient(
            base_url=config.snipeit.base_url,
            api_token=config.snipeit.api_token,
            timeout=config.api.timeout_seconds,
            max_retries=config.api.max_retries,
            retry_delay=config.api.retry_delay_seconds,
            rate_limit_wait=config.api.rate_limit_wait_seconds,
        )

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    def find_duplicates(self, users: List[Dict]) -> List[List[Dict]]:
        """
        Find groups of users that share the same non-empty email address.

        Returns list of groups, each group is a list of user dicts.
        Only groups with 2+ members are returned.
        """
        by_email: Dict[str, List[Dict]] = {}

        for u in users:
            email = (u.get("email") or "").strip().lower()
            if not email or "@" not in email:
                continue
            by_email.setdefault(email, []).append(u)

        return [group for group in by_email.values() if len(group) >= 2]

    def find_junk_accounts(self, users: List[Dict]) -> List[Dict]:
        """Find package_* and other junk/system accounts."""
        junk = []
        for u in users:
            name = (u.get("name") or "").strip().lower()
            username = (u.get("username") or "").strip().lower()
            if name.startswith("package_") or username.startswith("package_"):
                junk.append(u)
        return junk

    # ------------------------------------------------------------------
    # Merge logic
    # ------------------------------------------------------------------

    @staticmethod
    def pick_keeper(group: List[Dict]) -> Tuple[Dict, List[Dict]]:
        """
        Pick the best user to keep from a duplicate group.

        Priority:
        1. User with assets assigned
        2. User with more complete data (email, dept, jobtitle)
        3. Higher ID (more recent = likely created by Azure Starters)
        """
        def score(u: Dict) -> Tuple[int, int, int]:
            has_assets = 1 if (u.get("assets_count") or 0) > 0 else 0
            completeness = sum([
                1 if u.get("email") else 0,
                1 if u.get("jobtitle") else 0,
                1 if isinstance(u.get("department"), dict) and u["department"].get("name") else 0,
                1 if isinstance(u.get("company"), dict) and u["company"].get("name") else 0,
            ])
            uid = u.get("id", 0)
            return (has_assets, completeness, uid)

        ranked = sorted(group, key=score, reverse=True)
        keeper = ranked[0]
        losers = ranked[1:]
        return keeper, losers

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Run the cleanup:
        1. Detect duplicates
        2. Merge (reassign assets, delete loser)
        3. Detect & remove junk accounts

        Args:
            dry_run: If True, only report — don't change anything.
        """
        logger.info(f"Starting Cleanup module: dry_run={dry_run}")

        results: Dict[str, Any] = {
            "total_users": 0,
            "duplicate_groups": 0,
            "users_merged": 0,
            "assets_reassigned": 0,
            "users_deleted": 0,
            "junk_removed": 0,
            "errors": 0,
            "details": [],
        }

        # Fetch all users
        users = self.snipe.get_all_users()
        results["total_users"] = len(users)
        logger.debug(f"Fetched {len(users)} Snipe-IT users")

        # --- Audit CSV ---
        audit = AuditCSV(
            log_dir=self.config.logging.dir,
            module_name="cleanup",
            headers=[
                "timestamp", "action", "user_id", "user_name",
                "email", "keeper_id", "keeper_name", "notes",
            ],
            enabled=self.config.logging.audit_csv,
        )

        try:
            # --- Duplicates ---
            dup_groups = self.find_duplicates(users)
            results["duplicate_groups"] = len(dup_groups)
            logger.info(f"Found {len(dup_groups)} duplicate groups")

            for group in dup_groups:
                keeper, losers = self.pick_keeper(group)
                keeper_id = keeper["id"]
                keeper_name = keeper.get("name", "")

                for loser in losers:
                    loser_id = loser["id"]
                    loser_name = loser.get("name", "")
                    loser_assets = loser.get("assets_count") or 0

                    logger.info(
                        f"DUPLICATE: '{loser_name}' (id={loser_id}, assets={loser_assets}) "
                        f"→ merge into '{keeper_name}' (id={keeper_id})"
                    )

                    merge_ok = True
                    moved_assets = 0
                    moved_asset_ids: List[int] = []

                    # Reassign assets from loser → keeper
                    if loser_assets > 0:
                        assets = self.snipe.get_user_assets(loser_id)
                        if len(assets) < int(loser_assets):
                            logger.error(
                                "User %s reports %s assets but only %s were fetched; skipping merge",
                                loser_id,
                                loser_assets,
                                len(assets),
                            )
                            results["errors"] += 1
                            merge_ok = False
                        for asset in assets if merge_ok else []:
                            if not merge_ok:
                                break
                            aid = asset.get("id")
                            aname = asset.get("name") or asset.get("asset_tag") or str(aid)

                            if dry_run:
                                logger.info(f"  [DRY] Would reassign asset {aname} (id={aid}) → user {keeper_id}")
                                results["assets_reassigned"] += 1
                                moved_assets += 1
                            else:
                                current = self.snipe.get_asset_by_id(aid)
                                assigned_id = self.snipe.get_assigned_user_id(current or {})
                                if assigned_id != int(loser_id):
                                    logger.error(
                                        "Asset %s is no longer assigned to duplicate user %s; skipping merge",
                                        aid,
                                        loser_id,
                                    )
                                    results["errors"] += 1
                                    merge_ok = False
                                    continue
                                # Check-in from loser, then checkout to keeper
                                ok_in = self.snipe.checkin_asset(aid, note=f"Cleanup: merging duplicate user {loser_id}")
                                if ok_in:
                                    ok_out = self.snipe.checkout_asset(
                                        aid, keeper_id,
                                        note=f"Cleanup: reassigned from duplicate user {loser_id} to {keeper_id}",
                                    )
                                    if ok_out:
                                        results["assets_reassigned"] += 1
                                        moved_assets += 1
                                        moved_asset_ids.append(aid)
                                        logger.debug(f"  Reassigned asset {aname} → user {keeper_id}")
                                    else:
                                        results["errors"] += 1
                                        merge_ok = False
                                        logger.error(f"  Failed to checkout asset {aname} to keeper {keeper_id}")
                                        rollback_ok = self.snipe.checkout_asset(
                                            aid,
                                            loser_id,
                                            note="Cleanup rollback: keeper checkout failed",
                                        )
                                        if not rollback_ok:
                                            logger.critical(
                                                "Cleanup rollback failed for asset %s; asset is unassigned",
                                                aid,
                                            )
                                else:
                                    results["errors"] += 1
                                    merge_ok = False
                                    logger.error(f"  Failed to checkin asset {aname} from loser {loser_id}")
                            if not dry_run:
                                time.sleep(0.3)

                    if not dry_run and not merge_ok and moved_asset_ids:
                        logger.warning(
                            "Rolling back %d earlier asset transfer(s) for user %s",
                            len(moved_asset_ids),
                            loser_id,
                        )
                        for moved_asset_id in reversed(moved_asset_ids):
                            rollback_in = self.snipe.checkin_asset(
                                moved_asset_id,
                                note="Cleanup rollback: duplicate merge incomplete",
                            )
                            rollback_out = rollback_in and self.snipe.checkout_asset(
                                moved_asset_id,
                                loser_id,
                                note="Cleanup rollback: restoring duplicate user assignment",
                            )
                            if rollback_out:
                                results["assets_reassigned"] -= 1
                                moved_assets -= 1
                            else:
                                logger.critical(
                                    "Cleanup rollback failed for previously moved asset %s",
                                    moved_asset_id,
                                )

                    # Delete the loser
                    if not merge_ok:
                        logger.warning(
                            "Not deleting duplicate user %s because asset transfer was incomplete",
                            loser_id,
                        )
                    elif dry_run:
                        logger.info(f"  [DRY] Would delete user {loser_id} ({loser_name})")
                        results["users_deleted"] += 1
                        results["users_merged"] += 1
                    else:
                        if self._delete_user(loser_id):
                            results["users_deleted"] += 1
                            results["users_merged"] += 1
                            logger.info(f"  Deleted user {loser_id} ({loser_name})")
                        else:
                            results["errors"] += 1
                            logger.error(f"  Failed to delete user {loser_id}")

                    audit.write(
                        action="merge_duplicate",
                        user_id=str(loser_id),
                        user_name=loser_name,
                        email=loser.get("email", ""),
                        keeper_id=str(keeper_id),
                        keeper_name=keeper_name,
                        notes=(
                            f"assets_moved={moved_assets}, merge_ok={merge_ok}, "
                            f"dry_run={dry_run}"
                        ),
                    )

            # --- Junk accounts ---
            junk = self.find_junk_accounts(users)
            if junk:
                logger.info(f"Found {len(junk)} junk accounts")
                for u in junk:
                    uid = u["id"]
                    uname = u.get("name", "")
                    assets = u.get("assets_count") or 0

                    if assets > 0:
                        logger.warning(f"  Junk user {uid} ({uname}) has {assets} assets — skipping delete")
                        continue

                    if dry_run:
                        logger.info(f"  [DRY] Would delete junk user {uid} ({uname})")
                    else:
                        if self._delete_user(uid):
                            logger.info(f"  Deleted junk user {uid} ({uname})")
                        else:
                            results["errors"] += 1

                    results["junk_removed"] += 1
                    audit.write(
                        action="delete_junk",
                        user_id=str(uid),
                        user_name=uname,
                        email=u.get("email", ""),
                        keeper_id="",
                        keeper_name="",
                        notes=f"dry_run={dry_run}",
                    )

        finally:
            audit.close()

        self._print_summary(results, dry_run)
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _delete_user(self, user_id: int) -> bool:
        """Delete a Snipe-IT user by ID."""
        response = self.snipe._request("DELETE", f"/users/{user_id}")
        if response and response.status_code in (200, 201):
            result = response.json()
            if result.get("status") == "success":
                return True
            # Some Snipe-IT versions return 200 with error
            logger.warning(f"Delete user {user_id}: {result.get('messages')}")
            return False
        if response:
            logger.warning(f"Delete user {user_id}: HTTP {response.status_code}")
        return False

    def _print_summary(self, results: Dict, dry_run: bool) -> None:
        mode = "DRY RUN" if dry_run else "LIVE RUN"
        logger.info(
            f"Cleanup ({mode}): {results['total_users']} scanned, "
            f"{results['duplicate_groups']} dupes, "
            f"{results['users_merged']} merged, "
            f"{results['assets_reassigned']} reassigned, "
            f"{results['users_deleted']} deleted, "
            f"{results['junk_removed']} junk, "
            f"{results['errors']} errors"
        )

    def close(self) -> None:
        """Clean up resources."""
        self.snipe.close()


# Convenience function
def run_cleanup(config: Config, dry_run: bool = False) -> Dict[str, Any]:
    """Run the cleanup module."""
    module = CleanupModule(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()
