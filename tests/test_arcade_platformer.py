"""Playable physics contracts for the original Byte Valley platformer."""

from dataclasses import FrozenInstanceError
import math
import unittest

from devos.games.core import GameFrame
from devos.games.platformer import PlatformerGame


class PlatformerTests(unittest.TestCase):
    def make_game(self):
        game = PlatformerGame(seed=37)
        game.handle("start")
        return game

    def tick(self, game, seconds, action=None):
        for _ in range(round(seconds * 60)):
            if action:
                game.handle(action)
            game.update(1.0 / 60.0)

    def place(self, game, x, y=15.1, vy=0, grounded=True):
        game.x, game.y, game.vy = x, y, vy
        game.vx = 0
        game.grounded = grounded
        game._coyote = 0.08 if grounded else 0.0

    def test_waits_for_start_and_restart_resets_session(self):
        game = PlatformerGame()
        frame = game.snapshot()
        game.handle("right")
        game.update(1)
        self.assertEqual(frame, game.snapshot())
        self.assertEqual(frame.status, "READY")
        game.handle("start")
        self.tick(game, 0.25, "right")
        self.assertGreater(game.x, 3)
        game.handle("restart")
        self.assertEqual(game.snapshot(), frame)

    def test_gravity_lands_exactly_on_floor_without_render_advancing(self):
        game = self.make_game()
        self.place(game, 4, 10, grounded=False)
        self.tick(game, 1)
        self.assertAlmostEqual(game.y + game.PLAYER_HEIGHT, 16)
        self.assertEqual(game.vy, 0)
        self.assertTrue(game.grounded)
        before = game.snapshot()
        self.assertEqual(game.snapshot(), before)
        with self.assertRaises(FrozenInstanceError):
            before.score = 99

    def test_jump_has_arc_and_air_jump_does_not_reset_velocity(self):
        game = self.make_game()
        game.handle("jump")
        self.tick(game, 0.2)
        self.assertLess(game.y, 14)
        self.assertLess(game.vy, 0)
        previous_vy = game.vy
        game.handle("jump")
        game.update(game.STEP)
        self.assertGreater(game.vy, previous_vy)
        self.tick(game, 0.9)
        self.assertTrue(game.grounded)
        self.assertAlmostEqual(game.y + game.PLAYER_HEIGHT, 16)

    def test_horizontal_input_expires_and_friction_stops_motion(self):
        game = self.make_game()
        game.handle("right")
        self.tick(game, 0.2)
        moved = game.x
        self.assertGreater(moved, 3.5)
        self.tick(game, 0.5)
        self.assertEqual(game.vx, 0)
        stopped = game.x
        self.tick(game, 0.5)
        self.assertEqual(game.x, stopped)
        self.assertLess(stopped - moved, 0.7)
        self.tick(game, 0.25, "left")
        self.assertLess(game.x, stopped)

    def test_solid_wall_stops_horizontal_movement(self):
        game = self.make_game()
        self.place(game, 20)
        self.tick(game, 0.7, "right")
        self.assertAlmostEqual(game.x + game.PLAYER_WIDTH, 22)
        self.assertEqual(game.vx, 0)

    def test_head_collision_stops_ascent_and_platform_landing_is_solid(self):
        game = self.make_game()
        self.place(game, 11)
        game.handle("jump")
        self.tick(game, 0.2)
        self.assertGreaterEqual(game.y, 15)
        self.assertGreaterEqual(game.vy, 0)
        self.place(game, 11, 11.5, vy=3, grounded=False)
        self.tick(game, 0.4)
        self.assertTrue(game.grounded)
        self.assertAlmostEqual(game.y + game.PLAYER_HEIGHT, 14)

    def test_ordinary_jump_clears_four_cell_pit(self):
        game = self.make_game()
        self.place(game, 65.3)
        # The nearby spring is behind this takeoff.  Ordinary jump alone must
        # clear the widest pit, so the handcrafted route remains beatable.
        game.handle("jump")
        self.tick(game, 0.9, "right")
        self.assertGreater(game.x, 71)
        self.assertEqual(game.lives, 3)
        self.assertTrue(game.grounded)

    def test_complete_handcrafted_level_is_beatable_without_losing_lives(self):
        game = self.make_game()
        # A recorded 23.9-second keyboard route through the unmodified world.
        # Each numbered interval is 0.1s; jumps are short repeated key impulses.
        # This crosses every pit/obstacle/enemy rather than teleporting to a goal.
        jump_intervals = {4, 14, 20, 31, 41, 49, 68, 74, 92, 101, 108,
                          117, 125, 132, 141, 153, 166, 176, 193, 206,
                          215, 222, 233}
        for interval in range(239):
            for _ in range(6):
                game.handle("right")
                if interval in jump_intervals:
                    game.handle("jump")
                game.update(game.STEP)
        self.assertEqual(game.status, "WON")
        self.assertEqual(game.lives, 3)
        self.assertEqual(game.checkpoint, game.CHECKPOINTS[-1])
        self.assertGreater(game.score, 1000)

    def test_fall_costs_life_and_restores_safe_checkpoint(self):
        game = self.make_game()
        game.checkpoint = 49
        self.place(game, 33, 21, grounded=False)
        game.update(game.STEP)
        self.assertEqual(game.lives, 2)
        self.assertEqual(game.x, 49)
        self.assertTrue(game.grounded)
        self.assertEqual(game.status, "PLAYING")
        for _ in range(2):
            self.place(game, 33, 21, grounded=False)
            game.update(game.STEP)
        self.assertEqual(game.status, "GAME OVER")
        self.assertEqual(game.lives, 0)
        before = game.snapshot()
        game.update(1)
        self.assertEqual(game.snapshot(), before)
        game.handle("start")
        self.assertEqual(game.status, "PLAYING")
        self.assertEqual(game.lives, 3)

    def test_enemy_stomp_bounces_but_side_collision_costs_life(self):
        game = self.make_game()
        enemy = game.enemies[0]
        self.place(game, enemy.x, 14.1, vy=10, grounded=False)
        game.update(game.STEP)
        self.assertFalse(enemy.alive)
        self.assertLess(game.vy, 0)
        self.assertEqual(game.score, 150)
        self.assertEqual(game.lives, 3)
        other = game.enemies[1]
        self.place(game, other.x)
        game.update(game.STEP)
        self.assertEqual(game.lives, 2)
        self.assertTrue(other.alive)
        # A respawn shield prevents repeated contact from draining all lives.
        self.place(game, other.x)
        game.update(game.STEP)
        self.assertEqual(game.lives, 2)

    def test_coin_collected_once_and_checkpoint_activated_once(self):
        game = self.make_game()
        coin = (5, 14)
        self.assertIn(coin, game.coins)
        self.place(game, 5, 14, grounded=False)
        game.update(game.STEP)
        self.assertNotIn(coin, game.coins)
        self.assertEqual(game.score, 100)
        game.update(game.STEP)
        self.assertEqual(game.score, 100)
        self.place(game, 49)
        game.update(game.STEP)
        self.assertEqual(game.checkpoint, 49)
        self.assertEqual(game.score, 350)
        game.update(game.STEP)
        self.assertEqual(game.score, 350)

    def test_spring_launches_only_from_ground(self):
        game = self.make_game()
        self.place(game, 61)
        game.update(game.STEP)
        self.assertLess(game.vy, -15)
        self.assertFalse(game.grounded)
        # Standing on a shelf above the spring cannot trigger the spring.
        game.tiles[(61, 12)] = "="
        self.place(game, 61, 12 - game.PLAYER_HEIGHT)
        game.update(game.STEP)
        self.assertEqual(game.vy, 0)
        self.assertTrue(game.grounded)

    def test_goal_wins_once_and_camera_clamps_at_world_edges(self):
        game = self.make_game()
        self.assertEqual(game.camera_x, 0)
        self.place(game, 100)
        self.assertEqual(game.camera_x, 87)
        self.place(game, game.GOAL_X)
        game.update(game.STEP)
        self.assertEqual(game.status, "WON")
        self.assertEqual(game.camera_x, game.WORLD_WIDTH - game.WIDTH)
        score = game.score
        self.tick(game, 1)
        self.assertEqual(game.score, score)
        frame = game.snapshot()
        self.assertTrue(any(cell.char == ">" for cell in frame.cells))

    def test_pause_freezes_physics_and_discards_stale_direction(self):
        game = self.make_game()
        game.handle("right")
        self.tick(game, 0.1)
        game.handle("pause")
        frame = game.snapshot()
        self.tick(game, 1, "left")
        self.assertEqual(game.snapshot(), frame)
        self.assertEqual(game.status, "PAUSED")
        x = game.x
        game.handle("start")
        self.tick(game, 0.2)
        self.assertEqual(game.x, x)

    def test_fixed_step_determinism_and_invalid_delta_are_bounded(self):
        first, second = self.make_game(), self.make_game()
        for game in (first, second):
            game.handle("right")
            game.handle("jump")
        for _ in range(24):
            first.update(1 / 120)
        for _ in range(6):
            second.update(1 / 30)
        self.assertEqual(first.snapshot(), second.snapshot())
        self.assertEqual((first.x, first.y, first.vx, first.vy),
                         (second.x, second.y, second.vx, second.vy))
        frame = first.snapshot()
        for dt in (None, "bad", -1, 0, math.nan, math.inf):
            first.update(dt)
            self.assertEqual(first.snapshot(), frame)
        first.update(1e9)
        self.assertLessEqual(first.elapsed - second.elapsed, 0.2000001)

    def test_snapshot_cells_are_unique_bounded_ascii_and_read_only(self):
        game = self.make_game()
        for x in (3, 70, 139, 169):
            self.place(game, x)
            frame = game.snapshot()
            self.assertIsInstance(frame, GameFrame)
            self.assertEqual((frame.width, frame.height), (40, 18))
            self.assertEqual(len({(c.x, c.y) for c in frame.cells}), len(frame.cells))
            self.assertEqual(sum(c.char == "@" for c in frame.cells), 1)
            for cell in frame.cells:
                self.assertTrue(0 <= cell.x < frame.width)
                self.assertTrue(0 <= cell.y < frame.height)
                self.assertTrue(len(cell.char) == 1 and cell.char.isascii()
                                and cell.char.isprintable())
                self.assertIn(cell.tone, {"main", "muted", "accent", "hot"})
            self.assertEqual(frame, game.snapshot())


if __name__ == "__main__":
    unittest.main()
