"""Pure projections and shareable scene data: no providers or command handlers."""

from bisect import bisect_right
from dataclasses import dataclass, replace
import json
import math
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from collections.abc import Mapping

from .agents import AgentCard, ArenaEvent, ArenaSnapshot, Artifact, Handoff, ValidationReport


SPEEDS = (.25, .5, 1., 2., 4., 8.)
MAX_DOCUMENT = 60000
MAX_BOOKMARKS = 512
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_PATH = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/]|~[\\/]|/)[^\s\"'<>]+")
_URL = re.compile(r"https?://[^\s\"'<>]+", re.I)
_CREDENTIAL = re.compile(
    r"(?<![A-Za-z0-9_])(?P<prefix>(?:--)?[\"']?(?:access[_-]?token|refresh[_-]?token|token|secret|password|authorization|credentials?|api[_-]?key)[\"']?\s*(?:[:=]\s*|\s+))"
    r"(?P<value>(?:Bearer\s+)?(?:\[redacted\]|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+))", re.I)
_GENERIC_LABELS = {"manual": "Manual bookmark", "failure": "Failed check", "handoff": "Agent handoff", "surge": "Audience surge"}


def _text(value, limit=1024, include_paths=False):
    if not isinstance(value, str):
        return ""
    value = "".join(char for char in value if char in "\n\t" or ord(char) >= 32 and ord(char) != 127)
    def redact(match):
        original = match.group("value")
        quote = original[0] if original and original[0] in "\"'" else ""
        return match.group("prefix") + quote + "[redacted]" + quote

    value = _CREDENTIAL.sub(redact, value)
    if not include_paths:
        value = _URL.sub("[url]", value)
        value = _PATH.sub("[path]", value)
    return value.encode("utf-8", "replace")[:limit].decode("utf-8", "ignore")


def _number(value, default=0., maximum=1e12):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError("Replay values must be bounded finite nonnegative numbers")
    return float(value)


def _integer(value, minimum=0, maximum=1000000000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("Replay value must be a bounded integer")
    return value


def _id(value, fallback="unknown"):
    return value if isinstance(value, str) and _IDENTIFIER.fullmatch(value) else fallback


def _plain(value):
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _command_name(value):
    if not isinstance(value, str):
        return "[command redacted]"
    name = value.replace("\\", "/").rsplit("/", 1)[-1]
    return name if re.fullmatch(r"[A-Za-z0-9_.+-]{1,80}", name) else "[command redacted]"


def _bounded_document(value, limit=65536):
    if not isinstance(value, Mapping):
        raise ValueError("Replay document must be an object")
    try:
        plain = _plain(value)
        encoded = json.dumps(plain, allow_nan=False, ensure_ascii=True)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("Replay document must be bounded finite JSON")
    if len(encoded.encode()) > limit:
        raise ValueError("Replay document exceeds its bounded size")
    return plain


def _items(document, key, limit):
    value = document.get(key, [])
    if not isinstance(value, (list, tuple)) or len(value) > limit or any(not isinstance(item, Mapping) for item in value):
        raise ValueError("Invalid bounded replay array: " + key)
    return value


def safe_parameters(document):
    """Presentation only. Source/provider modes cannot become replay actions."""
    from .presets import validate
    document = _bounded_document(document)
    aliases = {"animation": "visual", "mode": "scene", "fps": "fps_cap", "palette": "theme"}
    allowed = {"screen", "scene", "visual", "theme", "background", "fps_cap", "speed", "auto_cycle",
               "scope", "scope_mode", "scope_auto_cycle", "seed", "intensity", "lab_binding",
               "binding_config", "director_enabled", "consumer"}
    canonical = {aliases.get(key, key): value for key, value in document.items() if aliases.get(key, key) in allowed}
    return validate(dict(canonical, schema_version=1))


def presentation_document(parameters, visual_time=0., protocol_time=0., paused=False):
    if type(paused) is not bool:
        raise ValueError("Recorded presentation paused must be boolean")
    return {"schema_version": 1, "parameters": safe_parameters(parameters),
            "visual_time": _number(visual_time), "protocol_time": _number(protocol_time), "paused": paused}


@dataclass(frozen=True)
class PresentationSnapshot:
    parameters: Mapping
    visual_time: float = 0.
    protocol_time: float = 0.
    paused: bool = False
    offset: float = 0.

    @property
    def source_screen(self):
        return self.parameters.get("screen", "cockpit")


@dataclass(frozen=True)
class Bookmark:
    id: str
    offset: float
    label: str
    kind: str = "manual"
    event_id: str = ""
    automatic: bool = False


def arena_document(snapshot, include_text=True, include_paths=False):
    """Bound a public snapshot; paths/argv/session identity are redacted by default.

    Public answer text is optional. Full prompts, raw terminal frames and tools
    are never read from an ArenaSnapshot. Validation keeps actual status/exit/hash;
    redacted argv contains only the executable basename, not reconstructed args.
    """
    def text(value, limit=1024):
        return _text(value, limit, include_paths) if include_text else ""

    def relative_paths(values):
        result = []
        for value in tuple(values)[:16]:
            if not isinstance(value, str):
                continue
            path = PurePosixPath(value.replace("\\", "/"))
            if include_paths or not path.is_absolute() and ".." not in path.parts and ":" not in value and not value.startswith("~"):
                result.append(_text(value, 160, include_paths))
        return result

    document = {"schema_version": 1, "mode": _id(snapshot.mode), "status": _id(snapshot.status),
                "elapsed": _number(snapshot.elapsed), "max_handoffs": _integer(snapshot.max_handoffs, 0, 10000),
                "deadline": _number(snapshot.deadline), "reason": text(snapshot.reason, 512),
                "verified": bool(snapshot.verified), "phase": _id(snapshot.phase, "idle"),
                "fix_cycles": _integer(snapshot.fix_cycles, 0, 100), "cards": [], "events": [],
                "handoffs": [], "artifacts": [], "validation_reports": []}
    for card in tuple(snapshot.cards)[-8:]:
        document["cards"].append({"id": _id(card.id), "name": _text(card.name, 80), "role": _text(card.role, 80),
                                  "status": _id(card.status), "summary": text(card.summary, 2048),
                                  "usage": [[_id(str(key)), value] for key, value in tuple(card.usage)[:16]
                                            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0],
                                  "cost": card.cost if isinstance(card.cost, (int, float)) and not isinstance(card.cost, bool) and math.isfinite(card.cost) and card.cost >= 0 else None})
    for event in tuple(snapshot.events)[-32:]:
        document["events"].append({"sequence": _integer(event.sequence), "kind": _id(event.kind),
                                   "actor": _id(event.actor), "text": text(event.text, 256), "timestamp": _number(event.timestamp)})
    for artifact in tuple(snapshot.artifacts)[-32:]:
        document["artifacts"].append({"id": _id(artifact.id), "kind": _id(artifact.kind),
                                      "sha256": artifact.sha256 if isinstance(artifact.sha256, str) and _HASH.fullmatch(artifact.sha256) else "",
                                      "producer": _id(artifact.producer), "base": _id(artifact.base, "") if artifact.base is not None else None,
                                      "paths": relative_paths(artifact.paths), "text": text(artifact.text, 768)})
    for message in tuple(snapshot.handoffs)[-32:]:
        document["handoffs"].append({"message_id": _id(message.message_id), "sequence": _integer(message.sequence),
                                     "sender": _id(message.sender), "recipient": _id(message.recipient),
                                     "summary": text(message.summary, 768), "expected_result": text(message.expected_result, 256),
                                     "status": _id(message.status), "artifacts": [_id(getattr(item, "id", item)) for item in tuple(message.artifacts)[:20]],
                                     "reply_to": _id(message.reply_to, "") if message.reply_to else None, "type": _id(message.type)})
    for report in tuple(snapshot.validation_reports)[-8:]:
        argv = tuple(report.argv)[:64]
        if include_paths:
            argv = tuple(_text(value, 256, True) for value in argv)
        else:
            argv = (_command_name(argv[0]),) if argv else ()
        document["validation_reports"].append({"status": _id(report.status), "argv": list(argv),
                                                "argv_redacted": not include_paths, "cwd": _text(report.cwd, 512, True) if include_paths else "",
                                                "revision": _id(report.revision, "") if report.revision else None,
                                                "artifact_hash": report.artifact_hash if isinstance(report.artifact_hash, str) and _HASH.fullmatch(report.artifact_hash) else "",
                                                "started": _number(report.started), "elapsed": _number(report.elapsed),
                                                "exit_code": report.exit_code if type(report.exit_code) is int else None,
                                                "stdout": text(report.stdout, 1024) if include_paths else "",
                                                "stderr": text(report.stderr, 1024) if include_paths else ""})
    # Public state stays within one canonical DataEvent (64 KiB), even when
    # long-lived missions have accumulated more history than one frame can show.
    while len(json.dumps(document, allow_nan=False).encode()) > MAX_DOCUMENT:
        largest = max((key for key in ("events", "artifacts", "handoffs", "validation_reports") if document[key]),
                      key=lambda key: len(json.dumps(document[key])), default=None)
        if largest is None:
            raise ValueError("Arena cards exceed the public snapshot budget")
        document[largest].pop(0)
        document["truncated"] = True
    return document


def arena_from_document(value):
    """Deserialize only display state; unknown keys cannot become actions."""
    document = _bounded_document(value)
    if document.get("schema_version", 1) != 1 or type(document.get("schema_version", 1)) is not int:
        raise ValueError("Unsupported Arena replay snapshot version")
    cards, artifacts, handoffs, reports, events = [], [], [], [], []
    for item in _items(document, "cards", 8):
        usage = item.get("usage", [])
        if not isinstance(usage, (tuple, list)) or len(usage) > 16:
            raise ValueError("Invalid reported usage")
        parsed_usage = []
        for pair in usage:
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                raise ValueError("Invalid reported usage item")
            parsed_usage.append((_id(pair[0]), _number(pair[1])))
        cost = item.get("cost")
        cards.append(AgentCard(_id(item.get("id")), _text(item.get("name", "Agent"), 80),
                               _text(item.get("role", ""), 80), _id(item.get("status"), "UNKNOWN"),
                               _text(item.get("summary", ""), 2048), "", tuple(parsed_usage),
                               None if cost is None else _number(cost)))
    for item in _items(document, "artifacts", 64):
        digest = item.get("sha256", "")
        if digest and (not isinstance(digest, str) or not _HASH.fullmatch(digest)):
            raise ValueError("Invalid replay artifact hash")
        paths = item.get("paths", [])
        if not isinstance(paths, (list, tuple)) or len(paths) > 64:
            raise ValueError("Invalid replay artifact paths")
        relative = tuple(path for path in paths if isinstance(path, str) and not PurePosixPath(path.replace("\\", "/")).is_absolute()
                         and ".." not in PurePosixPath(path.replace("\\", "/")).parts and ":" not in path and not path.startswith("~"))
        artifacts.append(Artifact(_id(item.get("id")), _id(item.get("kind")), digest, _id(item.get("producer")),
                                  _id(item.get("base"), "") if item.get("base") else None,
                                  tuple(_text(path, 160) for path in relative), _text(item.get("text", ""), 2048)))
    by_id = {item.id: item for item in artifacts}
    for item in _items(document, "handoffs", 64):
        references = item.get("artifacts", [])
        if not isinstance(references, (list, tuple)) or len(references) > 20:
            raise ValueError("Invalid handoff artifact references")
        handoffs.append(Handoff(_id(item.get("message_id")), _integer(item.get("sequence", 0)),
                                 _id(item.get("sender", item.get("from"))), _id(item.get("recipient", item.get("to"))),
                                 _text(item.get("summary", ""), 2048), _text(item.get("expected_result", ""), 1024),
                                 _id(item.get("status"), "unknown"), tuple(by_id.get(_id(key), _id(key)) for key in references),
                                 _id(item.get("reply_to"), "") if item.get("reply_to") else None, _id(item.get("type"), "review_request")))
    for item in _items(document, "validation_reports", 16):
        argv = item.get("argv", [])
        if not isinstance(argv, (list, tuple)) or len(argv) > 64:
            raise ValueError("Invalid replay validation argv")
        command = (_command_name(argv[0]),) if argv else ()
        code = item.get("exit_code")
        if code is not None:
            code = _integer(code, -65535, 65535)
        digest = item.get("artifact_hash", "")
        if digest and (not isinstance(digest, str) or not _HASH.fullmatch(digest)):
            raise ValueError("Invalid replay validation artifact hash")
        reports.append(ValidationReport(_id(item.get("status"), "UNKNOWN"), command, "",
                                        _id(item.get("revision"), "") if item.get("revision") else None, digest,
                                        _number(item.get("started", 0)), _number(item.get("elapsed", 0)), code, "", ""))
    for item in _items(document, "events", 256):
        events.append(ArenaEvent(_integer(item.get("sequence", 0)), _id(item.get("kind")), _id(item.get("actor")),
                                 _text(item.get("text", ""), 1024), _number(item.get("timestamp", 0))))
    verified = document.get("verified", False)
    if type(verified) is not bool:
        raise ValueError("Replay verification state must be boolean")
    verified = verified and bool(reports) and all(item.status == "SUCCEEDED" and item.exit_code == 0 and _HASH.fullmatch(item.artifact_hash) for item in reports)
    return ArenaSnapshot("replay", _id(document.get("status"), "UNKNOWN"), tuple(cards), tuple(events),
                         tuple(handoffs), tuple(artifacts), _number(document.get("elapsed", 0)),
                         _integer(document.get("max_handoffs", 0), 0, 10000), _number(document.get("deadline", 0)),
                         _text(document.get("reason", ""), 1024), verified, tuple(reports),
                         _id(document.get("phase"), "idle"), _integer(document.get("fix_cycles", 0), 0, 100))


def empty_arena():
    return ArenaSnapshot("replay", "NO_RECORDED_STATE", (), (), (), (), 0., 0, 0.,
                         "No managed state was recorded at this position.")


class PlaybackProjection:
    """Incremental in memory, with a reset on backward seek and no side effects."""
    def __init__(self, events, offsets, parameters=None):
        self._initial = safe_parameters(parameters or {})
        self._items = []
        for event, offset in zip(events, offsets):
            if event.topic == "arena.snapshot":
                document = event.payload.get("snapshot", event.payload)
                self._items.append((offset, "arena", arena_from_document(document)))
            elif event.topic == "presentation.changed":
                document = _bounded_document(event.payload)
                if type(document.get("schema_version", 1)) is not int or document.get("schema_version", 1) != 1:
                    raise ValueError("Unsupported presentation version")
                parameters = safe_parameters(document.get("parameters", document.get("preferences", {})))
                for key in ("visual_time", "protocol_time"):
                    if key in document:
                        _number(document[key])
                if "paused" in document and type(document["paused"]) is not bool:
                    raise ValueError("Recorded paused flag must be boolean")
                self._items.append((offset, "presentation", dict(document, parameters=parameters)))
            elif event.topic.startswith("arena.") or event.topic.startswith("agent.handoff."):
                self._items.append((offset, "event", event))
        self._offsets = tuple(item[0] for item in self._items)
        self._reset()

    def _reset(self):
        self._cursor = 0
        self._position = 0.
        self._arena = empty_arena()
        self._parameters = dict(self._initial)
        self._visual = self._protocol = self._anchor = 0.
        self._paused = False

    def _clock(self, position):
        elapsed = 0. if self._paused else max(0., position - self._anchor) * self._parameters.get("speed", 1.)
        return self._visual + elapsed, self._protocol + elapsed

    def seek(self, position):
        if position < self._position:
            self._reset()
        end = bisect_right(self._offsets, position)
        for offset, kind, value in self._items[self._cursor:end]:
            if kind == "arena":
                self._arena = value
            elif kind == "presentation":
                visual, protocol = self._clock(offset)
                self._visual = value.get("visual_time", visual)
                self._protocol = value.get("protocol_time", protocol)
                self._anchor = offset
                self._paused = value.get("paused", self._paused)
                self._parameters.update(value["parameters"])
            else:
                self._apply_event(value, offset)
        self._cursor, self._position = end, position

    def _apply_event(self, event, offset):
        payload = event.payload
        history = self._arena.events + (ArenaEvent(event.sequence, event.topic.split(".", 1)[-1],
                                                   _id(payload.get("actor"), "coordinator"),
                                                   _text(payload.get("text", payload.get("summary", "")), 1024), offset),)
        self._arena = replace(self._arena, events=history[-256:])
        if event.topic.startswith("agent.handoff."):
            message_id = payload.get("message_id")
            if not isinstance(message_id, str) or not _IDENTIFIER.fullmatch(message_id):
                return
            old = next((item for item in self._arena.handoffs if item.message_id == message_id), None)
            document = {"message_id": message_id, "sequence": payload.get("handoff_sequence", old.sequence if old else event.sequence),
                        "from": payload.get("from", old.sender if old else "unknown"),
                        "to": payload.get("to", old.recipient if old else "unknown"),
                        "summary": payload.get("summary", old.summary if old else ""),
                        "expected_result": payload.get("expected_result", old.expected_result if old else ""),
                        "status": payload.get("status", "ready" if event.topic.endswith("ready") else "unknown"),
                        "artifacts": _plain(payload.get("artifacts", []))}
            message = arena_from_document({"handoffs": [document]}).handoffs[0]
            values = tuple(item for item in self._arena.handoffs if item.message_id != message_id) + (message,)
            self._arena = replace(self._arena, handoffs=values[-64:])
        elif event.topic == "arena.turn":
            actor = _id(payload.get("actor"))
            status = payload.get("status")
            text = payload.get("text", "")
            if status is None and isinstance(text, str):
                prefix, separator, summary = text.partition(": ")
                if separator and prefix in ("SUCCEEDED", "FAILED", "CANCELLED", "DELIVERY_UNKNOWN", "RUNNING"):
                    status, text = prefix, summary
            if isinstance(status, str) and _IDENTIFIER.fullmatch(status):
                previous = next((item for item in self._arena.cards if item.id == actor), AgentCard(actor, actor.title(), ""))
                card = replace(previous, status=status, summary=_text(text, 2048))
                cards = tuple(item for item in self._arena.cards if item.id != actor) + (card,)
                self._arena = replace(self._arena, cards=cards[-8:])

    def arena_snapshot(self):
        return self._arena

    def presentation_snapshot(self):
        visual, protocol = self._clock(self._position)
        return PresentationSnapshot(_freeze(self._parameters), visual, protocol, self._paused, self._position)


def automatic_bookmarks(events, offsets):
    markers, previous, seen_handoffs, seen_failures, seen_events = [], {}, set(), set(), set()

    def add(kind, offset, event_id):
        if len(markers) < MAX_BOOKMARKS:
            markers.append(Bookmark("auto-" + str(len(markers)), offset, _GENERIC_LABELS[kind], kind, event_id, True))

    for event, offset in zip(events, offsets):
        identity = (event.source_id, event.source_session_id, event.id)
        if identity in seen_events:
            continue
        seen_events.add(identity)
        payload = event.payload
        if event.topic == "arena.snapshot":
            document = payload.get("snapshot", payload)
            for message in document.get("handoffs", []):
                identity = _id(message.get("message_id"))
                if identity not in seen_handoffs:
                    add("handoff", offset, event.id)
                    seen_handoffs.add(identity)
            for report in document.get("validation_reports", []):
                identity = (report.get("artifact_hash"), report.get("status"), report.get("exit_code"), report.get("started"))
                code = report.get("exit_code")
                failed = report.get("status") in ("FAILED", "TIMED_OUT", "CANCELLED") or type(code) is int and code != 0
                if failed and identity not in seen_failures:
                    add("failure", offset, event.id)
                    seen_failures.add(identity)
            continue
        kind = None
        code = payload.get("exit_code")
        if (payload.get("result") in ("failed", "error") or event.topic.endswith(".failed") or
                event.topic.endswith("completed") and type(code) is int and code != 0):
            kind = "failure"
        elif event.topic in ("agent.handoff.ready", "arena.handoff"):
            identity = _id(payload.get("message_id"), event.id)
            if identity not in seen_handoffs:
                kind = "handoff"
                seen_handoffs.add(identity)
        elif event.topic in ("site.surge", "audience.surge"):
            kind = "surge"
        elif event.topic == "metric.sample" and payload.get("key") in ("site.active_sessions_60s", "site.requests_per_sec"):
            value = payload.get("value")
            key = (event.source_id, payload["key"])
            before = previous.get(key)
            if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                threshold = 5. if payload["key"] == "site.requests_per_sec" else 10.
                if before is not None and value >= max(before * 1.5, before + threshold):
                    kind = "surge"
                previous[key] = value
        if event.topic == "recording.bookmark":
            raw_kind = payload.get("kind", "manual")
            kind = raw_kind if raw_kind in _GENERIC_LABELS else "manual"
            label = _text(payload.get("label", _GENERIC_LABELS[kind]), 160)
            markers.append(Bookmark(_id(payload.get("id"), "bookmark-" + str(len(markers))), offset,
                                    label, kind, event.id, False))
        elif kind:
            add(kind, offset, event.id)
        if len(markers) >= MAX_BOOKMARKS:
            break
    return tuple(markers)


def failure_offsets(events, offsets):
    """Keep failure navigation complete even after the visible marker cap."""
    result, seen, reports = [], set(), set()
    for event, offset in zip(events, offsets):
        identity = (event.source_id, event.source_session_id, event.id)
        if identity in seen:
            continue
        seen.add(identity)
        payload, failed = event.payload, False
        if event.topic == "arena.snapshot":
            for report in payload.get("snapshot", payload).get("validation_reports", []):
                identity = (report.get("artifact_hash"), report.get("status"), report.get("exit_code"), report.get("started"))
                code = report.get("exit_code")
                if identity not in reports and (report.get("status") in ("FAILED", "TIMED_OUT", "CANCELLED") or type(code) is int and code != 0):
                    failed = True
                reports.add(identity)
        else:
            code = payload.get("exit_code")
            failed = payload.get("result") in ("failed", "error") or event.topic.endswith(".failed") or type(code) is int and code != 0
        if failed and (not result or offset != result[-1]):
            result.append(offset)
    return tuple(result)


def scene_capsule(presentation, seed=42, bookmarks=()):
    """Share visual preferences and generic timeline markers, never user content."""
    if isinstance(presentation, PresentationSnapshot):
        document = presentation_document(dict(presentation.parameters), presentation.visual_time,
                                         presentation.protocol_time, presentation.paused)
    else:
        presentation = _bounded_document(presentation)
        document = presentation_document(presentation.get("parameters", presentation), presentation.get("visual_time", 0),
                                         presentation.get("protocol_time", 0), presentation.get("paused", False))
    seed = _integer(seed, -9223372036854775808, 9223372036854775807)
    document["parameters"]["seed"] = seed
    binding = document["parameters"].get("binding_config")
    if binding:
        # A shareable recipe describes a signal type, never a device/provider
        # identity. Custom source keys can encode device IDs, so omit those.
        canonical = {"site.active_sessions_60s", "site.authenticated_online", "site.active_users_30m",
                     "site.pageviews_per_min", "site.requests_per_sec", "body.heart_rate", "body.rr_interval",
                     "git.changed_files", "tests.latest", "agent.queue_length"}
        if binding.get("source_key") not in canonical:
            document["parameters"].pop("binding_config")
        else:
            binding["source_id"] = None
            if binding.get("expected_unit") not in (None, "sessions", "users", "views/min", "requests/s", "bpm", "ms", "ratio", "tasks", "files", "count", "events", "%", "seconds", "s"):
                binding["expected_unit"] = None
    markers = []
    for item in tuple(bookmarks)[:MAX_BOOKMARKS]:
        if isinstance(item, Bookmark):
            offset, kind = item.offset, item.kind
        elif isinstance(item, Mapping):
            offset, kind = _integer(item.get("offset_ms", 0), 0, 3153600000000) / 1000., item.get("kind", "manual")
        else:
            raise ValueError("Invalid capsule bookmark")
        if kind not in _GENERIC_LABELS:
            kind = "manual"
        markers.append({"offset_ms": int(_number(offset) * 1000), "kind": kind, "label": _GENERIC_LABELS[kind]})
    result = {"schema_version": 1, "format": "devos-scene-capsule", "seed": seed,
              "presentation": document, "bookmarks": markers}
    _bounded_document(result)
    return result


def export_scene_capsule(path, document_or_presentation, seed=None, bookmarks=()):
    if isinstance(document_or_presentation, Mapping) and document_or_presentation.get("format") == "devos-scene-capsule":
        source = _bounded_document(document_or_presentation)
        if type(source.get("schema_version")) is not int or source.get("schema_version") != 1:
            raise ValueError("Unsupported scene capsule version")
        document = scene_capsule(source.get("presentation", {}), source.get("seed", 42), source.get("bookmarks", ()))
    else:
        document = scene_capsule(document_or_presentation, 42 if seed is None else seed, bookmarks)
    encoded = (json.dumps(document, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode()
    if len(encoded) > 65536:
        raise ValueError("Scene capsule exceeds 64 KiB")
    path = Path(path).expanduser()
    with path.open("xb") as stream:
        stream.write(encoded)
    return path
