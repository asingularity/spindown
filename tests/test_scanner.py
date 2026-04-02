"""Tests for the directory scanner."""

import os

from hdtool.models import CopyStatus, FileType
from hdtool.scanner import scan_directory


class TestBasicScan:
    def test_scan_counts(self, basic_drive):
        result = scan_directory(str(basic_drive), show_progress=False)
        assert result.total_files > 0
        assert result.total_dirs > 0
        assert result.total_size > 0

    def test_scan_root_is_directory(self, basic_drive):
        result = scan_directory(str(basic_drive), show_progress=False)
        assert result.root.file_type == FileType.DIRECTORY

    def test_scan_finds_top_level_entries(self, basic_drive):
        result = scan_directory(str(basic_drive), show_progress=False)
        names = {c.name for c in result.root.children}
        assert "readme.txt" in names
        assert "Documents" in names
        assert "Photos" in names

    def test_scan_nested_directory(self, basic_drive):
        result = scan_directory(str(basic_drive), show_progress=False)
        docs = next(c for c in result.root.children if c.name == "Documents")
        assert docs.file_type == FileType.DIRECTORY
        child_names = {c.name for c in docs.children}
        assert "report.pdf" in child_names
        assert "Subfolder" in child_names

    def test_scan_file_size(self, basic_drive):
        result = scan_directory(str(basic_drive), show_progress=False)
        readme = next(c for c in result.root.children if c.name == "readme.txt")
        assert readme.size == len("Hello world")

    def test_scan_preserves_relative_paths(self, basic_drive):
        result = scan_directory(str(basic_drive), show_progress=False)
        docs = next(c for c in result.root.children if c.name == "Documents")
        assert docs.path == "Documents"
        report = next(c for c in docs.children if c.name == "report.pdf")
        assert report.path == os.path.join("Documents", "report.pdf")

    def test_directories_sorted_first(self, basic_drive):
        result = scan_directory(str(basic_drive), show_progress=False)
        children = result.root.children
        # Directories should come before files
        first_file_idx = None
        last_dir_idx = None
        for i, c in enumerate(children):
            if c.is_dir and (last_dir_idx is None or i > last_dir_idx):
                last_dir_idx = i
            if not c.is_dir and first_file_idx is None:
                first_file_idx = i
        if last_dir_idx is not None and first_file_idx is not None:
            assert last_dir_idx < first_file_idx


class TestCopyStatusDetection:
    def test_all_new_without_dest(self, basic_drive):
        result = scan_directory(str(basic_drive), show_progress=False)
        readme = next(c for c in result.root.children if c.name == "readme.txt")
        assert readme.copy_status == CopyStatus.NEW

    def test_detects_copied_file(self, partially_copied_drive):
        src, dst = partially_copied_drive
        result = scan_directory(str(src), str(dst), show_progress=False)
        folder_a = next(c for c in result.root.children if c.name == "folder_a")
        file1 = next(c for c in folder_a.children if c.name == "file1.txt")
        assert file1.copy_status == CopyStatus.COPIED

    def test_detects_new_file(self, partially_copied_drive):
        src, dst = partially_copied_drive
        result = scan_directory(str(src), str(dst), show_progress=False)
        folder_a = next(c for c in result.root.children if c.name == "folder_a")
        file2 = next(c for c in folder_a.children if c.name == "file2.txt")
        assert file2.copy_status == CopyStatus.NEW

    def test_detects_modified_file(self, modified_file_drive):
        src, dst = modified_file_drive
        result = scan_directory(str(src), str(dst), show_progress=False)
        data = next(c for c in result.root.children if c.name == "data.txt")
        assert data.copy_status == CopyStatus.MODIFIED

    def test_partial_directory_status(self, partially_copied_drive):
        src, dst = partially_copied_drive
        result = scan_directory(str(src), str(dst), show_progress=False)
        folder_a = next(c for c in result.root.children if c.name == "folder_a")
        assert folder_a.copy_status == CopyStatus.PARTIAL


class TestSymlinks:
    def test_detects_symlinks(self, symlink_drive):
        result = scan_directory(str(symlink_drive), show_progress=False)
        names_types = {c.name: c.file_type for c in result.root.children}
        assert names_types["valid_link"] == FileType.SYMLINK

    def test_symlink_target(self, symlink_drive):
        result = scan_directory(str(symlink_drive), show_progress=False)
        link = next(c for c in result.root.children if c.name == "valid_link")
        assert link.symlink_target == "real_file.txt"

    def test_dangling_symlink(self, symlink_drive):
        result = scan_directory(str(symlink_drive), show_progress=False)
        link = next(c for c in result.root.children if c.name == "broken_link")
        assert link.file_type == FileType.SYMLINK
        assert link.symlink_target == "/nonexistent/path"


class TestEdgeCases:
    def test_empty_file(self, edge_case_drive):
        result = scan_directory(str(edge_case_drive), show_progress=False)
        empty = next(c for c in result.root.children if c.name == "empty.dat")
        assert empty.size == 0

    def test_empty_directory(self, edge_case_drive):
        result = scan_directory(str(edge_case_drive), show_progress=False)
        empty_dir = next(c for c in result.root.children if c.name == "empty_dir")
        assert empty_dir.is_dir
        assert len(empty_dir.children) == 0

    def test_hidden_files(self, edge_case_drive):
        result = scan_directory(str(edge_case_drive), show_progress=False)
        names = {c.name for c in result.root.children}
        assert ".hidden" in names

    def test_unicode_filename(self, edge_case_drive):
        result = scan_directory(str(edge_case_drive), show_progress=False)
        names = {c.name for c in result.root.children}
        assert "café.txt" in names

    def test_spaces_in_filename(self, edge_case_drive):
        result = scan_directory(str(edge_case_drive), show_progress=False)
        names = {c.name for c in result.root.children}
        assert "file with spaces.txt" in names


class TestLargeTree:
    def test_scan_counts_large_tree(self, large_tree_drive):
        result = scan_directory(str(large_tree_drive), show_progress=False)
        # 5 * 5 * 10 = 250 files
        assert result.total_files == 250
        # 5 top + 5*5 sub = 30 dirs (including root counted once)
        assert result.total_dirs >= 30

    def test_total_size_matches(self, large_tree_drive):
        result = scan_directory(str(large_tree_drive), show_progress=False)
        # Each file is 128 bytes, 250 files
        assert result.total_size == 250 * 128
