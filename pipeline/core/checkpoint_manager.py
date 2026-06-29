import os
import json
import threading
from datetime import datetime


class CheckpointManager:
    """
    Manages persistent checkpoint state for the pipeline.

    Thread-safe: all methods that mutate self.data or write to disk acquire
    self._lock so that concurrent PDF worker threads cannot corrupt the
    checkpoint file or each other's in-memory state.

    Read-only methods (is_done, get_status, etc.) rely on CPython's GIL for
    atomic dict reads and do not acquire the lock, keeping the fast path cheap.
    """

    def __init__(self, checkpoint_file="checkpoint.json"):
        self.checkpoint_file = checkpoint_file
        self._lock = threading.Lock()
        self.data  = self._load()

    def _load(self):
        if os.path.exists(self.checkpoint_file):
            try:
                with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save(self):
        """Write current state to disk.  Caller must hold self._lock."""
        with open(self.checkpoint_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)

    def is_done(self, filename, pass_name):
        """Return True if the given file has completed the given pass."""
        return self.data.get(filename, {}).get(pass_name) == "done"

    def mark_done(self, filename, pass_name):
        """Mark the given file as having completed the given pass."""
        with self._lock:
            if filename not in self.data:
                self.data[filename] = {}
            self.data[filename][pass_name]    = "done"
            self.data[filename]["updated_at"] = datetime.now().isoformat()
            self._save()

    def get_status(self, filename):
        """Return all pass statuses for the given file."""
        return self.data.get(filename, {})

    def get_all_filenames(self):
        """Return every filename that has any checkpoint data."""
        return list(self.data.keys())

    def get_completed_passes(self, filename):
        """Return a list of pass names that are marked done for the given file."""
        return [
            k for k, v in self.data.get(filename, {}).items()
            if v == "done" and k != "updated_at"
        ]

    # Full pipeline sequence, in order — including the final RAG 'ingest' step.
    ALL_PASSES = ["pass_1", "pass_2", "pass_2b", "pass_3", "pass_3b",
                  "pass_4", "ingest"]

    def get_incomplete_passes(self, filename):
        """Return passes not yet completed for the given file."""
        completed = self.get_completed_passes(filename)
        return [p for p in self.ALL_PASSES if p not in completed]

    def get_summary(self):
        """Return a summary dict of completed passes and timestamps for all files."""
        summary = {}
        for filename, statuses in self.data.items():
            completed = [k for k, v in statuses.items() if v == "done" and k != "updated_at"]
            summary[filename] = {
                "completed_passes": completed,
                "updated_at":       statuses.get("updated_at", "unknown"),
            }
        return summary

    def reset(self, filename=None):
        """Reset checkpoint data for one file, or all files if filename is None."""
        with self._lock:
            if filename:
                self.data.pop(filename, None)
            else:
                self.data = {}
            self._save()

    def reset_pass(self, filename, pass_name):
        """Remove the checkpoint for a specific pass on a specific file."""
        with self._lock:
            if filename in self.data and pass_name in self.data[filename]:
                del self.data[filename][pass_name]
                self._save()

    def reset_all_passes(self, filename):
        """Clear all pass statuses for a file while keeping the file entry."""
        with self._lock:
            if filename in self.data:
                self.data[filename] = {}
                self._save()
