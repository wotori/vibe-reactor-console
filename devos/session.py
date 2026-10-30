"""Code scenes, visual selection and shared options, independent of curses."""

from dataclasses import dataclass
from collections import deque
from types import MappingProxyType
import math

from .modes import DEFAULT_MODE, MODES, MODE_BY_ID
from .simulation import Simulation, clamp
from .sources import SourceProvider
from .protocol import CONSUMER_IDS, protocol_frame
from .visuals import ANIMATIONS
from .screen_registry import SCREENS, SCREEN_BY_ID
from .telemetry import TelemetrySnapshot


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
                 background="terminal", animation=None, consumer="all", fps=60, auto_cycle=0, scope=True,
                 screen="cockpit", intensity="calm", lab_binding="online", data_mode="auto", arena_mode="demo"):
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
        self.screen_id = screen
        self.focused_panel = "preview" if screen == "visual-lab" else SCREEN_BY_ID[screen].panels[0]
        self.zoomed = False
        self.lab_binding, self.intensity = lab_binding, intensity
        self.data_mode, self.arena_mode = data_mode, arena_mode
        self.telemetry = TelemetrySnapshot()
        self.arena = self.replay_status = self.recording_status = self.task_snapshot = None
        self.terminal_slots, self.terminal_slot = (), "left"
        self.atlas_view, self.status_message = "assets", ""
        self.stream_view = self.palette_open = self.help_open = False
        self.composer_open, self.composer_text = False, ""
        self.inspector_kind, self.report_scroll = None, 0
        self.palette_query, self.palette_index = "", 0
        self.pending_actions = deque()
        self._last_event, self._event_time = None, None
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
        return (self.menu_kind is not None or self.palette_open or self.help_open or self.composer_open
                or self.inspector_kind is not None)

    @property
    def palette_commands(self):
        from .commands import search
        return search(self.palette_query)

    def choose_screen(self, screen):
        self.screen_id = screen
        self.focused_panel = "preview" if screen == "visual-lab" else SCREEN_BY_ID[screen].panels[0]
        self.zoomed = self.terminal_focused = False
        if screen == "arena":
            self.focused_panel = "terminal-" + self.terminal_slot

    def publish_telemetry(self, snapshot):
        self.telemetry = snapshot
        cue = next((item for item in reversed(snapshot.events) if item.topic in (
            "visual.cue", "agent.handoff.ready", "build.finished", "test.finished", "task.test.completed", "demo.build.finished")), None)
        if cue and cue.id != self._last_event:
            self._last_event = cue.id
            self._event_time = (cue.recorded_offset_ms / 1000.0 if cue.mode == "replay"
                                and cue.recorded_offset_ms is not None else self.visual_time)
        elif cue is None:
            self._last_event, self._event_time = None, None

    def visual_parameters(self):
        def value(key, fallback=0):
            item = self.telemetry.metric(key)
            return item.value if item and item.value is not None and item.quality in ("ok", "stale") else fallback
        online = value("site.active_sessions_60s", None)
        bpm = 0 if self.stream_view else value("body.heart_rate", 0)
        energy = {"calm": .2, "cinematic": .55, "showcase": .9}[self.intensity]
        if self.lab_binding == "online":
            energy = min(1.0, math.log1p(max(0, online)) / 8) if online is not None else energy
        elif self.lab_binding == "pulse" and bpm:
            energy = min(1., max(0., (bpm - 40) / 140))
        elif self.lab_binding == "queue" and self.arena:
            energy = min(1., sum(item.status in ("ready", "pending", "delivering", "delivered") for item in self.arena.handoffs) / 10)
        parameters = {"energy": energy, "particle_count": int(25 + energy * 160),
                      "event_sequence": len(self.telemetry.events),
                      "burst_age": max(0., self.visual_time - self._event_time) if self._event_time is not None else 1e9,
                      "seed": self.seed if self.seed is not None else 42,
                      "queue_left": 0, "queue_right": 0}
        if bpm and self.lab_binding == "pulse":
            parameters["pulse_bpm"] = bpm
        if self.lab_binding == "queue" and self.arena:
            for side, recipient in (("left", "codex"), ("right", "claude")):
                parameters["queue_" + side] = sum(item.recipient == recipient and item.status in (
                    "ready", "pending", "delivering", "delivered") for item in self.arena.handoffs)
        if self.lab_binding == "tests":
            event = next((item for item in reversed(self.telemetry.events) if item.topic in (
                "demo.build.finished", "build.finished", "test.finished", "task.test.completed")), None)
            if event:
                passed, total = event.payload.get("tests_passed"), event.payload.get("tests_total")
                if type(passed) is int and type(total) is int and 0 <= passed <= total and total > 0:
                    parameters.update(test_passed=passed, test_total=total, energy=passed / total)
        return MappingProxyType(parameters)

    def apply_preferences(self, document):
        from .presets import validate
        values = validate(document)
        if "scene" in values:
            self.activate(values["scene"])
        if "screen" in values:
            self.choose_screen(values["screen"])
        if "visual" in values:
            self.choose_animation(values["visual"])
        for key, attribute in (("theme", "palette"), ("fps_cap", "fps"), ("background", "background"),
                               ("scope", "scope"), ("auto_cycle", "auto_cycle")):
            if key in values:
                setattr(self.options, attribute, values[key])
        for key in ("speed", "intensity", "lab_binding", "seed"):
            if key in values:
                setattr(self, key, values[key])
        for key, action in (("data_mode", "data-mode"), ("arena_mode", "arena-mode")):
            if key in values and values[key] != getattr(self, key):
                setattr(self, key, values[key])
                self.pending_actions.append(action)

    def execute_command(self, command):
        self.palette_open = False
        kind, value = command.kind, command.value
        if kind == "screen":
            self.choose_screen(value)
        elif kind == "visual":
            self.choose_animation(value)
        elif kind == "scene":
            self.activate(value)
        elif kind == "preset":
            from .presets import resolve
            self.apply_preferences(resolve(value))
        elif kind in ("binding", "intensity"):
            setattr(self, "lab_binding" if kind == "binding" else "intensity", value)
        elif kind in ("data", "arena"):
            setattr(self, kind + "_mode", value)
            self.pending_actions.append(kind + "-mode")
        elif kind == "action":
            self.handle(value)

    def palette_key(self, key):
        if key == 27:
            self.palette_open = False
        elif key in (10, 13):
            commands = self.palette_commands
            if commands:
                self.execute_command(commands[self.palette_index % len(commands)])
        elif key in (8, 127, 263):
            self.palette_query = self.palette_query[:-1]
            self.palette_index = 0
        elif 32 <= key < 127:
            self.palette_query += chr(key)
            self.palette_index = 0

    def composer_key(self, key):
        if key == 27:
            self.composer_open = False
        elif key in (10, 13):
            self.composer_open = False
            self.pending_actions.append("handoff-send")
        elif key in (8, 127, 263):
            self.composer_text = self.composer_text[:-1]
        elif key == 21:
            self.composer_text = ""
        elif 32 <= key < 127 and len(self.composer_text) < 12000:
            self.composer_text += chr(key)

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
        if action.startswith("screen:"):
            self.choose_screen(action.split(":", 1)[1])
            return
        if action == "palette":
            self.palette_open = not self.palette_open
            self.help_open = False
            self.menu_kind = None
            self.terminal_focused = False
            self.palette_query, self.palette_index = "", 0
            return
        if self.help_open:
            self.help_open = False
            return
        if self.palette_open:
            if action in ("up", "down"):
                self.palette_index = (self.palette_index + (-1 if action == "up" else 1)) % max(1, len(self.palette_commands))
            elif action == "escape":
                self.palette_open = False
            return
        if action == "terminal-right":
            self.terminal_slot = "right" if self.terminal_slot == "left" else "left"
            self.focused_panel = "terminal-" + self.terminal_slot if self.screen_id == "arena" else "source"
            self.terminal_focused = False
            self.handle("terminal")
            return
        if action == "terminal":
            if self.screen_id == "replay":
                self.terminal_focused = False
                self.status_message = "Replay is read-only; switch screens for a live terminal."
                return
            if self.screen_id not in ("cockpit", "arena"):
                self.choose_screen("cockpit")
            self.menu_kind = None
            self.terminal_focused = not self.terminal_focused
            self.terminal_visible = True
            if self.terminal_focused:
                self.focused_panel = "source" if self.screen_id == "cockpit" else "terminal-" + self.terminal_slot
                self.pending_actions.append("terminal-start")
            return
        if action == "source":
            self.terminal_visible = not self.terminal_visible
            self.terminal_focused = False
            return
        if action == "quit":
            self.running = False
            return
        if action == "help":
            self.help_open = True
            return
        if action in ("inspect-task", "inspect-mission"):
            self.inspector_kind = action.split("-", 1)[1]
            self.report_scroll = 0
            return
        if action == "handoff" and self.arena and self.arena.mode in ("relay", "mission"):
            sender = "codex" if self.terminal_slot == "left" else "claude"
            card = next((item for item in self.arena.cards if item.id == sender), None)
            if card and card.status == "SUCCEEDED":
                self.composer_text = card.summary
                self.composer_open = True
            else:
                self.status_message = "Wait for a completed public reply before handing it off."
            return
        if action in ("panel", "panel-prev"):
            panels = SCREEN_BY_ID[self.screen_id].panels
            self.focused_panel = panels[(panels.index(self.focused_panel) + (-1 if action == "panel-prev" else 1)) % len(panels)]
            if self.focused_panel.startswith("terminal-"):
                self.terminal_slot = self.focused_panel.split("-", 1)[1]
            return
        if action == "zoom":
            self.zoomed = not self.zoomed
            return
        if action == "binding":
            values = ("online", "pulse", "tests", "queue")
            self.lab_binding = values[(values.index(self.lab_binding) + 1) % len(values)]
            return
        if action == "intensity":
            values = ("calm", "cinematic", "showcase")
            self.intensity = values[(values.index(self.intensity) + 1) % len(values)]
            return
        if action == "atlas":
            self.atlas_view = "repository" if self.atlas_view == "assets" else "assets"
            return
        if action == "stream":
            self.stream_view = not self.stream_view
            self.terminal_focused = False
            return
        if action in ("save", "record", "event", "handoff", "task", "mission-pause", "mission-stop", "mission-validate",
                      "replay-play", "replay-next", "replay-back", "replay-forward", "arena-mode"):
            self.pending_actions.append(action)
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
            if self.zoomed:
                self.zoomed = False
            else:
                self.running = False
        elif action == "tab":
            self.open_menu("modes")
        elif action == "animation":
            self.open_menu("animations")
        elif action == "pause":
            if self.screen_id == "replay" and self.replay_status:
                self.pending_actions.append("replay-play")
            else:
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
