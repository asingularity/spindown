"""Data models for the backup tool."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class FileType(Enum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    SPECIAL = "special"


class CopyStatus(Enum):
    NEW = "new"
    COPIED = "copied"
    MODIFIED = "modified"
    PARTIAL = "partial"


class IssueSeverity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class FileNode:
    """A file or directory in the source tree."""

    path: str  # Relative path from source root
    name: str
    file_type: FileType
    size: int = 0
    mtime: float = 0.0
    ctime: float = 0.0
    permissions: int = 0o755
    owner: str = ""
    group: str = ""
    copy_status: CopyStatus = CopyStatus.NEW
    children: list[FileNode] = field(default_factory=list)
    selected: bool = True
    error: Optional[str] = None
    symlink_target: Optional[str] = None

    @property
    def is_dir(self) -> bool:
        return self.file_type == FileType.DIRECTORY

    @property
    def total_size(self) -> int:
        if self.is_dir:
            return sum(c.total_size for c in self.children)
        return self.size

    @property
    def file_count(self) -> int:
        if self.is_dir:
            return sum(c.file_count for c in self.children)
        return 1

    @property
    def dir_count(self) -> int:
        if self.is_dir:
            return 1 + sum(c.dir_count for c in self.children)
        return 0

    @property
    def selected_size(self) -> int:
        if not self.selected:
            return 0
        if self.is_dir:
            return sum(c.selected_size for c in self.children)
        return self.size

    @property
    def selected_file_count(self) -> int:
        if not self.selected:
            return 0
        if self.is_dir:
            return sum(c.selected_file_count for c in self.children)
        return 1

    def select_all(self) -> None:
        self.selected = True
        for child in self.children:
            child.select_all()

    def deselect_all(self) -> None:
        self.selected = False
        for child in self.children:
            child.deselect_all()

    def update_parent_status(self) -> None:
        """Update selected state based on children (call on parent after child toggle)."""
        if not self.is_dir or not self.children:
            return
        all_selected = all(c.selected for c in self.children)
        any_selected = any(c.selected for c in self.children)
        self.selected = all_selected or any_selected

    def compute_copy_status(self) -> None:
        """Recompute copy_status for directories based on children."""
        if not self.is_dir:
            return
        for child in self.children:
            child.compute_copy_status()
        if not self.children:
            return
        statuses = {c.copy_status for c in self.children}
        if statuses == {CopyStatus.COPIED}:
            self.copy_status = CopyStatus.COPIED
        elif CopyStatus.COPIED in statuses or CopyStatus.PARTIAL in statuses:
            self.copy_status = CopyStatus.PARTIAL
        else:
            self.copy_status = CopyStatus.NEW


@dataclass
class Issue:
    """A problem found during pre-check."""

    path: str
    severity: IssueSeverity
    category: str
    message: str

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "severity": self.severity.value,
            "category": self.category,
            "message": self.message,
        }


@dataclass
class CopyError:
    """An error encountered during file copy."""

    path: str
    error_type: str
    message: str
    timestamp: str

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "error_type": self.error_type,
            "message": self.message,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CopyError:
        return cls(
            path=d["path"],
            error_type=d["error_type"],
            message=d["message"],
            timestamp=d["timestamp"],
        )


@dataclass
class SessionState:
    """Persistent state for resume capability."""

    session_id: str
    source_path: str
    dest_path: str
    started_at: str
    last_updated: str
    selected_paths: list[str] = field(default_factory=list)
    excluded_paths: list[str] = field(default_factory=list)
    status: str = "scanning"
    total_bytes: int = 0
    copied_bytes: int = 0
    error_count: int = 0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "source_path": self.source_path,
            "dest_path": self.dest_path,
            "started_at": self.started_at,
            "last_updated": self.last_updated,
            "selected_paths": self.selected_paths,
            "excluded_paths": self.excluded_paths,
            "status": self.status,
            "total_bytes": self.total_bytes,
            "copied_bytes": self.copied_bytes,
            "error_count": self.error_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SessionState:
        return cls(**d)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, s: str) -> SessionState:
        return cls.from_dict(json.loads(s))


@dataclass
class ScanResult:
    """Result of scanning the source directory."""

    root: FileNode
    total_files: int = 0
    total_dirs: int = 0
    total_size: int = 0
    unreadable_count: int = 0


def format_size(size_bytes: int) -> str:
    """Format byte count as human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024**2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024**3:
        return f"{size_bytes / 1024**2:.1f} MB"
    elif size_bytes < 1024**4:
        return f"{size_bytes / 1024**3:.1f} GB"
    else:
        return f"{size_bytes / 1024**4:.1f} TB"
