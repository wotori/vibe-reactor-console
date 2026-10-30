import dataclasses
import unittest

from devos.games.snake import SnakeGame


class SnakeGameTests(unittest.TestCase):
    def start(self, seed=42):
        game = SnakeGame(seed)
        game.handle("start")
        return game

    def tick(self, game, count=1):
        for _ in range(count):
            game.update(game.step_seconds)

    def test_ready_waits_for_explicit_start(self):
        game = SnakeGame()
        original = game.snapshot()
        game.handle("up")
        game.update(100)
        self.assertEqual(game.snapshot(), original)
        game.handle("start")
        self.tick(game)
        self.assertEqual(game.body[0], (15, 9))
        self.assertEqual(game.status, "PLAYING")

    def test_food_is_seeded_free_and_restart_reproduces_it(self):
        game = self.start(81)
        original = game.food
        self.assertNotIn(original, game.body)
        self.assertEqual(original, SnakeGame(81).food)
        game.food = (game.body[0][0] + 1, game.body[0][1])
        self.tick(game)
        self.assertEqual(game.score, 10)
        self.assertEqual(len(game.body), 4)
        self.assertNotIn(game.food, game.body)
        game.restart()
        self.assertEqual(game.food, original)
        self.assertEqual(game.status, "READY")
        self.assertEqual(game.score, 0)

    def test_reverse_is_ignored_including_multiple_quick_turns(self):
        game = self.start()
        game.handle("left")
        game.handle("up")
        game.handle("left")
        self.tick(game)
        self.assertEqual(game.body[0], (14, 8))
        self.assertEqual(game.direction, (0, -1))
        game.handle("left")
        self.tick(game)
        self.assertEqual(game.body[0], (13, 8))
        self.assertEqual(game.status, "PLAYING")

    def test_wall_collision_stops_simulation_until_restart(self):
        game = self.start()
        game.food = (0, 0)
        self.tick(game, 14)
        self.assertEqual(game.status, "GAME OVER")
        self.assertEqual(game.body[0], (27, 9))
        ended = game.snapshot()
        game.update(100)
        game.handle("up")
        self.assertEqual(game.snapshot(), ended)
        game.handle("start")
        self.assertEqual(game.status, "PLAYING")
        self.assertEqual(len(game.body), 3)

    def test_self_collision_and_moving_tail_cell(self):
        game = self.start()
        game.body = [(2, 2), (2, 3), (3, 3), (3, 2)]
        game.food = (0, 0)
        self.tick(game)
        self.assertEqual(game.status, "PLAYING")
        self.assertEqual(game.body[0], (3, 2))
        game.body = [(2, 2), (3, 2), (3, 3), (2, 3)]
        self.tick(game)
        self.assertEqual(game.status, "GAME OVER")
        self.assertEqual(game.reason, "TAIL COLLISION")

    def test_last_food_completes_board(self):
        game = self.start()
        game.body = [(0, 0)] + [
            (x, y) for y in range(game.height) for x in range(game.width)
            if (x, y) not in ((0, 0), (1, 0))]
        game.food = (1, 0)
        self.tick(game)
        self.assertEqual(game.status, "WON")
        self.assertIsNone(game.food)
        self.assertEqual(len(game.body), game.width * game.height)

    def test_speed_increases_with_level_and_has_floor(self):
        game = self.start()
        game.score = 40
        game.food = (15, 9)
        self.tick(game)
        self.assertEqual(game.level, 2)
        self.assertLess(game.step_seconds, 0.13)
        game.level = 100000
        self.assertGreaterEqual(game.step_seconds, 0.055)

    def test_pause_freezes_and_start_resumes_without_restart(self):
        game = self.start()
        self.tick(game)
        game.handle("pause")
        paused = game.snapshot()
        game.update(100)
        game.handle("up")
        self.assertEqual(game.snapshot(), paused)
        game.handle("start")
        self.tick(game)
        self.assertEqual(game.body[0], (16, 9))
        game.handle("pause")
        game.handle("pause")
        self.assertEqual(game.status, "PLAYING")

    def test_fixed_step_partition_and_large_dt_bound(self):
        one, split = self.start(), self.start()
        one.update(0.39)
        for _ in range(39):
            split.update(0.01)
        self.assertEqual(one.snapshot(), split.snapshot())
        large, bounded = self.start(), self.start()
        large.update(1000000)
        bounded.update(0.5)
        self.assertEqual(large.snapshot(), bounded.snapshot())
        before = large.snapshot()
        for value in (float("inf"), float("nan"), -1, 0):
            large.update(value)
        self.assertEqual(large.snapshot(), before)

    def test_snapshot_is_immutable_ascii_and_does_not_advance(self):
        game = self.start()
        first = game.snapshot()
        self.assertEqual(game.snapshot(), first)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            first.score = 99
        for cell in first.cells:
            self.assertEqual(len(cell.char), 1)
            self.assertTrue(cell.char.isascii())
            self.assertTrue(0 <= cell.x < first.width)
            self.assertTrue(0 <= cell.y < first.height)


if __name__ == "__main__":
    unittest.main()
