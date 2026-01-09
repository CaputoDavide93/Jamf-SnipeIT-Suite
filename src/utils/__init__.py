"""
Jamf-SnipeIT Suite - Utility functions
Logging, audit CSV, retry helpers, and user matching.
"""
import csv
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)


# =============================================================================
# Logging Setup
# =============================================================================

def setup_logging(
    log_dir: str = "./logs",
    level: str = "INFO",
    module_name: str = "jamf_snipeit_suite",
) -> Path:
    """
    Set up logging with file and console handlers.
    
    Args:
        log_dir: Directory for log files
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        module_name: Name for the log file
    
    Returns:
        Path to the log file
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_path / f"{module_name}_{timestamp}.log"
    
    # Configure root logger
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    # Clear existing handlers
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)
    
    # File handler
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
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
    console_formatter = logging.Formatter(
        "%(levelname)-7s | %(message)s"
    )
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)
    
    logger.info(f"Logging initialized: {log_file}")
    return log_file


def wait_with_countdown(seconds: float, message: str = "Rate limiting") -> None:
    """
    Wait for specified seconds while showing a countdown to the user.
    
    Args:
        seconds: Number of seconds to wait (supports floats for sub-second delays)
        message: Context message to display
    """
    import sys
    
    if seconds <= 0:
        return
    
    # For very short delays (< 1 second), just sleep without countdown
    if seconds < 1:
        time.sleep(seconds)
        return
    
    # For longer delays, show countdown
    whole_seconds = int(seconds)
    for remaining in range(whole_seconds, 0, -1):
        # Use \r to overwrite the same line
        sys.stdout.write(f"\r⏳ {message} - waiting {remaining}s...")
        sys.stdout.flush()
        time.sleep(1)
    
    # Handle any fractional remainder
    remainder = seconds - whole_seconds
    if remainder > 0:
        time.sleep(remainder)
    
    # Clear the countdown line
    sys.stdout.write("\r" + " " * 70 + "\r")
    sys.stdout.flush()


def rate_limit_delay(delay_seconds: float, context: str = "", item_num: int = 0, total_items: int = 0) -> None:
    """
    Standardized rate limit delay with optional countdown display.
    
    Args:
        delay_seconds: Seconds to wait
        context: Context message (e.g., module name)
        item_num: Current item number (for progress display)
        total_items: Total items (for progress display)
    """
    if delay_seconds <= 0:
        return
    
    # Build progress message
    if item_num and total_items:
        message = f"{context} [{item_num}/{total_items}]" if context else f"[{item_num}/{total_items}]"
    else:
        message = context or "Processing"
    
    # Use countdown for delays >= 1 second
    if delay_seconds >= 1:
        wait_with_countdown(delay_seconds, message)
    else:
        time.sleep(delay_seconds)


def clean_old_logs(log_dir: str, max_days: int = 30) -> None:
    """
    Remove log files older than max_days.
    
    Args:
        log_dir: Directory containing log files
        max_days: Maximum age of logs to keep
    """
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
# Audit CSV Writer
# =============================================================================

class AuditCSV:
    """
    CSV writer for audit logging of operations.
    """
    
    def __init__(
        self,
        log_dir: str = "./logs",
        module_name: str = "audit",
        headers: Optional[List[str]] = None,
    ):
        """
        Initialize audit CSV writer.
        
        Args:
            log_dir: Directory for audit files
            module_name: Name prefix for the file
            headers: CSV column headers
        """
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.file_path = log_path / f"{module_name}_{timestamp}.csv"
        
        self.headers = headers or [
            "timestamp",
            "action",
            "status",
            "details",
        ]
        
        self._file = open(self.file_path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=self.headers)
        self._writer.writeheader()
        
        logger.info(f"Audit CSV initialized: {self.file_path}")
    
    def write(self, **kwargs) -> None:
        """
        Write a row to the audit CSV.
        
        Args:
            **kwargs: Column values (must match headers)
        """
        # Add timestamp if not provided
        if "timestamp" not in kwargs:
            kwargs["timestamp"] = datetime.now().isoformat()
        
        # Fill missing columns with empty strings
        row = {h: kwargs.get(h, "") for h in self.headers}
        self._writer.writerow(row)
        self._file.flush()
    
    def close(self) -> None:
        """Close the CSV file."""
        if self._file:
            self._file.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# =============================================================================
# Retry Helper
# =============================================================================

def request_with_backoff(
    session: requests.Session,
    method: str,
    url: str,
    max_retries: int = 3,
    retry_delay: int = 2,
    **kwargs,
) -> requests.Response:
    """
    Make a request with exponential backoff retry.
    
    Args:
        session: Requests session
        method: HTTP method
        url: Request URL
        max_retries: Maximum retries
        retry_delay: Initial delay (exponential backoff)
        **kwargs: Additional request arguments
    
    Returns:
        Response object
    
    Raises:
        requests.RequestException: If all retries fail
    """
    last_exception = None
    
    for attempt in range(1, max_retries + 1):
        try:
            response = session.request(method, url, **kwargs)
            
            # Handle rate limiting
            if response.status_code == 429:
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
    
    raise last_exception or requests.RequestException("Request failed after retries")


# =============================================================================
# User Matching Utilities
# =============================================================================

def longest_common_subsequence(s1: str, s2: str) -> int:
    """
    Calculate the length of the longest common subsequence.
    
    Args:
        s1: First string
        s2: Second string
    
    Returns:
        Length of LCS
    """
    m, n = len(s1), len(s2)
    if m == 0 or n == 0:
        return 0
    
    # Use space-optimized DP
    prev = [0] * (n + 1)
    curr = [0] * (n + 1)
    
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i - 1] == s2[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev, curr = curr, prev
    
    return prev[n]


def char_overlap(s1: str, s2: str) -> int:
    """
    Calculate character overlap between two strings.
    
    Args:
        s1: First string
        s2: Second string
    
    Returns:
        Number of overlapping characters
    """
    from collections import Counter
    c1 = Counter(s1.lower())
    c2 = Counter(s2.lower())
    return sum((c1 & c2).values())


def bigram_dice_coefficient(s1: str, s2: str) -> float:
    """
    Calculate Dice coefficient based on character bigrams.
    
    Args:
        s1: First string
        s2: Second string
    
    Returns:
        Dice coefficient (0.0 to 1.0)
    """
    def get_bigrams(s: str) -> set:
        s = s.lower().replace(" ", "")
        return set(s[i:i+2] for i in range(len(s) - 1)) if len(s) > 1 else set()
    
    b1 = get_bigrams(s1)
    b2 = get_bigrams(s2)
    
    if not b1 or not b2:
        return 0.0
    
    intersection = len(b1 & b2)
    return (2.0 * intersection) / (len(b1) + len(b2))


def normalize_name(name: str) -> str:
    """
    Normalize a name for comparison.
    
    Args:
        name: Name to normalize
    
    Returns:
        Normalized lowercase name
    """
    if not name:
        return ""
    # Remove common prefixes/suffixes, lowercase, strip
    normalized = name.lower().strip()
    # Remove common noise
    for noise in ["[disabled]", "(disabled)", "-disabled"]:
        normalized = normalized.replace(noise, "")
    return normalized.strip()


class UserMatcher:
    """
    Match users between systems using fuzzy matching.
    """
    
    def __init__(
        self,
        users: List[Dict[str, Any]],
        email_domain: str = "",
        min_score: float = 14,
        weight_lcs: float = 1.0,
        weight_char_overlap: float = 0.3,
        weight_bigram_dice: float = 2.0,
        use_bigram_dice: bool = True,
    ):
        """
        Initialize user matcher.
        
        Args:
            users: List of user dictionaries with 'id', 'name', 'email', 'username'
            email_domain: Default email domain for guessing emails
            min_score: Minimum score for a confident match
            weight_lcs: Weight for LCS score
            weight_char_overlap: Weight for character overlap
            weight_bigram_dice: Weight for Dice coefficient
            use_bigram_dice: Whether to use bigram Dice
        """
        self.users = users
        self.email_domain = email_domain.lower().lstrip("@")
        self.min_score = min_score
        self.weight_lcs = weight_lcs
        self.weight_char_overlap = weight_char_overlap
        self.weight_bigram_dice = weight_bigram_dice
        self.use_bigram_dice = use_bigram_dice
        
        # Build lookup indexes
        self._by_email: Dict[str, Dict] = {}
        self._by_username: Dict[str, Dict] = {}
        self._by_name: Dict[str, Dict] = {}  # Full name index for exact matching
        
        for user in users:
            email = (user.get("email") or "").lower()
            username = (user.get("username") or "").lower()
            name = normalize_name(user.get("name") or "")
            
            if email:
                self._by_email[email] = user
            if username:
                self._by_username[username] = user
            if name:
                self._by_name[name] = user
    
    def find_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Find user by exact email match."""
        return self._by_email.get(email.lower())
    
    def find_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Find user by exact username match."""
        return self._by_username.get(username.lower())
    
    def find_by_name(self, full_name: str) -> Optional[Dict[str, Any]]:
        """Find user by exact full name match (normalized)."""
        return self._by_name.get(normalize_name(full_name))
    
    def best_match(
        self,
        full_name_hint: str = "",
        username: str = "",
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """
        Find the best matching user.
        
        Args:
            full_name_hint: Full name to match against
            username: Username to match against
        
        Returns:
            Tuple of (matched user or None, debug info dict)
        """
        debug_info: Dict[str, Any] = {
            "exact_hit_reason": None,
            "top_candidates": [],
        }
        
        # PRIORITY 1: Try exact full name match from Jamf (most reliable)
        if full_name_hint:
            exact = self.find_by_name(full_name_hint)
            if exact:
                debug_info["exact_hit_reason"] = f"full_name={full_name_hint}"
                return exact, debug_info
        
        # PRIORITY 2: Try exact email match (guess from username)
        if username and self.email_domain:
            guessed_email = f"{username.lower()}@{self.email_domain}"
            exact = self.find_by_email(guessed_email)
            if exact:
                debug_info["exact_hit_reason"] = f"email={guessed_email}"
                return exact, debug_info
        
        # PRIORITY 3: Try exact username match
        if username:
            exact = self.find_by_username(username)
            if exact:
                debug_info["exact_hit_reason"] = f"username={username}"
                return exact, debug_info
        
        # PRIORITY 4: Fuzzy matching on full name
        if not full_name_hint:
            return None, debug_info
        
        normalized_hint = normalize_name(full_name_hint)
        candidates: List[Tuple[float, Dict]] = []
        
        for user in self.users:
            user_name = normalize_name(user.get("name", ""))
            if not user_name:
                continue
            
            score = 0.0
            
            # LCS score
            lcs_len = longest_common_subsequence(normalized_hint, user_name)
            score += lcs_len * self.weight_lcs
            
            # Character overlap
            overlap = char_overlap(normalized_hint, user_name)
            score += overlap * self.weight_char_overlap
            
            # Bigram Dice
            if self.use_bigram_dice:
                dice = bigram_dice_coefficient(normalized_hint, user_name)
                score += dice * self.weight_bigram_dice * 10  # Scale to similar range
            
            if score > 0:
                candidates.append((score, user))
        
        # Sort by score descending
        candidates.sort(key=lambda x: x[0], reverse=True)
        
        # Record top candidates for debugging
        debug_info["top_candidates"] = [
            {"email": u.get("email"), "name": u.get("name"), "score": round(s, 2)}
            for s, u in candidates[:5]
        ]
        
        # Return best match if above threshold
        if candidates and candidates[0][0] >= self.min_score:
            return candidates[0][1], debug_info
        
        return None, debug_info


# =============================================================================
# Local User Extraction (from Jamf)
# =============================================================================

def pick_primary_local_identity(
    local_users: List[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[str]]:
    """
    Pick the primary local user from Jamf local accounts.
    
    Args:
        local_users: List of local user dicts from Jamf
    
    Returns:
        Tuple of (username, full_name) for the primary user
    """
    if not local_users:
        return None, None
    
    # Skip system and IT admin accounts
    # Add your organization's shared/admin account names here
    skip_users = {
        # System accounts
        "root", "daemon", "nobody", "guest", "_spotlight", "_mbsetupuser",
        # IT admin/management accounts (customize for your org)
        "admin", "administrator", "jamfadmin",
    }
    
    candidates = []
    
    for user in local_users:
        username = (user.get("name") or user.get("username") or "").strip()
        full_name = (user.get("realname") or user.get("real_name") or "").strip()
        
        if not username:
            continue
        
        username_lower = username.lower()
        
        # Skip system/IT admin accounts
        if username_lower in skip_users or username_lower.startswith("_"):
            continue
        
        # Prefer users with real names and home directories
        score = 0
        if full_name:
            score += 10
        if user.get("home") and "/Users/" in str(user.get("home", "")):
            score += 5
        if user.get("admin"):
            score -= 2  # Slightly deprioritize admin accounts
        
        candidates.append((score, username, full_name))
    
    if not candidates:
        return None, None
    
    # Sort by score descending
    candidates.sort(key=lambda x: x[0], reverse=True)
    
    best = candidates[0]
    return best[1], best[2]
