"""Code scenes, visual selection and shared options, independent of curses."""

from dataclasses import dataclass

from .modes import DEFAULT_MODE, MODES, MODE_BY_ID
from .simulation import Simulation, clamp
from .sources import SourceProvider
from .protocol import CONSUMER_IDS, protocol_frame
from .visuals import ANIMATIONS


PALETTES = {
    "ember": (209, 214, 196),
    "neon": (46, 51, 213),
    "matrix": (46, 82, 118),
    "ice": (51, 117, 147),
    "amber": (214, 220, 208),
}


@dataclass
class Options:
    fps: int = 60
    consumer: str = "all"
    background: str = "terminal"
    palette: str = "auto"
    scope: bool = True
    auto_cycle: float = 0.0


OPTION_LABELS = (
    ("auto_cycle", "Auto visuals"), ("scope", "Sine scope"), ("fps", "Frame cap"),
    ("consumer", "Consumer"), ("background", "Background"), ("palette", "Palette"),
    ("speed", "Simulation speed"),
)


class Session:
    def __init__(self, mode=DEFAULT_MODE, theme=None, speed=1.0, seed=None, sources=None,
                 background="terminal", animation=None, consumer="all", fps=60, auto_cycle=0, scope=True):
        self.seed = seed
        self.sources = sources if sources is not None else SourceProvider()
        self.mode_id = mode
        self.speed = speed
        self.paused = False
        self.options = Options(fps, consumer, background, theme or "auto", scope, auto_cycle)
        self.running = True
        self.menu_kind = None
        self.menu_focus = "list"
        self.menu_index = next(i for i, item in enumerate(MODES) if item.id == mode)
        self.animation_index = 0
        self.option_index = 0
        self.animation_override = animation
        self.visual_time = self.protocol_time = self.auto_elapsed = 0.0
        self.actual_fps = self.render_ms = 0.0
        self.frames = 0
        self.terminal_visible = self.terminal_focused = False
        self.terminal_frame = None
        self.simulations = {}
        self.activate(mode)

    @property
    def sim(self):
        return self.simulations[self.mode_id]

    @property
    def theme(self):
        return self.sim.mode.palette if self.options.palette == "auto" else self.options.palette

    @property
    def background(self):
        return self.options.background

    @property
    def animation(self):
        return self.animation_override or self.sim.mode.geometry

    @property
    def protocol(self):
        return protocol_frame(self.protocol_time, self.options.consumer)

    @property
    def menu_open(self):
        return self.menu_kind is not None

    @property
    def menu_entries(self):
        return ANIMATIONS if self.menu_kind == "animations" else MODES

    @property
    def picker_index(self):
        return self.animation_index if self.menu_kind == "animations" else self.menu_index

    def open_menu(self, kind):
        self.terminal_focused = False
        self.menu_kind, self.menu_focus = kind, "list"
        if kind == "animations":
            self.animation_index = next(i for i, item in enumerate(ANIMATIONS) if item.id == self.animation)
        else:
            self.menu_index = next(i for i, item in enumerate(MODES) if item.id == self.mode_id)

    def choose_animation(self, animation):
        self.animation_override = animation
        self.auto_elapsed = 0.0

    def next_animation(self):
        index = next(i for i, item in enumerate(ANIMATIONS) if item.id == self.animation)
        self.animation_override = ANIMATIONS[(index + 1) % len(ANIMATIONS)].id

    def cycle_option(self, key):
        choices = {
            "auto_cycle": (0.0, 8.0, 15.0, 30.0), "scope": (True, False),
            "fps": (30, 60, 90, 120), "consumer": CONSUMER_IDS,
            "background": ("terminal", "black"), "palette": ("auto", *PALETTES),
            "speed": (.5, 1.0, 1.5, 2.0, 4.0),
        }[key]
        current = self.speed if key == "speed" else getattr(self.options, key)
        index = choices.index(current) if current in choices else -1
        value = choices[(index + 1) % len(choices)]
        if key == "speed":
            self.speed = value
        else:
            setattr(self.options, key, value)
        if key == "consumer":
            self.protocol_time = 0.0
        elif key == "auto_cycle":
            self.auto_elapsed = 0.0

    def option_text(self, key):
        value = self.speed if key == "speed" else getattr(self.options, key)
        if key == "auto_cycle":
            return "OFF" if not value else "{:g}s".format(value)
        if key == "scope":
            return "ON" if value else "OFF"
        if key == "fps":
            return "{} FPS".format(value)
        if key == "speed":
            return "{:g}x".format(value)
        return value.upper()

    def activate(self, mode_id):
        if mode_id not in self.simulations:
            self.simulations[mode_id] = Simulation(MODE_BY_ID[mode_id], self.seed, sources=self.sources)
        self.mode_id = mode_id

    def handle(self, action):
        if action == "terminal":
            self.menu_kind = None
            self.terminal_focused = not self.terminal_focused
            self.terminal_visible = True
            return
        if action == "source":
            self.terminal_visible = not self.terminal_visible
            self.terminal_focused = False
            return
        if action == "quit":
            self.running = False
            return
        if self.menu_open:
            if action == "left":
                self.menu_focus = "list"
            elif action == "right":
                self.menu_focus = "options"
            elif action == "tab" and self.menu_kind == "animations":
                self.open_menu("modes")
            elif action == "animation" and self.menu_kind == "modes":
                self.open_menu("animations")
            elif action in ("up", "down", "tab", "animation"):
                direction = -1 if action == "up" else 1
                if self.menu_focus == "options" and action in ("up", "down"):
                    self.option_index = (self.option_index + direction) % len(OPTION_LABELS)
                elif self.menu_kind == "animations":
                    self.animation_index = (self.animation_index + direction) % len(ANIMATIONS)
                else:
                    self.menu_index = (self.menu_index + direction) % len(MODES)
            elif action in ("accept", "pause"):
                if self.menu_focus == "options":
                    self.cycle_option(OPTION_LABELS[self.option_index][0])
                else:
                    item = self.menu_entries[self.picker_index]
                    if self.menu_kind == "animations":
                        self.choose_animation(item.id)
                    else:
                        self.activate(item.id)
                    self.menu_kind = None
            elif action == "escape":
                self.menu_kind = None
            return
        if action == "escape":
            self.running = False
        elif action == "tab":
            self.open_menu("modes")
        elif action == "animation":
            self.open_menu("animations")
        elif action == "pause":
            self.paused = not self.paused
        elif action == "faster":
            self.speed = clamp(self.speed * 1.25, 0.25, 8.0)
        elif action == "slower":
            self.speed = clamp(self.speed / 1.25, 0.25, 8.0)
        elif action == "theme":
            names = list(PALETTES)
            self.options.palette = names[(names.index(self.theme) + 1) % len(names)]
        elif action == "next":
            self.sim.load_file()
        elif action == "reload":
            self.sim.load_file(advance=False)
        elif action == "background":
            self.cycle_option("background")
        elif action == "consumer":
            self.cycle_option("consumer")
        elif action == "scope":
            self.cycle_option("scope")
        elif action == "auto":
            self.cycle_option("auto_cycle")

    def update(self, dt):
        if not self.paused and not self.menu_open:
            self.sim.update(dt * self.speed)
            self.visual_time += dt * self.speed
            self.protocol_time += dt * self.speed
            if self.options.auto_cycle:
                self.auto_elapsed += dt
                while self.auto_elapsed >= self.options.auto_cycle:
                    self.auto_elapsed -= self.options.auto_cycle
                    self.next_animation()
