"""End-to-end integration tests with actual file copies."""

import os
import subprocess
import sys

import pytest

from hdtool.copier import PythonCopier, RsyncCopier, has_rsync
from hdtool.errors import ErrorTracker
from hdtool.precheck import run_precheck
from hdtool.scanner import scan_directory
from hdtool.state import create_session, load_state, save_state


class TestFullPipeline:
    """Test the complete scan -> precheck -> copy -> verify flow."""

    def test_basic_end_to_end_python(self, basic_drive, dest_dir):
        # Scan
        result = scan_directory(str(basic_drive), str(dest_dir), show_progress=False)
        assert result.total_files > 0

        # Precheck
        issues = run_precheck(result, str(basic_drive), str(dest_dir))
        errors = [i for i in issues if i.severity.value == "error"]
        assert len(errors) == 0

        # Copy
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = PythonCopier(str(basic_drive), str(dest_dir), tracker)
        success = copier.copy_tree(result.root)
        assert success
        assert not tracker.has_errors

        # Verify
        for child in result.root.children:
            dst_path = dest_dir / child.path
            assert dst_path.exists(), f"Missing: {child.path}"

    @pytest.mark.skipif(not has_rsync(), reason="rsync not available")
    def test_basic_end_to_end_rsync(self, basic_drive, dest_dir):
        result = scan_directory(str(basic_drive), str(dest_dir), show_progress=False)
        issues = run_precheck(result, str(basic_drive), str(dest_dir))
        errors = [i for i in issues if i.severity.value == "error"]
        assert len(errors) == 0

        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = RsyncCopier(str(basic_drive), str(dest_dir), tracker)
        success = copier.copy_tree(result.root)
        assert success

        for child in result.root.children:
            dst_path = dest_dir / child.path
            assert dst_path.exists(), f"Missing: {child.path}"


class TestResumePipeline:
    """Test the resume flow: copy partially, then resume."""

    def test_resume_completes_copy(self, basic_drive, dest_dir):
        # First copy: only Documents
        result = scan_directory(str(basic_drive), show_progress=False)
        photos = next(c for c in result.root.children if c.name == "Photos")
        photos.deselect_all()

        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = PythonCopier(str(basic_drive), str(dest_dir), tracker)
        copier.copy_tree(result.root)

        assert (dest_dir / "Documents").exists()
        assert not (dest_dir / "Photos").exists()

        # Save state
        session = create_session(str(basic_drive), str(dest_dir))
        session.status = "interrupted"
        save_state(str(dest_dir), session)

        # "Resume": now copy everything
        result2 = scan_directory(str(basic_drive), str(dest_dir), show_progress=False)
        tracker2 = ErrorTracker(str(dest_dir / ".errors2.jsonl"))
        copier2 = PythonCopier(str(basic_drive), str(dest_dir), tracker2)
        copier2.copy_tree(result2.root)

        assert (dest_dir / "Photos").exists()
        assert (dest_dir / "Photos" / "img_000.jpg").exists()


class TestEdgeCaseIntegration:
    def test_edge_cases_copy(self, edge_case_drive, dest_dir):
        result = scan_directory(str(edge_case_drive), str(dest_dir), show_progress=False)
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = PythonCopier(str(edge_case_drive), str(dest_dir), tracker)
        success = copier.copy_tree(result.root)
        assert success

        assert (dest_dir / "file with spaces.txt").exists()
        assert (dest_dir / "café.txt").exists()
        assert (dest_dir / "empty.dat").exists()
        assert (dest_dir / "empty_dir").is_dir()

    def test_symlinks_copied(self, symlink_drive, dest_dir):
        result = scan_directory(str(symlink_drive), str(dest_dir), show_progress=False)
        tracker = ErrorTracker(str(dest_dir / ".errors.jsonl"))
        copier = PythonCopier(str(symlink_drive), str(dest_dir), tracker)
        copier.copy_tree(result.root)

        # The real file should be copied
        assert (dest_dir / "real_file.txt").exists()
        assert (dest_dir / "subdir" / "data.txt").exists()


class TestStateIntegration:
    def test_state_survives_across_sessions(self, basic_drive, dest_dir):
        session = create_session(str(basic_drive), str(dest_dir))
        session.selected_paths = ["Documents", "Photos"]
        session.excluded_paths = ["Music"]
        session.total_bytes = 999999
        session.status = "copying"
        save_state(str(dest_dir), session)

        loaded = load_state(str(dest_dir))
        assert loaded is not None
        assert loaded.selected_paths == ["Documents", "Photos"]
        assert loaded.excluded_paths == ["Music"]
        assert loaded.total_bytes == 999999
        assert loaded.status == "copying"
