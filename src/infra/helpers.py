"""
Jamf-SnipeIT Suite - Utility helpers
Logging setup, countdown, rate-limit delay, log cleanup, HTTP retry.
"""
import csv
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


# =============================================================================
# Azure AD helpers
# =============================================================================

def leave_date_passed(azure_user: Dict[str, Any], default_on_invalid: bool = False) -> bool:
    """
    True if the Azure AD user's employeeLeaveDateTime exists and is in the past.

    Single source of truth for leave-date parsing so Leavers, Starters and
    Rehire Detection cannot drift apart (Azure returns ISO 8601 with Z or
    offset; naive values are treated as UTC).

    Args:
        azure_user: Azure AD user dictionary
        default_on_invalid: Returned when the date exists but cannot be
            parsed. Each caller picks its fail-safe direction:
            - Leavers/Starters use False (don't tag / don't skip creation)
            - Rehire Detection uses True (don't auto-restore)

    Returns:
        True if leave date has passed, False if absent or in the future
    """
    leave = azure_user.get("employeeLeaveDateTime")
    if not leave:
        return False
    try:
        leave_str = leave.replace("Z", "+00:00") if isinstance(leave, str) else str(leave)
        leave_dt = datetime.fromisoformat(leave_str)
        if leave_dt.tzinfo is None:
            leave_dt = leave_dt.replace(tzinfo=timezone.utc)
        return leave_dt <= datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return default_on_invalid


# =============================================================================
# Logging Setup
# =============================================================================

def setup_logging(
    log_dir: str = "./logs",
    level: str = "INFO",
    module_name: str = "jamf_snipeit_suite",
    log_file: Optional[str] = None,
) -> Path:
    """
    Set up logging with file and console handlers.

    Args:
        log_dir: Directory for log files
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        module_name: Name for the log file
        log_file: Explicit log file path (overrides log_dir/module_name)

    Returns:
        Path to the log file
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    if log_file:
        log_file_path = Path(log_file)
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file_path = log_path / f"{module_name}_{timestamp}.log"

    # Configure root logger
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Clear existing handlers
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)

    # File handler
    file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
    file_handler.setLevel(log_level)
    file_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_formatter = logging.Formatter("%(levelname)-7s | %(message)s")
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    logger.info(f"Logging initialized: {log_file_path}")
    return log_file_path


# =============================================================================
# Countdown / Rate-limit helpers
# =============================================================================

def wait_with_countdown(seconds: float, message: str = "Rate limiting") -> None:
    """Wait silently for the given delay.
    
    Short waits (≤5s) and longer waits both just sleep — no console
    output — to avoid flooding Docker logs with countdown lines.
    """
    if seconds <= 0:
        return
    if seconds <= 5:
        time.sleep(seconds)
        return

    # Long waits: log once at debug, then sleep silently
    logger.debug("%s — pausing %.0fs", message, seconds)
    time.sleep(seconds)


def rate_limit_delay(
    delay_seconds: float,
    context: str = "",
    item_num: int = 0,
    total_items: int = 0,
) -> None:
    """Standardized rate limit delay with optional countdown display."""
    if delay_seconds <= 0:
        return

    if item_num and total_items:
        message = f"{context} [{item_num}/{total_items}]" if context else f"[{item_num}/{total_items}]"
    else:
        message = context or "Processing"

    if delay_seconds >= 1:
        wait_with_countdown(delay_seconds, message)
    else:
        time.sleep(delay_seconds)


# =============================================================================
# Log cleanup
# =============================================================================

def clean_old_logs(log_dir: str, max_days: int = 30) -> None:
    """Remove log files older than *max_days*."""
    log_path = Path(log_dir)
    if not log_path.exists():
        return

    cutoff = datetime.now().timestamp() - (max_days * 24 * 60 * 60)

    for file in log_path.glob("*.log"):
        try:
            if file.stat().st_mtime < cutoff:
                file.unlink()
                logger.debug(f"Removed old log: {file.name}")
        except Exception as e:
            logger.warning(f"Could not remove {file}: {e}")


# =============================================================================
# Retry helper
# =============================================================================

def request_with_backoff(
    session: requests.Session,
    method: str,
    url: str,
    max_retries: int = 3,
    retry_delay: int = 2,
    **kwargs,
) -> requests.Response:
    """Make a request with exponential backoff retry."""
    last_exception = None
    last_response: Optional[requests.Response] = None

    for attempt in range(1, max_retries + 1):
        try:
            response = session.request(method, url, **kwargs)

            if response.status_code == 429:
                last_response = response
                if attempt == max_retries:
                    # Out of attempts — hand the 429 back so the caller can
                    # inspect it, rather than raising a generic error that
                    # discards the response entirely.
                    logger.warning("Rate limited and out of retries; returning 429")
                    return response
                delay = retry_delay * (2 ** (attempt - 1))
                logger.warning(f"Rate limited, waiting {delay}s (attempt {attempt})")
                time.sleep(delay)
                continue

            return response

        except requests.RequestException as e:
            last_exception = e
            if attempt < max_retries:
                delay = retry_delay * (2 ** (attempt - 1))
                logger.warning(f"Request failed: {e}. Retrying in {delay}s...")
                time.sleep(delay)

    if last_response is not None:
        return last_response
    raise last_exception or requests.RequestException("Request failed after retries")
