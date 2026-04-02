"""Tests for tree selection logic (not the TUI itself, but the data model)."""

from hdtool.models import CopyStatus, FileNode, FileType, format_size


def _make_tree() -> FileNode:
    """Create a sample tree for testing selection logic."""
    root = FileNode(path="", name="root", file_type=FileType.DIRECTORY, children=[
        FileNode(path="folder1", name="folder1", file_type=FileType.DIRECTORY, children=[
            FileNode(path="folder1/a.txt", name="a.txt", file_type=FileType.FILE, size=100),
            FileNode(path="folder1/b.txt", name="b.txt", file_type=FileType.FILE, size=200),
        ]),
        FileNode(path="folder2", name="folder2", file_type=FileType.DIRECTORY, children=[
            FileNode(path="folder2/c.txt", name="c.txt", file_type=FileType.FILE, size=300),
            FileNode(path="folder2/sub", name="sub", file_type=FileType.DIRECTORY, children=[
                FileNode(path="folder2/sub/d.txt", name="d.txt", file_type=FileType.FILE, size=400),
            ]),
        ]),
        FileNode(path="top.txt", name="top.txt", file_type=FileType.FILE, size=50),
    ])
    return root


class TestSelection:
    def test_all_selected_by_default(self):
        root = _make_tree()
        assert root.selected
        assert all(c.selected for c in root.children)

    def test_deselect_all(self):
        root = _make_tree()
        root.deselect_all()
        assert not root.selected
        for child in root.children:
            assert not child.selected

    def test_select_all(self):
        root = _make_tree()
        root.deselect_all()
        root.select_all()
        assert root.selected
        assert all(c.selected for c in root.children)

    def test_deselect_subtree(self):
        root = _make_tree()
        folder1 = root.children[0]
        folder1.deselect_all()
        assert not folder1.selected
        assert not folder1.children[0].selected
        # folder2 should still be selected
        assert root.children[1].selected


class TestSelectedSize:
    def test_total_size(self):
        root = _make_tree()
        assert root.total_size == 100 + 200 + 300 + 400 + 50

    def test_selected_size_all(self):
        root = _make_tree()
        assert root.selected_size == 1050

    def test_selected_size_partial(self):
        root = _make_tree()
        root.children[0].deselect_all()  # folder1: 300 bytes
        assert root.selected_size == 300 + 400 + 50  # folder2 + top.txt

    def test_selected_size_none(self):
        root = _make_tree()
        root.deselect_all()
        assert root.selected_size == 0


class TestFileCount:
    def test_total_file_count(self):
        root = _make_tree()
        assert root.file_count == 5  # a, b, c, d, top

    def test_selected_file_count(self):
        root = _make_tree()
        root.children[0].deselect_all()
        assert root.selected_file_count == 3  # c, d, top


class TestCopyStatus:
    def test_all_new(self):
        root = _make_tree()
        root.compute_copy_status()
        assert root.copy_status == CopyStatus.NEW

    def test_all_copied(self):
        root = _make_tree()
        for child in root.children:
            if child.is_dir:
                for gc in child.children:
                    gc.copy_status = CopyStatus.COPIED
                    if gc.is_dir:
                        for ggc in gc.children:
                            ggc.copy_status = CopyStatus.COPIED
            else:
                child.copy_status = CopyStatus.COPIED
        root.compute_copy_status()
        assert root.copy_status == CopyStatus.COPIED

    def test_partial(self):
        root = _make_tree()
        root.children[0].children[0].copy_status = CopyStatus.COPIED
        root.compute_copy_status()
        assert root.copy_status == CopyStatus.PARTIAL


class TestParentStatus:
    def test_update_parent_all_selected(self):
        root = _make_tree()
        folder1 = root.children[0]
        folder1.update_parent_status()
        assert folder1.selected

    def test_update_parent_none_selected(self):
        root = _make_tree()
        folder1 = root.children[0]
        for c in folder1.children:
            c.selected = False
        folder1.update_parent_status()
        assert not folder1.selected

    def test_update_parent_partial(self):
        root = _make_tree()
        folder1 = root.children[0]
        folder1.children[0].selected = False
        folder1.update_parent_status()
        # Should still be selected since some children are
        assert folder1.selected


class TestFormatSize:
    def test_bytes(self):
        assert format_size(0) == "0 B"
        assert format_size(512) == "512 B"

    def test_kilobytes(self):
        assert "KB" in format_size(1500)

    def test_megabytes(self):
        assert "MB" in format_size(5 * 1024 * 1024)

    def test_gigabytes(self):
        assert "GB" in format_size(2 * 1024**3)

    def test_terabytes(self):
        assert "TB" in format_size(3 * 1024**4)
