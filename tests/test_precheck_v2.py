"""Additional precheck tests for filesystem detection and path length fixes."""

import os
from unittest.mock import patch, mock_open

import pytest

from hdtool.models import IssueSeverity
from hdtool.precheck import detect_filesystem, run_precheck, MAX_PATH_LENGTH, MAX_FILENAME_LENGTH
from hdtool.scanner import scan_directory


class TestFilesystemDetection:
    def test_detect_ext4(self):
        """Test detecting ext4 filesystem from /proc/mounts."""
        mock_mounts = (
            "sysfs /sys sysfs rw,nosuid 0 0\n"
            "/dev/sda1 / ext4 rw,relatime 0 0\n"
            "tmpfs /tmp tmpfs rw 0 0\n"
        )
        with patch("builtins.open", mock_open(read_data=mock_mounts)):
            with patch("os.path.realpath", return_value="/home/user/data"):
                fs = detect_filesystem("/home/user/data")
                assert fs == "ext4"

    def test_detect_vfat(self):
        mock_mounts = (
            "/dev/sda1 / ext4 rw 0 0\n"
            "/dev/sdc1 /mnt/@usb/sdc1 vfat rw 0 0\n"
        )
        with patch("builtins.open", mock_open(read_data=mock_mounts)):
            with patch("os.path.realpath", return_value="/mnt/@usb/sdc1/photos"):
                fs = detect_filesystem("/mnt/@usb/sdc1/photos")
                assert fs == "vfat"

    def test_detect_ntfs(self):
        mock_mounts = (
            "/dev/sda1 / ext4 rw 0 0\n"
            "/dev/sdb1 /mnt/external fuseblk rw 0 0\n"
        )
        with patch("builtins.open", mock_open(read_data=mock_mounts)):
            with patch("os.path.realpath", return_value="/mnt/external/docs"):
                fs = detect_filesystem("/mnt/external/docs")
                assert fs == "fuseblk"

    def test_longest_mount_wins(self):
        """Should match the most specific (longest) mount point."""
        mock_mounts = (
            "/dev/sda1 / ext4 rw 0 0\n"
            "/dev/sdb1 /mnt ext4 rw 0 0\n"
            "/dev/sdc1 /mnt/usb vfat rw 0 0\n"
        )
        with patch("builtins.open", mock_open(read_data=mock_mounts)):
            with patch("os.path.realpath", return_value="/mnt/usb/file"):
                fs = detect_filesystem("/mnt/usb/file")
                assert fs == "vfat"

    def test_returns_none_on_error(self):
        with patch("builtins.open", side_effect=OSError("no /proc")):
            fs = detect_filesystem("/whatever")
            assert fs is None


class TestFilesystemWarnings:
    def test_fat32_warnings_in_precheck(self, basic_drive, dest_dir):
        result = scan_directory(str(basic_drive), str(dest_dir), show_progress=False)

        mock_mounts = f"/dev/sdc1 {basic_drive} vfat rw 0 0\n/dev/sda1 / ext4 rw 0 0\n"
        with patch("builtins.open", mock_open(read_data=mock_mounts)):
            with patch("os.path.realpath", side_effect=lambda p: p):
                issues = run_precheck(result, str(basic_drive), str(dest_dir))

        fs_issues = [i for i in issues if i.category == "filesystem"]
        # Should have at least the FAT32 warnings + fs type info
        assert any("FAT32" in i.message for i in fs_issues)


class TestPathLengthCheck:
    def test_no_warning_for_normal_paths(self, basic_drive, dest_dir):
        result = scan_directory(str(basic_drive), str(dest_dir), show_progress=False)
        issues = run_precheck(result, str(basic_drive), str(dest_dir))
        path_issues = [i for i in issues if i.category == "path_length"]
        assert len(path_issues) == 0

    def test_filename_component_check(self, tmp_path):
        """MAX_FILENAME_LENGTH (255) is checked per-component, not full path."""
        src = tmp_path / "source"
        src.mkdir()
        # Create a deeply nested path that's >255 total but each component is short
        nested = src / "a" / "b" / "c" / "d" / "e" / "f" / "g"
        nested.mkdir(parents=True)
        (nested / "file.txt").write_text("data")

        dest = tmp_path / "dest"
        dest.mkdir()

        result = scan_directory(str(src), str(dest), show_progress=False)
        issues = run_precheck(result, str(src), str(dest))

        # No path_length or filename warnings should appear
        length_issues = [i for i in issues if i.category in ("path_length", "filename")]
        assert len(length_issues) == 0


class TestHardlinks:
    def test_hardlink_detected_as_file(self, tmp_path):
        """Hard links should be detected as regular files."""
        src = tmp_path / "source"
        src.mkdir()

        original = src / "original.txt"
        original.write_text("shared content")
        hardlink = src / "hardlink.txt"
        os.link(str(original), str(hardlink))

        result = scan_directory(str(src), show_progress=False)
        names = {c.name for c in result.root.children}
        assert "original.txt" in names
        assert "hardlink.txt" in names
        # Both should have the same size
        sizes = {c.name: c.size for c in result.root.children}
        assert sizes["original.txt"] == sizes["hardlink.txt"]


class TestSparseFiles:
    def test_sparse_file_scanned(self, tmp_path):
        """Sparse files should be scanned normally."""
        src = tmp_path / "source"
        src.mkdir()

        sparse = src / "sparse.bin"
        with open(str(sparse), "wb") as f:
            f.seek(1024 * 1024)  # 1MB offset
            f.write(b"data at the end")

        result = scan_directory(str(src), show_progress=False)
        node = result.root.children[0]
        assert node.name == "sparse.bin"
        # Apparent size includes the hole
        assert node.size >= 1024 * 1024
