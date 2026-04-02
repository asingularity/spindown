"""Tests for session state management."""

import os

from hdtool.state import (
    SessionLock,
    create_session,
    delete_state,
    load_state,
    save_state,
)


class TestSessionState:
    def test_create_session(self):
        session = create_session("/source", "/dest")
        assert session.source_path == "/source"
        assert session.dest_path == "/dest"
        assert session.status == "scanning"
        assert session.session_id  # Non-empty UUID

    def test_save_and_load(self, tmp_path):
        dest = str(tmp_path / "dest")
        os.makedirs(dest)

        session = create_session("/source", dest)
        session.status = "copying"
        session.total_bytes = 1000000
        session.copied_bytes = 500000
        save_state(dest, session)

        loaded = load_state(dest)
        assert loaded is not None
        assert loaded.session_id == session.session_id
        assert loaded.status == "copying"
        assert loaded.total_bytes == 1000000
        assert loaded.copied_bytes == 500000

    def test_load_nonexistent(self, tmp_path):
        assert load_state(str(tmp_path)) is None

    def test_load_corrupted(self, tmp_path):
        dest = str(tmp_path)
        with open(os.path.join(dest, ".hdtool-state.json"), "w") as f:
            f.write("not valid json{{{")
        assert load_state(dest) is None

    def test_delete_state(self, tmp_path):
        dest = str(tmp_path / "dest")
        os.makedirs(dest)

        session = create_session("/source", dest)
        save_state(dest, session)
        assert load_state(dest) is not None

        delete_state(dest)
        assert load_state(dest) is None

    def test_save_updates_timestamp(self, tmp_path):
        dest = str(tmp_path / "dest")
        os.makedirs(dest)

        session = create_session("/source", dest)
        original_updated = session.last_updated
        save_state(dest, session)

        loaded = load_state(dest)
        # last_updated should be set by save_state
        assert loaded.last_updated >= original_updated

    def test_selected_paths_roundtrip(self, tmp_path):
        dest = str(tmp_path / "dest")
        os.makedirs(dest)

        session = create_session("/source", dest)
        session.selected_paths = ["/folder1", "/folder2/sub"]
        session.excluded_paths = ["/folder3"]
        save_state(dest, session)

        loaded = load_state(dest)
        assert loaded.selected_paths == ["/folder1", "/folder2/sub"]
        assert loaded.excluded_paths == ["/folder3"]


class TestSessionLock:
    def test_acquire_lock(self, tmp_path):
        dest = str(tmp_path)
        with SessionLock(dest) as lock:
            assert lock.acquired

    def test_concurrent_lock_fails(self, tmp_path):
        dest = str(tmp_path)
        with SessionLock(dest) as lock1:
            assert lock1.acquired
            with SessionLock(dest) as lock2:
                assert not lock2.acquired

    def test_lock_released_after_context(self, tmp_path):
        dest = str(tmp_path)
        with SessionLock(dest) as lock1:
            assert lock1.acquired

        # Should be able to acquire again
        with SessionLock(dest) as lock2:
            assert lock2.acquired

    def test_lock_file_cleaned_up(self, tmp_path):
        dest = str(tmp_path)
        with SessionLock(dest):
            pass
        assert not os.path.exists(os.path.join(dest, ".hdtool.lock"))
