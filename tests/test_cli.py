import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from devos import __version__


PROJECT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def invoke(self, *args):
        with tempfile.TemporaryDirectory() as config_dir:
            return subprocess.run([sys.executable, "-m", "devos", *args], cwd=PROJECT,
                                  env=dict(os.environ, XDG_CONFIG_HOME=config_dir),
                                  capture_output=True, text=True, timeout=5)

    def test_help_version_and_modes_need_no_terminal(self):
        for option, expected in (("--help", "TAB:"), ("--version", "Vibe Reactor Console " + __version__),
                                 ("--list-modes", "BARE METAL")):
            with self.subTest(option=option):
                result = self.invoke(option)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)

    def test_bad_arguments_fail_before_initializing_terminal(self):
        for args in (("--speed", "0"), ("--speed", "9"), ("--speed", "nan"),
                     ("--duration", "inf"), ("--duration", "-1"), ("--fps", "0"),
                     ("--mode", "missing"), ("--background", "invalid"), ("--fps", "121"),
                     ("--animation", "unknown"), ("--consumer", "unknown"), ("--auto-cycle", "0")):
            with self.subTest(args=args):
                result = self.invoke(*args)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("Traceback", result.stderr)

    def test_real_terminal_arguments_validate_before_starting_curses(self):
        for args in (("--command", ""), ("--command", "'unclosed"), ("--cwd", "/this-directory-does-not-exist")):
            result = self.invoke(*args)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Traceback", result.stderr)

    def test_redirected_output_gets_clear_error(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 1)
        self.assertIn("interactive terminal", result.stderr)

    def test_reactor_cli_choice_is_accepted(self):
        result = self.invoke("--animation", "reactor", "--fps", "120", "--background", "black")
        self.assertEqual(result.returncode, 1)
        self.assertIn("interactive terminal", result.stderr)
        self.assertNotIn("invalid choice", result.stderr)

    def test_direct_launcher_works_from_another_directory(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(PROJECT / "dev_show.py"), "--list-modes"],
                                    cwd=directory, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("BARE METAL", result.stdout)


@unittest.skipUnless(os.name == "posix", "PTY integration requires a Unix terminal")
class TerminalTests(unittest.TestCase):
    def run_terminal(self, actions=(), mode="bare-metal", term="xterm-256color", extra=()):
        import fcntl
        import pty
        import select
        import signal
        import struct
        import termios
        import time

        def state(fd):
            flags = termios.tcgetattr(fd)
            # PENDIN is a transient macOS flag, set when curses restores echo.
            flags[3] &= ~getattr(termios, "PENDIN", 0)
            return flags

        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 44, 144, 0, 0))
        original = state(slave)
        config_dir = tempfile.TemporaryDirectory()
        process = subprocess.Popen(
            [sys.executable, "-m", "devos", "--mode", mode, "--seed", "42", "--duration", "1.0", *extra],
            cwd=PROJECT, stdin=slave, stdout=slave, stderr=slave,
            env=dict(os.environ, TERM=term, XDG_CONFIG_HOME=config_dir.name))
        output = bytearray()
        pending = list(actions)
        start = time.monotonic()
        try:
            while time.monotonic() - start < 5:
                elapsed = time.monotonic() - start
                while pending and elapsed >= pending[0][0]:
                    _, action = pending.pop(0)
                    if action == "resize":
                        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
                        process.send_signal(signal.SIGWINCH)
                    elif action == "interrupt":
                        process.send_signal(signal.SIGINT)
                    else:
                        os.write(master, action)
                if select.select([master], [], [], 0.02)[0]:
                    output.extend(os.read(master, 65536))
                if process.poll() is not None:
                    while select.select([master], [], [], 0)[0]:
                        output.extend(os.read(master, 65536))
                    break
            self.assertIsNotNone(process.poll(), "terminal process did not exit")
            self.assertEqual(process.returncode, 0, output.decode(errors="replace")[-2000:])
            self.assertNotIn(b"Traceback", output)
            self.assertIn(b"\x1b[?1049h", output)
            self.assertIn(b"\x1b[?1049l", output)
            self.assertEqual(original, state(slave), "terminal modes were not restored")
            return bytes(output)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)
            os.close(slave)
            config_dir.cleanup()

    def test_bare_metal_auto_exit(self):
        output = self.run_terminal(extra=("--background", "black"))
        for marker in (b"BARE METAL", b"HYPERCORE", b"CPU STATE", b"MEMORY"):
            self.assertIn(marker, output)

    def test_ekza_snapshot_screen_and_reload(self):
        output = self.run_terminal(mode="ekza", extra=("--bundled",),
                                   actions=((0.2, b"nr"), (0.4, b"q")))
        for marker in (b"EKZA SPACE", b"EKZA CORE", b"ASSET ROUTE", b"SNAPSHOT"):
            self.assertIn(marker, output)

    def test_actual_tab_arrows_enter_pause_resize_and_quit(self):
        output = self.run_terminal(actions=(
            (0.15, b"\t"), (0.22, b"\x1bOB\x1bOB"), (0.30, b"\n"),
            (0.38, b" "), (0.46, "resize"), (0.56, b"t+-nb"), (0.68, b"q")))
        self.assertIn(b"SWITCH MODE", output)
        self.assertIn(b"HYPERSPACE", output)
        self.assertIn(b"PAUSED", output)

    def test_ctrl_c_restores_terminal(self):
        self.run_terminal(actions=((0.2, "interrupt"),), mode="lab")

    def test_eight_color_terminal(self):
        self.run_terminal(mode="hyperspace", term="xterm")

    def test_g_picker_shared_options_and_scope_in_actual_terminal(self):
        output = self.run_terminal(mode="ekza", extra=("--bundled", "--fps", "60"), actions=(
            (.15, b"g"), (.22, b"\x1bOC"), (.28, b"\n"),
            (.34, b"\x1bOB\n"), (.40, b"\x1bOD\x1bOB\n"), (.55, b"w"), (.66, b"q")))
        for label in (b"SWITCH ANIMATION", b"SHARED OPTIONS", b"MATH SCOPE", b"SOLID CUBE"):
            self.assertIn(label, output)

    def test_reactor_runs_and_resizes_in_actual_terminal(self):
        output = self.run_terminal(mode="ekza", extra=(
            "--bundled", "--animation", "reactor", "--fps", "120", "--background", "black"),
            actions=((.25, "resize"), (.50, b"g"), (.70, b"q")))
        for label in (b"PLASMA CORE", b"REACTOR", b"PLASMA REACTOR", b"SWITCH ANIMATION"):
            self.assertIn(label, output)

    @unittest.skipUnless(shutil.which("tmux"), "optional tmux integration")
    def test_embedded_shell_input_interrupt_focus_and_dashboard_menu(self):
        output = self.run_terminal(mode="ekza", extra=("--bundled", "--terminal", "--command", "/bin/sh -i"), actions=(
            (.15, b"printf '__%s__\\n' CHILD_INPUT_OK\r"), (.30, b"sleep 5\r"),
            (.43, b"\x03"), (.55, b"printf '__%s__\\n' AFTER_CTRL_C\r"),
            (.72, b"\x1dg"), (.86, b"q")))
        for label in (b"01 / TERMINAL", b"__CHILD_INPUT_OK__", b"__AFTER_CTRL_C__", b"SWITCH ANIMATION"):
            self.assertIn(label, output)

    @unittest.skipUnless(shutil.which("tmux"), "optional tmux integration")
    def test_embedded_shell_receives_resize_and_can_return_to_source(self):
        output = self.run_terminal(mode="ekza", extra=("--bundled", "--terminal", "--command", "/bin/sh -i"), actions=(
            (.20, "resize"), (.40, b"stty size\r"), (.65, b"\x1bOR"), (.80, b"q")))
        self.assertIn(b"10 44", output)
        self.assertIn(b"SOURCE", output)
        self.assertIn(b"ekza-bevy-sdk/src/assets.rs", output)
