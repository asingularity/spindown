"""Interactive terminal tree selector for choosing files/folders to copy."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Header, Footer, Static, Tree
from textual.widgets._tree import TreeNode
from rich.text import Text

from .models import CopyStatus, FileNode, FileType, format_size


class SelectionTree(Tree):
    """Tree widget with checkbox toggling."""

    BINDINGS = [
        Binding("space", "toggle_check", "Toggle", show=True),
        Binding("a", "select_all", "Select all", show=True),
        Binding("n", "deselect_all", "Deselect all", show=True),
        Binding("enter", "confirm", "Confirm", show=True),
        Binding("q", "quit_app", "Cancel", show=True),
    ]

    def __init__(self, root_node: FileNode, **kwargs):
        super().__init__(root_node.name, **kwargs)
        self.root_file_node = root_node

    def on_mount(self) -> None:
        self._build_tree(self.root, self.root_file_node)
        self.root.expand()
        # Expand first level
        for child in self.root.children:
            child.expand()

    def _build_tree(self, tree_node: TreeNode, file_node: FileNode) -> None:
        """Recursively build the tree UI from the FileNode data."""
        tree_node.data = file_node
        self._update_label(tree_node)

        for child in file_node.children:
            if child.is_dir:
                sub = tree_node.add("")
                self._build_tree(sub, child)
            else:
                leaf = tree_node.add_leaf("")
                leaf.data = child
                self._update_label(leaf)

    def _update_label(self, tree_node: TreeNode) -> None:
        """Update a tree node's label to reflect current state."""
        file_node: FileNode = tree_node.data
        if file_node is None:
            return

        text = Text()

        # Checkbox
        if file_node.selected:
            # Check if partially selected (dir with mixed children)
            if file_node.is_dir and file_node.children:
                all_sel = all(c.selected for c in file_node.children)
                if not all_sel:
                    text.append("[~] ", style="yellow bold")
                else:
                    text.append("[x] ", style="green bold")
            else:
                text.append("[x] ", style="green bold")
        else:
            text.append("[ ] ", style="dim")

        # Name
        if file_node.is_dir:
            text.append(f"{file_node.name}/", style="bold blue")
        elif file_node.file_type == FileType.SYMLINK:
            text.append(file_node.name, style="cyan")
        else:
            text.append(file_node.name)

        # Size
        size = file_node.total_size
        text.append(f"  ({format_size(size)})", style="dim")

        # File count for directories
        if file_node.is_dir:
            fc = file_node.file_count
            text.append(f"  {fc} file{'s' if fc != 1 else ''}", style="dim")

        # Copy status
        status_styles = {
            CopyStatus.COPIED: ("green", "COPIED"),
            CopyStatus.PARTIAL: ("yellow", "PARTIAL"),
            CopyStatus.MODIFIED: ("magenta", "MODIFIED"),
            CopyStatus.NEW: ("", ""),
        }
        style, label = status_styles.get(file_node.copy_status, ("", ""))
        if label:
            text.append(f"  [{label}]", style=style)

        tree_node.set_label(text)

    def action_toggle_check(self) -> None:
        """Toggle selection of the current node."""
        node = self.cursor_node
        if node is None or node.data is None:
            return

        file_node: FileNode = node.data
        if file_node.selected:
            file_node.deselect_all()
        else:
            file_node.select_all()

        # Update this node and all its children in the tree
        self._refresh_subtree(node)
        # Update parent labels up the chain
        self._refresh_ancestors(node)

        self.app.query_one("#summary", Static).update(self._summary_text())

    def _refresh_subtree(self, tree_node: TreeNode) -> None:
        """Refresh labels for a node and all descendants."""
        self._update_label(tree_node)
        for child in tree_node.children:
            self._refresh_subtree(child)

    def _refresh_ancestors(self, tree_node: TreeNode) -> None:
        """Refresh parent labels up to root."""
        parent = tree_node.parent
        while parent is not None:
            if parent.data is not None:
                file_node: FileNode = parent.data
                file_node.update_parent_status()
                self._update_label(parent)
            parent = parent.parent

    def _summary_text(self) -> str:
        root = self.root_file_node
        sel_size = root.selected_size
        sel_files = root.selected_file_count
        total_size = root.total_size
        total_files = root.file_count
        return (
            f" Selected: [bold]{format_size(sel_size)}[/bold] "
            f"({sel_files:,} files) "
            f" | Total: {format_size(total_size)} ({total_files:,} files) "
            f" | [dim]SPACE=toggle  A=all  N=none  ENTER=confirm  Q=cancel[/dim]"
        )

    def action_select_all(self) -> None:
        self.root_file_node.select_all()
        self._refresh_subtree(self.root)
        self.app.query_one("#summary", Static).update(self._summary_text())

    def action_deselect_all(self) -> None:
        self.root_file_node.deselect_all()
        self._refresh_subtree(self.root)
        self.app.query_one("#summary", Static).update(self._summary_text())

    def action_confirm(self) -> None:
        self.app.exit(True)

    def action_quit_app(self) -> None:
        self.app.exit(None)


class SelectorApp(App):
    """Full-screen TUI for selecting files/folders to backup."""

    CSS = """
    SelectionTree {
        height: 1fr;
    }
    #header_info {
        height: 3;
        padding: 0 1;
        background: $surface;
    }
    #summary {
        height: 1;
        dock: bottom;
        padding: 0 1;
        background: $accent;
        color: $text;
    }
    """

    TITLE = "Hard Drive Backup - Select Files"

    def __init__(self, root_node: FileNode, source: str, dest: str, **kwargs):
        super().__init__(**kwargs)
        self.root_node = root_node
        self.source = source
        self.dest = dest

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            f" Source: [bold]{self.source}[/bold]\n"
            f" Dest:   [bold]{self.dest}[/bold]\n"
            f" Total:  [bold]{format_size(self.root_node.total_size)}[/bold] "
            f"({self.root_node.file_count:,} files, "
            f"{self.root_node.dir_count:,} dirs)",
            id="header_info",
        )
        tree = SelectionTree(self.root_node, id="file_tree")
        tree.show_root = False
        yield tree
        yield Static("", id="summary")
        yield Footer()

    def on_mount(self) -> None:
        tree = self.query_one(SelectionTree)
        self.query_one("#summary", Static).update(tree._summary_text())


def run_selector(root_node: FileNode, source: str, dest: str) -> bool | None:
    """Run the interactive file selector.

    Args:
        root_node: Root of the scanned file tree.
        source: Source path (for display).
        dest: Destination path (for display).

    Returns:
        True if user confirmed selection.
        None if user cancelled.
    """
    app = SelectorApp(root_node, source, dest)
    return app.run()
