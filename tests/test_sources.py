from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from devos.cli import parser
from devos.ekza import EKZA_MODE
from devos.modes import MODES
from devos.session import Session
from devos.sources import MAX_SOURCE_BYTES, SourceProvider, packaged_lines


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name) / "ekza"
        self.root.mkdir()
        self.snippet = EKZA_MODE.snippets[0]
        self.file = self.root / self.snippet.source_path
        self.file.parent.mkdir(parents=True)
        self.provider = SourceProvider(self.root)

    def write_source(self, value="first"):
        lines = ["// context {}".format(i) for i in range(20)]
        lines += ["pub fn asset_slug() {", '    let version = "' + value + '";', "}"]
        lines += ["// tail {}".format(i) for i in range(100)]
        self.file.write_text("\n".join(lines), encoding="utf-8")

    def test_default_scene_is_ekza(self):
        self.assertEqual(MODES[0].id, "ekza")
        self.assertEqual(parser().parse_args([]).mode, "ekza")
        self.assertEqual(Session(sources=self.provider).mode_id, "ekza")

    def test_local_code_is_bounded_and_reports_actual_line_numbers(self):
        self.write_source()
        loaded = self.provider.load(self.snippet)
        self.assertEqual(loaded.kind, "LOCAL")
        self.assertEqual(loaded.first_line, 19)
        self.assertEqual(len(loaded.lines), self.snippet.max_lines)
        self.assertEqual(loaded.lines[2], "pub fn asset_slug() {")
        self.assertEqual(loaded.last_line, 66)

    def test_local_file_changes_are_reread_and_missing_files_fall_back(self):
        self.write_source("first")
        first = self.provider.load(self.snippet)
        self.write_source("updated")
        updated = self.provider.load(self.snippet)
        self.assertNotEqual(updated.digest, first.digest)
        self.assertIn('    let version = "updated";', updated.lines)
        self.file.unlink()
        fallback = self.provider.load(self.snippet)
        self.assertEqual(fallback.kind, "SNAPSHOT")
        self.assertEqual(fallback.first_line, self.snippet.first_line)
        self.assertEqual(fallback.lines, packaged_lines(self.snippet))

    def test_reload_keeps_selection_and_loads_new_code(self):
        self.write_source("first")
        session = Session(sources=self.provider)
        session.update(1)
        self.write_source("updated")
        session.handle("reload")
        self.assertEqual(session.sim.file_index, 0)
        self.assertEqual(session.sim.line_index, 0)
        self.assertIn('    let version = "updated";', session.sim.lines)
        session.handle("next")
        self.assertEqual(session.sim.file_index, 1)

    def test_bundled_flag_and_changed_anchor_use_explicit_snapshots(self):
        self.write_source("local_only")
        bundled = SourceProvider(self.root, bundled=True).load(self.snippet)
        self.assertEqual(bundled.kind, "SNAPSHOT")
        self.assertNotIn("local_only", "\n".join(bundled.lines))
        self.file.write_text("fn renamed() {}", encoding="utf-8")
        missing_anchor = self.provider.load(self.snippet)
        self.assertEqual(missing_anchor.kind, "SNAPSHOT")
        self.assertIn("anchor", missing_anchor.note)

    def test_source_paths_cannot_escape_the_selected_root(self):
        outside = self.root.parent / "outside.rs"
        outside.write_text("pub fn asset_slug() { SHOULD_NOT_DISPLAY }", encoding="utf-8")
        for path in ("../outside.rs", str(outside)):
            result = self.provider.load(replace(self.snippet, source_path=path))
            self.assertEqual(result.kind, "SNAPSHOT")
            self.assertNotIn("SHOULD_NOT_DISPLAY", "\n".join(result.lines))
        self.file.symlink_to(outside)
        self.assertEqual(self.provider.load(self.snippet).kind, "SNAPSHOT")

    def test_large_and_invalid_text_files_use_snapshots(self):
        self.file.write_bytes(b"x" * (MAX_SOURCE_BYTES + 1))
        self.assertEqual(self.provider.load(self.snippet).kind, "SNAPSHOT")
        self.file.write_bytes(b"\xff\xfe\x00")
        self.assertEqual(self.provider.load(self.snippet).kind, "SNAPSHOT")

    def test_terminal_controls_are_escaped(self):
        self.file.write_text("pub fn asset_slug() {\n\t// \x1b[31m\x00\n}", encoding="utf-8")
        result = self.provider.load(self.snippet)
        self.assertEqual(result.kind, "LOCAL")
        self.assertNotIn("\x1b", "\n".join(result.lines))
        self.assertNotIn("\x00", "\n".join(result.lines))
        self.assertIn("    // \\x1b[31m\\x00", result.lines)

