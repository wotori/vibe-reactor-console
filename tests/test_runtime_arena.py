from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from devos.agents import AgentCoordinator, Capabilities, TurnResult
from devos.cli import parser, runtime_options
from devos.runtime import Runtime
from devos.session import Session
from devos.sources import SourceProvider
from devos.terminals import TerminalManager


class Adapter:
    def detect(self):
        return Capabilities(True, can_stream=True, can_resume=True, read_only=True, workspace_write=True)

    def command(self, profile="read-only", session_id=None):
        return ("never-run-a-model", profile, session_id or "new")


class Turn:
    instances = []

    def __init__(self, argv, cwd, prompt, provider, timeout=900):
        self.argv, self.cwd, self.prompt, self.provider = argv, cwd, prompt, provider
        self.result, self.cancelled = None, False
        self.instances.append(self)

    def drain(self):
        return (), self.result

    def finish(self, status="SUCCEEDED"):
        self.result = TurnResult(status, self.provider + " completed public result", "session-" + self.provider)

    def cancel(self):
        self.cancelled = True

    def close(self):
        self.finish("CANCELLED")


class RuntimeArenaTests(unittest.TestCase):
    def setUp(self):
        Turn.instances = []
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        for actor in ("codex", "claude"):
            (self.root / actor).mkdir()
            (self.root / actor / "sample.py").write_text("example = 1\n")

    def managed_args(self, mode="relay"):
        return ["--agents", mode, "--mission", "A bounded local fake task", "--workspace-left", str(self.root / "codex"),
                "--workspace-right", str(self.root / "claude")]

    def runtime(self, *arguments, coordinator=None):
        args = parser().parse_args(["--no-config", *arguments])
        options = runtime_options(args)
        session = Session(**options, sources=SourceProvider(bundled=True))
        coordinator = coordinator or AgentCoordinator(mode=options["arena_mode"], adapters={actor: Adapter() for actor in ("codex", "claude")},
                                                      worker_factory=Turn)
        with patch("devos.runtime.AgentCoordinator", return_value=coordinator):
            runtime = Runtime(args, session)
        self.addCleanup(runtime.close)
        return runtime, session

    def test_explicit_managed_cli_starts_two_isolated_fake_workers_and_selects_arena(self):
        with patch("devos.agents.subprocess.Popen") as popen, patch("devos.agents.subprocess.run") as run:
            runtime, session = self.runtime(*self.managed_args())
            self.assertEqual(session.screen_id, "arena")
            self.assertEqual(runtime.arena.mode, "relay")
            self.assertEqual(len(Turn.instances), 2)
            self.assertEqual({turn.cwd for turn in Turn.instances}, {str(self.root / "codex"), str(self.root / "claude")})
            runtime.update(.1)
            self.assertTrue(all(card.status == "RUNNING" for card in session.arena.cards))
            popen.assert_not_called()
            run.assert_not_called()

    def test_completed_public_reply_can_be_manually_handed_off(self):
        runtime, session = self.runtime(*self.managed_args())
        for turn in Turn.instances:
            turn.finish()
        runtime.update(.1)
        self.assertEqual(runtime.arena.status, "READY")
        session.terminal_slot = "left"
        runtime.action("handoff")
        self.assertEqual(len(Turn.instances), 3)
        self.assertEqual(Turn.instances[-1].provider, "claude")
        self.assertIn("codex completed public result", Turn.instances[-1].prompt)
        self.assertEqual(runtime.arena.snapshot().handoffs[-1].status, "delivered")

    def test_handoff_composer_sends_the_human_edited_text_only_on_accept(self):
        runtime, session = self.runtime(*self.managed_args())
        for turn in Turn.instances:
            turn.finish()
        runtime.update(.1)
        session.handle("handoff")
        self.assertTrue(session.composer_open)
        self.assertEqual(len(Turn.instances), 2)
        session.composer_key(21)
        for char in "Check the bounded queue and include edge cases.":
            session.composer_key(ord(char))
        self.assertEqual(len(Turn.instances), 2)
        session.composer_key(13)
        runtime.update(.01)
        self.assertFalse(session.composer_open)
        self.assertEqual(len(Turn.instances), 3)
        self.assertIn("Check the bounded queue and include edge cases.", Turn.instances[-1].prompt)
        self.assertNotIn("codex completed public result", Turn.instances[-1].prompt)

    def test_unfinished_response_never_handed_off_and_watch_never_relays(self):
        runtime, session = self.runtime(*self.managed_args())
        runtime.action("handoff")
        self.assertEqual(len(Turn.instances), 2)
        self.assertIn("completed", session.status_message)
        watching, watch_session = self.runtime("--agents", "watch")
        watching.action("handoff")
        self.assertEqual(len(Turn.instances), 2)
        self.assertIn("human-owned", watch_session.status_message)

    def test_pause_stop_controls_remain_effective_while_ui_is_paused(self):
        runtime, session = self.runtime(*self.managed_args("mission"))
        runtime.action("mission-pause")
        self.assertEqual(runtime.arena.status, "PAUSING")
        session.paused = True
        for turn in Turn.instances:
            turn.finish()
        runtime.update(.1)
        self.assertEqual(runtime.arena.status, "PAUSED")
        self.assertEqual(len(Turn.instances), 2)
        runtime.action("mission-pause")
        runtime.update(.1)
        self.assertEqual(len(Turn.instances), 4)
        runtime.action("mission-stop")
        self.assertTrue(all(turn.cancelled for turn in Turn.instances[-2:]))
        for turn in Turn.instances[-2:]:
            turn.finish("CANCELLED")
        runtime.update(.1)
        self.assertFalse(runtime.arena.workers)
        self.assertEqual(runtime.arena.status, "CANCELLED")

    def test_task_only_starts_after_explicit_action_and_reports_actual_exit_code(self):
        runtime, session = self.runtime("--task", sys.executable + " -c 'import sys; print(\"LOCAL_CHECK\"); sys.exit(2)'")
        self.assertEqual(runtime.tasks.snapshot().state, "idle")
        runtime.update(.1)
        self.assertEqual(runtime.tasks.snapshot().state, "idle")
        runtime.action("task")
        deadline = time.monotonic() + 3
        while runtime.tasks.snapshot().state == "running" and time.monotonic() < deadline:
            runtime.update(.01)
            time.sleep(.01)
        runtime.update(.01)
        self.assertEqual(session.task_snapshot.exit_code, 2)
        self.assertIn("LOCAL_CHECK", session.task_snapshot.output)

    def test_startup_failure_closes_previously_created_owned_services(self):
        args = parser().parse_args(["--no-config", "--record", str(self.root / "recording.jsonl")])
        session = Session(**runtime_options(args), sources=SourceProvider(bundled=True))
        terminals = TerminalManager(terminal_factory=lambda *args, **kwargs: None)
        coordinator = AgentCoordinator()
        with patch("devos.runtime.TerminalManager", return_value=terminals), \
                patch.object(terminals, "close", wraps=terminals.close) as close_terminals, \
                patch("devos.runtime.AgentCoordinator", return_value=coordinator), \
                patch.object(coordinator, "close", wraps=coordinator.close) as close_arena, \
                patch("devos.runtime.Recorder", side_effect=OSError("recording unavailable")), \
                patch("devos.runtime.TelemetryHub.close", autospec=True) as close_hub:
            with self.assertRaisesRegex(OSError, "recording unavailable"):
                Runtime(args, session)
            close_terminals.assert_called_once()
            close_arena.assert_called_once()
            close_hub.assert_called_once()

    def test_explicit_mission_validation_runs_task_against_selected_workspace(self):
        runtime, session = self.runtime(*self.managed_args(), "--task", sys.executable + " -c 'print(\"REAL_VALIDATION\")'")
        for turn in Turn.instances:
            turn.finish()
        runtime.update(.1)
        self.assertEqual(runtime.arena.status, "READY")
        self.assertFalse(runtime.arena.verified)
        session.terminal_slot = "right"
        runtime.action("mission-validate")
        deadline = time.monotonic() + 3
        while runtime.arena.validation and time.monotonic() < deadline:
            runtime.update(.01)
            time.sleep(.01)
        runtime.update(.01)
        self.assertTrue(runtime.arena.verified)
        report = runtime.arena.validation_reports[-1]
        self.assertEqual(report.cwd, str(self.root / "claude"))
        self.assertEqual(report.exit_code, 0)
        self.assertIn("REAL_VALIDATION", report.stdout)
        self.assertEqual(session.arena.validation_reports[-1], report)
