"""Actual event recordings preserve mathematical selections across rewind."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from devos.cli import parser, runtime_options
from devos.recordings import Recorder, Replay
from devos.runtime import Runtime
from devos.session import Session
from devos.sources import SourceProvider
from devos.telemetry import DataEvent
from test_runtime import FakeArena, FakeHub, FakeTasks, FakeTerminals


class ScopeReplayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "math.jsonl"
        self.clock = 100.
        services = patch.multiple("devos.runtime", TelemetryHub=FakeHub, TerminalManager=FakeTerminals,
                                  AgentCoordinator=FakeArena, TaskRunner=FakeTasks)
        services.start()
        self.addCleanup(services.stop)
        monotonic = patch("devos.runtime.time.monotonic", side_effect=lambda: self.clock)
        monotonic.start()
        self.addCleanup(monotonic.stop)

    def runtime(self, *arguments):
        args = parser().parse_args(["--no-config", "--bundled", "--seed", "17", *arguments])
        session = Session(**runtime_options(args), sources=SourceProvider(bundled=True))
        runtime = Runtime(args, session)
        self.addCleanup(runtime.close)
        return runtime, session

    def record_math(self):
        runtime, session = self.runtime("--scope-mode", "parabola", "--scope-auto-cycle", "2")
        session.visual_time, session.protocol_time = 35., 70.
        runtime.start_recording(self.path)
        for elapsed in (1, 2):
            self.clock = 100. + elapsed
            session.update(1)
            runtime.update(1)
        self.assertEqual(session.effective_scope_mode, "lissajous")
        self.clock = 103.
        session.choose_scope_mode("rose")
        session.update(1)
        runtime.update(1)
        self.clock = 105.
        session.update(2)
        runtime.update(2)
        self.assertEqual(session.effective_scope_mode, "spiral")
        self.clock = 106.
        runtime.action("record")
        runtime.close()

    def test_recorded_auto_and_manual_switches_restore_at_exact_anchors_after_rewind(self):
        with patch("subprocess.Popen", side_effect=AssertionError("Math recording launched a process")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("Math recording connected")):
            self.record_math()
            replay = Replay(self.path)
            self.addCleanup(replay.close)
            self.assertEqual(replay.duration, 6)
            for offset, expected in ((0, "parabola"), (1.999, "parabola"), (2, "lissajous"),
                                     (3, "rose"), (5, "spiral"), (1, "parabola"),
                                     (4, "rose"), (2, "lissajous"), (6, "spiral")):
                with self.subTest(offset=offset):
                    replay.seek(offset)
                    presentation = replay.presentation_snapshot()
                    self.assertEqual(presentation.parameters["scope_mode"], expected)
                    self.assertEqual(presentation.parameters["scope_auto_cycle"], 2)
                    self.assertEqual(presentation.visual_time, 35 + offset)
                    self.assertEqual(presentation.protocol_time, 70 + offset)

            runtime, session = self.runtime("--replay", str(self.path))
            for offset, expected in ((5, "spiral"), (0, "parabola"), (3, "rose"), (2, "lissajous")):
                runtime.replay.seek(offset)
                runtime.update(0)
                self.assertEqual(session.effective_scope_mode, expected)
                session.update(20)
                self.assertEqual(session.effective_scope_mode, expected)
                runtime.update(0)
                self.assertEqual(session.visual_time, 35 + offset)
            self.assertEqual(runtime.terminals.created, [])
            self.assertEqual(runtime.tasks.started, [])

    def test_legacy_recording_without_math_preferences_remains_readable_with_rotation_off(self):
        with Recorder(self.path, parameters={"visual": "reactor", "scope": True}) as recorder:
            recorder.record(DataEvent("end", "recording.stopped", "fixture", "fixture-run", 1,
                                      "demo", None, "2026-10-31T12:00:00Z", {}), 4)
        with patch("subprocess.Popen", side_effect=AssertionError("Legacy replay launched a process")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("Legacy replay connected")):
            runtime, session = self.runtime("--replay", str(self.path))
            runtime.replay.seek(3)
            runtime.update(0)
        self.assertEqual(session.effective_scope_mode, "harmonics")
        self.assertEqual(session.options.scope_auto_cycle, 0)
        self.assertEqual(session.animation, "reactor")


if __name__ == "__main__":
    unittest.main()
