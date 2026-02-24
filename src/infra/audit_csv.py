"""
Jamf-SnipeIT Suite - Audit CSV Writer
Thread-safe CSV logger for operation auditing.
"""
import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class AuditCSV:
    """CSV writer for audit logging of operations."""

    def __init__(
        self,
        log_dir: str = "./logs",
        module_name: str = "audit",
        headers: Optional[List[str]] = None,
        enabled: bool = True,
    ):
        """
        Args:
            log_dir: Directory for audit files
            module_name: Name prefix for the file
            headers: CSV column headers
            enabled: If False, all writes are silently skipped (honours config.logging.audit_csv)
        """
        self.enabled = enabled
        self._file = None
        self._writer = None
        self.file_path = None

        if not self.enabled:
            logger.debug("AuditCSV disabled by configuration")
            return

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
        """Write a row. Missing columns are filled with empty strings."""
        if not self.enabled or self._writer is None:
            return

        if "timestamp" not in kwargs:
            kwargs["timestamp"] = datetime.now().isoformat()

        row = {h: kwargs.get(h, "") for h in self.headers}
        self._writer.writerow(row)
        self._file.flush()

    def close(self) -> None:
        """Close the CSV file."""
        if self._file:
            self._file.close()
            self._file = None
            self._writer = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
