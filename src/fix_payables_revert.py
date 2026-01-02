#!/usr/bin/env python3
"""
TEMPORARY SCRIPT - Revert assets from CreateFuture Payables to previous owners

This script:
1. Gets all assets assigned to "CreateFuture Payables" (user ID 887)
2. For each asset, checks the activity history
3. Finds the previous owner (the user it was checked in FROM before being assigned to Payables)
4. Reassigns the asset back to that previous owner
"""
import sys
import os
import time
import logging
import argparse

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import get_config
from core.snipe_client import SnipeITClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

PAYABLES_USER_ID = 887  # CreateFuture Payables user ID


def get_previous_owner_from_history(snipe: SnipeITClient, asset_id: int) -> dict:
    """
    Get the previous owner of an asset by checking its activity history.
    
    Looks for the pattern:
    - checkout to Payables
    - checkin from [PREVIOUS_OWNER]  <-- This is who we want
    
    Returns the previous owner dict or None
    """
    # Get asset activity/history
    try:
        url = f"{snipe.base_url}/api/v1/reports/activity"
        params = {
            "item_type": "asset",
            "item_id": asset_id,
            "limit": 50,
            "order": "desc"  # Most recent first
        }
        response = snipe.session.get(url, params=params, timeout=snipe.timeout)
        response.raise_for_status()
        data = response.json()
        
        activities = data.get("rows", [])
        
        # Look for the checkin that happened right before checkout to Payables
        found_payables_checkout = False
        
        for activity in activities:
            action_type = activity.get("action_type", "")
            target = activity.get("target", {})
            
            # First, find the checkout to Payables
            if action_type == "checkout" and target:
                target_id = target.get("id")
                if target_id == PAYABLES_USER_ID:
                    found_payables_checkout = True
                    continue
            
            # After finding Payables checkout, look for the checkin from previous owner
            if found_payables_checkout and action_type == "checkin from":
                if target and target.get("id"):
                    return {
                        "id": target.get("id"),
                        "name": target.get("name", "Unknown"),
                    }
        
        # Alternative: look for any checkout before the Payables one
        for i, activity in enumerate(activities):
            action_type = activity.get("action_type", "")
            target = activity.get("target", {})
            
            if action_type == "checkout" and target:
                target_id = target.get("id")
                if target_id == PAYABLES_USER_ID:
                    # Look at the next activity (older one)
                    for j in range(i + 1, len(activities)):
                        older_activity = activities[j]
                        older_action = older_activity.get("action_type", "")
                        older_target = older_activity.get("target", {})
                        
                        # Found a previous checkout to someone else
                        if older_action == "checkout" and older_target:
                            older_target_id = older_target.get("id")
                            if older_target_id and older_target_id != PAYABLES_USER_ID:
                                return {
                                    "id": older_target_id,
                                    "name": older_target.get("name", "Unknown"),
                                }
                        
                        # Found a checkin from someone
                        if older_action == "checkin from" and older_target:
                            return {
                                "id": older_target.get("id"),
                                "name": older_target.get("name", "Unknown"),
                            }
                    break
        
        return None
        
    except Exception as e:
        logger.error(f"Error getting activity for asset {asset_id}: {e}")
        return None


def get_payables_assets(snipe: SnipeITClient) -> list:
    """Get all assets assigned to CreateFuture Payables."""
    logger.info(f"Fetching assets assigned to user {PAYABLES_USER_ID} (CreateFuture Payables)...")
    
    try:
        url = f"{snipe.base_url}/api/v1/users/{PAYABLES_USER_ID}/assets"
        params = {"limit": 500}
        response = snipe.session.get(url, params=params, timeout=snipe.timeout)
        response.raise_for_status()
        data = response.json()
        
        assets = data.get("rows", [])
        logger.info(f"Found {len(assets)} assets assigned to Payables")
        return assets
        
    except Exception as e:
        logger.error(f"Error fetching Payables assets: {e}")
        return []


def revert_asset(snipe: SnipeITClient, asset_id: int, asset_tag: str, 
                 previous_owner: dict, dry_run: bool) -> bool:
    """
    Revert an asset back to its previous owner.
    
    1. Check in from Payables
    2. Check out to previous owner
    """
    owner_id = previous_owner["id"]
    owner_name = previous_owner["name"]
    
    if dry_run:
        logger.info(f"[DRY-RUN] Would revert asset {asset_tag} (ID: {asset_id}) to {owner_name} (ID: {owner_id})")
        return True
    
    try:
        # Step 1: Check in the asset from Payables
        logger.info(f"Checking in asset {asset_tag} from Payables...")
        checkin_url = f"{snipe.base_url}/api/v1/hardware/{asset_id}/checkin"
        checkin_data = {"note": "Reverting incorrect Payables assignment"}
        
        response = snipe.session.post(checkin_url, json=checkin_data, timeout=snipe.timeout)
        response.raise_for_status()
        result = response.json()
        
        if result.get("status") != "success":
            logger.error(f"Check-in failed for asset {asset_tag}: {result}")
            return False
        
        time.sleep(0.5)  # Small delay between operations
        
        # Step 2: Check out to previous owner
        logger.info(f"Checking out asset {asset_tag} to {owner_name}...")
        checkout_url = f"{snipe.base_url}/api/v1/hardware/{asset_id}/checkout"
        checkout_data = {
            "checkout_to_type": "user",
            "assigned_user": owner_id,
            "note": "Reverted from incorrect Payables assignment"
        }
        
        response = snipe.session.post(checkout_url, json=checkout_data, timeout=snipe.timeout)
        response.raise_for_status()
        result = response.json()
        
        if result.get("status") != "success":
            logger.error(f"Checkout failed for asset {asset_tag}: {result}")
            return False
        
        logger.info(f"✅ Successfully reverted asset {asset_tag} to {owner_name}")
        return True
        
    except Exception as e:
        logger.error(f"Error reverting asset {asset_tag}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Revert assets from Payables to previous owners")
    parser.add_argument('--config', default='config/config.yaml', help='Config file path')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without making changes')
    parser.add_argument('--limit', type=int, default=0, help='Limit number of assets to process (0 = all)')
    args = parser.parse_args()
    
    print("\n" + "=" * 70)
    print("  PAYABLES REVERT SCRIPT - Fixing incorrect asset assignments")
    print("=" * 70)
    
    if args.dry_run:
        print("🧪 DRY RUN MODE - No changes will be made\n")
    else:
        print("⚠️  LIVE MODE - Changes WILL be made to Snipe-IT\n")
    
    # Load config
    config = get_config(args.config)
    
    # Initialize Snipe-IT client
    snipe = SnipeITClient(
        base_url=config.snipeit.base_url,
        api_token=config.snipeit.api_token,
        timeout=config.api.timeout_seconds,
        max_retries=config.api.max_retries,
        retry_delay=config.api.retry_delay_seconds,
        rate_limit_wait=config.api.rate_limit_wait_seconds,
    )
    
    # Get assets assigned to Payables
    assets = get_payables_assets(snipe)
    
    if not assets:
        logger.info("No assets found assigned to Payables. Nothing to do.")
        return 0
    
    if args.limit > 0:
        assets = assets[:args.limit]
        logger.info(f"Limited to first {args.limit} assets")
    
    # Process each asset
    stats = {
        "total": len(assets),
        "reverted": 0,
        "no_previous_owner": 0,
        "errors": 0,
    }
    
    # Rate limiting delay (seconds between API calls)
    API_DELAY = 1.5  # 1.5 seconds between operations to avoid 429
    
    print(f"\nProcessing {len(assets)} assets...\n")
    
    for i, asset in enumerate(assets, 1):
        asset_id = asset.get("id")
        asset_tag = asset.get("asset_tag") or asset.get("serial") or str(asset_id)
        asset_name = asset.get("name", "Unknown")
        
        logger.info(f"[{i}/{len(assets)}] Processing: {asset_tag} ({asset_name})")
        
        # Add delay between assets to avoid rate limiting
        if i > 1:
            time.sleep(API_DELAY)
        
        # Get previous owner from history
        previous_owner = get_previous_owner_from_history(snipe, asset_id)
        
        if not previous_owner:
            logger.warning(f"  ⚠️  Could not find previous owner for {asset_tag}")
            stats["no_previous_owner"] += 1
            continue
        
        logger.info(f"  Found previous owner: {previous_owner['name']} (ID: {previous_owner['id']})")
        
        # Revert the asset
        success = revert_asset(snipe, asset_id, asset_tag, previous_owner, args.dry_run)
        
        if success:
            stats["reverted"] += 1
        else:
            stats["errors"] += 1
        
        # Rate limiting
        time.sleep(0.3)
    
    # Print summary
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"  Total assets processed:    {stats['total']}")
    print(f"  Successfully reverted:     {stats['reverted']}")
    print(f"  No previous owner found:   {stats['no_previous_owner']}")
    print(f"  Errors:                    {stats['errors']}")
    print("=" * 70 + "\n")
    
    if args.dry_run:
        print("This was a DRY RUN. Run without --dry-run to apply changes.\n")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
