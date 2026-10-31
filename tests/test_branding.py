"""Public launchers retain the same passive CLI and version contract."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from devos import __version__


PROJECT = Path(__file__).resolve().parents[1]


class BrandingTests(unittest.TestCase):
    def test_checkout_launchers_work_outside_the_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            for launcher in ("dev_console.py", "dev_show.py"):
                with self.subTest(launcher=launcher):
                    result = subprocess.run(
                        [sys.executable, str(PROJECT / launcher), "--version"],
                        cwd=directory, env=dict(os.environ, XDG_CONFIG_HOME=directory),
                        capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), "Vibe Reactor Console " + __version__)
                    self.assertEqual(list(Path(directory).iterdir()), [])

    def test_new_launcher_help_exposes_public_brand_and_arcade(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(PROJECT / "dev_console.py"), "--help"],
                cwd=directory, env=dict(os.environ, XDG_CONFIG_HOME=directory),
                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: vibe-reactor-console", result.stdout)
        self.assertIn("Vibe Reactor Console", result.stdout)
        self.assertIn("arcade", result.stdout)
        self.assertIn("--animation", result.stdout)


if __name__ == "__main__":
    unittest.main()
