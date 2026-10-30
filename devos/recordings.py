"""Opt-in, bounded event recordings and a side-effect-free replay playhead."""

from bisect import bisect_right
from collections import deque
from dataclasses import dataclass, replace
import gzip
import json
from pathlib import Path
import queue
import threading
from typing import Tuple

from .telemetry import DataEvent, TelemetryHub, utc_now


@dataclass(frozen=True)
class RecordingStatus:
    path: str
    active: bool
    events: int = 0
    bytes_written: int = 0
    pending: int = 0
    error: str = ""
    parts: Tuple[str, ...] = ()


class Recorder:
    """Asynchronous JSONL(.gz) writer; creation requires an explicit destination.

    Personal pulse, terminal text and prompts are separate opt-ins. ``record``
    only enqueues a bounded immutable event and never opens/writes a file.
    Existing files are never overwritten.
    """

    def __init__(self, path, seed=42, parameters=None, include_pulse=False,
                 include_terminal=False, include_prompts=False, max_bytes=50 * 1024 * 1024,
                 queue_capacity=1024, checkpoint_seconds=10.0):
        self.path = Path(path).expanduser()
        if self.path.exists():
            raise FileExistsError("Recording already exists: " + str(self.path))
        if not self.path.parent.is_dir():
            raise ValueError("Recording directory does not exist")
        # Serializable scene parameters only; commands/tokens/paths aren't metadata.
        banned = ("token", "secret", "password", "credential", "command", "argv", "cwd", "path",
                  "device", "file", "url", "root", "endpoint", "user_id", "session_id")

        def clean_parameters_value(value):
            if isinstance(value, dict):
                result = {}
                for key, item in value.items():
                    if any(word in str(key).lower() for word in banned):
                        continue
                    if isinstance(item, str) and item.startswith(("/", "~", "http://", "https://")):
                        continue
                    result[key] = clean_parameters_value(item)
                return result
            if isinstance(value, (tuple, list)):
                return [clean_parameters_value(item) for item in value
                        if not isinstance(item, str) or not item.startswith(("/", "~", "http://", "https://"))]
            return value

        clean_parameters = clean_parameters_value(dict(parameters or {}))
        json.dumps(clean_parameters, allow_nan=False)
        self._manifest = {"record_type": "manifest", "schema_version": 1,
                          "format": "devos-event-log", "created_at": utc_now(),
                          "seed": int(seed), "parameters": clean_parameters,
                          "categories": {"pulse": bool(include_pulse),
                                         "terminal": bool(include_terminal),
                                         "prompts": bool(include_prompts)}}
        self.include_pulse, self.include_terminal, self.include_prompts = include_pulse, include_terminal, include_prompts
        self.max_bytes = max(512, int(max_bytes))
        self._queue = queue.Queue(maxsize=max(1, int(queue_capacity)))
        self._stop, self._ready = threading.Event(), threading.Event()
        self._lock = threading.Lock()
        self._error, self._events, self._bytes, self._parts = "", 0, 0, []
        self._closed, self._last_offset = False, 0
        self._checkpoint_interval, self._last_checkpoint = max(1.0, checkpoint_seconds), -float("inf")
        self._state = {}
        self._thread = threading.Thread(target=self._write, name="devos-recorder", daemon=True)
        self._thread.start()
        # Startup verifies permissions before the UI advertises recording.
        self._ready.wait(timeout=1.0)
        if self._error:
            raise OSError(self._error)

    def _sanitize_event(self, event):
        if not isinstance(event, DataEvent):
            event = DataEvent.from_dict(event)
        personal = event.topic.startswith("body.") or event.topic == "metric.sample" and str(event.payload.get("key", "")).startswith("body.")
        if personal and not self.include_pulse:
            return None
        if event.topic.startswith("terminal.") and not self.include_terminal:
            return None
        if event.topic in ("agent.prompt", "agent.prompt.sent") and not self.include_prompts:
            return None
        document = event.to_dict()

        def clean(value):
            if isinstance(value, dict):
                result = {}
                for key, item in value.items():
                    lower = key.lower()
                    if any(word in lower for word in ("token", "secret", "password", "authorization", "credential")):
                        continue
                    if not self.include_prompts and lower in ("prompt", "system_prompt", "full_prompt"):
                        continue
                    if not self.include_terminal and lower in ("terminal_text", "transcript"):
                        continue
                    result[key] = clean(item)
                return result
            if isinstance(value, list):
                return [clean(item) for item in value]
            return value

        document["payload"] = clean(document["payload"])
        return document

    def record(self, event, offset_seconds=0.0):
        if self._closed or self._error:
            return False
        document = self._sanitize_event(event)
        if document is None:
            return True
        offset = max(self._last_offset, max(0, int(float(offset_seconds) * 1000)))
        self._last_offset = offset
        document["recorded_offset_ms"] = offset
        record = {"record_type": "event", "offset_ms": offset, "event": document}
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            with self._lock:
                self._error = "BACKPRESSURE: recording queue is full; recording stopped"
            return False
        if document["topic"] == "metric.sample":
            self._state[(document["source_id"], document["payload"]["key"])] = document
        elif document["topic"] in ("site.pages", "body.signal"):
            self._state[(document["source_id"], document["topic"])] = document
        if len(self._state) > 512:
            self._state.pop(next(iter(self._state)))
        if offset / 1000.0 - self._last_checkpoint >= self._checkpoint_interval:
            checkpoint = {"record_type": "checkpoint", "offset_ms": offset,
                          "events": tuple(self._state.values())}
            try:
                if len(json.dumps(checkpoint)) < 120000:
                    self._queue.put_nowait(checkpoint)
                    self._last_checkpoint = offset / 1000.0
            except queue.Full:
                # Checkpoints are an optimization; event loss is never hidden.
                pass
        return True

    __call__ = record

    def _part_path(self, part):
        if part == 0:
            return self.path
        name = self.path.name
        for suffix in (".jsonl.gz", ".jsonl", ".gz"):
            if name.endswith(suffix):
                return self.path.with_name(name[:-len(suffix)] + ".{:03d}".format(part) + suffix)
        return self.path.with_name(name + ".{:03d}".format(part))

    def _open(self, path):
        raw = path.open("xb")
        return gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) if str(path).endswith(".gz") else raw

    def _write(self):
        handle, raw_handle, part, part_size = None, None, 0, 0
        try:
            handle = self._open(self._part_path(part))
            raw_handle = handle.fileobj if isinstance(handle, gzip.GzipFile) else None
            with self._lock:
                self._parts.append(str(self._part_path(part)))

            def write(document):
                encoded = (json.dumps(document, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
                handle.write(encoded)
                with self._lock:
                    self._bytes += len(encoded)
                return len(encoded)

            part_size = write(dict(self._manifest, part=part))
            self._ready.set()
            while not self._stop.is_set() or not self._queue.empty():
                try:
                    record = self._queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                if part_size >= self.max_bytes:
                    next_path = self._part_path(part + 1)
                    write({"record_type": "rotation", "next": next_path.name})
                    handle.close()
                    if raw_handle:
                        raw_handle.close()
                    part += 1
                    if part >= 32:
                        raise ValueError("Recording reached 32 parts; start a new session")
                    handle = self._open(next_path)
                    raw_handle = handle.fileobj if isinstance(handle, gzip.GzipFile) else None
                    with self._lock:
                        self._parts.append(str(next_path))
                    part_size = write(dict(self._manifest, part=part))
                part_size += write(record)
                if record["record_type"] == "event":
                    with self._lock:
                        self._events += 1
                self._queue.task_done()
            handle.flush()
        except Exception as error:
            with self._lock:
                self._error = type(error).__name__ + ": " + str(error)[:160]
        finally:
            self._ready.set()
            if handle:
                handle.close()
            if raw_handle:
                raw_handle.close()

    def snapshot(self):
        with self._lock:
            return RecordingStatus(str(self.path), not self._closed and not bool(self._error),
                                   self._events, self._bytes, self._queue.qsize(), self._error,
                                   tuple(self._parts))

    @property
    def status(self):
        return self.snapshot()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._thread.join(timeout=3.0)
        if self._thread.is_alive():
            with self._lock:
                self._error = "Recording writer did not finish within shutdown deadline"

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


@dataclass(frozen=True)
class ReplayStatus:
    path: str
    playhead: float
    duration: float
    playing: bool
    speed: float
    event_count: int
    seed: int
    error: str = ""


class Replay:
    """Read-only event timeline. No worker, network, shell or command handler."""

    def __init__(self, path, max_bytes=200 * 1024 * 1024, max_events=200000):
        self.path = Path(path).expanduser()
        self.manifest, self.events, self._offsets, self._checkpoints = {}, [], [], []
        self.playhead, self.speed, self.playing, self._cursor = 0.0, 1.0, False, 0
        total, seen, pending_path, event_offsets = 0, set(), self.path, {}
        while pending_path is not None:
            resolved = pending_path.resolve()
            if resolved in seen or len(seen) >= 32:
                raise ValueError("Invalid recording rotation chain")
            seen.add(resolved)
            next_path = None
            opener = gzip.open if str(pending_path).endswith(".gz") else open
            with opener(pending_path, "rb") as handle:
                while True:
                    raw = handle.readline(131073)
                    if not raw:
                        break
                    total += len(raw)
                    if len(raw) > 131072 or total > max_bytes:
                        raise ValueError("Replay exceeds bounded input size")
                    if not raw.strip():
                        continue
                    document = json.loads(raw)
                    kind = document.get("record_type")
                    if kind == "manifest":
                        if document.get("schema_version") != 1 or document.get("format") != "devos-event-log":
                            raise ValueError("Unsupported recording manifest")
                        if not self.manifest:
                            self.manifest = document
                    elif kind == "rotation":
                        name = document.get("next")
                        if not isinstance(name, str) or Path(name).name != name or name in (".", ".."):
                            raise ValueError("Invalid rotation path")
                        next_path = pending_path.parent / name
                    elif kind == "checkpoint":
                        checkpoint_events = document.get("events", [])
                        if not isinstance(checkpoint_events, list) or len(checkpoint_events) > 1024:
                            raise ValueError("Invalid replay checkpoint")
                        checkpoint_offset = document.get("offset_ms")
                        self._offset(checkpoint_offset)
                        state = []
                        for item in checkpoint_events:
                            checkpoint_event = self._replay_event(DataEvent.from_dict(item))
                            if checkpoint_event.recorded_offset_ms is None:
                                identity = (checkpoint_event.source_id, checkpoint_event.source_session_id, checkpoint_event.id)
                                inferred = event_offsets.get(identity)
                                if inferred is None:
                                    raise ValueError("Checkpoint event needs its original recording offset")
                                checkpoint_event = replace(checkpoint_event, recorded_offset_ms=inferred)
                            state.append(checkpoint_event)
                        state = tuple(state)
                        self._checkpoints.append((self._offset(document.get("offset_ms")), len(self.events), state))
                    elif kind == "event" or kind is None and "topic" in document:
                        event_doc = document.get("event") if kind == "event" else document
                        event = self._replay_event(DataEvent.from_dict(event_doc))
                        offset_ms = document.get("offset_ms", event.recorded_offset_ms)
                        offset = self._offset(offset_ms)
                        # Some normalized envelopes carry their offset only in
                        # the recording wrapper. Every downstream cue must use
                        # the same timeline for seek and age reconstruction.
                        event = replace(event, recorded_offset_ms=offset_ms)
                        event_offsets[(event.source_id, event.source_session_id, event.id)] = offset_ms
                        if self._offsets and offset < self._offsets[-1]:
                            raise ValueError("Recording offsets must be monotonic")
                        self.events.append(event)
                        self._offsets.append(offset)
                        if len(self.events) > max_events:
                            raise ValueError("Replay event count exceeds limit")
                    else:
                        raise ValueError("Unknown recording record type")
            pending_path = next_path
        if not self.manifest:
            # Normalized event JSONL can be inspected without a writer manifest.
            self.manifest = {"schema_version": 1, "seed": 42, "parameters": {}}
        self.events, self._offsets = tuple(self.events), tuple(self._offsets)
        self.duration = self._offsets[-1] if self._offsets else 0.0
        self._state_events, self._hub, self._hub_cursor = (), None, 0
        self.seek(0.0)

    @staticmethod
    def _offset(value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("Recording needs a nonnegative integer offset_ms")
        return value / 1000.0

    @staticmethod
    def _replay_event(event):
        origin = event.origin_mode if event.mode == "replay" else event.mode
        return replace(event, mode="replay", origin_mode=origin)

    def seek(self, seconds, _return_events=True):
        self.playhead = min(self.duration, max(0.0, float(seconds)))
        self._cursor = bisect_right(self._offsets, self.playhead)
        if self._hub is not None and self._cursor >= self._hub_cursor:
            for index in range(self._hub_cursor, self._cursor):
                self._hub._accept(self.events[index], self._offsets[index])
            self._hub_cursor = self._cursor
            self._hub._elapsed = self.playhead
            return self.events[:self._cursor] if _return_events else ()
        # Checkpoint state reconstructs gauges; the full preceding journal remains
        # available for event tapes and effects without ever executing events.
        state, start = (), 0
        for offset, index, checkpoint in self._checkpoints:
            if offset <= self.playhead:
                state, start = checkpoint, index
            else:
                break
        latest = {(event.source_id, event.payload.get("key", event.topic)): event for event in state}
        for event in self.events[start:self._cursor]:
            if event.topic == "metric.sample" or event.topic in ("site.pages", "body.signal"):
                latest[(event.source_id, event.payload.get("key", event.topic))] = event
        self._state_events = tuple(latest.values())
        if self._hub is None or self._cursor < self._hub_cursor:
            self._hub = TelemetryHub(seed=self.manifest.get("seed", 42), demo=False,
                                      start_workers=False, clock=lambda: self.playhead,
                                      empty_live_placeholders=False)
            for event in sorted(state, key=lambda item: (item.source_id, item.sequence)):
                original_offset = (event.recorded_offset_ms or 0) / 1000.0
                self._hub._accept(event, original_offset)
            self._hub_cursor = start
        for index in range(self._hub_cursor, self._cursor):
            self._hub._accept(self.events[index], self._offsets[index])
        self._hub_cursor = self._cursor
        self._hub._elapsed = self.playhead
        self._hub._journal = deque(self.events[max(0, self._cursor - 2000):self._cursor], maxlen=2000)
        return self.events[:self._cursor] if _return_events else ()

    def update(self, dt):
        if not self.playing:
            return ()
        previous = self._cursor
        self.seek(self.playhead + max(0.0, float(dt)) * self.speed, _return_events=False)
        if self.playhead >= self.duration:
            self.playing = False
        return self.events[previous:self._cursor]

    def snapshot(self):
        return ReplayStatus(str(self.path), self.playhead, self.duration, self.playing,
                            self.speed, len(self.events), int(self.manifest.get("seed", 42)))

    def telemetry_snapshot(self):
        # The isolated in-memory store has no providers/commands. Sampling it
        # doesn't replay the entire log again on every graphical frame.
        return self._hub.snapshot()

    def next_failure(self):
        for event, offset in zip(self.events[self._cursor:], self._offsets[self._cursor:]):
            if event.payload.get("result") in ("failed", "error") or event.topic.endswith(".failed") or event.payload.get("exit_code", 0) not in (0, None):
                self.seek(offset)
                return offset
        return None

    def close(self):
        self.playing = False
