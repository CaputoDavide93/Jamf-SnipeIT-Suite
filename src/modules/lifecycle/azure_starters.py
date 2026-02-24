"""
Azure Starters Module
Syncs Azure AD starters group members to Snipe-IT users.
Creates new users in Snipe-IT for members not yet present.
"""
import html
import logging
from typing import Dict, Any, List, Optional

from core.config import Config
from clients.azure import AzureClient
from clients.snipeit import SnipeITClient

logger = logging.getLogger(__name__)

# Default batch size for processing
BATCH_SIZE = 50


class AzureStartersModule:
    """Module to sync Azure AD starters group to Snipe-IT users."""
    
    def __init__(self, config: Config, dry_run: bool = False):
        """Initialize the module with configuration.
        
        Args:
            config: Application configuration
            dry_run: If True, simulate actions without making changes
        """
        self.config = config
        self.dry_run = dry_run
        
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
        
        # Module settings
        self.settings = config.modules.get("azure_starters", {})
        self.update_job_titles = self.settings.get("update_job_titles", True)
        self.default_password = self.settings.get("default_password", "password")
        self.starters_group_id = (
            self.settings.get("group_id") or 
            getattr(config.azure, 'starters_group_id', None)
        )
    
    def run(self, dry_run: Optional[bool] = None) -> Dict[str, Any]:
        """Run the Azure Starters sync module.
        
        Args:
            dry_run: Override instance dry_run setting if provided
            
        Returns:
            Dictionary with results summary
        """
        if dry_run is not None:
            self.dry_run = dry_run
        
        mode_str = "[DRY RUN] " if self.dry_run else ""
        
        logger.info(f"")
        logger.info(f"{'='*60}")
        logger.info(f"  {mode_str}Azure Starters Module")
        logger.info(f"{'='*60}")
        logger.info(f"")
        
        # Validate configuration
        if not self.starters_group_id:
            logger.error("No starters group ID configured")
            logger.error("❌ No starters group ID configured")
            logger.error("   Set 'azure.starters_group_id' in config.yaml")
            return {
                "total_azure_users": 0,
                "users_created": 0,
                "users_updated": 0,
                "already_exists": 0,
                "errors": ["No starters group ID configured"]
            }
        
        # Fetch Azure AD starters
        logger.debug(f"📡 Fetching Azure AD starters group: {self.starters_group_id}")
        azure_users = self.azure.get_group_members(self.starters_group_id)
        logger.debug(f"   Found {len(azure_users)} members in Azure AD starters group")
        
        # Fetch all Snipe-IT users
        logger.debug(f"📡 Fetching Snipe-IT users...")
        snipe_users = self.snipe.get_all_users()
        logger.debug(f"   Found {len(snipe_users)} users in Snipe-IT")
        
        # Build email lookup map for Snipe-IT users
        snipe_users_by_email = {}
        for user in snipe_users:
            email = user.get("email") or ""
            email = email.lower().strip()
            if email:
                snipe_users_by_email[email] = user
        
        # Process users
        logger.debug(f"🔄 Processing users...")
        results = self._process_users(azure_users, snipe_users_by_email)
        
        # Print summary
        self._print_summary(results)
        
        return results
    
    def _process_users(
        self,
        azure_users: List[Dict[str, Any]],
        snipe_users_by_email: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Process Azure AD users and sync to Snipe-IT.
        
        Args:
            azure_users: List of Azure AD user objects
            snipe_users_by_email: Dict mapping email -> Snipe-IT user
            
        Returns:
            Results dictionary
        """
        results = {
            "total_azure_users": len(azure_users),
            "users_created": 0,
            "users_updated": 0,
            "already_exists": 0,
            "skipped": 0,
            "errors": [],
            "created_users": [],
            "updated_users": [],
        }
        
        for user in azure_users:
            try:
                self._process_single_user(user, snipe_users_by_email, results)
            except Exception as e:
                logger.error(f"Error processing user {user.get('displayName')}: {e}")
                results["errors"].append(f"{user.get('displayName')}: {str(e)}")
        
        return results
    
    def _process_single_user(
        self,
        azure_user: Dict[str, Any],
        snipe_users_by_email: Dict[str, Dict[str, Any]],
        results: Dict[str, Any],
    ) -> None:
        """Process a single Azure AD user.
        
        Args:
            azure_user: Azure AD user object
            snipe_users_by_email: Dict mapping email -> Snipe-IT user
            results: Results dict to update
        """
        # Extract user info
        email = AzureClient.extract_email(azure_user)
        if not email:
            logger.debug(f"Skipping user without email: {azure_user.get('id')}")
            results["skipped"] += 1
            return
        
        email_lower = email.lower().strip()
        display_name = azure_user.get("displayName", "")
        given_name = azure_user.get("givenName", "")
        surname = azure_user.get("surname", "")
        job_title = azure_user.get("jobTitle", "") or ""
        
        # Derive first/last name if not provided
        if not given_name or not surname:
            parts = display_name.split(" ", 1)
            given_name = given_name or parts[0] if parts else ""
            surname = surname or (parts[1] if len(parts) > 1 else "")
        
        # Extract username from email (part before @)
        username = email_lower.split("@")[0]
        
        logger.debug(f"Processing: {display_name} ({email})")
        
        # Check if user exists in Snipe-IT
        existing_user = snipe_users_by_email.get(email_lower)
        
        if existing_user:
            # User exists - optionally update job title
            self._handle_existing_user(
                existing_user, azure_user, job_title, results
            )
        else:
            # User doesn't exist - create new user
            self._create_new_user(
                email, username, given_name, surname, job_title, results
            )
    
    def _handle_existing_user(
        self,
        snipe_user: Dict[str, Any],
        azure_user: Dict[str, Any],
        job_title: str,
        results: Dict[str, Any],
    ) -> None:
        """Handle an existing Snipe-IT user.
        
        Args:
            snipe_user: Existing Snipe-IT user
            azure_user: Azure AD user data
            job_title: Job title from Azure AD
            results: Results dict to update
        """
        snipe_user_id = snipe_user.get("id")
        display_name = azure_user.get("displayName", "")
        current_job_title = html.unescape(snipe_user.get("jobtitle", "") or "")
        
        # Check if job title needs updating
        if self.update_job_titles and job_title and job_title != current_job_title:
            logger.debug(f"Updating job title for {display_name}: '{current_job_title}' -> '{job_title}'")
            
            if self.dry_run:
                logger.info(f"   [DRY RUN] Would update job title for: {display_name}")
                results["users_updated"] += 1
                results["updated_users"].append({
                    "name": display_name,
                    "email": snipe_user.get("email"),
                    "old_title": current_job_title,
                    "new_title": job_title,
                })
            else:
                try:
                    success = self.snipe.update_user(snipe_user_id, {
                        "jobtitle": job_title
                    })
                    if success:
                        logger.info(f"   ✅ Updated job title for: {display_name}")
                        results["users_updated"] += 1
                        results["updated_users"].append({
                            "name": display_name,
                            "email": snipe_user.get("email"),
                            "old_title": current_job_title,
                            "new_title": job_title,
                        })
                    else:
                        logger.warning(f"Failed to update job title for {display_name}")
                except Exception as e:
                    logger.error(f"Error updating user {display_name}: {e}")
                    results["errors"].append(f"Update {display_name}: {str(e)}")
        else:
            logger.debug(f"User already exists, no update needed: {display_name}")
            results["already_exists"] += 1
    
    def _create_new_user(
        self,
        email: str,
        username: str,
        first_name: str,
        last_name: str,
        job_title: str,
        results: Dict[str, Any],
    ) -> None:
        """Create a new user in Snipe-IT.
        
        Args:
            email: User email
            username: Username
            first_name: First name
            last_name: Last name
            job_title: Job title
            results: Results dict to update
        """
        display_name = f"{first_name} {last_name}".strip()
        logger.debug(f"Creating new Snipe-IT user: {display_name} ({email})")
        
        if self.dry_run:
            logger.info(f"   [DRY RUN] Would create user: {display_name} ({email})")
            results["users_created"] += 1
            results["created_users"].append({
                "name": display_name,
                "email": email,
                "username": username,
                "job_title": job_title,
            })
        else:
            try:
                user_data = {
                    "first_name": first_name,
                    "last_name": last_name or first_name,  # Snipe-IT requires last name
                    "email": email,
                    "username": username,
                    "password": self.default_password,
                    "password_confirmation": self.default_password,
                    "jobtitle": job_title,
                }
                
                result = self.snipe.create_user(user_data)
                if result:
                    logger.info(f"   ✅ Created user: {display_name} ({email})")
                    results["users_created"] += 1
                    results["created_users"].append({
                        "name": display_name,
                        "email": email,
                        "username": username,
                        "job_title": job_title,
                    })
                else:
                    logger.warning(f"Failed to create user: {display_name}")
                    results["errors"].append(f"Create failed: {display_name}")
            except Exception as e:
                logger.error(f"Error creating user {display_name}: {e}")
                results["errors"].append(f"Create {display_name}: {str(e)}")
    
    def _print_summary(self, results: Dict[str, Any]) -> None:
        """Print results summary.
        
        Args:
            results: Results dictionary
        """
        mode_str = "[DRY RUN] " if self.dry_run else ""
        
        logger.info(f"")
        logger.info(f"{'='*60}")
        logger.info(f"  {mode_str}Azure Starters - Summary")
        logger.info(f"{'='*60}")
        logger.info(f"  Total Azure AD users:    {results['total_azure_users']}")
        logger.info(f"  Users created:           {results['users_created']}")
        logger.info(f"  Users updated:           {results['users_updated']}")
        logger.info(f"  Already in Snipe-IT:     {results['already_exists']}")
        logger.info(f"  Skipped (no email):      {results['skipped']}")
        logger.info(f"  Errors:                  {len(results['errors'])}")
        
        # Show created users
        if results["created_users"]:
            logger.info(f"  Created Users:")
            for user in results["created_users"][:10]:
                title_str = f" - {user['job_title']}" if user.get('job_title') else ""
                logger.info(f"    • {user['name']} ({user['email']}){title_str}")
            if len(results["created_users"]) > 10:
                logger.info(f"    ... and {len(results['created_users']) - 10} more")
        
        # Show updated users
        if results["updated_users"]:
            logger.info(f"  Updated Users (job title):")
            for user in results["updated_users"][:10]:
                logger.info(f"    • {user['name']}: '{user['old_title']}' → '{user['new_title']}'")
            if len(results["updated_users"]) > 10:
                logger.info(f"    ... and {len(results['updated_users']) - 10} more")
        
        # Show errors
        if results["errors"]:
            logger.error(f"  ⚠️  Errors:")
            for error in results["errors"][:5]:
                logger.error(f"    • {error}")
            if len(results["errors"]) > 5:
                logger.error(f"    ... and {len(results['errors']) - 5} more")
        
        logger.info(f"{'='*60}")

    def close(self) -> None:
        """Clean up resources."""
        if hasattr(self, 'azure'):
            self.azure.close()
        if hasattr(self, 'snipe'):
            self.snipe.close()


def run_azure_starters(config: Config, dry_run: bool = False) -> Dict[str, Any]:
    """Convenience function to run the Azure Starters module.
    
    Args:
        config: Application configuration
        dry_run: If True, simulate actions
        
    Returns:
        Results dictionary
    """
    module = AzureStartersModule(config, dry_run=dry_run)
    return module.run()
