"""Code scenes, visual selection and shared options, independent of curses."""

from dataclasses import dataclass, replace
from collections import deque
import codecs
from types import MappingProxyType

from .modes import DEFAULT_MODE, MODES, MODE_BY_ID
from .simulation import Simulation, clamp
from .sources import SourceProvider
from .protocol import CONSUMER_IDS, protocol_frame
from .visuals import ANIMATIONS
from .screen_registry import SCREENS, SCREEN_BY_ID
from .telemetry import TelemetrySnapshot
from .bindings import BindingConfig, BindingEngine, BINDING_BY_ID
from .director import EventDirector
from .math_scope import SCOPES, SCOPE_BY_ID
from .games import GAME_SPECS, GAME_BY_ID, create_game


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
    scope_mode: str = "harmonics"
    scope_auto_cycle: float = 0.0


OPTION_LABELS = (
    ("auto_cycle", "Auto visuals"), ("scope", "Math scope"), ("fps", "Frame cap"),
    ("consumer", "Consumer"), ("background", "Background"), ("palette", "Palette"),
    ("speed", "Simulation speed"),
    ("scope_mode", "Scope function"), ("scope_auto_cycle", "Scope auto"),
)


class Session:
    def __init__(self, mode=DEFAULT_MODE, theme=None, speed=1.0, seed=None, sources=None,
                 background="terminal", animation=None, consumer="all", fps=60, auto_cycle=0, scope=True,
                 screen="cockpit", intensity="calm", lab_binding="online", data_mode="auto", arena_mode="demo",
                 binding_config=None, director_enabled=False, visual_time=0, protocol_time=0, paused=False,
                 scope_mode="harmonics", scope_auto_cycle=0):
        self.seed = seed
        self.sources = sources if sources is not None else SourceProvider()
        self.mode_id = mode
        self.speed = speed
        self.paused = paused
        self.options = Options(fps, consumer, background, theme or "auto", scope, auto_cycle,
                               scope_mode, scope_auto_cycle)
        self.running = True
        self.quit_confirm = self.quit_selected = False
        self.menu_kind = None
        self.menu_focus = "list"
        self.menu_index = next(i for i, item in enumerate(MODES) if item.id == mode)
        self.animation_index = 0
        self.option_index = 0
        self.animation_override = animation
        self.visual_time, self.protocol_time, self.auto_elapsed = visual_time, protocol_time, 0.0
        self.scope_elapsed = 0.0
        self.actual_fps = self.render_ms = 0.0
        self.frames = 0
        self.terminal_visible = self.terminal_focused = False
        self.terminal_frame = None
        self.screen_id = screen
        self.focused_panel = "preview" if screen == "visual-lab" else SCREEN_BY_ID[screen].panels[0]
        self.zoomed = False
        self.fullscreen_panel = None
        self._fullscreen_restore = None
        self.arcade_choice = 0
        self.arcade_game_id = None
        self.arcade_games = {}
        self.arcade_viewport_ready = True
        self.lab_binding, self.intensity = lab_binding, intensity
        self.data_mode, self.arena_mode = data_mode, arena_mode
        self.telemetry = TelemetrySnapshot()
        self.arena = self.replay_status = self.recording_status = self.task_snapshot = None
        self.terminal_slots, self.terminal_slot = (), "left"
        self.atlas_view, self.status_message = "assets", ""
        self.stream_view = self.palette_open = self.help_open = False
        self.composer_open, self.composer_text = False, ""
        self.project_editor, self.project_text, self.project_error = False, "", ""
        self.settings_enabled = True
        self._project_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.inspector_kind, self.report_scroll = None, 0
        self.palette_query, self.palette_index = "", 0
        self.pending_actions = deque()
        self.binding_config = BindingConfig.from_dict(binding_config) if binding_config else None
        self.binding_engine = BindingEngine()
        self.binding_frame = None
        self.binding_editor = False
        self.binding_row = 0
        self.director_enabled = director_enabled
        self.director = EventDirector()
        self.manual_visual_until = 0
        self.director_frame = None
        self.atlas_index = 0
        self.atlas_inspection = self.asset_catalog = None
        self.asset_manifest_expected = False
        self.replay_arena = self.replay_presentation = None
        self.replay_bookmarks = ()
        self.replay_stage = "visual"
        self.site_index = 0
        self.prepared_context = ""
        self._cue_timeline = None
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
    def effective_scope_mode(self):
        return self.options.scope_mode

    def choose_scope_mode(self, mode):
        if mode not in SCOPE_BY_ID:
            raise ValueError("Unknown scope function: " + str(mode))
        self.options.scope_mode = mode
        self.options.scope = True
        self.scope_elapsed = 0.0

    def next_scope(self):
        ids = tuple(item.id for item in SCOPES)
        self.choose_scope_mode(ids[(ids.index(self.effective_scope_mode) + 1) % len(ids)])

    def exit_fullscreen(self):
        if self.fullscreen_panel is not None:
            if self._fullscreen_restore is not None:
                self.focused_panel, self.zoomed = self._fullscreen_restore
            else:
                self.zoomed = False
            self.fullscreen_panel = self._fullscreen_restore = None

    @property
    def arcade_game(self):
        return self.arcade_games.get(self.arcade_game_id)

    def arcade_select(self, identifier):
        if identifier not in GAME_BY_ID:
            raise ValueError("Unknown arcade game: " + str(identifier))
        self.choose_screen("arcade")
        if identifier not in self.arcade_games:
            self.arcade_games[identifier] = create_game(identifier, 42 if self.seed is None else self.seed)
        self.arcade_game_id = identifier
        self.arcade_choice = next(index for index, spec in enumerate(GAME_SPECS) if spec.id == identifier)

    def arcade_key(self, key):
        """Arcade owns its letters before dashboard bindings can consume them."""
        from .terminal import curses
        if key in (ord("q"), ord("Q"), 3):
            self.request_quit()
            return
        if key == ord("?"):
            self.help_open = True
            return
        if key == ord("!"):
            self.handle("mission-stop")
            return
        if key == ord("/"):
            self.handle("palette")
            return
        if ord("1") <= key <= ord("8"):
            self.choose_screen(SCREENS[key - ord("1")].id)
            return
        if key == 27:
            if self.arcade_game is not None:
                self.arcade_game_id = None
            else:
                self.choose_screen("cockpit")
            return
        directions = {ord("a"): "left", ord("A"): "left", ord("d"): "right", ord("D"): "right",
                      ord("w"): "up", ord("W"): "up", ord("s"): "down", ord("S"): "down"}
        if curses is not None:
            directions.update({curses.KEY_LEFT: "left", curses.KEY_RIGHT: "right",
                               curses.KEY_UP: "up", curses.KEY_DOWN: "down"})
        enter = key in (10, 13) or curses is not None and key == curses.KEY_ENTER
        if self.arcade_game is None:
            direction = directions.get(key)
            if key in (ord("p"), ord("P")):
                self.handle("palette")
            elif enter or key == ord(" "):
                self.arcade_select(GAME_SPECS[self.arcade_choice].id)
            elif direction in ("left", "up") or key in (ord("k"), ord("K")):
                self.arcade_choice = (self.arcade_choice - 1) % len(GAME_SPECS)
            elif direction in ("right", "down") or key in (9, ord("j"), ord("J")):
                self.arcade_choice = (self.arcade_choice + 1) % len(GAME_SPECS)
            return
        if not self.arcade_viewport_ready:
            return
        action = directions.get(key)
        if enter:
            action = "start"
        elif key in (ord("p"), ord("P")):
            action = "pause"
        elif key in (ord("r"), ord("R")):
            action = "restart"
        elif key == ord(" "):
            action = {"snake": "pause", "blocks": "drop", "runner": "jump"}[self.arcade_game_id]
        if action:
            self.arcade_game.handle(action)

    def toggle_fullscreen(self, panel):
        if panel not in ("scope", "visual"):
            raise ValueError("Unknown fullscreen panel: " + str(panel))
        if self.fullscreen_panel == panel:
            self.exit_fullscreen()
            return
        if self.fullscreen_panel is None:
            self._fullscreen_restore = (self.focused_panel, self.zoomed)
        self.fullscreen_panel = panel
        self.zoomed = True
        self.terminal_focused = False
        if self.screen_id == "cockpit":
            self.focused_panel = panel

    @property
    def protocol(self):
        return protocol_frame(self.protocol_time, self.options.consumer)

    @property
    def menu_open(self):
        return (self.menu_kind is not None or self.palette_open or self.help_open or self.composer_open
                or self.inspector_kind is not None or self.binding_editor or self.project_editor or self.quit_confirm)

    def request_quit(self):
        """Ask before closing any worker or terminal; Stay is always the default."""
        if not self.quit_confirm:
            self.quit_selected = False
            self.quit_confirm = True

    def quit_action(self, action):
        if action in ("left", "up"):
            self.quit_selected = False
        elif action in ("right", "down"):
            self.quit_selected = True
        elif action == "tab":
            self.quit_selected = not self.quit_selected
        elif action == "escape":
            self.quit_confirm = False
            self.quit_selected = False
        elif action == "accept":
            if self.quit_selected:
                self.running = False
            self.quit_confirm = False
            self.quit_selected = False

    def quit_key(self, key):
        from .terminal import curses
        if key in (ord("y"), ord("Y")):
            self.quit_selected = True
            self.quit_action("accept")
        elif key in (ord("n"), ord("N"), 27):
            self.quit_action("escape")
        elif key in (10, 13):
            self.quit_action("accept")
        elif key == 9:
            self.quit_action("tab")
        elif curses is not None:
            action = {curses.KEY_LEFT: "left", curses.KEY_UP: "up", curses.KEY_RIGHT: "right",
                      curses.KEY_DOWN: "down", curses.KEY_BTAB: "tab", curses.KEY_ENTER: "accept"}.get(key)
            if action:
                self.quit_action(action)

    @property
    def current_binding_config(self):
        return self.binding_config or BINDING_BY_ID[self.lab_binding].config

    def select_binding(self, identifier):
        self.lab_binding = identifier
        self.binding_config = None
        self.update_binding(0)

    def update_binding(self, dt):
        self.binding_frame = self.binding_engine.update(self.telemetry, dt, self.current_binding_config,
                                                        arena=self.arena, stream_view=self.stream_view)

    def binding_key(self, action):
        fields = ("source_key", "parameter", "normalization", "input_min", "input_max", "output_min",
                  "output_max", "smoothing_seconds", "missing", "clamp")
        if action == "escape":
            self.binding_editor = False
            return
        if action == "save":
            self.pending_actions.append("save")
            return
        if action in ("up", "down"):
            self.binding_row = (self.binding_row + (-1 if action == "up" else 1)) % len(fields)
            return
        if action == "reload":
            self.binding_config = None
            self.update_binding(0)
            return
        if action not in ("left", "right", "accept"):
            return
        config = self.current_binding_config
        field = fields[self.binding_row]
        direction = -1 if action == "left" else 1
        choices = {"source_key": tuple(dict.fromkeys([item.config.source_key for item in BINDING_BY_ID.values()]
                    + [sample.key for sample in self.telemetry.metrics])),
                   "parameter": ("energy", "particle_count", "pulse_bpm"), "normalization": ("linear", "log"),
                   "smoothing_seconds": (0, .15, .4, .8, 1.5, 3), "missing": ("neutral", "fade"),
                   "clamp": (True, False)}
        value = getattr(config, field)
        if field in choices:
            values = choices[field]
            index = values.index(value) if value in values else 0
            value = values[(index + direction) % len(values)]
        else:
            span = abs(config.input_max - config.input_min) if field.startswith("input") else abs(config.output_max - config.output_min)
            value = round(value + direction * max(.1, span / 10), 3)
        try:
            updates = {field: value}
            if field == "source_key":
                updates.update(source_id=None, expected_unit=None)
            self.binding_config = replace(config, **updates)
            self.update_binding(0)
            self.status_message = "Binding updated; S saves preferences."
        except ValueError as exc:
            self.status_message = str(exc)

    @property
    def atlas_entries(self):
        if self.atlas_view == "repository":
            repo = self.telemetry.repository
            return tuple(sorted(repo.files, key=lambda item: (not item.changed, item.path))) if repo else ()
        return self.asset_catalog.nodes if self.asset_catalog else ()

    @property
    def atlas_selected(self):
        entries = self.atlas_entries
        return entries[self.atlas_index % len(entries)] if entries else None

    @property
    def selected_atlas_context(self):
        selected, inspected = self.atlas_selected, self.atlas_inspection
        if selected is None or inspected is None or inspected.selection is None:
            return ""
        identifier = getattr(selected, "id", None) or getattr(selected, "path", "")
        if inspected.selection.id != identifier:
            return ""
        if (inspected.selection.kind == "repository") != (self.atlas_view == "repository"):
            return ""
        return inspected.handoff_context

    @property
    def palette_commands(self):
        from .commands import search
        return search(self.palette_query)

    def choose_screen(self, screen):
        self.fullscreen_panel = self._fullscreen_restore = None
        self.screen_id = screen
        self.focused_panel = "preview" if screen == "visual-lab" else SCREEN_BY_ID[screen].panels[0]
        self.zoomed = self.terminal_focused = False
        if screen == "arena":
            self.focused_panel = "terminal-" + self.terminal_slot

    @property
    def cue_time(self):
        return self.visual_time if self._cue_timeline is None else self._cue_timeline

    def publish_telemetry(self, snapshot, dt=None, timeline=None):
        self.telemetry = snapshot
        self._cue_timeline = timeline
        if dt is None:
            # An untimed snapshot has no elapsed interval to smooth across.
            self.binding_engine.reset()
        self.update_binding(0 if dt is None else dt)
        self.director_frame = self.director.update(snapshot.events, self.cue_time, self.intensity,
            spectator=self.director_enabled, manual_visual=self.animation if self.visual_time < self.manual_visual_until or self.menu_open else None,
            terminal_focused=self.terminal_focused)
        if self.director_frame.recommended_visual and self.director_frame.recommended_visual != self.animation:
            self.animation_override = self.director_frame.recommended_visual
        cue = next((item for item in reversed(snapshot.events) if item.topic in (
            "visual.cue", "agent.handoff.ready", "build.finished", "test.finished", "task.test.completed", "demo.build.finished")), None)
        if cue and cue.id != self._last_event:
            self._last_event = cue.id
            self._event_time = (cue.recorded_offset_ms / 1000.0 if cue.mode == "replay"
                                and cue.recorded_offset_ms is not None else self.cue_time)
        elif cue is None:
            self._last_event, self._event_time = None, None

    def visual_parameters(self):
        energy = {"calm": .2, "cinematic": .55, "showcase": .9}[self.intensity]
        parameters = {"energy": energy, "particle_count": int(25 + energy * 160),
                      "event_sequence": len(self.telemetry.events),
                      "burst_age": max(0., self.cue_time - self._event_time) if self._event_time is not None else 1e9,
                      "seed": self.seed if self.seed is not None else 42,
                      "queue_left": 0, "queue_right": 0}
        config = self.current_binding_config
        private = self.stream_view and config.source_key.startswith("body.")
        frame = self.binding_frame
        if frame is None or frame.config != config or private:
            frame = BindingEngine().update(self.telemetry, 0, config, arena=self.arena, stream_view=self.stream_view)
        parameters.update(frame.parameters)
        if frame.raw is not None:
            if config.parameter == "particle_count":
                parameters["energy"] = clamp(float(parameters["particle_count"]) / 200, 0, 1)
            elif config.parameter == "pulse_bpm":
                parameters["energy"] = clamp((float(parameters["pulse_bpm"]) - 40) / 140, 0, 1)
        if self.director_frame:
            parameters.update(self.director_frame.parameters)
            if self.director_frame.cue is None and self._event_time is not None:
                parameters["burst_age"] = max(0, self.cue_time - self._event_time)
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
        if "scope_mode" in values:
            self.choose_scope_mode(values["scope_mode"])
        for key, attribute in (("theme", "palette"), ("fps_cap", "fps"), ("background", "background"),
                               ("scope", "scope"), ("auto_cycle", "auto_cycle"),
                               ("scope_auto_cycle", "scope_auto_cycle")):
            if key in values:
                setattr(self.options, attribute, values[key])
        if "scope_auto_cycle" in values:
            self.scope_elapsed = 0.0
        for key in ("speed", "intensity", "lab_binding", "seed"):
            if key in values:
                setattr(self, key, values[key])
        if "binding_config" in values:
            self.binding_config = BindingConfig.from_dict(values["binding_config"])
        elif "lab_binding" in values:
            self.binding_config = None
        for key in ("visual_time", "protocol_time", "paused", "director_enabled"):
            if key in values:
                setattr(self, key, values[key])
        if "consumer" in values:
            self.options.consumer = values["consumer"]
        self.update_binding(0)
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
        elif kind == "scope-mode":
            self.choose_scope_mode(value)
        elif kind == "game":
            self.arcade_select(value)
        elif kind == "scene":
            self.activate(value)
        elif kind == "preset":
            from .presets import resolve
            self.apply_preferences(resolve(value))
        elif kind in ("binding", "intensity"):
            self.select_binding(value) if kind == "binding" else setattr(self, "intensity", value)
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

    def project_key(self, key):
        """A path is literal input, including UTF-8 pasted through curses.getch."""
        if key == 27:
            self.project_editor = False
            self._project_decoder.reset()
        elif key in (10, 13):
            self._project_decoder.reset()
            if "source-project" not in self.pending_actions:
                self.pending_actions.append("source-project")
        elif key in (8, 127, 263):
            self._project_decoder.reset()
            self.project_text = self.project_text[:-1]
            self.project_error = ""
        elif key == 21:
            self._project_decoder.reset()
            self.project_text = self.project_error = ""
        elif 32 <= key <= 255 and key != 127:
            text = self._project_decoder.decode(bytes((key,)))
            text = "".join(char for char in text if char.isprintable())
            if len((self.project_text + text).encode("utf-8")) <= 4096:
                self.project_text += text
                self.project_error = ""

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
        self.manual_visual_until = self.visual_time + 8

    def next_animation(self):
        index = next(i for i, item in enumerate(ANIMATIONS) if item.id == self.animation)
        self.animation_override = ANIMATIONS[(index + 1) % len(ANIMATIONS)].id

    def cycle_option(self, key):
        if key == "scope_mode":
            self.next_scope()
            return
        choices = {
            "auto_cycle": (0.0, 8.0, 15.0, 30.0), "scope": (True, False),
            "fps": (30, 60, 90, 120), "consumer": CONSUMER_IDS,
            "background": ("terminal", "black"), "palette": ("auto", *PALETTES),
            "speed": (.5, 1.0, 1.5, 2.0, 4.0),
            "scope_auto_cycle": (0.0, 8.0, 15.0, 30.0),
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
        elif key == "scope_auto_cycle":
            self.scope_elapsed = 0.0

    def option_text(self, key):
        value = self.speed if key == "speed" else getattr(self.options, key)
        if key in ("auto_cycle", "scope_auto_cycle"):
            return "OFF" if not value else "{:g}s".format(value)
        if key == "scope":
            return "ON" if value else "OFF"
        if key == "fps":
            return "{} FPS".format(value)
        if key == "speed":
            return "{:g}x".format(value)
        if key == "scope_mode":
            return SCOPE_BY_ID[value].title.upper()
        return value.upper()

    def activate(self, mode_id):
        if mode_id not in self.simulations:
            self.simulations[mode_id] = Simulation(MODE_BY_ID[mode_id], self.seed, sources=self.sources)
        self.mode_id = mode_id

    def handle(self, action):
        if self.quit_confirm:
            self.quit_action(action)
            return
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
            self.exit_fullscreen()
            self.terminal_slot = "right" if self.terminal_slot == "left" else "left"
            self.focused_panel = "terminal-" + self.terminal_slot if self.screen_id == "arena" else "source"
            self.terminal_focused = False
            self.handle("terminal")
            return
        if action == "terminal":
            self.exit_fullscreen()
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
            self.exit_fullscreen()
            self.terminal_visible = not self.terminal_visible
            self.terminal_focused = False
            return
        if action == "quit":
            self.request_quit()
            return
        if action == "help":
            self.help_open = True
            return
        if action == "handoff" and self.screen_id == "atlas":
            self.handle("atlas-context")
            return
        if action == "binding-settings":
            self.binding_editor = True
            self.terminal_focused = False
            return
        if action == "source-folder":
            if self.stream_view:
                self.status_message = "Leave Stream View to edit your private project folder."
                return
            if self.screen_id == "replay":
                self.status_message = "Replay is read-only; switch screens to select a project folder."
                return
            self.project_editor = True
            self.project_text = self.sources.project_root or ""
            self.project_error = ""
            self._project_decoder.reset()
            self.terminal_focused = False
            self.menu_kind = None
            self.help_open = self.palette_open = False
            return
        if action == "director":
            self.director_enabled = not self.director_enabled
            return
        if action == "replay-stage":
            self.replay_stage = "arena" if self.replay_stage == "visual" else "visual"
            return
        if action in ("inspect-task", "inspect-mission", "inspect-atlas"):
            self.inspector_kind = action.split("-", 1)[1]
            self.report_scroll = 0
            return
        if action in ("atlas-next", "atlas-prev"):
            if self.atlas_entries:
                self.atlas_index = (self.atlas_index + (-1 if action == "atlas-prev" else 1)) % len(self.atlas_entries)
                self.pending_actions.append("atlas-select")
            return
        if action == "atlas-context":
            context = self.selected_atlas_context
            if context and not self.stream_view:
                self.prepared_context = context
                self.status_message = "Selected context prepared. Arena H includes it in the editable handoff."
            else:
                self.status_message = "Inspect a readable selection before preparing agent context."
            return
        if action == "handoff" and self.arena and self.arena.mode in ("relay", "mission"):
            sender = "codex" if self.terminal_slot == "left" else "claude"
            card = next((item for item in self.arena.cards if item.id == sender), None)
            if card and card.status == "SUCCEEDED":
                self.composer_text = (card.summary + ("\n\nSELECTED WORKSPACE CONTEXT:\n" + self.prepared_context
                                                     if self.prepared_context else ""))[:12000]
                self.composer_open = True
            else:
                self.status_message = "Wait for a completed public reply before handing it off."
            return
        if action in ("panel", "panel-prev"):
            self.exit_fullscreen()
            panels = SCREEN_BY_ID[self.screen_id].panels
            self.focused_panel = panels[(panels.index(self.focused_panel) + (-1 if action == "panel-prev" else 1)) % len(panels)]
            if self.focused_panel.startswith("terminal-"):
                self.terminal_slot = self.focused_panel.split("-", 1)[1]
            return
        if action == "zoom":
            if self.fullscreen_panel is not None:
                self.exit_fullscreen()
            else:
                self.zoomed = not self.zoomed
            return
        if action == "binding":
            values = tuple(BINDING_BY_ID)
            self.select_binding(values[(values.index(self.lab_binding) + 1) % len(values)])
            return
        if action == "intensity":
            values = ("calm", "cinematic", "showcase")
            self.intensity = values[(values.index(self.intensity) + 1) % len(values)]
            return
        if action == "atlas":
            self.atlas_view = "repository" if self.atlas_view == "assets" else "assets"
            self.atlas_index = 0
            self.pending_actions.append("atlas-select")
            return
        if action == "stream":
            self.stream_view = not self.stream_view
            self.terminal_focused = False
            return
        if action in ("save", "record", "event", "handoff", "task", "mission-pause", "mission-stop", "mission-validate",
                      "replay-play", "replay-next", "replay-back", "replay-forward", "replay-speed-up", "replay-speed-down",
                      "replay-bookmark", "replay-next-bookmark", "capsule-export", "mission-export", "atlas-export", "arena-mode"):
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
            if self.fullscreen_panel is not None:
                self.exit_fullscreen()
            elif self.zoomed:
                self.zoomed = False
            else:
                self.request_quit()
        elif self.screen_id == "atlas" and action in ("up", "down"):
            self.handle("atlas-prev" if action == "up" else "atlas-next")
        elif self.screen_id == "atlas" and action == "accept":
            self.handle("inspect-atlas")
        elif self.screen_id == "site" and action in ("up", "down"):
            if self.telemetry.pages:
                self.site_index = (self.site_index + (-1 if action == "up" else 1)) % len(self.telemetry.pages)
        elif action == "tab":
            self.open_menu("modes")
        elif action == "animation":
            self.open_menu("animations")
        elif action == "pause":
            if self.screen_id == "replay" and self.replay_status:
                self.pending_actions.append("replay-play")
            else:
                self.paused = not self.paused
        elif action in ("faster", "slower"):
            if self.screen_id == "replay" and self.replay_status:
                self.pending_actions.append("replay-speed-up" if action == "faster" else "replay-speed-down")
            else:
                self.speed = clamp(self.speed * (1.25 if action == "faster" else 1 / 1.25), 0.25, 8.0)
        elif action == "theme":
            names = list(PALETTES)
            self.options.palette = names[(names.index(self.theme) + 1) % len(names)]
        elif action == "next":
            self.sim.load_file()
        elif action == "reload":
            if self.screen_id == "atlas":
                self.pending_actions.append("atlas-refresh")
            else:
                self.sim.load_file(advance=False)
        elif action == "background":
            self.cycle_option("background")
        elif action == "consumer":
            self.cycle_option("consumer")
        elif action == "scope":
            self.cycle_option("scope")
        elif action == "scope-next":
            self.next_scope()
        elif action == "scope-auto":
            self.cycle_option("scope_auto_cycle")
        elif action == "scope-zoom":
            if self.screen_id != "replay":
                self.choose_screen("cockpit")
            self.options.scope = True
            if self.fullscreen_panel != "scope":
                self.toggle_fullscreen("scope")
        elif action == "scope-fullscreen":
            self.toggle_fullscreen("scope")
        elif action == "visual-fullscreen":
            self.toggle_fullscreen("visual")
        elif action == "auto":
            self.cycle_option("auto_cycle")

    def update(self, dt):
        if (self.screen_id == "arcade" and self.arcade_game is not None and not self.menu_open
                and self.fullscreen_panel is None and self.arcade_viewport_ready):
            self.arcade_game.update(dt)
        if self.paused or self.menu_open:
            self.sim.update(0)
        if not self.paused and not self.menu_open:
            self.sim.update(dt * self.speed)
            self.visual_time += dt * self.speed
            self.protocol_time += dt * self.speed
            if self.options.scope_auto_cycle and not (self.screen_id == "replay" and self.replay_status):
                self.scope_elapsed += dt
                steps = int(self.scope_elapsed / self.options.scope_auto_cycle)
                if steps:
                    self.scope_elapsed %= self.options.scope_auto_cycle
                    ids = tuple(item.id for item in SCOPES)
                    self.options.scope_mode = ids[(ids.index(self.effective_scope_mode) + steps) % len(ids)]
            if self.options.auto_cycle:
                self.auto_elapsed += dt
                while self.auto_elapsed >= self.options.auto_cycle:
                    self.auto_elapsed -= self.options.auto_cycle
                    self.next_animation()
