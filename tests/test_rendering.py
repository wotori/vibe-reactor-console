import unittest

from devos.layout import arrange
from devos.modes import MODES
from devos.renderer import Renderer
from devos.session import Session
from devos.shell import Run, Style, TerminalFrame
from devos.terminal import Canvas


class Screen:
    """Strict character buffer: accidental writes outside the screen fail tests."""

    def __init__(self, rows, cols):
        self.rows, self.cols = rows, cols
        self.erase()

    def getmaxyx(self):
        return self.rows, self.cols

    def erase(self):
        self.buffer = [[" "] * self.cols for _ in range(self.rows)]

    def addnstr(self, y, x, text, length, attr):
        text = text[:length]
        assert 0 <= y < self.rows and 0 <= x < self.cols
        assert x + len(text) < self.cols
        self.buffer[y][x:x + len(text)] = text

    def refresh(self):
        pass

    def text(self):
        return "\n".join("".join(row) for row in self.buffer)


class RenderingTests(unittest.TestCase):
    def test_real_terminal_frame_is_clipped_after_resize_and_preserved_by_menus(self):
        session = Session(animation="reactor")
        session.handle("terminal")
        frame = TerminalFrame(tuple((Run(0, "SHELL CONTENT " * 30, Style(51)),
                                      Run(200, "OUTSIDE", Style())) for _ in range(60)),
                              cursor_x=200, cursor_y=50, cursor_visible=True, status="LIVE / test")
        session.terminal_frame = frame
        for rows, cols in ((4, 12), (16, 48), (24, 80), (44, 144), (60, 240)):
            screen = Screen(rows, cols)
            renderer = Renderer(Canvas(screen, color=False))
            renderer.draw(session)
            if rows >= 16 and cols >= 48:
                self.assertIn("01 / TERMINAL", screen.text())
                self.assertIn("SHELL CONTENT", screen.text())
                self.assertIn("LIVE / test", screen.text())
            session.open_menu("animations")
            renderer.draw(session)
            self.assertIs(session.terminal_frame, frame)
            session.handle("escape")

    def test_layout_rectangles_are_inside_screen_and_do_not_overlap(self):
        for rows, cols in ((16, 48), (24, 80), (34, 100), (36, 120), (44, 144), (60, 240)):
            for hardware, focus, scope in ((hw, focus, scope) for hw in (False, True)
                                            for focus in (False, True) for scope in (False, True)):
                layout = arrange(rows, cols, hardware, geometry_focus=focus, scope=scope)
                panels = [getattr(layout, key) for key in ("editor", "geometry", "diagnostics", "logs", "memory", "scope")]
                panels = [p for p in panels if p is not None]
                cells = set()
                for rect in panels:
                    self.assertGreaterEqual(rect.height, 3)
                    self.assertGreaterEqual(rect.width, 5)
                    self.assertLessEqual(rect.y + rect.height, rows - 1)
                    self.assertLess(rect.x + rect.width, cols)
                    occupied = {(x, y) for y in range(rect.y, rect.y + rect.height)
                                for x in range(rect.x, rect.x + rect.width)}
                    self.assertFalse(cells & occupied)
                    cells.update(occupied)

    def test_all_scenes_and_menu_resize_without_mutating_simulation(self):
        for mode in MODES:
            session = Session(mode.id, seed=42)
            for _ in range(80):
                session.update(0.05)
            for rows, cols in ((4, 12), (8, 30), (16, 48), (24, 79), (24, 80), (36, 120), (44, 144), (60, 240)):
                with self.subTest(mode=mode.id, size=(cols, rows)):
                    screen = Screen(rows, cols)
                    renderer = Renderer(Canvas(screen, color=False))
                    before = (session.sim.elapsed, session.sim.rng.getstate(), tuple(session.sim.logs))
                    renderer.draw(session)
                    if cols >= 48 and rows >= 16:
                        self.assertIn("SOURCE", screen.text())
                        self.assertIn("BUILD STREAM", screen.text())
                    if mode.hardware and cols >= 120 and rows >= 36:
                        self.assertIn("CPU STATE", screen.text())
                        self.assertIn("MEMORY", screen.text())
                    session.handle("tab")
                    renderer.draw(session)
                    if rows >= 16 and cols >= 48:
                        self.assertIn("SWITCH MODE", screen.text())
                        self.assertIn("BARE METAL", screen.text())
                        self.assertIn("HYPERSPACE", screen.text())
                    session.handle("escape")
                    self.assertEqual(before, (session.sim.elapsed, session.sim.rng.getstate(), tuple(session.sim.logs)))

    def test_animation_menu_has_two_columns_and_scope_can_be_toggled(self):
        session = Session()
        screen = Screen(44, 144)
        renderer = Renderer(Canvas(screen, color=False))
        renderer.draw(session)
        self.assertIn("MATH SCOPE", screen.text())
        session.handle("animation")
        renderer.draw(session)
        for label in ("SWITCH ANIMATION", "T-POSE AVATAR", "SOLID CUBE", "ASSET SPHERE", "SHARED OPTIONS"):
            self.assertIn(label, screen.text())
        session.handle("right")
        session.handle("down")
        session.handle("accept")
        session.handle("escape")
        renderer.draw(session)
        self.assertNotIn("MATH SCOPE", screen.text())

    def test_options_are_accessible_in_a_narrow_terminal(self):
        session = Session()
        screen = Screen(16, 48)
        renderer = Renderer(Canvas(screen, color=False))
        session.handle("animation")
        session.handle("right")
        for _ in range(7):
            renderer.draw(session)
            self.assertIn("SHARED OPTIONS", screen.text())
            session.handle("down")

    def test_reactor_and_its_selected_menu_entry_fit_all_scenes_and_sizes(self):
        for mode in MODES:
            session = Session(mode.id, animation="reactor", seed=42)
            session.update(.7)
            for rows, cols in ((4, 12), (16, 48), (24, 80), (44, 144), (60, 240)):
                with self.subTest(mode=mode.id, size=(cols, rows)):
                    screen = Screen(rows, cols)
                    renderer = Renderer(Canvas(screen, color=False))
                    renderer.draw(session)
                    if rows >= 44:
                        self.assertIn("PLASMA CORE", screen.text())
                    session.handle("animation")
                    renderer.draw(session)
                    if rows >= 16 and cols >= 48:
                        self.assertIn("PLASMA REACTOR", screen.text())
                    session.handle("escape")
