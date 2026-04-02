"""Shared fixtures: virtual drives with various scenarios."""

from __future__ import annotations

import os
import time

import pytest


@pytest.fixture
def basic_drive(tmp_path):
    """A simple drive with normal files and directories."""
    src = tmp_path / "source"
    src.mkdir()

    # Top-level files
    (src / "readme.txt").write_text("Hello world")
    (src / "notes.md").write_text("# Notes\n\nSome notes here.")

    # Documents folder
    docs = src / "Documents"
    docs.mkdir()
    (docs / "report.pdf").write_bytes(os.urandom(2048))
    (docs / "letter.txt").write_text("Dear friend...")

    sub = docs / "Subfolder"
    sub.mkdir()
    (sub / "data.csv").write_text("a,b,c\n1,2,3\n")

    # Photos folder
    photos = src / "Photos"
    photos.mkdir()
    for i in range(3):
        (photos / f"img_{i:03d}.jpg").write_bytes(os.urandom(1024 * (i + 1)))

    return src


@pytest.fixture
def dest_dir(tmp_path):
    """An empty destination directory."""
    dst = tmp_path / "dest"
    dst.mkdir()
    return dst


@pytest.fixture
def basic_drive_with_dest(basic_drive, dest_dir):
    """A basic drive paired with an empty destination."""
    return basic_drive, dest_dir


@pytest.fixture
def timestamped_drive(tmp_path):
    """Drive with specific timestamps for metadata preservation testing."""
    src = tmp_path / "source"
    src.mkdir()

    files_and_times = {
        "old_file.txt": 1420070400.0,        # 2015-01-01 00:00:00 UTC
        "recent_file.txt": 1672531200.0,      # 2023-01-01 00:00:00 UTC
        "y2k_file.txt": 946684800.0,          # 2000-01-01 00:00:00 UTC
    }

    for name, mtime in files_and_times.items():
        p = src / name
        p.write_text(f"File: {name}")
        os.utime(p, (mtime, mtime))

    # Directory with specific timestamp
    d = src / "old_dir"
    d.mkdir()
    (d / "child.txt").write_text("child")
    os.utime(d / "child.txt", (1420070400.0, 1420070400.0))
    os.utime(d, (1420070400.0, 1420070400.0))

    return src


@pytest.fixture
def permission_drive(tmp_path):
    """Drive with permission issues.

    NOTE: Some permission tests only work when not running as root,
    since root can read everything.
    """
    src = tmp_path / "source"
    src.mkdir()

    # Normal readable file
    (src / "readable.txt").write_text("I can be read")

    # Unreadable file
    unreadable = src / "secret.txt"
    unreadable.write_text("Can't read me")
    unreadable.chmod(0o000)

    # Unreadable directory
    noread_dir = src / "locked_dir"
    noread_dir.mkdir()
    (noread_dir / "inside.txt").write_text("trapped")
    noread_dir.chmod(0o000)

    yield src

    # Cleanup: restore permissions so tmp_path cleanup works
    unreadable.chmod(0o644)
    noread_dir.chmod(0o755)


@pytest.fixture
def symlink_drive(tmp_path):
    """Drive with various symlink scenarios."""
    src = tmp_path / "source"
    src.mkdir()

    # Regular file
    target = src / "real_file.txt"
    target.write_text("I am real")

    # Valid symlink
    (src / "valid_link").symlink_to("real_file.txt")

    # Dangling symlink
    (src / "broken_link").symlink_to("/nonexistent/path")

    # Directory symlink
    sub = src / "subdir"
    sub.mkdir()
    (sub / "data.txt").write_text("data")
    (src / "dir_link").symlink_to("subdir")

    # Symlink pointing outside source
    (src / "external_link").symlink_to("/etc/hostname")

    return src


@pytest.fixture
def edge_case_drive(tmp_path):
    """Drive with filenames that are edge cases."""
    src = tmp_path / "source"
    src.mkdir()

    # Spaces
    (src / "file with spaces.txt").write_text("spaces")

    # Unicode
    (src / "café.txt").write_text("coffee")
    (src / "data").write_text("data")

    # Dots
    (src / ".hidden").write_text("hidden")
    (src / "..weird").write_text("weird")

    # Long filename (200 chars)
    long_name = "a" * 200 + ".txt"
    (src / long_name).write_text("long")

    # Empty file
    (src / "empty.dat").write_bytes(b"")

    # Empty directory
    (src / "empty_dir").mkdir()

    return src


@pytest.fixture
def large_tree_drive(tmp_path):
    """Drive with many nested directories and files."""
    src = tmp_path / "source"
    src.mkdir()

    # Create a tree: 5 top-level dirs, each with 5 subdirs, each with 10 files
    for i in range(5):
        d = src / f"dir_{i:02d}"
        d.mkdir()
        for j in range(5):
            sd = d / f"sub_{j:02d}"
            sd.mkdir()
            for k in range(10):
                (sd / f"file_{k:03d}.bin").write_bytes(os.urandom(128))

    return src


@pytest.fixture
def partially_copied_drive(tmp_path):
    """Source + destination where some files already exist in dest."""
    src = tmp_path / "source"
    dst = tmp_path / "dest"
    src.mkdir()
    dst.mkdir()

    # Create source files
    (src / "folder_a").mkdir()
    (src / "folder_a" / "file1.txt").write_text("file1")
    (src / "folder_a" / "file2.txt").write_text("file2")

    (src / "folder_b").mkdir()
    (src / "folder_b" / "file3.txt").write_text("file3")
    (src / "folder_b" / "file4.txt").write_text("file4")

    # Copy some to dest (simulating a partial previous copy)
    (dst / "folder_a").mkdir()
    (dst / "folder_a" / "file1.txt").write_text("file1")
    # Match the mtime so scanner recognizes it as copied
    src_stat = os.stat(src / "folder_a" / "file1.txt")
    os.utime(dst / "folder_a" / "file1.txt", (src_stat.st_atime, src_stat.st_mtime))

    return src, dst


@pytest.fixture
def modified_file_drive(tmp_path):
    """Source + destination where a file exists but has been modified."""
    src = tmp_path / "source"
    dst = tmp_path / "dest"
    src.mkdir()
    dst.mkdir()

    (src / "data.txt").write_text("new version of data")

    (dst / "data.txt").write_text("old version")
    # Set dest mtime to much older
    os.utime(dst / "data.txt", (1000000000, 1000000000))

    return src, dst
