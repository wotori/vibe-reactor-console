"""A deterministic seven-bag falling-blocks game with no terminal coupling."""

import math
import random

from .core import GameCell, GameFrame


def _rotate(points):
    return tuple((2 - y, x) for x, y in points)


def _rotations(points):
    result = [tuple(points)]
    for _ in range(3):
        result.append(_rotate(result[-1]))
    return tuple(result)


SHAPES = {
    "I": (((0, 1), (1, 1), (2, 1), (3, 1)),
          ((2, 0), (2, 1), (2, 2), (2, 3)),
          ((0, 2), (1, 2), (2, 2), (3, 2)),
          ((1, 0), (1, 1), (1, 2), (1, 3))),
    "O": (((1, 0), (2, 0), (1, 1), (2, 1)),) * 4,
    "T": _rotations(((1, 0), (0, 1), (1, 1), (2, 1))),
    "S": _rotations(((1, 0), (2, 0), (0, 1), (1, 1))),
    "Z": _rotations(((0, 0), (1, 0), (1, 1), (2, 1))),
    "J": _rotations(((0, 0), (0, 1), (1, 1), (2, 1))),
    "L": _rotations(((2, 0), (0, 1), (1, 1), (2, 1))),
}


class BlocksGame:
    """Ten by twenty cells, clockwise wall-kick rotation and ghost landing."""

    width = 10
    height = 20

    def __init__(self, seed=42):
        self.seed = seed
        self.restart()

    def restart(self):
        self._rng = random.Random(self.seed)
        self._bag = []
        self.board = [[None for _ in range(self.width)] for _ in range(self.height)]
        self.score = 0
        self.level = 1
        self.lines = 0
        self.status = "READY"
        self.reason = ""
        self._elapsed = 0.0
        self._clear_rows = ()
        self._clear_seconds = 0.0
        self.next_kind = self._draw_kind()
        self._spawn()

    def _draw_kind(self):
        if not self._bag:
            self._bag = list(SHAPES)
            self._rng.shuffle(self._bag)
        return self._bag.pop()

    @property
    def current(self):
        return self.kind

    @property
    def step_seconds(self):
        return max(0.075, 0.8 * (0.83 ** min(30, self.level - 1)))

    def _points(self, x=None, y=None, rotation=None, kind=None):
        px = self.x if x is None else x
        py = self.y if y is None else y
        shape = SHAPES[self.kind if kind is None else kind][
            self.rotation if rotation is None else rotation % 4]
        return tuple((px + dx, py + dy) for dx, dy in shape)

    def _fits(self, x=None, y=None, rotation=None):
        for px, py in self._points(x, y, rotation):
            if px < 0 or px >= self.width or py >= self.height:
                return False
            if py >= 0 and self.board[py][px] is not None:
                return False
        return True

    def _spawn(self):
        self.kind = self.next_kind
        self.next_kind = self._draw_kind()
        self.rotation = 0
        self.x, self.y = 3, 0
        if not self._fits():
            self.status, self.reason = "GAME OVER", "STACK REACHED SPAWN"

    def _rotate_piece(self):
        candidate = (self.rotation + 1) % 4
        # Deliberately compact wall kicks, rather than a claim of SRS rules.
        for dx, dy in ((0, 0), (-1, 0), (1, 0), (-2, 0), (2, 0), (0, -1), (0, -2)):
            if self._fits(self.x + dx, self.y + dy, candidate):
                self.x += dx
                self.y += dy
                self.rotation = candidate
                return True
        return False

    @property
    def ghost_y(self):
        landing = self.y
        for _ in range(self.height + 4):
            if not self._fits(y=landing + 1):
                break
            landing += 1
        return landing

    def _lock(self):
        points = self._points()
        if any(y < 0 for _, y in points):
            self.status, self.reason = "GAME OVER", "STACK REACHED CEILING"
            return
        for x, y in points:
            self.board[y][x] = self.kind
        full = tuple(index for index, row in enumerate(self.board) if all(row))
        if full:
            self.score += (0, 100, 300, 500, 800)[len(full)] * self.level
            self.lines += len(full)
            self.level = 1 + self.lines // 10
            self.board = [[None] * self.width for _ in full] + [
                row for index, row in enumerate(self.board) if index not in full]
            self._clear_rows, self._clear_seconds = full, 0.25
        self._elapsed = 0.0
        self._spawn()

    def _fall(self, soft=False):
        if self._fits(y=self.y + 1):
            self.y += 1
            if soft:
                self.score += 1
            return True
        self._lock()
        return False

    def handle(self, action):
        if action == "restart":
            self.restart()
            return
        if action == "start":
            if self.status == "GAME OVER":
                self.restart()
            if self.status in ("READY", "PAUSED"):
                self.status = "PLAYING"
            return
        if action == "pause":
            if self.status == "PLAYING":
                self.status = "PAUSED"
            elif self.status == "PAUSED":
                self.status = "PLAYING"
            return
        if self.status != "PLAYING":
            return
        if action in ("left", "right"):
            dx = -1 if action == "left" else 1
            if self._fits(x=self.x + dx):
                self.x += dx
        elif action in ("rotate", "up"):
            self._rotate_piece()
        elif action in ("down", "soft-drop"):
            self._fall(soft=True)
        elif action in ("drop", "jump"):
            landing = self.ghost_y
            self.score += 2 * (landing - self.y)
            self.y = landing
            self._lock()

    def update(self, dt):
        if self.status != "PLAYING" or not math.isfinite(dt) or dt <= 0:
            return
        elapsed = min(float(dt), 0.5)
        self._clear_seconds = max(0.0, self._clear_seconds - elapsed)
        self._elapsed += elapsed
        for _ in range(8):
            interval = self.step_seconds
            if self.status != "PLAYING" or self._elapsed + 1e-12 < interval:
                break
            self._elapsed -= interval
            if not self._fall():
                break
        self._elapsed = min(self._elapsed, self.step_seconds)

    def snapshot(self):
        cells = []
        for y, row in enumerate(self.board):
            for x, value in enumerate(row):
                if value is not None:
                    cells.append(GameCell(x, y, "#", "accent", True))
        if self.status != "GAME OVER":
            active = set(self._points())
            for x, y in self._points(y=self.ghost_y):
                if 0 <= y < self.height and (x, y) not in active:
                    cells.append(GameCell(x, y, ":", "muted"))
            for x, y in active:
                if 0 <= y < self.height:
                    cells.append(GameCell(x, y, "@", "main", True))
        # A short flash is decorative only; the board has already compacted.
        if self._clear_seconds > 0:
            occupied = {(cell.x, cell.y) for cell in cells}
            for row in self._clear_rows:
                for x in range(self.width):
                    if (x, row) not in occupied:
                        cells.append(GameCell(x, row, "-", "main"))
        preview = set(SHAPES[self.next_kind][0])
        details = ("LINES: %d" % self.lines, "NEXT: %s" % self.next_kind) + tuple(
            "".join("#" if (x, y) in preview else " " for x in range(4)).rstrip()
            for y in range(3)) + (self.reason or "GHOST ':' / SEVEN-BAG RANDOMIZER",)
        return GameFrame(self.width, self.height, tuple(cells), score=self.score,
                         level=self.level, status=self.status, details=details,
                         controls="ENTER start   ARROWS move   UP rotate   SPACE drop   P pause   R restart")
