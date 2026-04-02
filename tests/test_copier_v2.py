"""Additional copier tests: rsync flags, hardlinks, sparse files."""

import os

import pytest

from hdtool.copier import (
    PythonCopier,
    RsyncCopier,
    has_rsync,
)
from hdtool.errors import ErrorTracker
from hdtool.scanner import scan_directory


@pytest.mark.skipif(not has_rsync(), reason="rsync not available")
class TestRsyncFlags:
    def test_rsync_command_includes_hardlinks_flag(self, basic_drive, dest_dir):
        """Verify rsync is called with -H for hardlink preservation."""
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = RsyncCopier(str(basic_drive), str(dest_dir), tracker)
        cmd = copier._build_rsync_cmd("/src/", "/dst/", [])
        assert "-H" in cmd

    def test_rsync_command_includes_sparse_flag(self, basic_drive, dest_dir):
        """Verify rsync is called with -S for sparse file efficiency."""
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = RsyncCopier(str(basic_drive), str(dest_dir), tracker)
        cmd = copier._build_rsync_cmd("/src/", "/dst/", [])
        assert "-S" in cmd

    def test_modify_window_flag(self, basic_drive, dest_dir):
        """Verify --modify-window is added when specified."""
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = RsyncCopier(str(basic_drive), str(dest_dir), tracker, modify_window=1)
        cmd = copier._build_rsync_cmd("/src/", "/dst/", [])
        assert "--modify-window=1" in cmd

    def test_no_modify_window_by_default(self, basic_drive, dest_dir):
        """Verify --modify-window is NOT added when not needed."""
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = RsyncCopier(str(basic_drive), str(dest_dir), tracker)
        cmd = copier._build_rsync_cmd("/src/", "/dst/", [])
        assert not any("--modify-window" in c for c in cmd)

    def test_excludes_in_command(self, basic_drive, dest_dir):
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = RsyncCopier(str(basic_drive), str(dest_dir), tracker)
        cmd = copier._build_rsync_cmd("/src/", "/dst/", ["*.tmp", "cache/"])
        assert "--exclude" in cmd
        idx = cmd.index("--exclude")
        assert cmd[idx + 1] == "*.tmp"


@pytest.mark.skipif(not has_rsync(), reason="rsync not available")
class TestRsyncHardlinks:
    def test_hardlinks_preserved_within_directory(self, tmp_path):
        """rsync -H preserves hard link relationships within a directory subtree.

        Note: hardlinks between separate top-level items cannot be preserved
        because each gets its own rsync invocation. This is a known limitation
        and rarely matters in practice (cross-directory hardlinks on old drives
        are uncommon).
        """
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        dst.mkdir()

        # Put hardlinks in the SAME directory so they share one rsync call
        subdir = src / "data"
        subdir.mkdir()

        original = subdir / "original.txt"
        original.write_text("shared content")
        os.link(str(original), str(subdir / "hardlink.txt"))

        # Verify they share an inode in source
        assert os.stat(str(original)).st_ino == os.stat(str(subdir / "hardlink.txt")).st_ino

        root = scan_directory(str(src), show_progress=False).root
        tracker = ErrorTracker(str(dst / ".errors.jsonl"))
        copier = RsyncCopier(str(src), str(dst), tracker)
        copier.copy_tree(root)

        # Verify hard link is preserved in destination
        assert (dst / "data" / "original.txt").exists()
        assert (dst / "data" / "hardlink.txt").exists()
        assert os.stat(str(dst / "data" / "original.txt")).st_ino == os.stat(str(dst / "data" / "hardlink.txt")).st_ino


@pytest.mark.skipif(not has_rsync(), reason="rsync not available")
class TestRsyncSparseFiles:
    def test_sparse_file_copied(self, tmp_path):
        """rsync -S should handle sparse files."""
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        dst.mkdir()

        # Create a sparse file
        sparse = src / "sparse.bin"
        with open(str(sparse), "wb") as f:
            f.seek(1024 * 1024)
            f.write(b"end")

        root = scan_directory(str(src), show_progress=False).root
        tracker = ErrorTracker(str(dst / ".errors.jsonl"))
        copier = RsyncCopier(str(src), str(dst), tracker)
        copier.copy_tree(root)

        assert (dst / "sparse.bin").exists()
        # Content should match
        assert (dst / "sparse.bin").read_bytes() == sparse.read_bytes()
