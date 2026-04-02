"""File copying engine - rsync primary, pure-Python fallback."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable

from .errors import ErrorTracker
from .models import FileNode


@dataclass
class CopyProgress:
    """Current copy progress state."""

    bytes_copied: int = 0
    total_bytes: int = 0
    percentage: float = 0.0
    speed: str = ""
    eta: str = ""
    current_file: str = ""
    files_copied: int = 0
    total_files: int = 0


ProgressCallback = Callable[[CopyProgress], None]


# rsync --info=progress2 output pattern
RSYNC_PROGRESS_RE = re.compile(
    r"\s*([\d,]+)\s+(\d+)%\s+([\d.]+\S+/s)\s+(\S+)"
)

# rsync error line patterns
RSYNC_ERROR_RE = re.compile(
    r'rsync:\s+(?:\[(?:sender|receiver|generator)\]\s+)?(.+?):\s+(.+?)\s*\((\d+)\)$'
)
RSYNC_FAILED_RE = re.compile(
    r'rsync:\s+(?:send_files\s+)?failed to open "(.+?)":\s+(.+?)\s*\((\d+)\)$'
)
RSYNC_VANISHED_RE = re.compile(
    r'file has vanished: "(.+?)"'
)


def has_rsync() -> bool:
    """Check if rsync is available on the system."""
    return shutil.which("rsync") is not None


def compute_rsync_operations(root: FileNode) -> list[tuple[str, list[str]]]:
    """Compute list of (relative_path, exclude_patterns) for rsync calls.

    Walks the selection tree and finds the minimal set of subtrees to copy,
    with exclude patterns for deselected items within selected subtrees.
    """
    operations: list[tuple[str, list[str]]] = []

    for child in root.children:
        if not child.selected:
            continue

        if child.is_dir:
            excludes = _compute_excludes(child)
            operations.append((child.path, excludes))
        else:
            operations.append((child.path, []))

    return operations


def _compute_excludes(node: FileNode) -> list[str]:
    """Compute exclude patterns for partially-selected directory."""
    excludes: list[str] = []
    for child in node.children:
        if not child.selected:
            pattern = child.name + ("/" if child.is_dir else "")
            excludes.append(pattern)
        elif child.is_dir:
            sub_excludes = _compute_excludes(child)
            for ex in sub_excludes:
                excludes.append(child.name + "/" + ex)
    return excludes


class RsyncCopier:
    """Copy files using rsync with progress tracking and error collection."""

    def __init__(
        self,
        source: str,
        dest: str,
        error_tracker: ErrorTracker,
        on_progress: ProgressCallback | None = None,
        modify_window: int = 0,
    ):
        self.source = source.rstrip("/")
        self.dest = dest.rstrip("/")
        self.error_tracker = error_tracker
        self.on_progress = on_progress
        self.modify_window = modify_window
        self._cancelled = False
        self._process: subprocess.Popen | None = None

    def cancel(self) -> None:
        """Signal cancellation of current copy."""
        self._cancelled = True
        if self._process:
            self._process.send_signal(signal.SIGINT)

    def copy_tree(self, root: FileNode) -> bool:
        """Copy selected files from the tree.

        Returns True if completed without fatal errors.
        """
        operations = compute_rsync_operations(root)
        if not operations:
            return True

        total_bytes = root.selected_size
        total_files = root.selected_file_count
        cumulative_bytes = 0

        for rel_path, excludes in operations:
            if self._cancelled:
                return False

            src_path = os.path.join(self.source, rel_path)
            dst_path = os.path.join(self.dest, rel_path)

            # Determine if this is a file or directory
            is_dir = os.path.isdir(src_path)

            if is_dir:
                os.makedirs(dst_path, exist_ok=True)
                success, bytes_done = self._run_rsync(
                    src_path + "/",
                    dst_path + "/",
                    excludes,
                    total_bytes,
                    total_files,
                    cumulative_bytes,
                )
                cumulative_bytes += bytes_done
            else:
                dst_parent = os.path.dirname(dst_path)
                os.makedirs(dst_parent, exist_ok=True)
                success, bytes_done = self._run_rsync(
                    src_path,
                    dst_parent + "/",
                    [],
                    total_bytes,
                    total_files,
                    cumulative_bytes,
                )
                cumulative_bytes += bytes_done

            if not success and self._cancelled:
                return False

        return True

    def _build_rsync_cmd(self, src: str, dst: str, excludes: list[str]) -> list[str]:
        """Build the rsync command with appropriate flags."""
        cmd = [
            "rsync",
            "-a",                           # archive mode (preserves perms, times, symlinks, owner, group)
            "-H",                           # preserve hard links
            "-S",                           # handle sparse files efficiently
            "--info=progress2",             # overall progress
            "--partial",                    # keep partial files for resume
            "--partial-dir=.hdtool-partial",
            "--ignore-errors",              # continue on errors
            "--itemize-changes",            # log what changed
            "--stats",                      # show stats at end
            "--human-readable",
        ]

        if self.modify_window > 0:
            cmd.append(f"--modify-window={self.modify_window}")

        for pattern in excludes:
            cmd.extend(["--exclude", pattern])

        cmd.extend([src, dst])
        return cmd

    def _run_rsync(
        self,
        src: str,
        dst: str,
        excludes: list[str],
        total_bytes: int,
        total_files: int,
        cumulative_bytes: int,
    ) -> tuple[bool, int]:
        """Run a single rsync invocation.

        Returns (success, bytes_transferred).
        """
        cmd = self._build_rsync_cmd(src, dst, excludes)
        bytes_done = 0

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )

            # Read stdout (progress) and stderr (errors) in threads
            stderr_lines: list[str] = []

            def read_stderr():
                assert self._process is not None
                assert self._process.stderr is not None
                for raw_line in self._process.stderr:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if line:
                        stderr_lines.append(line)

            stderr_thread = threading.Thread(target=read_stderr, daemon=True)
            stderr_thread.start()

            # Read stdout byte by byte to catch \r-delimited progress updates
            assert self._process.stdout is not None
            buffer = b""
            while True:
                byte = self._process.stdout.read(1)
                if not byte:
                    break
                if byte in (b"\r", b"\n"):
                    line = buffer.decode("utf-8", errors="replace").strip()
                    if line:
                        parsed = self._parse_progress(line)
                        if parsed and self.on_progress:
                            bytes_done = parsed.bytes_copied
                            parsed.total_bytes = total_bytes
                            parsed.bytes_copied = cumulative_bytes + bytes_done
                            if total_bytes > 0:
                                parsed.percentage = (parsed.bytes_copied / total_bytes) * 100
                            self.on_progress(parsed)
                    buffer = b""
                else:
                    buffer += byte

            self._process.wait()
            stderr_thread.join(timeout=5)

            # Parse errors from stderr
            for line in stderr_lines:
                self._parse_error(line)

            return_code = self._process.returncode
            self._process = None

            # rsync exit codes: 0=success, 23=partial transfer, 24=vanished files
            return return_code in (0, 23, 24), bytes_done

        except Exception as e:
            self.error_tracker.add(src, "rsync_launch", str(e))
            self._process = None
            return False, bytes_done

    def _parse_progress(self, line: str) -> CopyProgress | None:
        """Parse an rsync --info=progress2 output line."""
        match = RSYNC_PROGRESS_RE.match(line)
        if not match:
            return None
        bytes_str, pct_str, speed, eta = match.groups()
        return CopyProgress(
            bytes_copied=int(bytes_str.replace(",", "")),
            percentage=float(pct_str),
            speed=speed,
            eta=eta,
        )

    def _parse_error(self, line: str) -> None:
        """Parse an rsync error line and add to tracker."""
        # Skip non-error lines
        if not line.startswith("rsync:") and "error" not in line.lower() and "vanished" not in line.lower():
            return

        # Vanished files (not really errors)
        match = RSYNC_VANISHED_RE.search(line)
        if match:
            self.error_tracker.add(match.group(1), "vanished", "File vanished during copy")
            return

        # Failed to open
        match = RSYNC_FAILED_RE.match(line)
        if match:
            path, msg, errno = match.groups()
            self.error_tracker.add(path, "open_failed", msg)
            return

        # General rsync errors
        match = RSYNC_ERROR_RE.match(line)
        if match:
            context, msg, errno = match.groups()
            self.error_tracker.add(context, "rsync", msg)
            return

        # Catch-all for unrecognized error lines
        if "error" in line.lower() or "failed" in line.lower():
            self.error_tracker.add("", "rsync", line)


class PythonCopier:
    """Pure-Python copier using shutil. Same interface as RsyncCopier."""

    def __init__(
        self,
        source: str,
        dest: str,
        error_tracker: ErrorTracker,
        on_progress: ProgressCallback | None = None,
    ):
        self.source = source.rstrip("/")
        self.dest = dest.rstrip("/")
        self.error_tracker = error_tracker
        self.on_progress = on_progress
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def copy_tree(self, root: FileNode) -> bool:
        """Copy selected files from the tree."""
        # Collect all files to copy
        files = self._collect_files(root)
        if not files:
            return True

        total_bytes = sum(size for _, _, size in files)
        total_files = len(files)
        copied_bytes = 0
        copied_files = 0

        for rel_path, is_dir, size in files:
            if self._cancelled:
                return False

            src = os.path.join(self.source, rel_path)
            dst = os.path.join(self.dest, rel_path)

            try:
                if is_dir:
                    os.makedirs(dst, exist_ok=True)
                    # Copy directory metadata
                    self._copy_dir_metadata(src, dst)
                else:
                    # Check if already copied (same size + mtime)
                    if self._already_copied(src, dst):
                        copied_bytes += size
                        copied_files += 1
                        if self.on_progress:
                            self.on_progress(CopyProgress(
                                bytes_copied=copied_bytes,
                                total_bytes=total_bytes,
                                percentage=(copied_bytes / total_bytes * 100) if total_bytes else 0,
                                current_file=rel_path,
                                files_copied=copied_files,
                                total_files=total_files,
                            ))
                        continue

                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    copied_bytes += size
                    copied_files += 1

                    if self.on_progress:
                        self.on_progress(CopyProgress(
                            bytes_copied=copied_bytes,
                            total_bytes=total_bytes,
                            percentage=(copied_bytes / total_bytes * 100) if total_bytes else 0,
                            current_file=rel_path,
                            files_copied=copied_files,
                            total_files=total_files,
                        ))
            except PermissionError as e:
                self.error_tracker.add(rel_path, "permission", str(e))
            except OSError as e:
                self.error_tracker.add(rel_path, "io", str(e))

        # Second pass: copy directory timestamps (must be done after all children are copied)
        self._fix_dir_timestamps(root)

        return not self._cancelled

    def _already_copied(self, src: str, dst: str) -> bool:
        """Check if file already exists at destination with matching size and mtime."""
        if not os.path.exists(dst):
            return False
        try:
            src_stat = os.stat(src)
            dst_stat = os.stat(dst)
            return (
                src_stat.st_size == dst_stat.st_size
                and abs(src_stat.st_mtime - dst_stat.st_mtime) < 2
            )
        except OSError:
            return False

    def _copy_dir_metadata(self, src: str, dst: str) -> None:
        """Copy directory metadata (timestamps, permissions)."""
        try:
            st = os.stat(src)
            os.chmod(dst, st.st_mode)
        except OSError:
            pass

    def _fix_dir_timestamps(self, node: FileNode) -> None:
        """Restore directory timestamps after all contents are copied.

        Must be done bottom-up since adding files changes dir mtime.
        """
        if not node.is_dir:
            return

        for child in node.children:
            if child.selected:
                self._fix_dir_timestamps(child)

        if not node.selected:
            return

        src = os.path.join(self.source, node.path) if node.path else self.source
        dst = os.path.join(self.dest, node.path) if node.path else self.dest

        try:
            st = os.stat(src)
            os.utime(dst, (st.st_atime, st.st_mtime))
        except OSError:
            pass

    def _collect_files(self, node: FileNode) -> list[tuple[str, bool, int]]:
        """Collect all selected files as (rel_path, is_dir, size) tuples."""
        result: list[tuple[str, bool, int]] = []
        self._walk_selected(node, result)
        return result

    def _walk_selected(
        self, node: FileNode, result: list[tuple[str, bool, int]]
    ) -> None:
        if not node.selected:
            return

        if node.is_dir:
            if node.path:  # Skip root node itself
                result.append((node.path, True, 0))
            for child in node.children:
                self._walk_selected(child, result)
        else:
            result.append((node.path, False, node.size))


# ---------------------------------------------------------------------------
# Fix-timestamps mode: repair timestamps from a previous bad copy
# ---------------------------------------------------------------------------

@dataclass
class FixTimestampsResult:
    """Result of a fix-timestamps operation."""
    files_checked: int = 0
    files_fixed: int = 0
    dirs_fixed: int = 0
    files_missing: int = 0
    files_size_mismatch: int = 0
    errors: list[tuple[str, str]] = None  # (path, error message)

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


def fix_timestamps(
    source: str,
    dest: str,
    on_progress: ProgressCallback | None = None,
) -> FixTimestampsResult:
    """Fix destination timestamps to match source without re-copying data.

    Walks source and destination in parallel. For each file that exists in both
    with matching size but different mtime, copies the timestamp from source to
    destination. Fixes directory timestamps bottom-up.

    Args:
        source: Source directory path (the truth).
        dest: Destination directory path (to fix).
        on_progress: Optional progress callback.

    Returns:
        FixTimestampsResult with counts of what was done.
    """
    source = os.path.abspath(source).rstrip("/")
    dest = os.path.abspath(dest).rstrip("/")
    result = FixTimestampsResult()

    # First pass: count total files for progress
    total_files = 0
    for _, _, files in os.walk(source):
        total_files += len(files)

    # Second pass: fix file timestamps
    checked = 0
    for dirpath, dirnames, filenames in os.walk(source):
        rel_dir = os.path.relpath(dirpath, source)
        if rel_dir == ".":
            rel_dir = ""

        for fname in filenames:
            rel_path = os.path.join(rel_dir, fname) if rel_dir else fname
            src_file = os.path.join(source, rel_path)
            dst_file = os.path.join(dest, rel_path)

            checked += 1
            result.files_checked += 1

            if on_progress and total_files > 0:
                on_progress(CopyProgress(
                    bytes_copied=checked,
                    total_bytes=total_files,
                    percentage=(checked / total_files) * 100,
                    current_file=rel_path,
                    files_copied=checked,
                    total_files=total_files,
                ))

            # Skip symlinks
            if os.path.islink(src_file):
                continue

            if not os.path.exists(dst_file):
                result.files_missing += 1
                continue

            try:
                src_stat = os.stat(src_file)
                dst_stat = os.stat(dst_file)

                # Only fix if size matches (same file content)
                if src_stat.st_size != dst_stat.st_size:
                    result.files_size_mismatch += 1
                    continue

                # Fix if timestamp differs
                if abs(src_stat.st_mtime - dst_stat.st_mtime) >= 2:
                    os.utime(dst_file, (src_stat.st_atime, src_stat.st_mtime))
                    result.files_fixed += 1
            except OSError as e:
                result.errors.append((rel_path, str(e)))

    # Third pass: fix directory timestamps bottom-up
    # Collect all directories, then process deepest first
    all_dirs: list[str] = []
    for dirpath, dirnames, _ in os.walk(source):
        rel_dir = os.path.relpath(dirpath, source)
        all_dirs.append(rel_dir)

    # Sort by depth (deepest first) for bottom-up processing
    all_dirs.sort(key=lambda d: d.count(os.sep), reverse=True)

    for rel_dir in all_dirs:
        src_dir = os.path.join(source, rel_dir) if rel_dir != "." else source
        dst_dir = os.path.join(dest, rel_dir) if rel_dir != "." else dest

        if not os.path.isdir(dst_dir):
            continue

        try:
            src_stat = os.stat(src_dir)
            dst_stat = os.stat(dst_dir)
            if abs(src_stat.st_mtime - dst_stat.st_mtime) >= 2:
                os.utime(dst_dir, (src_stat.st_atime, src_stat.st_mtime))
                result.dirs_fixed += 1
        except OSError as e:
            result.errors.append((rel_dir, str(e)))

    return result
