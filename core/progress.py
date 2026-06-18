"""TTY-aware progress helpers with a zero-dependency fallback.

Progress bars are additive observability: if tqdm is unavailable, disabled, or
stdout/stderr is not interactive, these helpers become no-ops.
"""

from __future__ import annotations

import sys
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
    """

    def __init__(self, total: int, mode: Any = "auto", desc: str = "Pipeline"):
        self._bar = None
        if not should_show_progress(mode, threshold=None):
            return
        try:
            from tqdm import tqdm
        except ImportError:
            return
        self._bar = tqdm(
            total=total,
            desc=desc,
            file=sys.stderr,
            leave=False,
            dynamic_ncols=True,
        )

    def advance(self, stage: str | None = None) -> None:
        if self._bar is None:
            return
        if stage:
            self._bar.set_description_str(f"Pipeline: {stage}")
        self._bar.update(1)

    def close(self) -> None:
        if self._bar is not None:
            self._bar.close()
            self._bar = None
