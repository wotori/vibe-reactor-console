"""Immutable telemetry snapshots and bounded asynchronous input adapters.

The UI only calls ``update`` and ``snapshot``. File, HTTP and Git I/O happen
in workers; a missing live signal never silently becomes a demonstration.
"""

from collections import OrderedDict, deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple
import urllib.error
import urllib.request

from .repository import inspect_repository


MAX_DOCUMENT_BYTES = 1024 * 1024
METRIC_DEFINITIONS = {
    "site.active_sessions_60s": ("sessions", 60),
    "site.authenticated_online": ("users", None),
    "site.active_users_30m": ("users", 1800),
    "site.pageviews_per_min": ("views/min", 60),
    "site.requests_per_sec": ("requests/s", 1),
    "body.heart_rate": ("bpm", None),
    "body.rr_interval": ("ms", None),
    "git.changed_files": ("files", None),
}
_TOPIC = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z0-9_-]+)+$")


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value, limit=128):
    return "".join(character if ord(character) >= 32 and ord(character) != 127 else "?"
                   for character in str(value))[:limit]


def freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    return value


def thaw(value):
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    return value


def _number(value, nullable=False):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Metric values must be finite numbers or null")
    return float(value)


def _timestamp(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Timestamp must be an ISO 8601 string or null")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return value


@dataclass(frozen=True)
class DataEvent:
    id: str
    topic: str
    source_id: str
    source_session_id: str
    sequence: int
    mode: str
    observed_at: Optional[str]
    received_at: str
    payload: Mapping[str, Any]
    schema_version: int = 1
    origin_mode: Optional[str] = None
    recorded_offset_ms: Optional[int] = None

    def __post_init__(self):
        object.__setattr__(self, "payload", freeze(self.payload))

    def to_dict(self):
        result = {"schema_version": 1, "id": self.id, "topic": self.topic,
                  "source_id": self.source_id, "source_session_id": self.source_session_id,
                  "sequence": self.sequence, "mode": self.mode,
                  "observed_at": self.observed_at, "received_at": self.received_at,
                  "payload": thaw(self.payload)}
        if self.origin_mode is not None:
            result["origin_mode"] = self.origin_mode
        if self.recorded_offset_ms is not None:
            result["recorded_offset_ms"] = self.recorded_offset_ms
        return result

    @classmethod
    def from_dict(cls, document):
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ValueError("Expected event schema_version 1")
        topic = document.get("topic", "")
        if not isinstance(topic, str) or not _TOPIC.fullmatch(topic) or len(topic) > 128:
            raise ValueError("Invalid event topic")
        fields = {}
        for key in ("id", "source_id", "source_session_id"):
            value = document.get(key)
            if not isinstance(value, str) or not value or len(value) > 128:
                raise ValueError("Invalid event " + key)
            fields[key] = _text(value)
        sequence = document.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("Event sequence must be a nonnegative integer")
        mode = document.get("mode")
        if mode not in ("demo", "live", "replay"):
            raise ValueError("Invalid event mode")
        origin = document.get("origin_mode")
        if mode == "replay" and origin not in ("demo", "live"):
            raise ValueError("Replay event needs original demo/live provenance")
        payload = document.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("Event payload must be an object")
        try:
            encoded = json.dumps(payload, allow_nan=False)
        except (ValueError, TypeError, RecursionError):
            raise ValueError("Event payload must contain finite JSON values")
        if len(encoded) > 65536:
            raise ValueError("Event payload exceeds 64 KiB")
        if topic == "metric.sample":
            if not isinstance(payload.get("key"), str) or not payload["key"] or len(payload["key"]) > 128:
                raise ValueError("Metric key is required")
            if not isinstance(payload.get("unit"), str) or not payload["unit"] or len(payload["unit"]) > 32:
                raise ValueError("Metric unit is required")
            quality = payload.get("quality", "ok")
            if quality not in ("ok", "stale", "unavailable", "error"):
                raise ValueError("Invalid metric quality")
            value = _number(payload.get("value"), nullable=True)
            if quality in ("unavailable", "error") and value is not None:
                raise ValueError("Unavailable/error measurements must have null value")
            window = payload.get("window_seconds")
            if window is not None and (isinstance(window, bool) or not isinstance(window, int) or window < 1):
                raise ValueError("Metric window must be a positive integer or null")
        elif topic == "site.pages":
            pages = payload.get("pages")
            if not isinstance(pages, list) or len(pages) > 200:
                raise ValueError("Page aggregates must be a bounded array")
            for item in pages:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                    raise ValueError("Invalid page aggregate")
                if _number(item.get("active_sessions")) < 0:
                    raise ValueError("Page counts cannot be negative")
        elif topic == "body.signal":
            intervals = payload.get("rr_intervals_ms", [])
            if not isinstance(intervals, list) or len(intervals) > 256:
                raise ValueError("RR intervals must be a bounded array")
            if any(not 100 <= _number(value) <= 5000 for value in intervals):
                raise ValueError("RR intervals must be 100..5000 ms")
        observed = _timestamp(document.get("observed_at"))
        received = _timestamp(document.get("received_at"))
        if received is None:
            raise ValueError("Event received_at is required")
        offset = document.get("recorded_offset_ms")
        if offset is not None and (isinstance(offset, bool) or not isinstance(offset, int) or offset < 0):
            raise ValueError("Invalid recording offset")
        return cls(topic=topic, sequence=sequence, mode=mode, observed_at=observed,
                   received_at=received, payload=payload, origin_mode=origin,
                   recorded_offset_ms=offset, **fields)


@dataclass(frozen=True)
class MetricSample:
    key: str
    value: Optional[float]
    unit: str
    source_id: str
    source_session_id: str
    sequence: int
    observed_at: Optional[str]
    received_monotonic: float
    mode: str
    quality: str = "ok"
    measurement_id: Optional[str] = None
    initial_age_seconds: Optional[float] = None
    time_basis: str = "received_only"
    origin_mode: Optional[str] = None
    window_seconds: Optional[int] = None
    age_seconds: float = 0.0
    time_quality: str = "unknown"


@dataclass(frozen=True)
class ProviderStatus:
    source_id: str
    mode: str
    quality: str = "unavailable"
    last_received_monotonic: Optional[float] = None
    error: str = ""
    failures: int = 0
    backpressure: bool = False
    age_seconds: Optional[float] = None


@dataclass(frozen=True)
class MetricHistory:
    key: str
    samples: Tuple[MetricSample, ...]


@dataclass(frozen=True)
class TelemetrySnapshot:
    metrics: Tuple[MetricSample, ...] = ()
    events: Tuple[DataEvent, ...] = ()
    providers: Tuple[ProviderStatus, ...] = ()
    histories: Tuple[MetricHistory, ...] = ()
    elapsed: float = 0.0
    demo_finished: bool = False
    pages: Tuple[Tuple[str, float], ...] = ()
    rr_intervals: Tuple[float, ...] = ()
    heart_contact: str = "unknown"
    heart_timing_basis: str = "received_only"
    repository: Any = None
    dropped_gauges: int = 0
    rejected_events: int = 0

    def metric(self, key):
        return next((item for item in self.metrics if item.key == key), None)

    def history(self, key):
        match = next((item for item in self.histories if item.key == key), None)
        return match.samples if match else ()

    @property
    def mode(self):
        modes = set(item.mode for item in self.metrics)
        return next(iter(modes)) if len(modes) == 1 else "mixed" if modes else "demo"


class BoundedInbox:
    """Latest-value gauge lane plus an ordered lifecycle lane; no silent loss."""

    def __init__(self, capacity=1024, gauge_capacity=256):
        self.capacity, self.gauge_capacity = capacity, gauge_capacity
        self._events = deque()
        self._gauges = OrderedDict()
        self._lock = threading.Lock()
        self.backpressure = False
        self.coalesced = 0

    def put(self, event):
        with self._lock:
            if event.topic == "metric.sample":
                key = (event.source_id, event.source_session_id, event.payload["key"])
                previous = self._gauges.get(key)
                if previous is not None and previous.sequence >= event.sequence:
                    return True
                if previous is not None:
                    self.coalesced += 1
                elif len(self._gauges) >= self.gauge_capacity:
                    self.backpressure = True
                    return False
                self._gauges[key] = event
                return True
            if len(self._events) >= self.capacity:
                self.backpressure = True
                return False
            self._events.append(event)
            return True

    def drain(self, limit=256, budget=0.0015):
        result, deadline = [], time.monotonic() + budget
        with self._lock:
            # Both lanes preserve sequence within a source before the store sees them.
            merged = list(self._events) + list(self._gauges.values())
            merged.sort(key=lambda event: (event.recorded_offset_ms if event.recorded_offset_ms is not None else 0,
                                          event.source_id, event.source_session_id, event.sequence))
            for event in merged:
                if len(result) >= limit or time.monotonic() >= deadline:
                    break
                result.append(event)
                if event.topic == "metric.sample":
                    self._gauges.pop((event.source_id, event.source_session_id, event.payload["key"]), None)
                else:
                    self._events.remove(event)
            if len(self._events) < self.capacity and len(self._gauges) < self.gauge_capacity:
                self.backpressure = False
        return tuple(result)

    def __len__(self):
        with self._lock:
            return len(self._events) + len(self._gauges)


class _JSONAdapter:
    """Normalize a generic event, site endpoint, metric map, or pulse bridge."""

    def __init__(self, source_id, clock=time.monotonic, wall_clock=utc_now):
        self.source_id, self.clock, self.wall_clock = source_id, clock, wall_clock
        self.session = source_id + "-run-" + str(time.time_ns())
        self.sequence = 0

    def event(self, topic, payload, observed=None, measurement_id=None):
        self.sequence += 1
        if measurement_id is not None:
            payload = dict(payload, measurement_id=str(measurement_id))
        event = DataEvent("{}-{}".format(self.session, self.sequence), topic,
                          self.source_id, self.session, self.sequence, "live", observed,
                          self.wall_clock(), payload)
        return DataEvent.from_dict(event.to_dict())

    def normalize(self, document):
        if isinstance(document, list):
            if len(document) > 1024:
                raise ValueError("At most 1024 events per document")
            return tuple(event for item in document for event in self.normalize(item))
        if not isinstance(document, dict):
            raise ValueError("Expected JSON object or event array")
        if "topic" in document:
            event = DataEvent.from_dict(document)
            if event.mode != "live":
                raise ValueError("A live input must label its events live")
            # The configured connection is the selected provider identity. Keep
            # upstream identity/session for provenance and sequence deduplication.
            original = event.source_id
            payload = dict(event.payload)
            if original != self.source_id:
                payload["upstream_source_id"] = original
            return (replace(event, source_id=self.source_id,
                            source_session_id=(original + ":" + event.source_session_id)[:128],
                            payload=payload),)
        if document.get("schema_version", 1) != 1:
            raise ValueError("Expected schema_version 1")
        if "events" in document:
            return self.normalize(document["events"])
        observed = _timestamp(document.get("observed_at"))
        measurement_id = document.get("measurement_id")
        quality = "ok" if document.get("collection_status", "ok") == "ok" else "unavailable"
        events = []

        def metric(key, value, unit=None, window=None):
            default_unit, default_window = METRIC_DEFINITIONS.get(key, ("count", None))
            numeric = _number(value, nullable=True)
            payload = {"key": key, "value": numeric if quality == "ok" else None,
                       "unit": unit or default_unit, "quality": quality if numeric is not None else "unavailable",
                       "window_seconds": window if window is not None else default_window}
            if key.startswith("body.") and "heart_rate_bpm" in document:
                payload["time_basis"] = _text(document.get("timing_basis", "notification_received"), 64)
            events.append(self.event("metric.sample", payload, observed, measurement_id))

        site_fields = (("active_sessions", "site.active_sessions_60s"),
                       ("authenticated_online", "site.authenticated_online"),
                       ("active_users_30m", "site.active_users_30m"),
                       ("pageviews_per_minute", "site.pageviews_per_min"),
                       ("requests_per_second", "site.requests_per_sec"))
        for field, key in site_fields:
            if field in document:
                window = document.get("active_window_seconds", 60) if field == "active_sessions" else None
                if isinstance(window, bool) or window is not None and (not isinstance(window, int) or window < 1):
                    raise ValueError("Invalid aggregation window")
                # A differently defined session window has its own honest key.
                if field == "active_sessions" and window != 60:
                    key = "site.active_sessions_{}s".format(window)
                metric(key, document[field], unit="sessions" if field == "active_sessions" else None, window=window)
        if "pages" in document:
            pages = document["pages"]
            if not isinstance(pages, list) or len(pages) > 200:
                raise ValueError("pages must be a bounded array")
            cleaned = []
            for page in pages:
                if not isinstance(page, dict) or not isinstance(page.get("path"), str):
                    raise ValueError("Invalid page aggregate")
                count = _number(page.get("active_sessions"))
                if count < 0:
                    raise ValueError("Page counts cannot be negative")
                # Query strings/fragments can contain personal data and aren't page labels.
                path = _text(page["path"].split("?", 1)[0].split("#", 1)[0], 160)
                cleaned.append({"path": path, "active_sessions": count})
            events.append(self.event("site.pages", {"pages": cleaned}, observed, measurement_id))
        if "heart_rate_bpm" in document:
            bpm = _number(document["heart_rate_bpm"], nullable=True)
            if bpm is not None and not 15 <= bpm <= 300:
                raise ValueError("Heart rate must be 15..300 BPM or null")
            # Notification receipt is not a hardware measurement timestamp.
            observed = _timestamp(document.get("observed_at") or document.get("received_at"))
            metric("body.heart_rate", bpm)
            intervals = document.get("rr_intervals_ms", [])
            if not isinstance(intervals, list) or len(intervals) > 256:
                raise ValueError("RR intervals must be a bounded array")
            intervals = tuple(_number(value) for value in intervals)
            if any(not 100 <= value <= 5000 for value in intervals):
                raise ValueError("RR intervals must be 100..5000 ms")
            if intervals:
                metric("body.rr_interval", intervals[-1])
            events.append(self.event("body.signal", {
                "rr_intervals_ms": intervals, "sensor_contact": _text(document.get("sensor_contact", "unknown"), 32),
                "timing_basis": _text(document.get("timing_basis", "notification_received"), 64),
                "measurement_id": str(measurement_id) if measurement_id is not None else observed,
            }, observed))
        raw_metrics = document.get("metrics", {})
        if not isinstance(raw_metrics, (dict, list)):
            raise ValueError("metrics must be a map or array")
        if len(raw_metrics) > 128:
            raise ValueError("At most 128 metrics per document")
        iterator = raw_metrics.items() if isinstance(raw_metrics, dict) else ((item.get("key"), item) for item in raw_metrics if isinstance(item, dict))
        for key, raw in iterator:
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ValueError("Invalid metric key")
            if isinstance(raw, dict):
                metric(key, raw.get("value"), raw.get("unit"), raw.get("window_seconds"))
            else:
                metric(key, raw)
        if not events:
            raise ValueError("JSON document contains no supported measurements")
        return tuple(events)


def _parse_json_bytes(raw):
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("Telemetry document exceeds 1 MiB")
    text = raw.decode("utf-8-sig")
    try:
        return json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON number")))
    except json.JSONDecodeError:
        # A file may also contain a bounded batch of normalized JSONL events.
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines or len(lines) > 1024:
            raise ValueError("Invalid JSON/JSONL telemetry")
        return [json.loads(line) for line in lines]


class JSONFileProvider(_JSONAdapter):
    def __init__(self, path, source_id="json-file", **kwargs):
        super().__init__(source_id, **kwargs)
        self.path = Path(path).expanduser()

    def poll(self):
        if not self.path.is_file():
            raise ValueError("Snapshot source must be a regular file; use a stream provider for pipes")
        with self.path.open("rb") as handle:
            raw = handle.read(MAX_DOCUMENT_BYTES + 1)
        return self.normalize(_parse_json_bytes(raw))

    def close(self):
        pass


class JSONStreamProvider(_JSONAdapter):
    """Follow a JSONL file or FIFO without blocking while waiting for a newline."""

    def __init__(self, path, source_id="json-stream", **kwargs):
        super().__init__(source_id, **kwargs)
        self.path, self._fd, self._identity, self._offset = Path(path).expanduser(), None, None, 0
        self._buffer, self._lock = bytearray(), threading.Lock()

    def poll(self):
        with self._lock:
            info = self.path.stat()
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISFIFO(info.st_mode)):
                raise ValueError("JSON stream must be a regular file or FIFO")
            identity = (info.st_dev, info.st_ino)
            if self._fd is None or identity != self._identity or stat.S_ISREG(info.st_mode) and info.st_size < self._offset:
                if self._fd is not None:
                    os.close(self._fd)
                self._fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
                self._identity, self._offset, self._buffer = identity, 0, bytearray()
            try:
                raw = os.read(self._fd, 65536)
            except BlockingIOError:
                raw = b""
            self._offset += len(raw)
            self._buffer.extend(raw)
            if len(self._buffer) > 131072:
                self._buffer.clear()
                raise ValueError("JSONL stream line exceeds 128 KiB")
            events, lines = [], 0
            while b"\n" in self._buffer and lines < 256:
                raw, _, remaining = self._buffer.partition(b"\n")
                self._buffer = bytearray(remaining)
                lines += 1
                if raw.strip():
                    events.extend(self.normalize(_parse_json_bytes(raw)))
                if len(events) > 1024:
                    raise ValueError("Stream batch exceeds 1024 events")
            return tuple(events)

    def close(self):
        with self._lock:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None


class HTTPJSONProvider(_JSONAdapter):
    def __init__(self, url, source_id="http-json", timeout=2.0, token_env=None, **kwargs):
        super().__init__(source_id, **kwargs)
        if not str(url).startswith(("http://", "https://")):
            raise ValueError("Telemetry URL must use http:// or https://")
        self.url, self.timeout, self.token_env = str(url), max(0.05, timeout), token_env

    def poll(self):
        headers = {"Accept": "application/json", "User-Agent": "DEVOS/telemetry"}
        if self.token_env:
            token = os.environ.get(self.token_env)
            if not token:
                raise ValueError("Telemetry token environment variable is unset")
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(self.url, headers=headers)
        deadline = time.monotonic() + self.timeout
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            chunks, count = [], 0
            while count <= MAX_DOCUMENT_BYTES:
                if time.monotonic() >= deadline:
                    raise TimeoutError("HTTP telemetry deadline exceeded")
                read = getattr(response, "read1", response.read)
                chunk = read(min(65536, MAX_DOCUMENT_BYTES + 1 - count))
                if not chunk:
                    break
                chunks.append(chunk)
                count += len(chunk)
            raw = b"".join(chunks)
        return self.normalize(_parse_json_bytes(raw))

    def close(self):
        pass


class _Worker:
    def __init__(self, provider, inbox, interval, status_callback, clock):
        self.provider, self.inbox = provider, inbox
        self.interval, self.status_callback, self.clock = interval, status_callback, clock
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="devos-" + provider.source_id, daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        failures, pending = 0, deque()
        while not self.stop.is_set():
            if pending:
                while pending and not self.stop.is_set():
                    if self.inbox.put(pending[0]):
                        pending.popleft()
                    else:
                        self.status_callback(ProviderStatus(self.provider.source_id, "live", "error",
                                                           error="BACKPRESSURE: input paused", backpressure=True))
                        self.stop.wait(0.02)
                if self.stop.is_set():
                    break
            try:
                events = self.provider.poll()
                if len(events) > 1024:
                    raise ValueError("Provider batch exceeds 1024 events")
                pending.extend(events)
                failures = 0
                self.status_callback(ProviderStatus(self.provider.source_id, "live", "ok", self.clock()))
                while pending:
                    if not self.inbox.put(pending[0]):
                        break
                    pending.popleft()
                delay = self.interval
            except Exception as error:
                failures += 1
                # Never include credentials/URLs or an HTTP response body in UI diagnostics.
                if isinstance(error, urllib.error.HTTPError):
                    message = "HTTP {}".format(error.code)
                elif isinstance(error, (OSError, urllib.error.URLError)):
                    message = type(error).__name__ + ": source unavailable"
                else:
                    message = _text(str(error), 180)
                self.status_callback(ProviderStatus(self.provider.source_id, "live", "error",
                                                   error=message, failures=failures))
                # Deterministic small jitter avoids identical clients hammering in lockstep.
                jitter = 0.9 + (sum(map(ord, self.provider.source_id)) % 20) / 100.0
                delay = min(60.0, self.interval * 2 ** min(failures - 1, 6)) * jitter
            self.stop.wait(max(0.02, delay))

    def close(self):
        self.stop.set()
        self.provider.close()
        # A broken third-party poll cannot block UI teardown forever.
        self.thread.join(timeout=0.1)


class TelemetryHub:
    def __init__(self, seed=42, source_file=None, source_url=None, heart_file=None,
                 git_root=None, interval=2.0, stale_after=15.0, unavailable_after=60.0,
                 timeout=2.0, token_env=None, start_workers=True, clock=time.monotonic,
                 wall_clock=utc_now, inbox_capacity=1024, journal_capacity=2000,
                 providers=(), source_selection=None, demo=True,
                 empty_live_placeholders=True, source_stream=None):
        if sum(bool(value) for value in (source_file, source_url, source_stream)) > 1:
            raise ValueError("Select one telemetry file, stream or URL")
        if not 0 < stale_after < unavailable_after:
            raise ValueError("TTL requires 0 < stale_after < unavailable_after")
        self.seed, self.clock, self.wall_clock = int(seed), clock, wall_clock
        self.stale_after, self.unavailable_after = float(stale_after), float(unavailable_after)
        self.inbox = BoundedInbox(inbox_capacity)
        self._journal = deque(maxlen=journal_capacity)
        self._metrics, self._histories, self._sequences = {}, {}, {}
        self._measurement_ids, self._retired_sessions, self._source_sessions = {}, set(), {}
        self._statuses, self._status_lock = {}, threading.Lock()
        self._workers, self._providers = [], []
        self._elapsed, self._demo_tick, self._rejected, self._closed = 0.0, -1, 0, False
        self._demo = bool(demo)
        self._empty_live_placeholders = bool(empty_live_placeholders)
        self._source_selection = dict(source_selection or {})
        self._pages, self._rr, self._heart_contact, self._heart_timing = (), deque(maxlen=256), "unknown", "received_only"
        self._repository, self._git_pending = None, None
        self._last_snapshot = None
        self._listeners = []
        self._demo_disabled_prefixes = set()
        kwargs = {"clock": clock, "wall_clock": wall_clock}
        if source_file or source_url or source_stream:
            if source_file:
                provider = JSONFileProvider(source_file, **kwargs)
            elif source_stream:
                provider = JSONStreamProvider(source_stream, **kwargs)
            else:
                provider = HTTPJSONProvider(source_url, timeout=timeout, token_env=token_env, **kwargs)
            self._providers.append(provider)
            self._demo_disabled_prefixes.add("site.")
            for key in METRIC_DEFINITIONS:
                if key.startswith("site."):
                    self._source_selection.setdefault(key, provider.source_id)
        if heart_file:
            provider = JSONFileProvider(heart_file, source_id="heart-rate-bridge", **kwargs)
            self._providers.append(provider)
            self._demo_disabled_prefixes.add("body.")
            for key in METRIC_DEFINITIONS:
                if key.startswith("body."):
                    self._source_selection.setdefault(key, provider.source_id)
        self._providers.extend(providers)
        if git_root:
            self._providers.append(_GitProvider(git_root, self._set_repository, **kwargs))
            self._demo_disabled_prefixes.add("git.")
            self._source_selection.setdefault("git.changed_files", "git")
        for provider in self._providers:
            self._set_status(ProviderStatus(provider.source_id, "live"))
            worker = _Worker(provider, self.inbox, max(0.05, interval), self._set_status, clock)
            self._workers.append(worker)
            if start_workers:
                worker.start()
        self._started = bool(start_workers)

    @property
    def elapsed(self):
        return self._elapsed

    def start(self):
        if not self._started and not self._closed:
            for worker in self._workers:
                worker.start()
            self._started = True

    def _set_status(self, status):
        with self._status_lock:
            old = self._statuses.get(status.source_id)
            if old and status.last_received_monotonic is None:
                status = replace(status, last_received_monotonic=old.last_received_monotonic)
            self._statuses[status.source_id] = status

    def _set_repository(self, snapshot):
        with self._status_lock:
            self._git_pending = snapshot

    def add_listener(self, listener):
        """Observe accepted events (e.g. an explicit Recorder) on the UI thread."""
        self._listeners.append(listener)

    def submit(self, event):
        try:
            event = DataEvent.from_dict(event.to_dict() if isinstance(event, DataEvent) else event)
            return self.inbox.put(event)
        except (ValueError, TypeError, KeyError):
            self._rejected += 1
            return False

    def select_source(self, key, source_id):
        if key not in METRIC_DEFINITIONS and key not in (sample.key for sample in self._metrics.values()):
            raise ValueError("Unknown metric: " + key)
        if source_id is None:
            self._source_selection.pop(key, None)
        else:
            self._source_selection[key] = str(source_id)

    def _demo_event(self, topic, payload, tick, source="demo"):
        sequence = self._demo_sequence.get(source, 0) + 1
        self._demo_sequence[source] = sequence
        # Fixed timeline + seed gives reproducible event IDs and measurements.
        stamp = datetime.fromtimestamp(1793448000 + tick, timezone.utc).isoformat().replace("+00:00", "Z")
        return DataEvent("{}-{}-{}".format(source, self.seed, sequence), topic, source,
                         "{}-seed-{}".format(source, self.seed), sequence, "demo", stamp,
                         stamp, payload, recorded_offset_ms=tick * 1000)

    def _produce_demo(self):
        if not hasattr(self, "_demo_sequence"):
            self._demo_sequence = {}
        target = min(60, int(self._elapsed))
        for tick in range(self._demo_tick + 1, target + 1):
            phase = self.seed * 0.037 + tick * 0.13
            online = max(0, round(27 + 17 * math.sin(phase) + 6 * math.sin(tick * 0.51)))
            values = {"site.active_sessions_60s": online,
                      "site.pageviews_per_min": round(online * 2.8 + 8),
                      "site.requests_per_sec": round(online * 0.37 + 2, 1),
                      "body.heart_rate": round(72 + 5 * math.sin(phase * 0.7)),
                      "body.rr_interval": round(830 + 20 * math.sin(phase * 1.7)),
                      "git.changed_files": 3 + tick // 15}
            for key, value in values.items():
                if any(key.startswith(prefix) for prefix in self._demo_disabled_prefixes):
                    continue
                unit, window = METRIC_DEFINITIONS[key]
                source = "demo-body" if key.startswith("body.") else "demo-site" if key.startswith("site.") else "demo-git"
                self.submit(self._demo_event("metric.sample", {"key": key, "value": value,
                            "unit": unit, "quality": "ok", "window_seconds": window}, tick, source))
            if "site." not in self._demo_disabled_prefixes:
                pages = ({"path": "/", "active_sessions": round(online * 0.4)},
                         {"path": "/ekza", "active_sessions": round(online * 0.35)},
                         {"path": "/devos", "active_sessions": round(online * 0.2)})
                self.submit(self._demo_event("site.pages", {"pages": pages}, tick, "demo-site"))
            if "body." not in self._demo_disabled_prefixes:
                self.submit(self._demo_event("body.signal", {"rr_intervals_ms": (values["body.rr_interval"],),
                            "sensor_contact": "demo", "timing_basis": "synthetic_demo",
                            "measurement_id": "demo-beat-{}".format(tick)}, tick, "demo-body"))
            story = _DEMO_STORY.get(tick)
            if story:
                topic, payload = story
                self.submit(self._demo_event(topic, payload, tick, "demo-arena"))
            if tick in (5, 19, 34, 50):
                self.submit(self._demo_event("visual.cue", {"cue": "traffic-surge", "duration_ms": 1200,
                            "strength": 0.75}, tick, "demo-director"))
        self._demo_tick = target

    def _accept(self, event, now):
        source, session = event.source_id, event.source_session_id
        if (source, session) in self._retired_sessions:
            return False
        previous_session = self._source_sessions.get(source)
        if previous_session is None and len(self._source_sessions) >= 512:
            self._rejected += 1
            return False
        if previous_session is not None and previous_session != session:
            if len(self._retired_sessions) >= 1024:
                # Resetting dedup state would let an old handoff execute again.
                # Reject additional producer sessions until a fresh hub instead.
                self._rejected += 1
                return False
            self._retired_sessions.add((source, previous_session))
            self._sequences.pop((source, previous_session), None)
        self._source_sessions[source] = session
        sequence_key = (source, session)
        if event.sequence <= self._sequences.get(sequence_key, -1):
            return False
        self._sequences[sequence_key] = event.sequence
        if event.topic == "metric.sample":
            payload = event.payload
            key = payload["key"]
            slot = (source, key)
            identity = payload.get("measurement_id") or event.observed_at
            old = self._metrics.get(slot)
            if old and identity is not None and self._measurement_ids.get(slot) == (session, identity):
                # A successful poll of a cache is not a new physical measurement.
                return False
            if old and old.observed_at and event.observed_at and old.source_session_id == session:
                if datetime.fromisoformat(event.observed_at.replace("Z", "+00:00")) < datetime.fromisoformat(old.observed_at.replace("Z", "+00:00")):
                    return False
            initial_age, time_quality = None, "unknown"
            if event.observed_at and event.mode == "live":
                wall = datetime.fromisoformat(self.wall_clock().replace("Z", "+00:00"))
                measured = datetime.fromisoformat(event.observed_at.replace("Z", "+00:00"))
                difference = (wall - measured).total_seconds()
                initial_age = max(0.0, difference)
                time_quality = "clock_skew" if difference < -5 else "source_timestamp"
            if event.mode in ("demo", "replay"):
                initial_age, time_quality = 0.0, event.mode + "_timeline"
            sample = MetricSample(key, _number(payload.get("value"), nullable=True), _text(payload["unit"], 32),
                                  source, session, event.sequence, event.observed_at, now, event.mode,
                                  payload.get("quality", "ok"), str(identity) if identity is not None else None,
                                  initial_age, _text(payload.get("time_basis", "source_timestamp" if event.observed_at else "received_only"), 64),
                                  event.origin_mode, payload.get("window_seconds"), 0.0, time_quality)
            if len(self._metrics) >= 512 and slot not in self._metrics:
                self._rejected += 1
                return False
            self._metrics[slot], self._measurement_ids[slot] = sample, (session, identity)
            history = self._histories.setdefault(slot, deque(maxlen=180))
            history.append(sample)
            if len(self._histories) > 512:
                self._histories.pop(next(iter(self._histories)))
        elif event.topic == "site.pages":
            selected = self._source_selection.get("site.active_sessions_60s")
            if selected is None or selected == source:
                pages = event.payload.get("pages", ())
                self._pages = tuple((_text(item["path"], 160), float(item["active_sessions"])) for item in pages[:200])
        elif event.topic == "body.signal":
            selected = self._source_selection.get("body.heart_rate")
            if selected is None or selected == source:
                identity = event.payload.get("measurement_id") or event.observed_at
                identity_key = (source, "body.signal")
                if identity is not None and self._measurement_ids.get(identity_key) == (session, identity):
                    return False
                self._measurement_ids[identity_key] = (session, identity)
                self._rr.extend(float(item) for item in event.payload.get("rr_intervals_ms", ())[:256])
                self._heart_contact = _text(event.payload.get("sensor_contact", "unknown"), 32)
                self._heart_timing = _text(event.payload.get("timing_basis", "received_only"), 64)
        self._journal.append(event)
        if len(self._measurement_ids) > 1024:
            self._measurement_ids.pop(next(iter(self._measurement_ids)))
        for listener in self._listeners:
            listener(event, self._elapsed)
        return True

    def update(self, dt=0.0, demo_paused=False):
        if self._closed:
            return self.snapshot()
        if not demo_paused:
            self._elapsed += max(0.0, float(dt))
        if self._demo:
            self._produce_demo()
        now = self.clock()
        for event in self.inbox.drain():
            try:
                self._accept(event, now)
            except (ValueError, TypeError, KeyError, OverflowError):
                self._rejected += 1
        with self._status_lock:
            if self._git_pending is not None:
                self._repository, self._git_pending = self._git_pending, None
        return self.snapshot()

    def _aged(self, sample, now):
        age = (sample.initial_age_seconds or 0.0) + max(0.0, now - sample.received_monotonic)
        stale, unavailable = (3.0, 10.0) if sample.key.startswith("body.") and sample.mode == "live" else (self.stale_after, self.unavailable_after)
        quality, value = sample.quality, sample.value
        if age >= unavailable:
            quality, value = "unavailable", None
        elif age >= stale and quality == "ok":
            quality = "stale"
        return replace(sample, age_seconds=age, quality=quality, value=value)

    def snapshot(self):
        now = self.clock()
        chosen = {}
        for (source, key), sample in self._metrics.items():
            requested = self._source_selection.get(key)
            if requested is not None and source != requested:
                continue
            existing = chosen.get(key)
            if existing is None or sample.received_monotonic >= existing.received_monotonic:
                chosen[key] = self._aged(sample, now)
        # Missing explicitly live-selected keys are unknown, never fake zero.
        for key, source in self._source_selection.items():
            if key not in chosen:
                unit, window = METRIC_DEFINITIONS.get(key, ("count", None))
                chosen[key] = MetricSample(key, None, unit, source, "--", 0, None,
                                           now, "live", "unavailable", window_seconds=window)
        if not self._demo and self._empty_live_placeholders:
            for key, (unit, window) in METRIC_DEFINITIONS.items():
                if key not in chosen and key.startswith(("site.", "body.")):
                    source = "unconfigured-heart" if key.startswith("body.") else "unconfigured-site"
                    chosen[key] = MetricSample(key, None, unit, source, "--", 0, None,
                                               now, "live", "unavailable", window_seconds=window)
        histories = tuple(MetricHistory(key, tuple(self._histories.get((sample.source_id, key), ())))
                          for key, sample in sorted(chosen.items()))
        with self._status_lock:
            statuses = tuple(self._statuses.values())
        providers = []
        for status in statuses:
            age = max(0.0, now - status.last_received_monotonic) if status.last_received_monotonic is not None else None
            quality = status.quality
            if age is not None and quality == "ok":
                quality = "unavailable" if age >= self.unavailable_after else "stale" if age >= self.stale_after else "ok"
            providers.append(replace(status, quality=quality, age_seconds=age,
                                     backpressure=status.backpressure or self.inbox.backpressure))
        if self._demo:
            providers.append(ProviderStatus("demo", "demo", "ok" if self._elapsed <= 60 else "stale", age_seconds=max(0.0, self._elapsed - 60)))
        return TelemetrySnapshot(tuple(chosen[key] for key in sorted(chosen)), tuple(self._journal),
                                 tuple(sorted(providers, key=lambda status: status.source_id)), histories,
                                 self._elapsed, self._elapsed >= 60, self._pages, tuple(self._rr),
                                 self._heart_contact, self._heart_timing, self._repository,
                                 self.inbox.coalesced, self._rejected)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._started:
            for worker in self._workers:
                worker.close()


class _GitProvider(_JSONAdapter):
    def __init__(self, root, callback, **kwargs):
        super().__init__("git", **kwargs)
        self.root, self.callback = root, callback

    def poll(self):
        snapshot = inspect_repository(self.root)
        self.callback(snapshot)
        if snapshot.quality != "ok":
            raise ValueError(snapshot.error)
        return (self.event("metric.sample", {"key": "git.changed_files", "value": snapshot.changed_files,
                                             "unit": "files", "quality": "ok", "window_seconds": None}, self.wall_clock()),)

    def close(self):
        pass


_DEMO_STORY = {
    0: ("demo.mission.started", {"mission": "avatar-parser", "state": "running", "label": "DEMO / finite 60s"}),
    2: ("agent.task.started", {"agent": "codex", "role": "builder", "text": "Implement immutable avatar revision parser"}),
    5: ("agent.task.started", {"agent": "claude", "role": "challenger", "text": "Design boundary and malformed-byte cases"}),
    9: ("agent.handoff.ready", {"from": "codex", "to": "claude", "text": "Parser patch ready; check length bounds", "artifact": "demo:patch-v1"}),
    15: ("demo.build.finished", {"run_id": "demo-test-1", "result": "failed", "tests_passed": 14, "tests_total": 15, "text": "Zero-length rendition exposes a boundary case"}),
    19: ("agent.handoff.ready", {"from": "claude", "to": "codex", "text": "Add zero-byte rejection before hash validation", "artifact": "demo:review-v1"}),
    24: ("agent.task.started", {"agent": "codex", "role": "builder", "text": "Apply explicit bounds guard and add regression case"}),
    32: ("agent.handoff.ready", {"from": "codex", "to": "claude", "text": "Bounds guard added; review fixed patch", "artifact": "demo:patch-v2"}),
    40: ("demo.build.finished", {"run_id": "demo-test-2", "result": "passed", "tests_passed": 16, "tests_total": 16, "text": "Synthetic regression suite passed"}),
    48: ("agent.handoff.ready", {"from": "claude", "to": "codex", "text": "Review complete: fixture invariants hold", "artifact": "demo:review-v2"}),
    54: ("demo.mission.completed", {"mission": "avatar-parser", "state": "completed", "text": "Demo mission complete; no commands were executed"}),
    60: ("demo.scenario.finished", {"state": "finished", "text": "60-second demo complete; restart to replay"}),
}
