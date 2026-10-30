"""Real local feeds through Runtime, with deterministic measurement freshness."""

import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from devos.cli import parser, runtime_options
from devos.agents import AgentCoordinator
from devos.runtime import Runtime
from devos.session import Session
from devos.sources import SourceProvider
from devos.telemetry import TelemetryHub
from devos.telemetry import DataEvent

from tests.test_telemetry import FakeClock
from tests import test_runtime_arena as arena_fakes


class RuntimeDataTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.clock = FakeClock()

    def start(self, *arguments):
        args = parser().parse_args(["--no-config", "--bundled", "--agents", "watch",
                                    "--cwd", str(self.root)] + list(arguments))
        settings = runtime_options(args)
        session = Session(sources=SourceProvider(bundled=True), **settings)

        def hub_factory(**kwargs):
            return TelemetryHub(interval=.05, stale_after=5, unavailable_after=10,
                                clock=self.clock, wall_clock=self.clock.wall, **kwargs)

        self.hub_patch = patch("devos.runtime.TelemetryHub", side_effect=hub_factory)
        self.hub_patch.start()
        self.addCleanup(self.hub_patch.stop)
        runtime = Runtime(args, session)
        self.addCleanup(runtime.close)
        return runtime, session

    def wait_until(self, runtime, predicate):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            runtime.update(.01)
            if predicate(runtime.session):
                return
            time.sleep(.01)
        self.fail("Runtime did not publish the expected local measurement")

    def test_real_site_file_keeps_observation_age_while_transport_remains_healthy(self):
        path = self.root / "site.json"
        document = {"schema_version": 1, "observed_at": self.clock.wall(),
                    "active_sessions": 28, "active_window_seconds": 60,
                    "pages": [{"path": "/ekza", "active_sessions": 12}]}
        path.write_text(json.dumps(document))
        runtime, session = self.start("--source-file", str(path))
        self.wait_until(runtime, lambda item: item.telemetry.metric("site.active_sessions_60s").value == 28)
        self.assertEqual(session.telemetry.pages, (("/ekza", 12.0),))
        self.assertEqual(session.telemetry.metric("site.active_sessions_60s").mode, "live")
        self.assertEqual(session.telemetry.metric("body.heart_rate").mode, "demo")

        self.clock.value = 6
        self.wait_until(runtime, lambda item: any(source.source_id == "json-file" and
            source.last_received_monotonic == 6 for source in item.telemetry.providers))
        cached = session.telemetry.metric("site.active_sessions_60s")
        self.assertEqual((cached.value, cached.age_seconds, cached.quality), (28, 6, "stale"))
        self.clock.value = 11
        runtime.update(.01)
        missing = session.telemetry.metric(cached.key)
        self.assertEqual((missing.mode, missing.value, missing.quality), ("live", None, "unavailable"))

        document["observed_at"] = self.clock.wall()
        path.write_text(json.dumps(document))
        self.wait_until(runtime, lambda item: item.telemetry.metric(cached.key).quality == "ok")
        self.assertEqual(session.telemetry.metric(cached.key).value, 28)
        self.assertEqual(session.telemetry.metric(cached.key).age_seconds, 0)

    def test_menu_pauses_demo_but_cannot_freeze_live_sensor_age(self):
        path = self.root / "heart.json"
        path.write_text(json.dumps({"schema_version": 1, "received_at": self.clock.wall(),
                                    "heart_rate_bpm": 72, "rr_intervals_ms": [830, 836]}))
        runtime, session = self.start("--heart-file", str(path), "--binding", "pulse")
        self.wait_until(runtime, lambda item: item.telemetry.metric("body.heart_rate").value == 72)
        self.assertEqual(session.telemetry.rr_intervals, (830, 836))
        elapsed = runtime.hub.elapsed
        session.handle("palette")
        self.clock.value = 4
        runtime.update(4)
        self.assertEqual(runtime.hub.elapsed, elapsed)
        self.assertEqual(session.telemetry.metric("body.heart_rate").quality, "stale")
        self.clock.value = 11
        runtime.update(7)
        self.assertIsNone(session.telemetry.metric("body.heart_rate").value)
        self.assertNotIn("pulse_bpm", session.visual_parameters())

    def test_switching_from_missing_live_to_demo_is_explicit_and_reversible(self):
        runtime, session = self.start("--data-mode", "live")
        runtime.update(0)
        before = session.telemetry.metric("site.active_sessions_60s")
        self.assertEqual((before.mode, before.value), ("live", None))
        session.data_mode = "demo"
        session.pending_actions.append("data-mode")
        runtime.update(0)
        demo = session.telemetry.metric(before.key)
        self.assertEqual(demo.mode, "demo")
        self.assertIsNotNone(demo.value)
        session.data_mode = "live"
        session.pending_actions.append("data-mode")
        runtime.update(0)
        restored = session.telemetry.metric(before.key)
        self.assertEqual((restored.mode, restored.value), ("live", None))

    def test_configured_task_runs_only_after_explicit_action_and_publishes_real_exit(self):
        marker = self.root / "started.txt"
        code = "from pathlib import Path; Path('started.txt').write_text('started'); raise SystemExit(7)"
        # argv is supplied directly here; CLI uses shlex rather than shell eval.
        runtime, session = self.start("--data-mode", "live")
        runtime.args.task = (sys.executable, "-c", code)
        for _ in range(5):
            runtime.update(.01)
        self.assertFalse(marker.exists())
        session.pending_actions.append("task")
        self.wait_until(runtime, lambda item: any(event.topic == "task.test.completed" for event in item.telemetry.events))
        self.assertEqual(marker.read_text(), "started")
        completion = next(event for event in session.telemetry.events if event.topic == "task.test.completed")
        self.assertEqual(completion.payload["exit_code"], 7)
        self.assertNotIn("tests_passed", completion.payload)
        self.assertEqual(session.task_snapshot.exit_code, 7)

    def test_replay_seek_uses_recorded_cue_age_instead_of_restarting_effects(self):
        path = self.root / "cues.jsonl"
        cue = DataEvent("cue-1", "visual.cue", "source", "run", 1, "demo", None,
                        "2026-10-31T12:00:00Z", {"cue": "traffic-surge"}).to_dict()
        later = DataEvent("later", "demo.scenario.finished", "source", "run", 2, "demo", None,
                          "2026-10-31T12:00:20Z", {"state": "finished"}).to_dict()
        path.write_text("\n".join(json.dumps(item) for item in (
            {"record_type": "event", "offset_ms": 1000, "event": cue},
            {"record_type": "event", "offset_ms": 20000, "event": later})))
        runtime, session = self.start("--replay", str(path))
        runtime.replay.seek(15)
        runtime.update(0)
        self.assertEqual((session.visual_time, session.protocol_time), (15, 15))
        self.assertEqual(session.visual_parameters()["burst_age"], 14)
        runtime.replay.seek(0)
        runtime.update(0)
        self.assertGreaterEqual(session.visual_parameters()["burst_age"], 1.2)
        runtime.replay.seek(1.5)
        runtime.update(0)
        self.assertEqual(session.visual_parameters()["burst_age"], .5)

    def test_managed_live_handoff_becomes_one_canonical_event_with_identity_and_status(self):
        roots = {actor: self.root / actor for actor in ("codex", "claude")}
        for root in roots.values():
            root.mkdir()
            (root / "fixture.py").write_text("value = 1\n")
        arena_fakes.Turn.instances = []
        coordinator = AgentCoordinator(mode="relay", adapters={actor: arena_fakes.Adapter() for actor in roots},
                                       worker_factory=arena_fakes.Turn)
        with patch("devos.runtime.AgentCoordinator", return_value=coordinator):
            runtime, session = self.start("--data-mode", "live", "--agents", "relay", "--mission", "Review local fixtures",
                                          "--workspace-left", str(roots["codex"]), "--workspace-right", str(roots["claude"]))
        for turn in arena_fakes.Turn.instances:
            turn.finish()
        runtime.update(0)
        runtime.action("handoff")
        runtime.update(0)
        runtime.update(0)
        canonical = [item for item in session.telemetry.events if item.topic == "agent.handoff.ready"]
        self.assertEqual(len(canonical), 1)
        message = coordinator.snapshot().handoffs[0]
        self.assertEqual(canonical[0].mode, "live")
        self.assertEqual(canonical[0].payload["message_id"], message.message_id)
        self.assertEqual((canonical[0].payload["from"], canonical[0].payload["to"]), ("codex", "claude"))
        self.assertEqual(canonical[0].payload["summary"], message.summary)
        session.lab_binding = "queue"
        self.assertEqual(session.visual_parameters()["queue_right"], 1)
        self.assertEqual(session.visual_parameters()["burst_age"], 0)
        for _ in range(3):
            runtime.update(0)
        self.assertEqual(len([item for item in session.telemetry.events if item.topic == "agent.handoff.ready"]), 1)
        arena_fakes.Turn.instances[-1].finish()
        runtime.update(0)
        runtime.update(0)
        updates = [item for item in session.telemetry.events if item.topic == "agent.handoff.updated"]
        self.assertEqual(len(updates), 1)
        self.assertEqual((updates[0].payload["message_id"], updates[0].payload["status"]), (message.message_id, "replied"))
        self.assertEqual(session.visual_parameters()["queue_right"], 0)
