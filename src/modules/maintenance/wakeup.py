"""
Jamf-SnipeIT Suite - Wake-Up Module
Sends MDM redeploy commands to wake up Jamf computers.
"""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import Config
from clients.jamf import JamfClient

logger = logging.getLogger(__name__)


class WakeUpModule:
    """
    Module to send wake-up (redeploy) commands to Jamf computers.
    Supports targeting by:
    - Dynamic group ID
    - Single serial number
    - List of serial numbers (from file or list)
    """
    
    def __init__(self, config: Config):
        """
        Initialize the Wake-Up module.
        
        Args:
            config: Suite configuration
        """
        self.config = config
        self.settings = config.modules.get("wakeup", {})
        self.default_group_id = self.settings.get("default_group_id", "")
        
        # Initialize Jamf client
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
    
    def wake_group(
        self,
        group_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Wake up all computers in a dynamic group.
        
        Args:
            group_id: Dynamic group ID (defaults to config)
            dry_run: If True, don't send commands
        
        Returns:
            Results dictionary
        """
        gid = group_id or self.default_group_id
        
        if not gid:
            raise ValueError("Group ID required. Set --group-id or in config.")
        
        logger.debug(f"Fetching computers from group {gid}")
        
        computers = self.jamf.get_dynamic_group_by_id(gid)
        
        if not computers:
            logger.warning(f"No computers in group {gid}")
            return {"total": 0, "successful": 0, "failed": 0}
        
        logger.debug(f"Found {len(computers)} computers in group")
        
        # Extract computer IDs
        computer_ids = []
        for comp in computers:
            comp_id = comp.get("id")
            if comp_id:
                computer_ids.append(int(comp_id))
        
        return self._send_wake_commands(computer_ids, dry_run)
    
    def wake_serial(
        self,
        serial_number: str,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Wake up a single computer by serial number.
        
        Args:
            serial_number: Computer serial number
            dry_run: If True, don't send command
        
        Returns:
            Results dictionary
        """
        logger.debug(f"Looking up computer by serial: {serial_number}")
        
        computer = self.jamf.get_computer_by_serial(serial_number)
        
        if not computer:
            raise ValueError(f"Computer not found: {serial_number}")
        
        comp_id = computer.get("general", {}).get("id")
        if not comp_id:
            raise ValueError(f"Could not get ID for serial: {serial_number}")
        
        logger.debug(f"Found computer ID: {comp_id}")
        
        return self._send_wake_commands([int(comp_id)], dry_run)
    
    def wake_serials(
        self,
        serial_numbers: List[str],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Wake up multiple computers by serial numbers.
        
        Args:
            serial_numbers: List of serial numbers
            dry_run: If True, don't send commands
        
        Returns:
            Results dictionary
        """
        logger.debug(f"Looking up {len(serial_numbers)} computers by serial")
        
        computer_ids = []
        not_found = []
        
        for serial in serial_numbers:
            serial = serial.strip()
            if not serial or serial.startswith("#"):
                continue
            
            computer = self.jamf.get_computer_by_serial(serial)
            if computer:
                comp_id = computer.get("general", {}).get("id")
                if comp_id:
                    computer_ids.append(int(comp_id))
                else:
                    not_found.append(serial)
            else:
                not_found.append(serial)
        
        if not_found:
            logger.warning(f"Computers not found: {not_found}")
        
        if not computer_ids:
            return {"total": 0, "successful": 0, "failed": 0, "not_found": not_found}
        
        results = self._send_wake_commands(computer_ids, dry_run)
        results["not_found"] = not_found
        return results
    
    def wake_from_file(
        self,
        file_path: str,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Wake up computers from a file containing serial numbers.
        
        Args:
            file_path: Path to file with serial numbers (one per line)
            dry_run: If True, don't send commands
        
        Returns:
            Results dictionary
        """
        path = Path(file_path)
        
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        serial_numbers = []
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if line and not line.startswith("#"):
                    serial_numbers.append(line)
        
        logger.debug(f"Loaded {len(serial_numbers)} serial numbers from {file_path}")
        
        return self.wake_serials(serial_numbers, dry_run)
    
    def _send_wake_commands(
        self,
        computer_ids: List[int],
        dry_run: bool,
    ) -> Dict[str, Any]:
        """
        Send wake-up commands to computers.
        
        Args:
            computer_ids: List of Jamf computer IDs
            dry_run: If True, don't send commands
        
        Returns:
            Results dictionary
        """
        results = {
            "total": len(computer_ids),
            "successful": 0,
            "failed": 0,
            "details": [],
        }
        
        for comp_id in computer_ids:
            if dry_run:
                logger.info(f"[DRY-RUN] Would send wake-up to computer {comp_id}")
                results["successful"] += 1
                results["details"].append({
                    "computer_id": comp_id,
                    "status": "dry-run",
                })
            else:
                result = self.jamf.redeploy_management_framework(comp_id)
                if result:
                    results["successful"] += 1
                    results["details"].append({
                        "computer_id": comp_id,
                        "status": "success",
                        "command_uuid": result.get("commandUuid"),
                    })
                else:
                    results["failed"] += 1
                    results["details"].append({
                        "computer_id": comp_id,
                        "status": "failed",
                    })
        
        # Print summary
        self._print_summary(results, dry_run)
        
        return results
    
    def _print_summary(self, results: Dict[str, Any], dry_run: bool) -> None:
        """Print processing summary."""
        
        mode = "DRY RUN" if dry_run else "LIVE RUN"
        
        not_found = len(results.get("not_found", []))
        logger.info(
            f"Wake-Up ({mode}): {results['total']} total, "
            f"{results['successful']} ok, "
            f"{results['failed']} failed"
            + (f", {not_found} not found" if not_found else "")
        )
        if results["failed"] > 0:
            for detail in results["details"]:
                if detail["status"] == "failed":
                    logger.warning(f"  Failed: computer {detail['computer_id']}")
    
    def close(self) -> None:
        """Clean up resources."""
        self.jamf.close()


def run_wakeup_group(
    config: Config,
    group_id: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Wake up all computers in a group."""
    module = WakeUpModule(config)
    try:
        return module.wake_group(group_id=group_id, dry_run=dry_run)
    finally:
        module.close()


def run_wakeup_serial(
    config: Config,
    serial_number: str,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Wake up a single computer by serial."""
    module = WakeUpModule(config)
    try:
        return module.wake_serial(serial_number=serial_number, dry_run=dry_run)
    finally:
        module.close()


def run_wakeup_file(
    config: Config,
    file_path: str,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Wake up computers from a file."""
    module = WakeUpModule(config)
    try:
        return module.wake_from_file(file_path=file_path, dry_run=dry_run)
    finally:
        module.close()
