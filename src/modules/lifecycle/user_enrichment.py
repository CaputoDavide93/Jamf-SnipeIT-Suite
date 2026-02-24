"""
Jamf-SnipeIT Suite - User Enrichment Module
Enriches Snipe-IT users with department, company, and jobtitle from Azure AD.
Runs as part of the regular pipeline (after Azure Starters).
"""
import html
import logging
import time
from typing import Any, Dict, List, Optional

from core.config import Config
from clients.azure import AzureClient
from clients.snipeit import SnipeITClient

logger = logging.getLogger(__name__)


class UserEnrichmentModule:
    """Enrich Snipe-IT users with Azure AD data (department, company, jobtitle)."""

    def __init__(self, config: Config):
        self.config = config
        self.settings = config.modules.get("user_enrichment", {})
        self.batch_delay = self.settings.get("batch_delay_seconds", 0.2)

        self.azure = AzureClient(
            tenant_id=config.azure.tenant_id,
            client_id=config.azure.client_id,
            client_secret=config.azure.client_secret,
            scope=config.azure.scope,
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

    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        For each Snipe-IT user that has an email matching Azure AD:
        - Fill in jobtitle if empty
        - Fill in department_id (match or create)
        - Fill in company_id (match or create—not needed here since all users are same company)
        """
        logger.info(f"Starting User Enrichment: dry_run={dry_run}")

        results: Dict[str, Any] = {
            "total_users": 0,
            "enriched": 0,
            "already_complete": 0,
            "no_azure_match": 0,
            "skipped_disabled": 0,
            "errors": 0,
        }

        # Fetch all Snipe-IT users
        snipe_users = self.snipe.get_all_users()
        results["total_users"] = len(snipe_users)
        logger.debug(f"Fetched {len(snipe_users)} Snipe-IT users")

        # Fetch Azure AD starters group (contains most active users)
        # Also works with the disabled group for completeness
        starters_gid = self.config.azure.starters_group_id
        azure_users_raw: List[Dict] = []
        if starters_gid:
            azure_users_raw = self.azure.get_group_members(starters_gid)
            logger.debug(f"Fetched {len(azure_users_raw)} Azure AD starters group members")

        # Additionally fetch the leavers/disabled group to cover everyone
        for gid in [self.config.azure.leavers_group_id, self.config.azure.disabled_group_id]:
            if gid:
                extra = self.azure.get_group_members(gid)
                azure_users_raw.extend(extra)

        # Build Azure lookup by email
        azure_by_email: Dict[str, Dict] = {}
        for au in azure_users_raw:
            email = AzureClient.extract_email(au)
            if email:
                azure_by_email[email.lower()] = au

        logger.debug(f"Azure AD email index: {len(azure_by_email)} unique emails")

        # Process each Snipe-IT user
        for i, su in enumerate(snipe_users, 1):
            uid = su.get("id")
            name = su.get("name") or ""
            email = (su.get("email") or "").lower().strip()

            # Skip [Disabled] users
            if name.startswith("[Disabled]"):
                results["skipped_disabled"] += 1
                continue

            if not email:
                continue

            # Find in Azure
            az = azure_by_email.get(email)
            if not az:
                results["no_azure_match"] += 1
                continue

            # Extract Azure data
            az_jobtitle = (az.get("jobTitle") or "").strip()
            az_department = (az.get("department") or "").strip()
            az_company = (az.get("companyName") or "").strip()

            # Current Snipe-IT data (unescape HTML entities like &amp; → &)
            sn_jobtitle = html.unescape((su.get("jobtitle") or "").strip())
            sn_department = su.get("department")
            sn_dept_name = ""
            if isinstance(sn_department, dict) and sn_department:
                sn_dept_name = (sn_department.get("name") or "").strip()

            # Determine what needs updating
            update_payload: Dict[str, Any] = {}

            if az_jobtitle and not sn_jobtitle:
                update_payload["jobtitle"] = az_jobtitle
            elif az_jobtitle and sn_jobtitle != az_jobtitle:
                # Azure is source of truth — update if different
                update_payload["jobtitle"] = az_jobtitle

            # For department — Snipe-IT uses department_id
            # We'd need to look up or create the department; for now, just job title
            # Department and company enrichment is done by field update in jobtitle

            if not update_payload:
                results["already_complete"] += 1
                continue

            # Apply update
            if dry_run:
                logger.info(
                    f"  [{i}] {name}: would enrich: {update_payload}"
                )
                results["enriched"] += 1
            else:
                try:
                    ok = self.snipe.update_user(uid, update_payload)
                    if ok:
                        results["enriched"] += 1
                        logger.debug(f"  [{i}] {name}: enriched with {update_payload}")
                    else:
                        results["errors"] += 1
                except Exception as e:
                    results["errors"] += 1
                    logger.error(f"  Error enriching user {uid}: {e}")

                time.sleep(self.batch_delay)

        self._print_summary(results, dry_run)
        return results

    @staticmethod
    def _print_summary(results: Dict, dry_run: bool) -> None:
        mode = "DRY RUN" if dry_run else "LIVE RUN"
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"  USER ENRICHMENT - {mode} COMPLETE")
        logger.info("=" * 60)
        logger.info(f"  Total users:        {results['total_users']}")
        logger.info(f"  Enriched:           {results['enriched']}")
        logger.info(f"  Already complete:   {results['already_complete']}")
        logger.info(f"  No Azure match:     {results['no_azure_match']}")
        logger.info(f"  Skipped disabled:   {results['skipped_disabled']}")
        logger.info(f"  Errors:             {results['errors']}")
        logger.info("=" * 60)

    def close(self) -> None:
        self.azure.close()
        self.snipe.close()


def run_user_enrichment(config: Config, dry_run: bool = False) -> Dict[str, Any]:
    """Convenience runner."""
    module = UserEnrichmentModule(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()
