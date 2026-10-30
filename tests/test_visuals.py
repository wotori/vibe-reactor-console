import math
import unittest

from devos.protocol import CONSUMERS, STAGES, STAGE_SECONDS, protocol_frame
from devos.session import Session
from devos.surfaces import mesh_for, render_surface
from devos.visuals import ANIMATIONS, render_animation


class VisualTests(unittest.TestCase):
    def test_every_animation_is_bounded_ascii_and_moves(self):
        for animation in ANIMATIONS:
            with self.subTest(animation=animation.id):
                for width, height in ((0, 0), (3, 3), (28, 7), (50, 16), (90, 30)):
                    for elapsed in (0, .7, 7):
                        pixels = render_animation(animation.id, elapsed, width, height, protocol_frame(elapsed))
                        self.assertTrue(all(0 <= x < width and 0 <= y < height for x, y in pixels))
                        self.assertTrue(all(pixel.char.isascii() for pixel in pixels.values()))
                first = render_animation(animation.id, 0, 60, 24, protocol_frame(0))
                later = render_animation(animation.id, 1, 60, 24, protocol_frame(1))
                self.assertNotEqual(first, later)

    def test_avatar_has_a_wide_t_pose_and_separated_legs(self):
        mesh = mesh_for("avatar")
        self.assertGreaterEqual(max(x for x, _, _ in mesh.points), 1.3)
        self.assertLessEqual(min(x for x, _, _ in mesh.points), -1.3)
        self.assertTrue(all(math.isfinite(value) for point in mesh.points for value in point))
        pixels = render_surface("avatar", 0, 60, 30)
        row_counts = [sum((x, y) in pixels for x in range(60)) for y in range(30)]
        self.assertGreater(max(row_counts[:15]), max(row_counts[20:]) * 2)
        self.assertTrue(any((29, y) not in pixels and (26, y) in pixels and (33, y) in pixels
                            for y in range(20, 28)))

    def test_sphere_uses_lighting_and_material_bands(self):
        pixels = render_surface("sphere", .4, 60, 24)
        self.assertGreater(len({pixel.char for pixel in pixels.values()}), 4)
        self.assertTrue({"main", "accent", "hot"}.issubset({pixel.tone for pixel in pixels.values()}))

    def test_protocol_flow_has_a_visible_moving_packet(self):
        frame = protocol_frame(STAGE_SECONDS * .5)
        pixels = render_animation("protocol", 0, 60, 18, frame)
        self.assertIn("@", {pixel.char for pixel in pixels.values()})


class ReactorTests(unittest.TestCase):
    def test_reactor_frame_is_repeatable_after_rendering_another_time_and_size(self):
        first = render_animation("reactor", .4, 80, 32, protocol_frame(0))
        render_animation("reactor", 7, 28, 7, protocol_frame(7))
        repeated = render_animation("reactor", .4, 80, 32, protocol_frame(17))
        self.assertEqual(first, repeated)

    def test_reactor_has_shaded_core_and_visible_outer_orbits(self):
        pixels = render_animation("reactor", .4, 80, 32, protocol_frame(0))
        core = [pixel for (x, y), pixel in pixels.items() if 32 <= x < 48 and 12 <= y < 20]
        outside = [pixel for (x, y), pixel in pixels.items() if x < 20 or x >= 60 or y < 8 or y >= 24]
        self.assertGreater(len(core), 12, "the reactor should have a filled central volume")
        self.assertGreater(len({pixel.char for pixel in core}), 3, "the core should show surface shading")
        self.assertGreater(len(outside), 20, "orbital geometry should extend beyond the core")
        self.assertTrue(any(pixel.tone in ("accent", "hot") for pixel in pixels.values()))


class ProtocolTests(unittest.TestCase):
    def test_all_consumer_examples_include_both_kinds_without_false_support(self):
        observed = set()
        for cycle in range(6):
            frame = protocol_frame(cycle * STAGE_SECONDS * len(STAGES), "all")
            observed.add((frame.consumer.id, frame.kind))
            self.assertTrue(frame.profile)
            if frame.kind == "weapon":
                self.assertTrue(frame.consumer.weapon_profile)
        self.assertTrue({("space", "avatar"), ("mirror", "avatar"), ("omoba", "avatar"),
                         ("omoba", "weapon")}.issubset(observed))
        self.assertNotIn(("space", "weapon"), observed)
        self.assertNotIn(("mirror", "weapon"), observed)

    def test_identity_is_stable_across_changed_revisions_and_hashes(self):
        first = protocol_frame(0)
        later = protocol_frame(STAGE_SECONDS * len(STAGES) * 2)
        self.assertEqual(first.identity, later.identity)
        self.assertNotEqual(first.revision, later.revision)
        self.assertNotEqual(first.digest, later.digest)

    def test_each_stage_explains_its_contract_and_selectors_are_exact(self):
        for consumer in CONSUMERS:
            for stage in range(len(STAGES)):
                frame = protocol_frame((stage + .5) * STAGE_SECONDS, consumer.id)
                self.assertEqual(frame.stage, stage)
                self.assertEqual(frame.explanation, STAGES[stage][1])
                self.assertAlmostEqual(frame.fraction, .5)
                self.assertEqual(frame.consumer.project_id, "omoba" if consumer.id == "omoba" else "ekza-" + consumer.id)


class ControlTests(unittest.TestCase):
    def test_animation_picker_does_not_change_code_and_uses_shared_options(self):
        session = Session()
        session.update(.5)
        original = session.sim
        before = original.typed_lines
        session.handle("animation")
        session.handle("right")
        session.handle("accept")  # Auto: off -> 8s
        session.handle("down")
        session.handle("accept")  # Scope: on -> off
        session.handle("down")
        session.handle("accept")  # FPS: 60 -> 90
        session.update(3)
        self.assertEqual(original.typed_lines, before)
        session.handle("left")
        session.handle("down")
        session.handle("accept")  # Avatar -> cube
        self.assertFalse(session.menu_open)
        self.assertEqual(session.animation, "cube")
        self.assertIs(session.sim, original)
        self.assertEqual(session.sim.typed_lines, before)
        options = session.options
        session.activate("bare-metal")
        self.assertIs(session.options, options)
        self.assertEqual(session.animation, "cube")
        self.assertEqual(session.options.fps, 90)
        self.assertFalse(session.options.scope)
        self.assertEqual(session.options.auto_cycle, 8)

    def test_automatic_cycle_uses_active_seconds_and_respects_pause_and_menu(self):
        session = Session(animation="avatar", auto_cycle=8, speed=4)
        session.update(7)
        self.assertEqual(session.animation, "avatar")
        session.handle("pause")
        session.update(30)
        self.assertEqual(session.animation, "avatar")
        session.handle("pause")
        session.handle("tab")
        session.update(30)
        session.handle("escape")
        self.assertEqual(session.animation, "avatar")
        session.update(1)
        self.assertEqual(session.animation, "cube")
        session.choose_animation("sphere")
        session.update(1)
        self.assertEqual(session.animation, "sphere")

    def test_global_options_survive_switching_both_scenes_and_visuals(self):
        session = Session()
        session.handle("scope")
        session.handle("consumer")
        session.handle("background")
        session.handle("theme")
        theme = session.theme
        session.activate("lab")
        session.choose_animation("sphere")
        self.assertFalse(session.options.scope)
        self.assertEqual(session.options.consumer, "space")
        self.assertEqual(session.background, "black")
        self.assertEqual(session.theme, theme)

    def test_reactor_is_selectable_without_resetting_code_or_shared_options(self):
        session = Session(fps=120, background="black", auto_cycle=15, scope=False)
        session.update(.5)
        original = session.sim
        before = (original.elapsed, original.typed_lines, tuple(original.logs))
        session.handle("animation")
        for _ in range(len(ANIMATIONS) - 1):
            session.handle("down")
        self.assertEqual(session.menu_entries[session.picker_index].id, "reactor")
        session.handle("accept")
        self.assertFalse(session.menu_open)
        self.assertEqual(session.animation, "reactor")
        self.assertIs(session.sim, original)
        self.assertEqual(before, (original.elapsed, original.typed_lines, tuple(original.logs)))
        session.activate("bare-metal")
        self.assertEqual(session.animation, "reactor")
        self.assertEqual(session.options.fps, 120)
        self.assertEqual(session.background, "black")
        self.assertEqual(session.options.auto_cycle, 15)
        self.assertFalse(session.options.scope)

    def test_automatic_cycle_reaches_reactor_and_wraps_to_avatar(self):
        session = Session(animation="tesseract", auto_cycle=8)
        session.update(8)
        self.assertEqual(session.animation, "reactor")
        session.update(8)
        self.assertEqual(session.animation, "avatar")
