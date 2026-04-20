#!/usr/bin/env python3
"""
Jamf-SnipeIT Suite - Main CLI Entry Point
Unified tool for asset management between Jamf Pro, Snipe-IT, and Azure AD.
"""
import argparse
import sys
import logging
from datetime import datetime
from typing import Optional

# Module imports
from core.config import get_config, Config
from infra.health import start_health_server, get_health_server
from modules import (
    LeaversModule, 
    SnipeToJamfModule, 
    UserMatchModule, 
    ModelSyncModule, 
    WakeUpModule,
    ReconciliationModule,
    AzureStartersModule,
    CorrectionModule,
)


def setup_logging(verbose: bool = False, log_file: Optional[str] = None) -> logging.Logger:
    """Configure logging for the application."""
    level = logging.DEBUG if verbose else logging.INFO
    
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=handlers
    )
    return logging.getLogger('jamf-snipeit-suite')


def print_banner():
    """Print application banner."""
    banner = """
╔═══════════════════════════════════════════════════════════╗
║                   Jamf-SnipeIT Suite                      ║
║     Unified Asset Management & Synchronization Tool       ║
╚═══════════════════════════════════════════════════════════╝
    """
    print(banner)


def cmd_leavers(args, config: Config):
    """Run the Leavers module - mark assets of disabled Azure users."""
    print("\n🔄 Running Leavers Module...")
    print("   Checking Azure AD for disabled users and updating Snipe-IT assets.\n")
    
    module = LeaversModule(config)
    results = module.run(dry_run=args.dry_run)
    
    # errors can be a list or int depending on module
    errors = results.get('errors', 0)
    error_count = len(errors) if isinstance(errors, list) else errors
    
    # Return both exit code and results for summary
    return (0 if error_count == 0 else 1, results)


def cmd_snipe_to_jamf(args, config: Config):
    """Run Snipe-IT to Jamf sync - update Jamf location from Snipe-IT."""
    print("\n🔄 Running Snipe-IT to Jamf Sync Module...")
    print("   Syncing user information from Snipe-IT assets to Jamf Pro.\n")
    
    module = SnipeToJamfModule(config)
    results = module.run(dry_run=args.dry_run)
    
    # errors can be a list or int depending on module
    errors = results.get('errors', 0)
    error_count = len(errors) if isinstance(errors, list) else errors
    
    # Return both exit code and results for summary
    return (0 if error_count == 0 else 1, results)


def cmd_user_match(args, config: Config):
    """Run User Match module - match Jamf computers to Snipe-IT users."""
    print("\n🔄 Running User Match Module...")
    print("   Matching and provisioning Jamf computers in Snipe-IT.\n")
    
    module = UserMatchModule(config)
    results = module.run(dry_run=args.dry_run)
    
    # errors can be a list or int depending on module
    errors = results.get('errors', 0)
    error_count = len(errors) if isinstance(errors, list) else errors
    
    # Return both exit code and results for summary
    return (0 if error_count == 0 else 1, results)


def cmd_model_sync(args, config: Config):
    """Run Model Sync module - sync hardware models between platforms."""
    print("\n🔄 Running Model Sync Module...")
    print("   Syncing hardware models between Jamf Pro and Snipe-IT.\n")
    
    module = ModelSyncModule(config)
    
    if getattr(args, 'check_only', False):
        results = module.check_models()
        print(f"\n📊 Model Check Results:")
        print(f"   Total models in Jamf: {results['total_jamf_models']}")
        print(f"   Missing in Snipe-IT: {len(results['missing_models'])}")
        if results['missing_models']:
            print("   Missing models:")
            for model in results['missing_models']:
                print(f"     - {model}")
        return (0, results)
    
    results = module.run(dry_run=args.dry_run)
    
    # Return both exit code and results for summary
    return (0 if results['errors'] == 0 else 1, results)


def cmd_wakeup(args, config: Config):
    """Run WakeUp module - send MDM redeploy commands."""
    print("\n🔄 Running WakeUp Module...")
    print("   Sending MDM redeploy commands to devices.\n")
    
    module = WakeUpModule(config)
    
    if args.group:
        results = module.wake_group(args.group, dry_run=args.dry_run)
    elif args.serial:
        results = module.wake_serial(args.serial, dry_run=args.dry_run)
    elif args.file:
        results = module.wake_from_file(args.file, dry_run=args.dry_run)
    else:
        print("❌ Error: Must specify --group, --serial, or --file")
        return 1
    
    
    return 0 if results['errors'] == 0 else 1


def cmd_reconcile(args, config: Config):
    """Run inventory reconciliation between Jamf and Snipe-IT."""
    print("\n🔍 Running Inventory Reconciliation...")
    print("   Comparing inventory between Jamf Pro and Snipe-IT.\n")
    
    module = ReconciliationModule(config, dry_run=args.dry_run)
    results = module.run(
        check_duplicates=not args.no_duplicates,
        check_mismatches=not args.no_mismatches,
        export_csv=args.export_csv,
        output_dir=args.output_dir
    )
    module.print_summary()
    
    # Return error if significant issues found
    issues = (len(results.jamf_only) + len(results.snipe_only) + 
              len(results.jamf_duplicates) + len(results.snipe_duplicates))
    return 0 if issues == 0 else 1


def cmd_azure_starters(args, config: Config):
    """Run Azure Starters module - sync Azure AD starters to Snipe-IT users."""
    print("\n👥 Running Azure Starters Module...")
    print("   Syncing Azure AD starters group members to Snipe-IT users.\n")
    
    module = AzureStartersModule(config)
    results = module.run(dry_run=args.dry_run)
    
    # errors can be a list or int depending on module
    errors = results.get('errors', [])
    error_count = len(errors) if isinstance(errors, list) else errors
    
    # Return both exit code and results for summary
    return (0 if error_count == 0 else 1, results)


def cmd_correction(args, config: Config):
    """Run Self-Healing Correction module - detect and fix wrong assignments."""
    print("\nRunning Self-Healing Correction Module...")
    print("   Validating existing Snipe-IT assignments against Jamf data.\n")

    module = CorrectionModule(config)
    results = module.run(dry_run=args.dry_run)

    errors = results.get('errors', 0)
    error_count = len(errors) if isinstance(errors, list) else errors

    return (0 if error_count == 0 else 1, results)


def cmd_health_check(args, config: Config):
    """Scan for stuck/inconsistent states."""
    from modules.maintenance.health_check import HealthCheckModule
    module = HealthCheckModule(config)
    try:
        results = module.run(dry_run=args.dry_run)
    finally:
        module.close()
    return (0, results)


def cmd_ai_audit(args, config: Config):
    """AI cross-platform audit."""
    from modules.maintenance.ai_audit import AIAuditModule
    module = AIAuditModule(config)
    try:
        results = module.run(dry_run=args.dry_run)
    finally:
        module.close()
    return (0, results)


def cmd_cleanup(args, config: Config):
    """Merge duplicate users, remove junk."""
    from modules.maintenance import CleanupModule
    module = CleanupModule(config)
    try:
        results = module.run(dry_run=args.dry_run)
    finally:
        module.close()
    return (0, results)


def cmd_user_enrichment(args, config: Config):
    """Push Azure fields to Snipe-IT."""
    from modules.lifecycle import UserEnrichmentModule
    module = UserEnrichmentModule(config)
    try:
        results = module.run(dry_run=args.dry_run)
    finally:
        module.close()
    return (0, results)


def cmd_peripherals_sync(args, config: Config):
    """Sync HiBob equipment."""
    from modules.sync import PeripheralsSyncModule
    module = PeripheralsSyncModule(config, dry_run=args.dry_run)
    try:
        results = module.run(dry_run=args.dry_run)
    finally:
        module.close()
    return (0, results)


def cmd_username_standardize(args, config: Config):
    """Strip @domain from usernames."""
    from modules.maintenance import UsernameStandardizer
    module = UsernameStandardizer(config)
    try:
        results = module.run(dry_run=args.dry_run)
    finally:
        module.close()
    return (0, results)


def cmd_run_all(args, config: Config):
    """Run all modules in sequence (except WakeUp which requires parameters)."""
    import os
    
    print("\n🔄 Running All Modules in Sequence...")
    print("   (WakeUp module skipped - requires explicit parameters)\n")
    
    modules = [
        ("Model Sync", lambda: cmd_model_sync(args, config)),
        ("Correction", lambda: cmd_correction(args, config)),
        ("User Match", lambda: cmd_user_match(args, config)),
        ("Snipe-to-Jamf", lambda: cmd_snipe_to_jamf(args, config)),
        ("Leavers", lambda: cmd_leavers(args, config)),
    ]
    
    run_results = {}
    module_data = {}
    
    for name, runner in modules:
        print(f"\n{'='*60}")
        print(f"  Starting: {name}")
        print(f"{'='*60}")
        try:
            result = runner()
            # Handle both tuple (code, data) and plain code returns
            if isinstance(result, tuple):
                code, data = result
            else:
                code, data = result, {}
            run_results[name] = code
            module_data[name] = data
        except Exception as e:
            print(f"❌ Error in {name}: {e}")
            run_results[name] = 1
            module_data[name] = {"exception": str(e)}
    
    # Print console summary
    print(f"\n{'='*60}")
    print("  Summary of All Modules")
    print(f"{'='*60}")
    for name, code in run_results.items():
        status = "✅ Success" if code == 0 else "❌ Failed"
        print(f"  {name}: {status}")
    
    # Write summary file to output directory
    summary_path = os.path.join(getattr(args, 'output_dir', './output'), 'run_summary.txt')
    os.makedirs(os.path.dirname(summary_path), exist_ok=True)
    
    _write_run_summary(summary_path, args, run_results, module_data)
    print(f"\n📝 Detailed summary written to: {summary_path}")
    
    return 0 if all(c == 0 for c in run_results.values()) else 1


def _write_run_summary(filepath: str, args, results: dict, module_data: dict):
    """Write a detailed run summary to file."""
    with open(filepath, 'w') as f:
        f.write("=" * 70 + "\n")
        f.write("         JAMF-SNIPEIT SUITE - RUN SUMMARY REPORT\n")
        f.write("=" * 70 + "\n\n")
        f.write(f"Run Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Dry Run: {'Yes' if getattr(args, 'dry_run', False) else 'No'}\n")
        f.write("\n" + "-" * 70 + "\n")
        f.write("OVERALL STATUS\n")
        f.write("-" * 70 + "\n\n")
        
        all_success = all(c == 0 for c in results.values())
        f.write(f"Overall Result: {'SUCCESS' if all_success else 'FAILED'}\n\n")
        
        for name, code in results.items():
            status = "✅ SUCCESS" if code == 0 else "❌ FAILED"
            f.write(f"  {name}: {status}\n")
        
        f.write("\n" + "=" * 70 + "\n")
        f.write("DETAILED MODULE RESULTS\n")
        f.write("=" * 70 + "\n")
        
        # Leavers Module
        if "Leavers" in module_data:
            f.write("\n" + "-" * 70 + "\n")
            f.write("LEAVERS MODULE\n")
            f.write("-" * 70 + "\n")
            data = module_data["Leavers"]
            if isinstance(data, dict):
                if "exception" in data:
                    f.write(f"  CRASHED: {data['exception']}\n")
                else:
                    f.write(f"  Total users processed:    {data.get('total_users', 0)}\n")
                    f.write(f"  Users with assets:        {data.get('matched_users', 0)}\n")
                    f.write(f"  Assets marked pending:    {data.get('updated_assets', 0)}\n")
                    f.write(f"  User names updated:       {data.get('updated_user_names', 0)}\n")
                    errors = data.get('errors', [])
                    error_count = len(errors) if isinstance(errors, list) else errors
                    f.write(f"  Errors:                   {error_count}\n")
                    if isinstance(errors, list) and errors:
                        f.write("\n  Error Details:\n")
                        for err in errors[:10]:  # Limit to first 10
                            f.write(f"    - {err}\n")
                        if len(errors) > 10:
                            f.write(f"    ... and {len(errors) - 10} more errors\n")
        
        # Snipe-to-Jamf Module
        if "Snipe-to-Jamf" in module_data:
            f.write("\n" + "-" * 70 + "\n")
            f.write("SNIPE-TO-JAMF MODULE\n")
            f.write("-" * 70 + "\n")
            data = module_data["Snipe-to-Jamf"]
            if isinstance(data, dict):
                if "exception" in data:
                    f.write(f"  CRASHED: {data['exception']}\n")
                else:
                    f.write(f"  Total processed:  {data.get('total_processed', 0)}\n")
                    f.write(f"  Updated:          {data.get('updated', 0)}\n")
                    f.write(f"  Skipped:          {data.get('skipped', 0)}\n")
                    f.write(f"  Errors:           {data.get('errors', 0)}\n")
                    details = data.get('details', [])
                    if details:
                        f.write("\n  Error Details:\n")
                        for d in details[:10]:
                            f.write(f"    - {d.get('serial', 'N/A')}: {d.get('error', 'Unknown')}\n")
                        if len(details) > 10:
                            f.write(f"    ... and {len(details) - 10} more\n")
        
        # User Match Module
        if "User Match" in module_data:
            f.write("\n" + "-" * 70 + "\n")
            f.write("USER MATCH MODULE\n")
            f.write("-" * 70 + "\n")
            data = module_data["User Match"]
            if isinstance(data, dict):
                if "exception" in data:
                    f.write(f"  CRASHED: {data['exception']}\n")
                else:
                    f.write(f"  Total devices:    {data.get('total_devices', 0)}\n")
                    f.write(f"  Assets created:   {data.get('assets_created', 0)}\n")
                    f.write(f"  Assets updated:   {data.get('assets_updated', 0)}\n")
                    f.write(f"  Checkouts:        {data.get('checkouts', 0)}\n")
                    f.write(f"  Reassignments:    {data.get('reassignments', 0)}\n")
                    f.write(f"  Skipped:          {data.get('skipped', 0)}\n")
                    f.write(f"  Errors:           {data.get('errors', 0)}\n")
        
        # Model Sync Module
        if "Model Sync" in module_data:
            f.write("\n" + "-" * 70 + "\n")
            f.write("MODEL SYNC MODULE\n")
            f.write("-" * 70 + "\n")
            data = module_data["Model Sync"]
            if isinstance(data, dict):
                if "exception" in data:
                    f.write(f"  CRASHED: {data['exception']}\n")
                else:
                    f.write(f"  Total processed:  {data.get('total_processed', 0)}\n")
                    f.write(f"  Updated:          {data.get('updated', 0)}\n")
                    f.write(f"  Skipped:          {data.get('skipped', 0)}\n")
                    f.write(f"  Errors:           {data.get('errors', 0)}\n")
        
        # Self-Healing Correction Module
        if "Correction" in module_data:
            f.write("\n" + "-" * 70 + "\n")
            f.write("SELF-HEALING CORRECTION MODULE\n")
            f.write("-" * 70 + "\n")
            data = module_data["Correction"]
            if isinstance(data, dict):
                if "exception" in data:
                    f.write(f"  CRASHED: {data['exception']}\n")
                else:
                    f.write(f"  Assets checked:        {data.get('total_assets_checked', 0)}\n")
                    f.write(f"  Correct assignments:   {data.get('correct_assignments', 0)}\n")
                    f.write(f"  Mismatches found:      {data.get('mismatches_found', 0)}\n")
                    f.write(f"  Corrections made:      {data.get('corrections_made', 0)}\n")
                    f.write(f"  No Jamf device:        {data.get('no_jamf_device', 0)}\n")
                    f.write(f"  No fresh match:        {data.get('no_fresh_match', 0)}\n")
                    f.write(f"  Errors:                {data.get('errors', 0)}\n")
        
        f.write("\n" + "=" * 70 + "\n")
        f.write("END OF REPORT\n")
        f.write("=" * 70 + "\n")


def interactive_menu(config: Config):
    """Interactive menu for selecting modules to run."""
    while True:
        print("\n" + "="*60)
        print("  Jamf-SnipeIT Suite - Interactive Menu")
        print("="*60)
        print("\n  Available Modules:")
        print("  1. Leavers - Mark assets of disabled Azure users")
        print("  2. Snipe-to-Jamf - Sync user info from Snipe-IT to Jamf")
        print("  3. User Match - Match Jamf computers to Snipe-IT users")
        print("  4. Model Sync - Sync hardware models between platforms")
        print("  5. WakeUp - Send MDM redeploy commands")
        print("  6. Reconciliation - Find inventory discrepancies")
        print("  7. Run All (except WakeUp)")
        print("  8. Run All (DRY RUN)")
        print("  9. Self-Healing Correction - Detect & fix wrong assignments")
        print("  0. Exit")
        
        choice = input("\n  Enter your choice: ").strip()
        
        # Create a mock args object
        class Args:
            dry_run = False
            check_only = False
            group = None
            serial = None
            file = None
            no_duplicates = False
            no_mismatches = False
            export_csv = False
            output_dir = "./output"
        
        args = Args()
        
        if choice == '1':
            cmd_leavers(args, config)
        elif choice == '2':
            cmd_snipe_to_jamf(args, config)
        elif choice == '3':
            cmd_user_match(args, config)
        elif choice == '4':
            check_only = input("  Check only? (y/n): ").strip().lower() == 'y'
            args.check_only = check_only
            cmd_model_sync(args, config)
        elif choice == '5':
            print("\n  WakeUp Options:")
            print("  1. Wake by Smart Group ID")
            print("  2. Wake by Serial Number")
            print("  3. Wake from File")
            sub = input("  Choice: ").strip()
            if sub == '1':
                args.group = int(input("  Enter Smart Group ID: ").strip())
            elif sub == '2':
                args.serial = input("  Enter Serial Number: ").strip()
            elif sub == '3':
                args.file = input("  Enter file path: ").strip()
            else:
                print("  Invalid choice")
                continue
            cmd_wakeup(args, config)
        elif choice == '6':
            export = input("  Export to CSV? (y/n): ").strip().lower() == 'y'
            args.export_csv = export
            cmd_reconcile(args, config)
        elif choice == '7':
            cmd_run_all(args, config)
        elif choice == '8':
            args.dry_run = True
            cmd_run_all(args, config)
        elif choice == '9':
            cmd_correction(args, config)
        elif choice == '0':
            print("\n  Goodbye! 👋\n")
            break
        else:
            print("  Invalid choice, please try again.")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Jamf-SnipeIT Suite - Unified Asset Management Tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run leavers module
  python main.py leavers

  # Dry run of snipe-to-jamf sync
  python main.py snipe-to-jamf --dry-run

  # Check models only
  python main.py model-sync --check-only

  # Wake up devices in a smart group
  python main.py wakeup --group 123

  # Run all modules
  python main.py all

  # Interactive mode
  python main.py --interactive
        """
    )
    
    parser.add_argument('--config', '-c', default='config/config.yaml',
                        help='Path to configuration file (default: config/config.yaml)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable verbose/debug logging')
    parser.add_argument('--log-file', '-l',
                        help='Path to log file (optional)')
    parser.add_argument('--interactive', '-i', action='store_true',
                        help='Run in interactive menu mode')
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Leavers command
    leavers_parser = subparsers.add_parser('leavers', 
        help='Mark assets of disabled Azure AD users as pending')
    leavers_parser.add_argument('--dry-run', '-n', action='store_true',
        help='Simulate actions without making changes')
    
    # Snipe-to-Jamf command
    snipe_jamf_parser = subparsers.add_parser('snipe-to-jamf',
        help='Sync user information from Snipe-IT to Jamf Pro')
    snipe_jamf_parser.add_argument('--dry-run', '-n', action='store_true',
        help='Simulate actions without making changes')
    
    # User Match command
    user_match_parser = subparsers.add_parser('user-match',
        help='Match Jamf computers to Snipe-IT users and provision assets')
    user_match_parser.add_argument('--dry-run', '-n', action='store_true',
        help='Simulate actions without making changes')
    
    # Model Sync command
    model_sync_parser = subparsers.add_parser('model-sync',
        help='Sync hardware models between Jamf Pro and Snipe-IT')
    model_sync_parser.add_argument('--dry-run', '-n', action='store_true',
        help='Simulate actions without making changes')
    model_sync_parser.add_argument('--check-only', action='store_true',
        help='Only check for missing models, do not create them')
    
    # WakeUp command
    wakeup_parser = subparsers.add_parser('wakeup',
        help='Send MDM redeploy commands to devices')
    wakeup_parser.add_argument('--dry-run', '-n', action='store_true',
        help='Simulate actions without making changes')
    wakeup_group = wakeup_parser.add_mutually_exclusive_group(required=True)
    wakeup_group.add_argument('--group', '-g', type=int,
        help='Smart Group ID to wake up all devices')
    wakeup_group.add_argument('--serial', '-s',
        help='Single serial number to wake up')
    wakeup_group.add_argument('--file', '-f',
        help='File containing serial numbers (one per line)')
    
    # Run All command
    all_parser = subparsers.add_parser('all',
        help='Run all modules in sequence (except WakeUp)')
    all_parser.add_argument('--dry-run', '-n', action='store_true',
        help='Simulate actions without making changes')
    
    # Reconciliation command
    reconcile_parser = subparsers.add_parser('reconcile',
        help='Reconcile inventory between Jamf Pro and Snipe-IT')
    reconcile_parser.add_argument('--dry-run', '-n', action='store_true',
        help='Simulate actions without making changes')
    reconcile_parser.add_argument('--no-duplicates', action='store_true',
        help='Skip duplicate detection')
    reconcile_parser.add_argument('--no-mismatches', action='store_true',
        help='Skip data mismatch detection')
    reconcile_parser.add_argument('--export-csv', '-e', action='store_true',
        help='Export results to CSV files')
    reconcile_parser.add_argument('--output-dir', '-o', default='./output',
        help='Output directory for CSV exports (default: ./output)')
    
    # Azure Starters command
    starters_parser = subparsers.add_parser('azure-starters',
        help='Sync Azure AD starters group members to Snipe-IT users')
    starters_parser.add_argument('--dry-run', '-n', action='store_true',
        help='Simulate actions without making changes')
    
    # Self-Healing Correction command
    correction_parser = subparsers.add_parser('correction',
        help='Detect and fix wrong asset assignments from previous runs')
    correction_parser.add_argument('--dry-run', '-n', action='store_true',
        help='Report mismatches without making changes')

    # Health check (scans stuck/inconsistent states)
    health_check_parser = subparsers.add_parser('health-check',
        help='Scan for stuck/inconsistent states and report to Slack')
    health_check_parser.add_argument('--dry-run', '-n', action='store_true',
        help='Report issues without sending Slack')

    # AI Audit (cross-platform LLM analysis)
    ai_audit_parser = subparsers.add_parser('ai-audit',
        help='AI-powered cross-platform audit (security, compliance, anomalies)')
    ai_audit_parser.add_argument('--dry-run', '-n', action='store_true',
        help='Run without posting to Slack')

    # Cleanup (merge duplicates, remove junk)
    cleanup_parser = subparsers.add_parser('cleanup',
        help='Merge duplicate users and remove junk accounts')
    cleanup_parser.add_argument('--dry-run', '-n', action='store_true')

    # User Enrichment (push Azure fields to Snipe-IT)
    enrich_parser = subparsers.add_parser('user-enrichment',
        help='Push Azure AD fields (job title, dept) to Snipe-IT')
    enrich_parser.add_argument('--dry-run', '-n', action='store_true')

    # Peripherals Sync (HiBob -> Snipe-IT accessories)
    peripherals_parser = subparsers.add_parser('peripherals-sync',
        help='Sync HiBob equipment to Snipe-IT accessories')
    peripherals_parser.add_argument('--dry-run', '-n', action='store_true')

    # Username Standardize (strip @domain from usernames)
    uname_std_parser = subparsers.add_parser('username-standardize',
        help='Strip @domain from Snipe-IT usernames')
    uname_std_parser.add_argument('--dry-run', '-n', action='store_true')

    # Health server command
    health_parser = subparsers.add_parser('health-server',
        help='Start health check HTTP server')
    health_parser.add_argument('--port', '-p', type=int, default=8080,
        help='Port to listen on (default: 8080)')
    health_parser.add_argument('--host', default='0.0.0.0',
        help='Host to bind to (default: 0.0.0.0)')
    
    args = parser.parse_args()
    
    # Setup logging
    logger = setup_logging(args.verbose, args.log_file)
    
    # Print banner
    print_banner()
    
    # Load configuration
    try:
        print(f"📁 Loading configuration from: {args.config}")
        config = get_config(args.config)
        print("✅ Configuration loaded successfully\n")
    except FileNotFoundError:
        print(f"❌ Configuration file not found: {args.config}")
        print("   Please copy config.yaml.example to config.yaml and update values.")
        return 1
    except Exception as e:
        print(f"❌ Error loading configuration: {e}")
        return 1
    
    # Interactive mode
    if args.interactive:
        interactive_menu(config)
        return 0
    
    # No command specified
    if not args.command:
        parser.print_help()
        print("\n💡 Tip: Use --interactive for menu-driven mode")
        return 0
    
    # Route to appropriate command handler
    command_handlers = {
        'leavers': cmd_leavers,
        'snipe-to-jamf': cmd_snipe_to_jamf,
        'user-match': cmd_user_match,
        'model-sync': cmd_model_sync,
        'wakeup': cmd_wakeup,
        'all': cmd_run_all,
        'reconcile': cmd_reconcile,
        'azure-starters': cmd_azure_starters,
        'correction': cmd_correction,
        'health-check': cmd_health_check,
        'ai-audit': cmd_ai_audit,
        'cleanup': cmd_cleanup,
        'user-enrichment': cmd_user_enrichment,
        'peripherals-sync': cmd_peripherals_sync,
        'username-standardize': cmd_username_standardize,
    }
    
    # Special handling for health server (long-running)
    if args.command == 'health-server':
        print(f"🏥 Starting health check server on {args.host}:{args.port}")
        server = start_health_server(port=args.port, host=args.host)
        print("   Press Ctrl+C to stop.\n")
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n\n⚠️  Health server stopped")
            return 0
    
    handler = command_handlers.get(args.command)
    if handler:
        try:
            return handler(args, config)
        except KeyboardInterrupt:
            print("\n\n⚠️  Operation cancelled by user")
            return 130
        except Exception as e:
            logger.exception(f"Error running {args.command}")
            print(f"\n❌ Error: {e}")
            return 1
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
