#!/usr/bin/env python3
"""
Jamf-SnipeIT Suite - Scheduler
APScheduler-based task scheduler for automated module execution.
"""
import argparse
import sys
import logging
from datetime import datetime
from typing import Optional

try:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    APSCHEDULER_AVAILABLE = True
except ImportError:
    BlockingScheduler = None  # type: ignore
    CronTrigger = None  # type: ignore
    APSCHEDULER_AVAILABLE = False

from core.config import get_config, Config
from modules import (
    LeaversModule, 
    SnipeToJamfModule, 
    UserMatchModule, 
    ModelSyncModule
)


def setup_logging(verbose: bool = False, log_file: Optional[str] = None) -> logging.Logger:
    """Configure logging for the scheduler."""
    level = logging.DEBUG if verbose else logging.INFO
    
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - [SCHEDULER] %(message)s',
        handlers=handlers
    )
    return logging.getLogger('jamf-snipeit-scheduler')


class ScheduledTaskRunner:
    """Runs scheduled tasks with the loaded configuration."""
    
    def __init__(self, config: Config, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run
        self.logger = logging.getLogger('jamf-snipeit-scheduler')
    
    def run_leavers(self):
        """Scheduled task: Run Leavers module."""
        self.logger.info("="*60)
        self.logger.info("Starting scheduled task: Leavers")
        self.logger.info("="*60)
        try:
            module = LeaversModule(self.config)
            results = module.run(dry_run=self.dry_run)
            self.logger.info(f"Leavers completed: {results.get('total_users', 0)} processed, "
                           f"{results.get('updated_assets', 0)} updated, {len(results.get('errors', []))} errors")
            module.close()
        except Exception as e:
            self.logger.exception(f"Error running Leavers: {e}")
    
    def run_snipe_to_jamf(self):
        """Scheduled task: Run Snipe-to-Jamf sync."""
        self.logger.info("="*60)
        self.logger.info("Starting scheduled task: Snipe-to-Jamf")
        self.logger.info("="*60)
        try:
            module = SnipeToJamfModule(self.config)
            results = module.run(dry_run=self.dry_run)
            self.logger.info(f"Snipe-to-Jamf completed: {results.get('total_processed', 0)} processed, "
                           f"{results.get('updated', 0)} updated, {results.get('errors', 0)} errors")
            module.close()
        except Exception as e:
            self.logger.exception(f"Error running Snipe-to-Jamf: {e}")
    
    def run_user_match(self):
        """Scheduled task: Run User Match module."""
        self.logger.info("="*60)
        self.logger.info("Starting scheduled task: User Match")
        self.logger.info("="*60)
        try:
            module = UserMatchModule(self.config)
            results = module.run(dry_run=self.dry_run)
            self.logger.info(f"User Match completed: {results.get('total_devices', 0)} processed, "
                           f"{results.get('assets_created', 0)} provisioned, {results.get('errors', 0)} errors")
            module.close()
        except Exception as e:
            self.logger.exception(f"Error running User Match: {e}")
    
    def run_model_sync(self):
        """Scheduled task: Run Model Sync module."""
        self.logger.info("="*60)
        self.logger.info("Starting scheduled task: Model Sync")
        self.logger.info("="*60)
        try:
            module = ModelSyncModule(self.config)
            results = module.run(dry_run=self.dry_run)
            self.logger.info(f"Model Sync completed: {results.get('total_processed', 0)} checked, "
                           f"{results.get('updated', 0)} updated, {results.get('errors', 0)} errors")
            module.close()
        except Exception as e:
            self.logger.exception(f"Error running Model Sync: {e}")
    
    def run_all(self):
        """Scheduled task: Run all modules in sequence."""
        self.logger.info("="*60)
        self.logger.info("Starting scheduled task: Run All Modules")
        self.logger.info("="*60)
        self.run_leavers()
        self.run_snipe_to_jamf()
        self.run_user_match()
        self.run_model_sync()
        self.logger.info("All scheduled tasks completed")


def create_scheduler(config: Config, dry_run: bool = False) -> "BlockingScheduler":
    """Create and configure the APScheduler."""
    if not APSCHEDULER_AVAILABLE:
        raise ImportError("APScheduler is not installed. Install with: pip install apscheduler")
    
    scheduler = BlockingScheduler()
    runner = ScheduledTaskRunner(config, dry_run=dry_run)
    logger = logging.getLogger('jamf-snipeit-scheduler')
    
    # Access scheduler config from config.scheduler dict
    schedule_config = config.scheduler or {}
    jobs_config = schedule_config.get("jobs", {})
    
    # Add scheduled jobs based on configuration
    leavers_job = jobs_config.get("leavers", {})
    if leavers_job.get("enabled") and leavers_job.get("cron"):
        logger.info(f"Scheduling Leavers: {leavers_job['cron']}")
        scheduler.add_job(
            runner.run_leavers,
            CronTrigger.from_crontab(leavers_job["cron"]),
            id='leavers',
            name='Leavers Module'
        )
    
    snipe_to_jamf_job = jobs_config.get("snipe_to_jamf", {})
    if snipe_to_jamf_job.get("enabled") and snipe_to_jamf_job.get("cron"):
        logger.info(f"Scheduling Snipe-to-Jamf: {snipe_to_jamf_job['cron']}")
        scheduler.add_job(
            runner.run_snipe_to_jamf,
            CronTrigger.from_crontab(snipe_to_jamf_job["cron"]),
            id='snipe_to_jamf',
            name='Snipe-to-Jamf Sync'
        )
    
    user_match_job = jobs_config.get("user_match", {})
    if user_match_job.get("enabled") and user_match_job.get("cron"):
        logger.info(f"Scheduling User Match: {user_match_job['cron']}")
        scheduler.add_job(
            runner.run_user_match,
            CronTrigger.from_crontab(user_match_job["cron"]),
            id='user_match',
            name='User Match Module'
        )
    
    model_sync_job = jobs_config.get("model_sync", {})
    if model_sync_job.get("enabled") and model_sync_job.get("cron"):
        logger.info(f"Scheduling Model Sync: {model_sync_job['cron']}")
        scheduler.add_job(
            runner.run_model_sync,
            CronTrigger.from_crontab(model_sync_job["cron"]),
            id='model_sync',
            name='Model Sync Module'
        )
    
    return scheduler


def main():
    """Main entry point for scheduler."""
    parser = argparse.ArgumentParser(
        description='Jamf-SnipeIT Suite - Scheduled Task Runner'
    )
    parser.add_argument('--config', '-c', default='config/config.yaml',
                        help='Path to configuration file')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable verbose logging')
    parser.add_argument('--log-file', '-l',
                        help='Path to log file')
    parser.add_argument('--dry-run', '-n', action='store_true',
                        help='Run in dry-run mode')
    parser.add_argument('--run-once', action='store_true',
                        help='Run all tasks once and exit (no scheduling)')
    
    args = parser.parse_args()
    
    if not APSCHEDULER_AVAILABLE and not args.run_once:
        print("❌ APScheduler is not installed. Install with: pip install apscheduler")
        print("   Or use --run-once to run all tasks immediately without scheduling.")
        return 1
    
    logger = setup_logging(args.verbose, args.log_file)
    
    print("""
╔═══════════════════════════════════════════════════════════╗
║            Jamf-SnipeIT Suite - Scheduler                 ║
║              Automated Task Execution                     ║
╚═══════════════════════════════════════════════════════════╝
    """)
    
    try:
        print(f"📁 Loading configuration from: {args.config}")
        config = get_config(args.config)
        print("✅ Configuration loaded successfully\n")
    except Exception as e:
        print(f"❌ Error loading configuration: {e}")
        return 1
    
    if args.run_once:
        print("🔄 Running all tasks once...\n")
        runner = ScheduledTaskRunner(config, dry_run=args.dry_run)
        runner.run_all()
        print("\n✅ All tasks completed")
        return 0
    
    try:
        scheduler = create_scheduler(config, dry_run=args.dry_run)
        
        jobs = scheduler.get_jobs()
        if not jobs:
            print("⚠️  No tasks scheduled. Check your config.yaml schedule section.")
            return 1
        
        print(f"📅 Scheduled {len(jobs)} task(s):")
        for job in jobs:
            print(f"   - {job.name}: {job.trigger}")
        
        print("\n🚀 Scheduler started. Press Ctrl+C to stop.\n")
        
        scheduler.start()
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Scheduler stopped by user")
        return 0
    except Exception as e:
        logger.exception(f"Scheduler error: {e}")
        return 1
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
