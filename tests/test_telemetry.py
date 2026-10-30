from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from devos.telemetry import (BoundedInbox, DataEvent, HTTPJSONProvider, JSONFileProvider, JSONStreamProvider,
                             TelemetryHub)


class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.base = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def wall(self):
        return (self.base + timedelta(seconds=self.value)).isoformat().replace("+00:00", "Z")


def metric_event(sequence=1, value=28, source="test", session="run-1", observed=None,
                 key="site.active_sessions_60s", mode="live"):
    return DataEvent("event-{}-{}-{}".format(source, session, sequence), "metric.sample",
                     source, session, sequence, mode, observed, "2026-10-31T12:00:00Z",
                     {"key": key, "value": value, "unit": "sessions", "quality": "ok",
                      "window_seconds": 60})


class TelemetryTests(unittest.TestCase):
    def hub(self, **kwargs):
        clock = kwargs.pop("clock", FakeClock())
        hub = TelemetryHub(clock=clock, wall_clock=clock.wall, start_workers=False,
                           demo=False, **kwargs)
        self.addCleanup(hub.close)
        return hub, clock

    def test_samples_are_deeply_immutable_and_reject_invalid_numbers(self):
        document = metric_event().to_dict()
        event = DataEvent.from_dict(document)
        document["payload"]["value"] = 999
        self.assertEqual(event.payload["value"], 28)
        with self.assertRaises(TypeError):
            event.payload["value"] = 2
        for invalid in (float("nan"), float("inf"), True, "28"):
            document["payload"]["value"] = invalid
            with self.assertRaises(ValueError):
                DataEvent.from_dict(document)

    def test_monotonic_ttl_and_missing_are_distinct_from_zero(self):
        hub, clock = self.hub(stale_after=5, unavailable_after=10)
        hub.submit(metric_event(value=0))
        self.assertEqual(hub.update().metric("site.active_sessions_60s").value, 0)
        clock.value = 6
        self.assertEqual(hub.snapshot().metric("site.active_sessions_60s").quality, "stale")
        clock.value = 11
        expired = hub.snapshot().metric("site.active_sessions_60s")
        self.assertIsNone(expired.value)
        self.assertEqual(expired.quality, "unavailable")
        self.assertEqual(hub.snapshot().history(expired.key)[0].value, 0)

    def test_cached_observed_at_does_not_refresh_measurement_age(self):
        hub, clock = self.hub(stale_after=5, unavailable_after=30)
        observed = clock.wall()
        hub.submit(metric_event(observed=observed))
        hub.update()
        clock.value = 9
        hub.submit(metric_event(sequence=2, observed=observed))
        cached = hub.update().metric("site.active_sessions_60s")
        self.assertEqual(cached.age_seconds, 9)
        self.assertEqual(cached.quality, "stale")
        self.assertEqual(len(hub.snapshot().history(cached.key)), 1)
        hub.submit(metric_event(sequence=3, observed=clock.wall()))
        fresh = hub.update().metric(cached.key)
        self.assertEqual(fresh.value, 28)
        self.assertEqual(fresh.age_seconds, 0)

    def test_initial_source_age_is_kept_across_successful_fetch(self):
        hub, clock = self.hub(stale_after=5, unavailable_after=10)
        clock.value = 20
        hub.submit(metric_event(observed="2026-10-31T12:00:00Z"))
        sample = hub.update().metric("site.active_sessions_60s")
        self.assertEqual(sample.age_seconds, 20)
        self.assertEqual(sample.quality, "unavailable")
        self.assertEqual(sample.time_basis, "source_timestamp")

    def test_sequence_and_session_dedup_keep_gauges_from_rolling_back(self):
        hub, _ = self.hub()
        hub.submit(metric_event(4, 40))
        hub.update()
        hub.submit(metric_event(3, 30))
        hub.submit(metric_event(4, 44))
        self.assertEqual(hub.update().metric("site.active_sessions_60s").value, 40)
        hub.submit(metric_event(0, 50, session="run-2"))
        self.assertEqual(hub.update().metric("site.active_sessions_60s").value, 50)
        hub.submit(metric_event(5, 55, session="run-1"))
        self.assertEqual(hub.update().metric("site.active_sessions_60s").value, 50)

    def test_explicit_live_selection_never_uses_demo_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            hub = TelemetryHub(source_file=Path(folder) / "missing.json", start_workers=False)
            self.addCleanup(hub.close)
            snapshot = hub.update(1)
            online = snapshot.metric("site.active_sessions_60s")
            self.assertEqual((online.mode, online.source_id, online.value), ("live", "json-file", None))
            self.assertEqual(snapshot.metric("body.heart_rate").mode, "demo")
            self.assertEqual(snapshot.mode, "mixed")
            self.assertFalse(snapshot.pages)

    def test_live_only_mode_without_connections_shows_unavailable_not_demo_or_zero(self):
        hub, _ = self.hub()
        snapshot = hub.update(0)
        self.assertFalse(snapshot.events)
        for key in ("site.active_sessions_60s", "body.heart_rate"):
            sample = snapshot.metric(key)
            self.assertEqual((sample.value, sample.mode, sample.quality), (None, "live", "unavailable"))
        self.assertEqual(snapshot.mode, "live")

    def test_bounded_inbox_coalesces_gauges_and_signals_lifecycle_backpressure(self):
        inbox = BoundedInbox(capacity=2, gauge_capacity=2)
        for sequence in range(1000):
            self.assertTrue(inbox.put(metric_event(sequence, sequence)))
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox.drain()[0].payload["value"], 999)
        for sequence in range(2):
            self.assertTrue(inbox.put(DataEvent(str(sequence), "agent.handoff.ready", "a", "s", sequence,
                                                "demo", None, "2026-10-31T12:00:00Z", {})))
        self.assertFalse(inbox.put(DataEvent("3", "agent.handoff.ready", "a", "s", 3,
                                             "demo", None, "2026-10-31T12:00:00Z", {})))
        self.assertTrue(inbox.backpressure)
        self.assertEqual(len(inbox.drain()), 2)

    def test_demo_is_deterministic_finite_and_has_no_actual_test_completion(self):
        logs = []
        for _ in range(2):
            hub = TelemetryHub(seed=17, start_workers=False)
            self.addCleanup(hub.close)
            for second in range(61):
                hub.update(1)
            first = tuple(event.to_dict() for event in hub.snapshot().events)
            hub.update(100)
            self.assertEqual(first, tuple(event.to_dict() for event in hub.snapshot().events))
            self.assertTrue(hub.snapshot().demo_finished)
            self.assertNotIn("task.test.completed", {event.topic for event in hub.snapshot().events})
            self.assertIn("demo.build.finished", {event.topic for event in hub.snapshot().events})
            self.assertTrue(all(event.mode == "demo" for event in hub.snapshot().events))
            logs.append(first)
        self.assertEqual(logs[0], logs[1])

    def test_heart_bridge_bpm_only_does_not_invent_measured_rr(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pulse.json"
            path.write_text(json.dumps({"schema_version": 1, "heart_rate_bpm": 72,
                                        "received_at": "2026-10-31T12:00:00Z"}))
            provider = JSONFileProvider(path, source_id="heart-rate-bridge")
            events = provider.poll()
            self.assertEqual([event.payload["key"] for event in events if event.topic == "metric.sample"], ["body.heart_rate"])
            body = next(event for event in events if event.topic == "body.signal")
            self.assertEqual(body.payload["rr_intervals_ms"], ())
            self.assertEqual(body.payload["timing_basis"], "notification_received")

    def test_rr_batches_are_deduplicated_and_pulse_has_short_ttl(self):
        hub, clock = self.hub(source_selection={"body.heart_rate": "heart"})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "heart.json"
            path.write_text(json.dumps({"heart_rate_bpm": 72, "rr_intervals_ms": [830, 836],
                                        "received_at": clock.wall()}))
            provider = JSONFileProvider(path, source_id="heart", clock=clock, wall_clock=clock.wall)
            for event in provider.poll():
                hub.submit(event)
            self.assertEqual(hub.update().rr_intervals, (830, 836))
            clock.value = 4
            for event in provider.poll():
                hub.submit(event)
            self.assertEqual(hub.update().rr_intervals, (830, 836))
            self.assertEqual(hub.snapshot().metric("body.heart_rate").quality, "stale")
            clock.value = 11
            self.assertIsNone(hub.snapshot().metric("body.heart_rate").value)

    def test_file_provider_accepts_upstream_event_and_preserves_its_origin(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.jsonl"
            path.write_text(json.dumps(metric_event().to_dict()) + "\n")
            provider = JSONFileProvider(path)
            event = provider.poll()[0]
            self.assertEqual(event.source_id, "json-file")
            self.assertEqual(event.payload["upstream_source_id"], "test")

    def test_site_endpoint_windows_and_pages_are_not_relabelled_as_people(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "site.json"
            path.write_text(json.dumps({"active_sessions": 28, "active_window_seconds": 30,
                                        "pageviews_per_minute": 84,
                                        "pages": [{"path": "/ekza?email=private#id", "active_sessions": 12}]}))
            events = JSONFileProvider(path).poll()
            sample = next(event for event in events if event.payload.get("key") == "site.active_sessions_30s")
            self.assertEqual((sample.payload["unit"], sample.payload["window_seconds"]), ("sessions", 30))
            page_event = next(event for event in events if event.topic == "site.pages")
            self.assertEqual(page_event.payload["pages"][0]["path"], "/ekza")

    def test_jsonl_stream_waits_for_complete_lines_and_follows_truncation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "metrics.jsonl"
            path.write_text('{"active_sessions":')
            provider = JSONStreamProvider(path)
            self.addCleanup(provider.close)
            self.assertEqual(provider.poll(), ())
            with path.open("a") as handle:
                handle.write('28}\n')
            first = provider.poll()
            self.assertEqual(first[0].payload["value"], 28)
            self.assertEqual(provider.poll(), ())
            path.write_text('{"active_sessions":9}\n')
            self.assertEqual(provider.poll()[0].payload["value"], 9)

    def test_fifo_stream_does_not_block_without_a_writer(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO unavailable")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.pipe"
            os.mkfifo(path)
            provider = JSONStreamProvider(path)
            self.addCleanup(provider.close)
            start = time.monotonic()
            self.assertEqual(provider.poll(), ())
            self.assertLess(time.monotonic() - start, 0.1)
            descriptor = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
            try:
                os.write(descriptor, b'{"active_sessions":3}\n')
            finally:
                os.close(descriptor)
            self.assertEqual(provider.poll()[0].payload["value"], 3)

    def test_blocking_provider_cannot_block_ui_or_shutdown(self):
        entered, release = threading.Event(), threading.Event()

        class Blocked:
            source_id = "blocked"

            def poll(self):
                entered.set()
                release.wait(timeout=2)
                return ()

            def close(self):
                release.set()

        hub = TelemetryHub(providers=(Blocked(),), demo=False)
        self.addCleanup(hub.close)
        self.assertTrue(entered.wait(0.5))
        started = time.monotonic()
        for _ in range(20):
            hub.update(1 / 60)
        self.assertLess(time.monotonic() - started, 0.1)
        hub.close()
        self.assertTrue(release.is_set())

    def test_malformed_events_are_rejected_without_crashing_update(self):
        hub, _ = self.hub()
        event = metric_event().to_dict()
        event["payload"]["value"] = float("nan")
        self.assertFalse(hub.submit(event))
        event["payload"]["value"] = 1
        event["observed_at"] = "not-a-timestamp"
        self.assertFalse(hub.submit(event))
        self.assertEqual(hub.update().rejected_events, 2)


class HTTPProviderTests(unittest.TestCase):
    def setUp(self):
        self.status = 200
        self.body = {"schema_version": 1, "active_sessions": 28, "active_window_seconds": 60,
                     "observed_at": "2026-10-31T12:00:00Z"}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(owner.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                payload = owner.body if isinstance(owner.body, str) else json.dumps(owner.body)
                self.wfile.write(payload.encode())

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:{}/metrics".format(self.server.server_port)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_http_schema_cached_timestamp_and_malformed_json(self):
        provider = HTTPJSONProvider(self.url, timeout=0.5)
        first, second = provider.poll()[0], provider.poll()[0]
        self.assertEqual(first.observed_at, second.observed_at)
        self.assertNotEqual(first.sequence, second.sequence)
        self.assertEqual(first.payload["value"], 28)
        self.body = "{malformed"
        with self.assertRaises(ValueError):
            provider.poll()

    def test_http_auth_failure_is_visible_without_demo_fallback(self):
        self.status = 401
        hub = TelemetryHub(source_url=self.url, interval=0.05)
        self.addCleanup(hub.close)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            snapshot = hub.update(0.01)
            if any(status.error for status in snapshot.providers):
                break
            time.sleep(0.01)
        source = next(status for status in snapshot.providers if status.source_id == "http-json")
        self.assertEqual(source.error, "HTTP 401")
        self.assertEqual(source.quality, "error")
        online = snapshot.metric("site.active_sessions_60s")
        self.assertEqual((online.mode, online.value), ("live", None))
