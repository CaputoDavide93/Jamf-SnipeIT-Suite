"""
Jamf-SnipeIT Suite - Sync State & Retry Queue
Persists last-run timestamps and a dead-letter queue for failed operations.
"""
import json
import logging
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def atomic_write_json(path: Path, data: Any, *, indent: Optional[int] = None) -> None:
    """Durably replace a JSON file without exposing a partially-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            json.dump(data, temp_file, indent=indent, default=str)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def backup_corrupt_json(path: Path) -> Optional[Path]:
    """Move unreadable JSON aside for diagnosis and return its backup path."""
    if not path.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    backup = path.with_name(f"{path.name}.corrupt-{timestamp}")
    try:
        os.replace(path, backup)
        return backup
    except OSError as exc:
        logger.error("Could not preserve corrupt JSON %s: %s", path, exc)
        return None


# =========================================================================
# Incremental sync state
# =========================================================================

class SyncState:
    """
    Lightweight JSON file that stores per-module state (last run time,
    counters, or any custom key-value data).

    File format::

        {
          "module_name": {
            "last_run": "2025-01-15T06:00:00",
            "custom_key": "value"
          }
        }
    """

    def __init__(self, state_file: str = "./output/sync_state.json"):
        self._path = Path(state_file)
        self._data: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ---- persistence -----

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                logger.debug(f"Loaded sync state from {self._path}")
            except (json.JSONDecodeError, OSError) as exc:
                backup = backup_corrupt_json(self._path)
                logger.warning(
                    "Could not load sync state: %s; preserved as %s",
                    exc,
                    backup or "<backup failed>",
                )
                self._data = {}
        else:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def _save(self) -> None:
        try:
            atomic_write_json(self._path, self._data, indent=2)
        except OSError as exc:
            logger.error(f"Failed to save sync state: {exc}")

    # ---- public API -----

    def get_last_run(self, module_name: str) -> Optional[str]:
        """Return the ISO timestamp of the last completed run, or None."""
        return self._data.get(module_name, {}).get("last_run")

    def set_last_run(self, module_name: str, timestamp: Optional[str] = None) -> None:
        """Record last-run time (defaults to now)."""
        if module_name not in self._data:
            self._data[module_name] = {}
        self._data[module_name]["last_run"] = timestamp or datetime.now().isoformat()
        self._save()

    def get(self, module_name: str, key: str, default: Any = None) -> Any:
        """Retrieve an arbitrary state value."""
        return self._data.get(module_name, {}).get(key, default)

    def set(self, module_name: str, key: str, value: Any) -> None:
        """Store an arbitrary state value and persist."""
        if module_name not in self._data:
            self._data[module_name] = {}
        self._data[module_name][key] = value
        self._save()

    def all(self) -> Dict[str, Dict[str, Any]]:
        """Return a snapshot of the entire state."""
        return dict(self._data)


# =========================================================================
# Retry / dead-letter queue
# =========================================================================

class RetryQueue:
    """
    Persists failed operations to a JSON file so they can be retried on
    the next run.

    Each item has::

        {
          "id": "<uuid>",
          "module": "user_match",
          "operation": "checkout_asset",
          "data": { ... },
          "error": "Timeout ...",
          "created": "2025-01-15T06:01:00",
          "attempts": 1,
          "status": "pending"       # pending | completed | dead
        }
    """

    MAX_ATTEMPTS = 3

    MAX_ITEMS = 500  # Auto-purge completed/dead items when queue exceeds this

    def __init__(self, queue_file: str = "./output/retry_queue.json"):
        self._path = Path(queue_file)
        self._items: List[Dict[str, Any]] = []
        self._load()
        self._auto_purge()

    # ---- persistence ----

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._items = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                backup = backup_corrupt_json(self._path)
                logger.warning(
                    "Could not load retry queue: %s; preserved as %s",
                    exc,
                    backup or "<backup failed>",
                )
                self._items = []
        else:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def _save(self) -> None:
        try:
            atomic_write_json(self._path, self._items, indent=2)
        except OSError as exc:
            logger.error(f"Failed to save retry queue: {exc}")

    def _auto_purge(self) -> None:
        """Remove completed/dead items if queue is too large."""
        if len(self._items) <= self.MAX_ITEMS:
            return
        before = len(self._items)
        self._items = [i for i in self._items if i.get("status") == "pending"]
        if len(self._items) < before:
            logger.info(f"Retry queue: auto-purged {before - len(self._items)} completed/dead items")
            self._save()

    # ---- public API ----

    def add(
        self,
        module: str,
        operation: str,
        data: Dict[str, Any],
        error: str,
    ) -> str:
        """Enqueue a failed operation.  Returns the item ID."""
        item_id = str(uuid.uuid4())[:8]
        self._items.append({
            "id": item_id,
            "module": module,
            "operation": operation,
            "data": data,
            "error": error,
            "created": datetime.now().isoformat(),
            "attempts": 1,
            "status": "pending",
        })
        self._save()
        logger.info(f"Retry queue: added {item_id} ({module}/{operation})")
        return item_id

    def get_pending(self, module: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return pending items, optionally filtered by module."""
        return [
            i for i in self._items
            if i["status"] == "pending"
            and (module is None or i["module"] == module)
        ]

    def mark_completed(self, item_id: str) -> None:
        for item in self._items:
            if item["id"] == item_id:
                item["status"] = "completed"
                break
        self._save()

    def mark_failed(self, item_id: str, error: str) -> None:
        for item in self._items:
            if item["id"] == item_id:
                item["attempts"] += 1
                item["error"] = error
                if item["attempts"] >= self.MAX_ATTEMPTS:
                    item["status"] = "dead"
                    logger.warning(f"Retry queue: item {item_id} moved to dead-letter after {item['attempts']} attempts")
                break
        self._save()

    def stats(self) -> Dict[str, int]:
        """Quick count of item statuses."""
        out: Dict[str, int] = {"pending": 0, "completed": 0, "dead": 0}
        for item in self._items:
            s = item.get("status", "pending")
            out[s] = out.get(s, 0) + 1
        return out

    def purge_completed(self) -> int:
        """Remove completed items.  Returns count removed."""
        before = len(self._items)
        self._items = [i for i in self._items if i["status"] != "completed"]
        removed = before - len(self._items)
        if removed:
            self._save()
        return removed
