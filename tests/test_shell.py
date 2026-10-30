import os
from pathlib import Path
import shutil
import subprocess
import time
import unittest
from unittest.mock import patch

from devos.app import action_for_key, terminal_key
from devos.session import Session
from devos.shell import EmbeddedTerminal, Style, cell_width, clip_cells, styled_lines
from devos.terminal import curses


def frame_text(frame):
    return "\n".join("".join(run.text for run in row) for row in frame.lines)


class CaptureTests(unittest.TestCase):
    def test_colors_truecolor_selection_and_reset_without_control_escape_leaks(self):
        lines = styled_lines("a\x1b[1;38;5;51mb\x1b[0mc\x1b[7md\x1b[27m"
                             "\x1b[38;2;20;40;60me\x1b[48;5;196mf\x1b[0mg", 40, 1)
        self.assertEqual("".join(run.text for run in lines[0]), "abcdefg")
        runs = {run.text: run.style for run in lines[0]}
        self.assertEqual(runs["b"], Style(51, True, False))
        self.assertEqual(runs["c"], Style())
        self.assertTrue(runs["d"].underline)
        self.assertEqual(runs["e"].foreground, (20, 40, 60))
        self.assertEqual(runs["f"].foreground, (20, 40, 60))
        self.assertEqual(runs["g"], Style())

    def test_capture_strips_osc_clipboard_cursor_sequences_and_control_characters(self):
        lines = styled_lines("ok\x1b]52;c;Zm9v\x07\x1b[2J\x1b[H!\x00\x07\r\x7f", 20, 2)
        self.assertEqual("".join(run.text for run in lines[0]), "ok!")

    def test_utf8_cjk_and_combining_marks_use_cell_width_for_clipping(self):
        self.assertEqual(clip_cells("界面abc", 5), "界面a")
        self.assertEqual(clip_cells("e\u0301xy", 2), "e\u0301x")
        runs = styled_lines("界\x1b[31mа\u0301бв", 4, 1)[0]
        self.assertEqual(runs[1].column, 2)
        self.assertEqual("".join(run.text for run in runs), "界а\u0301б")
        self.assertEqual(sum(cell_width(c) for run in runs for c in run.text), 4)

    def test_missing_tmux_is_explicit_and_does_not_start_a_process(self):
        with patch("devos.shell.shutil.which", return_value=None), patch("devos.shell.subprocess.run") as run:
            with self.assertRaisesRegex(RuntimeError, "needs tmux"):
                EmbeddedTerminal(80, 24)
            run.assert_not_called()

    def test_focus_source_and_scene_changes_preserve_the_terminal(self):
        session = Session()
        marker = object()
        session.terminal_frame = marker
        session.handle("terminal")
        self.assertTrue(session.terminal_visible and session.terminal_focused)
        session.handle("terminal")
        self.assertTrue(session.terminal_visible)
        self.assertFalse(session.terminal_focused)
        session.handle("source")
        self.assertFalse(session.terminal_visible)
        session.handle("terminal")
        session.open_menu("animations")
        self.assertFalse(session.terminal_focused)
        session.activate("bare-metal")
        self.assertIs(session.terminal_frame, marker)
        self.assertTrue(session.terminal_visible)

    @unittest.skipIf(curses is None, "curses unavailable")
    def test_child_keys_are_distinct_from_dashboard_shortcuts(self):
        self.assertEqual(action_for_key(curses.KEY_F2), "terminal")
        self.assertEqual(action_for_key(curses.KEY_F3), "source")
        self.assertEqual(terminal_key(ord("q")), b"q")
        self.assertEqual(terminal_key(3), b"\x03")
        self.assertEqual(terminal_key(9), b"\t")
        self.assertEqual(terminal_key(curses.KEY_UP), "Up")
        self.assertEqual(terminal_key(curses.KEY_DC), "DC")


@unittest.skipUnless(os.name == "posix" and shutil.which("tmux"), "optional tmux integration")
class EmbeddedTerminalTests(unittest.TestCase):
    def terminal(self, **kwargs):
        terminal = EmbeddedTerminal(60, 16, **kwargs)
        self.addCleanup(terminal.close)
        return terminal

    def until(self, terminal, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = terminal.snapshot()
            if predicate(frame):
                return frame
            if frame.dead:
                self.fail(frame.status + "\n" + frame_text(frame))
            time.sleep(.02)
        self.fail("terminal did not reach expected state\n" + str(terminal.snapshot()))

    def test_real_input_utf8_cwd_resize_and_server_cleanup(self):
        import tempfile

        with tempfile.TemporaryDirectory() as cwd:
            terminal = self.terminal(cwd=cwd, command=("/bin/sh", "-i"))
            terminal.send("printf '__%s__\\n' REAL_INPUT; printf 'Привет\\n'; pwd; stty size\r".encode())
            frame = self.until(terminal, lambda f: "__REAL_INPUT__" in frame_text(f) and "16 60" in frame_text(f))
            text = frame_text(frame)
            self.assertIn("Привет", text)
            self.assertIn(str(Path(cwd).resolve()), text.replace("\n", ""))
            terminal.resize(42, 10)
            time.sleep(.1)
            terminal.send(b"stty size\r")
            self.until(terminal, lambda f: "10 42" in frame_text(f))
            terminal.close()
            self.assertFalse(terminal._thread.is_alive())
            result = subprocess.run([terminal.executable, "-L", terminal.socket, "list-sessions"], capture_output=True)
            self.assertNotEqual(result.returncode, 0, "private tmux server leaked")

    def test_alternate_screen_cursor_clear_and_restore_are_emulated(self):
        import sys

        code = ("import sys,time; print('MAIN_SCREEN',flush=True); time.sleep(.25); "
                "sys.stdout.write('\\x1b[?1049h\\x1b[2J\\x1b[3;5H\\x1b[32mALT_SCREEN'); "
                "sys.stdout.flush(); time.sleep(.5); "
                "sys.stdout.write('\\x1b[?1049l'); sys.stdout.flush(); time.sleep(3)")
        terminal = self.terminal(command=(sys.executable, "-c", code))
        frame = self.until(terminal, lambda f: "ALT_SCREEN" in frame_text(f))
        self.assertNotIn("MAIN_SCREEN", frame_text(frame))
        self.assertIn("ALT_SCREEN", "".join(run.text for run in frame.lines[2]))
        self.assertEqual(frame.lines[2][0].text, "    ")
        self.assertEqual(frame.lines[2][1].column, 4)
        self.assertEqual(frame.lines[2][1].style.foreground, 2)
        self.until(terminal, lambda f: "MAIN_SCREEN" in frame_text(f) and "ALT_SCREEN" not in frame_text(f))

    def test_completed_command_remains_visible_instead_of_losing_the_pane(self):
        terminal = self.terminal(command=("/bin/sh", "-c", "printf '__FINISHED__\\n'"))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            frame = terminal.snapshot()
            if frame.dead:
                break
            time.sleep(.02)
        self.assertTrue(frame.dead)
        self.assertIn("__FINISHED__", frame_text(frame))
        self.assertIn("PROCESS EXITED", frame.status)

    def test_command_arguments_are_literal_even_with_shell_and_tmux_punctuation(self):
        import json
        import sys

        arguments = ("a b", ";", "$(not-a-command)", "`literal`", "quote'and\"")
        code = "import json,sys,time; print(json.dumps(sys.argv[1:]),flush=True); time.sleep(3)"
        terminal = self.terminal(command=(sys.executable, "-c", code, *arguments))
        expected = json.dumps(arguments)
        self.until(terminal, lambda f: expected in frame_text(f).replace("\n", ""))
