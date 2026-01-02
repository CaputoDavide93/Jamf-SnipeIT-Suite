#!/usr/bin/env python3
"""
Jamf-SnipeIT Suite - Docker Scheduler
Main entry point for Docker container with:
- Scheduler enabled by default
- Run all modules on startup
- "NOW" command for on-demand execution
- Next run time display in logs
"""
import argparse
import sys
import os
import logging
import signal
import threading
import time
import select
from datetime import datetime, timedelta
from typing import Optional, Dict, List

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
    APSCHEDULER_AVAILABLE = True
except ImportError:
    BackgroundScheduler = None  # type: ignore
    CronTrigger = None  # type: ignore
    EVENT_JOB_EXECUTED = None  # type: ignore
    EVENT_JOB_ERROR = None  # type: ignore
    APSCHEDULER_AVAILABLE = False

from core.config import get_config, Config
from modules import (
    LeaversModule,
    SnipeToJamfModule,
    UserMatchModule,
    ModelSyncModule,
    ReconciliationModule,
)


# Global state
scheduler: Optional[BackgroundScheduler] = None
config: Optional[Config] = None
running = True
logger = logging.getLogger('jamf-snipeit-docker')


def setup_logging(level: str = "INFO", log_file: Optional[str] = None):
    """Configure logging."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s | %(levelname)-7s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=handlers,
        force=True
    )


def print_banner():
    """Print startup banner."""
    banner = """
╔═══════════════════════════════════════════════════════════════╗
║              Jamf-SnipeIT Suite - Docker Mode                 ║
║         Automated Asset Management & Synchronization          ║
╠═══════════════════════════════════════════════════════════════╣
║  Type 'NOW' + Enter for on-demand menu                        ║
║  Press Ctrl+C to stop                                         ║
╚═══════════════════════════════════════════════════════════════╝
"""
    print(banner)


def run_module_safe(name: str, runner_fn, dry_run: bool = False) -> Dict:
    """
    Run a module safely, catching exceptions.
    
    Returns:
        Dict with 'success', 'error', and 'results' keys
    """
    logger.info(f"{'='*60}")
    logger.info(f"Starting module: {name}")
    logger.info(f"{'='*60}")
    
    try:
        results = runner_fn(dry_run=dry_run)
        logger.info(f"✅ {name} completed successfully")
        return {'success': True, 'error': None, 'results': results}
    except Exception as e:
        logger.error(f"❌ {name} failed: {e}")
        logger.exception("Full traceback:")
        return {'success': False, 'error': str(e), 'results': None}


def run_leavers(dry_run: bool = False) -> Dict:
    """Run Leavers module."""
    module = LeaversModule(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()


def run_snipe_to_jamf(dry_run: bool = False) -> Dict:
    """Run Snipe-to-Jamf module."""
    module = SnipeToJamfModule(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()


def run_user_match(dry_run: bool = False) -> Dict:
    """Run User Match module."""
    module = UserMatchModule(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()


def run_model_sync(dry_run: bool = False) -> Dict:
    """Run Model Sync module."""
    module = ModelSyncModule(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()


def run_reconciliation(dry_run: bool = False) -> Dict:
    """Run Reconciliation module."""
    module = ReconciliationModule(config, dry_run=dry_run)
    try:
        return module.run()
    finally:
        # ReconciliationModule doesn't have close(), but we add for consistency
        if hasattr(module, 'close'):
            module.close()


def run_all_modules_startup(dry_run: bool = False):
    """
    Run all modules on startup.
    Continue even if one fails, report results at end.
    """
    logger.info("")
    logger.info("🚀 " + "="*58)
    if dry_run:
        logger.info("   STARTUP: Running all modules (DRY RUN)")
    else:
        logger.info("   STARTUP: Running all modules sequentially")
    logger.info("🚀 " + "="*58)
    logger.info("")
    
    modules = [
        ("Leavers", run_leavers),
        ("Snipe-to-Jamf", run_snipe_to_jamf),
        ("User Match", run_user_match),
        ("Model Sync", run_model_sync),
    ]
    
    results = {}
    for name, runner in modules:
        results[name] = run_module_safe(name, runner, dry_run=dry_run)
        time.sleep(2)  # Small delay between modules
    
    # Print summary
    logger.info("")
    logger.info("📊 " + "="*58)
    logger.info("   STARTUP COMPLETE - SUMMARY")
    logger.info("📊 " + "="*58)
    
    success_count = 0
    fail_count = 0
    
    for name, result in results.items():
        if result['success']:
            logger.info(f"   ✅ {name}: SUCCESS")
            success_count += 1
        else:
            logger.error(f"   ❌ {name}: FAILED - {result['error']}")
            fail_count += 1
    
    logger.info("")
    logger.info(f"   Total: {success_count} succeeded, {fail_count} failed")
    logger.info("📊 " + "="*58)
    logger.info("")
    
    return results


def get_next_run_times() -> List[Dict]:
    """Get next run times for all scheduled jobs."""
    if not scheduler:
        return []
    
    jobs_info = []
    for job in scheduler.get_jobs():
        next_run = job.next_run_time
        if next_run:
            jobs_info.append({
                'name': job.name,
                'id': job.id,
                'next_run': next_run,
                'next_run_str': next_run.strftime('%Y-%m-%d %H:%M:%S %Z'),
            })
    
    # Sort by next run time
    jobs_info.sort(key=lambda x: x['next_run'])
    return jobs_info


def print_next_run_times():
    """Print next scheduled run times."""
    jobs = get_next_run_times()
    
    if not jobs:
        logger.info("📅 No jobs scheduled")
        return
    
    logger.info("")
    logger.info("📅 Next scheduled runs:")
    for job in jobs:
        time_until = job['next_run'] - datetime.now(job['next_run'].tzinfo)
        hours, remainder = divmod(int(time_until.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        
        if hours > 24:
            days = hours // 24
            hours = hours % 24
            time_str = f"in {days}d {hours}h {minutes}m"
        elif hours > 0:
            time_str = f"in {hours}h {minutes}m"
        else:
            time_str = f"in {minutes}m"
        
        logger.info(f"   • {job['name']}: {job['next_run_str']} ({time_str})")
    logger.info("")


def on_demand_menu():
    """Show interactive menu for on-demand execution."""
    print("\n" + "="*60)
    print("  ON-DEMAND EXECUTION MENU")
    print("="*60)
    print("\n  Select a module to run:")
    print("  1. Leavers - Mark assets of disabled Azure users")
    print("  2. Snipe-to-Jamf - Sync user info from Snipe-IT to Jamf")
    print("  3. User Match - Match Jamf computers to Snipe-IT users")
    print("  4. Model Sync - Sync hardware models between platforms")
    print("  5. Reconciliation - Find inventory discrepancies")
    print("  6. Run ALL modules")
    print("  7. Run ALL (DRY RUN - no changes)")
    print("  0. Cancel - Return to scheduler")
    print("="*60)
    
    try:
        choice = input("\n  Enter your choice (0-7): ").strip()
    except EOFError:
        return
    
    if choice == '0' or not choice:
        print("  Cancelled. Returning to scheduler.\n")
        return
    
    dry_run = False
    
    if choice == '1':
        run_module_safe("Leavers", run_leavers, dry_run)
    elif choice == '2':
        run_module_safe("Snipe-to-Jamf", run_snipe_to_jamf, dry_run)
    elif choice == '3':
        run_module_safe("User Match", run_user_match, dry_run)
    elif choice == '4':
        run_module_safe("Model Sync", run_model_sync, dry_run)
    elif choice == '5':
        run_module_safe("Reconciliation", run_reconciliation, dry_run)
    elif choice == '6':
        run_all_modules_startup()
    elif choice == '7':
        print("\n  🧪 DRY RUN MODE - No changes will be made\n")
        modules = [
            ("Leavers", run_leavers),
            ("Snipe-to-Jamf", run_snipe_to_jamf),
            ("User Match", run_user_match),
            ("Model Sync", run_model_sync),
        ]
        for name, runner in modules:
            run_module_safe(name, runner, dry_run=True)
    else:
        print("  Invalid choice.")
    
    print("\n  On-demand execution complete. Returning to scheduler.\n")
    print_next_run_times()


def input_listener():
    """
    Listen for 'NOW' command from stdin.
    Runs in a separate thread.
    """
    global running
    
    while running:
        try:
            # Check if stdin has data (non-blocking on Unix)
            if sys.stdin in select.select([sys.stdin], [], [], 1.0)[0]:
                line = sys.stdin.readline().strip().upper()
                if line == 'NOW':
                    on_demand_menu()
                elif line == 'STATUS':
                    print_next_run_times()
                elif line == 'HELP':
                    print("\n  Available commands:")
                    print("    NOW    - Open on-demand execution menu")
                    print("    STATUS - Show next scheduled run times")
                    print("    HELP   - Show this help message")
                    print("")
        except Exception:
            # In non-interactive mode, just sleep
            time.sleep(1)


def job_listener(event):
    """APScheduler event listener for job completion."""
    if event.exception:
        logger.error(f"Job {event.job_id} failed")
    else:
        logger.info(f"Job {event.job_id} completed successfully")
    
    # Print next run times after each job
    print_next_run_times()


def create_scheduler(cfg: Config) -> BackgroundScheduler:
    """Create and configure the APScheduler."""
    global scheduler
    
    if not APSCHEDULER_AVAILABLE:
        raise ImportError("APScheduler not installed. Install with: pip install apscheduler")
    
    scheduler = BackgroundScheduler(timezone=cfg.scheduler.get('timezone', 'UTC'))
    scheduler.add_listener(job_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    
    jobs_config = cfg.scheduler.get('jobs', {})
    
    # Add Leavers job
    if jobs_config.get('leavers', {}).get('enabled', False):
        cron = jobs_config['leavers'].get('cron', '0 9 * * 1')
        scheduler.add_job(
            lambda: run_module_safe("Leavers", run_leavers),
            CronTrigger.from_crontab(cron),
            id='leavers',
            name='Leavers Module'
        )
        logger.info(f"  ✓ Leavers scheduled: {cron}")
    
    # Add Snipe-to-Jamf job
    if jobs_config.get('snipe_to_jamf', {}).get('enabled', False):
        cron = jobs_config['snipe_to_jamf'].get('cron', '0 6 * * *')
        scheduler.add_job(
            lambda: run_module_safe("Snipe-to-Jamf", run_snipe_to_jamf),
            CronTrigger.from_crontab(cron),
            id='snipe_to_jamf',
            name='Snipe-to-Jamf Sync'
        )
        logger.info(f"  ✓ Snipe-to-Jamf scheduled: {cron}")
    
    # Add User Match job
    if jobs_config.get('user_match', {}).get('enabled', False):
        cron = jobs_config['user_match'].get('cron', '0 9 * * 2')
        scheduler.add_job(
            lambda: run_module_safe("User Match", run_user_match),
            CronTrigger.from_crontab(cron),
            id='user_match',
            name='User Match Module'
        )
        logger.info(f"  ✓ User Match scheduled: {cron}")
    
    # Add Model Sync job
    if jobs_config.get('model_sync', {}).get('enabled', False):
        cron = jobs_config['model_sync'].get('cron', '0 2 * * 0')
        scheduler.add_job(
            lambda: run_module_safe("Model Sync", run_model_sync),
            CronTrigger.from_crontab(cron),
            id='model_sync',
            name='Model Sync Module'
        )
        logger.info(f"  ✓ Model Sync scheduled: {cron}")
    
    return scheduler


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    global running
    logger.info("\n⚠️  Shutdown signal received...")
    running = False
    if scheduler:
        scheduler.shutdown(wait=False)
    sys.exit(0)


def main():
    global config, running
    
    parser = argparse.ArgumentParser(description='Jamf-SnipeIT Suite - Docker Scheduler')
    parser.add_argument('--config', '-c', default='/app/config/config.yaml',
                        help='Path to configuration file')
    parser.add_argument('--log-file', '-l', default='/app/logs/scheduler.log',
                        help='Path to log file')
    parser.add_argument('--no-startup-run', action='store_true',
                        help='Skip running all modules on startup')
    parser.add_argument('--scheduler-disabled', action='store_true',
                        help='Disable scheduler (just run startup and exit)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Run in dry-run mode (no changes made)')
    args = parser.parse_args()
    
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Print banner
    print_banner()
    
    # Load config
    try:
        logger.info(f"📁 Loading configuration from: {args.config}")
        config = get_config(args.config)
        
        # Setup logging from config
        log_level = config.logging.level if hasattr(config, 'logging') else 'INFO'
        setup_logging(level=log_level, log_file=args.log_file)
        
        logger.info("✅ Configuration loaded successfully")
    except Exception as e:
        print(f"❌ Failed to load configuration: {e}")
        return 1
    
    # Check if scheduler is enabled in config
    scheduler_cfg = config.scheduler if hasattr(config, 'scheduler') else {}
    scheduler_enabled = scheduler_cfg.get('enabled', True) if isinstance(scheduler_cfg, dict) else getattr(scheduler_cfg, 'enabled', True)
    if args.scheduler_disabled:
        scheduler_enabled = False
    
    # Check for dry-run mode
    dry_run = args.dry_run
    if dry_run:
        logger.info("🧪 DRY RUN MODE - No changes will be made to any systems")
    
    # Run all modules on startup if configured
    run_on_startup = scheduler_cfg.get('run_on_startup', True) if isinstance(scheduler_cfg, dict) else getattr(scheduler_cfg, 'run_on_startup', True)
    if run_on_startup and not args.no_startup_run:
        run_all_modules_startup(dry_run=dry_run)
    
    # If scheduler disabled, exit after startup run
    if not scheduler_enabled:
        logger.info("📅 Scheduler is disabled in configuration. Exiting.")
        return 0
    
    # Create and start scheduler
    logger.info("")
    logger.info("📅 Configuring scheduled jobs...")
    
    try:
        sched = create_scheduler(config)
        
        jobs = sched.get_jobs()
        if not jobs:
            logger.warning("⚠️  No jobs scheduled. Check config.yaml scheduler.jobs section.")
            logger.info("   Scheduler will run but no automated tasks configured.")
        
        sched.start()
        logger.info("")
        logger.info("🚀 Scheduler started successfully!")
        
    except Exception as e:
        logger.error(f"❌ Failed to start scheduler: {e}")
        return 1
    
    # Print initial next run times
    print_next_run_times()
    
    logger.info("💡 Type 'NOW' + Enter for on-demand execution menu")
    logger.info("💡 Type 'STATUS' + Enter to see next run times")
    logger.info("💡 Press Ctrl+C to stop")
    logger.info("")
    
    # Start input listener thread
    input_thread = threading.Thread(target=input_listener, daemon=True)
    input_thread.start()
    
    # Main loop - print status periodically
    last_status_print = time.time()
    status_interval = 3600  # Print status every hour
    
    try:
        while running:
            time.sleep(10)
            
            # Periodically print next run times
            if time.time() - last_status_print > status_interval:
                print_next_run_times()
                last_status_print = time.time()
                
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("\n⚠️  Shutting down scheduler...")
        if scheduler:
            scheduler.shutdown(wait=False)
        logger.info("👋 Goodbye!")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
