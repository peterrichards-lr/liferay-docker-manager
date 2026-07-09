import os
import tempfile
import unittest
from pathlib import Path

from ldm_core.utils import ProjectLock


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
