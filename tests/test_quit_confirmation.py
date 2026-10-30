"""Exit must be a deliberate dashboard action, without interrupting live work."""

import os
import shutil
import unittest
from unittest.mock import Mock, patch

from devos.agents import AgentCard, ArenaSnapshot
from devos.app import dispatch_key
from devos.renderer import Renderer
from devos.session import Session
from devos.shell import TerminalFrame
from devos.sources import SourceProvider
from devos.terminal import Canvas, curses
from devos.terminals import TerminalSlotSnapshot
from tests import test_cli
from tests.test_rendering import Screen


class QuitConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(sources=SourceProvider(bundled=True), seed=42)
        self.runtime = Mock()

    def key(self, key):
        dispatch_key(self.session, self.runtime, ord(key) if isinstance(key, str) else key)

    def test_q_opens_confirmation_with_stay_selected_and_enter_cancels(self):
        self.key('q')
        self.assertTrue(self.session.running)
        self.assertTrue(self.session.quit_confirm)
        self.assertFalse(self.session.quit_selected)
        self.assertTrue(self.session.menu_open)
        self.key('\n')
        self.assertFalse(self.session.quit_confirm)
        self.assertTrue(self.session.running)
        self.runtime.assert_not_called()
        self.assertEqual(self.runtime.mock_calls, [])

    def test_repeated_quit_or_interrupt_never_accepts_even_selected_exit(self):
        self.key('Q')
        self.key(curses.KEY_RIGHT)
        self.assertTrue(self.session.quit_selected)
        for key in ('q', 'Q', 3, 'q', 'Q'):
            with self.subTest(key=key):
                self.key(key)
                self.assertTrue(self.session.running)
                self.assertTrue(self.session.quit_confirm)
        self.assertEqual(self.runtime.mock_calls, [])

    def test_explicit_yes_accepts_and_no_or_escape_cancels(self):
        for cancel in ('n', 'N', 27):
            with self.subTest(cancel=cancel):
                self.session.request_quit()
                self.key(curses.KEY_RIGHT)
                self.key(cancel)
                self.assertTrue(self.session.running)
                self.assertFalse(self.session.quit_confirm)
        for confirm in ('y', 'Y'):
            with self.subTest(confirm=confirm):
                session = Session(sources=SourceProvider(bundled=True))
                session.request_quit()
                dispatch_key(session, self.runtime, ord(confirm))
                self.assertFalse(session.running)

    def test_arrow_or_tab_selection_requires_separate_enter(self):
        for move in (curses.KEY_RIGHT, curses.KEY_DOWN, 9):
            with self.subTest(move=move):
                session = Session(sources=SourceProvider(bundled=True))
                session.handle('quit')
                dispatch_key(session, self.runtime, move)
                self.assertTrue(session.quit_selected)
                self.assertTrue(session.running)
                dispatch_key(session, self.runtime, curses.KEY_ENTER)
                self.assertFalse(session.running)

    def test_new_request_always_defaults_to_stay(self):
        self.session.request_quit()
        self.key(curses.KEY_RIGHT)
        self.key('n')
        self.session.request_quit()
        self.assertFalse(self.session.quit_selected)
        self.key(curses.KEY_RIGHT)
        self.key(curses.KEY_LEFT)
        self.assertFalse(self.session.quit_selected)
        self.key('\r')
        self.assertTrue(self.session.running)
        self.assertFalse(self.session.quit_confirm)

    def test_global_escape_requests_confirmation_but_menu_and_zoom_escape_do_not(self):
        self.session.handle('animation')
        self.key(27)
        self.assertFalse(self.session.menu_open)
        self.assertTrue(self.session.running)
        self.session.zoomed = True
        self.key(27)
        self.assertFalse(self.session.zoomed)
        self.assertFalse(self.session.quit_confirm)
        self.key(27)
        self.assertTrue(self.session.quit_confirm)
        self.assertTrue(self.session.running)

    def test_dashboard_ctrl_c_requests_confirmation(self):
        self.key(3)
        self.assertTrue(self.session.quit_confirm)
        self.assertTrue(self.session.running)
        self.key('n')
        self.assertEqual(self.runtime.mock_calls, [])

    def test_confirmation_owns_shortcuts_and_function_keys_before_any_other_modal(self):
        self.session.project_editor = True
        self.session.project_text = 'unchanged'
        self.session.request_quit()
        before = (self.session.screen_id, self.session.animation, self.session.paused,
                  self.session.terminal_focused, tuple(self.session.pending_actions))
        for key in (curses.KEY_F2, curses.KEY_F3, curses.KEY_F4, curses.KEY_RESIZE,
                    ord('!'), ord('2'), ord('g'), ord('l'), ord('p'), ord(' '), ord('s'), ord('u')):
            self.key(key)
        after = (self.session.screen_id, self.session.animation, self.session.paused,
                 self.session.terminal_focused, tuple(self.session.pending_actions))
        self.assertEqual(before, after)
        self.assertEqual(self.session.project_text, 'unchanged')
        self.assertTrue(self.session.quit_confirm)
        self.assertEqual(self.runtime.mock_calls, [])
        self.key(27)
        self.assertTrue(self.session.project_editor)

    def test_focused_child_owns_quit_escape_and_ctrl_c_until_dashboard_is_selected(self):
        self.session.terminal_focused = self.session.terminal_visible = True
        for key in ('q', 'Q', 27, 3):
            self.key(key)
        self.assertEqual([call.args for call in self.runtime.terminals.send.call_args_list],
                         [(b'q', 'left'), (b'Q', 'left'), (b'\x1b', 'left'), (b'\x03', 'left')])
        self.assertTrue(self.session.running)
        self.assertFalse(self.session.quit_confirm)
        self.key(29)
        self.key('q')
        self.assertFalse(self.session.terminal_focused)
        self.assertTrue(self.session.quit_confirm)

    def test_confirmation_draw_resizes_without_io_or_changing_live_work_or_clocks(self):
        self.session.update(.5)
        self.session.terminal_slots = (TerminalSlotSnapshot(
            'left', TerminalFrame(status='LIVE / fixture shell'), False, True, ('/bin/sh',), '/tmp'),)
        self.session.arena = ArenaSnapshot('mission', 'RUNNING', (
            AgentCard('codex', 'Codex', 'builder', 'RUNNING'),), (), (), (), .5, 4, 60)
        self.session.request_quit()
        before = (self.session.visual_time, self.session.protocol_time, self.session.sim.elapsed,
                  self.session.sim.rng.getstate(), tuple(self.session.sim.logs),
                  self.session.arena, self.session.terminal_slots, tuple(self.session.pending_actions))
        with patch('builtins.open', side_effect=AssertionError('renderer opened a file')), \
                patch('subprocess.Popen', side_effect=AssertionError('renderer started a process')):
            for rows, cols in ((4, 12), (16, 48), (24, 80), (44, 144), (60, 240)):
                with self.subTest(rows=rows, cols=cols):
                    screen = Screen(rows, cols)
                    Renderer(Canvas(screen, color=False)).draw(self.session)
                    if rows >= 16 and cols >= 48:
                        for text in ('QUIT DEV/OS?', 'Stay', 'Exit'):
                            self.assertIn(text, screen.text())
        after = (self.session.visual_time, self.session.protocol_time, self.session.sim.elapsed,
                 self.session.sim.rng.getstate(), tuple(self.session.sim.logs),
                 self.session.arena, self.session.terminal_slots, tuple(self.session.pending_actions))
        self.assertEqual(before, after)
        self.assertTrue(self.session.running)
        self.assertTrue(self.session.quit_confirm)


@unittest.skipUnless(os.name == 'posix', 'requires Unix PTY')
class QuitConfirmationTerminalTests(unittest.TestCase):
    def run_terminal(self, **kwargs):
        return test_cli.TerminalTests.run_terminal(self, **kwargs)

    def test_signal_interrupt_cancel_returns_to_work_before_explicit_exit(self):
        output = self.run_terminal(extra=('--no-config', '--bundled', '--duration', '3'), actions=(
            (.25, 'interrupt'), (.45, b'n'), (.65, b'g'), (.85, b'\x1b'),
            (1.05, b'q'), (1.25, b'y')))
        self.assertIn(b'QUIT DEV/OS?', output)
        self.assertIn(b'SWITCH ANIMATION', output)

    @unittest.skipUnless(shutil.which('tmux'), 'optional tmux integration')
    def test_live_shell_survives_confirmation_and_default_stay_then_explicit_exit(self):
        output = self.run_terminal(extra=(
            '--no-config', '--bundled', '--terminal', '--command', '/bin/sh -i', '--duration', '3'),
            actions=((.25, b"printf '__%s__\\n' LIVE_BEFORE; sleep .5; printf '__%s__\\n' LIVE_DURING\r"),
                     (.45, b'\x1dq'), (.65, b'qq'), (.85, b'\n'),
                     (1.05, b"\x1dprintf '__%s__\\n' LIVE_AFTER_STAY\r"),
                     (1.4, b'\x1dq'), (1.6, b'\x1bOC\n')))
        for marker in (b'QUIT DEV/OS?', b'__LIVE_BEFORE__', b'__LIVE_DURING__', b'__LIVE_AFTER_STAY__'):
            self.assertIn(marker, output)


if __name__ == '__main__':
    unittest.main()
