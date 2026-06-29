"""TTY-aware progress helpers with a zero-dependency fallback.

Progress bars are additive observability: if tqdm is unavailable, disabled, or
stdout/stderr is not interactive, these helpers become no-ops.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterable
from typing import Any


DEFAULT_PROGRESS_THRESHOLD = 50_000


def progress_mode(progress: Any = "auto") -> str:
    """Resolve a progress mode from a string or config dictionary."""
    if isinstance(progress, dict):
        progress = progress.get("progress_mode", "auto")
    mode = str(progress or "auto").strip().lower()
    if mode not in {"auto", "bar", "plain", "none"}:
        return "auto"
    return mode


def should_show_progress(
    progress: Any = "auto",
    *,
    total: int | None = None,
    threshold: int | None = DEFAULT_PROGRESS_THRESHOLD,
    stream=None,
) -> bool:
    """Return True when a progress bar should be attempted."""
    mode = progress_mode(progress)
    if mode in {"plain", "none"}:
        return False
    if total is not None and threshold is not None and total < threshold and mode != "bar":
        return False
    if mode == "bar":
        return True
    stream = stream or sys.stderr
    return bool(getattr(stream, "isatty", lambda: False)())


def progress_iter(
    iterable: Iterable,
    desc: str,
    total: int | None = None,
    *,
    enabled: bool = True,
):
    """Wrap an iterable in tqdm when available; otherwise return it unchanged."""
    if not enabled:
        return iterable
    try:
        from tqdm import tqdm
    except ImportError:
        return iterable
    return tqdm(
        iterable,
        desc=desc,
        total=total,
        file=sys.stderr,
        leave=False,
        dynamic_ncols=True,
    )


class StageTracker:
    """Small stage-level progress bar for the pipeline.

    Logs remain on stdout. The bar, when enabled, is written to stderr.
    Plain mode prints elapsed time and ETA after each stage so batch terminals
    still show that the run is alive without requiring an interactive bar.
    """

    def __init__(self, total: int, mode: Any = "auto", desc: str = "Pipeline"):
        self._bar = None
        self._plain = False
        self._total = int(total)
        self._done = 0
        self._desc = desc
        self._started_at = time.monotonic()
        self._stage_started_at = self._started_at
        self._current_stage: str | None = None

        resolved_mode = progress_mode(mode)
        if resolved_mode == "none":
            return

        bar_requested = should_show_progress(resolved_mode, threshold=None)
        if bar_requested:
            try:
                from tqdm import tqdm
            except ImportError:
                self._plain = True
            else:
                self._bar = tqdm(
                    total=total,
                    desc=desc,
                    file=sys.stderr,
                    leave=False,
                    dynamic_ncols=True,
                )
                return

        # "auto" intentionally falls back to plain status lines in non-TTY
        # terminals. That matches the CLI help and avoids silent long runs.
        if resolved_mode in {"auto", "bar", "plain"}:
            self._plain = True

    @staticmethod
    def _fmt_duration(seconds: float | None) -> str:
        if seconds is None:
            return "estimating"
        seconds = max(0, int(round(seconds)))
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{sec:02d}"
        return f"{minutes:02d}:{sec:02d}"

    def _eta_seconds(self) -> float | None:
        if self._done <= 0:
            return None
        elapsed = time.monotonic() - self._started_at
        avg = elapsed / self._done
        return avg * max(self._total - self._done, 0)

    def start(self, stage: str) -> None:
        """Mark the beginning of a stage in plain mode."""
        self._current_stage = stage
        self._stage_started_at = time.monotonic()
        if self._bar is not None:
            self._bar.set_description_str(f"{self._desc}: {stage}")
            return
        if not self._plain:
            return
        elapsed = time.monotonic() - self._started_at
        print(
            f"  Progress: {self._done}/{self._total} {stage} started | "
            f"elapsed {self._fmt_duration(elapsed)} | ETA {self._fmt_duration(self._eta_seconds())}",
            flush=True,
        )

    def advance(self, stage: str | None = None) -> None:
        label = stage or self._current_stage or "stage"
        if self._bar is None:
            if not self._plain:
                return
            self._done = min(self._done + 1, self._total)
            now = time.monotonic()
            stage_elapsed = now - self._stage_started_at
            elapsed = now - self._started_at
            eta = self._eta_seconds()
            print(
                f"  Progress: {self._done}/{self._total} {label} done | "
                f"stage {self._fmt_duration(stage_elapsed)} | "
                f"elapsed {self._fmt_duration(elapsed)} | "
                f"ETA {self._fmt_duration(eta)}",
                flush=True,
            )
            return
        if stage:
            self._bar.set_description_str(f"{self._desc}: {stage}")
        self._bar.update(1)
        self._done = min(self._done + 1, self._total)

    def close(self) -> None:
        if self._bar is not None:
            self._bar.close()
            self._bar = None
