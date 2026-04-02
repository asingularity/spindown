"""Tests for error tracking and reporting."""

import json
import os

from hdtool.errors import ErrorTracker


class TestErrorTracker:
    def test_add_error(self, tmp_path):
        log = str(tmp_path / "errors.jsonl")
        tracker = ErrorTracker(log)
        tracker.add("/some/file.txt", "permission", "Permission denied")
        assert tracker.count == 1
        assert tracker.has_errors

    def test_empty_tracker(self, tmp_path):
        log = str(tmp_path / "errors.jsonl")
        tracker = ErrorTracker(log)
        assert tracker.count == 0
        assert not tracker.has_errors

    def test_persists_to_file(self, tmp_path):
        log = str(tmp_path / "errors.jsonl")
        tracker = ErrorTracker(log)
        tracker.add("/a.txt", "io", "Input/output error")
        tracker.add("/b.txt", "permission", "Permission denied")

        assert os.path.exists(log)
        with open(log) as f:
            lines = f.readlines()
        assert len(lines) == 2

        # Verify JSON is valid
        for line in lines:
            data = json.loads(line)
            assert "path" in data
            assert "error_type" in data
            assert "message" in data
            assert "timestamp" in data

    def test_reload_existing_log(self, tmp_path):
        log = str(tmp_path / "errors.jsonl")

        # First session
        t1 = ErrorTracker(log)
        t1.add("/a.txt", "io", "Error 1")

        # Second session loads existing
        t2 = ErrorTracker(log)
        assert t2.count == 1
        t2.add("/b.txt", "io", "Error 2")
        assert t2.count == 2

    def test_grouped(self, tmp_path):
        log = str(tmp_path / "errors.jsonl")
        tracker = ErrorTracker(log)
        tracker.add("/a.txt", "permission", "Denied")
        tracker.add("/b.txt", "io", "I/O error")
        tracker.add("/c.txt", "permission", "Denied")

        grouped = tracker.grouped()
        assert len(grouped["permission"]) == 2
        assert len(grouped["io"]) == 1

    def test_clear_log(self, tmp_path):
        log = str(tmp_path / "errors.jsonl")
        tracker = ErrorTracker(log)
        tracker.add("/a.txt", "io", "Error")
        tracker.clear_log()

        assert tracker.count == 0
        assert not os.path.exists(log)
