from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from devos.agents import AgentCard, ArenaEvent, ArenaSnapshot, Artifact, Handoff, ValidationReport
from devos.bindings import BindingConfig
from devos.playback import (Bookmark, arena_document, arena_from_document, export_scene_capsule,
                            presentation_document, scene_capsule)
from devos.recordings import Recorder, Replay
from devos.telemetry import DataEvent


def event(sequence, topic, payload):
    return DataEvent("event-" + str(sequence), topic, "fixture", "fixture-session", sequence, "live", None,
                     "2026-11-01T12:00:00Z", payload)


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "flight.jsonl"

    def recording(self, items, parameters=None):
        documents = [{"record_type": "manifest", "schema_version": 1, "format": "devos-event-log",
                      "seed": 17, "parameters": parameters or {}}]
        for index, (offset, topic, payload) in enumerate(items, 1):
            documents.append({"record_type": "event", "offset_ms": int(offset * 1000), "event": event(index, topic, payload).to_dict()})
        self.path.write_text("\n".join(json.dumps(item) for item in documents))
        return Replay(self.path)

    def test_presentation_seek_restores_original_pause_speed_and_clock_anchors(self):
        replay = self.recording([
            (0, "presentation.changed", presentation_document({"visual": "reactor", "speed": 1}, 10, 20)),
            (2, "presentation.changed", {"parameters": {"speed": 2}}),
            (3, "presentation.changed", {"parameters": {}, "paused": True}),
            (5, "presentation.changed", presentation_document({"visual": "torus-knot", "consumer": "space"}, 40, 50)),
            (7, "presentation.changed", {"parameters": {"speed": .5}}),
            (10, "recording.end", {}),
        ])
        for position, visual, protocol, paused in ((1, 11, 21, False), (2.5, 13, 23, False),
                                                   (4, 14, 24, True), (6, 42, 52, False), (8, 44.5, 54.5, False)):
            replay.seek(position)
            snapshot = replay.presentation_snapshot()
            self.assertEqual((snapshot.visual_time, snapshot.protocol_time, snapshot.paused), (visual, protocol, paused))
        replay.seek(2.5)
        before = replay.presentation_snapshot()
        replay.seek(9)
        replay.seek(2.5)
        self.assertEqual(before, replay.presentation_snapshot())
        with self.assertRaises(TypeError):
            before.parameters["visual"] = "cube"

    def test_playback_speed_controls_playhead_only_and_invalid_speeds_rejected(self):
        replay = self.recording([(0, "presentation.changed", presentation_document({"speed": 2}, 3, 7)),
                                 (20, "recording.end", {})])
        for invalid in (0, 3, 16, float("nan"), float("inf"), True, "2"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                replay.set_speed(invalid)
        replay.set_speed(4)
        replay.playing = True
        replay.update(.5)
        self.assertEqual(replay.playhead, 2)
        self.assertEqual(replay.presentation_snapshot().visual_time, 7)
        replay.playing = False
        replay.update(10)
        self.assertEqual(replay.playhead, 2)
        self.assertEqual(replay.cycle_speed(), 8)
        self.assertEqual(replay.cycle_speed(), .25)
        self.assertEqual(replay.cycle_speed(-1), 8)
        for invalid in (float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                replay.seek(invalid)

    def test_automatic_manual_bookmarks_are_deterministic_and_deduplicate_handoffs(self):
        def metric(value):
            return {"key": "site.active_sessions_60s", "value": value, "unit": "sessions", "quality": "ok", "window_seconds": 60}
        replay = self.recording([(0, "metric.sample", metric(10)), (1, "metric.sample", metric(25)),
                                 (2, "agent.handoff.ready", {"message_id": "handoff-1", "from": "codex", "to": "claude", "summary": "Review it"}),
                                 (3, "agent.handoff.updated", {"message_id": "handoff-1", "status": "replied"}),
                                 (4, "task.test.completed", {"exit_code": 2, "result": "failed"}),
                                 (5, "recording.bookmark", {"id": "mark-1", "label": "Saved moment", "kind": "manual"}),
                                 (6, "recording.end", {})])
        self.assertEqual([(item.offset, item.kind) for item in replay.bookmarks], [(1, "surge"), (2, "handoff"), (4, "failure"), (5, "manual")])
        marker = replay.add_bookmark("My mark", offset=3.5)
        original = replay.bookmarks
        for position in (5, 0, 4, 2, 6, 0):
            replay.seek(position)
        self.assertEqual(replay.bookmarks, original)
        self.assertEqual(replay.next_bookmark("failure").offset, 4)
        self.assertIsNone(replay.next_bookmark("failure"))
        self.assertIn(marker, replay.snapshot().bookmarks)

    def arena(self, status="RUNNING", verified=False, reports=(), handoff_status="delivered"):
        artifact = Artifact("artifact-1", "patch", "a" * 64, "codex", "b" * 64,
                            ("src/main.py", "/Users/private/code.py", "../escape"), "Public patch /Users/private/code.py token=hidden")
        handoff = Handoff("message-1", 1, "codex", "claude", "Public result", "Review the patch", handoff_status, (artifact,))
        return ArenaSnapshot("mission", status,
                             (AgentCard("codex", "Codex", "Builder", "SUCCEEDED", "Built /Users/private/code.py token=hidden", "secret-provider-session"),
                              AgentCard("claude", "Claude", "Reviewer", "RUNNING" if status == "RUNNING" else "SUCCEEDED")),
                             (ArenaEvent(1, "turn", "codex", "Public response", 123),), (handoff,), (artifact,),
                             4, 6, 900, "", verified, tuple(reports), "review" if status == "RUNNING" else "review-approved", 1)

    def report(self, code=0):
        return ValidationReport("SUCCEEDED" if code == 0 else "FAILED",
                                (sys.executable, "-m", "unittest", "--token=hidden", "/Users/private/tests"),
                                "/Users/private/project", "b" * 64, "a" * 64, 123, .5, code,
                                "Full prompt and other private raw output", "secret stderr")

    def test_canonical_arena_state_restores_after_forward_backward_and_repeated_seek(self):
        initial = replace(self.arena(), cards=(), handoffs=(), artifacts=(), events=(), phase="initial")
        reviewed = self.arena("READY_FOR_USER", True, (self.report(),), "replied")
        replay = self.recording([(0, "arena.snapshot", arena_document(initial)),
                                 (2, "arena.snapshot", arena_document(self.arena())),
                                 (4, "arena.snapshot", arena_document(reviewed)), (6, "recording.end", {})])
        replay.seek(4)
        expected = replay.arena_snapshot()
        self.assertEqual(expected.mode, "replay")
        self.assertTrue(expected.verified)
        self.assertEqual(expected.validation_reports[0].exit_code, 0)
        self.assertEqual(expected.validation_reports[0].argv, (Path(sys.executable).name,))
        self.assertEqual(expected.validation_reports[0].cwd, "")
        self.assertEqual(expected.handoffs[0].status, "replied")
        self.assertEqual(expected.artifacts[0].paths, ("src/main.py",))
        replay.seek(1)
        self.assertEqual(replay.arena_snapshot().phase, "initial")
        self.assertFalse(replay.arena_snapshot().cards)
        for position in (4, 2, 4, 0, 4):
            replay.seek(position)
        self.assertEqual(replay.arena_snapshot(), expected)
        with self.assertRaises(FrozenInstanceError):
            expected.cards[0].status = "changed"

    def test_public_arena_document_redacts_private_paths_identity_and_validation_arguments(self):
        document = arena_document(self.arena("READY_FOR_USER", True, (self.report(),)))
        text = json.dumps(document)
        for private in ("/Users/private", "token=hidden", "secret-provider-session", "Full prompt", "secret stderr", "--token"):
            self.assertNotIn(private, text)
        self.assertEqual(document["validation_reports"][0]["argv"], [Path(sys.executable).name])
        self.assertTrue(document["validation_reports"][0]["argv_redacted"])
        self.assertEqual(document["validation_reports"][0]["artifact_hash"], "a" * 64)
        self.assertFalse(arena_from_document(dict(document, validation_reports=[])).verified)

    def test_canonical_failed_validation_and_handoff_make_one_marker_across_snapshots(self):
        snapshot = self.arena("NEEDS_USER", False, (self.report(2),))
        replay = self.recording([(0, "recording.start", {}),
                                 (2, "arena.snapshot", arena_document(snapshot)),
                                 (3, "arena.snapshot", arena_document(snapshot)), (4, "recording.end", {})])
        self.assertEqual([item.kind for item in replay.bookmarks], ["handoff", "failure"])
        self.assertEqual(replay.next_failure(), 2)

    def test_legacy_handoff_updates_and_turn_events_project_without_guessing_actions(self):
        replay = self.recording([(0, "arena.mission", {"text": "Unstructured historical event"}),
                                 (1, "arena.turn", {"actor": "codex", "text": "SUCCEEDED: Finished public result"}),
                                 (2, "agent.handoff.ready", {"message_id": "message-1", "from": "codex", "to": "claude", "summary": "Review"}),
                                 (3, "agent.handoff.updated", {"message_id": "message-1", "status": "replied"})])
        replay.seek(0)
        self.assertFalse(replay.arena_snapshot().cards)
        replay.seek(3)
        self.assertEqual(replay.arena_snapshot().cards[0].status, "SUCCEEDED")
        self.assertEqual(len(replay.arena_snapshot().handoffs), 1)
        self.assertEqual(replay.arena_snapshot().handoffs[0].status, "replied")
        replay.seek(2)
        self.assertEqual(replay.arena_snapshot().handoffs[0].status, "ready")

    def test_recorded_commands_and_malformed_snapshot_never_start_services(self):
        with patch("subprocess.Popen", side_effect=AssertionError("process started")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("network opened")):
            replay = self.recording([(0, "presentation.changed", {"parameters": {"visual": "cube", "command": "touch forbidden", "arena_mode": "mission", "data_mode": "live"}}),
                                     (1, "command.requested", {"command": "touch forbidden"}),
                                     (2, "arena.snapshot", arena_document(self.arena()))])
            for position in (2, 0, 1, 2):
                replay.seek(position)
                replay.arena_snapshot()
                replay.presentation_snapshot()
            self.assertNotIn("arena_mode", replay.presentation_snapshot().parameters)
            with self.assertRaises(ValueError):
                self.recording([(0, "arena.snapshot", {"schema_version": 1, "cards": "invalid"})])

    def test_capsule_is_safe_exclusive_shareable_data_and_preserves_visual_phase(self):
        binding = BindingConfig("body.heart_rate", "pulse_bpm", source_id="PRIVATE-DEVICE-ID", expected_unit="bpm").to_dict()
        replay = self.recording([(0, "presentation.changed", presentation_document(
            {"visual": "reactor", "theme": "ice", "binding_config": binding, "command": "PRIVATE-PROMPT", "cwd": "/Users/private"}, 40, 80)),
                                 (5, "recording.end", {})])
        replay.seek(3)
        marker = replay.add_bookmark("PRIVATE-PROMPT /Users/private", offset=2)
        document = scene_capsule(replay.presentation_snapshot(), 17, (marker,))
        text = json.dumps(document)
        self.assertNotIn("PRIVATE-DEVICE-ID", text)
        self.assertNotIn("PRIVATE-PROMPT", text)
        self.assertNotIn("/Users/private", text)
        self.assertNotIn("manual-", text)
        self.assertEqual(document["presentation"]["visual_time"], 43)
        self.assertEqual(document["bookmarks"][0]["label"], "Manual bookmark")
        self.assertIsNone(document["presentation"]["parameters"]["binding_config"]["source_id"])
        path = self.path.with_name("capsule.json")
        export_scene_capsule(path, document)
        self.assertEqual(json.loads(path.read_text()), document)
        with self.assertRaises(FileExistsError):
            export_scene_capsule(path, document)
        self.assertEqual(json.loads(path.read_text()), document)
        with self.assertRaises(TypeError):
            replay.presentation_snapshot().parameters["binding_config"]["source_id"] = "mutated"

    def test_capsule_rejects_nonfinite_or_oversized_input_before_writing(self):
        path = self.path.with_name("bad-capsule.json")
        for presentation in ({"visual_time": float("nan")}, {"parameters": {"speed": float("inf")}},
                             {"parameters": {"visual": "x" * 70000}}):
            with self.subTest(presentation=str(presentation)[:80]), self.assertRaises(ValueError):
                export_scene_capsule(path, presentation)
            self.assertFalse(path.exists())

    def test_long_mission_public_snapshot_stays_within_event_budget(self):
        snapshot = self.arena()
        artifacts = tuple(Artifact("artifact-" + str(index), "patch", "a" * 64, "codex", "b" * 64,
                                   tuple("src/file_{}.py".format(number) for number in range(16)), "Public patch " * 1000)
                          for index in range(64))
        handoffs = tuple(Handoff("message-" + str(index), index, "codex", "claude", "Long public summary " * 1000,
                                 "Review the actual artifact", "replied", artifacts[:20]) for index in range(64))
        snapshot = replace(snapshot, artifacts=artifacts, handoffs=handoffs)
        document = arena_document(snapshot)
        self.assertLess(len(json.dumps(document).encode()), 65536)
        self.assertTrue(document["truncated"])
        projected = arena_from_document(document)
        self.assertEqual(projected.handoffs[-1].message_id, "message-63")
        self.assertEqual(projected.artifacts[-1].id, "artifact-63")

    def test_next_failure_still_finds_late_checks_after_visible_bookmark_limit(self):
        replay = self.recording([(index, "task.test.completed", {"exit_code": 2, "result": "failed"}) for index in range(1, 520)])
        self.assertEqual(len(replay.bookmarks), 512)
        replay.seek(517)
        self.assertEqual(replay.next_failure(), 518)

    def test_rotation_cannot_follow_symlink_to_a_private_file(self):
        private = Path(self.folder.name) / "private.jsonl"
        private.write_text("not a recording")
        linked = Path(self.folder.name) / "linked.jsonl"
        linked.symlink_to(private)
        self.path.write_text(json.dumps({"record_type": "rotation", "next": linked.name}))
        with self.assertRaisesRegex(ValueError, "regular files"):
            Replay(self.path)
