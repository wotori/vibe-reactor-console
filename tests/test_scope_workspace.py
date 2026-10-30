"""The math scope behaves as a workstation instrument, without stealing input."""

import os
import unittest
from unittest.mock import Mock, patch

from devos.app import action_for_key, dispatch_key
from devos.renderer import Renderer
from devos.session import Session
from devos.shell import TerminalFrame
from devos.sources import SourceProvider
from devos.terminal import Canvas
from tests import test_cli
from tests.test_rendering import Screen


class ScopeWorkspaceTests(unittest.TestCase):
    def session(self, **kwargs):
        return Session(sources=SourceProvider(bundled=True), seed=42, **kwargs)

    def test_automatic_functions_follow_active_wall_time_independently_of_speed(self):
        slow = self.session(scope_mode='harmonics', scope_auto_cycle=2, speed=.25)
        fast = self.session(scope_mode='harmonics', scope_auto_cycle=2, speed=8)
        for session in (slow, fast):
            for _ in range(5):
                session.update(.5)
            self.assertEqual(session.effective_scope_mode, 'parabola')
            self.assertAlmostEqual(session.scope_elapsed, .5)
            self.assertEqual(session.options.scope_mode, 'parabola')
        self.assertAlmostEqual(fast.visual_time / slow.visual_time, 32)

    def test_pause_and_each_dashboard_modal_freeze_automatic_function_selection(self):
        session = self.session(scope_mode='harmonics', scope_auto_cycle=2)
        runtime = Mock()
        session.update(.5)
        session.handle('pause')
        session.update(5)
        self.assertAlmostEqual(session.scope_elapsed, .5)
        self.assertEqual(session.effective_scope_mode, 'harmonics')
        session.handle('pause')
        for action in ('animation', 'palette', 'help', 'binding-settings', 'source-folder', 'quit'):
            with self.subTest(action=action):
                session.handle(action)
                self.assertTrue(session.menu_open)
                session.update(5)
                self.assertAlmostEqual(session.scope_elapsed, .5)
                self.assertEqual(session.effective_scope_mode, 'harmonics')
                dispatch_key(session, runtime, 27)
                self.assertFalse(session.menu_open)
                self.assertTrue(session.running)
        session.update(1.5)
        self.assertEqual(session.effective_scope_mode, 'parabola')
        self.assertAlmostEqual(session.scope_elapsed, 0)
        self.assertEqual(runtime.mock_calls, [])

    def test_manual_next_continues_current_automatic_function_and_restarts_interval(self):
        session = self.session(scope_mode='harmonics', scope_auto_cycle=2)
        session.update(4.25)
        self.assertEqual(session.effective_scope_mode, 'lissajous')
        session.handle('scope-next')
        self.assertEqual(session.effective_scope_mode, 'fourier')
        self.assertEqual(session.options.scope_mode, 'fourier')
        self.assertAlmostEqual(session.scope_elapsed, 0)
        self.assertEqual(session.options.scope_auto_cycle, 2)
        session.update(1.75)
        self.assertEqual(session.effective_scope_mode, 'fourier')
        session.update(.25)
        self.assertEqual(session.effective_scope_mode, 'wave-packet')

    def test_auto_toggle_can_be_disabled_without_changing_the_displayed_function(self):
        session = self.session(scope_mode='harmonics')
        session.handle('scope-auto')
        self.assertGreater(session.options.scope_auto_cycle, 0)
        session.update(session.options.scope_auto_cycle)
        self.assertEqual(session.effective_scope_mode, 'parabola')
        for _ in range(4):
            if not session.options.scope_auto_cycle:
                break
            session.handle('scope-auto')
        self.assertEqual(session.options.scope_auto_cycle, 0)
        session.update(30)
        self.assertEqual(session.effective_scope_mode, 'parabola')

    def test_recorded_replay_functions_do_not_advance_with_the_local_auto_clock(self):
        session = self.session(scope_mode='parabola', scope_auto_cycle=2, screen='replay')
        session.replay_status = Mock()
        session.update(5)
        self.assertEqual(session.effective_scope_mode, 'parabola')
        self.assertAlmostEqual(session.scope_elapsed, 0)

    def test_f_selects_functions_but_child_and_quit_confirmation_own_their_keys(self):
        session = self.session(scope_mode='harmonics')
        runtime = Mock()
        for char in 'fF':
            self.assertEqual(action_for_key(ord(char)), 'scope-next')
        session.terminal_focused = session.terminal_visible = True
        for char in 'fFwW':
            dispatch_key(session, runtime, ord(char))
        self.assertEqual(b''.join(call.args[0] for call in runtime.terminals.send.call_args_list), b'fFwW')
        self.assertEqual(session.effective_scope_mode, 'harmonics')
        self.assertTrue(session.options.scope)
        dispatch_key(session, runtime, 29)
        dispatch_key(session, runtime, ord('f'))
        self.assertEqual(session.effective_scope_mode, 'parabola')
        dispatch_key(session, runtime, ord('q'))
        for char in 'fFwW':
            dispatch_key(session, runtime, ord(char))
        self.assertEqual(session.effective_scope_mode, 'parabola')
        self.assertTrue(session.options.scope)
        self.assertTrue(session.quit_confirm)
        self.assertTrue(session.running)

    def test_palette_selects_parabola_and_visibility_preserves_selection(self):
        session = self.session(scope_mode='harmonics')
        runtime = Mock()
        dispatch_key(session, runtime, ord('p'))
        for char in 'parabola':
            dispatch_key(session, runtime, ord(char))
        commands = session.palette_commands
        self.assertEqual(len(commands), 1)
        self.assertEqual((commands[0].kind, commands[0].value), ('scope-mode', 'parabola'))
        dispatch_key(session, runtime, 10)
        self.assertFalse(session.palette_open)
        self.assertEqual(session.effective_scope_mode, 'parabola')
        dispatch_key(session, runtime, ord('w'))
        self.assertFalse(session.options.scope)
        dispatch_key(session, runtime, ord('W'))
        self.assertTrue(session.options.scope)
        self.assertEqual(session.effective_scope_mode, 'parabola')
        self.assertEqual(runtime.mock_calls, [])

    def test_fullscreen_command_preserves_code_visual_and_existing_terminal(self):
        session = self.session(scope_mode='rose', scope=False, screen='arena', animation='reactor')
        runtime = Mock()
        session.terminal_visible = True
        session.terminal_frame = TerminalFrame(status='LIVE / existing shell')
        before = (session.mode_id, session.animation, session.sim.snippet, session.terminal_frame)
        dispatch_key(session, runtime, ord('p'))
        for char in 'Math scope / fullscreen':
            dispatch_key(session, runtime, ord(char))
        self.assertEqual(len(session.palette_commands), 1)
        dispatch_key(session, runtime, 10)
        self.assertEqual(session.screen_id, 'cockpit')
        self.assertEqual(session.focused_panel, 'scope')
        self.assertTrue(session.zoomed)
        self.assertTrue(session.options.scope)
        self.assertEqual(session.effective_scope_mode, 'rose')
        self.assertEqual(before, (session.mode_id, session.animation, session.sim.snippet, session.terminal_frame))
        self.assertTrue(session.terminal_visible)
        dispatch_key(session, runtime, 27)
        self.assertFalse(session.zoomed)
        self.assertFalse(session.quit_confirm)
        self.assertTrue(session.running)
        self.assertEqual(runtime.mock_calls, [])

    def test_zoomed_scope_animates_and_renderer_is_pure_across_sizes_and_backgrounds(self):
        session = self.session(scope_mode='parabola')
        session.focused_panel, session.zoomed = 'scope', True
        session.update(.4)
        frames = []
        for background in ('terminal', 'black'):
            session.options.background = background
            for rows, cols in ((4, 12), (16, 48), (24, 80), (44, 144), (60, 240)):
                with self.subTest(background=background, rows=rows, cols=cols):
                    before = (session.scope_elapsed, session.visual_time, session.protocol_time,
                              session.options.scope_mode, session.sim.rng.getstate())
                    screen = Screen(rows, cols)
                    canvas = Canvas(screen, color=False)
                    with patch('builtins.open', side_effect=AssertionError('render opened a file')), \
                            patch('subprocess.Popen', side_effect=AssertionError('render started a process')):
                        Renderer(canvas).draw(session)
                    self.assertEqual(canvas.background, background)
                    self.assertEqual(before, (session.scope_elapsed, session.visual_time, session.protocol_time,
                                              session.options.scope_mode, session.sim.rng.getstate()))
                    if rows >= 24:
                        self.assertIn('MATH SCOPE', screen.text())
                        self.assertIn('PARABOLA', screen.text())
                    if (rows, cols) == (44, 144):
                        frames.append(screen.text())
        self.assertEqual(frames[0], frames[1])
        session.update(.4)
        screen = Screen(44, 144)
        Renderer(Canvas(screen, color=False)).draw(session)
        # Ignore the caption and frame: changing a time label alone is not animation.
        self.assertNotEqual(frames[-1].splitlines()[5:-2], screen.text().splitlines()[5:-2])


@unittest.skipUnless(os.name == 'posix', 'requires Unix PTY')
class ScopeTerminalTests(unittest.TestCase):
    def run_terminal(self, **kwargs):
        return test_cli.TerminalTests.run_terminal(self, **kwargs)

    def test_selected_function_and_f_switch_work_in_real_curses(self):
        output = self.run_terminal(extra=(
            '--no-config', '--bundled', '--scope-mode', 'parabola', '--background', 'black'),
            actions=((.15, b'p'), (.22, b'Math scope / fullscreen\n'),
                     (.4, b'f'), (.65, b'q'), (.8, b'y')))
        for label in (b'MATH SCOPE', b'PARABOLA', b'LISSAJOUS', b'QUIT DEV/OS?'):
            self.assertIn(label, output)


if __name__ == '__main__':
    unittest.main()
