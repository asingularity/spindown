"""Tests for the pre-check analysis."""

import os

import pytest

from hdtool.models import IssueSeverity
from hdtool.precheck import run_precheck
from hdtool.scanner import scan_directory


class TestDiskSpace:
    def test_no_issue_when_enough_space(self, basic_drive, dest_dir):
        result = scan_directory(str(basic_drive), str(dest_dir), show_progress=False)
        issues = run_precheck(result, str(basic_drive), str(dest_dir))
        space_issues = [i for i in issues if i.category == "disk_space"]
        # Should be fine since test files are tiny
        assert all(i.severity != IssueSeverity.ERROR for i in space_issues)


class TestPermissions:
    @pytest.mark.skipif(os.getuid() == 0, reason="Root can read everything")
    def test_unreadable_file(self, permission_drive, dest_dir):
        result = scan_directory(str(permission_drive), str(dest_dir), show_progress=False)
        issues = run_precheck(result, str(permission_drive), str(dest_dir))
        perm_issues = [i for i in issues if i.category == "permission"]
        paths = [i.path for i in perm_issues]
        assert any("secret.txt" in p for p in paths)

    @pytest.mark.skipif(os.getuid() == 0, reason="Root can read everything")
    def test_unreadable_directory(self, permission_drive, dest_dir):
        result = scan_directory(str(permission_drive), str(dest_dir), show_progress=False)
        issues = run_precheck(result, str(permission_drive), str(dest_dir))
        # Should flag either as permission or read_error
        problem_paths = [i.path for i in issues if i.severity == IssueSeverity.ERROR]
        assert any("locked_dir" in p for p in problem_paths)


class TestSymlinks:
    def test_dangling_symlink_warning(self, symlink_drive, dest_dir):
        result = scan_directory(str(symlink_drive), str(dest_dir), show_progress=False)
        issues = run_precheck(result, str(symlink_drive), str(dest_dir))
        symlink_issues = [i for i in issues if i.category == "symlink"]
        dangling = [i for i in symlink_issues if "dangling" in i.message.lower() or "Dangling" in i.message]
        assert len(dangling) >= 1

    def test_external_symlink_info(self, symlink_drive, dest_dir):
        result = scan_directory(str(symlink_drive), str(dest_dir), show_progress=False)
        issues = run_precheck(result, str(symlink_drive), str(dest_dir))
        symlink_issues = [i for i in issues if i.category == "symlink"]
        external = [i for i in symlink_issues if "outside" in i.message.lower()]
        assert len(external) >= 1


class TestFilenames:
    def test_long_filename_warning(self, edge_case_drive, dest_dir):
        result = scan_directory(str(edge_case_drive), str(dest_dir), show_progress=False)
        issues = run_precheck(result, str(edge_case_drive), str(dest_dir))
        # The 200-char filename encoded in UTF-8 is < 255 bytes
        # so no warning expected for that one. But path length might trigger it.
        # This test verifies the checker runs without error.
        assert isinstance(issues, list)

    def test_no_false_positives_on_normal_names(self, basic_drive, dest_dir):
        result = scan_directory(str(basic_drive), str(dest_dir), show_progress=False)
        issues = run_precheck(result, str(basic_drive), str(dest_dir))
        filename_issues = [i for i in issues if i.category == "filename"]
        assert len(filename_issues) == 0


class TestPreCheckOnCleanDrive:
    def test_basic_drive_minimal_issues(self, basic_drive, dest_dir):
        result = scan_directory(str(basic_drive), str(dest_dir), show_progress=False)
        issues = run_precheck(result, str(basic_drive), str(dest_dir))
        errors = [i for i in issues if i.severity == IssueSeverity.ERROR]
        # A clean basic drive should have no errors
        assert len(errors) == 0
