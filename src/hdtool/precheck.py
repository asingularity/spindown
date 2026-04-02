"""Pre-check analysis - predicts copy problems before they happen."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from .models import FileNode, FileType, Issue, IssueSeverity, ScanResult


MAX_FILENAME_LENGTH = 255   # Per-component limit (ext4, NTFS, HFS+)
MAX_PATH_LENGTH = 4096      # Full path limit on Linux

# Characters that are legal on Linux but problematic on NTFS/FAT/HFS+
CROSS_FS_PROBLEMATIC = set('\\:*?"<>|')

# Known filesystem limitations
FS_WARNINGS: dict[str, list[str]] = {
    "vfat": [
        "FAT32: No permissions, no symlinks, no hard links",
        "FAT32: 2-second timestamp resolution (timestamps may appear slightly different)",
        "FAT32: 4 GB max file size",
        "FAT32: Limited Unicode support",
    ],
    "fuseblk": [  # NTFS via FUSE
        "NTFS: Permissions may not be preserved accurately",
        "NTFS: Some special characters in filenames may cause issues",
    ],
    "ntfs": [
        "NTFS: Permissions may not be preserved accurately",
    ],
    "ntfs3": [
        "NTFS: Permissions may not be preserved accurately",
    ],
    "hfsplus": [
        "HFS+: Resource forks will not be copied (only data fork)",
        "HFS+: Unicode normalization differences may cause filename mismatches",
    ],
    "exfat": [
        "exFAT: No permissions, no symlinks, no hard links",
        "exFAT: Limited metadata support",
    ],
}


def detect_filesystem(path: str) -> str | None:
    """Detect the filesystem type for a given path by reading /proc/mounts.

    Returns filesystem type string (e.g., 'ext4', 'vfat', 'ntfs') or None.
    """
    try:
        path = os.path.realpath(path)
        best_mount = ""
        best_fstype = None

        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount_point = parts[1]
                fs_type = parts[2]
                # Find the longest matching mount point
                if path.startswith(mount_point) and len(mount_point) > len(best_mount):
                    best_mount = mount_point
                    best_fstype = fs_type

        return best_fstype
    except OSError:
        return None


def run_precheck(
    scan_result: ScanResult,
    source: str,
    dest: str,
    console: Console | None = None,
) -> list[Issue]:
    """Analyze scanned tree for potential copy problems.

    Args:
        scan_result: Result from scanner.
        source: Source directory path.
        dest: Destination directory path.
        console: Rich console for output.

    Returns:
        List of issues found.
    """
    if console is None:
        console = Console()

    issues: list[Issue] = []

    # Check disk space
    _check_disk_space(scan_result, dest, issues)

    # Detect and warn about filesystem types
    _check_filesystems(source, dest, issues)

    # Walk the tree and check each node
    _check_node(scan_result.root, source, dest, issues)

    return issues


def _check_disk_space(scan_result: ScanResult, dest: str, issues: list[Issue]) -> None:
    """Check if destination has enough free space."""
    try:
        usage = shutil.disk_usage(dest)
        needed = scan_result.total_size
        available = usage.free

        if needed > available:
            deficit = needed - available
            issues.append(Issue(
                path=dest,
                severity=IssueSeverity.ERROR,
                category="disk_space",
                message=(
                    f"Not enough space on destination. "
                    f"Need {_fmt(needed)}, only {_fmt(available)} available "
                    f"(short by {_fmt(deficit)})"
                ),
            ))
        elif needed > available * 0.9:
            issues.append(Issue(
                path=dest,
                severity=IssueSeverity.WARNING,
                category="disk_space",
                message=(
                    f"Destination will be nearly full after copy. "
                    f"Need {_fmt(needed)}, {_fmt(available)} available"
                ),
            ))
    except OSError as e:
        issues.append(Issue(
            path=dest,
            severity=IssueSeverity.ERROR,
            category="disk_space",
            message=f"Cannot check destination disk space: {e}",
        ))


def _check_filesystems(source: str, dest: str, issues: list[Issue]) -> None:
    """Detect filesystem types and warn about known limitations."""
    for label, path in [("Source", source), ("Destination", dest)]:
        fs_type = detect_filesystem(path)
        if fs_type and fs_type in FS_WARNINGS:
            for warning in FS_WARNINGS[fs_type]:
                issues.append(Issue(
                    path=path,
                    severity=IssueSeverity.WARNING,
                    category="filesystem",
                    message=f"{label}: {warning}",
                ))
        if fs_type:
            issues.append(Issue(
                path=path,
                severity=IssueSeverity.INFO,
                category="filesystem",
                message=f"{label} filesystem: {fs_type}",
            ))


def _check_node(node: FileNode, source_root: str, dest_root: str, issues: list[Issue]) -> None:
    """Recursively check a node for potential issues."""
    abs_path = os.path.join(source_root, node.path) if node.path else source_root

    # Scan error from scanner
    if node.error:
        issues.append(Issue(
            path=node.path or "/",
            severity=IssueSeverity.ERROR,
            category="read_error",
            message=node.error,
        ))

    # Permission checks
    _check_permissions(node, abs_path, issues)

    # Symlink checks
    if node.file_type == FileType.SYMLINK:
        _check_symlink(node, abs_path, source_root, issues)

    # Special file checks
    if node.file_type == FileType.SPECIAL:
        issues.append(Issue(
            path=node.path,
            severity=IssueSeverity.WARNING,
            category="special_file",
            message="Special file (device/socket/fifo) - cannot be copied",
        ))

    # Filename checks
    _check_filename(node, issues)

    # Path length checks
    _check_path_length(node, dest_root, issues)

    # Recurse into children
    for child in node.children:
        _check_node(child, source_root, dest_root, issues)


def _check_permissions(node: FileNode, abs_path: str, issues: list[Issue]) -> None:
    """Check for permission issues that would prevent reading."""
    if node.error:
        return  # Already flagged

    try:
        if node.is_dir:
            if not os.access(abs_path, os.R_OK | os.X_OK):
                issues.append(Issue(
                    path=node.path,
                    severity=IssueSeverity.ERROR,
                    category="permission",
                    message="Directory not readable/executable - contents will be skipped",
                ))
        else:
            if not os.access(abs_path, os.R_OK):
                issues.append(Issue(
                    path=node.path,
                    severity=IssueSeverity.ERROR,
                    category="permission",
                    message="File not readable - will fail to copy",
                ))
    except OSError:
        pass  # Path may not exist anymore


def _check_symlink(
    node: FileNode, abs_path: str, source_root: str, issues: list[Issue]
) -> None:
    """Check symlink validity."""
    if not node.symlink_target:
        return

    # Check if symlink target exists
    if not os.path.exists(abs_path):
        issues.append(Issue(
            path=node.path,
            severity=IssueSeverity.WARNING,
            category="symlink",
            message=f"Dangling symlink -> {node.symlink_target}",
        ))
        return

    # Check if symlink points outside source
    try:
        real_target = os.path.realpath(abs_path)
        if not real_target.startswith(os.path.realpath(source_root)):
            issues.append(Issue(
                path=node.path,
                severity=IssueSeverity.INFO,
                category="symlink",
                message=f"Symlink points outside source -> {node.symlink_target}",
            ))
    except OSError:
        pass


def _check_filename(node: FileNode, issues: list[Issue]) -> None:
    """Check for problematic characters in filename."""
    name = node.name

    # Null bytes or control characters
    if any(ord(c) < 32 for c in name):
        issues.append(Issue(
            path=node.path,
            severity=IssueSeverity.WARNING,
            category="filename",
            message="Filename contains control characters",
        ))

    # Characters problematic on NTFS/FAT
    problem_chars = CROSS_FS_PROBLEMATIC.intersection(set(name))
    if problem_chars:
        issues.append(Issue(
            path=node.path,
            severity=IssueSeverity.INFO,
            category="filename",
            message=f"Filename contains chars problematic on some filesystems: {problem_chars}",
        ))

    # Very long filename (per-component)
    name_bytes = name.encode("utf-8", errors="replace")
    if len(name_bytes) > MAX_FILENAME_LENGTH:
        issues.append(Issue(
            path=node.path,
            severity=IssueSeverity.WARNING,
            category="filename",
            message=f"Filename is {len(name_bytes)} bytes (max {MAX_FILENAME_LENGTH} on most filesystems)",
        ))


def _check_path_length(node: FileNode, dest_root: str, issues: list[Issue]) -> None:
    """Check if the full destination path would exceed filesystem limits."""
    if not node.path:
        return
    full_dest_path = os.path.join(dest_root, node.path)
    if len(full_dest_path.encode("utf-8", errors="replace")) > MAX_PATH_LENGTH:
        issues.append(Issue(
            path=node.path,
            severity=IssueSeverity.WARNING,
            category="path_length",
            message=f"Full destination path exceeds {MAX_PATH_LENGTH} bytes",
        ))


def display_precheck_report(issues: list[Issue], console: Console | None = None) -> None:
    """Display a formatted pre-check report."""
    if console is None:
        console = Console()

    if not issues:
        console.print(Panel("[bold green]Pre-check passed - no issues found[/bold green]"))
        return

    errors = [i for i in issues if i.severity == IssueSeverity.ERROR]
    warnings = [i for i in issues if i.severity == IssueSeverity.WARNING]
    infos = [i for i in issues if i.severity == IssueSeverity.INFO]

    # Summary
    parts = []
    if errors:
        parts.append(f"[bold red]{len(errors)} errors[/bold red]")
    if warnings:
        parts.append(f"[bold yellow]{len(warnings)} warnings[/bold yellow]")
    if infos:
        parts.append(f"[dim]{len(infos)} info[/dim]")
    console.print(Panel(f"Pre-check: {', '.join(parts)}"))

    # Detailed table
    table = Table(show_header=True, header_style="bold", expand=True, show_lines=False)
    table.add_column("Sev", width=5, no_wrap=True)
    table.add_column("Category", width=12, no_wrap=True)
    table.add_column("Path", ratio=2)
    table.add_column("Issue", ratio=3)

    for issue in sorted(issues, key=lambda i: (
        {"error": 0, "warning": 1, "info": 2}[i.severity.value],
        i.category,
        i.path,
    )):
        sev_style = {
            IssueSeverity.ERROR: "[bold red]ERR[/bold red]",
            IssueSeverity.WARNING: "[yellow]WARN[/yellow]",
            IssueSeverity.INFO: "[dim]INFO[/dim]",
        }[issue.severity]

        path_display = issue.path if len(issue.path) <= 60 else "..." + issue.path[-57:]
        table.add_row(sev_style, issue.category, path_display, issue.message)

    console.print(table)


def _fmt(size_bytes: int) -> str:
    """Format bytes as human-readable."""
    from .models import format_size
    return format_size(size_bytes)
