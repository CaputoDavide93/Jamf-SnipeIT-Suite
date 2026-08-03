"""
Mutex lock via AWS SSM Parameter Store.

Prevents concurrent module runs from colliding (e.g. two Fargate tasks
triggered minutes apart both running User Match → one reverts the other's
work). Set MUTEX_DISABLED=true explicitly for local development without AWS.
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
    """SSM-backed mutex that fails closed unless explicitly disabled."""

    def __init__(self, param_name: str = _LOCK_PARAM):
        self.param_name = param_name
        self._ssm = None
        self._disabled = os.environ.get("MUTEX_DISABLED", "false").strip().lower() in (
            "1", "true", "yes",
        )
        self._owner = f"{socket.gethostname()}-{os.getpid()}"
        self._acquired = False
        self._refresh_thread = None
        if self._disabled:
            logger.warning("Mutex explicitly disabled by MUTEX_DISABLED")
            return
        try:
            import boto3
            self._ssm = boto3.client("ssm")
        except Exception as exc:
            logger.error("Mutex backend unavailable: %s", exc)

    def acquire(self) -> bool:
        """Try to acquire. Returns True if acquired, False if already held."""
        if self._disabled:
            return True
        if not self._ssm:
            logger.error("Mutex acquisition refused: SSM backend unavailable")
            return False

        now = datetime.now(timezone.utc)
        try:
            # Claim atomically: Overwrite=False makes SSM reject the write if
            # the parameter already exists, so two tasks racing here cannot
            # both win. The previous check-then-write left a window where both
            # read "no lock" and both proceeded.
            expiry = (now + timedelta(minutes=_LOCK_TTL_MIN)).isoformat()
            value = f"{self._owner}|{expiry}"
            try:
                self._claim(value)
            except Exception as first_error:
                if not self._is_already_exists(first_error):
                    raise
                if not self._holder_expired(now):
                    return False
                # Stale lock: delete it and re-claim, still without Overwrite.
                # If a competitor re-claims between the delete and our put we
                # lose the race and back off, which is the safe direction.
                try:
                    self._ssm.delete_parameter(Name=self.param_name)
                except Exception:
                    pass
                try:
                    self._claim(value)
                except Exception as second_error:
                    if self._is_already_exists(second_error):
                        logger.warning("Mutex re-claimed by another run — backing off")
                        return False
                    raise

            self._acquired = True
            self._start_refresh_thread()
            logger.info(f"Mutex acquired by {self._owner} (TTL {_LOCK_TTL_MIN} min)")
            return True
        except Exception as e:
            logger.error(f"Mutex acquire error: {e}")
            return False

    def _claim(self, value: str) -> None:
        """Create the lock parameter, failing if it already exists."""
        self._ssm.put_parameter(
            Name=self.param_name,
            Value=value,
            Type="String",
            Overwrite=False,
        )

    def _is_already_exists(self, err: Exception) -> bool:
        """Whether an SSM error means the lock parameter is already present."""
        cls = getattr(getattr(self._ssm, "exceptions", None), "ParameterAlreadyExists", None)
        if cls is not None and isinstance(cls, type) and isinstance(err, cls):
            return True
        return (
            "ParameterAlreadyExists" in type(err).__name__
            or "already exists" in str(err).lower()
        )

    def _holder_expired(self, now: datetime) -> bool:
        """Whether a readable, well-formed existing lock is past its TTL."""
        try:
            resp = self._ssm.get_parameter(Name=self.param_name)
            val = resp["Parameter"]["Value"]
        except Exception as exc:
            logger.error("Mutex holder could not be inspected: %s", exc)
            return False

        parts = val.split("|", 1)
        if len(parts) != 2:
            logger.error("Mutex value malformed — refusing to reclaim")
            return False
        owner, expiry = parts
        try:
            expiry_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        except ValueError:
            logger.error(f"Mutex expiry unparseable ('{expiry}') — refusing to reclaim")
            return False
        if expiry_dt > now:
            logger.warning(f"Mutex held by '{owner}' until {expiry_dt.isoformat()}")
            return False
        logger.warning(f"Mutex held by '{owner}' expired at {expiry_dt.isoformat()} — reclaiming")
        return True

    def release(self) -> None:
        if not self._acquired or not self._ssm:
            return
        self._stop_refresh_thread()
        try:
            # Only delete a lock we still own. If our TTL lapsed and another
            # run reclaimed it, deleting here would strip that run's lock.
            if self._still_owner():
                self._ssm.delete_parameter(Name=self.param_name)
                logger.info("Mutex released")
            else:
                logger.warning("Mutex no longer owned by this run — not deleting")
        except Exception as e:
            logger.debug(f"Mutex release error: {e}")
        self._acquired = False

    def _still_owner(self) -> bool:
        """Whether the stored lock still names this process as owner."""
        try:
            resp = self._ssm.get_parameter(Name=self.param_name)
            return resp["Parameter"]["Value"].split("|", 1)[0] == self._owner
        except Exception as exc:
            logger.warning("Mutex ownership could not be verified: %s", exc)
            return False

    def _refresh_lock(self):
        """Background thread to extend TTL while the lock is held."""
        while self._acquired and self._ssm:
            refresh_result = self._refresh_once()
            if refresh_result is False:
                return
            time.sleep(max(60, _LOCK_REFRESH_SEC))

    def _refresh_once(self) -> Optional[bool]:
        """Refresh one TTL: True=done, False=owner lost, None=retry later."""
        try:
            response = self._ssm.get_parameter(Name=self.param_name)
            stored_owner = response["Parameter"]["Value"].split("|", 1)[0]
        except Exception as exc:
            logger.warning("Mutex ownership check failed during refresh: %s", exc)
            return None

        if stored_owner != self._owner:
            logger.error("Mutex ownership lost or unverifiable — stopping refresh")
            return False
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
            return True
        except Exception as e:
            logger.warning(f"Mutex refresh error: {e}")
            return None

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
