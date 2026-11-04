"""The ASCII anime pianist is a pure, bounded illustration with a synthetic melody."""

import os
import re
import unittest
from unittest.mock import Mock, patch

from devos import presets
from devos.app import dispatch_key
from devos.cli import parser, runtime_options
from devos.commands import search
from devos.pianist import blink, melody_state, render_pianist
from devos.protocol import protocol_frame
from devos.renderer import Renderer
from devos.session import Session
from devos.sources import SourceProvider
from devos.terminal import Canvas
from devos.visuals import ANIMATION_BY_ID, render_animation
from tests import test_cli
from tests.test_rendering import Screen


def lines_of(pixels, width, height):
    return ["".join(pixels[x, y].char if (x, y) in pixels else " " for x in range(width)) for y in range(height)]


class PianistRenderTests(unittest.TestCase):
    def test_registered_visual_is_ascii_bounded_and_repeatable(self):
        spec = ANIMATION_BY_ID["pianist"]
        self.assertEqual(spec.parameters, ("energy", "pulse_bpm"))
        self.assertIn("NO AUDIO", spec.caption)
        for width, height in ((0, 0), (3, 3), (12, 4), (28, 7), (46, 13), (78, 21), (118, 41), (238, 57)):
            for elapsed in (0, .87, 1.95, 7.3):
                with self.subTest(width=width, height=height, elapsed=elapsed):
                    pixels = render_animation("pianist", elapsed, width, height, protocol_frame(elapsed), {"energy": .55})
                    self.assertTrue(all(0 <= x < width and 0 <= y < height for x, y in pixels))
                    self.assertTrue(all(pixel.char.isascii() and pixel.char.isprintable() for pixel in pixels.values()))
        first = render_pianist(4.2, 118, 41, None, {"energy": .7})
        render_pianist(9.9, 46, 13, None, {"energy": .1})
        self.assertEqual(first, render_pianist(4.2, 118, 41, None, {"energy": .7}))
        self.assertNotEqual(first, render_pianist(4.4, 118, 41, None, {"energy": .7}))

    def test_portrait_shows_two_eyes_a_smile_hands_and_a_full_width_keyboard(self):
        width, height = 118, 41
        pixels = render_pianist(3.0, width, height, None, {"energy": .55})
        lines = lines_of(pixels, width, height)
        self.assertEqual(lines[36], "=" * width, "the keyboard strip spans the whole viewport")
        self.assertIn("###", lines[37])
        self.assertGreaterEqual(lines[40].count("|___|"), width // 8)
        self.assertTrue(any("@" in line[:width // 2] and "@" in line[width // 2:] for line in lines[10:20]),
                        "both eyes should show an iris")
        self.assertTrue(any(re.search(r"\\_+/", line) for line in lines[18:23]), "a small smile below the nose")
        self.assertEqual(len(re.findall(r"/\|{3,5}\\", lines[35])), 2, "two hands rest on the keys")
        tones = {pixel.tone for pixel in pixels.values()}
        self.assertTrue({"accent", "main", "text", "hot", "muted"}.issubset(tones))
        self.assertTrue(any(pixel.char == "'" for pixel in pixels.values()), "hair shine and nose use apostrophes")

    def test_large_viewports_scale_the_portrait_and_use_bigger_hands(self):
        width, height = 238, 57
        lines = lines_of(render_pianist(3.0, width, height, None, {"energy": .55}), width, height)
        self.assertEqual(lines[52], "=" * width)
        self.assertEqual(lines[51].count("/|||||\\"), 2)
        portrait_rows = [index for index, line in enumerate(lines[:52]) if "@" in line]
        self.assertTrue(portrait_rows and portrait_rows[0] > 12, "eyes sit well below the hair line on tall screens")
        self.assertGreater(sum(line.count("/") + line.count("\\") for line in lines[:52]), 1000, "long hair fills the frame")

    def test_tiny_viewports_keep_only_the_keyboard_hands_and_notes(self):
        width, height = 28, 7
        lines = lines_of(render_pianist(3.0, width, height, None, {"energy": .55}), width, height)
        self.assertEqual(lines[4], "=" * width)
        self.assertNotIn("@", "".join(lines))
        self.assertGreaterEqual(lines[3].count("/|||\\"), 1)

    def test_eyes_blink_deterministically_and_the_mouth_opens_on_the_high_note(self):
        self.assertEqual(blink(3.0), 1.0)
        self.assertLess(blink(2.0), .3)
        open_eyes = "".join(lines_of(render_pianist(3.0, 118, 41, None, {"energy": .55}), 118, 41))
        closed_eyes = "".join(lines_of(render_pianist(2.0, 118, 41, None, {"energy": .55}), 118, 41))
        self.assertIn("@", open_eyes)
        self.assertNotIn("@", closed_eyes)
        self.assertTrue(any(re.search(r"\\_+/.*\\_+/", line)
                            for line in lines_of(render_pianist(2.0, 118, 41, None, {"energy": .55}), 118, 41)[12:19]),
                        "closed lids are drawn as two gentle curves")
        lines = lines_of(render_pianist(.87, 118, 41, None, {"energy": .55}), 118, 41)
        self.assertTrue(any(" o " in line[52:66] for line in lines[19:22]), "the mouth opens on the melody's top note")

    def test_melody_moves_the_hands_presses_keys_and_floats_notes(self):
        tempo = 76 + 50 * .55
        hands, index, fraction = melody_state(.05, tempo)
        self.assertEqual(index, 0)
        self.assertTrue(all(key is not None for position, key, notes in hands), "both hands play the first eighth")
        self.assertEqual(melody_state(1.0, 60)[1], 2)
        self.assertEqual(melody_state(1.0, 150)[1], 5)
        width, height = 118, 41
        early = render_pianist(.05, width, height, None, {"energy": .55})
        keyboard_hot = [pixel for (x, y), pixel in early.items() if y >= 37 and pixel.tone == "hot"]
        self.assertTrue(keyboard_hot and {pixel.char for pixel in keyboard_hot} <= {"=", "_"}, "pressed keys glow")
        released = render_pianist(.28, width, height, None, {"energy": .55})
        self.assertFalse([pixel for (x, y), pixel in released.items() if y >= 37 and pixel.tone == "hot"])
        start = lines_of(early, width, height)[35]
        later = lines_of(render_pianist(.9, width, height, None, {"energy": .55}), width, height)[35]
        self.assertNotEqual(re.search(r"/\|+\\", start).start(), re.search(r"/\|+\\", later).start(),
                            "the left hand travels between keys")
        notes = [(x, y, pixel) for (x, y), pixel in render_pianist(1.5, width, height, None, {"energy": .55}).items()
                 if y < 33 and pixel.char in "dpbJ#" and pixel.tone in ("hot", "warn", "accent", "muted")]
        self.assertGreaterEqual(len(notes), 3)
        slow = render_pianist(1.0, width, height, None, {"energy": .55, "pulse_bpm": 60})
        fast = render_pianist(1.0, width, height, None, {"energy": .55, "pulse_bpm": 150})
        self.assertNotEqual(lines_of(slow, width, height)[35], lines_of(fast, width, height)[35],
                            "a pulse binding changes the tempo")


class PianistWorkspaceTests(unittest.TestCase):
    def session(self, **kwargs):
        return Session(sources=SourceProvider(bundled=True), seed=42, **kwargs)

    def render(self, session, rows=44, cols=144):
        screen = Screen(rows, cols)
        with patch("builtins.open", side_effect=AssertionError("render opened a file")), \
                patch("subprocess.Popen", side_effect=AssertionError("render started a process")):
            Renderer(Canvas(screen, color=False)).draw(session)
        return screen.text()

    def test_palette_action_selects_the_pianist_and_opens_the_immersive_view(self):
        self.assertEqual({item.id for item in search("pianist")}, {"visual:pianist", "action:pianist"})
        self.assertEqual([item.id for item in search("Visual / fullscreen")], ["action:visual-fullscreen"])
        session, runtime = self.session(animation="reactor"), Mock()
        session.handle("pianist")
        self.assertEqual((session.animation, session.fullscreen_panel), ("pianist", "visual"))
        session.handle("pianist")
        self.assertEqual(session.fullscreen_panel, "visual", "repeating the command keeps the view open")
        dispatch_key(session, runtime, ord("y"))
        self.assertIsNone(session.fullscreen_panel)
        self.assertEqual(session.animation, "pianist")
        session.handle("pianist")
        session.handle("escape")
        self.assertIsNone(session.fullscreen_panel)
        self.assertTrue(session.running)
        self.assertEqual(runtime.mock_calls, [])

    def test_fullscreen_render_fills_the_viewport_with_the_portrait_and_controls(self):
        session = self.session(animation="pianist", fullscreen="visual")
        session.update(3.0)
        for rows, cols in ((16, 48), (24, 80), (44, 144), (60, 240)):
            with self.subTest(rows=rows, cols=cols):
                text = self.render(session, rows, cols)
                first, last = text.splitlines()[0], text.splitlines()[-1]
                self.assertIn("ANIME PIANIST", first)
                self.assertIn("DEMO", first)
                self.assertIn("Y / ESC back", last)
                self.assertNotIn("01 / SOURCE", text)
                self.assertRegex(text, r"\|_+\|_+\|")
                if rows >= 24:
                    self.assertIn("NO AUDIO", text)
                    self.assertIn("@", text)
        session.handle("help")
        self.assertIn("Anime pianist", self.render(session))

    def test_cli_fullscreen_flag_is_start_up_view_state_and_never_a_saved_preference(self):
        args = parser().parse_args(["--no-config", "--animation", "pianist", "--fullscreen", "visual"])
        settings = runtime_options(args)
        self.assertEqual(settings["fullscreen"], "visual")
        session = Session(sources=SourceProvider(bundled=True), **settings)
        self.assertEqual((session.animation, session.fullscreen_panel), ("pianist", "visual"))
        session.handle("escape")
        self.assertIsNone(session.fullscreen_panel)
        scope = Session(sources=SourceProvider(bundled=True),
                        **runtime_options(parser().parse_args(["--no-config", "--fullscreen", "scope"])))
        self.assertEqual(scope.fullscreen_panel, "scope")
        self.assertIsNone(Session(sources=SourceProvider(bundled=True),
                                  **runtime_options(parser().parse_args(["--no-config"]))).fullscreen_panel)
        self.assertNotIn("fullscreen", presets.SAFE_KEYS)
        with self.assertRaises(SystemExit):
            parser().parse_args(["--fullscreen", "arcade"])


@unittest.skipUnless(os.name == "posix", "requires Unix PTY")
class PianistTerminalTests(unittest.TestCase):
    def run_terminal(self, **kwargs):
        return test_cli.TerminalTests.run_terminal(self, **kwargs)

    def test_startup_fullscreen_pianist_renders_in_actual_curses_and_escape_returns(self):
        output = self.run_terminal(extra=("--no-config", "--bundled", "--animation", "pianist", "--fullscreen", "visual",
                                          "--background", "black"), actions=((.6, b"\x1b"), (.8, b"q"), (.9, b"y")))
        for caption in (b"ANIME PIANIST", b"|___|___|", b"01 / SOURCE", b"QUIT DEV/OS?"):
            self.assertIn(caption, output)


if __name__ == "__main__":
    unittest.main()
