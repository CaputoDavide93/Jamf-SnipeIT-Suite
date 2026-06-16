"""
Mutex lock via AWS SSM Parameter Store.

Prevents concurrent module runs from colliding (e.g. two Fargate tasks
triggered minutes apart both running User Match → one reverts the other's
work). Silent no-op if AWS/boto3 not available (local dev).
"""
import logging
import os
import socket
import time
import threading
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_LOCK_PARAM = os.environ.get("MUTEX_LOCK_PARAM", "/jamf-snipeit-suite-prod/run-lock")
_LOCK_TTL_MIN = 60  # Locks auto-expire after 60 minutes
_LOCK_REFRESH_SEC = int(os.environ.get("MUTEX_LOCK_REFRESH_SEC", "300"))


class RunMutex:
    """SSM-backed mutex. Best-effort; silent no-op if SSM unavailable."""

    def __init__(self, param_name: str = _LOCK_PARAM):
        self.param_name = param_name
        self._ssm = None
        self._owner = f"{socket.gethostname()}-{os.getpid()}"
        self._acquired = False
        self._refresh_thread = None
        try:
            import boto3
            self._ssm = boto3.client("ssm")
        except Exception:
            logger.debug("Mutex: boto3 unavailable, locking disabled")

    def acquire(self) -> bool:
        """Try to acquire. Returns True if acquired, False if already held."""
        if not self._ssm:
            return True  # no-op when SSM unavailable

        now = datetime.now(timezone.utc)
        try:
            # Check existing lock
            try:
                resp = self._ssm.get_parameter(Name=self.param_name)
                val = resp["Parameter"]["Value"]
                # Format: "owner|expiry_iso"
                parts = val.split("|", 1)
                if len(parts) == 2:
                    owner, expiry = parts
                    try:
                        expiry_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                        if expiry_dt > now:
                            logger.warning(f"Mutex held by '{owner}' until {expiry_dt.isoformat()}")
                            return False
                    except ValueError:
                        pass
            except self._ssm.exceptions.ParameterNotFound:
                pass

            # Acquire
            expiry = (now + timedelta(minutes=_LOCK_TTL_MIN)).isoformat()
            self._ssm.put_parameter(
                Name=self.param_name,
                Value=f"{self._owner}|{expiry}",
                Type="String",
                Overwrite=True,
            )
            self._acquired = True
            self._start_refresh_thread()
            logger.info(f"Mutex acquired by {self._owner} (TTL {_LOCK_TTL_MIN} min)")
            return True
        except Exception as e:
            logger.warning(f"Mutex acquire error (continuing without lock): {e}")
            return True

    def release(self) -> None:
        if not self._acquired or not self._ssm:
            return
        self._stop_refresh_thread()
        try:
            self._ssm.delete_parameter(Name=self.param_name)
            logger.info("Mutex released")
        except Exception as e:
            logger.debug(f"Mutex release error: {e}")
        self._acquired = False

    def _refresh_lock(self):
        """Background thread to extend TTL while the lock is held."""
        while self._acquired and self._ssm:
            try:
                now = datetime.now(timezone.utc)
                expiry = (now + timedelta(minutes=_LOCK_TTL_MIN)).isoformat()
                self._ssm.put_parameter(
                    Name=self.param_name,
                    Value=f"{self._owner}|{expiry}",
                    Type="String",
                    Overwrite=True,
                )
                logger.debug("Mutex TTL refreshed")
            except Exception as e:
                logger.warning(f"Mutex refresh error: {e}")
            time.sleep(max(60, _LOCK_REFRESH_SEC))

    def _start_refresh_thread(self):
        if not self._ssm or self._refresh_thread:
            return
        self._refresh_thread = threading.Thread(target=self._refresh_lock, daemon=True)
        self._refresh_thread.start()

    def _stop_refresh_thread(self):
        if self._refresh_thread:
            self._acquired = False
            self._refresh_thread = None

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f"Could not acquire mutex {self.param_name} — another run in progress")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
