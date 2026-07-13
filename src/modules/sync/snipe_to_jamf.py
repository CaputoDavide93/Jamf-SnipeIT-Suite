"""
Jamf-SnipeIT Suite - Snipe-IT to Jamf Sync Module
Writes the Snipe-IT asset ID back to each Jamf computer as an Extension Attribute.

IMPORTANT: Jamf is the source of truth for user identity (username, full name,
email). This module NEVER writes to Jamf location fields — it only sets the
SnipeIT_Asset_ID EA so Jamf knows which Snipe-IT asset record corresponds to
each computer.
"""
import logging
import time
from typing import Any, Dict, List, Optional

from core.config import Config
from clients.jamf import JamfClient
from clients.snipeit import SnipeITClient
from infra.progress import ProgressTracker
from infra.helpers import rate_limit_delay

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
        
        # Pre-built serial→asset map for bulk lookup
        self._serial_map: Optional[Dict[str, Dict]] = None
    
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
        
        # Pre-filter: skip computers that already have an email in Jamf
        # This avoids expensive individual jamf.get_computer_by_serial() calls
        # for devices that User Match has already populated.
        filtered = []
        skipped_prefilter = 0
        for comp in computers:
            serial = comp.get("serial_number", "").strip()
            if not serial:
                continue
            # If the basic list has email info, we can skip here.
            # Otherwise we have to check inside _sync_single_computer.
            filtered.append(comp)
        
        logger.info(f"Will attempt sync for {len(filtered)} computers")
        
        progress = ProgressTracker("Snipe→Jamf", total=len(filtered), log_every=50)
        
        for i, comp in enumerate(filtered, 1):
            serial = comp.get("serial_number", "").strip()
            
            if not serial:
                progress.advance()
                continue
            
            logger.debug(f"[{i}/{len(filtered)}] Processing serial: {serial}")
            
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
            
            progress.advance()
            
            # Rate limiting delay
            if not dry_run and self.update_delay > 0:
                rate_limit_delay(self.update_delay, "Snipe→Jamf", i, len(filtered))
        
        progress.finish(extra=f"updated={results['updated']}, skipped={results['skipped']}, errors={results['errors']}")
        
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
        logger.debug(f"Syncing single computer: {serial_number}")
        return self._sync_single_computer(serial_number, dry_run)
    
    def _sync_single_computer(self, serial: str, dry_run: bool) -> bool:
        """
        Write the Snipe-IT asset ID to the Jamf EA for a single computer.

        This module NEVER touches Jamf location/identity fields (username,
        real_name, email, position). Jamf is the source of truth for those.

        Args:
            serial: Computer serial number
            dry_run: If True, don't make changes

        Returns:
            True if the EA was updated
        """
        # Lazy-load the serial→asset map (one bulk call, reused for all computers)
        if self._serial_map is None:
            logger.info("Building Snipe-IT serial → asset map...")
            self._serial_map = self.snipe.get_assets_by_serial_map()
            logger.info(f"Loaded {len(self._serial_map)} assets from Snipe-IT")

        # Look up asset by serial
        asset = self._serial_map.get(serial.upper())
        if not asset:
            logger.debug(f"[{serial}] No Snipe-IT asset found")
            return False

        asset_id = asset.get("id")
        if not asset_id:
            return False

        # Look up Jamf computer to get its ID and current EA value
        jamf_computer = self.jamf.get_computer_by_serial(serial)
        if not jamf_computer:
            logger.debug(f"[{serial}] Not found in Jamf")
            return False

        jamf_id = (jamf_computer.get("general") or {}).get("id")
        if not jamf_id:
            return False

        # Check if EA already has the correct value (skip unnecessary writes)
        ea_name = self.config.jamf.ea_snipe_asset_id
        ext_attrs = jamf_computer.get("extension_attributes") or []
        for ea in ext_attrs:
            if ea.get("name") == ea_name and str(ea.get("value", "")).strip() == str(asset_id):
                logger.debug(f"[{serial}] EA already set to {asset_id}")
                return False

        if dry_run:
            logger.info(f"[DRY-RUN] Would set {ea_name}={asset_id} on Jamf computer {jamf_id} ({serial})")
            return True

        # Write ONLY the EA — do not touch location fields
        from clients.jamf import safe_xml_text
        xml = f"""<computer>
  <extension_attributes>
    <extension_attribute>
      <name>{safe_xml_text(ea_name)}</name>
      <value>{safe_xml_text(str(asset_id))}</value>
    </extension_attribute>
  </extension_attributes>
</computer>"""

        response = self.jamf._request(
            "PUT",
            f"/JSSResource/computers/id/{jamf_id}",
            xml_data=xml.encode("utf-8"),
        )

        if response and response.status_code in (200, 201):
            logger.info(f"[{serial}] Set {ea_name}={asset_id} on Jamf computer {jamf_id}")
            return True

        logger.error(f"[{serial}] Failed to set EA on Jamf computer {jamf_id}")
        return False
    
    def _print_summary(self, results: Dict[str, Any], dry_run: bool) -> None:
        """Print processing summary."""
        
        mode = "DRY RUN" if dry_run else "LIVE RUN"
        
        logger.info(
            f"Snipe-to-Jamf ({mode}): {results['total_processed']} processed, "
            f"{results['updated']} updated, "
            f"{results['skipped']} skipped, "
            f"{results['errors']} errors"
        )
    
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
