"""Error tracking and reporting for copy operations."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .models import CopyError


class ErrorTracker:
    """Collects and persists copy errors."""

    def __init__(self, log_path: str):
        """Initialize error tracker.

        Args:
            log_path: Path to the error log file (JSON lines format).
        """
        self.log_path = log_path
        self.errors: list[CopyError] = []
        self._load_existing()

    def _load_existing(self) -> None:
        """Load errors from a previous session's log file."""
        if not os.path.exists(self.log_path):
            return
        try:
            with open(self.log_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.errors.append(CopyError.from_dict(json.loads(line)))
        except (OSError, json.JSONDecodeError):
            pass  # Start fresh if log is corrupted

    def add(self, path: str, error_type: str, message: str) -> None:
        """Record a copy error.

        Args:
            path: File path that caused the error.
            error_type: Category of error (e.g., 'permission', 'io', 'rsync').
            message: Human-readable error description.
        """
        error = CopyError(
            path=path,
            error_type=error_type,
            message=message,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self.errors.append(error)
        self._append_to_log(error)

    def _append_to_log(self, error: CopyError) -> None:
        """Append a single error to the log file."""
        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            with open(self.log_path, "a") as f:
                f.write(json.dumps(error.to_dict()) + "\n")
        except OSError:
            pass  # Can't write log - don't let this stop the copy

    @property
    def count(self) -> int:
        return len(self.errors)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def grouped(self) -> dict[str, list[CopyError]]:
        """Group errors by error_type."""
        groups: dict[str, list[CopyError]] = defaultdict(list)
        for err in self.errors:
            groups[err.error_type].append(err)
        return dict(groups)

    def clear_log(self) -> None:
        """Remove the error log file."""
        try:
            os.remove(self.log_path)
        except OSError:
            pass
        self.errors.clear()


def display_error_report(tracker: ErrorTracker, console: Console | None = None) -> None:
    """Display a formatted error report."""
    if console is None:
        console = Console()

    if not tracker.has_errors:
        console.print(Panel("[bold green]No errors during copy[/bold green]"))
        return

    console.print(Panel(f"[bold red]{tracker.count} errors encountered during copy[/bold red]"))

    # Summary by type
    grouped = tracker.grouped()
    summary_table = Table(title="Errors by type", show_header=True, header_style="bold")
    summary_table.add_column("Error Type", style="bold")
    summary_table.add_column("Count", justify="right")
    for etype, errors in sorted(grouped.items(), key=lambda x: -len(x[1])):
        summary_table.add_row(etype, str(len(errors)))
    console.print(summary_table)

    # Full error list
    detail_table = Table(title="All errors", show_header=True, header_style="bold", expand=True)
    detail_table.add_column("Type", width=12, no_wrap=True)
    detail_table.add_column("Path", ratio=2)
    detail_table.add_column("Message", ratio=3)
    detail_table.add_column("Time", width=20, no_wrap=True)

    for err in tracker.errors:
        path_display = err.path if len(err.path) <= 60 else "..." + err.path[-57:]
        # Show just time portion
        time_display = err.timestamp.split("T")[1][:8] if "T" in err.timestamp else err.timestamp
        detail_table.add_row(err.error_type, path_display, err.message, time_display)

    console.print(detail_table)

    console.print(f"\n[dim]Full error log saved to: {tracker.log_path}[/dim]")
