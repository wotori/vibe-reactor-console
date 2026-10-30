from types import SimpleNamespace
import unittest
from unittest.mock import patch

from devos.session import PALETTES, Session
from devos.shell import Style
from devos.terminal import Canvas


class ColorTests(unittest.TestCase):
    def setUp(self):
        self.pairs = []
        self.writes = []
        self.defaults = []
        self.backgrounds = []
        self.fake = SimpleNamespace(
            COLORS=256, COLOR_PAIRS=256, COLOR_BLACK=0, COLOR_YELLOW=3,
            COLOR_CYAN=6, COLOR_GREEN=2, COLOR_RED=1, COLOR_MAGENTA=5, COLOR_WHITE=7,
            A_BOLD=1, A_REVERSE=2, A_UNDERLINE=4, error=RuntimeError,
            has_colors=lambda: True, start_color=lambda: None,
            use_default_colors=lambda: self.defaults.append(True),
            init_pair=lambda *args: self.pairs.append(args), color_pair=lambda index: index << 8,
        )
        self.screen = SimpleNamespace(
            getmaxyx=lambda: (24, 80), attrset=lambda attr: None,
            bkgd=lambda *args: self.backgrounds.append(args),
            addnstr=lambda *args: self.writes.append(args),
        )
        self.patch = patch("devos.terminal.curses", self.fake)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_default_background_is_inherited_for_every_palette(self):
        canvas = Canvas(self.screen)
        for palette in PALETTES:
            canvas.configure(palette)
            canvas.put(0, 0, "selection", selected=True)
        self.assertTrue(self.defaults)
        self.assertTrue(all(background == -1 for _, _, background in self.pairs))
        self.assertTrue(all(not (write[-1] & self.fake.A_REVERSE) for write in self.writes))
        self.assertTrue(all(write[-1] & self.fake.A_UNDERLINE for write in self.writes))

    def test_forced_black_uses_fixed_black_and_can_switch_back(self):
        canvas = Canvas(self.screen)
        canvas.configure("neon", "black")
        self.assertTrue(all(background == 16 for _, _, background in self.pairs))
        self.assertFalse(self.defaults)
        self.pairs.clear()
        canvas.configure("neon", "terminal")
        self.assertTrue(all(background == -1 for _, _, background in self.pairs))
        self.assertEqual(len(self.backgrounds), 2)

    def test_basic_terminal_and_unsupported_defaults_fall_back_to_black(self):
        self.fake.COLORS = 8

        def unsupported():
            raise self.fake.error("no default-color capability")

        self.fake.use_default_colors = unsupported
        Canvas(self.screen).configure("ember")
        self.assertTrue(all(background == 0 for _, _, background in self.pairs))

    def test_palette_and_scene_switches_preserve_background_choice(self):
        session = Session(mode="lab")
        self.assertEqual(session.background, "terminal")
        session.handle("background")
        session.handle("theme")
        session.activate("bare-metal")
        self.assertEqual(session.background, "black")
        session.handle("background")
        self.assertEqual(session.background, "terminal")

    def test_child_text_keeps_background_and_does_not_use_reverse_video(self):
        for background, expected in (("terminal", -1), ("black", 16)):
            canvas = Canvas(self.screen)
            canvas.configure("neon", background)
            self.pairs.clear()
            canvas.put_terminal(2, 2, "test", Style((0, 255, 255), True, True), 20)
            self.assertTrue(self.pairs)
            self.assertTrue(all(bg == expected for _, _, bg in self.pairs))
            self.assertTrue(all(not (write[-1] & self.fake.A_REVERSE) for write in self.writes))
