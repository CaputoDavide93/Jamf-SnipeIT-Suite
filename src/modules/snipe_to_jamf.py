"""
Jamf-SnipeIT Suite - Snipe-IT to Jamf Sync Module
Syncs user information FROM Snipe-IT TO Jamf (reverse direction).
"""
import logging
import time
from typing import Any, Dict, List, Optional

from core import Config, JamfClient, SnipeITClient
from utils import AuditCSV

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
                time.sleep(self.update_delay)
        
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
        
        Args:
            serial: Computer serial number
            dry_run: If True, don't make changes
        
        Returns:
            True if updated
        """
        # Look up in Snipe-IT
        snipe_asset = self.snipe.get_asset_by_serial(serial)
        
        if not snipe_asset:
            logger.info(f"No Snipe-IT asset for serial: {serial}")
            return False
        
        # Check if assigned to a user
        assigned_to = snipe_asset.get("assigned_to")
        if not assigned_to:
            logger.info(f"Snipe-IT asset {serial} is unassigned")
            return False
        
        assigned_user_id = assigned_to.get("id")
        if not assigned_user_id:
            logger.info(f"No assigned user ID for asset: {serial}")
            return False
        
        # Fetch full user details from Snipe-IT
        snipe_user = self.snipe.get_user_by_id(assigned_user_id)
        if not snipe_user:
            logger.error(f"Could not fetch Snipe-IT user {assigned_user_id}")
            return False
        
        # Extract user info
        username = snipe_user.get("username", "")
        full_name = snipe_user.get("name", "")
        email = snipe_user.get("email", "")
        
        # Check if this is a generic/shared username that should be skipped
        skip_usernames = [u.lower() for u in self.config.matching.skip_usernames]
        if username.lower() in skip_usernames:
            logger.info(f"Skipping generic/shared Snipe-IT user: {username}")
            return False
        
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
        
        # Look up in Jamf
        jamf_computer = self.jamf.get_computer_by_serial(serial)
        if not jamf_computer:
            logger.info(f"No Jamf computer for serial: {serial}")
            return False
        
        jamf_id = jamf_computer.get("general", {}).get("id")
        if not jamf_id:
            logger.error(f"Could not get Jamf ID for serial: {serial}")
            return False
        
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
