"""Validated visual bindings with exact-time smoothing and explicit provenance.

Only the selected source is read. Raw measurements remain exact; EMA is applied
to decorative parameters and never changes a source card or a test outcome.
"""

from dataclasses import asdict, dataclass
import math
import re
from types import MappingProxyType
from typing import Optional


PARAMETERS = frozenset(("energy", "particle_count", "pulse_bpm", "test_passed", "test_total",
                        "queue_left", "queue_right"))
QUEUE_STATUSES = frozenset(("ready", "pending", "delivering", "delivered"))
TEST_TOPICS = frozenset(("task.test.completed", "test.finished", "build.finished", "demo.build.finished"))


def finite(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(name + " must be a finite number")
    return float(value)


@dataclass(frozen=True)
class BindingConfig:
    source_key: str
    parameter: str
    normalization: str = "linear"
    input_min: float = 0
    input_max: float = 100
    output_min: float = 0
    output_max: float = 1
    clamp: bool = True
    smoothing_seconds: float = .8
    missing: str = "neutral"
    neutral_value: Optional[float] = 0
    source_id: Optional[str] = None
    expected_unit: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.source_key, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", self.source_key):
            raise ValueError("Invalid binding source_key")
        if not isinstance(self.parameter, str) or self.parameter not in PARAMETERS:
            raise ValueError("Unknown visual parameter: " + str(self.parameter))
        if self.normalization not in ("linear", "log", "log1p"):
            raise ValueError("Binding normalization must be linear or log")
        if self.normalization == "log1p":
            object.__setattr__(self, "normalization", "log")
        for name in ("input_min", "input_max", "output_min", "output_max", "smoothing_seconds"):
            finite(getattr(self, name), name)
        if self.input_max <= self.input_min:
            raise ValueError("Binding input_max must exceed input_min")
        if self.output_max == self.output_min:
            raise ValueError("Binding output range must not be empty")
        finite(self.input_max - self.input_min, "Binding input span")
        finite(self.output_max - self.output_min, "Binding output span")
        if self.normalization == "log" and self.input_min < 0:
            raise ValueError("Log bindings require a nonnegative input range")
        if not 0 <= self.smoothing_seconds <= 3600:
            raise ValueError("Binding smoothing_seconds must be 0..3600")
        if type(self.clamp) is not bool or self.missing not in ("neutral", "fade"):
            raise ValueError("Invalid binding clamp or missing policy")
        if self.neutral_value is not None:
            finite(self.neutral_value, "neutral_value")
        for name in ("source_id", "expected_unit"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value or len(value) > 128):
                raise ValueError("Invalid binding " + name)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, document):
        if not isinstance(document, dict):
            raise ValueError("Binding configuration must be an object")
        try:
            return cls(**document)
        except TypeError as exc:
            raise ValueError("Invalid binding configuration fields: " + str(exc))


@dataclass(frozen=True)
class BindingSpec:
    id: str
    title: str
    description: str
    config: BindingConfig


BINDINGS = (
    BindingSpec("online", "ACTIVE SESSIONS", "Active sessions in the source's explicit window drive particle density.",
                BindingConfig("site.active_sessions_60s", "particle_count", "log", 0, 1000, 0, 200,
                              smoothing_seconds=.8, missing="fade", expected_unit="sessions")),
    BindingSpec("pulse", "BODY PULSE", "Measured BPM drives an artistic pulse; missing BPM is never invented.",
                BindingConfig("body.heart_rate", "pulse_bpm", "linear", 30, 240, 30, 240,
                              smoothing_seconds=.25, neutral_value=None, expected_unit="bpm")),
    BindingSpec("tests", "TEST SHIELD", "The latest completed run drives a shield; exact counts stay exact.",
                BindingConfig("tests.latest", "energy", "linear", 0, 1, 0, 1,
                              smoothing_seconds=.35, expected_unit="ratio")),
    BindingSpec("queue", "AGENT QUEUES", "Confirmed queued or delivered handoffs become satellites.",
                BindingConfig("agent.queue_length", "energy", "linear", 0, 12, 0, 1,
                              smoothing_seconds=.5, expected_unit="tasks")),
    BindingSpec("git", "WORKSPACE CHANGES", "A measured Git changed-file count drives visual energy.",
                BindingConfig("git.changed_files", "energy", "linear", 0, 50, 0, 1,
                              smoothing_seconds=.8, missing="fade", expected_unit="files")),
)
BINDING_BY_ID = {item.id: item for item in BINDINGS}


@dataclass(frozen=True)
class BindingFrame:
    parameters: object
    config: BindingConfig
    source_key: str
    source_id: Optional[str]
    source_session_id: Optional[str]
    mode: str
    quality: str
    unit: Optional[str]
    raw: Optional[float]
    transformed: Optional[float]
    smoothed: Optional[float]
    age_seconds: Optional[float] = None
    window_seconds: Optional[int] = None
    missing_reason: str = ""

    def __post_init__(self):
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))

    @property
    def source(self):
        return self.source_id


def transform(raw, config):
    value = finite(raw, "Binding measurement")
    if config.clamp:
        value = max(config.input_min, min(config.input_max, value))
    if config.normalization == "log":
        if value < 0:
            raise ValueError("Log binding measurement must be nonnegative")
        fraction = ((math.log1p(value) - math.log1p(config.input_min)) /
                    (math.log1p(config.input_max) - math.log1p(config.input_min)))
    else:
        fraction = (value - config.input_min) / (config.input_max - config.input_min)
    return finite(config.output_min + fraction * (config.output_max - config.output_min), "Binding transformed value")


def selected_source(snapshot, config, arena, stream_view):
    """Return metadata and exact supplemental parameters for one source only."""
    result = dict(raw=None, source_id=config.source_id, source_session_id=None, mode="unknown",
                  quality="unavailable", unit=config.expected_unit, age_seconds=None,
                  window_seconds=None, missing_reason="No selected source", extras={})
    if stream_view and config.source_key.startswith("body."):
        result.update(source_id=None, missing_reason="Private signal hidden by Stream View")
        return result
    if config.source_key == "tests.latest" or config.source_key in TEST_TOPICS:
        topics = TEST_TOPICS if config.source_key == "tests.latest" else (config.source_key,)
        event = next((item for item in reversed(snapshot.events if snapshot else ())
                      if item.topic in topics and (config.source_id is None or item.source_id == config.source_id)), None)
        if event is None:
            return result
        result.update(source_id=event.source_id, source_session_id=event.source_session_id,
                      mode=event.mode, unit="ratio")
        passed, total = event.payload.get("tests_passed"), event.payload.get("tests_total")
        if type(passed) is int and type(total) is int and total > 0 and 0 <= passed <= total:
            result.update(raw=passed / total, quality="ok", missing_reason="",
                          extras={"test_passed": passed, "test_total": total})
        elif type(event.payload.get("exit_code")) is int:
            state = event.payload.get("state", "completed")
            if state == "completed":
                result.update(raw=float(event.payload["exit_code"] == 0), quality="ok", missing_reason="")
            else:
                result.update(quality="error", missing_reason="Run did not complete: " + str(state))
        return result
    if config.source_key == "agent.queue_length" and arena is not None and config.source_id in (None, "arena"):
        active = tuple(item for item in arena.handoffs if item.status in QUEUE_STATUSES)
        extras = {"queue_left": sum(item.recipient == "codex" for item in active),
                  "queue_right": sum(item.recipient == "claude" for item in active)}
        result.update(raw=len(active), source_id="arena", source_session_id="arena-" + arena.mode,
                      mode=arena.mode if arena.mode in ("demo", "replay") else "live", quality="ok", unit="tasks",
                      missing_reason="", extras=extras)
        return result
    sample = snapshot.metric(config.source_key) if snapshot is not None else None
    if sample is None or config.source_id is not None and sample.source_id != config.source_id:
        return result
    result.update(raw=sample.value, source_id=sample.source_id, source_session_id=sample.source_session_id,
                  mode=sample.mode, quality=sample.quality, unit=sample.unit,
                  age_seconds=sample.age_seconds, window_seconds=sample.window_seconds, missing_reason="")
    if sample.value is None or sample.quality not in ("ok", "stale"):
        result.update(raw=None, missing_reason="Selected measurement is " + sample.quality)
    return result


class BindingEngine:
    """A single selected patch with no I/O and constant-size smoothing state."""

    def __init__(self):
        self.reset()

    def reset(self):
        self._config = self._identity = self._value = None
        self._measured = False

    def update(self, snapshot, dt, config, arena=None, stream_view=False):
        dt = finite(dt, "Binding dt")
        if dt < 0:
            raise ValueError("Binding dt must be nonnegative")
        if isinstance(config, BindingSpec):
            config = config.config
        if isinstance(config, dict):
            config = BindingConfig.from_dict(config)
        if not isinstance(config, BindingConfig):
            raise ValueError("Expected a BindingConfig")
        selected = selected_source(snapshot, config, arena, stream_view)
        extras = selected.pop("extras")
        raw, transformed = selected["raw"], None
        if raw is not None:
            try:
                if config.expected_unit is not None and selected["unit"] != config.expected_unit:
                    raise ValueError("Expected " + config.expected_unit + ", received " + str(selected["unit"]))
                transformed = transform(raw, config)
            except ValueError as exc:
                raw = None
                selected.update(raw=None, quality="error", missing_reason=str(exc))
                extras = {}
        private = stream_view and config.source_key.startswith("body.")
        identity = (selected["source_id"], selected["source_session_id"], selected["mode"])
        source_changed = self._identity is not None and selected["source_id"] is not None and identity != self._identity
        if config != self._config or source_changed or private:
            self._value = None
            self._measured = False
        self._config = config
        if selected["source_id"] is not None:
            self._identity = identity
        target = transformed if raw is not None else config.neutral_value
        if raw is not None and not self._measured:
            # A missing startup placeholder is not an observed zero. The first
            # real value initializes EMA rather than fading from invented data.
            self._value, self._measured = target, True
        if self._value is None:
            self._value = target
        elif dt > 0 and target is not None:
            tau = config.smoothing_seconds
            if raw is None and config.missing == "fade":
                tau = tau or .8
            if tau == 0 or raw is None and config.missing == "neutral":
                self._value = target
            else:
                alpha = -math.expm1(-dt / tau)
                self._value = self._value * (1 - alpha) + target * alpha
        elif raw is None and target is None:
            self._value = None
        parameters = dict(extras)
        if self._value is not None and not private:
            parameters[config.parameter] = self._value
        return BindingFrame(parameters=parameters, config=config, source_key=config.source_key,
                            transformed=transformed, smoothed=None if private else self._value, **selected)
