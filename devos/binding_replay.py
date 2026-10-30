"""Analytic zero-order-hold binding replay over recorded event offsets.

EMA advances at event and TTL boundaries, never at graphics frame boundaries.
This provides a stable reconstruction, not a claim of matching the original UI
scheduler bit for bit. No provider workers, files, commands or live data are used.
"""

from collections import OrderedDict, deque
from copy import copy
from dataclasses import dataclass, replace
import json
import math
from types import MappingProxyType

from .bindings import BINDING_BY_ID, TEST_TOPICS, BindingConfig, BindingEngine, BindingSpec
from .playback import PlaybackProjection, safe_parameters
from .telemetry import DataEvent, TelemetryHub


MAX_EVENTS = 200000
MAX_BYTES = 200 * 1024 * 1024
CHECKPOINT_INTERVAL = 256
MAX_CHECKPOINTS = 8


@dataclass(frozen=True)
class _ArenaView:
    mode: str
    handoffs: tuple
    status: str
    cards: tuple


def seconds(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 3153600000:
        raise ValueError("Binding replay positions must be finite bounded seconds")
    return float(value)


def configuration(value):
    if isinstance(value, BindingSpec):
        return value.config
    if isinstance(value, BindingConfig):
        return value
    if isinstance(value, dict):
        return BindingConfig.from_dict(value)
    raise ValueError("Expected a replay BindingConfig")


def initial_config(preferences):
    if preferences.get("binding_config") is not None:
        return configuration(preferences["binding_config"])
    return BINDING_BY_ID[preferences.get("lab_binding", "online")].config


class ReplayBindings:
    """A bounded incremental replay with up to eight canonical checkpoints.

    ``offsets`` use seconds, as Replay._offsets does. An explicit ``config`` is
    an analysis override for the whole timeline; None follows recorded settings.
    An explicit ``arena`` is held as a selected-state override because its past
    cannot be inferred. Omit it to reconstruct recorded queue changes instead.
    """

    def __init__(self, events, offsets, manifestprefs=None):
        if len(events) != len(offsets) or len(events) > MAX_EVENTS:
            raise ValueError("Replay binding event/offset count is invalid or exceeds limit")
        normalized, positions, total = [], [], 0
        for event, offset in zip(events, offsets):
            offset = seconds(offset)
            if positions and offset < positions[-1]:
                raise ValueError("Binding replay offsets must be monotonic")
            if not isinstance(event, DataEvent):
                raise ValueError("Replay bindings require normalized DataEvent objects")
            origin = event.origin_mode if event.mode == "replay" else event.mode
            event = replace(event, mode="replay", origin_mode=origin, recorded_offset_ms=round(offset * 1000))
            event = DataEvent.from_dict(event.to_dict())
            total += len(json.dumps(event.to_dict(), ensure_ascii=True).encode())
            if total > MAX_BYTES:
                raise ValueError("Replay binding events exceed 200 MiB")
            normalized.append(event)
            positions.append(offset)
        self.events, self.offsets = tuple(normalized), tuple(positions)
        self.preferences = safe_parameters(manifestprefs or {})
        self._projection = PlaybackProjection(self.events, self.offsets, self.preferences)
        self._hub = None
        self._override = self._arena_override = None
        self._stream_view, self._cache_key = False, None
        self._checkpoints = deque(maxlen=MAX_CHECKPOINTS)
        self._last_requested = 0.
        self._reset()

    @property
    def cache_info(self):
        return MappingProxyType({"events": len(self.events), "processed": self._cursor,
                                 "checkpoints": len(self._checkpoints), "anchor": self._anchor})

    def _new_hub(self):
        if self._hub is not None:
            self._hub.close()
        self._hub = TelemetryHub(seed=self.preferences.get("seed", 42), start_workers=False,
                                 demo=False, empty_live_placeholders=False, clock=lambda: self._clock)

    def _reset(self):
        self._clock = self._anchor = 0.
        self._cursor, self._last_requested = 0, 0.
        self._config = initial_config(self.preferences)
        self._engine, self._tests = BindingEngine(), OrderedDict()
        self._projection._reset()
        self._new_hub()

    def _current_config(self):
        return self._override or self._config

    def _arena(self):
        arena = self._arena_override or self._projection.arena_snapshot()
        if getattr(arena, "status", "") == "NO_RECORDED_STATE" and not arena.handoffs and not arena.cards:
            return None
        return _ArenaView("replay", tuple(arena.handoffs), getattr(arena, "status", ""), tuple(getattr(arena, "cards", ())))

    def _snapshot(self, config):
        self._hub._elapsed = self._clock
        self._hub._source_selection.clear()
        if config.source_id is not None:
            self._hub._source_selection[config.source_key] = config.source_id
        snapshot = self._hub.snapshot()
        if config.source_key == "tests.latest" or config.source_key in TEST_TOPICS:
            # A long metric-only span must not erase the most recent completed
            # run merely because the display journal has a 2000-event limit.
            snapshot = replace(snapshot, events=tuple(sorted(self._tests.values(),
                               key=lambda event: event.recorded_offset_ms)))
        return snapshot

    def _next_transition(self, snapshot, config, position, end):
        sample = snapshot.metric(config.source_key)
        if sample is None or sample.value is None:
            return end
        measured_pulse = sample.key.startswith("body.") and sample.origin_mode == "live"
        thresholds = (3., 10.) if measured_pulse else (15., 60.)
        for threshold in thresholds:
            remaining = threshold - sample.age_seconds
            if remaining > 1e-10:
                return min(end, position + remaining)
        return end

    def _advance(self, engine, start, end):
        config, arena = self._current_config(), self._arena()
        position = start
        while position < end:
            self._clock = position
            snapshot = self._snapshot(config)
            following = self._next_transition(snapshot, config, position, end)
            engine.update(snapshot, following - position, config, arena, self._stream_view)
            position = following
        self._clock = end
        return engine.update(self._snapshot(config), 0, config, arena, self._stream_view)

    def _apply_group(self, offset):
        self._advance(self._engine, self._anchor, offset)
        while self._cursor < len(self.events) and self.offsets[self._cursor] == offset:
            event = self.events[self._cursor]
            accepted = self._hub._accept(event, offset)
            if accepted and event.topic in TEST_TOPICS:
                identity = (event.source_id, event.topic)
                self._tests[identity] = event
                self._tests.move_to_end(identity)
                if len(self._tests) > 128:
                    self._tests.popitem(last=False)
            if event.topic == "presentation.changed":
                document = event.payload.get("parameters", event.payload.get("preferences", {}))
                parameters = safe_parameters(dict(document))
                if "binding_config" in parameters:
                    self._config = configuration(parameters["binding_config"])
                elif "lab_binding" in parameters:
                    self._config = BINDING_BY_ID[parameters["lab_binding"]].config
            self._cursor += 1
        self._projection.seek(offset)
        self._anchor = self._clock = offset
        self._engine.update(self._snapshot(self._current_config()), 0, self._current_config(),
                            self._arena(), self._stream_view)
        if self._cursor // CHECKPOINT_INTERVAL > (self._checkpoints[-1]["cursor"] // CHECKPOINT_INTERVAL if self._checkpoints else 0):
            self._checkpoint()

    def _checkpoint(self):
        projection = copy(self._projection)
        projection._parameters = dict(self._projection._parameters)
        state = {"anchor": self._anchor, "cursor": self._cursor, "config": self._config,
                 "engine": copy(self._engine), "tests": self._tests.copy(), "projection": projection,
                 "metrics": dict(self._hub._metrics), "sequences": dict(self._hub._sequences),
                 "source_sessions": dict(self._hub._source_sessions),
                 "retired_sessions": set(self._hub._retired_sessions),
                 "measurement_ids": dict(self._hub._measurement_ids),
                 "journal": tuple(self._hub._journal)}
        self._checkpoints.append(state)

    def _restore(self, checkpoint):
        self._anchor = self._clock = checkpoint["anchor"]
        self._cursor, self._config = checkpoint["cursor"], checkpoint["config"]
        self._engine, self._tests = copy(checkpoint["engine"]), checkpoint["tests"].copy()
        self._projection = copy(checkpoint["projection"])
        self._projection._parameters = dict(checkpoint["projection"]._parameters)
        self._new_hub()
        for name in ("metrics", "sequences", "source_sessions", "measurement_ids"):
            setattr(self._hub, "_" + name, dict(checkpoint[name]))
        self._hub._retired_sessions = set(checkpoint["retired_sessions"])
        self._hub._journal = deque(checkpoint["journal"], maxlen=2000)

    def seek(self, playhead, config=None, arena=None, stream_view=False):
        playhead = seconds(playhead)
        override = configuration(config) if config is not None else None
        arena_identity = None if arena is None else (arena.mode, getattr(arena, "status", ""), tuple(
            (item.message_id, item.status, item.recipient, item.sequence) for item in arena.handoffs))
        key = (override, arena_identity, bool(stream_view))
        self._override, self._arena_override, self._stream_view = override, arena, bool(stream_view)
        if key != self._cache_key:
            self._cache_key = key
            self._checkpoints.clear()
            self._reset()
        elif playhead < self._last_requested:
            eligible = [item for item in self._checkpoints if item["anchor"] <= playhead]
            if eligible:
                self._restore(max(eligible, key=lambda item: item["anchor"]))
            else:
                self._reset()
        while self._cursor < len(self.events) and self.offsets[self._cursor] <= playhead:
            self._apply_group(self.offsets[self._cursor])
        # Preview from the canonical event anchor, never from the last graphics
        # frame. Playback at x0.25/x8 and a direct seek therefore share one EMA.
        frame = self._advance(copy(self._engine), self._anchor, playhead)
        self._last_requested = playhead
        return frame

    def close(self):
        if self._hub is not None:
            self._hub.close()
