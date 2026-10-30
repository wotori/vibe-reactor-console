"""User-visible workspace behavior across commands, data bindings and rendering."""

from dataclasses import replace
import math
from types import MappingProxyType
import unittest
from unittest import mock

from devos.agents import AgentCard, ArenaSnapshot, Handoff
from devos.app import dispatch_key, terminal_rectangles
from devos.cli import parser, runtime_options
from devos.commands import catalog, search
from devos.presets import preferences, resolve
from devos.renderer import Renderer
from devos.runtime import Runtime
from devos.screen_registry import SCREENS, SCREEN_BY_ID
from devos.session import Session
from devos.telemetry import DataEvent, MetricSample, TelemetrySnapshot
from devos.terminal import Canvas
from devos.shell import TerminalFrame

from tests.test_rendering import Screen


def measured(key, value, unit="sessions", quality="ok", mode="live"):
    return MetricSample(key, value, unit, "fixture", "fixture-run", 1, None, 0.0,
                        mode, quality, window_seconds=60 if key.startswith("site.") else None)


def data_event(identifier, topic, payload, sequence=1, mode="live"):
    return DataEvent(identifier, topic, "fixture", "fixture-run", sequence, mode,
                     None, "2026-10-31T12:00:00Z", payload)


class WorkspaceSessionTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(seed=42, animation="gravity-well")

    def command(self, identifier):
        return next(item for item in catalog() if item.id == identifier)

    def test_searching_and_running_a_screen_command_does_not_launch_a_program(self):
        session = self.session
        session.handle("palette")
        for char in "screen body":
            session.palette_key(ord(char))
        self.assertTrue(session.palette_commands)
        self.assertTrue(all(item.kind == "screen" and item.value == "body" for item in session.palette_commands))
        with mock.patch("subprocess.Popen", side_effect=AssertionError("Screen command launched a child")):
            session.palette_key(13)
        self.assertEqual(session.screen_id, "body")
        self.assertFalse(session.palette_open)
        self.assertEqual(tuple(session.pending_actions), ())
        self.assertTrue(session.running)

    def test_q_is_palette_filter_text_and_escape_closes_without_quitting(self):
        session = self.session
        session.handle("palette")
        session.palette_key(ord("q"))
        self.assertEqual(session.palette_query, "q")
        self.assertTrue(any("queue" in item.title.lower() for item in session.palette_commands))
        session.handle("quit")  # Dashboard actions are suppressed by an overlay.
        self.assertTrue(session.running)
        session.palette_key(27)
        self.assertFalse(session.palette_open)
        self.assertTrue(session.running)

    def test_changing_screens_preserves_work_and_exposes_a_valid_panel(self):
        session = self.session
        session.update(3)
        elapsed, code_state = session.sim.elapsed, session.sim.rng.getstate()
        session.terminal_slot, session.terminal_focused = "right", True
        session.terminal_frame = object()
        frame = session.terminal_frame
        for spec in SCREENS:
            session.handle("screen:" + spec.id)
            self.assertIn(session.focused_panel, spec.panels)
            self.assertFalse(session.terminal_focused)
            session.handle("panel")
            self.assertIn(session.focused_panel, spec.panels)
            session.handle("zoom")
            self.assertTrue(session.zoomed)
            session.choose_screen("cockpit")
            self.assertFalse(session.zoomed)
            self.assertEqual((session.sim.elapsed, session.sim.rng.getstate()), (elapsed, code_state))
            self.assertIs(session.terminal_frame, frame)

    def test_escape_restores_zoom_and_reverse_panel_navigation_is_available(self):
        session = self.session
        session.choose_screen("visual-lab")
        first = session.focused_panel
        session.handle("panel")
        self.assertNotEqual(session.focused_panel, first)
        session.handle("panel-prev")
        self.assertEqual(session.focused_panel, first)
        session.handle("zoom")
        session.handle("escape")
        self.assertFalse(session.zoomed)
        self.assertTrue(session.running)

    def test_imported_preferences_reproduce_seed_and_never_execute_embedded_commands(self):
        session = self.session
        document = dict(resolve("showcase"), seed=917, background="black", fps_cap=90,
                        command="rm -rf imaginary", argv=["untrusted"], task="untrusted",
                        token="secret", source_file="/private/source.json")
        with mock.patch("subprocess.Popen", side_effect=AssertionError("Preset launched a child")):
            session.apply_preferences(document)
        saved = preferences(session)
        self.assertEqual((saved["screen"], saved["visual"], saved["seed"]), ("visual-lab", "torus-knot", 917))
        self.assertEqual((session.background, session.options.fps), ("black", 90))
        self.assertEqual(session.visual_parameters()["seed"], 917)
        self.assertFalse(set(saved) & {"command", "argv", "task", "token", "source_file"})
        self.assertFalse(set(session.pending_actions) & {"task", "terminal", "record", "handoff"})

    def test_preferences_can_request_source_switch_without_doing_io_in_session(self):
        session = self.session
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("Preference update used network")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("Preference update launched child")):
            session.execute_command(self.command("data:live"))
        self.assertEqual(session.data_mode, "live")
        self.assertEqual(tuple(session.pending_actions), ("data-mode",))

    def test_online_zero_is_a_real_low_signal_and_not_a_missing_sample(self):
        session = self.session
        session.publish_telemetry(TelemetrySnapshot(metrics=(measured("site.active_sessions_60s", 0),)))
        empty = session.visual_parameters()
        session.publish_telemetry(TelemetrySnapshot(metrics=(measured("site.active_sessions_60s", 200),)))
        busy = session.visual_parameters()
        self.assertLess(empty["particle_count"], busy["particle_count"])
        self.assertEqual(session.telemetry.metric("site.active_sessions_60s").value, 200)
        self.assertLessEqual(busy["particle_count"], 200)

    def test_pulse_binding_accepts_stale_value_but_unavailable_has_no_measured_bpm(self):
        session = self.session
        session.lab_binding = "pulse"
        heart = measured("body.heart_rate", 72, "bpm", quality="stale")
        session.publish_telemetry(TelemetrySnapshot(metrics=(heart,)))
        self.assertEqual(session.visual_parameters()["pulse_bpm"], 72)
        missing = replace(heart, value=None, quality="unavailable")
        session.publish_telemetry(TelemetrySnapshot(metrics=(missing,)))
        parameters = session.visual_parameters()
        self.assertNotIn("pulse_bpm", parameters)
        self.assertIsNone(session.telemetry.metric("body.heart_rate").value)
        self.assertTrue(all(math.isfinite(value) for value in parameters.values() if isinstance(value, (float, int))))

    def test_selected_binding_does_not_silently_apply_unselected_personal_pulse(self):
        session = self.session
        session.lab_binding = "online"
        session.publish_telemetry(TelemetrySnapshot(metrics=(measured("site.active_sessions_60s", 28),
                                                            measured("body.heart_rate", 120, "bpm"))))
        self.assertNotIn("pulse_bpm", session.visual_parameters())
        session.execute_command(self.command("binding:pulse"))
        self.assertEqual(session.visual_parameters()["pulse_bpm"], 120)

    def test_test_binding_uses_exact_counts_when_known_and_never_invents_them_from_exit_code(self):
        session = self.session
        session.lab_binding = "tests"
        completion = data_event("suite-1", "task.test.completed", {"run_id": "suite-1", "exit_code": 0})
        session.publish_telemetry(TelemetrySnapshot(events=(completion,)))
        self.assertFalse(session.visual_parameters().get("test_total"))
        counted = data_event("suite-2", "task.test.completed", {"run_id": "suite-2", "exit_code": 1,
                                                                  "tests_passed": 14, "tests_total": 15}, 2)
        session.publish_telemetry(TelemetrySnapshot(events=(completion, counted)))
        parameters = session.visual_parameters()
        self.assertEqual((parameters["test_passed"], parameters["test_total"]), (14, 15))
        self.assertEqual(session.telemetry.events[-1].payload["exit_code"], 1)

    def test_newer_uncounted_test_run_supersedes_an_older_counted_success(self):
        session = self.session
        session.lab_binding = "tests"
        passed = data_event("demo-success", "demo.build.finished", {"run_id": "demo", "tests_passed": 16,
                            "tests_total": 16, "result": "passed"}, mode="demo")
        failed = data_event("actual-failure", "task.test.completed", {"run_id": "real", "exit_code": 7,
                            "result": "failed"}, 2)
        session.publish_telemetry(TelemetrySnapshot(events=(passed,)))
        self.assertEqual(session.visual_parameters()["test_passed"], 16)
        session.publish_telemetry(TelemetrySnapshot(events=(passed, failed)))
        parameters = session.visual_parameters()
        self.assertFalse(parameters.get("test_total"))
        self.assertFalse(parameters.get("test_passed"))

    def runtime(self, session, *arguments):
        args = parser().parse_args(["--no-config", "--bundled"] + list(arguments))
        runtime_options(args)
        runtime = Runtime(args, session)
        self.addCleanup(runtime.close)
        return runtime

    def test_palette_terminal_on_replay_keeps_dashboard_keyboard_owner(self):
        session = self.session
        runtime = self.runtime(session, "--replay", "examples/events.jsonl")
        session.execute_command(self.command("action:terminal"))
        runtime.update(0)
        self.assertFalse(session.terminal_focused)
        self.assertEqual(runtime.terminals.snapshot().status, "NOT STARTED / human-owned terminal")
        dispatch_key(session, runtime, ord("q"))
        self.assertFalse(session.running)

    def test_palette_terminal_creates_a_child_and_reveals_it_from_a_zoomed_visual(self):
        session = self.session
        runtime = self.runtime(session)

        class Child:
            def __init__(self, *args, **kwargs):
                pass

            def set_visible(self, visible):
                pass

            def snapshot(self):
                return TerminalFrame(status="RUNNING TEST CHILD")

            def close(self):
                pass

        runtime.terminals.factory = Child
        session.focused_panel, session.zoomed = "visual", True
        session.execute_command(self.command("action:terminal"))
        runtime.update(0)
        self.assertTrue(session.terminal_focused)
        self.assertEqual(session.terminal_frame.status, "RUNNING TEST CHILD")
        visible = dict(terminal_rectangles(Screen(44, 160), session))
        self.assertIn(session.terminal_slot, visible)
        self.assertEqual(session.focused_panel, "source")

    def test_queue_binding_tracks_pending_recipients_and_not_historical_exchange_count(self):
        session = self.session
        session.lab_binding = "queue"
        cards = (AgentCard("codex", "Codex", "builder", "IDLE"),
                 AgentCard("claude", "Claude", "reviewer", "IDLE"))
        replied = tuple(Handoff("old-" + str(index), index, "codex", "claude", "done", "review", "replied") for index in range(6))
        session.arena = ArenaSnapshot("relay", "READY", cards, (), replied, (), 10, 10, 300)
        idle = session.visual_parameters()
        self.assertEqual((idle["queue_left"], idle["queue_right"]), (0, 0))
        pending = Handoff("pending", 7, "codex", "claude", "ready", "review", "ready")
        session.arena = replace(session.arena, handoffs=replied + (pending,))
        busy = session.visual_parameters()
        self.assertEqual((busy["queue_left"], busy["queue_right"]), (0, 1))
        self.assertGreater(busy["energy"], idle["energy"])

    def test_only_new_effect_events_trigger_bursts_and_metric_polls_do_not_restart_them(self):
        session = self.session
        self.assertGreaterEqual(session.visual_parameters()["burst_age"], 1.2)
        session.visual_time = 5
        cue = data_event("cue-1", "visual.cue", {"cue": "traffic-surge", "duration_ms": 1200, "strength": .7})
        session.publish_telemetry(TelemetrySnapshot(events=(cue,)))
        self.assertEqual(session.visual_parameters()["burst_age"], 0)
        session.update(2)
        poll = data_event("poll-2", "metric.sample", {"key": "site.active_sessions_60s", "value": 28}, 2)
        session.publish_telemetry(TelemetrySnapshot(events=(cue, poll)))
        self.assertGreaterEqual(session.visual_parameters()["burst_age"], 1.2)
        session.publish_telemetry(TelemetrySnapshot(events=(cue, poll)))
        self.assertGreaterEqual(session.visual_parameters()["burst_age"], 1.2)

    def test_real_renderer_reads_workspace_state_without_advancing_sources_or_randomness(self):
        session = self.session
        session.publish_telemetry(TelemetrySnapshot(metrics=(measured("site.active_sessions_60s", 28),)))
        session.update(3)
        screen = Screen(44, 160)
        renderer = Renderer(Canvas(screen, color=False))
        for spec in SCREENS:
            session.choose_screen(spec.id)
            renderer.draw(session)  # Resolve lazy module imports before IO guards.
        before = (session.sim.elapsed, session.visual_time, session.protocol_time,
                  session.sim.rng.getstate(), session.telemetry, tuple(session.pending_actions),
                  session._last_event, session._event_time)
        with mock.patch("subprocess.Popen", side_effect=AssertionError("Renderer launched a child")), \
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("Renderer polled network")), \
                mock.patch("pathlib.Path.read_text", side_effect=AssertionError("Renderer read a source file")):
            for spec in SCREENS:
                session.choose_screen(spec.id)
                renderer.draw(session)
                first = screen.text()
                renderer.draw(session)
                self.assertEqual(first, screen.text())
                self.assertEqual(before, (session.sim.elapsed, session.visual_time, session.protocol_time,
                    session.sim.rng.getstate(), session.telemetry, tuple(session.pending_actions),
                    session._last_event, session._event_time))
        parameters = session.visual_parameters()
        self.assertIsInstance(parameters, MappingProxyType)
        with self.assertRaises(TypeError):
            parameters["energy"] = 999
