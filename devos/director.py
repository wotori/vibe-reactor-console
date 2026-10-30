"""Finite visual cues and spectator recommendations; no UI or source control.

Recorded offsets make seek reconstruction independent of render-frame count.
Live events without an offset are timed when first observed by this director.
"""

from collections import OrderedDict
from dataclasses import dataclass, replace
import math
from types import MappingProxyType


@dataclass(frozen=True)
class IntensityProfile:
    base_energy: float
    particle_limit: int
    burst_strength: float
    cue_duration: float
    switch_cooldown: float
    minimum_interval: float


INTENSITY_PROFILES = MappingProxyType({
    "calm": IntensityProfile(.2, 60, .4, .9, 10, .3),
    "cinematic": IntensityProfile(.55, 140, .8, 1.2, 10, .15),
    "showcase": IntensityProfile(.9, 200, 1., 1.5, 6, .08),
})


@dataclass(frozen=True)
class VisualCue:
    id: str
    topic: str
    kind: str
    started_at: float
    duration: float
    priority: int
    visual: str
    count: int
    mode: str
    source_id: str
    strength: float = 1.


@dataclass(frozen=True)
class DirectorFrame:
    cue: object
    parameters: object
    recommended_visual: object
    cooldown_remaining: float

    def __post_init__(self):
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


CUE_KINDS = MappingProxyType({
    "failure": (100, "packet-garden"),
    "handoff": (80, "twin-core"),
    "success": (60, "torus-knot"),
    "surge": (40, "gravity-well"),
    "git": (20, "voxel-tide"),
})


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


class EventDirector:
    def __init__(self, surge_high=100, surge_low=70, debounce_seconds=.25, max_seen=2048):
        if not all(numeric(value) for value in (surge_high, surge_low, debounce_seconds)) or not 0 <= surge_low < surge_high or debounce_seconds < 0:
            raise ValueError("Invalid director surge thresholds or debounce")
        if type(max_seen) is not int or not 16 <= max_seen <= 8192:
            raise ValueError("Director max_seen must be 16..8192")
        self.surge_high, self.surge_low = surge_high, surge_low
        self.debounce_seconds, self.max_seen = debounce_seconds, max_seen
        self.reset()

    def reset(self):
        self._seen, self._watermarks, self._metrics = OrderedDict(), OrderedDict(), OrderedDict()
        self._active = {}
        self._elapsed, self._sequence, self._signature = None, 0, None
        self._last_emit, self._last_priority = -math.inf, 0
        self._visual, self._last_switch = None, -math.inf

    @property
    def state_size(self):
        return MappingProxyType({"seen": len(self._seen), "sources": len(self._watermarks),
                                 "metrics": len(self._metrics), "active": len(self._active)})

    def _remember(self, event):
        identity = (event.source_id, event.source_session_id)
        if event.id in self._seen or event.sequence <= self._watermarks.get(identity, -1):
            return False
        self._seen[event.id] = None
        self._watermarks[identity] = event.sequence
        self._watermarks.move_to_end(identity)
        if len(self._seen) > self.max_seen:
            self._seen.popitem(last=False)
        if len(self._watermarks) > 256:
            self._watermarks.popitem(last=False)
        self._sequence += 1
        return True

    def _kind(self, event):
        topic, payload = event.topic, event.payload
        if topic.startswith("body.") or str(payload.get("key", "")).startswith("body."):
            return None
        if topic == "metric.sample":
            key, value = payload.get("key"), payload.get("value")
            if not numeric(value) or payload.get("quality", "ok") != "ok":
                return None
            identity = (event.source_id, event.source_session_id, key)
            previous = self._metrics.get(identity)
            if key == "site.active_sessions_60s":
                active = previous if previous is not None else False
                current = value >= self.surge_low if active else value >= self.surge_high
                self._metrics[identity] = current
                kind = "surge" if current and not active else None
            elif key == "git.changed_files":
                self._metrics[identity] = value
                kind = "git" if previous is not None and value != previous else None
            else:
                return None
            self._metrics.move_to_end(identity)
            if len(self._metrics) > 256:
                self._metrics.popitem(last=False)
            return kind
        if topic == "visual.cue":
            kind = payload.get("kind", payload.get("type", payload.get("cue")))
            if not isinstance(kind, str):
                return None
            kind = kind.casefold().replace("_", "-")
            kind = {"traffic-surge": "surge", "test-failed": "failure", "testfailed": "failure",
                    "test-failure": "failure", "test-passed": "success", "testpassed": "success",
                    "test-success": "success", "handoff-ready": "handoff", "git-change": "git",
                    "git-changed": "git"}.get(kind, kind)
            return kind if kind in CUE_KINDS else None
        failed = payload.get("result") == "failed" or payload.get("state") in ("failed", "timed_out", "error")
        passed, total = payload.get("tests_passed"), payload.get("tests_total")
        counts = type(passed) is int and type(total) is int and 0 <= passed <= total and total > 0
        if ".fail" in topic or failed or counts and passed < total:
            return "failure"
        if "handoff" in topic:
            return "handoff"
        if topic in ("task.test.completed", "test.finished", "build.finished", "demo.build.finished"):
            code = payload.get("exit_code")
            if type(code) is int and payload.get("state", "completed") == "completed":
                return "success" if code == 0 else "failure"
            if payload.get("result") == "passed" or counts and passed == total:
                return "success"
        if topic in ("site.traffic.surge", "site.surge"):
            return "surge"
        if topic in ("git.status.changed", "git.files.changed"):
            return "git"
        return None

    def _accept(self, event, started_at, kind, profile, allow_switch):
        priority, visual = CUE_KINDS[kind]
        existing = self._active.get(kind)
        if existing and 0 <= started_at - existing.started_at < self.debounce_seconds:
            self._active[kind] = replace(existing, count=min(1000000, existing.count + 1))
            return
        if started_at - self._last_emit < profile.minimum_interval and priority <= self._last_priority:
            return
        duration, strength = profile.cue_duration, 1.
        if event.topic == "visual.cue":
            milliseconds = event.payload.get("duration_ms")
            requested_strength = event.payload.get("strength")
            if numeric(milliseconds):
                duration = max(.05, min(5., milliseconds / 1000))
            if numeric(requested_strength):
                strength = max(0., min(1., requested_strength))
        cue = VisualCue(event.id, event.topic, kind, started_at, duration,
                        priority, visual, 1, event.mode, event.source_id, strength)
        self._active[kind] = cue
        self._last_emit, self._last_priority = started_at, priority
        if allow_switch and (self._visual is None or started_at - self._last_switch >= profile.switch_cooldown):
            # At a shared offset a failure wins over an already active handoff.
            stronger = any(other.priority > priority and 0 <= started_at - other.started_at < other.duration
                           for other in self._active.values())
            if not stronger:
                self._visual, self._last_switch = visual, started_at
        elif allow_switch and started_at == self._last_switch and priority > 0:
            strongest = max((item for item in self._active.values()
                             if 0 <= started_at - item.started_at < item.duration), key=lambda item: item.priority)
            self._visual = strongest.visual

    def update(self, events, elapsed, intensity="cinematic", spectator=False,
               manual_visual=None, terminal_focused=False):
        if not numeric(elapsed) or elapsed < 0:
            raise ValueError("Director elapsed must be a finite nonnegative number")
        if intensity not in INTENSITY_PROFILES:
            raise ValueError("Unknown intensity profile: " + str(intensity))
        if self._elapsed is not None and elapsed < self._elapsed:
            self.reset()
        self._elapsed = elapsed
        profile = INTENSITY_PROFILES[intensity]
        allow_switch = spectator and not terminal_focused and manual_visual is None
        events = tuple(events)
        signature = (len(events), events[0].id, events[-1].id) if events else ()
        if signature != self._signature:
            timed = []
            for index, event in enumerate(events):
                offset = event.recorded_offset_ms
                when = offset / 1000 if offset is not None else event.payload.get("offset_seconds", elapsed)
                if numeric(when) and 0 <= when <= elapsed:
                    timed.append((when, index, event))
            timed.sort(key=lambda item: (item[0], item[1]))
            for when, _, event in timed:
                if event.topic.startswith("body.") or str(event.payload.get("key", "")).startswith("body."):
                    continue
                if self._remember(event):
                    kind = self._kind(event)
                    if kind is not None:
                        self._accept(event, when, kind, profile, allow_switch)
            # Future events must be considered again when the playhead advances.
            if len(timed) == len(events):
                self._signature = signature
        self._active = {kind: cue for kind, cue in self._active.items()
                        if 0 <= elapsed - cue.started_at < cue.duration}
        cue = max(self._active.values(), key=lambda item: (item.priority, item.started_at), default=None)
        age = elapsed - cue.started_at if cue else 1e9
        strength = profile.burst_strength * cue.strength * max(0, 1 - age / cue.duration) if cue else 0
        params = {"burst_age": age, "cue_strength": strength,
                  "cue_count": cue.count if cue else 0, "event_sequence": self._sequence}
        return DirectorFrame(cue, params, self._visual if allow_switch else None,
                             max(0, profile.switch_cooldown - (elapsed - self._last_switch)))

    def reconstruct(self, events, elapsed, **options):
        self.reset()
        return self.update(events, elapsed, **options)
