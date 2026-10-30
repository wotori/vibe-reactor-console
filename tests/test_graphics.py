from collections import Counter
import math
import unittest

from devos.graphics import edges, line_cells, project, rasterize, rotate, vertices, wave, wireframe
from devos.syntax import spans


class GeometryTests(unittest.TestCase):
    def test_tesseract_has_sixteen_vertices_and_thirty_two_edges(self):
        points, connections = vertices(4), edges(4)
        self.assertEqual(len(set(points)), 16)
        self.assertEqual(len(set(connections)), 32)
        self.assertEqual(set(Counter(i for pair in connections for i in pair).values()), {4})
        for first, second in connections:
            self.assertEqual(sum(a != b for a, b in zip(points[first], points[second])), 1)

    def test_rotations_preserve_norm_and_projections_are_finite(self):
        for point in vertices(4):
            for elapsed in (0, 0.2, 12, 1234, 99999):
                rotated = rotate(rotate(point, 0, 3, elapsed), 1, 2, elapsed * 0.3)
                self.assertAlmostEqual(sum(v * v for v in rotated), 4)
                self.assertTrue(all(math.isfinite(v) for v in project(point, elapsed)))

    def test_ascii_projection_is_bounded_and_animated(self):
        for geometry in ("cube", "tesseract"):
            for width, height in ((0, 0), (3, 3), (24, 7), (50, 16), (96, 40)):
                for elapsed in (0, 1, 3, 20, 100):
                    pixels = wireframe(geometry, elapsed, width, height)
                    self.assertTrue(all(0 <= x < width and 0 <= y < height for x, y in pixels))
                    self.assertTrue(all(pixel.char.isascii() for pixel in pixels.values()))
            self.assertNotEqual(wireframe(geometry, 0, 60, 24), wireframe(geometry, 2, 60, 24))
        self.assertIn("hot", {p.tone for p in wireframe("tesseract", 1, 80, 30).values()})

    def test_wave_is_bounded(self):
        for elapsed in (0, 0.25, 99):
            self.assertTrue(all(0 <= x < 40 and 0 <= y < 3 for x, y in wave(elapsed, 40)))

    def test_initial_view_is_two_nested_cubes_with_radial_w_links(self):
        points = [project(point, 0) for point in vertices(4)]
        for index in range(0, 16, 2):
            inner, outer = points[index], points[index + 1]
            self.assertAlmostEqual(inner[0] * outer[1] - inner[1] * outer[0], 0)
            self.assertGreater(math.hypot(*outer[:2]), math.hypot(*inner[:2]) * 1.5)

    def test_line_raster_is_connected_at_shallow_and_steep_angles(self):
        for end in ((23, 7), (7, 23), (-23, 7), (0, 10), (10, 0)):
            cells = line_cells(0, 0, *end)
            self.assertEqual(cells[0], (0, 0))
            self.assertEqual(cells[-1], end)
            self.assertTrue(all(max(abs(a - c), abs(b - d)) <= 1
                                for (a, b), (c, d) in zip(cells, cells[1:])))

    def test_near_edge_wins_a_crossing_regardless_of_draw_order(self):
        points = [(1, 3, -1), (7, 3, -1), (4, 1, 1), (4, 5, 1)]
        for connections in (((0, 1), (2, 3)), ((2, 3), (0, 1))):
            pixels = rasterize(points, connections, 3, 9, 7)
            self.assertEqual(pixels[4, 3].char, "|")


class SyntaxTests(unittest.TestCase):
    def test_assembly_registers_opcodes_hex_and_comments(self):
        colored = {token: tone for _, token, tone in spans("mov rax, 0xff ; long mode", "NASM")}
        self.assertEqual(colored["mov"], "hot")
        self.assertEqual(colored["rax"], "accent")
        self.assertEqual(colored["0xff"], "warn")
        self.assertEqual(colored["; long mode"], "muted")

    def test_c_semicolon_does_not_start_a_comment(self):
        colored = {token: tone for _, token, tone in spans("uint64_t flags = 0x1000; return flags; // end", "C")}
        self.assertEqual(colored["uint64_t"], "hot")
        self.assertEqual(colored["return"], "hot")
        self.assertEqual(colored["// end"], "muted")
