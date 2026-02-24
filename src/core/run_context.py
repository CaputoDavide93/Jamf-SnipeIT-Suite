"""
Jamf-SnipeIT Suite - Run Context (Shared Data Bus)
Caches expensive API fetches so multiple modules in the same startup
run can share users, assets, and computers without re-fetching.
Also collects per-module execution metrics and errors.
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =========================================================================
# Module execution metrics
# =========================================================================

@dataclass
class ModuleMetrics:
    """Execution metrics for a single module run."""
    module_name: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_seconds: float = 0.0
    items_processed: int = 0
    items_updated: int = 0
    items_skipped: int = 0
    items_errored: int = 0
    custom: Dict[str, Any] = field(default_factory=dict)

    def start(self) -> "ModuleMetrics":
        self.start_time = datetime.now()
        return self

    def stop(self) -> "ModuleMetrics":
        self.end_time = datetime.now()
        if self.start_time:
            self.duration_seconds = (self.end_time - self.start_time).total_seconds()
        return self

    def set(self, key: str, value: Any) -> None:
        self.custom[key] = value

    def as_dict(self) -> Dict[str, Any]:
        return {
            "module": self.module_name,
            "start": self.start_time.isoformat() if self.start_time else None,
            "end": self.end_time.isoformat() if self.end_time else None,
            "duration_s": round(self.duration_seconds, 2),
            "processed": self.items_processed,
            "updated": self.items_updated,
            "skipped": self.items_skipped,
            "errored": self.items_errored,
            **self.custom,
        }


# =========================================================================
# Shared data bus
# =========================================================================

class RunContext:
    """
    Shared data bus passed through a single startup / scheduled run.

    Lazily caches bulk API responses so downstream modules skip redundant
    fetches.  Also accumulates per-module metrics and errors for the
    summary / Slack notification at the end of the run.
    """

    def __init__(self) -> None:
        self._snipe_users: Optional[List[Dict]] = None
        self._snipe_assets: Optional[List[Dict]] = None
        self._serial_map: Optional[Dict[str, Dict]] = None
        self._jamf_computers_basic: Optional[List[Dict]] = None

        self.metrics: Dict[str, ModuleMetrics] = {}
        self.errors: List[Dict[str, Any]] = []
        self.run_start: datetime = datetime.now()

    # ----- lazy getters -----

    def get_snipe_users(self, snipe_client: Any) -> List[Dict]:
        """Get (or cache) all Snipe-IT users."""
        if self._snipe_users is None:
            logger.info("[RunContext] Fetching all Snipe-IT users (shared cache)…")
            self._snipe_users = snipe_client.get_all_users()
            logger.info(f"[RunContext] Cached {len(self._snipe_users)} Snipe-IT users")
        return self._snipe_users

    def get_snipe_assets(self, snipe_client: Any) -> List[Dict]:
        """Get (or cache) all Snipe-IT assets."""
        if self._snipe_assets is None:
            logger.info("[RunContext] Fetching all Snipe-IT assets (shared cache)…")
            self._snipe_assets = snipe_client.get_all_assets()
            logger.info(f"[RunContext] Cached {len(self._snipe_assets)} Snipe-IT assets")
        return self._snipe_assets

    def get_serial_map(self, snipe_client: Any) -> Dict[str, Dict]:
        """Get (or build) serial→asset map from cached assets."""
        if self._serial_map is None:
            assets = self.get_snipe_assets(snipe_client)
            self._serial_map = {}
            for a in assets:
                serial = (a.get("serial") or "").strip().upper()
                if serial:
                    self._serial_map[serial] = a
            logger.info(f"[RunContext] Built serial map with {len(self._serial_map)} entries")
        return self._serial_map

    def get_jamf_basic(self, jamf_client: Any) -> List[Dict]:
        """Get (or cache) basic Jamf computer list."""
        if self._jamf_computers_basic is None:
            logger.info("[RunContext] Fetching basic Jamf computer list (shared cache)…")
            self._jamf_computers_basic = jamf_client.get_all_computers_basic()
            logger.info(f"[RunContext] Cached {len(self._jamf_computers_basic)} Jamf computers")
        return self._jamf_computers_basic

    # ----- cache invalidation -----

    def invalidate(self, key: Optional[str] = None) -> None:
        """Clear cached data.  Pass a key ('snipe_users', 'snipe_assets',
        'serial_map', 'jamf_basic') to clear a specific cache, or None
        to clear everything."""
        targets = {
            "snipe_users": "_snipe_users",
            "snipe_assets": "_snipe_assets",
            "serial_map": "_serial_map",
            "jamf_basic": "_jamf_computers_basic",
        }
        if key is None:
            for attr in targets.values():
                setattr(self, attr, None)
        elif key in targets:
            setattr(self, targets[key], None)

    # ----- metrics helpers -----

    def start_module(self, module_name: str) -> ModuleMetrics:
        """Create and start metrics for a module."""
        m = ModuleMetrics(module_name=module_name).start()
        self.metrics[module_name] = m
        return m

    def stop_module(self, module_name: str, results: Optional[Dict] = None) -> ModuleMetrics:
        """Stop metrics for a module, optionally ingesting result counts."""
        m = self.metrics.get(module_name)
        if not m:
            m = ModuleMetrics(module_name=module_name)
            self.metrics[module_name] = m
        m.stop()
        if results:
            m.items_processed = results.get("total_processed", results.get("total_assets_checked", 0))
            m.items_updated = results.get("updated", results.get("corrections_made", 0))
            m.items_skipped = results.get("skipped", 0)
            m.items_errored = results.get("errors", 0) if isinstance(results.get("errors"), int) else len(results.get("errors", []))
        return m

    def record_error(self, module_name: str, error: Any) -> None:
        """Append an error entry for the run summary."""
        self.errors.append({
            "module": module_name,
            "error": str(error),
            "time": datetime.now().isoformat(),
        })

    # ----- summary -----

    def summary(self) -> Dict[str, Any]:
        """Produce a JSON-serialisable run summary."""
        elapsed = (datetime.now() - self.run_start).total_seconds()
        return {
            "run_start": self.run_start.isoformat(),
            "duration_seconds": round(elapsed, 2),
            "modules": {name: m.as_dict() for name, m in self.metrics.items()},
            "total_errors": len(self.errors),
            "errors": self.errors[:50],  # cap for Slack message size
        }
