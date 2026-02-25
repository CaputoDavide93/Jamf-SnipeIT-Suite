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
        Find groups of users that share the same normalised name.

        Returns list of groups, each group is a list of user dicts.
        Only groups with 2+ members are returned.
        """
        by_name: Dict[str, List[Dict]] = {}

        for u in users:
            raw_name = (u.get("name") or "").strip()
            # Strip [Disabled] prefix for comparison
            norm = raw_name.lower()
            if norm.startswith("[disabled]"):
                norm = norm.replace("[disabled]", "").strip()
            if not norm or len(norm) < 3:
                continue
            by_name.setdefault(norm, []).append(u)

        return [group for group in by_name.values() if len(group) >= 2]

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

                    # Reassign assets from loser → keeper
                    if loser_assets > 0:
                        assets = self.snipe.get_user_assets(loser_id)
                        for asset in assets:
                            aid = asset.get("id")
                            aname = asset.get("name") or asset.get("asset_tag") or str(aid)

                            if dry_run:
                                logger.info(f"  [DRY] Would reassign asset {aname} (id={aid}) → user {keeper_id}")
                                results["assets_reassigned"] += 1
                            else:
                                # Check-in from loser, then checkout to keeper
                                ok_in = self.snipe.checkin_asset(aid, note=f"Cleanup: merging duplicate user {loser_id}")
                                if ok_in:
                                    ok_out = self.snipe.checkout_asset(
                                        aid, keeper_id,
                                        note=f"Cleanup: reassigned from duplicate user {loser_id} to {keeper_id}",
                                    )
                                    if ok_out:
                                        results["assets_reassigned"] += 1
                                        logger.debug(f"  Reassigned asset {aname} → user {keeper_id}")
                                    else:
                                        results["errors"] += 1
                                        logger.error(f"  Failed to checkout asset {aname} to keeper {keeper_id}")
                                else:
                                    results["errors"] += 1
                                    logger.error(f"  Failed to checkin asset {aname} from loser {loser_id}")
                            time.sleep(0.3)

                    # Delete the loser
                    if dry_run:
                        logger.info(f"  [DRY] Would delete user {loser_id} ({loser_name})")
                        results["users_deleted"] += 1
                    else:
                        if self._delete_user(loser_id):
                            results["users_deleted"] += 1
                            logger.info(f"  Deleted user {loser_id} ({loser_name})")
                        else:
                            results["errors"] += 1
                            logger.error(f"  Failed to delete user {loser_id}")

                    results["users_merged"] += 1
                    audit.write(
                        action="merge_duplicate",
                        user_id=str(loser_id),
                        user_name=loser_name,
                        email=loser.get("email", ""),
                        keeper_id=str(keeper_id),
                        keeper_name=keeper_name,
                        notes=f"assets_moved={loser_assets}, dry_run={dry_run}",
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
