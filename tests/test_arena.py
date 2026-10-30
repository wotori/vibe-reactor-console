from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from devos.agents import (AgentCoordinator, Capabilities, CLIAdapter, ManagedTurn, TurnResult,
                          prepare_workspaces, snapshot_manifest, validate_workspaces)


class FakeAdapter:
    def __init__(self, actor):
        self.actor = actor
        self.commands = []

    def detect(self):
        return Capabilities(True, True, True, read_only=True, workspace_write=True)

    def command(self, profile="read-only", session_id=None):
        self.commands.append((profile, session_id))
        return ("fake", self.actor, profile, session_id or "new")


class FakeWorker:
    instances = []

    def __init__(self, argv, cwd, prompt, provider, timeout=900):
        self.argv, self.cwd, self.prompt, self.provider = argv, cwd, prompt, provider
        self.result, self.cancelled = None, False
        self.instances.append(self)

    def drain(self):
        return (), self.result

    def finish(self, text="A public proposal", status="SUCCEEDED"):
        self.result = TurnResult(status, text, "session-" + self.provider)

    def cancel(self):
        self.cancelled = True

    def close(self):
        self.finish("", "CANCELLED")


class ArenaTests(unittest.TestCase):
    def setUp(self):
        FakeWorker.instances = []
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.workspaces = {actor: str(root / actor) for actor in ("codex", "claude")}
        for path in self.workspaces.values():
            Path(path).mkdir()
            (Path(path) / "sample.py").write_text("before = 1\n")
        self.adapters = {actor: FakeAdapter(actor) for actor in self.workspaces}

    def coordinator(self, **kwargs):
        return AgentCoordinator(adapters=self.adapters, worker_factory=FakeWorker, **kwargs)

    def test_constructor_and_finite_demo_make_no_subprocess_or_model_calls(self):
        with patch("devos.agents.subprocess.Popen") as popen, patch("devos.agents.subprocess.run") as run:
            coordinator = AgentCoordinator()
            coordinator.update(100)
            self.assertEqual(coordinator.status, "DEMO_COMPLETE")
            self.assertEqual(len(coordinator.handoffs), 2)
            self.assertFalse(coordinator.step_demo())
            popen.assert_not_called()
            run.assert_not_called()
        with self.assertRaises(FrozenInstanceError):
            coordinator.snapshot().cards[0].status = "mutated"

    def test_parallel_initial_turns_then_only_completed_results_are_relayed(self):
        coordinator = self.coordinator()
        coordinator.start_mission("Design a bounded event bus", self.workspaces)
        self.assertEqual(len(coordinator.workers), 2)
        self.assertEqual({w.cwd for w in FakeWorker.instances}, {str(Path(path).resolve()) for path in self.workspaces.values()})
        FakeWorker.instances[0].finish("Proposal A")
        coordinator.update()
        self.assertEqual(len(coordinator.handoffs), 0)
        FakeWorker.instances[1].finish("Proposal B")
        coordinator.update()
        self.assertEqual(len(coordinator.handoffs), 2)
        self.assertEqual(len(FakeWorker.instances), 4)
        self.assertIn("Proposal A", FakeWorker.instances[2].prompt)
        self.assertIn("Proposal B", FakeWorker.instances[3].prompt)
        self.assertEqual(self.adapters["codex"].commands[-1][1], "session-codex")
        self.assertEqual(self.adapters["claude"].commands[-1][1], "session-claude")
        FakeWorker.instances[2].finish("Critique B")
        FakeWorker.instances[3].finish("Critique A")
        coordinator.update()
        self.assertEqual(coordinator.status, "READY_FOR_USER")
        self.assertFalse(coordinator.snapshot().verified)
        self.assertEqual(len(FakeWorker.instances), 4)

    def test_duplicate_message_and_no_progress_are_bounded(self):
        coordinator = self.coordinator(mode="relay")
        coordinator.start_mission("Review a design", self.workspaces, mode="relay")
        for worker in FakeWorker.instances:
            worker.finish("Public completed result")
        coordinator.update()
        first = coordinator.send_handoff("codex", "claude", "Public completed result", message_id="message-1")
        count = len(FakeWorker.instances)
        duplicate = coordinator.send_handoff("codex", "claude", "Public completed result", message_id="message-1")
        self.assertEqual(first, duplicate)
        self.assertEqual(len(FakeWorker.instances), count)
        coordinator.send_handoff("codex", "claude", "Public completed result", message_id="message-2")
        self.assertEqual(coordinator.reason, "NO_PROGRESS / identical artifact and verdict")

    def test_pause_does_not_dispatch_and_stop_waits_for_acknowledgment(self):
        coordinator = self.coordinator()
        coordinator.start_mission("Review a design", self.workspaces)
        coordinator.pause()
        self.assertEqual(coordinator.status, "PAUSING")
        for worker in FakeWorker.instances:
            worker.finish()
        coordinator.update()
        self.assertEqual(coordinator.status, "PAUSED")
        self.assertEqual(len(FakeWorker.instances), 2)
        coordinator.resume()
        coordinator.update()
        self.assertEqual(len(FakeWorker.instances), 4)
        coordinator.stop()
        self.assertTrue(all(w.cancelled for w in FakeWorker.instances[2:]))
        self.assertTrue(all(c.status == "CANCELLING" for c in coordinator.snapshot().cards))
        for worker in FakeWorker.instances[2:]:
            worker.finish("", "CANCELLED")
        coordinator.update()
        self.assertFalse(coordinator.workers)

    def test_deadline_handoff_limit_and_uncertain_delivery_never_retry(self):
        now = [0.0]
        coordinator = self.coordinator(clock=lambda: now[0])
        coordinator.start_mission("Review a design", self.workspaces, timeout=1)
        now[0] = 2
        coordinator.update()
        self.assertEqual(coordinator.status, "NEEDS_USER")
        self.assertIn("DEADLINE", coordinator.reason)
        self.assertTrue(all(w.cancelled for w in FakeWorker.instances))
        another = self.coordinator()
        another.start_mission("Review a design", self.workspaces, max_handoffs=1)
        for worker in FakeWorker.instances[-2:]:
            worker.finish()
        another.update()
        self.assertEqual(len(another.handoffs), 1)
        self.assertEqual(another.status, "NEEDS_USER")
        final = self.coordinator()
        final.start_mission("Review a design", self.workspaces)
        FakeWorker.instances[-2].finish("", "DELIVERY_UNKNOWN")
        final.update()
        count = len(FakeWorker.instances)
        final.update()
        self.assertEqual(final.status, "NEEDS_USER")
        self.assertEqual(len(FakeWorker.instances), count)

    def test_builder_actual_patch_not_claimed_tests_is_in_artifact(self):
        coordinator = self.coordinator()
        coordinator.start_mission("Improve code", self.workspaces, scenario="builder-challenger")
        (Path(self.workspaces["codex"]) / "sample.py").write_text("after = 2\n")
        for worker in FakeWorker.instances:
            worker.finish("I think the implementation is ready")
        coordinator.update()
        artifact = next(a for a in coordinator.artifacts if a.kind == "patch")
        self.assertEqual(artifact.paths, ("sample.py",))
        self.assertIn("-before = 1", artifact.text)
        self.assertIn("+after = 2", artifact.text)
        self.assertIn("ACTUAL PATCH", FakeWorker.instances[2].prompt)
        self.assertFalse(coordinator.verified)

    def test_workspace_alias_nesting_and_symlink_escape_rejected(self):
        same = self.workspaces["codex"]
        with self.assertRaises(ValueError):
            validate_workspaces({"codex": same, "claude": same})
        nested = Path(same) / "nested"
        nested.mkdir()
        with self.assertRaises(ValueError):
            validate_workspaces({"codex": same, "claude": nested})
        alias = Path(self.temporary.name) / "alias"
        alias.symlink_to(same, target_is_directory=True)
        with self.assertRaises(ValueError):
            validate_workspaces({"codex": same, "claude": alias})

    def test_snapshot_mode_works_without_commit_and_excludes_secret_links(self):
        root = Path(self.temporary.name)
        source = root / "source"
        source.mkdir()
        (source / "real.py").write_text("print('real')\n")
        (source / ".env").write_text("SECRET=never-copy")
        (source / "escape").symlink_to(root)
        workspaces = prepare_workspaces(source, root / "mission")
        for workspace in workspaces.values():
            self.assertTrue((Path(workspace) / "real.py").exists())
            self.assertFalse((Path(workspace) / ".env").exists())
            self.assertFalse((Path(workspace) / "escape").exists())
        self.assertEqual(snapshot_manifest(workspaces["codex"]), snapshot_manifest(workspaces["claude"]))
        with self.assertRaisesRegex(ValueError, "existing commit"):
            prepare_workspaces(source, root / "worktree", mode="worktree")

    def test_profiles_never_use_bypass_or_latest_session(self):
        for provider in ("codex", "claude"):
            adapter = CLIAdapter(provider, executable="fake")
            adapter.capabilities = Capabilities(True, True, True, read_only=True, workspace_write=True)
            command = adapter.command("read-only", session_id="session-explicit")
            self.assertIn("session-explicit", command)
            self.assertNotIn("--last", command)
            self.assertFalse(any("bypass" in arg for arg in command))
            if provider == "codex":
                self.assertIn("read-only", command)
            else:
                self.assertIn("--restricted", command)
                self.assertEqual(command[command.index("--tools") + 1], "Read,Glob,Grep")
            with self.assertRaises(ValueError):
                adapter.command(session_id="../../other")

    def test_validation_records_real_argv_hash_and_exit_and_detects_changed_content(self):
        coordinator = self.coordinator(mode="mission")
        coordinator.workspaces = validate_workspaces(self.workspaces)
        coordinator.start_validation(((sys.executable, "-c", "print('real check passed')"),))
        deadline = time.monotonic() + 5
        while coordinator.validation and time.monotonic() < deadline:
            coordinator.update()
            time.sleep(.01)
        self.assertTrue(coordinator.verified)
        self.assertEqual(coordinator.status, "READY_FOR_USER")
        report = coordinator.validation_reports[0]
        self.assertEqual(report.exit_code, 0)
        self.assertIn("real check passed", report.stdout)
        self.assertEqual(len(report.artifact_hash), 64)
        self.assertTrue(any(artifact.kind == "test_report" for artifact in coordinator.artifacts))
        coordinator.start_validation(((sys.executable, "-c", "from pathlib import Path; Path('sample.py').write_text('changed')"),))
        deadline = time.monotonic() + 5
        while coordinator.validation and time.monotonic() < deadline:
            coordinator.update()
            time.sleep(.01)
        self.assertFalse(coordinator.verified)
        self.assertEqual(coordinator.status, "NEEDS_USER")
        self.assertIn("changed", coordinator.reason)

    def test_failed_validation_never_becomes_verified(self):
        coordinator = self.coordinator(mode="mission")
        coordinator.workspaces = validate_workspaces(self.workspaces)
        coordinator.start_validation(((sys.executable, "-c", "import sys; sys.stderr.write('failure'); sys.exit(2)"),))
        deadline = time.monotonic() + 5
        while coordinator.validation and time.monotonic() < deadline:
            coordinator.update()
            time.sleep(.01)
        self.assertEqual(coordinator.validation_reports[0].exit_code, 2)
        self.assertEqual(coordinator.status, "NEEDS_USER")
        self.assertFalse(coordinator.verified)

    def test_two_fake_structured_cli_processes_complete_finite_managed_graph(self):
        class ProcessAdapter(FakeAdapter):
            def command(self, profile="read-only", session_id=None):
                if self.actor == "codex":
                    events = [{"type": "thread.started", "thread_id": "session-codex"},
                              {"type": "item.completed", "item": {"type": "agent_message", "text": "Codex public fake result"}},
                              {"type": "turn.completed", "usage": {}}]
                else:
                    events = [{"type": "result", "subtype": "success", "session_id": "session-claude",
                               "result": "Claude public fake result"}]
                code = "import sys,time; sys.stdin.read(); time.sleep(.05); " + "; ".join(
                    "print(" + repr(json.dumps(event)) + ",flush=True)" for event in events)
                return (sys.executable, "-c", code)

        coordinator = AgentCoordinator(mode="mission", adapters={actor: ProcessAdapter(actor) for actor in self.workspaces})
        self.addCleanup(coordinator.close)
        coordinator.start_mission("Two local fake processes, no model or API", self.workspaces)
        self.assertEqual(len(coordinator.workers), 2)
        deadline = time.monotonic() + 5
        while coordinator.status == "RUNNING" and time.monotonic() < deadline:
            coordinator.update()
            time.sleep(.01)
        self.assertEqual(coordinator.status, "READY_FOR_USER")
        self.assertEqual(len(coordinator.handoffs), 2)
        self.assertTrue(all(message.status == "replied" for message in coordinator.handoffs))
        self.assertEqual({card.session_id for card in coordinator.snapshot().cards}, {"session-codex", "session-claude"})


class ManagedTurnTests(unittest.TestCase):
    def worker(self, code, provider="codex", timeout=3):
        worker = ManagedTurn((sys.executable, "-c", code), Path.cwd(), "Explicit fake prompt", provider, timeout)
        self.addCleanup(worker.close)
        return worker

    def until_finished(self, worker, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            events, result = worker.drain()
            if result is not None:
                return result
            time.sleep(.01)
        self.fail("Fake managed worker did not complete")

    def test_concurrent_stderr_and_json_stdout_do_not_deadlock(self):
        events = [{"type": "thread.started", "thread_id": "session-codex"},
                  {"type": "item.completed", "item": {"type": "agent_message", "text": "public answer"}},
                  {"type": "turn.completed", "usage": {"input_tokens": 2}}]
        code = "import sys,json; sys.stdin.read(); sys.stderr.write('x'*300000); sys.stderr.flush(); " + "; ".join(
            "print(" + repr(json.dumps(event)) + ",flush=True)" for event in events)
        result = self.until_finished(self.worker(code))
        self.assertEqual(result.status, "SUCCEEDED")
        self.assertEqual(result.text, "public answer")
        self.assertEqual(result.session_id, "session-codex")

    def test_claude_final_result_parsed_but_deltas_do_not_complete_turn(self):
        events = [{"type": "system", "subtype": "init", "session_id": "session-claude"},
                  {"type": "stream_event", "event": {"type": "content_block_delta", "text": "ignore"}},
                  {"type": "result", "subtype": "success", "result": "completed", "session_id": "session-claude",
                   "total_cost_usd": .02, "usage": {"output_tokens": 3}}]
        code = "import sys; sys.stdin.read(); " + "; ".join("print(" + repr(json.dumps(event)) + ",flush=True)" for event in events)
        result = self.until_finished(self.worker(code, "claude"))
        self.assertEqual(result.text, "completed")
        self.assertEqual(result.cost, .02)

    def test_malformed_and_missing_completion_are_not_relayable(self):
        malformed = self.until_finished(self.worker("import sys; sys.stdin.read(); print('not json')"))
        self.assertEqual(malformed.status, "FAILED")
        delta = self.until_finished(self.worker("import sys; sys.stdin.read(); print('{\"type\":\"item.updated\"}')"))
        self.assertEqual(delta.status, "DELIVERY_UNKNOWN")

    def test_timeout_and_cancellation_reap_owned_process(self):
        worker = self.worker("import sys,time; sys.stdin.read(); time.sleep(20)", timeout=.08)
        result = self.until_finished(worker)
        self.assertEqual(result.status, "DELIVERY_UNKNOWN")
        self.assertIsNotNone(worker._process.poll())
        cancelled = self.worker("import sys,time; sys.stdin.read(); time.sleep(20)")
        time.sleep(.04)
        cancelled.cancel()
        self.assertEqual(self.until_finished(cancelled).status, "CANCELLED")
        self.assertIsNotNone(cancelled._process.poll())
