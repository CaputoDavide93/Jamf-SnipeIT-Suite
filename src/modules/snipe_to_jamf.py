"""
Jamf-SnipeIT Suite - Snipe-IT to Jamf Sync Module
Syncs user information FROM Snipe-IT TO Jamf (reverse direction).

IMPORTANT: Jamf local users are the source of truth. This module only
enriches Jamf location fields with Snipe-IT user data (email, name, job title)
when the Snipe-IT assigned user matches the Jamf local user.
"""
import logging
import time
from typing import Any, Dict, List, Optional

from core import Config, JamfClient, SnipeITClient
from utils import AuditCSV, pick_primary_local_identity, rate_limit_delay, UserMatcher

logger = logging.getLogger(__name__)


class SnipeToJamfModule:
    """
    Module to sync user information from Snipe-IT to Jamf Pro.
    For each Jamf computer, looks up the assigned user in Snipe-IT
    and updates Jamf with the user details.
    """
    
    def __init__(self, config: Config):
        """
        Initialize the Snipe-to-Jamf sync module.
        
        Args:
            config: Suite configuration
        """
        self.config = config
        self.settings = config.modules.get("snipe_to_jamf", {})
        self.update_delay = self.settings.get("update_delay_seconds", 0.2)
        
        # Initialize clients
        self.jamf = JamfClient(
            base_url=config.jamf.base_url,
            username=config.jamf.username,
            password=config.jamf.password,
            client_id=config.jamf.client_id,
            client_secret=config.jamf.client_secret,
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
        
        # Cache for bulk-fetched data
        self._snipe_asset_map: Optional[Dict[str, Dict]] = None
        self._user_matcher: Optional[UserMatcher] = None
    
    def run(
        self,
        serial_numbers: Optional[List[str]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Run the Snipe-IT to Jamf sync.
        
        Args:
            serial_numbers: Specific serials to sync (None = all)
            dry_run: If True, don't make changes
        
        Returns:
            Results dictionary with statistics
        """
        logger.info(f"Starting Snipe→Jamf sync: dry_run={dry_run}")
        
        results = {
            "total_processed": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
            "details": [],
        }
        
        # Get computers to process
        if serial_numbers:
            computers = [{"serial_number": s} for s in serial_numbers]
        else:
            computers = self.jamf.get_all_computers_basic()
        
        if not computers:
            logger.warning("No computers to process")
            return results
        
        # OPTIMIZATION: Pre-fetch all Snipe-IT assets in one batch
        # This reduces API calls from N to 1 for Snipe-IT lookups
        if not serial_numbers:  # Only bulk fetch when processing all
            logger.info("Pre-fetching all Snipe-IT assets for bulk lookup...")
            self._snipe_asset_map = self.snipe.get_assets_by_serial_map()
        else:
            self._snipe_asset_map = None
        
        logger.info(f"Processing {len(computers)} computers")
        
        for i, comp in enumerate(computers, 1):
            serial = comp.get("serial_number", "").strip()
            
            if not serial:
                continue
            
            logger.info(f"[{i}/{len(computers)}] Processing serial: {serial}")
            
            try:
                updated = self._sync_single_computer(serial, dry_run)
                results["total_processed"] += 1
                
                if updated:
                    results["updated"] += 1
                else:
                    results["skipped"] += 1
                    
            except Exception as e:
                logger.error(f"Error processing {serial}: {e}")
                results["errors"] += 1
                results["details"].append({"serial": serial, "error": str(e)})
            
            # Rate limiting delay
            if self.update_delay > 0:
                rate_limit_delay(self.update_delay, "Snipe→Jamf", i, len(computers))
        
        # Print summary
        self._print_summary(results, dry_run)
        
        return results
    
    def run_single(self, serial_number: str, dry_run: bool = False) -> bool:
        """
        Sync a single computer by serial number.
        
        Args:
            serial_number: Computer serial number
            dry_run: If True, don't make changes
        
        Returns:
            True if updated successfully
        """
        logger.info(f"Syncing single computer: {serial_number}")
        return self._sync_single_computer(serial_number, dry_run)
    
    def _sync_single_computer(self, serial: str, dry_run: bool) -> bool:
        """
        Sync user info for a single computer.
        
        IMPORTANT: Jamf local users are the source of truth.
        We only update Jamf if the Snipe-IT assigned user matches
        the Jamf local user, to enrich with email/name/job title.
        
        Args:
            serial: Computer serial number
            dry_run: If True, don't make changes
        
        Returns:
            True if updated
        """
        # First, get Jamf computer to check local user (source of truth)
        jamf_computer = self.jamf.get_computer_by_serial(serial)
        if not jamf_computer:
            logger.info(f"No Jamf computer for serial: {serial}")
            return False
        
        jamf_id = jamf_computer.get("general", {}).get("id")
        if not jamf_id:
            logger.error(f"Could not get Jamf ID for serial: {serial}")
            return False
        
        # Get Jamf local user (source of truth)
        groups_accounts = jamf_computer.get("groups_accounts", {}) or {}
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
        
        jamf_username, jamf_fullname = pick_primary_local_identity(local_users)
        
        if not jamf_username:
            logger.info(f"No primary local user in Jamf for serial: {serial}")
            return False
        
        logger.debug(f"Jamf local user (source of truth): {jamf_username}, full name: {jamf_fullname}")
        
        # JAMF IS SOURCE OF TRUTH: Look up the Jamf local user in Snipe-IT
        # Use UserMatcher to try full name first, then username, then fuzzy match
        if self._user_matcher is None:
            logger.info("Loading Snipe-IT users for matching...")
            users = self.snipe.get_all_users()
            self._user_matcher = UserMatcher(
                users=users,
                email_domain=self.config.matching.email_domain,
                min_score=self.config.matching.min_score,
                weight_lcs=self.config.matching.weight_lcs,
                weight_char_overlap=self.config.matching.weight_char_overlap,
                weight_bigram_dice=self.config.matching.weight_bigram_dice,
                use_bigram_dice=self.config.matching.use_bigram_dice,
            )
            logger.info(f"Loaded {len(users)} users for matching")
        
        snipe_user, debug_info = self._user_matcher.best_match(
            full_name_hint=jamf_fullname or "",
            username=jamf_username,
        )
        
        if debug_info.get("exact_hit_reason"):
            logger.debug(f"Match reason: {debug_info['exact_hit_reason']}")
        
        if not snipe_user:
            logger.info(f"No Snipe-IT user found matching Jamf user '{jamf_username}' (full name: '{jamf_fullname}') for {serial}")
            return False
        
        # Extract user info from Snipe-IT
        username = snipe_user.get("username", "")
        full_name = snipe_user.get("name", "")
        first_name = snipe_user.get("first_name", "")
        email = snipe_user.get("email", "")
        
        # Check if this is a disabled user (name starts with [Disabled])
        if full_name.startswith("[Disabled]") or first_name.startswith("[Disabled]"):
            logger.info(f"Skipping disabled Snipe-IT user: {full_name or first_name} ({serial})")
            return False
        
        # Check if this is a generic/shared username that should be skipped
        skip_usernames = [u.lower() for u in self.config.matching.skip_usernames]
        if jamf_username.lower() in skip_usernames:
            logger.info(f"Skipping generic/shared Jamf user: {jamf_username}")
            return False
        
        logger.debug(f"Found Snipe-IT user: {username} ({full_name}) for Jamf user: {jamf_username}")
        
        # Job title handling
        job_title = (
            snipe_user.get("jobtitle")
            or snipe_user.get("job_title")
            or snipe_user.get("title")
            or ""
        )
        
        # Department handling
        department = snipe_user.get("department")
        if isinstance(department, dict):
            department = department.get("name", "")
        else:
            department = department or ""
        
        logger.debug(f"Snipe-IT user: {username}, {full_name}, {email}")
        
        # Update Jamf
        if dry_run:
            logger.info(
                f"[DRY-RUN] Would update Jamf computer {jamf_id}: "
                f"username={username}, name={full_name}, email={email}"
            )
            return True
        
        success = self.jamf.update_computer_location(
            computer_id=jamf_id,
            username=username,
            realname=full_name,
            email=email,
            position=job_title,
            department=department,
            dry_run=False,
        )
        
        if success:
            logger.info(f"Updated Jamf computer {jamf_id} with user: {username}")
            return True
        else:
            logger.error(f"Failed to update Jamf computer {jamf_id}")
            return False
    
    def _print_summary(self, results: Dict[str, Any], dry_run: bool) -> None:
        """Print processing summary."""
        
        mode = "DRY RUN" if dry_run else "LIVE RUN"
        
        print("\n" + "=" * 60)
        print(f"SNIPE→JAMF SYNC - {mode} COMPLETE")
        print("=" * 60)
        print(f"Total processed:  {results['total_processed']}")
        print(f"Updated:          {results['updated']}")
        print(f"Skipped:          {results['skipped']}")
        print(f"Errors:           {results['errors']}")
        print("=" * 60 + "\n")
    
    def close(self) -> None:
        """Clean up resources."""
        self.jamf.close()
        self.snipe.close()


def run_snipe_to_jamf(
    config: Config,
    serial_numbers: Optional[List[str]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Convenience function to run the Snipe→Jamf sync.
    
    Args:
        config: Suite configuration
        serial_numbers: Specific serials to sync (None = all)
        dry_run: If True, don't make changes
    
    Returns:
        Results dictionary
    """
    module = SnipeToJamfModule(config)
    try:
        return module.run(serial_numbers=serial_numbers, dry_run=dry_run)
    finally:
        module.close()
