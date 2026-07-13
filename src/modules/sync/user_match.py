"""
Jamf-SnipeIT Suite - User Match Module
Auto-provisions Snipe-IT assets from Jamf and matches users.
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import Config
from core.client_factory import create_jamf_client, create_snipeit_client, create_slack_client, load_user_overrides
from infra.audit_csv import AuditCSV
from infra.progress import ProgressTracker
from infra.helpers import rate_limit_delay
from matching.user_matcher import (
    UserMatcher,
    can_auto_reassign,
    pick_primary_local_identity,
)
from matching.ai_resolver import AIResolver

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
        
        # Clients (centralised factory)
        self.jamf = create_jamf_client(config)
        self.snipe = create_snipeit_client(config)
        
        # Load model map
        self.model_map = self._load_model_map(model_map_path)
        
        self.slack = create_slack_client(config)

        # User directory (lazy loaded)
        self._user_matcher: Optional[UserMatcher] = None

        # Error circuit breaker
        self._consecutive_errors = 0
        self._error_abort_threshold = max(
            1, int(self.settings.get("consecutive_error_abort", 5))
        )
        self._dry_run = False
        self._dry_run_created_users_by_email: Dict[str, Dict[str, Any]] = {}
    
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
        """Get or create user matcher with Snipe-IT + Azure AD users."""
        if self._user_matcher is None:
            logger.debug("Loading Snipe-IT users for matching...")
            users = self.snipe.get_all_users()

            # Load ALL active Azure AD users for cross-platform matching
            logger.debug("Loading Azure AD users for cross-platform matching...")
            azure_users = []
            try:
                from clients.azure import AzureClient
                azure = AzureClient(
                    tenant_id=self.config.azure.tenant_id,
                    client_id=self.config.azure.client_id,
                    client_secret=self.config.azure.client_secret,
                    scope=self.config.azure.scope,
                    timeout=self.config.api.timeout_seconds,
                )
                azure_users = azure.get_all_active_users()
                azure.close()
                # Store on self for auto-create fallback
                self._azure_users_by_upn = {
                    (u.get("userPrincipalName") or "").lower(): u for u in azure_users
                }
                prefix_candidates: Dict[str, List[Dict[str, Any]]] = {}
                for u in azure_users:
                    upn = (u.get("userPrincipalName") or "").lower()
                    prefix = upn.split("@")[0].replace(".", "").replace("-", "").replace("_", "")
                    if prefix:
                        prefix_candidates.setdefault(prefix, []).append(u)
                self._azure_users_by_prefix = {
                    prefix: matches[0]
                    for prefix, matches in prefix_candidates.items()
                    if len(matches) == 1
                }
                ambiguous_prefixes = sum(
                    1 for matches in prefix_candidates.values() if len(matches) > 1
                )
                if ambiguous_prefixes:
                    logger.warning(
                        "Ignored %d ambiguous Azure username prefixes",
                        ambiguous_prefixes,
                    )
                logger.info(f"Loaded {len(azure_users)} active Azure AD users")
            except Exception as e:
                logger.warning(f"Could not load Azure AD users: {e}")
                self._azure_users_by_upn = {}
                self._azure_users_by_prefix = {}

            # Initialize AI resolver
            ai_api_key = getattr(self.config, 'ai_api_key', '') or os.environ.get('AI_API_KEY', '')
            ai_resolver = (
                AIResolver(
                    api_key=ai_api_key,
                    slack=None if self._dry_run else self.slack,
                    persist_cache=not self._dry_run,
                )
                if ai_api_key
                else None
            )

            self._user_matcher = UserMatcher(
                users=users,
                email_domain=self.config.matching.email_domain,
                min_score=self.config.matching.min_score,
                weight_lcs=self.config.matching.weight_lcs,
                weight_char_overlap=self.config.matching.weight_char_overlap,
                weight_bigram_dice=self.config.matching.weight_bigram_dice,
                use_bigram_dice=self.config.matching.use_bigram_dice,
                ai_resolver=ai_resolver,
                azure_users=azure_users,
                overrides=load_user_overrides(),
            )
            logger.debug(f"Loaded {len(users)} Snipe-IT + {len(azure_users)} Azure AD users for matching")
        return self._user_matcher
    
    def _choose_model_id(self, model_identifier: str) -> int:
        """Choose Snipe model ID from model identifier."""
        if model_identifier and model_identifier in self.model_map:
            return int(self.model_map[model_identifier])
        return self.config.snipeit.model_fallback_id

    def _try_create_from_azure(
        self,
        jamf_username: str,
        jamf_fullname: str,
        serial: str,
        hostname: str,
        dry_run: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """If the Jamf local user exists in Azure AD as active but not in Snipe-IT,
        auto-create them in Snipe-IT. Returns the new Snipe-IT user dict or None."""
        if not getattr(self, "_azure_users_by_prefix", None):
            return None

        # Try to find matching Azure user by normalized username
        uname_norm = jamf_username.lower().replace(".", "").replace("-", "").replace("_", "")
        azure_user = self._azure_users_by_prefix.get(uname_norm)

        # Fallback: match by display name
        if not azure_user and jamf_fullname:
            target = jamf_fullname.lower().strip()
            name_matches = [
                u
                for u in self._azure_users_by_upn.values()
                if (u.get("displayName") or "").lower().strip() == target
            ]
            if len(name_matches) == 1:
                azure_user = name_matches[0]

        if not azure_user:
            return None

        # Must be active
        if not azure_user.get("accountEnabled", True):
            return None

        email = (azure_user.get("mail") or azure_user.get("userPrincipalName") or "").strip()
        if not email:
            return None

        # Check if they actually exist in Snipe-IT by email (race safety)
        existing = self.snipe.find_user_by_email(email)
        if existing:
            existing_name = str(
                existing.get("first_name") or existing.get("name") or ""
            )
            if existing_name.startswith("[Disabled]"):
                logger.warning(
                    "Azure user %s maps to inactive Snipe-IT user %s; not creating or assigning",
                    email,
                    existing.get("id"),
                )
                return None
            return existing

        first = azure_user.get("givenName") or (jamf_fullname.split()[0] if jamf_fullname else email.split("@")[0])
        last = azure_user.get("surname") or (jamf_fullname.split()[-1] if " " in jamf_fullname else first)
        display_name = azure_user.get("displayName") or f"{first} {last}"

        if dry_run:
            planned = self._dry_run_created_users_by_email.get(email.lower())
            if planned:
                return {**planned, "_created": False}
            logger.info(
                "[DRY-RUN] Would create Snipe-IT user %s (%s) for %s (%s)",
                display_name,
                email,
                serial,
                hostname,
            )
            planned = {
                "id": f"DRY-RUN-USER:{email.lower()}",
                "first_name": first,
                "last_name": last,
                "name": display_name,
                "email": email,
                "username": email,
                "_dry_run_created": True,
                "_created": True,
            }
            self._dry_run_created_users_by_email[email.lower()] = planned
            return planned

        # Create them
        import secrets, string
        alphabet = string.ascii_letters + string.digits + "!@#$%"
        pw = ''.join(secrets.choice(alphabet) for _ in range(24))

        user_data = {
            "first_name": first,
            "last_name": last,
            "email": email,
            "username": email,
            "password": pw,
            "password_confirmation": pw,
            "jobtitle": azure_user.get("jobTitle") or "",
        }
        new_user = self.snipe.create_user(user_data)
        if not new_user:
            return None
        new_user["_created"] = True

        logger.info(
            f"Auto-created Snipe-IT user: {display_name} ({email}) "
            f"for Jamf local account '{jamf_username}' on {serial} ({hostname})"
        )
        return new_user
    
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
        self._dry_run = dry_run
        
        results = {
            "total_devices": 0,
            "users_created": 0,
            "assets_created": 0,
            "assets_updated": 0,
            "checkouts": 0,
            "reassignments": 0,
            "jamf_updates": 0,
            "skipped": 0,
            "errors": 0,
            "unmatched_devices": [],
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
                errors_before = results["errors"]
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

                if results["errors"] > errors_before:
                    self._consecutive_errors += 1
                else:
                    self._consecutive_errors = 0

                if self._consecutive_errors >= self._error_abort_threshold:
                    logger.error(
                        f"Aborting run after {self._consecutive_errors} consecutive device errors"
                    )
                    break
                
                progress.advance()
                
                # Batch delay
                if not dry_run and i % self.batch_size == 0 and i < len(computers):
                    batch_num = i // self.batch_size
                    total_batches = (len(computers) + self.batch_size - 1) // self.batch_size
                    rate_limit_delay(self.batch_delay, "User Match", batch_num, total_batches)
        
        finally:
            audit.close()
        
        progress.finish(extra=f"created={results['assets_created']}, updated={results['assets_updated']}, errors={results['errors']}")
        
        if not dry_run and self.config.slack.notify_inline:
            if self._user_matcher and self._user_matcher.warnings:
                self.slack.notify_matching_warnings(self._user_matcher.warnings)

            if results.get("unmatched_devices"):
                self.slack.notify_investigation_needed(
                    channel_id=self.config.slack.channel_id,
                    title="User Match - Unmatched Devices",
                    items=results["unmatched_devices"],
                )

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
        extension_attributes = computer.get("extension_attributes", []) or []
        
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

        if not serial:
            logger.warning(f"Computer {comp_id} has no serial number, skipping")
            results["skipped"] += 1
            audit.write(
                jamf_id=comp_id,
                hostname=hostname,
                action="skip",
                result="skipped",
                notes="Missing serial number",
            )
            return
        
        # Pick primary local user (pass config skip list + Jamf location data)
        skip_usernames = self.config.matching.skip_usernames
        primary_username, full_name_hint, original_email = pick_primary_local_identity(
            local_users,
            skip_usernames=skip_usernames,
            location=location,
        )
        logger.debug(f"Primary identity: username={primary_username}, name={full_name_hint}, email={original_email}")
        
        if not primary_username:
            logger.warning(f"No primary user for device {comp_id}, skipping")
            results["skipped"] += 1
            results["unmatched_devices"].append({
                "description": f"`{serial}` *{hostname}* — No local user account (only admin/system accounts)"
            })
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
            original_email=original_email or "",
        )
        
        snipe_user_id = user_match.get("id") if user_match else None
        snipe_email = user_match.get("email") if user_match else None
        snipe_name = user_match.get("name") if user_match else None
        snipe_username = user_match.get("username") if user_match else None
        assignment_blocked = False
        
        if debug_info.get("exact_hit_reason"):
            logger.debug(f"Exact match: {debug_info['exact_hit_reason']}")
        
        # If match was rejected due to ambiguity, skip user operations for this device
        if debug_info.get("rejected_reason"):
            logger.warning(f"Match rejected ({debug_info['rejected_reason']}) for user '{primary_username}', device {comp_id}")
            top = debug_info.get("top_candidates", [])
            top_str = ", ".join(f"{c.get('name', '?')} ({c.get('score', 0)})" for c in top[:3])
            results["unmatched_devices"].append({
                "description": (
                    f"`{serial}` *{hostname}*\n"
                    f"      Local user: `{primary_username}` ({full_name_hint or 'no name'})\n"
                    f"      Candidates: {top_str}"
                )
            })
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

        if snipe_user_id and str(snipe_name or "").strip().startswith("[Disabled]"):
            logger.warning(
                "Refusing to assign %s to inactive Snipe-IT user %s",
                serial,
                snipe_name,
            )
            results["unmatched_devices"].append({
                "description": (
                    f"`{serial}` *{hostname}* — matched inactive user "
                    f"*{snipe_name}*; assignment blocked"
                )
            })
            snipe_user_id = None
            snipe_email = None
            snipe_name = None
            snipe_username = None
            assignment_blocked = True

        if snipe_user_id:
            logger.debug(f"Matched Snipe user: id={snipe_user_id}, email={snipe_email}")
        elif not debug_info.get("rejected_reason") and not assignment_blocked:
            # Not found in Snipe-IT — check if the Jamf local user exists in Azure AD.
            # If they do (active), auto-create them in Snipe-IT.
            created = self._try_create_from_azure(
                primary_username,
                full_name_hint,
                serial,
                hostname,
                dry_run=dry_run,
            )
            if created:
                snipe_user_id = created.get("id")
                snipe_email = created.get("email")
                snipe_name = created.get("name")
                snipe_username = created.get("username")
                if created.get("_created"):
                    results["users_created"] += 1
                # Refresh user matcher cache so subsequent devices see the new user
                if self._user_matcher and not any(
                    user.get("id") == created.get("id")
                    for user in self._user_matcher.users
                ):
                    self._user_matcher.users.append(created)
                logger.info(
                    f"Auto-created Snipe-IT user from Azure AD: {snipe_name} "
                    f"(id={snipe_user_id}) for local account '{primary_username}'"
                )
            else:
                logger.debug("No confident Snipe user match")
                results["unmatched_devices"].append({
                    "description": (
                        f"`{serial}` *{hostname}*\n"
                        f"      Local user: `{primary_username}` ({full_name_hint or 'no name'})\n"
                        f"      No matching Snipe-IT user found (not in Azure AD either)"
                    )
                })
        
        # Find or create asset
        asset = self.snipe.get_asset_by_serial(serial)
        action = "none"
        
        if asset:
            logger.debug(f"Existing Snipe asset: id={asset.get('id')}")
            
            # Check current status - DO NOT override pending status (set by Leavers module)
            current_status_label = asset.get("status_label")
            current_status_id = None
            if isinstance(current_status_label, dict):
                current_status_id = current_status_label.get("id")
                status_name = current_status_label.get("name", "")
            elif isinstance(current_status_label, (int, str)):
                try:
                    current_status_id = int(current_status_label) if current_status_label else None
                except (TypeError, ValueError):
                    current_status_id = None
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
                action = "status_update"
                if dry_run:
                    logger.info(
                        "[DRY-RUN] Would update asset %s status from %s to %s",
                        asset.get("id"),
                        status_name or current_status_id,
                        deployed_id,
                    )
                    results["assets_updated"] += 1
                elif self.snipe.update_asset_status(asset.get("id"), deployed_id):
                    results["assets_updated"] += 1
                else:
                    logger.error("Failed to update status for asset %s", asset.get("id"))
                    results["errors"] += 1
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
                    asset_tag=self.snipe.next_cf_tag(),
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
        if snipe_user_id:
            if asset_id == "DRY-RUN":
                action = "create_and_checkout"
                logger.info(
                    "[DRY-RUN] Would checkout newly-created asset %s to user %s",
                    serial,
                    snipe_user_id,
                )
                results["checkouts"] += 1
            else:
                current_asset = asset if dry_run else self.snipe.get_asset_by_id(asset_id)
                if current_asset is None:
                    logger.error("Could not verify current assignment for asset %s", asset_id)
                    results["errors"] += 1
                else:
                    current_uid = self.snipe.get_assigned_user_id(current_asset)
                    dry_run_user = str(snipe_user_id).startswith("DRY-RUN-USER:")
                    target_uid = None if dry_run_user else int(snipe_user_id)

                    if target_uid is not None and current_uid == target_uid:
                        logger.debug(
                            "Asset %s already correctly assigned to user %s",
                            asset_id,
                            snipe_user_id,
                        )
                    elif current_uid:
                        match_reason = debug_info.get("exact_hit_reason", "") or ""
                        assigned_to = current_asset.get("assigned_to") or {}
                        current_name = str(
                            assigned_to.get("name")
                            or assigned_to.get("first_name")
                            or ""
                        )
                        current_inactive = current_name.strip().startswith("[Disabled]")
                        if match_reason.startswith("ai_resolved") and not current_name:
                            current_user = self.snipe.get_user_by_id(current_uid) or {}
                            current_name = str(
                                current_user.get("first_name")
                                or current_user.get("name")
                                or ""
                            )
                            current_inactive = current_name.strip().startswith("[Disabled]")

                        may_reassign = not dry_run_user and can_auto_reassign(
                            match_reason,
                            current_inactive=current_inactive,
                            target_inactive=False,
                            allow_reassignment=allow_reassignment,
                        )
                        if not may_reassign:
                            logger.info(
                                "Asset %s matched user %s via %s but remains assigned to %s",
                                asset_id,
                                snipe_user_id,
                                match_reason or "non-deterministic Azure fallback",
                                current_uid,
                            )
                        else:
                            action = "reassign"
                            if dry_run:
                                logger.info(
                                    "[DRY-RUN] Would reassign asset %s from user %s to user %s",
                                    asset_id,
                                    current_uid,
                                    snipe_user_id,
                                )
                                results["reassignments"] += 1
                            else:
                                logger.info(
                                    "Reassigning asset %s from user %s to user %s",
                                    asset_id,
                                    current_uid,
                                    snipe_user_id,
                                )
                                checkin_ok = self.snipe.checkin_asset(
                                    asset_id,
                                    note="Auto check-in for reassignment",
                                )
                                if not checkin_ok:
                                    logger.error("Check-in failed for asset %s", asset_id)
                                    results["errors"] += 1
                                elif self.snipe.checkout_asset(asset_id, target_uid):
                                    logger.info(
                                        "Reassignment successful: asset %s -> user %s",
                                        asset_id,
                                        snipe_user_id,
                                    )
                                    results["reassignments"] += 1
                                else:
                                    logger.warning(
                                        "Checkout failed for asset %s; rolling back to user %s",
                                        asset_id,
                                        current_uid,
                                    )
                                    rollback_ok = self.snipe.checkout_asset(
                                        asset_id,
                                        current_uid,
                                        note="Rollback: reassignment checkout failed",
                                    )
                                    if not rollback_ok:
                                        self._notify_critical_checkout_failure(
                                            asset_id,
                                            current_uid,
                                            target_uid,
                                            hostname,
                                        )
                                    results["errors"] += 1
                    else:
                        action = "checkout"
                        if dry_run:
                            logger.info(
                                "[DRY-RUN] Would checkout asset %s to user %s",
                                asset_id,
                                snipe_user_id,
                            )
                            results["checkouts"] += 1
                        elif self.snipe.checkout_asset(asset_id, target_uid):
                            logger.info(
                                "Checkout successful: asset %s -> user %s",
                                asset_id,
                                snipe_user_id,
                            )
                            results["checkouts"] += 1
                        else:
                            logger.error("Checkout failed for asset %s", asset_id)
                            results["errors"] += 1
        
        # Write back to Jamf:
        #   - Asset ID EA (always, when we have an asset)
        #   - Location fields ONLY when we have a confirmed Snipe-IT match
        #     (this repairs poisoned Jamf location data with verified info
        #      from Snipe-IT, which sources from Azure/HiBob)
        ea_name = self.config.jamf.ea_snipe_asset_id
        if asset_id and asset_id != "DRY-RUN" and ea_name:
            if snipe_user_id:
                # Confident match — write verified Snipe-IT user data to Jamf
                desired_username = primary_username or location.get("username", "")
                desired_realname = snipe_name or location.get("real_name", "")
                desired_email = snipe_email or location.get("email_address", "")
            else:
                # No match — only write the EA, preserve existing location
                desired_username = location.get("username", "")
                desired_realname = location.get("real_name", "")
                desired_email = location.get("email_address", "")

            if self._jamf_update_needed(
                location,
                extension_attributes,
                username=desired_username,
                realname=desired_realname,
                email=desired_email,
                position=location.get("position", ""),
                ea_name=ea_name,
                ea_value=str(asset_id),
            ):
                update_ok = self.jamf.update_computer_location_and_ea(
                    comp_id,
                    username=desired_username,
                    realname=desired_realname,
                    email=desired_email,
                    position=location.get("position", ""),
                    ea_name=ea_name,
                    ea_value=str(asset_id),
                    dry_run=dry_run,
                )
                if update_ok:
                    results["jamf_updates"] += 1
                else:
                    results["errors"] += 1
        
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

    def _notify_critical_checkout_failure(self, asset_id: int, previous_uid: Optional[int], target_uid: Optional[int], hostname: str = "") -> None:
        """Escalate when checkout + rollback fail, to avoid silent orphaning."""
        msg = (
            f"Critical checkout failure: asset {asset_id}, host {hostname or 'unknown'}, "
            f"target user {target_uid}, previous user {previous_uid}"
        )
        logger.error(msg)
        if self.slack:
            try:
                self.slack.send(msg)
            except Exception:
                logger.debug("Slack notify failed for critical checkout failure")

    @staticmethod
    def _jamf_update_needed(
        location: Dict[str, Any],
        extension_attributes: Any,
        *,
        username: str,
        realname: str,
        email: str,
        position: str,
        ea_name: str,
        ea_value: str,
    ) -> bool:
        """Return whether Jamf location or EA values actually differ."""
        desired_location = {
            "username": username,
            "real_name": realname,
            "email_address": email,
            "position": position,
        }
        for key, desired in desired_location.items():
            if str(location.get(key) or "").strip() != str(desired or "").strip():
                return True

        attrs = extension_attributes
        if isinstance(attrs, dict):
            attrs = attrs.get("extension_attribute") or attrs.get("extension_attributes") or []
        if isinstance(attrs, dict):
            attrs = [attrs]
        if not isinstance(attrs, list):
            attrs = []
        for attribute in attrs:
            if attribute.get("name") == ea_name:
                return str(attribute.get("value") or "").strip() != str(ea_value).strip()
        return True
    
    def _print_summary(self, results: Dict[str, Any], dry_run: bool) -> None:
        """Print processing summary."""
        
        mode = "DRY RUN" if dry_run else "LIVE RUN"
        
        logger.info(
            f"User Match ({mode}): {results['total_devices']} devices, "
            f"{results['users_created']} users created, "
            f"{results['assets_created']} created, "
            f"{results['assets_updated']} updated, "
            f"{results['checkouts']} checkouts, "
            f"{results['reassignments']} reassigned, "
            f"{results['jamf_updates']} Jamf updates, "
            f"{results['skipped']} skipped, "
            f"{results['errors']} errors"
        )
    
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
