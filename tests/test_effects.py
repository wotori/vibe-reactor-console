"""Visual contracts: stable worlds, finite inputs, bounded topology and cues."""

import math
from types import MappingProxyType
import unittest

from devos.effects import DepthCanvas, garden_topology
from devos.protocol import protocol_frame
from devos.visuals import ANIMATIONS, render_animation


class EffectTests(unittest.TestCase):
    def render(self, name, time=4.2, parameters=None, size=(64, 24)):
        return render_animation(name, time, *size, protocol_frame(time), parameters)

    def test_new_worlds_are_repeatable_across_resizing_and_time(self):
        for spec in ANIMATIONS[6:]:
            with self.subTest(spec=spec.id):
                bindings = MappingProxyType({"energy": .7, "event_sequence": 14, "particle_count": 130})
                initial = self.render(spec.id, parameters=bindings)
                self.render(spec.id, 99, size=(28, 10))
                self.assertEqual(initial, self.render(spec.id, parameters=bindings))
                self.assertGreater(len(initial), 20)
                self.assertLessEqual(len(initial), 64 * 24)
                self.assertTrue(all(len(pixel.char) == 1 and pixel.char.isascii() for pixel in initial.values()))

    def test_bindings_change_every_new_world_without_mutating_them(self):
        for name, parameter, low, high in (
                ("gravity-well", "particle_count", 0, 200),
                ("torus-knot", "test_passed", 0, 10),
                ("packet-garden", "seed", 1, 99),
                ("twin-core", "queue_left", 0, 12),
                ("voxel-tide", "energy", 0, 1),
                ("asset-orrery", "event_sequence", 0, 2),
                ("reactor", "pulse_bpm", 40, 120)):
            with self.subTest(name=name, parameter=parameter):
                first = {parameter: low, "test_total": 10}
                second = {parameter: high, "test_total": 10}
                before = dict(first)
                self.assertNotEqual(self.render(name, parameters=first), self.render(name, parameters=second))
                self.assertEqual(first, before)

    def test_malformed_parameters_are_finite_and_bounded(self):
        for spec in ANIMATIONS:
            for invalid in (None, "not-a-number", math.nan, math.inf, -math.inf):
                with self.subTest(name=spec.id, value=invalid):
                    pixels = self.render(spec.id, parameters={name: invalid for name in spec.parameters})
                    self.assertTrue(all(0 <= x < 64 and 0 <= y < 24 for x, y in pixels))

    def test_garden_never_grows_beyond_256_nodes_and_has_valid_parent_links(self):
        topology = garden_topology(42, 1000)
        self.assertEqual(len(topology), 256)
        self.assertEqual(topology[0].parent, -1)
        self.assertTrue(all(0 <= node.parent < index for index, node in enumerate(topology[1:], 1)))
        self.assertEqual({node.kind for node in topology}, {"arrival", "test", "handoff", "failure"})
        frame = self.render("packet-garden", parameters={"event_sequence": 1000})
        label = "".join(frame[x, 23].char if (x, 23) in frame else " " for x in range(64))
        self.assertIn("+744 EVENTS", label)

    def test_black_horizon_occludes_a_far_surface_but_accepts_a_near_particle(self):
        canvas = DepthCanvas(64, 24, 3.35)
        canvas.point((0, 0, -1), "*", "hot")
        canvas.sphere((0, 0, 0), .8, hollow=True)
        x, y, _ = canvas.project((0, 0, 0))
        self.assertEqual(canvas.pixels[round(x), round(y)].char, " ")
        canvas.point((0, 0, 1), "@", "hot")
        self.assertEqual(canvas.pixels[round(x), round(y)].char, "@")

    def test_event_burst_is_finite(self):
        idle = self.render("gravity-well")
        self.assertNotEqual(idle, self.render("gravity-well", parameters={"burst_age": .2}))
        self.assertEqual(idle, self.render("gravity-well", parameters={"burst_age": 1.2}))

    def test_torus_has_lighting_depth_and_test_front(self):
        pixels = self.render("torus-knot", parameters={"particle_count": 0})
        self.assertGreater(len({pixel.char for pixel in pixels.values()}), 4)
        self.assertTrue({"main", "muted", "hot"}.issubset({pixel.tone for pixel in pixels.values()}))
        self.assertNotEqual(pixels, self.render("torus-knot", parameters={"particle_count": 0,
                                                                       "test_passed": 5, "test_total": 10}))


if __name__ == "__main__":
    unittest.main()
