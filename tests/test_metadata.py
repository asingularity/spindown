"""Tests specifically for metadata preservation across both copiers."""

import os
import stat

import pytest

from hdtool.copier import PythonCopier, RsyncCopier, has_rsync
from hdtool.errors import ErrorTracker
from hdtool.scanner import scan_directory


COPIERS = ["python"]
if has_rsync():
    COPIERS.append("rsync")


def _make_copier(name, src, dst, tracker):
    if name == "rsync":
        return RsyncCopier(src, dst, tracker)
    return PythonCopier(src, dst, tracker)


@pytest.fixture(params=COPIERS)
def copier_name(request):
    return request.param


class TestTimestampPreservation:
    def test_file_mtime_preserved(self, timestamped_drive, dest_dir, copier_name):
        root = scan_directory(str(timestamped_drive), show_progress=False).root
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = _make_copier(copier_name, str(timestamped_drive), str(dest_dir), tracker)
        copier.copy_tree(root)

        for name in ("old_file.txt", "recent_file.txt", "y2k_file.txt"):
            src_mtime = os.stat(str(timestamped_drive / name)).st_mtime
            dst_mtime = os.stat(str(dest_dir / name)).st_mtime
            assert abs(src_mtime - dst_mtime) < 2, (
                f"{name}: src mtime={src_mtime}, dst mtime={dst_mtime}, "
                f"diff={abs(src_mtime - dst_mtime)}"
            )

    def test_old_timestamp_survives(self, timestamped_drive, dest_dir, copier_name):
        """Verify that a file from 2000 keeps its year-2000 timestamp."""
        root = scan_directory(str(timestamped_drive), show_progress=False).root
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = _make_copier(copier_name, str(timestamped_drive), str(dest_dir), tracker)
        copier.copy_tree(root)

        dst_mtime = os.stat(str(dest_dir / "y2k_file.txt")).st_mtime
        # 946684800 = 2000-01-01 00:00:00 UTC
        assert abs(dst_mtime - 946684800.0) < 2

    def test_dir_mtime_preserved(self, timestamped_drive, dest_dir, copier_name):
        root = scan_directory(str(timestamped_drive), show_progress=False).root
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = _make_copier(copier_name, str(timestamped_drive), str(dest_dir), tracker)
        copier.copy_tree(root)

        src_mtime = os.stat(str(timestamped_drive / "old_dir")).st_mtime
        dst_mtime = os.stat(str(dest_dir / "old_dir")).st_mtime
        assert abs(src_mtime - dst_mtime) < 2


class TestContentIntegrity:
    def test_binary_content_matches(self, basic_drive, dest_dir, copier_name):
        root = scan_directory(str(basic_drive), show_progress=False).root
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = _make_copier(copier_name, str(basic_drive), str(dest_dir), tracker)
        copier.copy_tree(root)

        src_bytes = (basic_drive / "Documents" / "report.pdf").read_bytes()
        dst_bytes = (dest_dir / "Documents" / "report.pdf").read_bytes()
        assert src_bytes == dst_bytes

    def test_text_content_matches(self, basic_drive, dest_dir, copier_name):
        root = scan_directory(str(basic_drive), show_progress=False).root
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = _make_copier(copier_name, str(basic_drive), str(dest_dir), tracker)
        copier.copy_tree(root)

        assert (dest_dir / "readme.txt").read_text() == "Hello world"

    def test_file_sizes_match(self, large_tree_drive, dest_dir, copier_name):
        root = scan_directory(str(large_tree_drive), show_progress=False).root
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = _make_copier(copier_name, str(large_tree_drive), str(dest_dir), tracker)
        copier.copy_tree(root)

        for i in range(5):
            for j in range(5):
                for k in range(10):
                    rel = f"dir_{i:02d}/sub_{j:02d}/file_{k:03d}.bin"
                    src_size = os.stat(str(large_tree_drive / rel)).st_size
                    dst_size = os.stat(str(dest_dir / rel)).st_size
                    assert src_size == dst_size, f"Size mismatch for {rel}"


class TestIdempotency:
    def test_copy_twice_no_errors(self, basic_drive, dest_dir, copier_name):
        """Running copy twice should succeed with no errors."""
        root = scan_directory(str(basic_drive), show_progress=False).root
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = _make_copier(copier_name, str(basic_drive), str(dest_dir), tracker)

        copier.copy_tree(root)

        # Run again
        root2 = scan_directory(str(basic_drive), str(dest_dir), show_progress=False).root
        tracker2 = ErrorTracker(str(dest_dir / ".errors2.jsonl"))
        copier2 = _make_copier(copier_name, str(basic_drive), str(dest_dir), tracker2)
        copier2.copy_tree(root2)

        assert not tracker2.has_errors

    def test_timestamps_stable_after_recopy(self, timestamped_drive, dest_dir, copier_name):
        """Timestamps should not change when re-copying."""
        root = scan_directory(str(timestamped_drive), show_progress=False).root
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = _make_copier(copier_name, str(timestamped_drive), str(dest_dir), tracker)
        copier.copy_tree(root)

        mtime_after_first = os.stat(str(dest_dir / "old_file.txt")).st_mtime

        # Copy again
        root2 = scan_directory(str(timestamped_drive), str(dest_dir), show_progress=False).root
        tracker2 = ErrorTracker(str(dest_dir / ".errors2.jsonl"))
        copier2 = _make_copier(copier_name, str(timestamped_drive), str(dest_dir), tracker2)
        copier2.copy_tree(root2)

        mtime_after_second = os.stat(str(dest_dir / "old_file.txt")).st_mtime
        assert abs(mtime_after_first - mtime_after_second) < 2
