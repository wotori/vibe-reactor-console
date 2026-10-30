from pathlib import Path
import shutil
import tempfile
import time
import unittest

from devos.shell import EmbeddedTerminal, TerminalFrame
from devos.terminals import TerminalManager


class FakeTerminal:
    def __init__(self, width, height, cwd=None, command=None):
        self.size, self.cwd, self.command = (width, height), cwd, command
        self.inputs, self.visible, self.closed = [], True, False

    def set_visible(self, visible):
        self.visible = visible

    def send(self, data):
        self.inputs.append(data)
        return True

    def resize(self, width, height):
        self.size = width, height

    def snapshot(self):
        return TerminalFrame(status=str(self.command))

    def close(self):
        self.closed = True


class TerminalManagerTests(unittest.TestCase):
    def test_construction_and_configuration_do_not_start_processes(self):
        calls = []
        manager = TerminalManager(terminal_factory=lambda *args, **kwargs: calls.append(args))
        manager.configure("left", ("codex",))
        manager.configure("right", ("claude",))
        self.assertEqual(calls, [])
        self.assertIn("NOT STARTED", manager.snapshot().status)

    def test_independent_focus_resize_input_and_hidden_jobs_survive(self):
        manager = TerminalManager(60, 20, terminal_factory=FakeTerminal)
        left = manager.create("left", ("codex",))
        right = manager.create("right", ("claude",))
        manager.send(b"left")
        manager.focus("right")
        manager.send(b"right")
        manager.resize(30, 12, "right")
        self.assertEqual(left.inputs, [b"left"])
        self.assertEqual(right.inputs, [b"right"])
        self.assertEqual(left.size, (60, 20))
        self.assertEqual(right.size, (30, 12))
        manager.set_visible(("left",))
        self.assertFalse(right.visible)
        self.assertFalse(right.closed)
        self.assertIs(manager.create("left"), left)
        self.assertTrue(manager.snapshots()[1].focused)
        manager.close()
        self.assertTrue(left.closed and right.closed)

    def test_invalid_slot_and_reconfigure_running_are_rejected(self):
        manager = TerminalManager(terminal_factory=FakeTerminal)
        with self.assertRaises(ValueError):
            manager.focus("third")
        manager.create()
        with self.assertRaises(RuntimeError):
            manager.configure("left", ("other",))
        manager.close()

    @unittest.skipUnless(shutil.which("tmux"), "optional tmux")
    def test_two_real_processes_run_at_once_with_separate_inputs_and_cleanup(self):
        manager = TerminalManager(50, 8)
        self.addCleanup(manager.close)
        left = manager.create("left", ("/bin/sh", "-i"))
        right = manager.create("right", ("/bin/sh", "-i"))
        self.assertNotEqual(left.socket, right.socket)
        manager.send(b"printf '__%s__\\n' LEFT_ONLY\r", "left")
        manager.send(b"printf '__%s__\\n' RIGHT_ONLY\r", "right")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            ltext = "\n".join("".join(run.text for run in row) for row in manager.snapshot("left").lines)
            rtext = "\n".join("".join(run.text for run in row) for row in manager.snapshot("right").lines)
            if "__LEFT_ONLY__" in ltext and "__RIGHT_ONLY__" in rtext:
                break
            time.sleep(.02)
        self.assertIn("__LEFT_ONLY__", ltext)
        self.assertNotIn("__RIGHT_ONLY__", ltext)
        self.assertIn("__RIGHT_ONLY__", rtext)
        self.assertNotIn("__LEFT_ONLY__", rtext)
        with self.assertRaises(ValueError):
            left.send(b"x" * 16385)
        manager.close()
        self.assertFalse(left._thread.is_alive() or right._thread.is_alive())
