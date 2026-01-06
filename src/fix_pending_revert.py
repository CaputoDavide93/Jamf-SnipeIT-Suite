#!/usr/bin/env python3
"""
Fix script to revert assets from Pending status back to Deployed.

This script reverts changes made by the Leavers module that incorrectly
set assets to Pending status.

Usage:
    python src/fix_pending_revert.py --dry-run           # Preview changes
    python src/fix_pending_revert.py                     # Apply changes

Options:
    --dry-run       Preview changes without applying them
    --serial SERIAL Only process specific serial number(s)
"""
import argparse
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from core.snipe_client import SnipeITClient
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# Serial numbers of assets to revert (from the screenshot showing today's changes)
SERIALS_TO_REVERT = [
    "C02G8458ML85",
    "J93K09JJX3",
    "T4VMY0K7CF",
    "MHY6GQVD29",
    "HJQ3K7XK33",
    "GWYT25W0JG",
    "TQKXC9F919",
    "C02GG1RYML85",
    "C6L4WD6X5N",
    "Y1HWKD900T",
    "K36KPD2V7N",
    "J63Y0RX07L",
    "K4Q104F722",
    "DV79G3H177",
    "C56P2766N2",
    "WVRHF459CG",
    "T64GGQYGGP",
]


def main():
    parser = argparse.ArgumentParser(description="Revert assets from Pending to Deployed status")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    parser.add_argument("--serial", action="append", help="Specific serial(s) to process")
    parser.add_argument("--all-pending", action="store_true", 
                        help="Revert ALL assets in Pending status (use with caution)")
    args = parser.parse_args()
    
    # Load config
    config = Config()
    
    # Initialize Snipe-IT client
    snipe = SnipeITClient(
        base_url=config.snipeit.base_url,
        api_token=config.snipeit.api_token,
        timeout=config.api.timeout_seconds,
        max_retries=config.api.max_retries,
        retry_delay=config.api.retry_delay_seconds,
    )
    
    # Status IDs from config
    deployed_status_id = config.snipeit.status_deployed_id  # 1 = Deployed
    pending_status_id = config.snipeit.status_pending_id    # 8 = Pending
    
    print("=" * 60)
    print("REVERT PENDING ASSETS TO DEPLOYED")
    print("=" * 60)
    print(f"Deployed Status ID: {deployed_status_id}")
    print(f"Pending Status ID: {pending_status_id}")
    print(f"Dry Run: {args.dry_run}")
    print("=" * 60)
    
    # Determine which serials to process
    if args.serial:
        serials = args.serial
    elif args.all_pending:
        # Fetch all pending assets using search with status filter
        print("\n⚠️  Fetching ALL pending assets...")
        pending_assets = snipe.search_assets(status="Pending", limit=500)
        serials = [a.get("serial") for a in pending_assets if a.get("serial")]
        print(f"Found {len(serials)} pending assets")
    else:
        serials = SERIALS_TO_REVERT
    
    print(f"\nProcessing {len(serials)} serial numbers...")
    
    results = {
        "total": 0,
        "reverted": 0,
        "skipped": 0,
        "not_found": 0,
        "errors": 0,
    }
    
    for serial in serials:
        serial = serial.strip()
        if not serial:
            continue
        
        results["total"] += 1
        logger.info(f"Processing: {serial}")
        
        # Find asset in Snipe-IT
        asset = snipe.get_asset_by_serial(serial)
        if not asset:
            logger.warning(f"Asset not found: {serial}")
            results["not_found"] += 1
            continue
        
        asset_id = asset.get("id")
        asset_name = asset.get("name") or asset.get("asset_tag") or serial
        
        # Get current status
        status_label = asset.get("status_label", {})
        if isinstance(status_label, dict):
            current_status_id = status_label.get("id")
            current_status_name = status_label.get("name", "Unknown")
        else:
            current_status_id = asset.get("status_id")
            current_status_name = str(status_label)
        
        # Check if actually pending
        if current_status_id != pending_status_id:
            logger.info(f"Asset {serial} not in Pending status (current: {current_status_name}), skipping")
            results["skipped"] += 1
            continue
        
        # Get assigned user info for logging
        assigned_to = asset.get("assigned_to", {})
        assigned_user = assigned_to.get("name", "Unassigned") if assigned_to else "Unassigned"
        
        if args.dry_run:
            logger.info(f"[DRY-RUN] Would revert {asset_name} ({serial}): Pending → Deployed (user: {assigned_user})")
            results["reverted"] += 1
        else:
            # Update status back to Deployed
            success = snipe.update_asset_status(asset_id, deployed_status_id)
            if success:
                logger.info(f"✅ Reverted {asset_name} ({serial}): Pending → Deployed (user: {assigned_user})")
                results["reverted"] += 1
            else:
                logger.error(f"❌ Failed to revert {asset_name} ({serial})")
                results["errors"] += 1
    
    # Print summary
    print("\n" + "=" * 60)
    print("REVERT COMPLETE" + (" (DRY RUN)" if args.dry_run else ""))
    print("=" * 60)
    print(f"Total processed:   {results['total']}")
    print(f"Reverted:          {results['reverted']}")
    print(f"Skipped:           {results['skipped']}")
    print(f"Not found:         {results['not_found']}")
    print(f"Errors:            {results['errors']}")
    print("=" * 60)
    
    snipe.close()


if __name__ == "__main__":
    main()
