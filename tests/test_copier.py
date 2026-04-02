"""Tests for the copy engines (rsync and Python fallback)."""

import os
import time

import pytest

from hdtool.copier import (
    PythonCopier,
    RsyncCopier,
    compute_rsync_operations,
    has_rsync,
)
from hdtool.errors import ErrorTracker
from hdtool.models import CopyStatus, FileNode, FileType
from hdtool.scanner import scan_directory


def _make_tree(src_path: str) -> FileNode:
    """Scan source and return the root node."""
    return scan_directory(src_path, show_progress=False).root


class TestComputeRsyncOperations:
    def test_all_selected(self, basic_drive):
        root = _make_tree(str(basic_drive))
        ops = compute_rsync_operations(root)
        # Should have one operation per top-level child
        assert len(ops) > 0

    def test_nothing_selected(self, basic_drive):
        root = _make_tree(str(basic_drive))
        root.deselect_all()
        ops = compute_rsync_operations(root)
        assert len(ops) == 0

    def test_partial_selection_has_excludes(self, basic_drive):
        root = _make_tree(str(basic_drive))
        # Deselect one child of Documents
        docs = next(c for c in root.children if c.name == "Documents")
        if docs.children:
            docs.children[0].deselect_all()
            ops = compute_rsync_operations(root)
            doc_op = next((p, e) for p, e in ops if "Documents" in p)
            # Should have at least one exclude
            assert len(doc_op[1]) >= 1

    def test_deselected_top_level_excluded(self, basic_drive):
        root = _make_tree(str(basic_drive))
        # Deselect Photos entirely
        photos = next(c for c in root.children if c.name == "Photos")
        photos.deselect_all()
        ops = compute_rsync_operations(root)
        op_paths = [p for p, _ in ops]
        assert not any("Photos" in p for p in op_paths)


class TestPythonCopier:
    def test_copies_all_files(self, basic_drive, dest_dir):
        root = _make_tree(str(basic_drive))
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = PythonCopier(str(basic_drive), str(dest_dir), tracker)

        success = copier.copy_tree(root)
        assert success
        assert not tracker.has_errors

        # Verify files exist
        assert (dest_dir / "readme.txt").exists()
        assert (dest_dir / "Documents" / "report.pdf").exists()
        assert (dest_dir / "Photos" / "img_000.jpg").exists()

    def test_preserves_content(self, basic_drive, dest_dir):
        root = _make_tree(str(basic_drive))
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = PythonCopier(str(basic_drive), str(dest_dir), tracker)
        copier.copy_tree(root)

        assert (dest_dir / "readme.txt").read_text() == "Hello world"
        assert (dest_dir / "Documents" / "letter.txt").read_text() == "Dear friend..."

    def test_preserves_file_timestamps(self, timestamped_drive, dest_dir):
        root = _make_tree(str(timestamped_drive))
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = PythonCopier(str(timestamped_drive), str(dest_dir), tracker)
        copier.copy_tree(root)

        src_mtime = os.stat(str(timestamped_drive / "old_file.txt")).st_mtime
        dst_mtime = os.stat(str(dest_dir / "old_file.txt")).st_mtime
        assert abs(src_mtime - dst_mtime) < 2

    def test_preserves_directory_timestamps(self, timestamped_drive, dest_dir):
        root = _make_tree(str(timestamped_drive))
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = PythonCopier(str(timestamped_drive), str(dest_dir), tracker)
        copier.copy_tree(root)

        src_mtime = os.stat(str(timestamped_drive / "old_dir")).st_mtime
        dst_mtime = os.stat(str(dest_dir / "old_dir")).st_mtime
        assert abs(src_mtime - dst_mtime) < 2

    def test_skips_already_copied(self, partially_copied_drive):
        src, dst = partially_copied_drive
        root = scan_directory(str(src), str(dst), show_progress=False).root
        tracker = ErrorTracker(str(dst / ".errors.jsonl"))

        progress_files = []

        def on_progress(p):
            if p.current_file:
                progress_files.append(p.current_file)

        copier = PythonCopier(str(src), str(dst), tracker, on_progress)
        copier.copy_tree(root)

        # file1.txt was already copied - should be skipped but still counted
        assert (dst / "folder_a" / "file1.txt").exists()
        assert (dst / "folder_a" / "file2.txt").exists()
        assert (dst / "folder_b" / "file3.txt").exists()

    def test_partial_selection(self, basic_drive, dest_dir):
        root = _make_tree(str(basic_drive))
        # Deselect Photos
        photos = next(c for c in root.children if c.name == "Photos")
        photos.deselect_all()

        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = PythonCopier(str(basic_drive), str(dest_dir), tracker)
        copier.copy_tree(root)

        assert (dest_dir / "Documents").exists()
        assert not (dest_dir / "Photos").exists()

    def test_handles_empty_files(self, edge_case_drive, dest_dir):
        root = _make_tree(str(edge_case_drive))
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = PythonCopier(str(edge_case_drive), str(dest_dir), tracker)
        copier.copy_tree(root)

        assert (dest_dir / "empty.dat").exists()
        assert (dest_dir / "empty.dat").stat().st_size == 0

    def test_handles_unicode_filenames(self, edge_case_drive, dest_dir):
        root = _make_tree(str(edge_case_drive))
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = PythonCopier(str(edge_case_drive), str(dest_dir), tracker)
        copier.copy_tree(root)

        assert (dest_dir / "café.txt").exists()

    def test_handles_spaces_in_filenames(self, edge_case_drive, dest_dir):
        root = _make_tree(str(edge_case_drive))
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = PythonCopier(str(edge_case_drive), str(dest_dir), tracker)
        copier.copy_tree(root)

        assert (dest_dir / "file with spaces.txt").exists()

    def test_cancel(self, large_tree_drive, dest_dir):
        root = _make_tree(str(large_tree_drive))
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = PythonCopier(str(large_tree_drive), str(dest_dir), tracker)

        # Cancel immediately
        copier.cancel()
        success = copier.copy_tree(root)
        assert not success

    def test_progress_callback(self, basic_drive, dest_dir):
        root = _make_tree(str(basic_drive))
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))

        progress_updates = []

        def on_progress(p):
            progress_updates.append(p)

        copier = PythonCopier(str(basic_drive), str(dest_dir), tracker, on_progress)
        copier.copy_tree(root)

        assert len(progress_updates) > 0
        # Last update should be 100%
        assert progress_updates[-1].percentage > 99

    def test_copies_large_tree(self, large_tree_drive, dest_dir):
        root = _make_tree(str(large_tree_drive))
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = PythonCopier(str(large_tree_drive), str(dest_dir), tracker)

        success = copier.copy_tree(root)
        assert success
        assert not tracker.has_errors

        # Verify structure
        for i in range(5):
            for j in range(5):
                for k in range(10):
                    assert (dest_dir / f"dir_{i:02d}" / f"sub_{j:02d}" / f"file_{k:03d}.bin").exists()


@pytest.mark.skipif(not has_rsync(), reason="rsync not available")
class TestRsyncCopier:
    def test_copies_all_files(self, basic_drive, dest_dir):
        root = _make_tree(str(basic_drive))
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = RsyncCopier(str(basic_drive), str(dest_dir), tracker)

        success = copier.copy_tree(root)
        assert success

        assert (dest_dir / "readme.txt").exists()
        assert (dest_dir / "Documents" / "report.pdf").exists()

    def test_preserves_content(self, basic_drive, dest_dir):
        root = _make_tree(str(basic_drive))
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = RsyncCopier(str(basic_drive), str(dest_dir), tracker)
        copier.copy_tree(root)

        assert (dest_dir / "readme.txt").read_text() == "Hello world"

    def test_preserves_timestamps(self, timestamped_drive, dest_dir):
        root = _make_tree(str(timestamped_drive))
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = RsyncCopier(str(timestamped_drive), str(dest_dir), tracker)
        copier.copy_tree(root)

        src_mtime = os.stat(str(timestamped_drive / "old_file.txt")).st_mtime
        dst_mtime = os.stat(str(dest_dir / "old_file.txt")).st_mtime
        assert abs(src_mtime - dst_mtime) < 2

    def test_partial_selection(self, basic_drive, dest_dir):
        root = _make_tree(str(basic_drive))
        photos = next(c for c in root.children if c.name == "Photos")
        photos.deselect_all()

        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = RsyncCopier(str(basic_drive), str(dest_dir), tracker)
        copier.copy_tree(root)

        assert (dest_dir / "Documents").exists()
        assert not (dest_dir / "Photos").exists()

    def test_resume_skips_existing(self, partially_copied_drive):
        src, dst = partially_copied_drive
        root = scan_directory(str(src), str(dst), show_progress=False).root
        tracker = ErrorTracker(str(dst / ".errors.jsonl"))
        copier = RsyncCopier(str(src), str(dst), tracker)
        copier.copy_tree(root)

        # All files should now exist
        assert (dst / "folder_a" / "file1.txt").exists()
        assert (dst / "folder_a" / "file2.txt").exists()
        assert (dst / "folder_b" / "file3.txt").exists()
        assert (dst / "folder_b" / "file4.txt").exists()
