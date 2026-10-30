"""Deterministic, fixed-step Snake simulation for the local arcade."""

import math
import random

from .core import GameCell, GameFrame


class SnakeGame:
    """A wall-bound snake; input is semantic and snapshots never advance time."""

    width = 28
    height = 18
    _directions = {
        "left": (-1, 0), "right": (1, 0),
        "up": (0, -1), "down": (0, 1),
    }

    def __init__(self, seed=42):
        self.seed = seed
        self.restart()

    def restart(self):
        self._rng = random.Random(self.seed)
        cx, cy = self.width // 2, self.height // 2
        self.body = [(cx, cy), (cx - 1, cy), (cx - 2, cy)]
        self.direction = (1, 0)
        self._pending_direction = self.direction
        self._turn_queued = False
        self._elapsed = 0.0
        self._particles = []
        self.score = 0
        self.level = 1
        self.status = "READY"
        self.reason = ""
        self.food = self._new_food()

    @property
    def step_seconds(self):
        return max(0.055, 0.13 * (0.92 ** min(30, self.level - 1)))

    def _new_food(self):
        occupied = set(self.body)
        free = [(x, y) for y in range(self.height)
                for x in range(self.width) if (x, y) not in occupied]
        return self._rng.choice(free) if free else None

    def handle(self, action):
        if action == "restart":
            self.restart()
            return
        if action == "start":
            if self.status in ("GAME OVER", "WON"):
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
        if self.status != "PLAYING" or action not in self._directions:
            return
        candidate = self._directions[action]
        # At most one turn per simulation step. Two quick keypresses cannot
        # reverse through a pending perpendicular turn into the snake's neck.
        if not self._turn_queued and candidate != self.direction:
            if candidate != (-self.direction[0], -self.direction[1]):
                self._pending_direction = candidate
                self._turn_queued = True

    def _step(self):
        self.direction = self._pending_direction
        self._turn_queued = False
        dx, dy = self.direction
        head = (self.body[0][0] + dx, self.body[0][1] + dy)
        growing = head == self.food
        collision_body = self.body if growing else self.body[:-1]
        if not (0 <= head[0] < self.width and 0 <= head[1] < self.height):
            self.status, self.reason = "GAME OVER", "WALL COLLISION"
            return
        if head in collision_body:
            self.status, self.reason = "GAME OVER", "TAIL COLLISION"
            return
        self.body.insert(0, head)
        if growing:
            self.score += 10
            self.level = 1 + self.score // 50
            self._particles = [(head[0] + ox, head[1] + oy, 0.35)
                               for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1))]
            self.food = self._new_food()
            if self.food is None:
                self.status, self.reason = "WON", "BOARD COMPLETE"
        else:
            self.body.pop()

    def update(self, dt):
        if self.status != "PLAYING" or not math.isfinite(dt) or dt <= 0:
            return
        # A stalled terminal must not simulate seconds of unavoidable deaths.
        elapsed = min(float(dt), 0.5)
        self._particles = [(x, y, life - elapsed)
                           for x, y, life in self._particles if life > elapsed]
        self._elapsed += elapsed
        for _ in range(8):
            interval = self.step_seconds
            if self._elapsed + 1e-12 < interval or self.status != "PLAYING":
                break
            self._elapsed -= interval
            self._step()
        self._elapsed = min(self._elapsed, self.step_seconds)

    def snapshot(self):
        cells = []
        occupied = set(self.body)
        for x, y, _ in self._particles:
            if (0 <= x < self.width and 0 <= y < self.height
                    and (x, y) not in occupied and (x, y) != self.food):
                cells.append(GameCell(x, y, "+", "muted"))
        if self.food is not None:
            cells.append(GameCell(self.food[0], self.food[1], "*", "main", True))
        for index, (x, y) in enumerate(self.body):
            cells.append(GameCell(x, y, "@" if index == 0 else "o",
                                  "main" if index == 0 else "accent", index == 0))
        details = ("LENGTH: %d" % len(self.body),
                   "SPEED: %.1f CELLS/S" % (1.0 / self.step_seconds),
                   self.reason or "WALLS ARE SOLID / NO REVERSE")
        return GameFrame(self.width, self.height, tuple(cells), score=self.score,
                         level=self.level, status=self.status, details=details,
                         controls="ENTER start   ARROWS / WASD move   P pause   R restart")
