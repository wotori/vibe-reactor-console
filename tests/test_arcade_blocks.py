import unittest

from devos.games.blocks import BlocksGame, SHAPES


class BlocksGameTests(unittest.TestCase):
    def start(self, seed=42):
        game = BlocksGame(seed)
        game.handle("start")
        return game

    def place(self, game, kind, x, y, rotation=0):
        game.kind, game.x, game.y, game.rotation = kind, x, y, rotation

    def test_ready_waits_for_start_and_gravity_is_fixed(self):
        game = BlocksGame()
        ready = game.snapshot()
        game.handle("drop")
        game.update(100)
        self.assertEqual(game.snapshot(), ready)
        game.handle("start")
        game.update(0.4)
        self.assertEqual(game.y, 0)
        game.update(0.4)
        self.assertEqual(game.y, 1)

    def test_seven_bag_contains_each_piece_once_and_seed_restarts(self):
        game = BlocksGame(129)
        first = [game.kind, game.next_kind] + [game._draw_kind() for _ in range(5)]
        second = [game._draw_kind() for _ in range(7)]
        self.assertEqual(set(first), set(SHAPES))
        self.assertEqual(set(second), set(SHAPES))
        self.assertEqual(len(first), 7)
        game.restart()
        self.assertEqual(game.kind, first[0])
        self.assertEqual(game.next_kind, first[1])
        self.assertEqual(game.status, "READY")

    def test_move_respects_walls_and_occupied_cells(self):
        game = self.start()
        self.place(game, "O", -1, 5)
        game.handle("left")
        self.assertEqual(game.x, -1)
        game.board[5][2] = "T"
        game.handle("right")
        self.assertEqual(game.x, -1)
        game.board[5][2] = None
        game.handle("right")
        self.assertEqual(game.x, 0)

    def test_rotation_kicks_piece_off_left_wall(self):
        game = self.start()
        self.place(game, "I", -2, 5, 1)
        self.assertTrue(game._fits())
        game.handle("rotate")
        self.assertEqual(game.rotation, 2)
        self.assertEqual(game.x, 0)
        self.assertTrue(game._fits())

    def test_rotation_cannot_pass_through_existing_stack(self):
        game = self.start()
        self.place(game, "T", 3, 5)
        active = set(game._points())
        game.board = [[None if (x, y) in active else "O"
                       for x in range(game.width)] for y in range(game.height)]
        original = game.x, game.y, game.rotation
        game.handle("rotate")
        self.assertEqual((game.x, game.y, game.rotation), original)

    def test_ghost_uses_real_stack_and_hard_drop_locks_at_ghost(self):
        game = self.start()
        self.place(game, "O", 3, 0)
        game.board[15][4] = "I"
        self.assertEqual(game.ghost_y, 13)
        snapshot = game.snapshot()
        self.assertEqual({(c.x, c.y) for c in snapshot.cells if c.char == ":"},
                         {(4, 13), (5, 13), (4, 14), (5, 14)})
        next_kind = game.next_kind
        game.handle("drop")
        self.assertEqual(game.score, 26)
        for x, y in ((4, 13), (5, 13), (4, 14), (5, 14)):
            self.assertEqual(game.board[y][x], "O")
        self.assertEqual(game.kind, next_kind)
        self.assertEqual(game.y, 0)

    def test_soft_drop_scores_only_successful_rows(self):
        game = self.start()
        self.place(game, "O", 3, 17)
        game.handle("soft-drop")
        self.assertEqual(game.y, 18)
        self.assertEqual(game.score, 1)
        game.handle("down")
        self.assertEqual(game.score, 1)
        self.assertEqual(game.board[19][4], "O")

    def test_single_line_compacts_stack_and_scores(self):
        game = self.start()
        self.place(game, "I", 0, 18)
        game.board[19] = [None] * 4 + ["T"] * 6
        game.board[18][9] = "L"
        game.handle("drop")
        self.assertEqual(game.lines, 1)
        self.assertEqual(game.score, 100)
        self.assertEqual(game.board[19][9], "L")
        self.assertEqual(game.board[0], [None] * game.width)

    def test_four_lines_and_level_transition_use_pre_clear_level(self):
        game = self.start()
        game.lines = 9
        game.board[16:20] = [[None] + ["T"] * 9 for _ in range(4)]
        self.place(game, "I", -2, 16, 1)
        game.handle("drop")
        self.assertEqual(game.lines, 13)
        self.assertEqual(game.level, 2)
        self.assertEqual(game.score, 800)
        self.assertTrue(all(value is None for row in game.board for value in row))
        self.assertLess(game.step_seconds, 0.8)

    def test_spawn_obstruction_is_game_over_without_erasing_stack(self):
        game = self.start()
        game.board[0:4] = [["T"] * game.width for _ in range(4)]
        board = [list(row) for row in game.board]
        game._spawn()
        self.assertEqual(game.status, "GAME OVER")
        self.assertEqual(game.board, board)
        ended = game.snapshot()
        game.handle("drop")
        game.update(100)
        self.assertEqual(game.snapshot(), ended)
        game.handle("start")
        self.assertEqual(game.status, "PLAYING")
        self.assertTrue(all(value is None for row in game.board for value in row))

    def test_pause_preserves_board_and_resume_does_not_reset(self):
        game = self.start()
        game.handle("left")
        game.handle("pause")
        snapshot = game.snapshot()
        game.handle("drop")
        game.update(10)
        self.assertEqual(game.snapshot(), snapshot)
        game.handle("pause")
        self.assertEqual(game.status, "PLAYING")
        self.assertEqual(game.x, 2)

    def test_deterministic_timing_and_catchup_is_bounded(self):
        one, split = self.start(), self.start()
        one.update(0.4)
        one.update(0.4)
        for _ in range(80):
            split.update(0.01)
        self.assertEqual(one.snapshot(), split.snapshot())
        large, bounded = self.start(), self.start()
        large.level = bounded.level = 50
        large.update(1000000)
        bounded.update(0.5)
        self.assertEqual(large.snapshot(), bounded.snapshot())
        before = large.snapshot()
        for value in (float("inf"), float("nan"), -10, 0):
            large.update(value)
        self.assertEqual(large.snapshot(), before)

    def test_snapshot_is_pure_with_ascii_cells_and_next_preview(self):
        game = self.start()
        first = game.snapshot()
        self.assertEqual(game.snapshot(), first)
        self.assertIn("NEXT: " + game.next_kind, first.details)
        for cell in first.cells:
            self.assertEqual(len(cell.char), 1)
            self.assertTrue(cell.char.isascii())
            self.assertTrue(0 <= cell.x < first.width)
            self.assertTrue(0 <= cell.y < first.height)


if __name__ == "__main__":
    unittest.main()
