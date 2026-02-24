"""
Jamf-SnipeIT Suite - Username Standardization Module
Converts all email-style usernames (user@domain.com) to plain format (user.name).
One-time migration module, safe to re-run (idempotent).
"""
import logging
import time
from typing import Any, Dict, List

from core.config import Config
from clients.snipeit import SnipeITClient

logger = logging.getLogger(__name__)


class UsernameStandardizer:
    """Standardize Snipe-IT usernames to plain (no @domain) format."""

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

    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Standardize all email-style usernames to plain format.

        user@createfuture.com → user.name  (the part before @)
        """
        logger.info(f"Starting Username Standardization: dry_run={dry_run}")

        results: Dict[str, Any] = {
            "total_users": 0,
            "already_plain": 0,
            "updated": 0,
            "errors": 0,
            "skipped": 0,
            "details": [],
        }

        users = self.snipe.get_all_users()
        results["total_users"] = len(users)
        logger.debug(f"Fetched {len(users)} Snipe-IT users")

        for i, user in enumerate(users, 1):
            uid = user.get("id")
            username = (user.get("username") or "").strip()

            if not username:
                results["skipped"] += 1
                continue

            # Already plain format — skip
            if "@" not in username:
                results["already_plain"] += 1
                continue

            # Extract the part before @
            new_username = username.split("@")[0].lower()

            if not new_username or len(new_username) < 2:
                logger.warning(f"  Skipping user {uid}: bad prefix '{new_username}' from '{username}'")
                results["skipped"] += 1
                continue

            logger.debug(f"  [{i}/{len(users)}] {username} → {new_username}")

            if dry_run:
                results["updated"] += 1
                results["details"].append({
                    "id": uid, "old": username, "new": new_username,
                })
            else:
                try:
                    ok = self.snipe.update_user(uid, {"username": new_username})
                    if ok:
                        results["updated"] += 1
                        results["details"].append({
                            "id": uid, "old": username, "new": new_username,
                        })
                    else:
                        results["errors"] += 1
                        logger.error(f"  Failed to update user {uid}")
                except Exception as e:
                    results["errors"] += 1
                    logger.error(f"  Error updating user {uid}: {e}")

                time.sleep(0.15)  # Rate limiting

        self._print_summary(results, dry_run)
        return results

    @staticmethod
    def _print_summary(results: Dict, dry_run: bool) -> None:
        mode = "DRY RUN" if dry_run else "LIVE RUN"
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"  USERNAME STANDARDIZATION - {mode} COMPLETE")
        logger.info("=" * 60)
        logger.info(f"  Total users:        {results['total_users']}")
        logger.info(f"  Already plain:      {results['already_plain']}")
        logger.info(f"  Updated:            {results['updated']}")
        logger.info(f"  Skipped:            {results['skipped']}")
        logger.info(f"  Errors:             {results['errors']}")
        logger.info("=" * 60)

    def close(self) -> None:
        self.snipe.close()


def run_username_standardize(config: Config, dry_run: bool = False) -> Dict[str, Any]:
    """Convenience runner."""
    module = UsernameStandardizer(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()
