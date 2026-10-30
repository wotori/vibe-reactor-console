import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from devos.recordings import Recorder, Replay
from devos.telemetry import DataEvent, TelemetryHub
from devos.playback import arena_document, presentation_document
from devos.agents import AgentCard, ArenaSnapshot, ValidationReport
from tests.test_telemetry import FakeClock


def event(sequence, value=28, mode="live", key="site.active_sessions_60s"):
    return DataEvent("sample-" + str(sequence), "metric.sample", "site", "site-run-1",
                     sequence, mode, None, "2026-10-31T12:00:00Z",
                     {"key": key, "value": value, "unit": "sessions", "quality": "ok",
                      "window_seconds": 60})


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "flight.jsonl"

    def test_jsonl_recording_seek_and_gauge_age_preserve_original_mode(self):
        recorder = Recorder(self.path, seed=17, checkpoint_seconds=1)
        for sequence in range(1, 5):
            self.assertTrue(recorder.record(event(sequence, value=sequence * 10), sequence * 2))
        recorder.close()
        self.assertFalse(recorder.status.error)
        self.assertEqual(recorder.status.events, 4)
        replay = Replay(self.path)
        self.assertEqual(replay.snapshot().seed, 17)
        self.assertEqual(replay.duration, 8)
        replay.seek(5)
        sample = replay.telemetry_snapshot().metric("site.active_sessions_60s")
        self.assertEqual((sample.value, sample.mode, sample.origin_mode, sample.age_seconds), (20, "replay", "live", 1))
        replay.seek(8)
        self.assertEqual(replay.telemetry_snapshot().metric(sample.key).value, 40)
        replay.seek(3)
        self.assertEqual(replay.telemetry_snapshot().metric(sample.key).value, 10)
        replay.playing = True
        replay.speed = 2
        new = replay.update(2)
        self.assertEqual(replay.playhead, 7)
        self.assertEqual([item.payload["value"] for item in new], [20, 30])

    def test_gzip_rotation_and_replay_read_all_parts(self):
        self.path = self.path.with_suffix(".jsonl.gz")
        recorder = Recorder(self.path, max_bytes=1200, checkpoint_seconds=1000)
        for sequence in range(10):
            recorder.record(event(sequence, sequence), sequence)
        recorder.close()
        self.assertFalse(recorder.status.error)
        self.assertGreater(len(recorder.status.parts), 1)
        replay = Replay(self.path)
        replay.seek(9)
        self.assertEqual(len(replay.events), 10)
        self.assertEqual(replay.telemetry_snapshot().metric("site.active_sessions_60s").value, 9)
        with gzip.open(self.path, "rt") as handle:
            self.assertEqual(json.loads(handle.readline())["seed"], 42)

    def test_personal_pulse_terminal_and_prompts_are_separate_opt_ins(self):
        with Recorder(self.path, parameters={"animation": "reactor", "token": "secret", "cwd": "/private",
                                             "nested": {"token": "secret", "source_file": "/private"}}) as recorder:
            recorder.record(event(1, 72, key="body.heart_rate"), 1)
            recorder.record(DataEvent("tty", "terminal.frame", "tty", "s", 1, "live", None,
                                      "2026-10-31T12:00:00Z", {"terminal_text": "private"}), 2)
            recorder.record(DataEvent("prompt", "agent.prompt", "agent", "s", 1, "live", None,
                                      "2026-10-31T12:00:00Z", {"prompt": "private"}), 3)
            recorder.record(DataEvent("handoff", "agent.handoff.ready", "agent", "s", 2, "live", None,
                                      "2026-10-31T12:00:00Z", {"text": "summary", "prompt": "private", "token": "secret"}), 4)
        text = self.path.read_text()
        self.assertNotIn("private", text)
        self.assertNotIn("secret", text)
        replay = Replay(self.path)
        self.assertEqual(len(replay.events), 1)
        self.assertEqual(replay.manifest["parameters"], {"animation": "reactor", "nested": {}})

    def test_recording_existing_path_never_overwrites(self):
        self.path.write_text("keep me")
        with self.assertRaises(FileExistsError):
            Recorder(self.path)
        self.assertEqual(self.path.read_text(), "keep me")

    def test_replaying_commands_and_handoffs_has_no_live_side_effects(self):
        with Recorder(self.path) as recorder:
            recorder.record(DataEvent("launch", "command.requested", "agent", "s", 1, "live", None,
                                      "2026-10-31T12:00:00Z", {"command": "touch do-not-create", "approval": True}), 0)
            recorder.record(event(2, 42), 2)
        with mock.patch("subprocess.Popen", side_effect=AssertionError("Replay launched a process")), \
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("Replay used network")):
            replay = Replay(self.path)
            for position in (0, 2, 1, 2, 0):
                replay.seek(position)
                replay.telemetry_snapshot()
            self.assertEqual(replay.events[0].mode, "replay")
            self.assertEqual(replay.events[0].origin_mode, "live")

    def test_demo_replay_can_be_recorded_again_without_losing_provenance(self):
        hub = TelemetryHub(seed=7, start_workers=False)
        self.addCleanup(hub.close)
        recorder = Recorder(self.path)
        hub.add_listener(recorder)
        hub.update(1)
        hub.update(1)
        recorder.close()
        first = Replay(self.path)
        second_path = self.path.with_name("second.jsonl")
        with Recorder(second_path) as second:
            for item in first.events:
                second.record(item, (item.recorded_offset_ms or 0) / 1000)
        rerecorded = Replay(second_path)
        self.assertTrue(all(item.mode == "replay" and item.origin_mode == "demo" for item in rerecorded.events))

    def test_failures_can_be_found_and_seek_does_not_reemit_them(self):
        with Recorder(self.path) as recorder:
            recorder.record(event(1, 5), 0)
            recorder.record(DataEvent("failure", "task.test.completed", "task", "r", 1, "live", None,
                                      "2026-10-31T12:00:00Z", {"run_id": "r", "exit_code": 7, "result": "failed"}), 3)
            recorder.record(event(2, 9), 5)
        replay = Replay(self.path)
        self.assertEqual(replay.next_failure(), 3)
        self.assertIsNone(replay.next_failure())
        replay.playing = True
        self.assertEqual([item.payload.get("value") for item in replay.update(2)], [9])

    def test_bad_rotation_paths_and_nonmonotonic_offsets_are_rejected(self):
        self.path.write_text(json.dumps({"record_type": "rotation", "next": "../secret"}) + "\n")
        with self.assertRaises(ValueError):
            Replay(self.path)
        lines = []
        for offset in (1000, 500):
            lines.append(json.dumps({"record_type": "event", "offset_ms": offset,
                                     "event": event(offset).to_dict()}))
        self.path.write_text("\n".join(lines))
        with self.assertRaises(ValueError):
            Replay(self.path)

    def test_outer_recording_offset_is_attached_to_replayed_events_and_checkpoint_samples(self):
        first = event(1, 10).to_dict()
        last = event(2, 20).to_dict()
        documents = [
            {"record_type": "event", "offset_ms": 2000, "event": first},
            {"record_type": "checkpoint", "offset_ms": 5000, "events": [first]},
            {"record_type": "event", "offset_ms": 8000, "event": last},
        ]
        self.path.write_text("\n".join(json.dumps(item) for item in documents))
        replay = Replay(self.path)
        self.assertEqual(tuple(item.recorded_offset_ms for item in replay.events), (2000, 8000))
        replay.seek(8)
        replay.seek(5)  # Force restoration through the checkpoint.
        sample = replay.telemetry_snapshot().metric("site.active_sessions_60s")
        self.assertEqual((sample.value, sample.age_seconds), (10, 3))

    def test_replay_preserves_upstream_initial_age_and_live_pulse_ttl(self):
        clock = FakeClock()
        clock.value = 100
        hub = TelemetryHub(clock=clock, wall_clock=clock.wall, demo=False, start_workers=False)
        self.addCleanup(hub.close)
        with Recorder(self.path, include_pulse=True) as recorder:
            hub.add_listener(recorder)
            old = event(1).to_dict()
            old["observed_at"] = "2026-10-31T12:00:00Z"
            hub.submit(old)
            hub.update(0)
            pulse = event(2, 72, key="body.heart_rate").to_dict()
            pulse["payload"]["unit"] = "bpm"
            pulse["observed_at"] = clock.wall()
            hub.submit(pulse)
            hub.update(0)
            recorder.record(DataEvent("end", "recording.bookmark", "other", "r", 1, "live",
                                      None, clock.wall(), {}), 20)
        replay = Replay(self.path)
        replay.seek(0)
        online = replay.telemetry_snapshot().metric("site.active_sessions_60s")
        self.assertEqual((online.value, online.quality, online.age_seconds), (None, "unavailable", 100))
        pulse = replay.telemetry_snapshot().metric("body.heart_rate")
        self.assertEqual((pulse.value, pulse.age_seconds), (72, 0))
        replay.seek(4)
        self.assertEqual(replay.telemetry_snapshot().metric("body.heart_rate").quality, "stale")
        replay.seek(11)
        self.assertIsNone(replay.telemetry_snapshot().metric("body.heart_rate").value)

    def test_recorded_public_snapshots_manual_bookmark_and_presentation_roundtrip(self):
        report = ValidationReport("SUCCEEDED", ("/private/python3", "--token=hidden", "/private/tests"),
                                  "/private/workspace", None, "a" * 64, 1, .1, 0, "private raw output", "")
        snapshot = ArenaSnapshot("mission", "READY_FOR_USER", (AgentCard("codex", "Codex", "Builder", "SUCCEEDED", "Done"),),
                                 (), (), (), 4, 6, 900, verified=True, validation_reports=(report,))
        with Recorder(self.path) as recorder:
            recorder.record(DataEvent("presentation", "presentation.changed", "ui", "run", 1, "live", None,
                                      "2026-10-31T12:00:00Z", presentation_document({"visual": "reactor", "speed": 2}, 10, 20)), 0)
            self.assertTrue(recorder.bookmark("Saved /private/workspace", 1))
            recorder.record(DataEvent("arena", "arena.snapshot", "ui", "run", 2, "live", None,
                                      "2026-10-31T12:00:00Z", arena_document(snapshot, include_paths=True)), 2)
        text = self.path.read_text()
        for private in ("/private/workspace", "--token=hidden", "private raw output"):
            self.assertNotIn(private, text)
        replay = Replay(self.path)
        replay.seek(2)
        self.assertTrue(replay.arena_snapshot().verified)
        self.assertEqual(replay.arena_snapshot().validation_reports[0].exit_code, 0)
        self.assertEqual(replay.presentation_snapshot().visual_time, 14)
        self.assertEqual(replay.bookmarks[0].kind, "manual")
        self.assertEqual(replay.bookmarks[0].offset, 1)

    def test_malformed_record_shapes_and_nonfinite_timing_are_clear_errors(self):
        for document in ([], True, {"record_type": "manifest", "schema_version": True, "format": "devos-event-log"},
                         {"record_type": "manifest", "schema_version": 1, "format": "devos-event-log", "seed": True},
                         {"record_type": "manifest", "schema_version": 1, "format": "devos-event-log", "parameters": []}):
            self.path.write_text(json.dumps(document))
            with self.subTest(document=document), self.assertRaises(ValueError):
                Replay(self.path)
        self.path.unlink()
        with Recorder(self.path) as recorder:
            with self.assertRaises(ValueError):
                recorder.record(event(1), float("nan"))
