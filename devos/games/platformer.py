"""A small original side-scroller with deterministic, terminal-friendly physics.

Input is a short movement impulse rather than a held key: terminals do not send
key-release events.  The simulation owns its clock; snapshots only draw it.
"""

from dataclasses import dataclass
import math

from .core import GameCell, GameFrame


@dataclass
class _Enemy:
    x: float
    left: float
    right: float
    direction: int = 1
    alive: bool = True


class PlatformerGame:
    """Guide the little courier to the flag in the handcrafted Byte Valley."""

    WIDTH = 40
    HEIGHT = 18
    WORLD_WIDTH = 176
    PLAYER_WIDTH = 0.7
    PLAYER_HEIGHT = 0.9
    STEP = 1.0 / 60.0
    GRAVITY = 30.0
    SPEED = 7.0
    JUMP_SPEED = 12.8
    INPUT_SECONDS = 0.22
    GOAL_X = 169
    CHECKPOINTS = (49, 99, 139)
    SPRINGS = (61, 140)
    CONTROLS = "LEFT/RIGHT move | UP/SPACE jump | ENTER start | P pause | R restart"

    def __init__(self, seed=42):
        self.seed = seed
        self.restart()

    def restart(self):
        self.tiles = {}
        pits = set(range(32, 35)) | set(range(67, 71))
        pits |= set(range(113, 117)) | set(range(149, 153))
        for x in range(self.WORLD_WIDTH):
            if x not in pits:
                self.tiles[(x, 16)] = "="
                self.tiles[(x, 17)] = "#"
        # Shelves form optional high routes.  Two-cell steps and four-cell pits
        # are all within the ordinary jump's 2.7-cell rise / 6-cell range.
        for left, right, y in ((10, 15, 14), (16, 20, 12), (26, 30, 14),
                               (53, 58, 14), (63, 67, 11), (76, 81, 14),
                               (88, 93, 14), (95, 101, 12), (106, 111, 14),
                               (121, 126, 14), (143, 148, 12), (155, 160, 14)):
            for x in range(left, right):
                self.tiles[(x, y)] = "="
        for left, right, top in ((22, 24, 14), (84, 86, 14), (132, 134, 14)):
            for x in range(left, right):
                for y in range(top, 16):
                    self.tiles[(x, y)] = "+" if y == top else "|"
        self.coins = set()
        for left, right, y in ((5, 9, 14), (11, 15, 12), (17, 20, 10),
                               (28, 31, 12), (36, 40, 14), (54, 58, 12),
                               (63, 67, 9), (73, 77, 14), (89, 93, 12),
                               (96, 101, 10), (108, 111, 12), (119, 122, 14),
                               (124, 127, 12), (144, 148, 10),
                               (156, 160, 12), (163, 168, 14)):
            self.coins.update((x, y) for x in range(left, right))
        self.total_coins = len(self.coins)
        self.enemies = [_Enemy(42.0, 40.0, 46.0), _Enemy(78.0, 76.0, 82.0),
                        _Enemy(128.0, 127.0, 131.0), _Enemy(160.0, 159.0, 166.0)]
        self.x = 3.0
        self.y = 16.0 - self.PLAYER_HEIGHT
        self.vx = self.vy = 0.0
        self.grounded = True
        self.score = 0
        self.lives = 3
        self.status = "READY"
        self.elapsed = 0.0
        self.checkpoint = 3
        self._direction = 0
        self._input_timer = 0.0
        self._jump_buffer = 0.0
        self._coyote = 0.08
        self._invulnerable = 0.0
        self._accumulator = 0.0

    @property
    def camera_x(self):
        return max(0, min(self.WORLD_WIDTH - self.WIDTH, int(self.x) - 13))

    def handle(self, action):
        if action == "restart":
            self.restart()
            return
        if action == "pause" and self.status in ("PLAYING", "PAUSED"):
            self.status = "PAUSED" if self.status == "PLAYING" else "PLAYING"
            self._accumulator = 0.0
            # A direction pressed before a pause must not unexpectedly resume.
            self._input_timer = 0.0
            self._direction = 0
            self.vx = 0.0
            return
        if action == "start":
            if self.status in ("GAME OVER", "WON"):
                self.restart()
            if self.status in ("READY", "PAUSED"):
                self.status = "PLAYING"
            return
        if self.status != "PLAYING":
            return
        if action in ("left", "right"):
            self._direction = -1 if action == "left" else 1
            self._input_timer = self.INPUT_SECONDS
        elif action in ("up", "jump"):
            self._jump_buffer = 0.12

    def update(self, dt):
        if self.status != "PLAYING":
            return
        try:
            dt = float(dt)
        except (TypeError, ValueError, OverflowError):
            return
        if not math.isfinite(dt) or dt <= 0.0:
            return
        self._accumulator += min(dt, 0.2)
        steps = min(12, int((self._accumulator + 1e-12) / self.STEP))
        for _ in range(steps):
            self._accumulator = max(0.0, self._accumulator - self.STEP)
            self._step(self.STEP)
            if self.status != "PLAYING":
                self._accumulator = 0.0
                break

    def _step(self, dt):
        self.elapsed += dt
        self._invulnerable = max(0.0, self._invulnerable - dt)
        self._input_timer = max(0.0, self._input_timer - dt)
        self._coyote = 0.08 if self.grounded else max(0.0, self._coyote - dt)
        target = self.SPEED * self._direction if self._input_timer > 0 else 0.0
        acceleration = 60.0 if target else 45.0
        delta = target - self.vx
        self.vx += max(-acceleration * dt, min(acceleration * dt, delta))
        if self._jump_buffer > 0.0 and self._coyote > 0.0:
            self.vy = -self.JUMP_SPEED
            self.grounded = False
            self._coyote = 0.0
            self._jump_buffer = 0.0
        else:
            self._jump_buffer = max(0.0, self._jump_buffer - dt)

        previous_feet = self.y + self.PLAYER_HEIGHT
        self._move_horizontal(self.vx * dt)
        self.vy = min(22.0, self.vy + self.GRAVITY * dt)
        self._move_vertical(self.vy * dt)

        for enemy in self.enemies:
            if not enemy.alive:
                continue
            enemy.x += enemy.direction * 1.2 * dt
            if enemy.x >= enemy.right:
                enemy.x = enemy.right
                enemy.direction = -1
            elif enemy.x <= enemy.left:
                enemy.x = enemy.left
                enemy.direction = 1
            if self._overlap(enemy.x, 15.15, 0.8, 0.85):
                if self.vy > 0.0 and previous_feet <= 15.45:
                    enemy.alive = False
                    self.score += 150
                    self.y = 15.15 - self.PLAYER_HEIGHT
                    self.vy = -8.2
                    self.grounded = False
                elif self._invulnerable <= 0.0:
                    self._lose_life()
                    return

        for x, y in tuple(self.coins):
            if self._overlap(x + 0.2, y + 0.2, 0.6, 0.6):
                self.coins.remove((x, y))
                self.score += 100
        for checkpoint in self.CHECKPOINTS:
            if self.x >= checkpoint and self.checkpoint < checkpoint:
                self.checkpoint = checkpoint
                self.score += 250
        if self.grounded and abs(self.y + self.PLAYER_HEIGHT - 16.0) < 1e-7:
            for spring in self.SPRINGS:
                if abs(self.x + self.PLAYER_WIDTH * 0.5 - (spring + 0.5)) < 0.65:
                    self.vy = -17.5
                    self.grounded = False
                    self._coyote = 0.0
                    break
        if self.y > self.HEIGHT + 2:
            self._lose_life()
        elif self.x >= self.GOAL_X and self.y < 16:
            self.status = "WON"
            self.score += 1000
            self.vx = self.vy = 0.0

    def _overlap(self, x, y, width, height):
        return (self.x < x + width and self.x + self.PLAYER_WIDTH > x
                and self.y < y + height and self.y + self.PLAYER_HEIGHT > y)

    def _colliding_tiles(self):
        epsilon = 1e-8
        for y in range(math.floor(self.y), math.floor(self.y + self.PLAYER_HEIGHT - epsilon) + 1):
            for x in range(math.floor(self.x), math.floor(self.x + self.PLAYER_WIDTH - epsilon) + 1):
                if (x, y) in self.tiles:
                    yield x, y

    def _move_horizontal(self, amount):
        self.x = max(0.0, min(self.WORLD_WIDTH - self.PLAYER_WIDTH, self.x + amount))
        collisions = tuple(self._colliding_tiles())
        if collisions:
            if amount > 0:
                self.x = min(x for x, _ in collisions) - self.PLAYER_WIDTH
            elif amount < 0:
                self.x = max(x for x, _ in collisions) + 1.0
            self.vx = 0.0

    def _move_vertical(self, amount):
        self.y += amount
        self.grounded = False
        collisions = tuple(self._colliding_tiles())
        if collisions:
            if amount > 0:
                self.y = min(y for _, y in collisions) - self.PLAYER_HEIGHT
                self.grounded = True
            elif amount < 0:
                self.y = max(y for _, y in collisions) + 1.0
            self.vy = 0.0

    def _lose_life(self):
        self.lives -= 1
        if self.lives <= 0:
            self.status = "GAME OVER"
        self.x = float(self.checkpoint)
        self.y = 16.0 - self.PLAYER_HEIGHT
        self.vx = self.vy = 0.0
        self.grounded = True
        self._direction = 0
        self._input_timer = self._jump_buffer = 0.0
        self._coyote = 0.08
        self._invulnerable = 1.5

    def snapshot(self):
        cells = {}
        camera = self.camera_x

        def put(x, y, char, tone="main", bold=False):
            if 0 <= x < self.WIDTH and 0 <= y < self.HEIGHT and char != " ":
                cells[(x, y)] = GameCell(x, y, char, tone, bold)

        # A slow independent cloud layer creates depth while the world scrolls.
        cloud_offset = int(camera * 0.18 + self.elapsed * 0.35)
        for origin, row in ((4, 2), (24, 4), (49, 1), (73, 3)):
            base = (origin - cloud_offset) % 82 - 8
            for dy, text in enumerate((" .--. ", "(____)")):
                for dx, char in enumerate(text):
                    put(base + dx, row + dy, char, "muted")
        for wx in range(camera, min(camera + self.WIDTH, self.WORLD_WIDTH)):
            # Low, subdued hills stay behind real traversable geometry.
            hill_y = 13 - int(2 * math.sin((wx - camera * 0.5) * 0.17))
            put(wx - camera, hill_y, ".", "muted")
        for (wx, y), char in self.tiles.items():
            put(wx - camera, y, char, "main" if y < 17 else "muted")
        for wx, y in self.coins:
            char = "*" if int(self.elapsed * 5) % 4 != 2 else ":"
            put(wx - camera, y, char, "accent", True)
        for wx in self.SPRINGS:
            put(wx - camera, 15, "^", "accent", True)
        for wx in self.CHECKPOINTS:
            put(wx - camera, 14, "!" if wx <= self.checkpoint else "o", "accent")
            put(wx - camera, 15, "|", "muted")
        for y in range(9, 16):
            put(self.GOAL_X + 1 - camera, y, "|", "accent")
        for dx, char in enumerate(">>>"):
            put(self.GOAL_X + 2 + dx - camera, 9, char, "hot", True)
        for enemy in self.enemies:
            if enemy.alive:
                put(int(enemy.x) - camera, 15, "m", "hot", True)
        # Keep the character visible during invulnerability; only its tone pulses.
        player_tone = "accent" if self._invulnerable and int(self.elapsed * 12) % 2 else "hot"
        put(int(self.x + self.PLAYER_WIDTH * 0.5) - camera,
            int(self.y + self.PLAYER_HEIGHT * 0.5), "@", player_tone, True)
        return GameFrame(
            width=self.WIDTH, height=self.HEIGHT,
            cells=tuple(cells[key] for key in sorted(cells, key=lambda p: (p[1], p[0]))),
            score=self.score, level=1, status=self.status, lives=self.lives,
            details=("BYTE VALLEY | original ASCII side-scroller",
                     "DIST %03d/%03d | COINS %d/%d" % (
                         min(self.GOAL_X, max(0, int(self.x))), self.GOAL_X,
                         self.total_coins - len(self.coins), self.total_coins),
                     "CHECKPOINT %03d | TIME %05.1fs | ^ spring" % (self.checkpoint, self.elapsed)),
            controls=self.CONTROLS)
