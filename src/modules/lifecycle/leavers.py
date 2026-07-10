"""
Jamf-SnipeIT Suite - Leavers Module
Marks Snipe-IT assets as "Pending" for Azure AD disabled/leaving users.
"""
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from core.config import Config
from clients.azure import AzureClient
from clients.snipeit import SnipeITClient
from clients.slack import SlackClient
from infra.audit_csv import AuditCSV
from infra.progress import ProgressTracker
from infra.helpers import setup_logging, rate_limit_delay, leave_date_passed

logger = logging.getLogger(__name__)

BATCH_SIZE = 10  # Process users in batches to avoid rate limits


class LeaversModule:
    """
    Module to process leavers/disabled users from Azure AD
    and mark their Snipe-IT assets as pending.
    """
    
    def __init__(self, config: Config):
        """
        Initialize the Leavers module.
        
        Args:
            config: Suite configuration
        """
        self.config = config
        self.settings = config.modules.get("leavers", {})
        
        # Initialize clients
        self.azure = AzureClient(
            tenant_id=config.azure.tenant_id,
            client_id=config.azure.client_id,
            client_secret=config.azure.client_secret,
            scope=config.azure.scope,
            timeout=config.api.timeout_seconds,
        )
        
        self.snipe = SnipeITClient(
            base_url=config.snipeit.base_url,
            api_token=config.snipeit.api_token,
            timeout=config.api.timeout_seconds,
            max_retries=config.api.max_retries,
            retry_delay=config.api.retry_delay_seconds,
            rate_limit_wait=config.api.rate_limit_wait_seconds,
        )
        
        # Slack notifications (optional)
        self.slack = SlackClient(
            bot_token=config.slack.bot_token,
            channel_id=config.slack.channel_id,
            enabled=config.slack.enabled and config.slack.notify_disabled_with_assets,
        )
    
    def run(
        self,
        group_type: str = "leavers",
        dry_run: bool = False,
        lookback_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Run the leavers processing.
        
        Args:
            group_type: "leavers" or "disabled"
            dry_run: If True, don't make changes
            lookback_days: Days to look back (if not using group)
        
        Returns:
            Results dictionary with statistics
        """
        logger.info(f"Starting Leavers module: group_type={group_type}, dry_run={dry_run}")
        
        # Fetch users from Azure
        users = self._fetch_users(group_type, lookback_days)
        
        if not users:
            logger.info("No users to process")
            return {"total_users": 0, "matched_users": 0, "updated_assets": 0}
        
        # Process users
        results = self._process_users(users, group_type, dry_run)
        
        # Print summary
        self._print_summary(results, dry_run)
        
        return results
    
    def _fetch_users(
        self,
        group_type: str,
        lookback_days: Optional[int],
    ) -> List[Dict[str, Any]]:
        """Fetch users from Azure AD."""
        
        if group_type == "leavers" and self.config.azure.leavers_group_id:
            logger.debug(f"Fetching leavers group: {self.config.azure.leavers_group_id}")
            return self.azure.get_group_members(self.config.azure.leavers_group_id)
        
        elif group_type == "disabled" and self.config.azure.disabled_group_id:
            logger.debug(f"Fetching disabled group: {self.config.azure.disabled_group_id}")
            return self.azure.get_group_members(self.config.azure.disabled_group_id)
        
        else:
            # Fallback to filter-based query
            filter_clause = self.settings.get("user_filter", "accountEnabled eq false")
            logger.debug(f"Fetching users with filter: {filter_clause}")
            return self.azure.get_disabled_users(filter_clause)
    
    def _process_users(
        self,
        users: List[Dict[str, Any]],
        group_type: str,
        dry_run: bool,
    ) -> Dict[str, Any]:
        """Process users and update Snipe-IT assets."""
        
        results = {
            "total_users": len(users),
            "matched_users": 0,
            "updated_assets": 0,
            "updated_user_names": 0,
            "errors": [],
        }
        
        pending_status_id = self.config.snipeit.status_pending_id
        
        total_batches = (len(users) + BATCH_SIZE - 1) // BATCH_SIZE
        logger.info(f"Processing {len(users)} users in {total_batches} batches")
        progress = ProgressTracker("Leavers", total=len(users), log_every=25)
        
        for batch_num, batch_start in enumerate(range(0, len(users), BATCH_SIZE), 1):
            batch = users[batch_start:batch_start + BATCH_SIZE]
            logger.debug(f"Batch {batch_num}/{total_batches}: Processing {len(batch)} users")
            
            for user in batch:
                try:
                    self._process_single_user(user, group_type, dry_run, pending_status_id, results)
                except Exception as e:
                    logger.error(f"Error processing user {user.get('displayName')}: {e}")
                    results["errors"].append(str(e))
                progress.advance()
            
            # Delay between batches
            if batch_start + BATCH_SIZE < len(users):
                rate_limit_delay(2, "Leavers", batch_num, total_batches)
        
        progress.finish(extra=f"matched={results['matched_users']}, assets_updated={results['updated_assets']}")
        
        return results
    
    def _process_single_user(
        self,
        user: Dict[str, Any],
        group_type: str,
        dry_run: bool,
        pending_status_id: int,
        results: Dict[str, Any],
    ) -> None:
        """Process a single user."""
        
        # Skip enabled users in disabled group mode
        if group_type == "disabled" and user.get("accountEnabled") is True:
            logger.debug(f"Skipping enabled user: {user.get('displayName')}")
            return
        
        # Get user email
        email = AzureClient.extract_email(user)
        if not email:
            logger.debug(f"Skipping user without email: {user.get('id')}")
            return
        
        logger.debug(f"Processing: {user.get('displayName')} ({email})")
        
        # Find user in Snipe-IT
        snipe_user = self.snipe.find_user_by_email(email)
        if not snipe_user:
            logger.debug(f"No Snipe-IT user found for: {email}")
            return
        
        snipe_user_id = snipe_user.get("id")
        logger.debug(f"Found Snipe-IT user: id={snipe_user_id}")
        
        # Add [Disabled] prefix only when user is truly inactive:
        #   - accountEnabled == false (hard disabled), OR
        #   - employeeLeaveDateTime in the past (actually left)
        # Users still in notice period (in leavers group but accountEnabled=True,
        # leave date in future) do NOT get tagged yet.
        if self._should_tag_disabled(user):
            self._update_user_name_disabled(snipe_user, dry_run, results)
        
        # Find user's assets
        assets = self._get_user_assets(snipe_user_id, snipe_user)
        if not assets:
            logger.debug(f"No assets found for user: {email}")
            return
        
        results["matched_users"] += 1
        logger.debug(f"Found {len(assets)} assets for user")
        
        # Track asset names for Slack notification
        processed_asset_names = []
        
        # Update each asset to pending status
        for asset in assets:
            asset_id = asset.get("id")
            
            # Verify still assigned to this user
            current_asset = self.snipe.get_asset_by_id(asset_id)
            if current_asset:
                assigned_user_id = self.snipe.get_assigned_user_id(current_asset)
                if assigned_user_id and assigned_user_id != snipe_user_id:
                    logger.debug(f"Asset {asset_id} now assigned to different user, skipping")
                    continue
            
            # Check if already pending
            current_status_label = current_asset.get("status_label") if current_asset else asset.get("status_label")
            current_status_id = None
            if isinstance(current_status_label, dict):
                current_status_id = current_status_label.get("id")
            elif isinstance(current_status_label, (int, str)):
                try:
                    current_status_id = int(current_status_label)
                except (ValueError, TypeError):
                    pass
            
            if current_status_id == pending_status_id:
                logger.debug(f"Asset {asset_id} already pending")
                continue
            
            # Update status
            asset_name = asset.get("name") or asset.get("asset_tag") or asset_id
            
            if dry_run:
                logger.info(f"[DRY-RUN] Would mark asset {asset_name} as pending (keep assigned to leaver)")
                results["updated_assets"] += 1
            else:
                # Set status to Pending but DO NOT check in.
                # The asset stays assigned to the leaver so it's clear who
                # had it last. Pending status prevents User Match / Correction
                # from touching it. The asset will be manually reassigned
                # when the machine is collected and given to someone else.
                status_ok = self.snipe.update_asset_status(asset_id, pending_status_id)
                if not status_ok:
                    logger.error(f"Failed to set pending status on asset {asset_name}")
                    continue

                logger.info(f"Marked asset {asset_name} as pending (still assigned to leaver)")
                results["updated_assets"] += 1
                processed_asset_names.append(str(asset_name))
        
        # Log disabled users with assets (notification removed — users are
        # disabled before they actually leave, so this is expected).
        if processed_asset_names and not dry_run:
            display_name = user.get("displayName", email)
            logger.info(
                f"Disabled user {display_name} ({email}): "
                f"{len(processed_asset_names)} asset(s) marked pending"
            )
    
    def _get_user_assets(
        self,
        user_id: int,
        snipe_user: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Get assets assigned to a user via the dedicated API endpoint."""
        
        # Use direct user->assets endpoint (much more reliable than name search)
        assets = self.snipe.get_user_assets(user_id)
        
        if not assets:
            logger.debug(f"No assets found via /users/{user_id}/assets endpoint")
        
        return assets
    
    @staticmethod
    def _should_tag_disabled(azure_user: Dict[str, Any]) -> bool:
        """Tag [Disabled] only if truly inactive:
        - accountEnabled == false, OR
        - employeeLeaveDateTime <= now (leave date passed)

        Unparseable leave dates fail safe to False (don't tag).
        """
        if not azure_user.get("accountEnabled", True):
            return True
        return leave_date_passed(azure_user, default_on_invalid=False)

    def _update_user_name_disabled(
        self,
        snipe_user: Dict[str, Any],
        dry_run: bool,
        results: Dict[str, Any],
    ) -> None:
        """Add [Disabled] prefix to user name."""
        
        user_id = snipe_user.get("id")
        current_name = snipe_user.get("first_name") or snipe_user.get("name") or ""
        
        if not current_name or current_name.startswith("[Disabled]"):
            return
        
        new_name = f"[Disabled] {current_name}"
        
        if dry_run:
            logger.info(f"[DRY-RUN] Would rename user {user_id}: {current_name} -> {new_name}")
            results["updated_user_names"] += 1
        else:
            update_data = {}
            if "first_name" in snipe_user:
                update_data["first_name"] = new_name
            else:
                update_data["name"] = new_name
            
            if self.snipe.update_user(user_id, update_data):
                logger.info(f"Renamed user {user_id}: {current_name} -> {new_name}")
                results["updated_user_names"] += 1
    
    def _print_summary(self, results: Dict[str, Any], dry_run: bool) -> None:
        """Print processing summary."""
        
        mode = "DRY RUN" if dry_run else "LIVE RUN"
        
        parts = [
            f"Leavers ({mode}): {results['total_users']} users",
            f"{results['matched_users']} with assets",
            f"{results['updated_assets']} marked pending",
        ]
        if results.get("updated_user_names", 0) > 0:
            parts.append(f"{results['updated_user_names']} names updated")
        if results.get("errors"):
            parts.append(f"{len(results['errors'])} errors")
        logger.info(", ".join(parts))
    
    def close(self) -> None:
        """Clean up resources."""
        self.azure.close()
        self.snipe.close()


def run_leavers(
    config: Config,
    group_type: str = "leavers",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Convenience function to run the leavers module.
    
    Args:
        config: Suite configuration
        group_type: "leavers" or "disabled"
        dry_run: If True, don't make changes
    
    Returns:
        Results dictionary
    """
    module = LeaversModule(config)
    try:
        return module.run(group_type=group_type, dry_run=dry_run)
    finally:
        module.close()
