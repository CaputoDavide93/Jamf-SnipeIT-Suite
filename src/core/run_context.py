"""Per-run module metrics and error collection."""
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


class RunContext:
    """
    Accumulates per-module metrics and errors for one startup run.

    Platform data is intentionally not shared between modules: modules earlier
    in the chain mutate users and assets, so downstream modules must refetch
    authoritative state instead of observing a stale run-level cache.
    """

    def __init__(self) -> None:
        self.metrics: Dict[str, ModuleMetrics] = {}
        self.errors: List[Dict[str, Any]] = []
        self.run_start: datetime = datetime.now()

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
