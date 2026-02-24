"""
Jamf-SnipeIT Suite - User Match Module
Auto-provisions Snipe-IT assets from Jamf and matches users.
"""
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import Config
from clients.jamf import JamfClient
from clients.snipeit import SnipeITClient
from clients.slack import SlackClient
from infra.audit_csv import AuditCSV
from infra.progress import ProgressTracker
from infra.helpers import rate_limit_delay
from matching.user_matcher import UserMatcher, pick_primary_local_identity

logger = logging.getLogger(__name__)


class UserMatchModule:
    """
    Module to auto-provision Snipe-IT assets from Jamf computers,
    match local users to Snipe-IT users, and checkout assets.
    """
    
    def __init__(self, config: Config, model_map_path: Optional[str] = None):
        """
        Initialize the User Match module.
        
        Args:
            config: Suite configuration
            model_map_path: Path to model_identifier -> snipe_model_id JSON map
        """
        self.config = config
        self.settings = config.modules.get("user_match", {})
        self.batch_size = self.settings.get("batch_size", 100)
        self.batch_delay = self.settings.get("batch_delay_seconds", 30)
        
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
        
        # Load model map
        self.model_map = self._load_model_map(model_map_path)
        
        # Slack notifications (for ambiguous-match warnings)
        self.slack = SlackClient(
            bot_token=config.slack.bot_token,
            channel_id=config.slack.channel_id,
            enabled=config.slack.enabled,
        )
        
        # User directory (lazy loaded)
        self._user_matcher: Optional[UserMatcher] = None
    
    def _load_model_map(self, path: Optional[str]) -> Dict[str, int]:
        """Load model identifier to Snipe model ID mapping."""
        if not path:
            # Try default location
            default_path = Path("config/model_map.json")
            if default_path.exists():
                path = str(default_path)
            else:
                return {}
        
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load model map from {path}: {e}")
            return {}
    
    def _get_user_matcher(self) -> UserMatcher:
        """Get or create user matcher with Snipe-IT users."""
        if self._user_matcher is None:
            logger.debug("Loading Snipe-IT users for matching...")
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
            logger.debug(f"Loaded {len(users)} users for matching")
        return self._user_matcher
    
    def _choose_model_id(self, model_identifier: str) -> int:
        """Choose Snipe model ID from model identifier."""
        if model_identifier and model_identifier in self.model_map:
            return int(self.model_map[model_identifier])
        return self.config.snipeit.model_fallback_id
    
    def run(
        self,
        smart_group: Optional[str] = None,
        limit: Optional[int] = None,
        dry_run: bool = False,
        allow_reassignment: bool = False,
    ) -> Dict[str, Any]:
        """
        Run the user match provisioning.
        
        Args:
            smart_group: Jamf smart group name (defaults to config)
            limit: Maximum devices to process
            dry_run: If True, don't make changes
            allow_reassignment: Allow reassigning assets to different users
        
        Returns:
            Results dictionary with statistics
        """
        group_name = smart_group or self.config.jamf.smart_group
        
        if not group_name:
            raise ValueError("Smart group name required. Set --smart-group or in config.")
        
        # Use config setting if not overridden
        if not allow_reassignment:
            allow_reassignment = self.config.matching.allow_reassignment
        
        logger.info(f"Starting User Match: group={group_name}, dry_run={dry_run}")
        
        results = {
            "total_devices": 0,
            "assets_created": 0,
            "assets_updated": 0,
            "checkouts": 0,
            "reassignments": 0,
            "skipped": 0,
            "errors": 0,
        }
        
        # Get computers from smart group
        computers = self.jamf.get_computers_in_smart_group(group_name)
        
        if not computers:
            logger.warning(f"No computers in smart group: {group_name}")
            return results
        
        if limit and len(computers) > limit:
            computers = computers[:limit]
        
        results["total_devices"] = len(computers)
        logger.info(f"Processing {len(computers)} devices")
        
        # Initialize audit CSV
        audit = AuditCSV(
            log_dir=self.config.logging.dir,
            module_name="user_match",
            headers=[
                "timestamp", "jamf_id", "serial", "hostname", "primary_username",
                "snipe_user_id", "snipe_user_email", "asset_id", "action", "result", "notes"
            ],
            enabled=self.config.logging.audit_csv,
        )
        
        progress = ProgressTracker("User Match", total=len(computers), log_every=50)
        
        try:
            for i, comp_brief in enumerate(computers, 1):
                logger.debug(f"[{i}/{len(computers)}] Processing device {comp_brief.get('id')}")
                
                try:
                    self._process_device(
                        comp_brief, dry_run, allow_reassignment, results, audit
                    )
                except Exception as e:
                    logger.exception(f"Error processing device {comp_brief.get('id')}: {e}")
                    results["errors"] += 1
                    audit.write(
                        jamf_id=comp_brief.get("id"),
                        serial=comp_brief.get("serial_number", ""),
                        action="error",
                        result="error",
                        notes=str(e),
                    )
                
                progress.advance()
                
                # Batch delay
                if i % self.batch_size == 0 and i < len(computers):
                    batch_num = i // self.batch_size
                    total_batches = (len(computers) + self.batch_size - 1) // self.batch_size
                    rate_limit_delay(self.batch_delay, "User Match", batch_num, total_batches)
        
        finally:
            audit.close()
        
        progress.finish(extra=f"created={results['assets_created']}, updated={results['assets_updated']}, errors={results['errors']}")
        
        # Send Slack notification for ambiguous matches so admin can fix them
        if self._user_matcher and self._user_matcher.warnings:
            self.slack.notify_matching_warnings(self._user_matcher.warnings)
        
        # Print summary
        self._print_summary(results, dry_run)
        
        return results
    
    def _process_device(
        self,
        comp_brief: Dict[str, Any],
        dry_run: bool,
        allow_reassignment: bool,
        results: Dict[str, Any],
        audit: AuditCSV,
    ) -> None:
        """Process a single device."""
        
        comp_id = comp_brief.get("id")
        serial = comp_brief.get("serial_number") or comp_brief.get("serial") or ""
        hostname = comp_brief.get("name") or comp_brief.get("computer_name") or ""
        
        # Get full computer details
        detail = self.jamf.get_computer_by_id(comp_id)
        if not detail:
            logger.warning(f"Could not fetch details for computer {comp_id}")
            results["skipped"] += 1
            return
        
        computer = detail.get("computer", {}) or {}
        general = computer.get("general", {}) or {}
        hardware = computer.get("hardware", {}) or {}
        location = computer.get("location", {}) or {}
        groups_accounts = computer.get("groups_accounts", {}) or {}
        
        # Get local users
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
        
        # Update serial/hostname from detailed info
        serial = serial or hardware.get("serial_number", "")
        hostname = hostname or general.get("name", "")
        
        # Pick primary local user (pass config skip list + Jamf location data)
        skip_usernames = self.config.matching.skip_usernames
        primary_username, full_name_hint = pick_primary_local_identity(
            local_users,
            skip_usernames=skip_usernames,
            location=location,
        )
        logger.debug(f"Primary identity: username={primary_username}, name={full_name_hint}")
        
        if not primary_username:
            logger.warning(f"No primary user for device {comp_id}, skipping")
            results["skipped"] += 1
            audit.write(
                jamf_id=comp_id, serial=serial, hostname=hostname,
                action="skip", result="skipped", notes="No primary username"
            )
            return
        
        # Check if this is a generic/shared username that should be skipped
        skip_usernames = [u.lower() for u in self.config.matching.skip_usernames]
        if primary_username.lower() in skip_usernames:
            logger.debug(f"Skipping generic/shared username: {primary_username}")
            results["skipped"] += 1
            audit.write(
                jamf_id=comp_id, serial=serial, hostname=hostname,
                primary_username=primary_username,
                action="skip", result="skipped", notes=f"Generic username: {primary_username}"
            )
            return
        
        # Match to Snipe-IT user
        matcher = self._get_user_matcher()
        user_match, debug_info = matcher.best_match(
            full_name_hint=full_name_hint or "",
            username=primary_username,
        )
        
        snipe_user_id = user_match.get("id") if user_match else None
        snipe_email = user_match.get("email") if user_match else None
        snipe_name = user_match.get("name") if user_match else None
        snipe_username = user_match.get("username") if user_match else None
        
        if debug_info.get("exact_hit_reason"):
            logger.debug(f"Exact match: {debug_info['exact_hit_reason']}")
        
        # If match was rejected due to ambiguity, skip user operations for this device
        if debug_info.get("rejected_reason"):
            logger.warning(f"Match rejected ({debug_info['rejected_reason']}) for user '{primary_username}', device {comp_id}")
            snipe_user_id = None
            snipe_email = None
            snipe_name = None
            snipe_username = None
        
        # Check if matched Snipe-IT user is a generic/shared account that should be skipped
        if snipe_username and snipe_username.lower() in skip_usernames:
            logger.debug(f"Skipping checkout to generic Snipe-IT user: {snipe_username}")
            snipe_user_id = None  # Don't checkout to this user
            snipe_email = None
            snipe_name = None
        
        if snipe_user_id:
            logger.debug(f"Matched Snipe user: id={snipe_user_id}, email={snipe_email}")
        else:
            logger.debug("No confident Snipe user match")
        
        # Find or create asset
        asset = self.snipe.get_asset_by_serial(serial)
        action = "update"
        
        if asset:
            logger.debug(f"Existing Snipe asset: id={asset.get('id')}")
            
            # Check current status - DO NOT override pending status (set by Leavers module)
            current_status_label = asset.get("status_label")
            current_status_id = None
            if isinstance(current_status_label, dict):
                current_status_id = current_status_label.get("id")
                status_name = current_status_label.get("name", "")
            elif isinstance(current_status_label, (int, str)):
                current_status_id = int(current_status_label) if current_status_label else None
                status_name = str(current_status_label)
            else:
                status_name = ""
            
            pending_id = self.config.snipeit.status_pending_id
            if current_status_id and current_status_id == pending_id:
                logger.debug(f"Asset {asset.get('id')} is in pending/leaver status — skipping to preserve Leavers module state")
                results["skipped"] += 1
                audit.write(
                    jamf_id=comp_id, serial=serial, hostname=hostname,
                    primary_username=primary_username,
                    asset_id=str(asset.get('id')),
                    action="skip", result="skipped",
                    notes=f"Asset in pending status (set by Leavers)"
                )
                return
            
            # Only update status to deployed if it's not already deployed
            deployed_id = self.config.snipeit.status_deployed_id
            if current_status_id != deployed_id:
                if not dry_run:
                    self.snipe.update_asset_status(
                        asset.get("id"),
                        deployed_id
                    )
            
            results["assets_updated"] += 1
        else:
            # Create new asset
            model_identifier = hardware.get("model_identifier", "")
            model_id = self._choose_model_id(model_identifier)
            
            action = "create"
            
            if dry_run:
                logger.info(f"[DRY-RUN] Would create asset: serial={serial}, model_id={model_id}")
                asset = {"id": "DRY-RUN", "serial": serial}
            else:
                asset = self.snipe.create_asset(
                    name=serial,
                    serial=serial,
                    model_id=model_id,
                    status_id=self.config.snipeit.status_deployed_id,
                    company_id=self.config.snipeit.company_id,
                    location_id=self.config.snipeit.location_id,
                )
            
            if asset:
                results["assets_created"] += 1
                logger.info(f"Created Snipe asset: id={asset.get('id')}")
            else:
                logger.error(f"Failed to create asset for serial {serial}")
                results["errors"] += 1
                return
        
        asset_id = asset.get("id")
        
        # Handle checkout/reassignment
        if snipe_user_id and asset_id != "DRY-RUN":
            # Check current assignment
            current_asset = self.snipe.get_asset_by_id(asset_id) if not dry_run else asset
            current_uid = self.snipe.get_assigned_user_id(current_asset) if current_asset else None
            
            if current_uid and current_uid == int(snipe_user_id):
                # Already assigned to correct user — no action needed
                logger.debug(f"Asset {asset_id} already correctly assigned to user {snipe_user_id}, skipping")
            elif current_uid and current_uid != int(snipe_user_id):
                # Different user assigned
                if allow_reassignment:
                    # Safety check: log the old and new users for audit trail
                    action = "reassign"
                    if dry_run:
                        logger.info(f"[DRY-RUN] Would reassign asset {asset_id} from user {current_uid} to user {snipe_user_id}")
                        results["reassignments"] += 1
                    else:
                        # Check in first, then immediately checkout to minimize race window
                        logger.info(f"Reassigning asset {asset_id} from user {current_uid} to user {snipe_user_id}")
                        checkin_ok = self.snipe.checkin_asset(asset_id, note="Auto check-in for reassignment")
                        if checkin_ok:
                            checkout_ok = self.snipe.checkout_asset(asset_id, int(snipe_user_id))
                            if checkout_ok:
                                logger.info(f"Reassignment successful: asset {asset_id} -> user {snipe_user_id}")
                                results["reassignments"] += 1
                            else:
                                logger.error(f"Checkout failed after checkin for asset {asset_id}")
                                results["errors"] += 1
                        else:
                            logger.error(f"Checkin failed for asset {asset_id}, skipping reassignment")
                            results["errors"] += 1
                else:
                    logger.debug(f"Asset {asset_id} assigned to {current_uid}, reassignment disabled")
            elif not current_uid:
                # Not assigned, checkout
                action = "checkout"
                if dry_run:
                    logger.debug(f"[DRY-RUN] Would checkout asset {asset_id} to user {snipe_user_id}")
                    results["checkouts"] += 1
                else:
                    logger.debug(f"Checking out asset {asset_id} to user {snipe_user_id}")
                    checkout_ok = self.snipe.checkout_asset(asset_id, int(snipe_user_id))
                    if checkout_ok:
                        logger.info(f"Checkout successful: asset {asset_id} -> user {snipe_user_id}")
                        results["checkouts"] += 1
                    else:
                        logger.error(f"Checkout failed for asset {asset_id}")
                        results["errors"] += 1
        
        # Update Jamf with EA - only update location fields if we have a valid Snipe user match
        # This prevents overwriting good data with empty values
        ea_name = self.config.jamf.ea_snipe_asset_id
        
        if snipe_user_id:
            # We have a confident match — update Jamf with Snipe-IT user data + asset ID EA
            update_username = primary_username if primary_username else location.get("username", "")
            update_realname = snipe_name if snipe_name else location.get("real_name", "")
            update_email = snipe_email if snipe_email else location.get("email_address", "")
            update_position = location.get("position", "")
            
            self.jamf.update_computer_location_and_ea(
                comp_id,
                username=update_username,
                realname=update_realname,
                email=update_email,
                position=update_position,
                ea_name=ea_name,
                ea_value=str(asset_id) if asset_id != "DRY-RUN" else "",
                dry_run=dry_run,
            )
        elif asset_id and asset_id != "DRY-RUN":
            # No user match, but we still need to set the asset ID EA
            # Only update the EA, don't touch location fields (preserve existing data)
            self.jamf.update_computer_location_and_ea(
                comp_id,
                username=location.get("username", ""),
                realname=location.get("real_name", ""),
                email=location.get("email_address", ""),
                position=location.get("position", ""),
                ea_name=ea_name,
                ea_value=str(asset_id),
                dry_run=dry_run,
            )
        
        # Write audit record
        audit.write(
            jamf_id=comp_id,
            serial=serial,
            hostname=hostname,
            primary_username=primary_username,
            snipe_user_id=str(snipe_user_id or ""),
            snipe_user_email=snipe_email or "",
            asset_id=str(asset_id),
            action=action,
            result="ok",
            notes="",
        )
    
    def _print_summary(self, results: Dict[str, Any], dry_run: bool) -> None:
        """Print processing summary."""
        
        mode = "DRY RUN" if dry_run else "LIVE RUN"
        
        print("\n" + "=" * 60)
        print(f"USER MATCH MODULE - {mode} COMPLETE")
        print("=" * 60)
        print(f"Total devices:      {results['total_devices']}")
        print(f"Assets created:     {results['assets_created']}")
        print(f"Assets updated:     {results['assets_updated']}")
        print(f"Checkouts:          {results['checkouts']}")
        print(f"Reassignments:      {results['reassignments']}")
        print(f"Skipped:            {results['skipped']}")
        print(f"Errors:             {results['errors']}")
        print("=" * 60 + "\n")
    
    def close(self) -> None:
        """Clean up resources."""
        self.jamf.close()
        self.snipe.close()


def run_user_match(
    config: Config,
    smart_group: Optional[str] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
    allow_reassignment: bool = False,
) -> Dict[str, Any]:
    """
    Convenience function to run the user match module.
    
    Args:
        config: Suite configuration
        smart_group: Jamf smart group name
        limit: Maximum devices to process
        dry_run: If True, don't make changes
        allow_reassignment: Allow reassigning assets
    
    Returns:
        Results dictionary
    """
    module = UserMatchModule(config)
    try:
        return module.run(
            smart_group=smart_group,
            limit=limit,
            dry_run=dry_run,
            allow_reassignment=allow_reassignment,
        )
    finally:
        module.close()
