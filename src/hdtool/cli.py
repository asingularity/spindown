"""CLI entry point and main application flow."""

from __future__ import annotations

import os
import signal
import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from .copier import (
    CopyProgress,
    PythonCopier,
    RsyncCopier,
    fix_timestamps,
    has_rsync,
)
from .errors import ErrorTracker, display_error_report
from .models import FileNode, format_size
from .precheck import detect_filesystem, display_precheck_report, run_precheck
from .scanner import scan_directory
from .selector import run_selector
from .state import (
    SessionLock,
    create_session,
    delete_state,
    error_log_path,
    load_state,
    save_state,
)


@click.command()
@click.option("--source", default="/source", help="Source directory to back up")
@click.option("--dest", default="/dest", help="Destination directory for backup")
@click.option("--resume", "auto_resume", is_flag=True, help="Auto-resume without prompting")
@click.option("--no-precheck", is_flag=True, help="Skip pre-check analysis")
@click.option("--dry-run", is_flag=True, help="Show what would be copied without copying")
@click.option("--python-copier", is_flag=True, help="Use Python copier instead of rsync")
@click.option(
    "--fix-timestamps", "fix_ts", is_flag=True,
    help="Fix timestamps on destination files from a previous bad copy (no data re-copied)",
)
def main(
    source: str,
    dest: str,
    auto_resume: bool,
    no_precheck: bool,
    dry_run: bool,
    python_copier: bool,
    fix_ts: bool,
) -> None:
    """Hard Drive Backup Tool - safely copy drive contents with metadata preservation."""
    console = Console()

    # Validate paths
    source = os.path.abspath(source)
    dest = os.path.abspath(dest)

    if not os.path.isdir(source):
        console.print(f"[bold red]Error:[/bold red] Source directory not found: {source}")
        sys.exit(1)

    os.makedirs(dest, exist_ok=True)

    # ---- Fix timestamps mode ----
    if fix_ts:
        _run_fix_timestamps(source, dest, console)
        return

    # ---- Normal backup mode ----
    use_rsync = has_rsync() and not python_copier
    engine = "rsync" if use_rsync else "python"

    console.print(Panel(
        f"[bold]Hard Drive Backup Tool[/bold]\n\n"
        f"  Source:  [cyan]{source}[/cyan]\n"
        f"  Dest:    [cyan]{dest}[/cyan]\n"
        f"  Engine:  {engine}",
        title="hdtool",
    ))

    # Acquire lock
    with SessionLock(dest) as lock:
        if not lock.acquired:
            console.print("[bold red]Error:[/bold red] Another hdtool instance is running on this destination.")
            sys.exit(1)

        # Check for existing session
        existing_state = load_state(dest)
        if existing_state and existing_state.status in ("copying", "interrupted"):
            console.print(
                f"\n[yellow]Previous session found[/yellow]\n"
                f"  Started: {existing_state.started_at}\n"
                f"  Status:  {existing_state.status}\n"
                f"  Progress: {format_size(existing_state.copied_bytes)} / "
                f"{format_size(existing_state.total_bytes)}\n"
                f"  Errors:  {existing_state.error_count}"
            )
            if auto_resume:
                resume = True
            else:
                choice = click.prompt(
                    "Resume previous session? [Y]es / [N]ew / [D]elete",
                    type=click.Choice(["y", "n", "d"], case_sensitive=False),
                    default="y",
                )
                if choice == "d":
                    delete_state(dest)
                    existing_state = None
                    resume = False
                    console.print("[dim]Previous session deleted.[/dim]")
                elif choice == "n":
                    existing_state = None
                    resume = False
                else:
                    resume = True
        else:
            resume = False
            existing_state = None

        # Phase 1: Scan
        console.print("\n[bold blue]Phase 1: Scanning source drive...[/bold blue]")
        scan_result = scan_directory(source, dest, console)
        console.print(
            f"  Found [bold]{scan_result.total_files:,}[/bold] files, "
            f"[bold]{scan_result.total_dirs:,}[/bold] directories, "
            f"[bold]{format_size(scan_result.total_size)}[/bold] total"
        )
        if scan_result.unreadable_count:
            console.print(
                f"  [yellow]{scan_result.unreadable_count} unreadable entries[/yellow]"
            )

        # Phase 2: Pre-check
        if not no_precheck:
            console.print("\n[bold blue]Phase 2: Pre-check analysis...[/bold blue]")
            issues = run_precheck(scan_result, source, dest, console)
            display_precheck_report(issues, console)

            errors = [i for i in issues if i.severity.value == "error"]
            if errors and not dry_run:
                if not click.confirm("\nErrors found. Continue anyway?", default=True):
                    console.print("[dim]Cancelled.[/dim]")
                    return

        # Phase 3: Selection
        if resume and existing_state:
            # Restore selection from previous session
            console.print("\n[bold blue]Restoring previous selection...[/bold blue]")
            _restore_selection(scan_result.root, existing_state.selected_paths, existing_state.excluded_paths)
            console.print(
                f"  Selected: [bold]{format_size(scan_result.root.selected_size)}[/bold] "
                f"({scan_result.root.selected_file_count:,} files)"
            )
            if not click.confirm("Proceed with this selection?", default=True):
                resume = False

        if not resume:
            console.print("\n[bold blue]Phase 3: Select files to copy...[/bold blue]")
            console.print("[dim]  Opening interactive selector...[/dim]\n")

            result = run_selector(scan_result.root, source, dest)
            if result is None:
                console.print("[dim]Cancelled.[/dim]")
                return

        selected_size = scan_result.root.selected_size
        selected_files = scan_result.root.selected_file_count

        if selected_files == 0:
            console.print("[yellow]No files selected. Nothing to do.[/yellow]")
            return

        console.print(
            f"\n  Will copy: [bold]{format_size(selected_size)}[/bold] "
            f"({selected_files:,} files)"
        )

        if dry_run:
            console.print("[yellow]Dry run - no files will be copied.[/yellow]")
            _show_dry_run(scan_result.root, console)
            return

        if not click.confirm("Start copying?", default=True):
            console.print("[dim]Cancelled.[/dim]")
            return

        # Phase 4: Copy
        console.print("\n[bold blue]Phase 4: Copying files...[/bold blue]")
        console.print("[dim]  Press Ctrl+C to pause safely[/dim]\n")

        # Create/update session state
        session = existing_state or create_session(source, dest)
        session.status = "copying"
        session.selected_paths = _collect_selected_paths(scan_result.root)
        session.excluded_paths = _collect_excluded_paths(scan_result.root)
        session.total_bytes = selected_size
        save_state(dest, session)

        # Set up error tracker
        tracker = ErrorTracker(error_log_path(dest))

        # Set up progress display
        progress = Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            TextColumn("[progress.percentage]{task.percentage:>3.1f}%"),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
        )

        # Detect source filesystem for rsync tuning
        modify_window = _detect_modify_window(source)

        # Create copier
        if use_rsync:
            copier: RsyncCopier | PythonCopier = RsyncCopier(
                source, dest, tracker, modify_window=modify_window,
            )
        else:
            copier = PythonCopier(source, dest, tracker)

        # Handle Ctrl+C gracefully
        def handle_sigint(sig, frame):
            console.print("\n[yellow]Interrupting... saving state...[/yellow]")
            copier.cancel()

        original_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, handle_sigint)

        try:
            with progress:
                task_id = progress.add_task("Copying", total=selected_size)

                def on_progress(p: CopyProgress):
                    progress.update(
                        task_id,
                        completed=p.bytes_copied,
                    )
                    session.copied_bytes = p.bytes_copied

                copier.on_progress = on_progress
                success = copier.copy_tree(scan_result.root)

        finally:
            signal.signal(signal.SIGINT, original_handler)

        # Update session state
        session.error_count = tracker.count
        if success:
            session.status = "completed"
        else:
            session.status = "interrupted"
        save_state(dest, session)

        # Phase 5: Report
        console.print()
        if success:
            console.print(Panel("[bold green]Copy complete![/bold green]"))
        else:
            console.print(Panel("[bold yellow]Copy interrupted - resume later with --resume[/bold yellow]"))

        display_error_report(tracker, console)

        console.print(
            f"\n[dim]State saved to: {dest}/.hdtool-state.json[/dim]"
            f"\n[dim]Error log at:   {dest}/.hdtool-errors.jsonl[/dim]"
        )


def _run_fix_timestamps(source: str, dest: str, console: Console) -> None:
    """Run the fix-timestamps mode."""
    console.print(Panel(
        f"[bold]Fix Timestamps Mode[/bold]\n\n"
        f"  Source (truth): [cyan]{source}[/cyan]\n"
        f"  Dest (to fix):  [cyan]{dest}[/cyan]\n\n"
        f"  Will update destination file timestamps to match source.\n"
        f"  No file data will be copied or modified.",
        title="hdtool --fix-timestamps",
    ))

    if not os.path.isdir(dest):
        console.print(f"[bold red]Error:[/bold red] Destination directory not found: {dest}")
        sys.exit(1)

    if not click.confirm("Proceed?", default=True):
        console.print("[dim]Cancelled.[/dim]")
        return

    progress = Progress(
        TextColumn("[bold blue]Fixing timestamps..."),
        BarColumn(bar_width=40),
        TextColumn("[progress.percentage]{task.percentage:>3.1f}%"),
        MofNCompleteColumn(),
        TextColumn("{task.fields[current]}"),
        console=console,
    )

    with progress:
        task_id = progress.add_task("fix", total=1, current="scanning...")

        def on_progress(p: CopyProgress):
            progress.update(
                task_id,
                completed=p.bytes_copied,
                total=p.total_bytes,
                current=f"[dim]{p.current_file[-50:]}[/dim]" if p.current_file else "",
            )

        result = fix_timestamps(source, dest, on_progress)

    console.print()
    console.print(Panel(
        f"[bold green]Timestamps fixed![/bold green]\n\n"
        f"  Files checked:        {result.files_checked:,}\n"
        f"  File timestamps fixed: {result.files_fixed:,}\n"
        f"  Dir timestamps fixed:  {result.dirs_fixed:,}\n"
        f"  Files not in dest:     {result.files_missing:,}\n"
        f"  Size mismatches:       {result.files_size_mismatch:,}\n"
        f"  Errors:                {len(result.errors):,}",
    ))

    if result.errors:
        console.print("\n[bold red]Errors:[/bold red]")
        for path, msg in result.errors[:50]:
            console.print(f"  {path}: {msg}")
        if len(result.errors) > 50:
            console.print(f"  ... and {len(result.errors) - 50} more")


def _detect_modify_window(source: str) -> int:
    """Detect if source filesystem needs a modify-window for rsync.

    FAT32/exFAT have 2-second timestamp resolution, so rsync should use
    --modify-window=1 to avoid re-transferring files that haven't changed.
    """
    fs_type = detect_filesystem(source)
    if fs_type in ("vfat", "exfat", "msdos"):
        return 1
    return 0


def _restore_selection(root: FileNode, selected: list[str], excluded: list[str]) -> None:
    """Restore selection state from a saved session."""
    selected_set = set(selected)
    excluded_set = set(excluded)

    def _apply(node: FileNode) -> None:
        if node.path in excluded_set:
            node.deselect_all()
        elif node.path in selected_set:
            node.selected = True
        for child in node.children:
            _apply(child)

    _apply(root)


def _collect_selected_paths(root: FileNode) -> list[str]:
    """Collect paths of all selected nodes."""
    paths: list[str] = []

    def _walk(node: FileNode) -> None:
        if node.selected:
            paths.append(node.path)
        for child in node.children:
            _walk(child)

    _walk(root)
    return paths


def _collect_excluded_paths(root: FileNode) -> list[str]:
    """Collect paths of all deselected nodes."""
    paths: list[str] = []

    def _walk(node: FileNode) -> None:
        if not node.selected:
            paths.append(node.path)
        for child in node.children:
            _walk(child)

    _walk(root)
    return paths


def _show_dry_run(root: FileNode, console: Console) -> None:
    """Show what would be copied in dry-run mode."""

    def _walk(node: FileNode, indent: int = 0) -> None:
        if not node.selected:
            return
        prefix = "  " * indent
        if node.path:  # skip root
            mark = "D" if node.is_dir else "F"
            status = node.copy_status.value.upper()
            console.print(
                f"  {prefix}[{mark}] {node.name} ({format_size(node.total_size)}) [{status}]"
            )
        for child in node.children:
            _walk(child, indent + 1)

    _walk(root)
