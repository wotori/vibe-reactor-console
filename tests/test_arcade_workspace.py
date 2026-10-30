"""Arcade input ownership and actual console composition, without paid jobs."""

from dataclasses import replace
import os
import unittest
from unittest.mock import Mock

from devos.app import dispatch_key
from devos.arcade_view import _project_cells, draw_arcade
from devos.games import GAME_SPECS
from devos.games.core import GameCell, GameFrame
from devos.renderer import Renderer
from devos.screen_registry import SCREENS
from devos.session import Session
from devos.shell import Run, Style, TerminalFrame
from devos.sources import SourceProvider
from devos.terminal import Canvas, curses
from tests.test_rendering import Screen


def session_fixture():
    return Session(seed=42, sources=SourceProvider(bundled=True))


class ArcadeInputTests(unittest.TestCase):
    def setUp(self):
        self.session = session_fixture()
        self.runtime = Mock()

    def press(self, key):
        dispatch_key(self.session, self.runtime, ord(key) if isinstance(key, str) else key)

    def open_snake(self):
        self.press("8")
        self.press("\n")

    def test_lobby_selection_is_explicit_and_enters_ready_before_playing(self):
        self.press("8")
        self.assertEqual(self.session.screen_id, "arcade")
        self.assertIsNone(self.session.arcade_game)
        self.assertEqual(self.session.arcade_games, {})
        self.press(curses.KEY_DOWN)
        self.assertEqual(self.session.arcade_choice, 1)
        self.press("\t")
        self.assertEqual(self.session.arcade_choice, 2)
        self.press("k")
        self.assertEqual(self.session.arcade_choice, 1)
        self.press("\n")
        self.assertEqual(self.session.arcade_game_id, "blocks")
        self.assertEqual(self.session.arcade_game.snapshot().status, "READY")
        self.press("\n")
        self.assertEqual(self.session.arcade_game.snapshot().status, "PLAYING")

    def test_game_keys_do_not_trigger_dashboard_settings_or_workers(self):
        self.open_snake()
        original = (self.session.animation, self.session.options.auto_cycle,
                    self.session.options.scope, self.session.fullscreen_panel,
                    self.session.options.background, self.session.speed)
        for key in "WASDRXYwasdrxy":
            self.press(key)
        self.assertEqual(original, (self.session.animation, self.session.options.auto_cycle,
                        self.session.options.scope, self.session.fullscreen_panel,
                        self.session.options.background, self.session.speed))
        self.assertFalse(self.session.palette_open)
        self.assertEqual(tuple(self.session.pending_actions), ())
        self.runtime.ensure_terminal.assert_not_called()
        self.runtime.terminals.send.assert_not_called()

    def test_pause_restart_and_lobby_preserve_game_instance(self):
        self.open_snake()
        game = self.session.arcade_game
        self.press("\n")
        self.press("p")
        self.assertEqual(game.snapshot().status, "PAUSED")
        self.press("\n")
        self.assertEqual(game.snapshot().status, "PLAYING")
        self.session.update(.18)
        frame = game.snapshot()
        self.press("\x1b")
        self.assertIsNone(self.session.arcade_game)
        self.assertEqual(self.session.screen_id, "arcade")
        self.session.update(.5)
        self.assertEqual(frame, game.snapshot())
        self.press("\n")
        self.assertIs(self.session.arcade_game, game)
        self.press("r")
        self.assertEqual(game.snapshot().status, "READY")
        self.press("\x1b")
        self.press("\x1b")
        self.assertEqual(self.session.screen_id, "cockpit")

    def test_modals_inactive_screens_fullscreen_and_small_viewport_freeze_game(self):
        self.open_snake()
        self.press("\n")
        game = self.session.arcade_game
        for name, value in (("quit_confirm", True), ("help_open", True), ("palette_open", True),
                            ("menu_kind", "animations"), ("fullscreen_panel", "scope"),
                            ("arcade_viewport_ready", False)):
            with self.subTest(gate=name):
                before = game.snapshot()
                setattr(self.session, name, value)
                self.session.update(.6)
                self.assertEqual(before, game.snapshot())
                setattr(self.session, name, None if name in ("menu_kind", "fullscreen_panel") else
                        True if name == "arcade_viewport_ready" else False)
        self.session.choose_screen("cockpit")
        before = game.snapshot()
        self.session.update(.6)
        self.assertEqual(before, game.snapshot())

    def test_saved_visual_pause_does_not_disable_explicitly_started_game(self):
        self.session.paused = True
        self.open_snake()
        self.press("\n")
        before = self.session.arcade_game.snapshot()
        self.session.update(.2)
        self.assertNotEqual(before, self.session.arcade_game.snapshot())
        self.assertTrue(self.session.paused)

    def test_small_window_also_blocks_invisible_moves_drops_and_restarts(self):
        self.session.arcade_select("blocks")
        self.press("\n")
        self.session.arcade_viewport_ready = False
        game = self.session.arcade_game
        before = game.snapshot()
        for key in (curses.KEY_LEFT, curses.KEY_UP, curses.KEY_DOWN, ord(" "), ord("r"), ord("p")):
            self.press(key)
        self.session.update(.5)
        self.assertEqual(before, game.snapshot())
        self.press(27)
        self.assertIsNone(self.session.arcade_game)
        self.assertIn("blocks", self.session.arcade_games)

    def test_quit_confirmation_stops_ticks_without_killing_session(self):
        self.open_snake()
        self.press("\n")
        self.press("q")
        self.assertTrue(self.session.quit_confirm)
        self.assertTrue(self.session.running)
        before = self.session.arcade_game.snapshot()
        self.session.update(.7)
        self.assertEqual(before, self.session.arcade_game.snapshot())
        self.press("\x1b")
        self.assertTrue(self.session.running)
        self.assertFalse(self.session.quit_confirm)
        self.press("q")
        self.press("Y")
        self.assertFalse(self.session.running)

    def test_terminal_keeps_exclusive_input_and_retains_its_frame(self):
        self.open_snake()
        self.session.terminal_frame = TerminalFrame(((Run(0, "SHELL STILL ALIVE", Style()),),),
                                                   status="LIVE / fixture")
        frame = self.session.terminal_frame
        self.press(curses.KEY_F2)
        self.assertEqual(self.session.screen_id, "cockpit")
        self.assertTrue(self.session.terminal_focused)
        for char in "8qwasdpXY":
            self.press(char)
        self.assertFalse(self.session.quit_confirm)
        self.assertEqual(b"".join(call.args[0] for call in self.runtime.terminals.send.call_args_list),
                         b"8qwasdpXY")
        self.assertIs(self.session.terminal_frame, frame)
        self.press(29)
        self.press("8")
        self.assertEqual(self.session.screen_id, "arcade")
        self.assertIn("snake", self.session.arcade_games)
        self.assertIs(self.session.terminal_frame, frame)

    def test_emergency_mission_stop_remains_available_during_gameplay(self):
        self.open_snake()
        self.press("\n")
        before = self.session.arcade_game.snapshot()
        self.press("!")
        self.assertEqual(tuple(self.session.pending_actions), ("mission-stop",))
        self.assertEqual(before, self.session.arcade_game.snapshot())

    def test_existing_pickers_keep_input_ownership_after_switching_to_arcade(self):
        self.open_snake()
        self.press("\n")
        game = self.session.arcade_game
        for picker_key, kind in (("\t", "modes"), ("g", "animations")):
            with self.subTest(picker=kind):
                self.press("1")
                self.press(picker_key)
                self.assertEqual(self.session.menu_kind, kind)
                index = self.session.picker_index
                self.press("8")
                before = game.snapshot()
                choice = self.session.arcade_choice
                self.press(curses.KEY_DOWN)
                self.assertEqual(self.session.picker_index,
                                 (index + 1) % len(self.session.menu_entries))
                self.assertEqual(self.session.arcade_choice, choice)
                self.session.update(.3)
                self.assertEqual(game.snapshot(), before)
                self.press(27)
                self.assertIsNone(self.session.menu_kind)
                self.assertEqual(self.session.screen_id, "arcade")
                self.assertIs(self.session.arcade_game, game)
                self.assertEqual(game.snapshot(), before)


class ArcadeRenderingTests(unittest.TestCase):
    def draw(self, session, rows, cols):
        screen = Screen(rows, cols)
        canvas = Canvas(screen, color=False)
        renderer = Renderer(canvas)
        renderer.draw(session)
        return screen.text(), canvas

    def test_all_eight_screens_and_games_fit_resizes_without_advancing_state(self):
        session = session_fixture()
        self.assertEqual(len(SCREENS), 8)
        for background in ("terminal", "black"):
            session.options.background = background
            for spec in SCREENS:
                session.choose_screen(spec.id)
                for size in ((4, 12), (16, 48), (24, 80), (44, 144), (60, 240)):
                    with self.subTest(screen=spec.id, background=background, size=size):
                        before = (session.sim.elapsed, session.sim.rng.getstate(), session.visual_time)
                        _, canvas = self.draw(session, *size)
                        self.assertEqual(canvas.background, background)
                        self.assertEqual(before, (session.sim.elapsed, session.sim.rng.getstate(), session.visual_time))
            for spec in GAME_SPECS:
                session.arcade_select(spec.id)
                for size in ((16, 48), (24, 80), (44, 144), (60, 240)):
                    with self.subTest(game=spec.id, background=background, size=size):
                        before = session.arcade_game.snapshot()
                        text, canvas = self.draw(session, *size)
                        self.assertIn(spec.title.upper(), text)
                        self.assertIn("READY", text)
                        self.assertIn("SCORE", text)
                        self.assertEqual(before, session.arcade_game.snapshot())
                        self.assertEqual(canvas.background, background)

    def test_lobby_lists_all_games_at_minimum_and_wide_sizes(self):
        session = session_fixture()
        session.choose_screen("arcade")
        for size in ((16, 48), (24, 80), (44, 144)):
            text, _ = self.draw(session, *size)
            for spec in GAME_SPECS:
                self.assertIn(spec.title.upper(), text)
            self.assertIn("INPUT: DEVOS", text)
            self.assertIn("ENTER", text)
            self.assertEqual(session.arcade_games, {})

    def test_compact_projection_keeps_last_row_and_player_over_terrain(self):
        frame = GameFrame(10, 20, (GameCell(0, 18, "#", "muted"),
                                  GameCell(0, 19, "@", "hot", True),
                                  GameCell(9, 19, "*", "accent")))
        projected = _project_cells(frame, 2)
        self.assertEqual(projected[(0, 9)], ("@", "hot", True))
        self.assertEqual(projected[(9, 9)], ("*", "accent", False))
        self.assertEqual(frame.height, 20)

    def test_game_status_overlays_draw_without_mutating_or_executing(self):
        session = session_fixture()
        session.arcade_select("snake")
        frame = session.arcade_game.snapshot()
        for status in ("READY", "PLAYING", "PAUSED", "GAME OVER", "WON"):
            immutable = replace(frame, status=status)
            game = Mock()
            game.snapshot.return_value = immutable
            session.arcade_games["snake"] = game
            for size in ((16, 48), (44, 144)):
                text, _ = self.draw(session, *size)
                self.assertIn(status, text)
                self.assertTrue(all(char == "\n" or 32 <= ord(char) < 127 for char in text))
            game.handle.assert_not_called()
            game.update.assert_not_called()


@unittest.skipUnless(os.name == "posix", "requires Unix PTY")
class ArcadeTerminalTests(unittest.TestCase):
    def test_arcade_lobby_play_return_and_quit_confirmation_in_real_curses(self):
        from tests.test_cli import TerminalTests
        output = TerminalTests.run_terminal(self, extra=("--no-config", "--bundled"), actions=(
            (.12, b"8"), (.25, b"\n"), (.36, b"\n"), (.48, b"\x1bOC"),
            (.62, b"\x1b"), (.77, b"q"), (.89, b"Y")))
        for marker in (b"ARCADE", b"SNAKE", b"FALLING BLOCKS", b"BYTE RUNNER", b"SCORE", b"EXIT"):
            self.assertIn(marker, output)


if __name__ == "__main__":
    unittest.main()
