"""Conservative integration produces a candidate and preserves all inputs."""

from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from devos.agents import snapshot_manifest
from devos.integration import IntegrationResult, integrate_workspaces


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.base, self.left, self.right = (self.root / name for name in ("base", "left", "right"))
        for path in (self.base, self.left, self.right):
            path.mkdir()
        self.destination = self.root / "candidate"

    def write(self, root, relative, content):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode())

    def same(self, relative, content):
        for root in (self.base, self.left, self.right):
            self.write(root, relative, content)

    def integrate(self):
        before = tuple(snapshot_manifest(root) for root in (self.base, self.left, self.right))
        with patch("subprocess.Popen", side_effect=AssertionError("Integration must not launch commands")):
            result = integrate_workspaces(self.base, self.left, self.right, self.destination)
        after = tuple(snapshot_manifest(root) for root in (self.base, self.left, self.right))
        self.assertEqual(before, after)
        self.assertIsInstance(result, IntegrationResult)
        with self.assertRaises(FrozenInstanceError):
            result.root = "changed"
        return result, json.loads(Path(result.report_path).read_text())

    def test_independent_add_edit_delete_and_identical_results_merge_without_conflicts(self):
        self.same("unchanged.txt", "base")
        self.same("left.txt", "base")
        self.same("right.txt", "base")
        self.same("deleted.txt", "remove")
        self.same("agreed.txt", "old")
        self.write(self.left, "left.txt", "left edit")
        self.write(self.right, "right.txt", "right edit")
        self.write(self.left, "new/a.txt", "left addition")
        self.write(self.right, "new/b.txt", "right addition")
        self.write(self.left, "agreed.txt", "same new bytes")
        self.write(self.right, "agreed.txt", "same new bytes")
        (self.left / "deleted.txt").unlink()
        result, report = self.integrate()
        self.assertEqual(result.conflicts, ())
        self.assertEqual(set(result.applied), {"left.txt", "right.txt", "deleted.txt", "agreed.txt", "new/a.txt", "new/b.txt"})
        self.assertEqual((self.destination / "left.txt").read_text(), "left edit")
        self.assertEqual((self.destination / "right.txt").read_text(), "right edit")
        self.assertEqual((self.destination / "agreed.txt").read_text(), "same new bytes")
        self.assertFalse((self.destination / "deleted.txt").exists())
        self.assertEqual(report["status"], "ready-for-review")

    def test_divergent_bytes_and_delete_versus_edit_keep_base_and_both_artifacts(self):
        self.same("module.py", "base")
        self.same("removed.py", "base removed")
        self.write(self.left, "module.py", "left")
        self.write(self.right, "module.py", "right")
        (self.left / "removed.py").unlink()
        self.write(self.right, "removed.py", "right changed")
        result, report = self.integrate()
        self.assertEqual(result.conflicts, ("module.py", "removed.py"))
        self.assertEqual(result.applied, ())
        self.assertEqual((self.destination / "module.py").read_text(), "base")
        self.assertEqual((self.destination / "removed.py").read_text(), "base removed")
        artifacts = self.destination / ".devos/integration-conflicts"
        self.assertEqual((artifacts / "left/module.py").read_text(), "left")
        self.assertEqual((artifacts / "right/module.py").read_text(), "right")
        self.assertFalse((artifacts / "left/removed.py").exists())
        self.assertEqual((artifacts / "right/removed.py").read_text(), "right changed")
        self.assertEqual(report["status"], "needs-user")
        removed = next(item for item in report["conflicts"] if item["path"] == "removed.py")
        self.assertTrue(removed["versions"]["left"]["deleted"])

    def test_file_directory_addition_collision_keeps_base_subtree(self):
        self.write(self.left, "package", "a new file")
        self.write(self.right, "package/new.py", "a new child")
        result, report = self.integrate()
        self.assertEqual(result.conflicts, ("package",))
        self.assertFalse((self.destination / "package").exists())
        self.assertEqual((self.destination / ".devos/integration-conflicts/left/package").read_text(), "a new file")
        self.assertEqual((self.destination / ".devos/integration-conflicts/right/package/new.py").read_text(), "a new child")
        self.assertEqual(report["conflicts"][0]["reason"], "file-versus-directory collision")

    def test_one_side_can_replace_a_directory_with_a_file_without_touching_roots(self):
        self.same("package/deep/module.py", "base child")
        (self.left / "package/deep/module.py").unlink()
        (self.left / "package/deep").rmdir()
        (self.left / "package").rmdir()
        self.write(self.left, "package", "replacement")
        result, _ = self.integrate()
        self.assertEqual(result.conflicts, ())
        self.assertEqual((self.destination / "package").read_text(), "replacement")

    def test_base_file_remains_on_a_replacement_versus_edit_conflict(self):
        self.same("package", "base file")
        (self.left / "package").unlink()
        self.write(self.left, "package/module.py", "new child")
        self.write(self.right, "package", "right edit")
        result, _ = self.integrate()
        self.assertEqual(result.conflicts, ("package",))
        self.assertEqual((self.destination / "package").read_text(), "base file")
        self.assertEqual((self.destination / ".devos/integration-conflicts/left/package/module.py").read_text(), "new child")

    def test_secrets_repository_metadata_large_files_and_old_reports_are_excluded(self):
        for name in (".env", ".env.local", ".ENV.production", "secret.pem", "private.key", "secrets.json",
                     "credentials.json", ".npmrc", ".git/config", ".devos/integration-report.json"):
            self.same(name, "private bytes must not be copied")
        self.same("large.bin", b"x" * (1024 * 1024 + 1))
        self.same("src/public.py", "print('public')")
        _, report = self.integrate()
        copied = {str(path.relative_to(self.destination)) for path in self.destination.rglob("*") if path.is_file()}
        self.assertEqual(copied, {"src/public.py", ".devos/integration-report.json"})
        self.assertEqual(report["policy"]["credentials"], "excluded")

    def test_links_and_special_files_are_rejected_before_creating_candidate(self):
        self.write(self.base, "public.txt", "base")
        (self.left / "external-link").symlink_to(self.base / "public.txt")
        with self.assertRaises(ValueError):
            integrate_workspaces(self.base, self.left, self.right, self.destination)
        self.assertFalse(self.destination.exists())
        (self.left / "external-link").unlink()
        os.mkfifo(str(self.left / "fifo"))
        with self.assertRaisesRegex(ValueError, "regular files"):
            integrate_workspaces(self.base, self.left, self.right, self.destination)
        self.assertFalse(self.destination.exists())

    def test_destination_is_exclusive_and_outside_all_input_roots(self):
        self.destination.mkdir()
        self.write(self.destination, "preserve.txt", "untouched")
        with self.assertRaises(ValueError):
            integrate_workspaces(self.base, self.left, self.right, self.destination)
        self.assertEqual((self.destination / "preserve.txt").read_text(), "untouched")
        for root in (self.base, self.left, self.right):
            with self.assertRaisesRegex(ValueError, "outside"):
                integrate_workspaces(self.base, self.left, self.right, root / "candidate")

    def test_source_change_during_capture_fails_before_any_destination_write(self):
        self.same("module.py", "base")
        original = snapshot_manifest
        def changing(root):
            manifest = original(root)
            self.write(root, "module.py", "changed after hash")
            return manifest
        with patch("devos.integration.snapshot_manifest", side_effect=changing):
            with self.assertRaisesRegex(ValueError, "changed during integration"):
                integrate_workspaces(self.base, self.left, self.right, self.destination)
        self.assertFalse(self.destination.exists())

    def test_filename_case_aliases_are_visible_conflicts_not_silent_overwrites(self):
        self.write(self.left, "Module.py", "left")
        self.write(self.right, "module.py", "right")
        result, _ = self.integrate()
        self.assertEqual(result.conflicts, ("Module.py", "module.py"))
        self.assertFalse((self.destination / "Module.py").exists())
        self.assertFalse((self.destination / "module.py").exists())

    def test_case_alias_between_a_file_and_directory_is_a_conflict(self):
        self.write(self.left, "Package", "left file")
        self.write(self.right, "package/new.py", "right child")
        result, _ = self.integrate()
        self.assertEqual(result.conflicts, ("Package", "package/new.py"))
        self.assertFalse((self.destination / "Package").exists())

    def test_executable_regular_files_keep_execution_mode_without_special_bits(self):
        self.same("script.py", "print('base')")
        (self.base / "script.py").chmod(0o755)
        self.write(self.left, "script.py", "print('new')")
        (self.left / "script.py").chmod(0o755)
        self.integrate()
        self.assertEqual((self.destination / "script.py").stat().st_mode & 0o7777, 0o755)


if __name__ == "__main__":
    unittest.main()


class BuildArtifactTests(unittest.TestCase):
    def test_cache_and_build_outputs_do_not_change_code_identity_or_enter_candidate(self):
        from devos.agents import snapshot_manifest
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            roots = [root / name for name in ('base', 'left', 'right')]
            for source in roots:
                source.mkdir()
                (source / 'source.py').write_text('value = 1\n')
            before = snapshot_manifest(roots[1])[1]
            for name in ('.pytest_cache', '.mypy_cache', '.ruff_cache', 'build', 'dist', 'target', 'package.egg-info'):
                (roots[1] / name).mkdir()
                (roots[1] / name / 'generated').write_text('not source\n')
            self.assertEqual(snapshot_manifest(roots[1])[1], before)
            result = integrate_workspaces(*roots, root / 'candidate')
            self.assertFalse(result.conflicts)
            self.assertEqual(sorted(path.name for path in Path(result.root).iterdir()), ['.devos', 'source.py'])
