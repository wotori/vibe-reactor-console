"""Real workspace service roundtrips; all agent responses are local fixtures."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from devos.agents import AgentCard, Artifact, Handoff
from devos.cli import parser, runtime_options
from devos.recordings import Recorder, Replay
from devos.runtime import Runtime
from devos.session import Session
from devos.sources import SourceProvider


REAL_MONOTONIC = time.monotonic


class Clock:
    value = 100.

    def __call__(self):
        return self.value


class ReplayWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.flight = self.root / "flight.jsonl"
        self.exports = self.root / "exports"

    def runtime(self, *arguments):
        args = parser().parse_args(["--no-config", "--export-dir", str(self.exports), *arguments])
        session = Session(**runtime_options(args), sources=SourceProvider(bundled=True))
        runtime = Runtime(args, session)
        self.addCleanup(runtime.close)
        return runtime, session

    def record_checked_workspace(self):
        runtime, session = self.runtime("--data-mode", "live", "--agents", "watch", "--animation", "cube")
        session.seed = 31
        session.visual_time, session.protocol_time = 40., 70.
        session.choose_screen("arena")
        left, right = self.root / "builder", self.root / "reviewer"
        left.mkdir()
        right.mkdir()
        code = b"def value():\n    return 1\n"
        (left / "example.py").write_bytes(code)
        (right / "example.py").write_bytes(code)
        arena = runtime.arena
        arena.mode, arena.status, arena.phase = "mission", "NEEDS_USER", "review-approved"
        arena.workspaces = {"codex": str(left), "claude": str(right)}
        summary = 'Public fixture reply {"token":"PRIVATE_TOKEN"} --api-key PRIVATE_KEY /Users/private/project'
        arena.cards = {"codex": AgentCard("codex", "Codex", "Builder", "SUCCEEDED", summary, "PRIVATE_SESSION"),
                       "claude": AgentCard("claude", "Claude", "Reviewer", "SUCCEEDED", "Fixture review approved the captured bytes")}
        artifact = Artifact("patch-1", "patch", hashlib.sha256(code).hexdigest(), "codex", "b" * 64,
                            ("example.py",), "Public fixture patch " + summary)
        arena.artifacts.append(artifact)
        arena.handoffs.append(Handoff("message-1", 1, "codex", "claude", summary, "Review the exact artifact", "replied", (artifact,)))
        arena._event("turn", "codex", "SUCCEEDED: " + summary)
        clock = Clock()
        with patch("devos.runtime.time.monotonic", side_effect=clock):
            runtime.start_recording(self.flight)
            clock.value = 101.
            session.update(1)
            runtime.update(1)
            runtime.action("replay-bookmark")
            # This is an explicitly invoked local check, not an agent/model job.
            arena.start_validation([(sys.executable, "-c", "print('LOCAL_CHECK_PASSED')")], workspace="codex")
            deadline = REAL_MONOTONIC() + 3
            while arena.validation and REAL_MONOTONIC() < deadline:
                runtime.update(0)
                time.sleep(.01)
            self.assertTrue(arena.verified)
            report = arena.validation_reports[-1]
            self.assertEqual(report.exit_code, 0)
            self.assertIn("LOCAL_CHECK_PASSED", report.stdout)
            clock.value = 102.
            session.choose_animation("torus-knot")
            session.options.consumer = "space"
            session.update(1)
            runtime.update(1)
            clock.value = 103.
            session.update(1)
            session.paused = True
            runtime.update(0)
            clock.value = 105.
            runtime.update(0)
            runtime.action("record")
        runtime.close()
        return report

    def test_runtime_recording_restores_nonzero_initial_phase_visual_pause_and_check_evidence(self):
        with patch("devos.agents.CLIAdapter.detect", side_effect=AssertionError("Agent CLI probe")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("Network request")):
            actual = self.record_checked_workspace()
        text = self.flight.read_text()
        for private in ("PRIVATE_TOKEN", "PRIVATE_KEY", "PRIVATE_SESSION", "/Users/private/project"):
            self.assertNotIn(private, text)
        replay = Replay(self.flight)
        self.assertEqual(len(replay.event_offsets), len(replay.events))
        self.assertIsInstance(replay.event_offsets, tuple)
        self.assertEqual(replay.duration, 5)
        self.assertEqual(replay.presentation_snapshot().visual_time, 40)
        self.assertEqual(replay.presentation_snapshot().protocol_time, 70)
        self.assertEqual(replay.presentation_snapshot().parameters["visual"], "cube")
        replay.seek(2)
        self.assertEqual(replay.presentation_snapshot().parameters["visual"], "torus-knot")
        self.assertEqual(replay.presentation_snapshot().parameters["consumer"], "space")
        replay.seek(4)
        presentation = replay.presentation_snapshot()
        self.assertTrue(presentation.paused)
        self.assertEqual((presentation.visual_time, presentation.protocol_time), (43, 73))
        arena = replay.arena_snapshot()
        self.assertEqual((arena.mode, arena.status, arena.phase), ("replay", "READY_FOR_USER", "review-approved"))
        self.assertTrue(arena.verified)
        self.assertEqual(arena.validation_reports[-1].artifact_hash, actual.artifact_hash)
        self.assertEqual(arena.validation_reports[-1].exit_code, actual.exit_code)
        self.assertEqual(arena.handoffs[-1].message_id, "message-1")
        self.assertEqual(arena.handoffs[-1].status, "replied")
        self.assertTrue(any(item.kind == "manual" for item in replay.bookmarks))

    def test_replay_workspace_actions_do_not_launch_jobs_and_emergency_stop_uses_real_service(self):
        self.record_checked_workspace()
        with patch("subprocess.Popen", side_effect=AssertionError("Replay launched a process")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("Replay opened network")):
            runtime, session = self.runtime("--replay", str(self.flight))
            runtime.replay.seek(4)
            runtime.update(0)
            self.assertEqual(session.screen_id, "replay")
            self.assertEqual(session.arena.mode, "replay")
            self.assertIs(session.arena, session.replay_arena)
            displayed = session.arena
            self.assertIsNot(displayed, runtime.arena)
            for action in ("task", "handoff", "handoff-send", "event", "mission-pause", "mission-validate",
                           "arena-mode", "data-mode", "record", "terminal-start"):
                runtime.action(action)
                self.assertIn("read-only", session.status_message)
            self.assertFalse(runtime.terminals._terminals)
            self.assertEqual(runtime.tasks.snapshot().state, "idle")
            with patch.object(runtime.arena, "stop", wraps=runtime.arena.stop) as stop:
                runtime.action("mission-stop")
                stop.assert_called_once()
                self.assertEqual(runtime.arena.status, "CANCELLED")
                self.assertEqual(displayed.status, "READY_FOR_USER")

    def test_replay_speed_bookmarks_capsule_and_public_mission_exports_roundtrip(self):
        actual = self.record_checked_workspace()
        with patch("subprocess.Popen", side_effect=AssertionError("Export launched a process")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("Export opened network")):
            runtime, session = self.runtime("--replay", str(self.flight))
            runtime.replay.seek(2)
            runtime.update(0)
            runtime.action("replay-speed-up")
            self.assertEqual(runtime.replay.speed, 2)
            runtime.action("replay-bookmark")
            self.assertTrue(any(item.kind == "manual" and item.offset == 2 for item in runtime.replay.bookmarks))
            runtime.action("replay-play")
            runtime.update(.5)
            self.assertEqual(runtime.replay.playhead, 3)
            self.assertEqual(session.visual_time, 43)
            runtime.action("replay-play")
            runtime.action("capsule-export")
            capsule = next(self.exports.glob("*.capsule.json"))
            document = json.loads(capsule.read_text())
            self.assertEqual(document["seed"], 31)
            self.assertEqual(document["presentation"]["visual_time"], 43)
            self.assertEqual(document["presentation"]["parameters"]["visual"], "torus-knot")
            self.assertTrue(document["bookmarks"])
            imported_args = parser().parse_args(["--no-config", "--preset", str(capsule)])
            imported = Session(**runtime_options(imported_args), sources=SourceProvider(bundled=True))
            self.assertEqual((imported.animation, imported.visual_time, imported.protocol_time), ("torus-knot", 43, 73))
            self.assertTrue(imported.paused)
            self.assertIsNone(imported_args.terminal_command)
            self.assertIsNone(imported_args.mission)
            runtime.action("mission-export")
        report_path = next(self.exports.glob("*-mission/report.json"))
        report = json.loads(report_path.read_text())
        markdown = report_path.with_name("report.md").read_text()
        self.assertEqual(report["origin"], "recorded")
        self.assertTrue(report["arena"]["verified"])
        self.assertEqual(report["arena"]["validation_reports"][-1]["exit_code"], 0)
        self.assertEqual(report["arena"]["validation_reports"][-1]["artifact_hash"], actual.artifact_hash)
        self.assertIn(actual.artifact_hash, markdown)
        self.assertIn("exit: 0", markdown)
        exported = report_path.read_text() + markdown + capsule.read_text()
        for private in ("PRIVATE_TOKEN", "PRIVATE_KEY", "PRIVATE_SESSION", "/Users/private/project", str(self.root / "builder")):
            self.assertNotIn(private, exported)

    def test_quiet_live_recording_keeps_full_duration_and_initial_clock(self):
        runtime, session = self.runtime("--data-mode", "live", "--agents", "watch")
        session.visual_time, session.protocol_time = 80, 120
        clock = Clock()
        with patch("devos.runtime.time.monotonic", side_effect=clock):
            runtime.start_recording(self.flight)
            clock.value = 110
            runtime.action("record")
        replay = Replay(self.flight)
        self.assertEqual(replay.duration, 10)
        replay.seek(10)
        self.assertEqual((replay.presentation_snapshot().visual_time, replay.presentation_snapshot().protocol_time), (90, 130))

    def test_public_text_redaction_is_idempotent_for_json_flags_and_bearer_tokens(self):
        from devos.playback import _text
        original = '{"token":"PRIVATE_TOKEN", "password": "PRIVATE PASSWORD"} --api-key PRIVATE_KEY Authorization: Bearer PRIVATE_BEARER /Users/private/project'
        public = _text(original)
        for private in ("PRIVATE_TOKEN", "PRIVATE PASSWORD", "PRIVATE_KEY", "PRIVATE_BEARER", "/Users/private/project"):
            self.assertNotIn(private, public)
        self.assertEqual(_text(public), public)

    def test_public_event_redaction_preserves_explicit_prompt_and_terminal_opt_ins(self):
        from devos.telemetry import DataEvent
        with Recorder(self.flight, include_prompts=True, include_terminal=True) as recorder:
            recorder.record(DataEvent("public", "arena.fixture", "fixture", "fixture-run", 1, "live", None,
                                      "2026-11-01T12:00:00Z", {
                                          "text": '{"token":"PRIVATE_TOKEN"} /Users/private/project',
                                          "prompt": "EXPLICIT_RAW_PROMPT /Users/private/project",
                                          "terminal_text": "EXPLICIT_RAW_TERMINAL",
                                          "cwd": "/Users/private/cwd", "argv": ["python", "--api-key", "PRIVATE_KEY"]}), 0)
        text = self.flight.read_text()
        self.assertIn("EXPLICIT_RAW_PROMPT /Users/private/project", text)
        self.assertIn("EXPLICIT_RAW_TERMINAL", text)
        self.assertNotIn("PRIVATE_TOKEN", text)
        self.assertNotIn("PRIVATE_KEY", text)
        self.assertNotIn("/Users/private/cwd", text)
