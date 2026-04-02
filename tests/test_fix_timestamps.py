"""Tests for the fix-timestamps feature."""

import os
import shutil

import pytest

from hdtool.copier import fix_timestamps


@pytest.fixture
def bad_copy(tmp_path):
    """Simulate a previous copy that destroyed timestamps.

    Source has files with old timestamps.
    Dest has the same files but with current timestamps (as if cp was used without -p).
    """
    src = tmp_path / "source"
    dst = tmp_path / "dest"
    src.mkdir()
    dst.mkdir()

    # Create source files with specific timestamps
    files = {
        "doc.txt": (1420070400.0, "Hello"),           # 2015-01-01
        "photo.jpg": (1277942400.0, "x" * 1024),     # 2010-07-01
        "nested/deep/file.bin": (946684800.0, "data"), # 2000-01-01
    }

    for rel_path, (mtime, content) in files.items():
        src_file = src / rel_path
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_text(content)
        os.utime(src_file, (mtime, mtime))

        # Copy to dest but with WRONG timestamps (current time)
        dst_file = dst / rel_path
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        dst_file.write_text(content)
        # dst_file keeps default mtime (now) — this simulates the bad copy

    # Also set source directory timestamps
    os.utime(src / "nested" / "deep", (946684800.0, 946684800.0))
    os.utime(src / "nested", (946684800.0, 946684800.0))

    return src, dst, files


class TestFixTimestamps:
    def test_fixes_file_timestamps(self, bad_copy):
        src, dst, files = bad_copy
        result = fix_timestamps(str(src), str(dst))

        assert result.files_fixed == 3
        assert result.errors == []

        for rel_path, (expected_mtime, _) in files.items():
            dst_mtime = os.stat(str(dst / rel_path)).st_mtime
            assert abs(dst_mtime - expected_mtime) < 2, (
                f"{rel_path}: expected mtime ~{expected_mtime}, got {dst_mtime}"
            )

    def test_fixes_directory_timestamps(self, bad_copy):
        src, dst, _ = bad_copy
        result = fix_timestamps(str(src), str(dst))

        assert result.dirs_fixed >= 1  # At least nested/deep

        src_mtime = os.stat(str(src / "nested" / "deep")).st_mtime
        dst_mtime = os.stat(str(dst / "nested" / "deep")).st_mtime
        assert abs(src_mtime - dst_mtime) < 2

    def test_skips_missing_files(self, bad_copy):
        src, dst, _ = bad_copy

        # Remove one file from dest
        os.remove(str(dst / "photo.jpg"))

        result = fix_timestamps(str(src), str(dst))
        assert result.files_missing == 1
        assert result.files_fixed == 2  # Only the other two

    def test_skips_size_mismatch(self, bad_copy):
        src, dst, _ = bad_copy

        # Modify dest file to be different size
        (dst / "doc.txt").write_text("Different content that is longer")

        result = fix_timestamps(str(src), str(dst))
        assert result.files_size_mismatch == 1
        assert result.files_fixed == 2

    def test_already_correct_timestamps(self, tmp_path):
        """If timestamps already match, nothing should be fixed."""
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        dst.mkdir()

        (src / "a.txt").write_text("hello")
        os.utime(str(src / "a.txt"), (1420070400.0, 1420070400.0))

        (dst / "a.txt").write_text("hello")
        os.utime(str(dst / "a.txt"), (1420070400.0, 1420070400.0))

        result = fix_timestamps(str(src), str(dst))
        assert result.files_checked == 1
        assert result.files_fixed == 0

    def test_progress_callback(self, bad_copy):
        src, dst, _ = bad_copy

        progress_updates = []
        result = fix_timestamps(str(src), str(dst), on_progress=lambda p: progress_updates.append(p))

        assert len(progress_updates) >= 3  # At least one per file
        assert progress_updates[-1].percentage > 99

    def test_idempotent(self, bad_copy):
        """Running fix twice should be safe — second run fixes nothing."""
        src, dst, _ = bad_copy

        result1 = fix_timestamps(str(src), str(dst))
        assert result1.files_fixed == 3

        result2 = fix_timestamps(str(src), str(dst))
        assert result2.files_fixed == 0

    def test_empty_directories(self, tmp_path):
        """Test with empty directories."""
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        dst.mkdir()

        (src / "empty_dir").mkdir()
        (dst / "empty_dir").mkdir()
        os.utime(str(src / "empty_dir"), (1420070400.0, 1420070400.0))

        result = fix_timestamps(str(src), str(dst))
        assert result.dirs_fixed >= 1

    def test_large_tree(self, tmp_path):
        """Test fix-timestamps on a larger tree."""
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        dst.mkdir()

        # Create 100 files in nested dirs
        for i in range(10):
            d = src / f"dir_{i}"
            d.mkdir()
            dd = dst / f"dir_{i}"
            dd.mkdir()
            for j in range(10):
                content = f"file_{i}_{j}"
                (d / f"f_{j}.txt").write_text(content)
                os.utime(str(d / f"f_{j}.txt"), (1420070400.0, 1420070400.0))
                (dd / f"f_{j}.txt").write_text(content)
                # Dest has wrong timestamp (current)

        result = fix_timestamps(str(src), str(dst))
        assert result.files_checked == 100
        assert result.files_fixed == 100
