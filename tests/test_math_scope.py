"""Geometry and clock contracts for the mathematical oscilloscope."""

from dataclasses import FrozenInstanceError
import math
import unittest

from devos.graphics import Pixel
from devos.math_scope import SCOPES, SCOPE_BY_ID, render_scope, scope_caption


class MathScopeTests(unittest.TestCase):
    def test_catalog_has_distinct_immutable_descriptors(self):
        expected = {"harmonics", "parabola", "lissajous", "fourier", "wave-packet",
                    "rose", "spiral", "epicycles"}
        self.assertEqual(set(SCOPE_BY_ID), expected)
        self.assertEqual(len(SCOPES), len(expected))
        for scope in SCOPES:
            self.assertIs(SCOPE_BY_ID[scope.id], scope)
            for text in (scope.title, scope.formula, scope.description):
                self.assertTrue(text)
                self.assertTrue(text.isascii())
            with self.assertRaises(FrozenInstanceError):
                scope.title = "changed"

    def test_all_modes_stay_inside_tiny_and_large_viewports(self):
        for width, height in ((0, 0), (-1, 5), (5, -1), (1, 1), (2, 2),
                              (30, 5), (49, 5), (80, 10), (138, 37), (240, 60)):
            for scope in SCOPES:
                for elapsed in (0.0, 3.2, 53.125):
                    with self.subTest(mode=scope.id, size=(width, height), time=elapsed):
                        frame = render_scope(scope.id, elapsed, width, height)
                        self.assertLessEqual(len(frame), max(0, width) * max(0, height))
                        if width <= 0 or height <= 0:
                            self.assertEqual(frame, {})
                        else:
                            self.assertTrue(frame)
                        for (x, y), pixel in frame.items():
                            self.assertIsInstance(x, int)
                            self.assertIsInstance(y, int)
                            self.assertTrue(0 <= x < width and 0 <= y < height)
                            self.assertIsInstance(pixel, Pixel)
                            self.assertEqual(len(pixel.char), 1)
                            self.assertTrue(pixel.char.isascii())
                            self.assertTrue(pixel.char.isprintable())
                            self.assertIn(pixel.tone, {"muted", "accent", "main", "hot"})

    def test_frame_does_not_depend_on_render_history_or_frame_rate(self):
        for scope in SCOPES:
            with self.subTest(mode=scope.id):
                direct = render_scope(scope.id, 8.25, 80, 10)
                for time in (0, 0.01, 0.02, 2.0, 15.0, 8.2):
                    render_scope(scope.id, time, 80, 10)
                self.assertEqual(render_scope(scope.id, 8.25, 80, 10), direct)
                render_scope(scope.id, 8.25, 240, 60)
                self.assertEqual(render_scope(scope.id, 8.25, 80, 10), direct)

    def test_every_mode_moves_even_in_the_small_source_panel(self):
        for scope in SCOPES:
            with self.subTest(mode=scope.id):
                frames = [render_scope(scope.id, time, 49, 5) for time in (0, 1.0, 3.2)]
                self.assertNotEqual(frames[0], frames[1])
                self.assertNotEqual(frames[1], frames[2])
                for frame in frames:
                    self.assertTrue(any(pixel.tone == "hot" for pixel in frame.values()))

    def test_nonfinite_clock_is_safe_and_unknown_mode_uses_harmonics(self):
        for scope in SCOPES:
            reference = render_scope(scope.id, 0, 49, 5)
            for time in (math.nan, math.inf, -math.inf, None, "not a clock"):
                self.assertEqual(render_scope(scope.id, time, 49, 5), reference)
            self.assertTrue(render_scope(scope.id, 1e308, 49, 5))
        self.assertEqual(render_scope("missing", 1, 49, 5),
                         render_scope("harmonics", 1, 49, 5))

    def test_parabola_opens_upward_and_vertex_moves(self):
        width, height = 101, 41
        first = render_scope("parabola", 0, width, height)
        vertex = next(position for position, pixel in first.items() if pixel.char == "@")
        self.assertEqual(vertex, (50, 30))
        # At t=0 the real curve is y=x^2-.52.  Its two endpoints are
        # above the vertex on screen; a sine or inverted bowl fails this check.
        for edge in (0, width - 1):
            main_rows = [y for (x, y), pixel in first.items()
                         if x == edge and pixel.tone == "main"]
            self.assertTrue(main_rows)
            self.assertLess(max(main_rows), vertex[1] - 10)
        second = render_scope("parabola", 2.0, width, height)
        moved = next(position for position, pixel in second.items() if pixel.char == "@")
        self.assertGreater(moved[0], vertex[0] + 10)
        self.assertNotEqual(moved[1], vertex[1])

    def test_epicycle_endpoint_and_waveform_begin_at_the_same_ordinate(self):
        for time in (0.0, 0.73, 2.9, 8.25):
            with self.subTest(time=time):
                frame = render_scope("epicycles", time, 141, 41)
                head = [position for position, pixel in frame.items()
                        if pixel.char == "@" and pixel.tone == "hot"]
                trace = [position for position, pixel in frame.items()
                         if pixel.char == "+" and pixel.tone == "hot"]
                self.assertEqual(len(head), 1)
                self.assertEqual(len(trace), 1)
                self.assertEqual(head[0][1], trace[0][1])
                self.assertLess(head[0][0], trace[0][0])
        frame = render_scope("epicycles", 0, 141, 41)
        self.assertEqual(frame[round(140 * 0.445), 20], Pixel("@", "hot", True))

    def test_parametric_traces_retain_faded_and_bright_sections(self):
        for mode in ("lissajous", "rose", "spiral"):
            with self.subTest(mode=mode):
                frame = render_scope(mode, 3.2, 138, 37)
                tones = {pixel.tone for pixel in frame.values()}
                self.assertEqual(tones, {"muted", "accent", "main", "hot"})
                curve = [(x, y) for (x, y), pixel in frame.items()
                         if pixel.char not in (".", ":", "+")]
                self.assertGreater(max(x for x, _ in curve) - min(x for x, _ in curve), 90)
                self.assertGreater(max(y for _, y in curve) - min(y for _, y in curve), 20)

    def test_readout_explains_changing_math_parameters(self):
        self.assertIn("1 odd harmonic", scope_caption("fourier", 0))
        self.assertIn("8 odd harmonics", scope_caption("fourier", 11.0))
        self.assertIn("n=1..15", scope_caption("fourier", 11.0))
        self.assertNotEqual(scope_caption("parabola", 0), scope_caption("parabola", 2))
        for scope in SCOPES:
            for time in (0, 3.2):
                caption = scope_caption(scope.id, time)
                self.assertTrue(caption)
                self.assertTrue(caption.isascii())
        self.assertEqual(scope_caption("missing", 0), SCOPE_BY_ID["harmonics"].formula)


if __name__ == "__main__":
    unittest.main()
