import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.utils import FileLock, ProjectLock


class TestProjectLock(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.project_path = Path(self.test_dir)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.test_dir)

    def test_acquire_and_release_success(self):
        """Verify project lock is acquired and released cleanly."""
        lock = ProjectLock(self.project_path)
        lock.acquire()

        lock_file = self.project_path / ".liferay-docker" / ".ldm_lock"
        self.assertTrue(lock_file.exists())

        # Verify PID was written
        content = lock_file.read_text()
        self.assertIn(f"PID: {os.getpid()}", content)

        # Release lock
        lock.release()
        self.assertFalse(lock_file.exists())

    def test_concurrency_violation(self):
        """Verify trying to acquire lock when already held raises RuntimeError."""
        lock1 = ProjectLock(self.project_path)
        lock1.acquire()

        lock2 = ProjectLock(self.project_path)
        with self.assertRaises(RuntimeError) as ctx:
            lock2.acquire()

        self.assertIn(
            "Concurrency Violation: Another instance of LDM is running on this project.",
            str(ctx.exception),
        )

        lock1.release()

    def test_context_manager(self):
        """Verify context manager interface for ProjectLock."""
        lock_file = self.project_path / ".liferay-docker" / ".ldm_lock"

        with ProjectLock(self.project_path):
            self.assertTrue(lock_file.exists())

        self.assertFalse(lock_file.exists())

    def test_stale_lock_recovery(self):
        """Verify stale lock is detected and auto-recovered."""
        lock_file = self.project_path / ".liferay-docker" / ".ldm_lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        # Write stale PID that doesn't exist
        lock_file.write_text("PID: 999999\n", encoding="utf-8")

        lock = ProjectLock(self.project_path)
        # Should succeed because 999999 is not running
        lock.acquire()
        self.assertTrue(lock_file.exists())
        content = lock_file.read_text(encoding="utf-8")
        self.assertIn(f"PID: {os.getpid()}", content)
        lock.release()

    def test_live_lock_blocking(self):
        """Verify live lock blocks acquisition and raises RuntimeError."""
        lock_file = self.project_path / ".liferay-docker" / ".ldm_lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        # Write live PID of the current process
        lock_file.write_text(f"PID: {os.getpid()}\n", encoding="utf-8")

        # Open and hold the flock to simulate a live lock
        import fcntl

        with open(lock_file, "r+") as hold_fd:
            fcntl.flock(hold_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            lock = ProjectLock(self.project_path)
            with self.assertRaises(RuntimeError) as ctx:
                lock.acquire()

            self.assertIn(
                "Concurrency Violation: Another instance of LDM is running on this project.",
                str(ctx.exception),
            )
            fcntl.flock(hold_fd, fcntl.LOCK_UN)

    @patch("time.sleep")
    def test_project_lock_retries_and_raises(self, mock_sleep):
        """Verify ProjectLock retries acquisition and eventually raises on failure."""
        lock_file = self.project_path / ".liferay-docker" / ".ldm_lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        # Write PID of current process and hold the flock to simulate a locked file
        lock_file.write_text(f"PID: {os.getpid()}\n", encoding="utf-8")

        import fcntl

        with open(lock_file, "r+") as hold_fd:
            fcntl.flock(hold_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            lock = ProjectLock(self.project_path)
            with self.assertRaises(RuntimeError) as ctx:
                lock.acquire(retries=3)

            self.assertIn("Concurrency Violation", str(ctx.exception))
            self.assertEqual(
                mock_sleep.call_count, 2
            )  # Should sleep twice for 3 retries

            fcntl.flock(hold_fd, fcntl.LOCK_UN)


class TestFileLock(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.lock_file = Path(self.test_dir) / "test.lock"

    def tearDown(self):
        import shutil

        shutil.rmtree(self.test_dir)

    def test_acquire_release(self):
        lock = FileLock(self.lock_file)
        lock.acquire()
        self.assertTrue(self.lock_file.exists())
        lock.release()
        self.assertFalse(self.lock_file.exists())

    @patch("time.sleep")
    def test_file_lock_retries_and_raises(self, mock_sleep):
        from ldm_core.utils import FileLock

        lock1 = FileLock(self.lock_file)
        lock1.acquire()

        lock2 = FileLock(self.lock_file)
        with self.assertRaises(RuntimeError) as ctx:
            lock2.acquire(retries=3)

        self.assertIn("Could not acquire lock on file", str(ctx.exception))
        self.assertEqual(mock_sleep.call_count, 2)

        lock1.release()
