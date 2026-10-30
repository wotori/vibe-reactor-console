import unittest
from unittest.mock import Mock

from devos.app import action_for_key, dispatch_key, terminal_rectangles
from devos.screen_registry import SCREENS
from devos.session import Session
from devos.sources import SourceProvider
from devos.terminal import curses
from tests.test_rendering import Screen


class InputTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(sources=SourceProvider(bundled=True))
        self.runtime = Mock()

    def test_input_child_gets_dashboard_shortcuts_verbatim(self):
        self.session.terminal_focused = self.session.terminal_visible = True
        for char in '1234567qpzg!':
            dispatch_key(self.session, self.runtime, ord(char))
        self.assertEqual(self.session.screen_id, 'cockpit')
        self.assertTrue(self.session.running)
        self.assertFalse(self.session.palette_open)
        self.assertEqual(b''.join(call.args[0] for call in self.runtime.terminals.send.call_args_list), b'1234567qpzg!')
        dispatch_key(self.session, self.runtime, 29)
        self.assertFalse(self.session.terminal_focused)

    def test_search_and_handoff_editor_own_their_text_and_function_keys(self):
        self.session.handle('palette')
        for char in 'queue':
            dispatch_key(self.session, self.runtime, ord(char))
        dispatch_key(self.session, self.runtime, curses.KEY_F2)
        self.assertEqual(self.session.palette_query, 'queue')
        self.assertFalse(self.session.terminal_focused)
        self.assertTrue(self.session.running)
        dispatch_key(self.session, self.runtime, 27)
        self.session.composer_open = True
        dispatch_key(self.session, self.runtime, ord('q'))
        dispatch_key(self.session, self.runtime, curses.KEY_F4)
        self.assertEqual(self.session.composer_text, 'q')
        self.assertEqual(self.session.terminal_slot, 'left')
        dispatch_key(self.session, self.runtime, 21)
        self.assertEqual(self.session.composer_text, '')
        self.runtime.ensure_terminal.assert_not_called()

    def test_all_screen_zoom_terminal_sizes_survive_tiny_resize(self):
        for item in SCREENS:
            self.session.choose_screen(item.id)
            self.session.terminal_visible = True
            for zoom in (False, True):
                self.session.zoomed = zoom
                for rows, cols in ((4, 12), (15, 47), (16, 48), (44, 144), (60, 240)):
                    for slot, rect in terminal_rectangles(Screen(rows, cols), self.session):
                        self.assertIn(slot, ('left', 'right'))
                        self.assertGreater(rect.width, 4)
                        self.assertLessEqual(rect.y + rect.height, rows - 1)

    def test_replay_rejects_terminal_focus_but_global_stop_is_a_dashboard_action(self):
        self.session.choose_screen('replay')
        dispatch_key(self.session, self.runtime, curses.KEY_F2)
        self.runtime.ensure_terminal.assert_not_called()
        self.assertFalse(self.session.terminal_focused)
        dispatch_key(self.session, self.runtime, ord('!'))
        self.assertEqual(self.session.pending_actions.pop(), 'mission-stop')
        self.assertEqual(action_for_key(ord('2')), 'screen:visual-lab')

    def test_external_control_text_cannot_escape_a_panel(self):
        from devos.terminal import Canvas
        screen = Screen(16, 48)
        Canvas(screen, color=False).put(1, 1, 'ok\n\x1b[2J\tvalue')
        self.assertNotIn('\x1b', screen.text())
        self.assertIn('ok  [2J value', screen.text())
