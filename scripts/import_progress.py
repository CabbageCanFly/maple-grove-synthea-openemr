#!/usr/bin/env python3
"""Consistent timestamped progress output for OpenEMR import scripts."""

from __future__ import annotations

import sys
import time
from collections import Counter
from datetime import datetime
from typing import TextIO


_COMPLETED_STATUSES = {"CREATED", "SKIPPED", "FAILED", "SEEDED"}


def timestamp() -> str:
    """Return a short local terminal timestamp."""
    return datetime.now().astimezone().strftime("%H:%M:%S")


def format_duration(seconds: float) -> str:
    """Render a compact human-readable duration."""
    if 0 < seconds < 1:
        return "<1s"

    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


class ImportProgress:
    """Track per-record status, elapsed time, and a current-stage ETA."""

    def __init__(
        self,
        item_name: str,
        item_name_plural: str,
        total: int,
        *,
        quiet: bool = False,
        progress_every: int = 100,
    ) -> None:
        self.item_name = item_name
        self.item_name_plural = item_name_plural
        self.total = max(0, total)
        self.quiet = quiet
        self.progress_every = max(0, progress_every)
        self.started = time.monotonic()
        self.processed = 0
        self.counts: Counter[str] = Counter()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def start(self) -> None:
        print(
            f"[{timestamp()}] {self.item_name_plural} import started: "
            f"{self.total} selected",
            flush=True,
        )

    def _timing_text(self) -> str:
        parts = [f"elapsed {format_duration(self.elapsed)}"]

        if self.processed > 0 and self.processed < self.total:
            average = self.elapsed / self.processed
            remaining = average * (self.total - self.processed)
            parts.append(f"ETA {format_duration(remaining)}")

        return " | ".join(parts)

    def _record_line(
        self,
        status: str,
        label: str,
        detail: str,
    ) -> str:
        line = (
            f"[{timestamp()}] {self.item_name} "
            f"{self.processed}/{self.total} {status} — {label}"
        )
        if detail:
            line += f" — {detail}"
        return f"{line} | {self._timing_text()}"

    def _summary_line(self) -> str:
        status_order = ("CREATED", "SEEDED", "SKIPPED", "FAILED")
        counts = ", ".join(
            f"{status.casefold()}={self.counts[status]}"
            for status in status_order
            if self.counts[status]
            or status in {"CREATED", "SKIPPED", "FAILED"}
        )
        percent = (
            (self.processed / self.total) * 100
            if self.total
            else 100.0
        )
        return (
            f"[{timestamp()}] {self.item_name_plural} "
            f"{self.processed}/{self.total} ({percent:.0f}%) | "
            f"{counts} | {self._timing_text()}"
        )

    def record(
        self,
        status: str,
        label: str,
        detail: str = "",
        *,
        stream: TextIO | None = None,
    ) -> None:
        """Record one completed source item and print appropriate output."""
        normalized = status.strip().upper()
        if normalized not in _COMPLETED_STATUSES:
            raise ValueError(f"Unsupported completed status: {status}")

        self.processed += 1
        self.counts[normalized] += 1

        destination = stream
        if destination is None:
            destination = sys.stderr if normalized == "FAILED" else sys.stdout

        if not self.quiet or normalized == "FAILED":
            print(
                self._record_line(normalized, label, detail),
                file=destination,
                flush=True,
            )
            return

        if self.progress_every and (
            self.processed % self.progress_every == 0
            or self.processed == self.total
        ):
            print(self._summary_line(), flush=True)

    def warning(self, label: str, detail: str) -> None:
        """Print a warning without advancing the processed counter."""
        print(
            f"[{timestamp()}] {self.item_name} WARNING — {label} — {detail}",
            file=sys.stderr,
            flush=True,
        )

    def finish(self) -> None:
        """Print the final processed count and elapsed stage duration."""
        print(
            f"[{timestamp()}] {self.item_name_plural} import finished: "
            f"{self.processed}/{self.total} processed in "
            f"{format_duration(self.elapsed)}",
            flush=True,
        )
