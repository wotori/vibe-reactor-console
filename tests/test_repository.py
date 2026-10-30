from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from devos.repository import inspect_repository, read_diff, run_bounded
from devos.task_runner import TaskRunner


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.git("init", "-q")
        self.git("config", "user.name", "DEVOS Test")
        self.git("config", "user.email", "devos@example.invalid")
        (self.root / "hello.py").write_text("print('first')\n")
        self.git("add", "hello.py")
        self.git("commit", "-qm", "fixture")

    def git(self, *args):
        return subprocess.run(("git",) + args, cwd=self.root, check=True, capture_output=True)

    def test_repository_reports_real_tracked_untracked_and_deleted_files(self):
        (self.root / "hello.py").write_text("print('changed')\n")
        (self.root / "new.py").write_text("# untracked\n")
        before = (self.root / ".git" / "index").read_bytes()
        result = inspect_repository(self.root)
        self.assertEqual((result.quality, result.changed_files, result.mode), ("ok", 2, "live"))
        statuses = {item.path: item.status for item in result.files}
        self.assertEqual(statuses["hello.py"], " M")
        self.assertEqual(statuses["new.py"], "??")
        self.assertEqual((self.root / ".git" / "index").read_bytes(), before)
        self.assertIn("+print('changed')", read_diff(self.root, "hello.py"))
        self.assertIn("# untracked", read_diff(self.root, "new.py"))

    def test_repository_parser_handles_spaces_and_rename_old_path(self):
        self.git("mv", "hello.py", "hello renamed.py")
        result = inspect_repository(self.root)
        renamed = next(item for item in result.files if item.path == "hello renamed.py")
        self.assertEqual(renamed.previous_path, "hello.py")
        self.assertEqual(renamed.status, "R ")

    def test_diff_path_cannot_escape_root_even_via_symlink(self):
        outside = self.root.parent / (self.root.name + "-outside.txt")
        outside.write_text("private")
        self.addCleanup(outside.unlink)
        (self.root / "escape").symlink_to(outside)
        for path in ("../elsewhere", str(outside), "escape"):
            with self.assertRaises(ValueError):
                read_diff(self.root, path)

    def test_bounded_process_output_and_timeout_do_not_hang(self):
        result = run_bounded((sys.executable, "-c", "print('x' * 100000)"), limit=1000)
        self.assertTrue(result.truncated)
        self.assertEqual(len(result.output), 1000)
        result = run_bounded((sys.executable, "-c", "import time; time.sleep(5)"), timeout=0.1)
        self.assertTrue(result.timed_out)

    def test_non_repository_is_unavailable_and_bounded_tree_is_marked(self):
        other = self.root / "empty"
        other.mkdir()
        outside = self.root.parent / (self.root.name + "-not-git")
        outside.mkdir()
        self.addCleanup(outside.rmdir)
        self.assertEqual(inspect_repository(outside).quality, "unavailable")
        self.assertTrue(inspect_repository(self.root, max_files=0).truncated)


class TaskRunnerTests(unittest.TestCase):
    def wait_for_task(self, runner):
        deadline = time.monotonic() + 3
        while runner.snapshot().state == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertNotEqual(runner.snapshot().state, "running")

    def test_explicit_command_reports_actual_exit_without_invented_test_counts(self):
        events = []
        runner = TaskRunner(callback=events.append)
        self.addCleanup(runner.close)
        self.assertEqual(runner.snapshot().state, "idle")
        runner.start((sys.executable, "-c", "print('test failed'); raise SystemExit(7)"))
        self.wait_for_task(runner)
        self.assertEqual(runner.snapshot().exit_code, 7)
        self.assertIn("test failed", runner.snapshot().output)
        finished = next(item for item in events if item.topic == "task.test.completed")
        self.assertEqual(finished.payload["exit_code"], 7)
        self.assertNotIn("tests_passed", finished.payload)
        self.assertNotIn("coverage", finished.payload)

    def test_cancel_terminates_only_owned_child_and_second_task_is_possible(self):
        runner = TaskRunner()
        self.addCleanup(runner.close)
        runner.start((sys.executable, "-c", "import time; time.sleep(20)"))
        runner.cancel()
        self.wait_for_task(runner)
        self.assertEqual(runner.snapshot().state, "cancelled")
        runner.close()
        runner.start((sys.executable, "-c", "print('done')"))
        self.wait_for_task(runner)
        self.assertEqual(runner.snapshot().exit_code, 0)

    def test_timeout_and_large_output_are_bounded(self):
        runner = TaskRunner(output_limit=1024)
        self.addCleanup(runner.close)
        runner.start((sys.executable, "-c", "print('x' * 100000)"))
        self.wait_for_task(runner)
        self.assertTrue(runner.snapshot().truncated)
        self.assertLessEqual(len(runner.snapshot().output), 1024)
        runner.close()
        runner.start((sys.executable, "-c", "import time; time.sleep(20)"), timeout=0.1)
        self.wait_for_task(runner)
        self.assertEqual(runner.snapshot().state, "timed_out")
