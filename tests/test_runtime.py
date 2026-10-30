"""Service lifecycle contracts without launching terminals, models or workers."""

from dataclasses import replace
from types import MappingProxyType
import unittest
from unittest.mock import patch

from devos.agents import AgentCard, ArenaSnapshot
from devos.cli import parser, runtime_options
from devos.recordings import RecordingStatus, ReplayStatus
from devos.runtime import Runtime
from devos.session import Session
from devos.shell import TerminalFrame
from devos.task_runner import TaskSnapshot
from devos.telemetry import DataEvent, MetricSample, TelemetrySnapshot


def snapshot(mode, value):
    return TelemetrySnapshot(metrics=(MetricSample("site.active_sessions_60s", value, "sessions",
        mode + "-source", "run-1", 1, None, 0, mode),))


class FakeHub:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs, self.listeners, self.pending = kwargs, [], []
        self.closed, self.updates = False, []
        self.state = snapshot("live", 42)
        self.instances.append(self)

    def add_listener(self, callback):
        self.listeners.append(callback)

    def submit(self, event):
        self.pending.append(event)
        return True

    def update(self, dt=0, demo_paused=False):
        self.updates.append((dt, demo_paused))
        pending, self.pending = self.pending, []
        for event in pending:
            for callback in self.listeners:
                callback(event, dt)
        return self.state

    def close(self):
        self.closed = True


class FakeTerminals:
    instances = []

    def __init__(self, **kwargs):
        self.configuration, self.created, self.closed, self.focused = {}, [], False, None
        self.instances.append(self)

    def configure(self, slot, command=None, cwd=None):
        self.configuration[slot] = (command, cwd)

    def create(self, slot):
        self.created.append(slot)

    def focus(self, slot):
        self.focused = slot

    def snapshot(self, slot):
        return TerminalFrame(status="fake terminal")

    def snapshots(self):
        return ()

    def close(self, slot=None):
        self.closed = True


class FakeArena:
    instances = []

    def __init__(self, mode="demo"):
        self.mode, self.status, self.closed = mode, "IDLE", False
        self.updated, self.steps, self.stops, self.sent = 0, 0, 0, []
        self.cards = (AgentCard("codex", "Codex", "implementer", "SUCCEEDED", "A completed public result."),
                      AgentCard("claude", "Claude", "reviewer", "IDLE"))
        self.events = ()
        self.instances.append(self)

    def snapshot(self):
        return ArenaSnapshot(self.mode, self.status, self.cards, self.events, (), (), 0, 6, 120)

    def update(self, dt):
        self.updated += 1

    def step_demo(self):
        self.steps += 1

    def send_handoff(self, sender, recipient, text):
        self.sent.append((sender, recipient, text))

    def pause(self):
        self.status = "PAUSED"

    def resume(self):
        self.status = "RUNNING"

    def stop(self):
        self.stops += 1

    def close(self):
        self.closed = True


class FakeTasks:
    instances = []

    def __init__(self, callback=None):
        self.callback, self.started, self.closed = callback, [], False
        self.instances.append(self)

    def start(self, argv, cwd=None):
        self.started.append((argv, cwd))

    def snapshot(self):
        return TaskSnapshot()

    def close(self):
        completion = getattr(self, "completion_on_close", None)
        if completion is not None:
            self.callback(completion)
            self.completion_on_close = None
        self.closed = True


class FakeRecorder:
    instances = []

    def __init__(self, path, **kwargs):
        self.path, self.kwargs, self.events, self.closed = str(path), kwargs, [], False
        self.instances.append(self)

    def record(self, event, elapsed=0):
        self.events.append((event, elapsed))

    def snapshot(self):
        return RecordingStatus(self.path, not self.closed, len(self.events))

    def close(self):
        self.closed = True


class FakeReplay:
    instances = []
    bookmarks = ()
    events, event_offsets = (), ()

    def __init__(self, path):
        self.path, self.playhead, self.playing, self.closed = path, 0, False, False
        self.manifest = {"parameters": {"schema_version": 1, "arena_mode": "watch", "data_mode": "live",
                                        "visual": "cube", "command": "never execute"}}
        self.state, self.seeks = snapshot("replay", 8), []
        self.instances.append(self)

    def snapshot(self):
        return ReplayStatus(self.path, self.playhead, 20, self.playing, 1, 2, 17)

    def telemetry_snapshot(self):
        return self.state

    def presentation_snapshot(self):
        from devos.playback import PresentationSnapshot
        return PresentationSnapshot({"visual": "cube"}, self.playhead, self.playhead)

    def arena_snapshot(self):
        return ArenaSnapshot("replay", "NOT RECORDED", (), (), (), (), self.playhead, 0, 0)

    def update(self, dt):
        if self.playing:
            self.playhead += dt

    def seek(self, position):
        self.seeks.append(position)
        self.playhead = max(0, min(20, position))

    def next_failure(self):
        self.seek(10)

    def close(self):
        self.closed = True


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.clock = 0.0
        for kind in (FakeHub, FakeTerminals, FakeArena, FakeTasks, FakeRecorder, FakeReplay):
            kind.instances.clear()
        self.services = patch.multiple("devos.runtime", TelemetryHub=FakeHub, TerminalManager=FakeTerminals,
                                      AgentCoordinator=FakeArena, TaskRunner=FakeTasks,
                                      Recorder=FakeRecorder, Replay=FakeReplay)
        self.services.start()
        self.addCleanup(self.services.stop)
        monotonic = patch("devos.runtime.time.monotonic", side_effect=lambda: self.clock)
        monotonic.start()
        self.addCleanup(monotonic.stop)

    def runtime(self, *arguments):
        args = parser().parse_args(["--no-config", "--bundled", *arguments])
        session = Session(**runtime_options(args))
        runtime = Runtime(args, session)
        self.addCleanup(runtime.close)
        return runtime, session

    def event(self, sequence):
        return DataEvent("event-" + str(sequence), "demo.build.finished", "fixture", "run-1", sequence,
                         "demo", None, "2026-10-31T12:00:00Z", {"status": "finished"})

    def test_configured_task_runs_only_after_explicit_ui_action(self):
        runtime, session = self.runtime("--task", "python3 -m unittest")
        runtime.update(.1)
        self.assertEqual(runtime.tasks.started, [])
        self.assertEqual(runtime.terminals.created, [])
        session.handle("task")
        runtime.update(.1)
        self.assertEqual(runtime.tasks.started, [(("python3", "-m", "unittest"), None)])

    def test_recording_restart_and_data_reset_deliver_each_event_exactly_once(self):
        runtime, session = self.runtime()
        runtime.start_recording("/not-written/first.jsonl")
        first = runtime.recorder
        runtime.submit(self.event(1))
        runtime.update(.1)
        self.assertEqual([event.id for event, _ in first.events if event.id.startswith("event-")], ["event-1"])
        self.assertEqual([event.topic for event, _ in first.events[:2]], ["presentation.changed", "arena.snapshot"])
        runtime.action("record")
        self.assertTrue(first.closed)
        runtime.start_recording("/not-written/second.jsonl")
        second = runtime.recorder
        runtime.submit(self.event(2))
        runtime.update(.1)
        self.assertEqual([event.id for event, _ in second.events if event.id.startswith("event-")], ["event-2"])
        old = runtime.hub
        runtime.reset_hub()
        self.assertTrue(old.closed)
        runtime.submit(self.event(3))
        runtime.update(.1)
        self.assertEqual([event.id for event, _ in second.events if event.id.startswith("event-")], ["event-2", "event-3"])

    def test_a_new_recording_uses_its_own_relative_clock(self):
        runtime, _ = self.runtime()
        self.clock = 100
        runtime.start_recording("/not-written/later.jsonl")
        self.clock = 100.5
        runtime.record_event(self.event(1))
        self.assertEqual([offset for event, offset in runtime.recorder.events if event.id == "event-1"], [.5])

    def test_replay_manifest_and_controls_never_launch_live_jobs(self):
        runtime, session = self.runtime("--replay", "/not-read/fixture.jsonl")
        self.assertEqual(session.screen_id, "replay")
        self.assertEqual(tuple(session.pending_actions), ())
        self.assertEqual(runtime.terminals.created, [])
        original_arena, original_hub = runtime.arena, runtime.hub
        session.pending_actions.extend(("task", "handoff", "event", "arena-mode", "data-mode", "mission-pause"))
        runtime.update(.1)
        self.assertEqual(runtime.tasks.started, [])
        self.assertEqual(original_arena.sent, [])
        self.assertEqual(original_arena.steps, 0)
        self.assertIs(runtime.arena, original_arena)
        self.assertIs(runtime.hub, original_hub)
        session.handle("terminal")
        runtime.ensure_terminal()
        self.assertEqual(runtime.terminals.created, [])
        self.assertFalse(session.terminal_focused)

    def test_emergency_mission_stop_remains_available_from_replay_screen(self):
        runtime, session = self.runtime()
        runtime.arena.mode, runtime.arena.status = "mission", "RUNNING"
        session.choose_screen("replay")
        session.handle("mission-stop")
        runtime.update(.1)
        self.assertEqual(runtime.arena.stops, 1)

    def test_replay_presentation_does_not_change_underlying_service_modes(self):
        runtime, session = self.runtime("--replay", "/not-read/fixture.jsonl")
        self.assertEqual(session.arena_mode, runtime.arena.mode)
        self.assertEqual(session.data_mode, "auto")
        self.assertEqual(session.animation, "cube")

    def test_replay_and_live_snapshots_remain_separate_while_switching_screens(self):
        runtime, session = self.runtime("--replay", "/not-read/fixture.jsonl")
        runtime.update(.1)
        self.assertIs(session.telemetry, runtime.replay.state)
        session.choose_screen("site")
        runtime.update(.1)
        self.assertIs(session.telemetry, runtime.live_snapshot)
        self.assertEqual(session.telemetry.metrics[0].value, 42)
        session.choose_screen("replay")
        runtime.update(.1)
        self.assertIs(session.telemetry, runtime.replay.state)
        self.assertEqual(session.telemetry.metrics[0].value, 8)

    def test_completed_managed_reply_is_eligible_for_explicit_handoff(self):
        runtime, session = self.runtime()
        runtime.arena.mode = session.arena_mode = "relay"
        runtime.action("handoff")
        self.assertEqual(runtime.arena.sent, [("codex", "claude", "A completed public result.")])
        runtime.arena.cards = (replace(runtime.arena.cards[0], status="RUNNING"), runtime.arena.cards[1])
        runtime.action("handoff")
        self.assertEqual(len(runtime.arena.sent), 1)

    def test_menu_pauses_demo_but_never_stops_draining_managed_workers(self):
        runtime, session = self.runtime()
        session.open_menu("animations")
        runtime.update(.1)
        self.assertEqual(runtime.arena.updated, 0)
        self.assertTrue(runtime.hub.updates[-1][1])
        runtime.arena.mode = "relay"
        runtime.update(.1)
        self.assertEqual(runtime.arena.updated, 1)

    def test_close_owns_every_worker_and_constructor_failure_cleans_up(self):
        runtime, _ = self.runtime("--replay", "/not-read/fixture.jsonl")
        runtime.start_recording("/not-written/recording.jsonl")
        runtime.close()
        self.assertTrue(all(service.closed for service in (
            runtime.hub, runtime.terminals, runtime.tasks, runtime.arena, runtime.recorder, runtime.replay)))
        args = parser().parse_args(["--no-config", "--replay", "/not-read/bad.jsonl"])
        session = Session(**runtime_options(args))
        with patch("devos.runtime.Replay", side_effect=ValueError("invalid manifest")):
            with self.assertRaisesRegex(ValueError, "invalid manifest"):
                Runtime(args, session)
        self.assertTrue(FakeHub.instances[-1].closed)
        self.assertTrue(FakeTerminals.instances[-1].closed)
        self.assertTrue(FakeArena.instances[-1].closed)
        self.assertTrue(FakeTasks.instances[-1].closed)

    def test_shutdown_flushes_task_completion_before_closing_recording(self):
        runtime, _ = self.runtime()
        runtime.start_recording("/not-written/final.jsonl")
        event = DataEvent("completion", "task.test.completed", "task-runner", "run-1", 1,
                          "live", None, "2026-10-31T12:00:00Z", {"state": "cancelled", "exit_code": -15})
        runtime.tasks.completion_on_close = event
        runtime.close()
        self.assertTrue(runtime.recorder.closed)
        self.assertIn("completion", [item.id for item, _ in runtime.recorder.events])


if __name__ == "__main__":
    unittest.main()
