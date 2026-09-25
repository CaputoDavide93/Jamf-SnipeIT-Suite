#!/usr/bin/env python3
"""
Jamf-SnipeIT Suite - Docker Scheduler
Main entry point for Docker container with:
- Pre-flight API connectivity check
- Per-module execution metrics and Slack summaries
- Module execution metrics & Slack notifications
- Scheduler with on-demand "NOW" menu
- Config hot-reload (watches file mtime)
"""
import argparse
import sys
import os
import logging
import signal
import threading
import time
import select
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Dict, List

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

from core.config import get_config, reload_config, Config
from core.run_context import RunContext
from core.state import SyncState
from clients.slack import SlackClient
from infra.health import start_health_server, get_health_server
from infra.helpers import result_error_count
from modules.sync import (
    UserMatchModule,
    SnipeToJamfModule,
    ModelSyncModule,
    CorrectionModule,
    PeripheralsSyncModule,
)
from modules.lifecycle import (
    AzureStartersModule,
    LeaversModule,
    RehireDetectionModule,
    UserEnrichmentModule,
)
from modules.maintenance import (
    ReconciliationModule,
    CleanupModule,
    UsernameStandardizer,
)
from modules.maintenance.ai_audit import AIAuditModule
from modules.maintenance.health_check import HealthCheckModule


# Global state
scheduler: Optional[Any] = None
config: Optional[Config] = None
slack: Optional[SlackClient] = None
sync_state: Optional[SyncState] = None
running = True
_config_mtime: float = 0.0  # for hot-reload
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
    print("\n  Jamf-SnipeIT Suite — Docker Mode")
    print("  Type NOW for on-demand menu | Ctrl+C to stop\n")


def pre_flight_check() -> bool:
    """
    Verify API connectivity before running modules.
    Returns True if all critical APIs are reachable.
    """
    from clients.snipeit import SnipeITClient
    from clients.jamf import JamfClient
    from clients.azure import AzureClient

    logger.info("Pre-flight API check...")
    all_ok = True

    def _check_snipe():
        c = SnipeITClient(
            base_url=config.snipeit.base_url,
            api_token=config.snipeit.api_token,
            timeout=config.api.timeout_seconds,
            max_retries=2,
            retry_delay=2,
        )
        try:
            return c.ping()
        finally:
            c.close()

    def _check_jamf():
        c = JamfClient(
            base_url=config.jamf.base_url,
            username=config.jamf.username,
            password=config.jamf.password,
            client_id=config.jamf.client_id,
            client_secret=config.jamf.client_secret,
            timeout=config.api.timeout_seconds,
            max_retries=2,
            retry_delay=2,
        )
        try:
            return c.ping()
        finally:
            c.close()

    def _check_azure():
        c = AzureClient(
            tenant_id=config.azure.tenant_id,
            client_id=config.azure.client_id,
            client_secret=config.azure.client_secret,
            timeout=config.api.timeout_seconds,
            max_retries=2,
            retry_delay=2,
        )
        try:
            return c.ping()
        finally:
            c.close()

    checks = [
        ("Snipe-IT", _check_snipe),
        ("Jamf Pro", _check_jamf),
        ("Azure AD", _check_azure),
    ]

    for name, check_fn in checks:
        try:
            ok = check_fn()
            if ok:
                logger.info(f"  {name}: OK")
            else:
                logger.warning(f"  {name}: unexpected response")
                all_ok = False
        except Exception as e:
            logger.error(f"  {name}: unreachable — {e}")
            all_ok = False
            if slack:
                slack.notify_error(name, str(e))

    if all_ok:
        logger.info("All APIs reachable")
    else:
        logger.error("Pre-flight failed — module execution blocked")

    return all_ok


def check_config_reload():
    """Hot-reload config if the file has been modified."""
    global config, _config_mtime
    try:
        cfg_path = Path(config._config_path) if hasattr(config, '_config_path') else Path('/app/config/config.yaml')
        if cfg_path.exists():
            mtime = cfg_path.stat().st_mtime
            if mtime > _config_mtime and _config_mtime > 0:
                logger.info("Config changed — reloading")
                new_cfg = reload_config()
                if new_cfg:
                    config = new_cfg
                    logger.info("Config reloaded")
                else:
                    logger.warning("Config reload returned None, keeping old")
            _config_mtime = mtime
    except Exception as e:
        logger.debug(f"Config reload check failed: {e}")


def run_module_safe(name: str, runner_fn, dry_run: bool = False,
                    ctx: Optional[RunContext] = None,
                    module_key: Optional[str] = None) -> Dict:
    """
    Run a module safely, catching exceptions.
    Optionally tracks metrics via RunContext.

    Returns:
        Dict with 'success', 'error', and 'results' keys
    """
    module_key = module_key or {
        "Azure Starters": "azure_starters",
        "User Enrichment": "user_enrichment",
        "Rehire Detection": "rehire_detection",
        "Model Sync": "model_sync",
        "Correction": "correction",
        "User Match": "user_match",
        "Snipe-to-Jamf": "snipe_to_jamf",
        "Leavers": "leavers",
        "Peripherals Sync": "peripherals_sync",
        "Cleanup": "cleanup",
        "Username Standardize": "username_standardize",
        "AI Audit": "ai_audit",
        "Health Check": "health_check",
        "Reconciliation": "reconciliation",
    }.get(name)
    if module_key:
        settings = config.get_module_settings(module_key)
        if not settings.enabled:
            logger.info("--- %s skipped: disabled in config ---", name)
            return {
                'success': True,
                'error': None,
                'results': {'skipped': True, 'reason': 'disabled'},
            }
        dry_run = dry_run or settings.dry_run

    logger.info(f"--- {name} started ---")

    if ctx:
        ctx.start_module(name)

    health = get_health_server()

    try:
        results = runner_fn(dry_run=dry_run)
        error_count = result_error_count(results)
        succeeded = error_count == 0
        logger.info(
            "--- %s %s%s ---",
            name,
            "completed" if succeeded else "completed with errors",
            f" ({error_count})" if error_count else "",
        )
        if ctx:
            ctx.stop_module(name, results=results if isinstance(results, dict) else None)
        if sync_state and succeeded:
            sync_state.set_last_run(name)
        if health:
            health.record_run(success=succeeded, module_name=name)
        if not succeeded and slack:
            slack.notify_error(name, f"Module returned {error_count} error(s)")
        return {
            'success': succeeded,
            'error': None if succeeded else f"module_errors:{error_count}",
            'results': results,
        }
    except Exception as e:
        logger.error(f"--- {name} FAILED: {e} ---")
        logger.exception("Traceback:")
        if ctx:
            ctx.record_error(name, e)
            ctx.stop_module(name)
        if health:
            health.record_run(success=False, module_name=name)
        if slack:
            slack.notify_error(name, str(e))
        return {'success': False, 'error': str(e), 'results': None}


def run_scheduled(name: str, runner_fn, dry_run: bool = False) -> Dict:
    """Wrapper for cron-triggered jobs: serialize via RunMutex.

    Individual scheduled jobs previously ran without the mutex, so a slow
    module could overlap the next cron slot (or the startup full run) and
    they would revert each other's checkin/checkout work. Skipped runs are
    reported rather than queued — the next cron firing picks the work up.
    """
    from infra.mutex import RunMutex
    mutex = RunMutex()
    if not mutex.acquire():
        logger.warning(f"--- {name} SKIPPED: another run holds the mutex ---")
        health = get_health_server()
        if health:
            health.record_run(success=False, module_name=name)
        raise RuntimeError(f"{name} skipped: mutex unavailable or already held")
    try:
        outcome = run_module_safe(name, runner_fn, dry_run=dry_run)
        if not outcome.get("success", False):
            raise RuntimeError(f"{name} failed: {outcome.get('error') or 'unknown error'}")
        return outcome
    finally:
        mutex.release()


def run_leavers(dry_run: bool = False) -> Dict:
    """Run Leavers module."""
    module = LeaversModule(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()


def run_rehire_detection(dry_run: bool = False) -> Dict:
    """Run Rehire Detection module — restore [Disabled] users active again in AAD."""
    module = RehireDetectionModule(config)
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


def run_correction(dry_run: bool = False) -> Dict:
    """Run Self-Healing Correction module."""
    module = CorrectionModule(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()


def run_azure_starters(dry_run: bool = False) -> Dict:
    """Run Azure Starters module — creates new Snipe-IT users from Azure AD."""
    module = AzureStartersModule(config, dry_run=dry_run)
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


def run_cleanup(dry_run: bool = False) -> Dict:
    """Run Cleanup module — detect and merge duplicate users."""
    module = CleanupModule(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()


def run_peripherals_sync(dry_run: bool = False) -> Dict:
    """Run Peripherals Sync — HiBob equipment → Snipe-IT accessories."""
    module = PeripheralsSyncModule(config, dry_run=dry_run)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()


def run_username_standardize(dry_run: bool = False) -> Dict:
    """Run Username Standardization — strip @domain from usernames."""
    module = UsernameStandardizer(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()


def run_user_enrichment(dry_run: bool = False) -> Dict:
    """Run User Enrichment — push Azure AD fields to Snipe-IT user records."""
    module = UserEnrichmentModule(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()


def run_ai_audit(dry_run: bool = False) -> Dict:
    """Run AI Cross-Platform Audit."""
    module = AIAuditModule(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()


def run_health_check(dry_run: bool = False) -> Dict:
    """Run Health Check."""
    module = HealthCheckModule(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()


def run_all_modules_startup(dry_run: bool = False):
    """
    Run all modules on startup with shared execution metrics.
    Continue even if one fails, report results at end.
    """
    mode = "DRY RUN" if dry_run else "LIVE"
    logger.info(f"=== STARTUP RUN ({mode}) ===")

    # Mutex: prevent concurrent full-sync runs
    from infra.mutex import RunMutex
    mutex = RunMutex()
    if not mutex.acquire():
        logger.error("Another run already in progress — aborting")
        return {"aborted": True, "reason": "mutex_held"}

    try:
        if not pre_flight_check():
            return {"aborted": True, "reason": "preflight_failed"}

        ctx = RunContext()

        # Rehire Detection runs right after provisioning and BEFORE the
        # sync chain: re-hired users must be un-ghosted before Correction,
        # User Match, and Leavers evaluate them.
        modules = [
            ("Azure Starters", run_azure_starters),
            ("User Enrichment", run_user_enrichment),
            ("Rehire Detection", run_rehire_detection),
            ("Model Sync", run_model_sync),
            ("Correction", run_correction),
            ("User Match", run_user_match),
            ("Snipe-to-Jamf", run_snipe_to_jamf),
            ("Leavers", run_leavers),
            ("Peripherals Sync", run_peripherals_sync),
        ]

        results = {}
        for name, runner in modules:
            results[name] = run_module_safe(name, runner, dry_run=dry_run, ctx=ctx)
            time.sleep(2)  # Small delay between modules

        # Summary
        success_count = sum(1 for r in results.values() if r['success'])
        fail_count = len(results) - success_count

        logger.info("=== RUN SUMMARY: %d succeeded, %d failed ===", success_count, fail_count)

        for name, result in results.items():
            status = "OK" if result['success'] else "FAILED"
            logger.info(f"  {name}: {status}")

        summary = ctx.summary()
        if summary and summary.get("modules"):
            for mod_name, mod_data in summary["modules"].items():
                dur = mod_data.get("duration_s", 0)
                processed = mod_data.get("processed", 0)
                if dur > 0 or processed > 0:
                    logger.info(f"  {mod_name}: {dur:.0f}s, {processed} items")

        # Slack run summary
        if slack and config.slack.notify_module_summary:
            slack.notify_run_summary(summary)

        return results
    finally:
        # Always release — an exception in summary/Slack must not leave the
        # lock held for the full TTL.
        mutex.release()


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
        logger.info("No jobs scheduled")
        return

    logger.info("Next scheduled runs:")
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

        logger.info(f"  {job['name']}: {job['next_run_str']} ({time_str})")


def on_demand_menu():
    """Show interactive menu for on-demand execution."""
    print("\n" + "="*60)
    print("  ON-DEMAND EXECUTION MENU")
    print("="*60)
    print("\n  Select a module to run:")
    print("  1.  Leavers - Mark assets of disabled Azure users")
    print("  2.  Snipe-to-Jamf - Sync user info from Snipe-IT to Jamf")
    print("  3.  User Match - Match Jamf computers to Snipe-IT users")
    print("  4.  Model Sync - Sync hardware models between platforms")
    print("  5.  Reconciliation - Find inventory discrepancies")
    print("  6.  Run ALL modules")
    print("  7.  Run ALL (DRY RUN - no changes)")
    print("  8.  Self-Healing Correction - Detect & fix wrong assignments")
    print("  9.  Azure Starters - Create new Snipe-IT users from Azure AD")
    print("  10. Cleanup - Merge duplicate users & remove junk accounts")
    print("  11. Cleanup (DRY RUN)")
    print("  12. Peripherals Sync - HiBob equipment → Snipe-IT accessories")
    print("  13. Username Standardize - Strip @domain from all usernames")
    print("  14. Username Standardize (DRY RUN)")
    print("  15. User Enrichment - Push Azure AD fields to Snipe-IT users")
    print("  16. User Enrichment (DRY RUN)")
    print("  17. AI Cross-Platform Audit")
    print("  18. AI Cross-Platform Audit (DRY RUN)")
    print("  19. Rehire Detection - Restore [Disabled] users active again in AAD")
    print("  20. Rehire Detection (DRY RUN)")
    print("  0.  Cancel - Return to scheduler")
    print("="*60)

    try:
        choice = input("\n  Enter your choice (0-20): ").strip()
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
        run_module_safe("Reconciliation", run_reconciliation, dry_run, module_key="reconciliation")
    elif choice == '6':
        run_all_modules_startup()
    elif choice == '7':
        print("\n  🧪 DRY RUN MODE - No changes will be made\n")
        run_all_modules_startup(dry_run=True)
    elif choice == '8':
        run_module_safe("Self-Healing Correction", run_correction, dry_run, module_key="correction")
    elif choice == '9':
        run_module_safe("Azure Starters", run_azure_starters, dry_run)
    elif choice == '10':
        run_module_safe("Cleanup", run_cleanup, dry_run)
    elif choice == '11':
        run_module_safe("Cleanup (DRY RUN)", run_cleanup, dry_run=True, module_key="cleanup")
    elif choice == '12':
        run_module_safe("Peripherals Sync", run_peripherals_sync, dry_run)
    elif choice == '13':
        run_module_safe("Username Standardize", run_username_standardize, dry_run)
    elif choice == '14':
        run_module_safe("Username Standardize (DRY)", run_username_standardize, dry_run=True, module_key="username_standardize")
    elif choice == '15':
        run_module_safe("User Enrichment", run_user_enrichment, dry_run)
    elif choice == '16':
        run_module_safe("User Enrichment (DRY)", run_user_enrichment, dry_run=True, module_key="user_enrichment")
    elif choice == '17':
        run_module_safe("AI Audit", run_ai_audit, dry_run)
    elif choice == '18':
        run_module_safe("AI Audit (DRY)", run_ai_audit, dry_run=True, module_key="ai_audit")
    elif choice == '19':
        run_module_safe("Rehire Detection", run_rehire_detection, dry_run)
    elif choice == '20':
        run_module_safe("Rehire Detection (DRY)", run_rehire_detection, dry_run=True, module_key="rehire_detection")
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
        logger.error(f"Job {event.job_id} failed: {event.exception}")
    else:
        logger.info(f"Job {event.job_id} completed successfully")
    
    # Print next run times after each job
    print_next_run_times()


def create_scheduler(cfg: Config, dry_run: bool = False) -> Any:
    """Create and configure the APScheduler."""
    global scheduler
    
    if not APSCHEDULER_AVAILABLE:
        raise ImportError("APScheduler not installed. Install with: pip install apscheduler")
    
    scheduler = BackgroundScheduler(timezone=cfg.scheduler.get('timezone', 'UTC'))
    scheduler.add_listener(job_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    
    jobs_config = cfg.scheduler.get('jobs', {})
    jitter = int(cfg.scheduler.get('jitter_seconds', 120))  # spreads starts to avoid collisions
    
    # Add Leavers job
    # Rehire Detection MUST be scheduled before Leavers (Tuesday chain)
    # so returning employees are un-tagged before Leavers re-evaluates.
    if jobs_config.get('rehire_detection', {}).get('enabled', False):
        cron = jobs_config['rehire_detection'].get('cron', '35 18 * * 2')
        scheduler.add_job(
            lambda: run_scheduled("Rehire Detection", run_rehire_detection, dry_run=dry_run),
            CronTrigger.from_crontab(cron),
            id='rehire_detection',
            name='Rehire Detection',
            jitter=jitter,
        )
        logger.info(f"  Rehire Detection: {cron}")

    if jobs_config.get('leavers', {}).get('enabled', False):
        cron = jobs_config['leavers'].get('cron', '0 9 * * 1')
        scheduler.add_job(
            lambda: run_scheduled("Leavers", run_leavers, dry_run=dry_run),
            CronTrigger.from_crontab(cron),
            id='leavers',
            name='Leavers Module',
            jitter=jitter,
        )
        logger.info(f"  Leavers: {cron}")
    
    # Add Snipe-to-Jamf job
    if jobs_config.get('snipe_to_jamf', {}).get('enabled', False):
        cron = jobs_config['snipe_to_jamf'].get('cron', '0 6 * * *')
        scheduler.add_job(
            lambda: run_scheduled("Snipe-to-Jamf", run_snipe_to_jamf, dry_run=dry_run),
            CronTrigger.from_crontab(cron),
            id='snipe_to_jamf',
            name='Snipe-to-Jamf Sync',
            jitter=jitter,
        )
        logger.info(f"  Snipe-to-Jamf: {cron}")
    
    # Add User Match job
    if jobs_config.get('user_match', {}).get('enabled', False):
        cron = jobs_config['user_match'].get('cron', '0 9 * * 2')
        scheduler.add_job(
            lambda: run_scheduled("User Match", run_user_match, dry_run=dry_run),
            CronTrigger.from_crontab(cron),
            id='user_match',
            name='User Match Module',
            jitter=jitter,
        )
        logger.info(f"  User Match: {cron}")
    
    # Add Model Sync job
    if jobs_config.get('model_sync', {}).get('enabled', False):
        cron = jobs_config['model_sync'].get('cron', '0 2 * * 0')
        scheduler.add_job(
            lambda: run_scheduled("Model Sync", run_model_sync, dry_run=dry_run),
            CronTrigger.from_crontab(cron),
            id='model_sync',
            name='Model Sync Module',
            jitter=jitter,
        )
        logger.info(f"  Model Sync: {cron}")
    
    # Add Azure Starters job
    if jobs_config.get('azure_starters', {}).get('enabled', False):
        cron = jobs_config['azure_starters'].get('cron', '0 6 * * 1')
        scheduler.add_job(
            lambda: run_scheduled("Azure Starters", run_azure_starters, dry_run=dry_run),
            CronTrigger.from_crontab(cron),
            id='azure_starters',
            name='Azure Starters Module',
            jitter=jitter,
        )
        logger.info(f"  Azure Starters: {cron}")
    
    # Add Correction job
    if jobs_config.get('correction', {}).get('enabled', False):
        cron = jobs_config['correction'].get('cron', '0 8 * * *')
        scheduler.add_job(
            lambda: run_scheduled("Correction", run_correction, dry_run=dry_run),
            CronTrigger.from_crontab(cron),
            id='correction',
            name='Self-Healing Correction',
            jitter=jitter,
        )
        logger.info(f"  Correction: {cron}")

    # Add Cleanup / Duplicate Detection job
    if jobs_config.get('cleanup', {}).get('enabled', False):
        cron = jobs_config['cleanup'].get('cron', '0 3 * * 0')
        scheduler.add_job(
            lambda: run_scheduled("Cleanup", run_cleanup, dry_run=dry_run),
            CronTrigger.from_crontab(cron),
            id='cleanup',
            name='Cleanup & Duplicate Detection',
            jitter=jitter,
        )
        logger.info(f"  Cleanup: {cron}")

    # Add User Enrichment job
    if jobs_config.get('user_enrichment', {}).get('enabled', False):
        cron = jobs_config['user_enrichment'].get('cron', '30 6 * * 1')
        scheduler.add_job(
            lambda: run_scheduled("User Enrichment", run_user_enrichment, dry_run=dry_run),
            CronTrigger.from_crontab(cron),
            id='user_enrichment',
            name='User Enrichment Module',
            jitter=jitter,
        )
        logger.info(f"  User Enrichment: {cron}")

    # Add Peripherals Sync job (HiBob → Snipe-IT accessories)
    if jobs_config.get('peripherals_sync', {}).get('enabled', False):
        cron = jobs_config['peripherals_sync'].get('cron', '0 8 * * 1')
        scheduler.add_job(
            lambda: run_scheduled("Peripherals Sync", run_peripherals_sync, dry_run=dry_run),
            CronTrigger.from_crontab(cron),
            id='peripherals_sync',
            name='Peripherals Sync (HiBob)',
            jitter=jitter,
        )
        logger.info(f"  Peripherals Sync: {cron}")

    # Add AI Audit job (weekly, after cleanup)
    if jobs_config.get('ai_audit', {}).get('enabled', False):
        cron = jobs_config['ai_audit'].get('cron', '0 4 * * 0')
        scheduler.add_job(
            lambda: run_scheduled("AI Audit", run_ai_audit, dry_run=dry_run),
            CronTrigger.from_crontab(cron),
            id='ai_audit',
            name='AI Cross-Platform Audit',
            jitter=jitter,
        )
        logger.info(f"  AI Audit: {cron}")

    # Add Health Check job (daily, after full sync)
    if jobs_config.get('health_check', {}).get('enabled', False):
        cron = jobs_config['health_check'].get('cron', '0 9 * * *')
        scheduler.add_job(
            lambda: run_scheduled("Health Check", run_health_check, dry_run=dry_run),
            CronTrigger.from_crontab(cron),
            id='health_check',
            name='Health Check',
            jitter=jitter,
        )
        logger.info(f"  Health Check: {cron}")

    return scheduler


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    global running
    logger.info("Shutdown signal received")
    running = False
    if scheduler:
        scheduler.shutdown(wait=False)
    sys.exit(0)


def main():
    global config, running, slack, sync_state, _config_mtime
    
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
        logger.info(f"Loading config: {args.config}")
        config = get_config(args.config)

        # Setup logging from config
        log_level = config.logging.level if hasattr(config, 'logging') else 'INFO'
        setup_logging(level=log_level, log_file=args.log_file)

        logger.info("Config loaded")
    except Exception as e:
        print(f"FATAL: Failed to load configuration: {e}")
        return 1

    # Track config file mtime for hot-reload
    cfg_path = Path(args.config)
    if cfg_path.exists():
        _config_mtime = cfg_path.stat().st_mtime

    # Initialise Slack client
    if hasattr(config, 'slack') and config.slack and config.slack.enabled:
        slack = SlackClient(
            bot_token=config.slack.bot_token,
            channel_id=config.slack.channel_id,
            enabled=True,
        )
        logger.info("Slack: enabled")
    else:
        logger.info("Slack: disabled")

    # Initialise persistent state helpers
    sync_state = SyncState()

    # Start health server
    try:
        health_server = start_health_server(port=8080)
        logger.info("Health server: :8080")
    except Exception as e:
        logger.warning(f"Health server failed: {e}")
    
    # Check if scheduler is enabled in config
    scheduler_cfg = config.scheduler if hasattr(config, 'scheduler') else {}
    scheduler_enabled = scheduler_cfg.get('enabled', True) if isinstance(scheduler_cfg, dict) else getattr(scheduler_cfg, 'enabled', True)
    if args.scheduler_disabled:
        scheduler_enabled = False
    
    # Check for dry-run mode
    dry_run = args.dry_run
    if dry_run:
        logger.info("DRY RUN MODE — no changes will be made")
    
    # Run all modules on startup if configured
    run_on_startup = scheduler_cfg.get('run_on_startup', True) if isinstance(scheduler_cfg, dict) else getattr(scheduler_cfg, 'run_on_startup', True)
    startup_results = None
    if run_on_startup and not args.no_startup_run:
        startup_results = run_all_modules_startup(dry_run=dry_run)
    
    # If scheduler disabled, exit after startup run
    if not scheduler_enabled:
        logger.info("Scheduler disabled — exiting")
        if isinstance(startup_results, dict):
            if startup_results.get("aborted"):
                return 1
            if any(
                isinstance(result, dict) and not result.get("success", False)
                for result in startup_results.values()
            ):
                return 1
        return 0

    # Create and start scheduler
    logger.info("Configuring scheduled jobs...")
    
    try:
        sched = create_scheduler(config, dry_run=dry_run)
        
        jobs = sched.get_jobs()
        if not jobs:
            logger.warning("No jobs scheduled — check config.yaml scheduler.jobs")

        sched.start()
        logger.info("Scheduler started")

    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")
        return 1

    # Print initial next run times
    print_next_run_times()

    # Update health server
    health = get_health_server()
    if health:
        health.update_status(scheduler_running=True)
    
    # Start input listener thread
    input_thread = threading.Thread(target=input_listener, daemon=True)
    input_thread.start()
    
    # Main loop - print status periodically and check for config changes
    last_status_print = time.time()
    status_interval = 3600  # Print status every hour
    
    try:
        while running:
            time.sleep(10)

            # Hot-reload config if file changed
            check_config_reload()

            # Periodically print next run times
            if time.time() - last_status_print > status_interval:
                print_next_run_times()
                last_status_print = time.time()
                
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Shutting down scheduler...")
        if scheduler:
            scheduler.shutdown(wait=False)
        logger.info("Shutdown complete")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
