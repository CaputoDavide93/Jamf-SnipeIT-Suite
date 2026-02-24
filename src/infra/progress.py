"""
Jamf-SnipeIT Suite - Progress Tracker
Lightweight logger-based progress reporting with visual bar.
"""
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Visual bar characters
_FULL = "█"
_PARTIAL = "▓"
_LIGHT = "░"
_BAR_WIDTH = 20


def _progress_bar(current: int, total: int, width: int = _BAR_WIDTH) -> str:
    """Render a compact progress bar: ████████░░░░░░░░░░░░"""
    if total <= 0:
        return ""
    ratio = min(current / total, 1.0)
    filled = int(ratio * width)
    return _FULL * filled + _LIGHT * (width - filled)


class ProgressTracker:
    """
    Logs progress messages at configurable intervals with visual bars.

    Usage::

        progress = ProgressTracker("User Match", total=555)
        for comp in computers:
            ... process ...
            progress.advance()
        progress.finish()
    """

    def __init__(
        self,
        label: str,
        total: int = 0,
        log_every: int = 25,
        log_every_seconds: float = 30.0,
    ):
        self.label = label
        self.total = total
        self.log_every = max(1, log_every)
        self.log_every_seconds = log_every_seconds

        self.current = 0
        self._start = time.monotonic()
        self._last_log_time = self._start

    def advance(self, n: int = 1, detail: str = "") -> None:
        """Record *n* items processed and log if interval reached."""
        self.current += n
        now = time.monotonic()

        should_log = (
            self.current % self.log_every == 0
            or (now - self._last_log_time) >= self.log_every_seconds
            or self.current == self.total
        )

        if should_log:
            self._emit(detail)
            self._last_log_time = now

    def _emit(self, detail: str = "") -> None:
        elapsed = time.monotonic() - self._start
        rate = self.current / elapsed if elapsed > 0 else 0

        if self.total > 0:
            pct = self.current / self.total * 100
            bar = _progress_bar(self.current, self.total)
            msg = (
                f"  {bar}  {self.label}  "
                f"{self.current}/{self.total} ({pct:.0f}%)  "
                f"{rate:.1f}/s"
            )
        else:
            msg = f"  ⏳  {self.label}  {self.current} processed  {rate:.1f}/s"

        if detail:
            msg += f"  │ {detail}"

        logger.info(msg)

    def finish(self, extra: str = "") -> float:
        """Log final summary and return elapsed seconds."""
        elapsed = time.monotonic() - self._start
        rate = self.current / elapsed if elapsed > 0 else 0
        if self.total > 0:
            bar = _progress_bar(self.current, self.total)
        else:
            bar = _FULL * _BAR_WIDTH
        msg = (
            f"  {bar}  {self.label}  ✓ {self.current} items  "
            f"{elapsed:.0f}s ({rate:.1f}/s)"
        )
        if extra:
            msg += f"  │ {extra}"
        logger.info(msg)
        return elapsed
