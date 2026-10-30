import os
from pathlib import Path
import random
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from devos.modes import MODES
from devos.project_sources import MAX_FILE_BYTES
from devos.simulation import Simulation
from devos.sources import SourceProvider


class ProjectSourceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "project"
        self.root.mkdir()
        self.providers = []
        self.addCleanup(self.close_providers)

    def close_providers(self):
        for provider in self.providers:
            provider.close()

    def provider(self, root=None, **options):
        provider = SourceProvider(project_root=self.root if root is None else root, **options)
        self.providers.append(provider)
        return provider

    def write(self, relative, text):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def wait(self, probe, predicate=bool):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = probe()
            if predicate(result):
                return result
            time.sleep(.005)
        self.fail("background source preparation did not finish")

    def select(self, provider, current=None, refresh=False):
        rng = random.Random(9)
        return self.wait(lambda: provider.poll_project(rng, current, refresh))

    def test_real_excerpt_has_language_relative_path_and_actual_line_numbers(self):
        lines = ["def line_{}(): return {}".format(i, i) for i in range(180)]
        self.write("src/scene.py", "\n".join(lines))
        selected = self.select(self.provider())
        self.assertEqual(selected.snippet.path, "src/scene.py")
        self.assertEqual(selected.snippet.language, "Python")
        self.assertEqual(selected.source.kind, "PROJECT")
        self.assertGreater(selected.source.first_line, 1)
        self.assertEqual(selected.source.lines, tuple(lines[selected.source.first_line - 1:selected.source.last_line]))
        self.assertEqual(len(selected.source.lines), 56)

    def test_next_does_not_repeat_when_another_file_is_available(self):
        self.write("one.c", "int main(void) { return 1; }")
        self.write("two.rs", "fn main() { println!(\"two\"); }")
        provider = self.provider()
        first = self.select(provider)
        second = self.select(provider, first)
        third = self.select(provider, second)
        self.assertNotEqual(first.snippet.path, second.snippet.path)
        self.assertEqual(first.snippet.path, third.snippet.path)

    def test_reload_rereads_same_file_and_excerpt_without_stale_text(self):
        target = self.write("live.py", "\n".join("print('old{}')".format(i) for i in range(160)))
        provider = self.provider()
        first = self.select(provider)
        target.write_text("\n".join("print('new{}')".format(i) for i in range(160)))
        self.write("alternative.py", "print('other')")
        refreshed = self.select(provider, first, refresh=True)
        self.assertEqual(refreshed.snippet.path, "live.py")
        self.assertEqual(refreshed.source.first_line, first.source.first_line)
        self.assertNotEqual(refreshed.source.digest, first.source.digest)
        self.assertNotIn("old", "\n".join(refreshed.source.lines))
        self.assertIn("new", "\n".join(refreshed.source.lines))

    def test_index_excludes_hidden_secrets_dependencies_binary_large_and_symlinks(self):
        self.write("src/good.c", "int selected = 42;")
        for relative in (".env.py", "credentials.py", "api_keys.py", "keys.rs", "private_key.c",
                         ".git/hidden.py", "node_modules/third_party.py", "target/out.rs",
                         "vendor/dependency.c", "dist/bundle.js", ".venv/package.py"):
            self.write(relative, "SHOULD_NOT_APPEAR")
        self.write("huge.py", "x" * (MAX_FILE_BYTES + 1))
        self.write("binary.py", "hello\x00binary")
        self.write("invalid.py", "placeholder").write_bytes(b"\xff\xfe")
        external = Path(self.directory.name) / "external.py"
        external.write_text("SHOULD_NOT_APPEAR")
        (self.root / "outside.py").symlink_to(external)
        (self.root / "outside").symlink_to(external.parent, target_is_directory=True)
        os.mkfifo(str(self.root / "fifo.py"))
        provider = self.provider()
        selected = self.select(provider)
        self.assertEqual(selected.snippet.path, "src/good.c")
        # Binary candidates can be indexed, but must never be published as code.
        self.assertEqual(provider.project_status.file_count, 3)

    def test_symlink_swap_after_index_is_rejected_and_never_blocks_on_fifo(self):
        target = self.write("race.py", "print('safe')")
        provider = self.provider()
        first = self.select(provider)
        target.unlink()
        external = Path(self.directory.name) / "external.py"
        external.write_text("PRIVATE_OUTSIDE_DATA")
        target.symlink_to(external)
        rng = random.Random(5)
        self.wait(lambda: (provider.poll_project(rng, first), provider.project_status)[1],
                  lambda status: status.state == "empty")
        target.unlink()
        os.mkfifo(str(target))
        provider.request_project(rng, first, True)
        self.wait(lambda: (provider.poll_project(rng, first, True), provider.project_status)[1],
                  lambda status: status.state == "empty")

    def test_pending_selection_does_not_read_or_wait_on_dashboard_thread(self):
        self.write("code.py", "print('prepared')")
        entered, release = threading.Event(), threading.Event()
        from devos import project_sources
        original = project_sources._prepare

        def slow_prepare(*args, **kwargs):
            entered.set()
            release.wait(2)
            return original(*args, **kwargs)

        with patch("devos.project_sources._prepare", side_effect=slow_prepare):
            provider = self.provider()
            simulation = Simulation(MODES[0], seed=7, sources=provider)
            original_source = simulation.source
            self.wait(lambda: (simulation.update(0), entered.is_set())[1])
            before = time.monotonic()
            for _ in range(200):
                simulation.update(0)
            self.assertLess(time.monotonic() - before, .1)
            self.assertIs(simulation.source, original_source)
            release.set()
            self.wait(lambda: (simulation.update(0), simulation.source.kind)[1], lambda kind: kind == "PROJECT")
            self.assertEqual(simulation.elapsed, 0)

    def test_empty_and_missing_roots_fall_back_with_explicit_note(self):
        for root in (self.root, self.root / "missing"):
            provider = self.provider(root)
            simulation = Simulation(MODES[0], sources=provider)
            self.wait(lambda: (simulation.update(0), simulation._project_waiting)[1], lambda waiting: not waiting)
            self.assertEqual(simulation.source.kind, "SNAPSHOT")
            self.assertIn("scene sources", simulation.source_note)
            self.assertTrue(simulation.lines)

    def test_empty_result_is_not_retried_per_frame_and_explicit_reload_recovers(self):
        from devos import project_sources
        with patch("devos.project_sources._scan", wraps=project_sources._scan) as scan:
            provider = self.provider()
            simulation = Simulation(MODES[0], sources=provider)
            self.wait(lambda: (simulation.update(0), simulation._project_waiting)[1], lambda waiting: not waiting)
            initial_calls = scan.call_count
            for _ in range(100):
                simulation.update(.001)
            self.assertEqual(scan.call_count, initial_calls)
            self.write("new.py", "print('now available')")
            simulation.load_file(advance=False)
            self.wait(lambda: (simulation.update(0), simulation.source.kind)[1], lambda kind: kind == "PROJECT")
            self.assertIn("now available", "\n".join(simulation.lines))

    def test_bundled_remembers_root_without_starting_a_worker_and_explicit_selection_enables_it(self):
        self.write("scene.rs", "fn scene() {}")
        with patch("devos.sources.ProjectSourceWorker", side_effect=AssertionError("must not scan bundled project")):
            provider = self.provider(bundled=True)
            simulation = Simulation(MODES[0], sources=provider)
            simulation.update(0)
            self.assertEqual(provider.project_root, str(self.root))
            self.assertFalse(provider.project_active)
            self.assertEqual(simulation.source.kind, "SNAPSHOT")
        provider.set_project_root(self.root)
        self.wait(lambda: (simulation.update(0), simulation.source.kind)[1], lambda kind: kind == "PROJECT")
        provider.set_project_root(None)
        simulation.update(0)
        self.assertNotEqual(simulation.source.kind, "PROJECT")
        self.assertFalse(provider.bundled)

    def test_switching_roots_discards_old_pending_result_and_escapes_terminal_controls(self):
        self.write("old.py", "print('OLD_PROJECT')")
        provider = self.provider()
        first = self.select(provider)
        other = Path(self.directory.name) / "other"
        other.mkdir()
        (other / "new.py").write_text("print('NEW_PROJECT')\n# \x1b[31m\x07")
        provider.set_project_root(other)
        selected = self.select(provider, first)
        text = "\n".join(selected.source.lines)
        self.assertIn("NEW_PROJECT", text)
        self.assertNotIn("OLD_PROJECT", text)
        self.assertNotIn("\x1b", text)
        self.assertIn("\\x1b", text)

    def test_scan_budget_and_display_width_are_bounded(self):
        self.write("wide.py", "x" * 2000)
        with patch("devos.project_sources.MAX_ENTRIES", 2):
            provider = self.provider()
            selected = self.select(provider)
        self.assertLessEqual(len(selected.source.lines[0]), 514)
        self.assertIn("truncated", selected.source.note)

    def test_index_entry_limit_stops_traversal_and_reports_partial_catalog(self):
        for index in range(15):
            self.write("code{}.py".format(index), "print({})".format(index))
        with patch("devos.project_sources.MAX_ENTRIES", 3):
            provider = self.provider()
            selected = self.select(provider)
            self.assertEqual(selected.source.kind, "PROJECT")
            self.assertLessEqual(provider.project_status.file_count, 3)
            self.assertIn("scan limit", provider.project_status.note)

    def test_latest_reload_supersedes_an_older_inflight_selection(self):
        self.write("one.py", "print('one')")
        self.write("two.py", "print('two')")
        provider = self.provider()
        current = self.select(provider)
        from devos import project_sources
        original = project_sources._prepare
        entered, release = threading.Event(), threading.Event()
        calls = []

        def slow_first(*args, **kwargs):
            calls.append(args[1])
            if len(calls) == 1:
                entered.set()
                release.wait(2)
            return original(*args, **kwargs)

        rng = random.Random(3)
        with patch("devos.project_sources._prepare", side_effect=slow_first):
            provider.request_project(rng, current)
            self.assertTrue(entered.wait(1))
            provider.request_project(rng, current, refresh=True)
            release.set()
            reloaded = self.wait(lambda: provider.poll_project(rng, current))
        self.assertGreaterEqual(len(calls), 2)
        self.assertNotEqual(calls[0], current.snippet.path)
        self.assertEqual(reloaded.snippet.path, current.snippet.path)


if __name__ == "__main__":
    unittest.main()
