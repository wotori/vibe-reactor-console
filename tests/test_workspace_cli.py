import os
import shutil
import unittest
from tests import test_cli


@unittest.skipUnless(os.name == 'posix', 'requires Unix PTY')
class WorkspaceCliTests(unittest.TestCase):
    def run_terminal(self, **kwargs):
        return test_cli.TerminalTests.run_terminal(self, **kwargs)

    def test_seven_screens_and_palette_work_in_real_curses(self):
        output = self.run_terminal(extra=('--no-config', '--bundled'), actions=(
            (.12, b'2'), (.22, b'3'), (.32, b'4'), (.42, b'5'), (.52, b'6'), (.62, b'7'),
            (.72, b'p'), (.78, b'queue'), (.85, b'\x1b'), (.93, b'q')))
        for marker in (b'VISUAL LAB', b'AGENT ARENA', b'SITE OBSERVATORY', b'BODY SIGNAL',
                       b'EKZA ATLAS', b'REPLAY STUDIO', b'COMMANDS'):
            self.assertIn(marker, output)

    def test_unconfigured_live_source_is_visible_as_unknown(self):
        output = self.run_terminal(extra=('--no-config', '--screen', 'site', '--data-mode', 'live'))
        self.assertIn(b'UNAVAILABLE', output)
        self.assertIn(b'LIVE', output)

    def test_binding_form_owns_keys_and_closes_in_real_curses(self):
        output = self.run_terminal(extra=('--no-config', '--bundled', '--screen', 'visual-lab'), actions=(
            (.16, b'l'), (.30, b'\x1b[B\x1b[C'), (.42, b'r'), (.58, b'\x1b'), (.76, b'q')))
        self.assertIn(b'BINDING SETTINGS', output)
        self.assertIn(b'EMA seconds', output)

    def test_palette_exports_a_reloadable_capsule_from_real_curses(self):
        import tempfile
        from pathlib import Path
        from devos.presets import load
        with tempfile.TemporaryDirectory() as directory:
            self.run_terminal(extra=('--no-config', '--bundled', '--export-dir', directory), actions=(
                (.18, b'p'), (.32, b'Scene / export capsule\n'), (.85, b'q')))
            paths = tuple(Path(directory).glob('*.capsule.json'))
            self.assertEqual(len(paths), 1)
            settings = load(paths[0])
            self.assertIn('binding_config', settings)
            self.assertNotIn('terminal_command', settings)

    @unittest.skipUnless(shutil.which('tmux'), 'optional tmux')
    def test_two_real_terminal_slots_route_input_and_survive_screen_switch(self):
        output = self.run_terminal(extra=('--no-config', '--screen', 'arena', '--agents', 'watch',
                                          '--command', 'sh', '--right-command', 'sh'), actions=(
            (.25, b'printf "LEFT_REAL_SLOT\\n"\n'),
            (.45, b'\x1d\x1bOS'), (.55, b'printf "RIGHT_REAL_SLOT\\n"\n'),
            (.78, b'\x1d2'), (.88, b'3')))
        self.assertIn(b'LEFT_REAL_SLOT', output)
        self.assertIn(b'RIGHT_REAL_SLOT', output)
        self.assertIn(b'VISUAL LAB', output)
