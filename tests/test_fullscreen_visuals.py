"""Immersive animations keep dashboard state and child input ownership intact."""

import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from devos import presets, settings
from devos.app import action_for_key, dispatch_key, terminal_rectangles
from devos.cli import parser, runtime_options
from devos.math_scope import SCOPES
from devos.renderer import Renderer
from devos.runtime import Runtime
from devos.session import Session
from devos.shell import TerminalFrame
from devos.sources import SourceProvider
from devos.terminal import Canvas, curses
from devos.visuals import ANIMATIONS
from tests import test_cli
from tests.test_rendering import Screen


class FullscreenWorkspaceTests(unittest.TestCase):
    def session(self, **kwargs):
        return Session(sources=SourceProvider(bundled=True), seed=42, **kwargs)

    def test_direct_shortcuts_toggle_and_switch_the_two_immersive_views(self):
        session, runtime = self.session(scope=False, animation='reactor'), Mock()
        self.assertIsNone(session.fullscreen_panel)
        for key, action in (('x', 'scope-fullscreen'), ('X', 'scope-fullscreen'),
                            ('y', 'visual-fullscreen'), ('Y', 'visual-fullscreen')):
            self.assertEqual(action_for_key(ord(key)), action)
        dispatch_key(session, runtime, ord('x'))
        self.assertEqual(session.fullscreen_panel, 'scope')
        dispatch_key(session, runtime, ord('y'))
        self.assertEqual(session.fullscreen_panel, 'visual')
        dispatch_key(session, runtime, ord('Y'))
        self.assertIsNone(session.fullscreen_panel)
        dispatch_key(session, runtime, ord('X'))
        self.assertEqual(session.fullscreen_panel, 'scope')
        dispatch_key(session, runtime, ord('x'))
        self.assertIsNone(session.fullscreen_panel)
        self.assertTrue(session.running)
        self.assertEqual(runtime.mock_calls, [])

    def test_escape_and_screen_switch_leave_fullscreen_without_quitting(self):
        session = self.session()
        for panel in ('scope', 'visual'):
            with self.subTest(panel=panel):
                session.toggle_fullscreen(panel)
                session.handle('escape')
                self.assertIsNone(session.fullscreen_panel)
                self.assertFalse(session.zoomed)
                self.assertFalse(session.quit_confirm)
                self.assertTrue(session.running)
                session.toggle_fullscreen(panel)
                session.handle('screen:atlas')
                self.assertEqual(session.screen_id, 'atlas')
                self.assertIsNone(session.fullscreen_panel)
                session.toggle_fullscreen(panel)
                session.choose_screen('arena')
                self.assertIsNone(session.fullscreen_panel)

    def test_switching_fullscreen_views_restores_the_original_panel_and_zoom(self):
        session = self.session(screen='visual-lab')
        session.focused_panel, session.zoomed = 'bindings', True
        session.toggle_fullscreen('scope')
        self.assertEqual(session.screen_id, 'visual-lab')
        session.toggle_fullscreen('visual')
        self.assertEqual(session.screen_id, 'visual-lab')
        session.handle('zoom')
        self.assertIsNone(session.fullscreen_panel)
        self.assertEqual((session.focused_panel, session.zoomed), ('bindings', True))
        session.choose_screen('replay')
        session.toggle_fullscreen('scope')
        self.assertEqual(session.screen_id, 'replay')
        session.handle('escape')
        self.assertEqual(session.screen_id, 'replay')

    def test_cancelling_dashboard_modals_preserves_the_immersive_view(self):
        session, runtime = self.session(), Mock()
        for panel in ('scope', 'visual'):
            session.toggle_fullscreen(panel)
            for action in ('animation', 'tab', 'palette', 'help', 'binding-settings', 'source-folder', 'quit'):
                with self.subTest(panel=panel, modal=action):
                    session.handle(action)
                    self.assertTrue(session.menu_open)
                    dispatch_key(session, runtime, 27)
                    self.assertFalse(session.menu_open)
                    self.assertEqual(session.fullscreen_panel, panel)
                    self.assertTrue(session.running)
            session.handle('escape')
        self.assertEqual(runtime.mock_calls, [])

    def test_hidden_terminals_keep_their_frame_and_receive_no_visible_rectangle(self):
        session = self.session(animation='reactor')
        frame = TerminalFrame(status='LIVE / existing shell')
        session.terminal_frame = frame
        session.terminal_visible = True
        simulation = session.sim
        for panel in ('scope', 'visual'):
            session.toggle_fullscreen(panel)
            for rows, cols in ((4, 12), (16, 48), (24, 80), (44, 144), (60, 240)):
                self.assertEqual(terminal_rectangles(Screen(rows, cols), session), ())
            session.update(.2)
            self.assertIs(session.sim, simulation)
            self.assertIs(session.terminal_frame, frame)
            self.assertTrue(session.terminal_visible)
            self.assertEqual(session.animation, 'reactor')
            session.handle('escape')
        self.assertTrue(terminal_rectangles(Screen(44, 144), session))

    def test_child_owns_x_y_and_quit_confirmation_owns_y(self):
        session, runtime = self.session(), Mock()
        session.terminal_visible = session.terminal_focused = True
        for char in 'xXyY':
            dispatch_key(session, runtime, ord(char))
        self.assertEqual(b''.join(call.args[0] for call in runtime.terminals.send.call_args_list), b'xXyY')
        self.assertIsNone(session.fullscreen_panel)
        dispatch_key(session, runtime, 29)
        dispatch_key(session, runtime, ord('x'))
        self.assertEqual(session.fullscreen_panel, 'scope')
        dispatch_key(session, runtime, ord('q'))
        dispatch_key(session, runtime, ord('x'))
        self.assertTrue(session.quit_confirm)
        self.assertEqual(session.fullscreen_panel, 'scope')
        dispatch_key(session, runtime, ord('y'))
        self.assertFalse(session.running)
        self.assertEqual(session.fullscreen_panel, 'scope')

    def test_f2_returns_to_live_terminal_from_fullscreen(self):
        session, runtime = self.session(), Mock()
        session.toggle_fullscreen('visual')
        dispatch_key(session, runtime, curses.KEY_F2)
        self.assertIsNone(session.fullscreen_panel)
        self.assertTrue(session.terminal_focused)
        self.assertTrue(session.terminal_visible)
        runtime.ensure_terminal.assert_called_once_with()

    def test_fullscreen_is_transient_and_not_saved_in_portable_or_local_settings(self):
        session = self.session()
        session.toggle_fullscreen('scope')
        for snapshot in (presets.preferences(session), settings.local_preferences(session)):
            self.assertNotIn('fullscreen_panel', snapshot)
            self.assertNotIn('fullscreen', snapshot)
        restored = self.session()
        self.assertIsNone(restored.fullscreen_panel)

    def test_actual_replay_keeps_its_clock_and_read_only_services_in_both_views(self):
        path = Path(__file__).resolve().parents[1] / 'examples' / 'events.jsonl'
        for panel in ('scope', 'visual'):
            with self.subTest(panel=panel), \
                    patch('subprocess.Popen', side_effect=AssertionError('fullscreen replay launched a process')), \
                    patch('urllib.request.urlopen', side_effect=AssertionError('fullscreen replay connected')):
                args = parser().parse_args(['--no-config', '--bundled', '--replay', str(path)])
                session = Session(sources=SourceProvider(bundled=True), **runtime_options(args))
                runtime = Runtime(args, session)
                try:
                    runtime.update(0)
                    session.toggle_fullscreen(panel)
                    runtime.replay.playing = True
                    session.update(.1)
                    runtime.update(.1)
                    self.assertEqual(session.screen_id, 'replay')
                    self.assertEqual(session.fullscreen_panel, panel)
                    self.assertAlmostEqual(runtime.replay.playhead, .1)
                    self.assertEqual(runtime.terminals._terminals, {})
                    self.assertEqual(runtime.tasks.snapshot().state, 'idle')
                finally:
                    runtime.close()

    def test_palette_opens_visual_and_math_fullscreen_without_starting_work(self):
        session, runtime = self.session(), Mock()
        for title, panel in (('Math scope / fullscreen', 'scope'), ('Visual / fullscreen', 'visual')):
            dispatch_key(session, runtime, ord('p'))
            for char in title:
                dispatch_key(session, runtime, ord(char))
            self.assertEqual(len(session.palette_commands), 1)
            dispatch_key(session, runtime, 10)
            self.assertFalse(session.palette_open)
            self.assertEqual(session.fullscreen_panel, panel)
            dispatch_key(session, runtime, 27)
        self.assertEqual(runtime.mock_calls, [])


class FullscreenRenderingTests(unittest.TestCase):
    def render(self, session, rows=44, cols=144):
        screen = Screen(rows, cols)
        canvas = Canvas(screen, color=False)
        before = (session.sim.elapsed, session.visual_time, session.protocol_time, session.scope_elapsed,
                  session.sim.rng.getstate(), tuple(session.sim.logs), session.fullscreen_panel)
        with patch('builtins.open', side_effect=AssertionError('render opened a file')), \
                patch('subprocess.Popen', side_effect=AssertionError('render started a process')):
            Renderer(canvas).draw(session)
        self.assertEqual(before, (session.sim.elapsed, session.visual_time, session.protocol_time,
                                 session.scope_elapsed, session.sim.rng.getstate(), tuple(session.sim.logs),
                                 session.fullscreen_panel))
        self.assertEqual(canvas.background, session.background)
        return screen.text()

    def test_every_registered_visual_fits_small_and_large_fullscreen(self):
        session = Session(sources=SourceProvider(bundled=True), seed=42)
        session.toggle_fullscreen('visual')
        session.update(.42)
        for animation in ANIMATIONS:
            session.choose_animation(animation.id)
            for rows, cols in ((16, 48), (60, 240)):
                with self.subTest(visual=animation.id, rows=rows, cols=cols):
                    text = self.render(session, rows, cols)
                    self.assertNotIn('01 / SOURCE', text)
                    self.assertNotIn('BUILD STREAM', text)

    def test_all_functions_fill_the_viewport_without_cockpit_panels(self):
        for scope in SCOPES:
            session = Session(sources=SourceProvider(bundled=True), scope_mode=scope.id, seed=42)
            session.toggle_fullscreen('scope')
            session.update(.42)
            for rows, cols in ((4, 12), (16, 48), (24, 80), (44, 144), (60, 240)):
                with self.subTest(scope=scope.id, rows=rows, cols=cols):
                    text = self.render(session, rows, cols)
                    if rows >= 16:
                        self.assertIn('MATH SCOPE', text.splitlines()[0])
                        self.assertNotIn('01 / SOURCE', text)
                        self.assertNotIn('BUILD STREAM', text)
                        # The immersive plot starts above the old cockpit header.
                        self.assertTrue(any(char not in ' |+-' for char in ''.join(text.splitlines()[2:5])))

    def test_scope_and_visual_graphics_animate_beyond_their_captions_on_native_and_black(self):
        for panel in ('scope', 'visual'):
            session = Session(sources=SourceProvider(bundled=True), scope_mode='parabola',
                              animation='reactor', seed=42)
            session.toggle_fullscreen(panel)
            session.update(.42)
            initial = []
            for background in ('terminal', 'black'):
                session.options.background = background
                initial.append(self.render(session))
                for rows, cols in ((16, 48), (24, 80), (60, 240)):
                    self.render(session, rows, cols)
            self.assertEqual(initial[0], initial[1])
            self.assertNotIn('01 / SOURCE', initial[0])
            self.assertNotIn('BUILD STREAM', initial[0])
            session.update(.8)
            later = self.render(session)
            self.assertNotEqual(initial[-1].splitlines()[5:-3], later.splitlines()[5:-3])

    def test_overlays_render_and_cancel_without_destroying_fullscreen(self):
        session, runtime = Session(sources=SourceProvider(bundled=True)), Mock()
        session.toggle_fullscreen('scope')
        for action, caption in (('help', 'CONTROLS'), ('palette', 'COMMANDS'), ('animation', 'SWITCH ANIMATION'),
                                ('quit', 'QUIT DEV/OS?')):
            session.handle(action)
            text = self.render(session)
            self.assertIn(caption, text)
            dispatch_key(session, runtime, 27)
            self.assertEqual(session.fullscreen_panel, 'scope')
            self.assertIn('MATH SCOPE', self.render(session))


@unittest.skipUnless(os.name == 'posix', 'requires Unix PTY')
class FullscreenTerminalTests(unittest.TestCase):
    def run_terminal(self, **kwargs):
        return test_cli.TerminalTests.run_terminal(self, **kwargs)

    def test_x_f_y_g_and_escape_work_in_actual_curses(self):
        output = self.run_terminal(extra=('--no-config', '--bundled', '--scope-mode', 'parabola',
                                          '--animation', 'reactor', '--background', 'black'), actions=(
            (.14, b'x'), (.25, b'f'), (.36, b'y'), (.48, b'g'), (.62, b'\x1b'),
            (.72, b'\x1b'), (.83, b'q'), (.92, b'y')))
        for caption in (b'MATH SCOPE', b'PARABOLA', b'LISSAJOUS', b'PLASMA', b'SWITCH ANIMATION', b'QUIT DEV/OS?'):
            self.assertIn(caption, output)


if __name__ == '__main__':
    unittest.main()
