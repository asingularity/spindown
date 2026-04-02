"""Directory scanner - walks source tree and compares with destination."""

from __future__ import annotations

import grp
import os
import pwd
import stat
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn

from .models import CopyStatus, FileNode, FileType, ScanResult


def _get_owner(st: os.stat_result) -> str:
    try:
        return pwd.getpwuid(st.st_uid).pw_name
    except (KeyError, OverflowError):
        return str(st.st_uid)


def _get_group(st: os.stat_result) -> str:
    try:
        return grp.getgrgid(st.st_gid).gr_name
    except (KeyError, OverflowError):
        return str(st.st_gid)


def _file_type_from_stat(st: os.stat_result) -> FileType:
    """Determine file type from stat result. Symlinks must be handled before calling."""
    mode = st.st_mode
    if stat.S_ISDIR(mode):
        return FileType.DIRECTORY
    if stat.S_ISREG(mode):
        return FileType.FILE
    return FileType.SPECIAL


def _compare_file(source_path: str, dest_path: str) -> CopyStatus:
    """Compare a source file with its destination counterpart."""
    if not os.path.exists(dest_path):
        return CopyStatus.NEW
    try:
        src_stat = os.stat(source_path)
        dst_stat = os.stat(dest_path)
        if src_stat.st_size == dst_stat.st_size and abs(src_stat.st_mtime - dst_stat.st_mtime) < 2:
            return CopyStatus.COPIED
        return CopyStatus.MODIFIED
    except OSError:
        return CopyStatus.NEW


def scan_directory(
    source: str,
    dest: str | None = None,
    console: Console | None = None,
    show_progress: bool = True,
) -> ScanResult:
    """Scan source directory and optionally compare with destination.

    Args:
        source: Source directory path.
        dest: Destination directory path (for copy status comparison).
        console: Rich console for output.
        show_progress: Whether to show progress spinner.

    Returns:
        ScanResult with the complete file tree.
    """
    if console is None:
        console = Console()

    source = os.path.abspath(source)
    if dest:
        dest = os.path.abspath(dest)

    total_files = 0
    total_dirs = 0
    total_size = 0
    unreadable = 0

    def _scan_node(abs_path: str, rel_path: str, progress=None, task=None) -> FileNode:
        nonlocal total_files, total_dirs, total_size, unreadable

        name = os.path.basename(abs_path) or abs_path

        # Handle symlinks
        if os.path.islink(abs_path):
            try:
                link_target = os.readlink(abs_path)
            except OSError:
                link_target = "<unreadable>"

            try:
                st = os.lstat(abs_path)
            except OSError:
                st = None

            node = FileNode(
                path=rel_path,
                name=name,
                file_type=FileType.SYMLINK,
                size=0,
                mtime=st.st_mtime if st else 0,
                ctime=st.st_ctime if st else 0,
                permissions=st.st_mode if st else 0,
                owner=_get_owner(st) if st else "",
                group=_get_group(st) if st else "",
                symlink_target=link_target,
            )
            if dest:
                dest_path = os.path.join(dest, rel_path)
                node.copy_status = _compare_file(abs_path, dest_path)
            total_files += 1
            return node

        # Stat the path
        try:
            st = os.lstat(abs_path)
        except OSError as e:
            unreadable += 1
            return FileNode(
                path=rel_path,
                name=name,
                file_type=FileType.FILE,
                error=str(e),
            )

        ft = _file_type_from_stat(st)

        if ft == FileType.SPECIAL:
            total_files += 1
            return FileNode(
                path=rel_path,
                name=name,
                file_type=FileType.SPECIAL,
                mtime=st.st_mtime,
                ctime=st.st_ctime,
                permissions=st.st_mode,
                owner=_get_owner(st),
                group=_get_group(st),
            )

        if ft == FileType.FILE:
            total_files += 1
            total_size += st.st_size
            node = FileNode(
                path=rel_path,
                name=name,
                file_type=FileType.FILE,
                size=st.st_size,
                mtime=st.st_mtime,
                ctime=st.st_ctime,
                permissions=st.st_mode,
                owner=_get_owner(st),
                group=_get_group(st),
            )
            if dest:
                dest_path = os.path.join(dest, rel_path)
                node.copy_status = _compare_file(abs_path, dest_path)
            if progress and task is not None:
                progress.update(task, advance=1)
            return node

        # Directory
        total_dirs += 1
        children: list[FileNode] = []

        try:
            entries = sorted(os.scandir(abs_path), key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
        except PermissionError as e:
            unreadable += 1
            return FileNode(
                path=rel_path,
                name=name,
                file_type=FileType.DIRECTORY,
                mtime=st.st_mtime,
                ctime=st.st_ctime,
                permissions=st.st_mode,
                owner=_get_owner(st),
                group=_get_group(st),
                error=f"Permission denied: {e}",
            )
        except OSError as e:
            unreadable += 1
            return FileNode(
                path=rel_path,
                name=name,
                file_type=FileType.DIRECTORY,
                error=str(e),
            )

        for entry in entries:
            child_rel = os.path.join(rel_path, entry.name) if rel_path else entry.name
            child_node = _scan_node(entry.path, child_rel, progress, task)
            children.append(child_node)

        node = FileNode(
            path=rel_path,
            name=name,
            file_type=FileType.DIRECTORY,
            size=sum(c.total_size for c in children),
            mtime=st.st_mtime,
            ctime=st.st_ctime,
            permissions=st.st_mode,
            owner=_get_owner(st),
            group=_get_group(st),
            children=children,
        )

        if dest:
            node.compute_copy_status()

        if progress and task is not None:
            progress.update(task, advance=1)

        return node

    if show_progress:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]Scanning source drive..."),
            TextColumn("{task.fields[status]}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("scan", status="")

            def update_status():
                progress.update(task, status=f"[dim]{total_files} files, {total_dirs} dirs found[/dim]")

            # Patch to update status display during scan
            orig_scan = _scan_node

            def _scan_with_status(abs_path, rel_path, prog=None, tsk=None):
                result = orig_scan(abs_path, rel_path, prog, tsk)
                update_status()
                return result

            root = _scan_with_status(source, "", progress, task)
    else:
        root = _scan_node(source, "")

    return ScanResult(
        root=root,
        total_files=total_files,
        total_dirs=total_dirs,
        total_size=total_size,
        unreadable_count=unreadable,
    )
