"""SOURCE folder selection through actual Session/Runtime boundaries.

Project contents stay local display data: selecting a folder does not start a
terminal, execute its files, or place its path/text in shareable recordings.
"""

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from devos.app import dispatch_key
from devos.cli import parser, runtime_options
from devos.presets import preferences
from devos.runtime import Runtime
from devos.session import Session
from devos.sources import SourceProvider
from devos.terminal import curses
from devos.terminal import Canvas
from devos.renderer import Renderer
from tests.test_rendering import Screen
from tests import test_cli


class SourceWorkspaceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        environment = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config")})
        environment.start()
        self.addCleanup(environment.stop)
        for target in ("devos.agents.AgentCoordinator.start_mission",
                       "devos.terminals.TerminalManager.create", "devos.task_runner.TaskRunner.start"):
            guard = patch(target, side_effect=AssertionError("SOURCE must never launch a job"))
            guard.start()
            self.addCleanup(guard.stop)

    def project(self, name, filename, text):
        path = self.root / name
        path.mkdir()
        (path / filename).write_text(text, encoding="utf-8")
        return path

    def start(self, project=None, *arguments):
        argv = ["--no-config", "--agents", "watch", "--data-mode", "live", "--cwd", str(self.root)]
        if project is not None:
            argv += ["--project-root", str(project)]
        args = parser().parse_args(argv + list(arguments))
        settings = runtime_options(args)
        provider = SourceProvider(ekza_root=self.root / "absent-ekza", project_root=args.project_root)
        session = Session(sources=provider, **settings)
        runtime = Runtime(args, session)
        self.addCleanup(runtime.close)
        return runtime, session

    def wait_source(self, runtime, marker):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            runtime.session.update(0)
            runtime.update(0)
            if runtime.session.sim.source.kind == "PROJECT" and marker in "\n".join(runtime.session.sim.lines):
                return runtime.session.sim
            time.sleep(.005)
        self.fail("Project source did not reach SOURCE: " + repr((runtime.session.sim.snippet,
                                                                 runtime.session.sim.source)))

    @staticmethod
    def type_text(session, runtime, text):
        for byte in text.encode("utf-8"):
            dispatch_key(session, runtime, byte)

    def choose_folder(self, runtime, path):
        session = runtime.session
        session.handle("source-folder")
        self.assertTrue(session.project_editor)
        dispatch_key(session, runtime, 21)  # Ctrl+U clears a previously selected folder.
        self.type_text(session, runtime, str(path))
        dispatch_key(session, runtime, 13)
        runtime.update(0)
        self.assertFalse(session.project_editor, session.project_error)

    def test_real_project_source_next_reload_and_folder_switch_do_not_change_terminal_cwd(self):
        first = self.project("first-project", "alpha.py", "PRIVATE_SOURCE_ALPHA = 1\nprint(PRIVATE_SOURCE_ALPHA)\n")
        second = self.project("second-project", "beta.c", "int PRIVATE_SOURCE_BETA(void) { return 2; }\n")
        runtime, session = self.start(first)
        sim = self.wait_source(runtime, "PRIVATE_SOURCE_ALPHA")
        self.assertEqual(Path(sim.snippet.path).name, "alpha.py")
        self.assertEqual(sim.source.first_line, 1)
        self.assertEqual(Path(session.sources.project_root), first)
        self.assertEqual(runtime.terminals.cwd, str(self.root))
        original_cwd = runtime.args.cwd

        # R reads current bytes; N also works when only one eligible file exists.
        (first / "alpha.py").write_text("PRIVATE_SOURCE_UPDATED = 3\n", encoding="utf-8")
        session.handle("reload")
        self.wait_source(runtime, "PRIVATE_SOURCE_UPDATED")
        session.handle("next")
        self.wait_source(runtime, "PRIVATE_SOURCE_UPDATED")
        self.choose_folder(runtime, second)
        sim = self.wait_source(runtime, "PRIVATE_SOURCE_BETA")
        self.assertEqual(Path(sim.snippet.path).name, "beta.c")
        self.assertNotIn("PRIVATE_SOURCE_ALPHA", "\n".join(sim.lines))
        self.assertEqual(Path(session.sources.project_root), second)
        self.assertEqual((runtime.args.cwd, runtime.terminals.cwd), (original_cwd, str(self.root)))
        self.assertEqual(runtime.terminals._terminals, {})
        self.assertEqual(runtime.arena.workers, {})
        self.assertEqual(runtime.tasks.snapshot().state, "idle")
        self.assertEqual((second / "beta.c").read_text(), "int PRIVATE_SOURCE_BETA(void) { return 2; }\n")

    def test_folder_editor_owns_unicode_text_shortcuts_function_keys_and_cancel(self):
        session = Session(sources=SourceProvider(ekza_root=self.root / "absent-ekza", bundled=True))
        runtime = Mock()
        session.handle("source-folder")
        dispatch_key(session, runtime, 21)
        text = str(self.root / "qgp123 Проект 项目")
        self.type_text(session, runtime, text)
        for key in (curses.KEY_F2, curses.KEY_F3, curses.KEY_F4, curses.KEY_LEFT, curses.KEY_RIGHT):
            dispatch_key(session, runtime, key)
        self.assertTrue(session.project_editor)
        self.assertEqual(session.project_text, text)
        self.assertTrue(session.running)
        self.assertFalse(session.palette_open)
        self.assertFalse(session.terminal_focused)
        self.assertEqual(session.screen_id, "cockpit")
        self.assertEqual(tuple(session.pending_actions), ())
        runtime.ensure_terminal.assert_not_called()
        runtime.terminals.send.assert_not_called()

        # A UTF-8 code point is removed as one character rather than broken bytes.
        dispatch_key(session, runtime, 127)
        self.assertEqual(session.project_text, text[:-1])
        dispatch_key(session, runtime, 27)
        self.assertFalse(session.project_editor)
        self.assertEqual(tuple(session.pending_actions), ())
        self.assertIsNone(session.sources.project_root)

        # Native terminal ownership still comes before ordinary dashboard letters.
        session.terminal_visible = session.terminal_focused = True
        for byte in b"qgp123":
            dispatch_key(session, runtime, byte)
        self.assertEqual(b"".join(call.args[0] for call in runtime.terminals.send.call_args_list), b"qgp123")
        self.assertTrue(session.running)
        self.assertFalse(session.project_editor)

    def test_folder_form_resizes_without_io_or_advancing_the_simulation(self):
        session = Session(sources=SourceProvider(bundled=True), animation="reactor")
        session.handle("source-folder")
        session.project_text = "/example/" + "project/" * 30
        before = (session.sim.elapsed, session.sim.rng.getstate(), tuple(session.sim.logs))
        for rows, cols in ((4, 12), (16, 48), (24, 80), (44, 144), (60, 240)):
            screen = Screen(rows, cols)
            with patch("os.scandir", side_effect=AssertionError("Renderer cannot scan")), \
                 patch("builtins.open", side_effect=AssertionError("Renderer cannot read")):
                Renderer(Canvas(screen, color=False)).draw(session)
            if cols >= 80:
                self.assertIn("SOURCE / PROJECT FOLDER", screen.text())
            self.assertEqual((session.sim.elapsed, session.sim.rng.getstate(), tuple(session.sim.logs)), before)

    def test_invalid_folder_keeps_editor_and_previous_project_until_corrected(self):
        first = self.project("valid", "actual.py", "ACTUAL_LOCAL_FILE = True\n")
        runtime, session = self.start(first)
        self.wait_source(runtime, "ACTUAL_LOCAL_FILE")
        session.handle("source-folder")
        dispatch_key(session, runtime, 21)
        self.type_text(session, runtime, str(self.root / "missing-project"))
        dispatch_key(session, runtime, 13)
        runtime.update(0)
        self.assertTrue(session.project_editor)
        self.assertTrue(session.project_error)
        self.assertEqual(Path(session.sources.project_root), first)
        dispatch_key(session, runtime, 21)
        self.type_text(session, runtime, str(first))
        dispatch_key(session, runtime, 13)
        runtime.update(0)
        self.assertFalse(session.project_editor, session.project_error)
        self.wait_source(runtime, "ACTUAL_LOCAL_FILE")

    def test_project_root_and_source_text_are_not_recorded_or_exported_with_scene(self):
        project = self.project("PRIVATE_ROOT_DO_NOT_SHARE", "private.py", "PRIVATE_CONTENT_DO_NOT_SHARE = 'local only'\n")
        recording = self.root / "events.jsonl"
        export_dir = self.root / "exports"
        runtime, session = self.start(project, "--record", str(recording), "--export-dir", str(export_dir))
        self.wait_source(runtime, "PRIVATE_CONTENT_DO_NOT_SHARE")
        public = preferences(session)
        self.assertNotIn("project_root", public)
        self.assertNotIn(str(project), json.dumps(public))
        runtime.action("capsule-export")
        runtime.close()
        capsules = tuple(export_dir.glob("*.capsule.json"))
        self.assertEqual(len(capsules), 1)
        for path in (recording,) + capsules:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(str(project), text)
            self.assertNotIn("PRIVATE_ROOT_DO_NOT_SHARE", text)
            self.assertNotIn("PRIVATE_CONTENT_DO_NOT_SHARE", text)
            self.assertNotIn("project_root", text)

    def test_imported_preset_cannot_select_a_local_project_or_change_terminal_directory(self):
        project = self.project("private-import-target", "never.py", "IMPORT_MUST_NOT_SCAN_ME = 1\n")
        preset = self.root / "untrusted-preset.json"
        preset.write_text(json.dumps({"schema_version": 1, "visual": "reactor", "project_root": str(project),
                                     "source_project": str(project), "cwd": str(project), "command": "never"}),
                          encoding="utf-8")
        args = parser().parse_args(["--no-config", "--preset", str(preset)])
        with patch("devos.sources.SourceProvider.set_project_root", side_effect=AssertionError("Preset cannot select a root")), \
             patch("subprocess.Popen", side_effect=AssertionError("Preset cannot launch a process")):
            resolved = runtime_options(args)
            session = Session(sources=SourceProvider(ekza_root=self.root / "absent-ekza", bundled=True), **resolved)
        self.assertEqual(session.animation, "reactor")
        self.assertIsNone(args.project_root)
        self.assertIsNone(args.cwd)
        self.assertIsNone(session.sources.project_root)
        self.assertNotIn("project_root", resolved)
        self.assertNotIn("IMPORT_MUST_NOT_SCAN_ME", "\n".join(session.sim.lines))

    @unittest.skipUnless(os.name == "posix", "requires Unix PTY")
    def test_actual_curses_restarts_with_visual_selected_from_palette(self):
        config = self.root / "local-settings.json"
        config.write_text('{"schema_version": 1}\n', encoding="utf-8")
        output = test_cli.TerminalTests.run_terminal(self, extra=("--config", str(config), "--bundled"), actions=(
            (.18, b"p"), (.30, b"Visual / PLASMA REACTOR\n"), (.72, b"q")))
        self.assertTrue(b"PLASMA CORE" in output, output[-1500:].decode(errors="replace"))
        self.assertEqual(json.loads(config.read_text())["visual"], "reactor")
        restarted = test_cli.TerminalTests.run_terminal(self, extra=("--config", str(config), "--bundled"),
                                               actions=((.58, b"q"),))
        self.assertTrue(b"PLASMA CORE" in restarted, restarted[-1500:].decode(errors="replace"))
        self.assertEqual(json.loads(config.read_text())["visual"], "reactor")

    @unittest.skipUnless(os.name == "posix", "requires Unix PTY")
    def test_actual_curses_folder_editor_reads_and_remembers_a_project(self):
        project = self.project("qgp123-проект-项目", "runtime_fixture.py",
                               "ACTUAL_SOURCE_FROM_FOLDER = 42\nprint(ACTUAL_SOURCE_FROM_FOLDER)\n")
        config = self.root / "source-settings.json"
        config.write_text('{"schema_version": 1}\n', encoding="utf-8")
        output = test_cli.TerminalTests.run_terminal(self, extra=("--config", str(config), "--duration", "1.5"),
                                           actions=((.16, b"u"), (.32, b"\x15" + str(project).encode("utf-8") + b"\n"),
                                                    (1.15, b"q")))
        # curses sends incremental cursor/color updates between typed characters;
        # the actual source bytes are asserted in the Runtime test above.
        self.assertTrue(b"PROJECT" in output, output[-1500:].decode(errors="replace"))
        self.assertTrue(b"runtime_fixture.py" in output, output[-1500:].decode(errors="replace"))
        self.assertEqual(Path(json.loads(config.read_text())["project_root"]), project)
        restarted = test_cli.TerminalTests.run_terminal(self, extra=("--config", str(config)), actions=((.75, b"q"),))
        self.assertTrue(b"PROJECT" in restarted, restarted[-1500:].decode(errors="replace"))
        self.assertTrue(b"runtime_fixture.py" in restarted, restarted[-1500:].decode(errors="replace"))


if __name__ == "__main__":
    unittest.main()
