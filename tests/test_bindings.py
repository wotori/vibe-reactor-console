"""Exact smoothing, truthful selected sources and deterministic finite cues."""

from dataclasses import FrozenInstanceError, replace
import math
from types import SimpleNamespace
import unittest

from devos.agents import Handoff
from devos.bindings import BINDINGS, BINDING_BY_ID, BindingConfig, BindingEngine, transform
from devos.director import EventDirector, INTENSITY_PROFILES
from devos.telemetry import DataEvent, MetricSample, TelemetrySnapshot


def sample(value=0, key="site.active_sessions_60s", source="site", session="s1", quality="ok", unit="sessions"):
    return MetricSample(key, value, unit, source, session, 1, None, 0, "live", quality=quality,
                        age_seconds=2, window_seconds=60)


def snapshot(value=0, **options):
    return TelemetrySnapshot(metrics=(sample(value, **options),))


def event(sequence, topic="agent.handoff.ready", offset=0, payload=None, mode="replay", source="fixture"):
    return DataEvent("event-" + str(sequence), topic, source, "s1", sequence, mode, None,
                     "2026-10-31T12:00:00Z", payload or {}, origin_mode="demo" if mode == "replay" else None,
                     recorded_offset_ms=round(offset * 1000) if offset is not None else None)


class BindingTests(unittest.TestCase):
    def config(self, **values):
        return BindingConfig("site.active_sessions_60s", "energy", input_max=100, **values)

    def test_builtin_registry_is_frozen_and_serializes_config_without_side_effects(self):
        self.assertEqual(tuple(item.id for item in BINDINGS), ("online", "pulse", "tests", "queue", "git"))
        for spec in BINDINGS:
            self.assertEqual(BindingConfig.from_dict(spec.config.to_dict()), spec.config)
            with self.assertRaises(FrozenInstanceError):
                spec.config.parameter = "changed"

    def test_linear_log_clamp_and_reversed_output_have_exact_results(self):
        linear = BindingConfig("git.changed_files", "energy", input_min=10, input_max=20,
                               output_min=2, output_max=4, smoothing_seconds=0)
        self.assertEqual(transform(15, linear), 3)
        self.assertEqual(transform(100, linear), 4)
        self.assertEqual(transform(-100, linear), 2)
        self.assertEqual(transform(25, replace(linear, clamp=False)), 5)
        self.assertEqual(transform(15, replace(linear, output_min=4, output_max=2)), 3)
        log = replace(linear, normalization="log", input_min=0, input_max=99, output_min=0, output_max=1)
        self.assertAlmostEqual(transform(9, log), .5)

    def test_ema_uses_exact_time_constant_and_is_frame_partition_independent(self):
        config = self.config(smoothing_seconds=2)
        first, split = BindingEngine(), BindingEngine()
        first.update(snapshot(0), 0, config)
        split.update(snapshot(0), 0, config)
        whole = first.update(snapshot(100), 2, config)
        for _ in range(20):
            partitioned = split.update(snapshot(100), .1, config)
        self.assertAlmostEqual(whole.smoothed, 1 - math.exp(-1))
        self.assertAlmostEqual(whole.smoothed, partitioned.smoothed)
        self.assertEqual(whole.raw, 100)
        self.assertEqual(whole.transformed, 1)
        self.assertEqual(first.update(snapshot(0), 0, config).smoothed, whole.smoothed)

    def test_source_session_and_configuration_changes_reset_smoothing(self):
        engine, config = BindingEngine(), self.config(smoothing_seconds=20)
        engine.update(snapshot(0), 0, config)
        self.assertEqual(engine.update(snapshot(100, source="new-site"), .1, config).smoothed, 1)
        self.assertEqual(engine.update(snapshot(50, source="new-site", session="s2"), .1, config).smoothed, .5)
        self.assertEqual(engine.update(snapshot(50), .1, replace(config, output_max=2)).smoothed, 1)

    def test_first_measurement_initializes_without_treating_startup_missing_as_zero(self):
        engine, config = BindingEngine(), self.config(smoothing_seconds=20)
        self.assertIsNone(engine.update(snapshot(None, quality="unavailable"), 0, config).raw)
        self.assertEqual(engine.update(snapshot(100), 0, config).smoothed, 1)

    def test_missing_is_not_zero_and_fade_retains_explicit_unavailable_metadata(self):
        engine = BindingEngine()
        config = self.config(smoothing_seconds=2, missing="fade")
        engine.update(snapshot(100), 0, config)
        missing = engine.update(snapshot(None, quality="unavailable"), 2, config)
        self.assertIsNone(missing.raw)
        self.assertIsNone(missing.transformed)
        self.assertEqual(missing.quality, "unavailable")
        self.assertAlmostEqual(missing.smoothed, math.exp(-1))
        neutral = engine.update(snapshot(None, quality="unavailable"), .1, replace(config, missing="neutral"))
        self.assertEqual(neutral.smoothed, 0)
        self.assertEqual(engine.update(snapshot(0), .1, config).raw, 0)

    def test_stale_source_value_age_units_and_window_remain_exact(self):
        frame = BindingEngine().update(snapshot(28, quality="stale"), .1, self.config())
        self.assertEqual((frame.raw, frame.quality, frame.age_seconds, frame.unit, frame.window_seconds),
                         (28, "stale", 2, "sessions", 60))
        with self.assertRaises(TypeError):
            frame.parameters["energy"] = 3

    def test_nonfinite_types_ranges_and_unit_mismatch_are_rejected(self):
        for field, value in (("input_min", math.nan), ("input_max", math.inf), ("output_max", True),
                             ("smoothing_seconds", -1), ("neutral_value", math.nan),
                             ("parameter", []), ("source_key", "invalid key")):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                replace(self.config(), **{field: value})
        with self.assertRaises(ValueError):
            self.config(normalization="log", input_min=-1)
        with self.assertRaises(ValueError):
            BindingConfig.from_dict({"command": "never"})
        engine = BindingEngine()
        for value in (math.inf, math.nan, True, "28"):
            frame = engine.update(snapshot(value), .1, self.config())
            self.assertEqual(frame.quality, "error")
            self.assertIsNone(frame.raw)
        mismatch = engine.update(snapshot(1, unit="requests/s"), .1, self.config(expected_unit="sessions"))
        self.assertEqual(mismatch.quality, "error")
        with self.assertRaises(ValueError):
            engine.update(snapshot(), math.nan, self.config())
        overflow = BindingConfig("site.active_sessions_60s", "energy", input_max=1,
                                 output_max=1e308, clamp=False)
        self.assertEqual(engine.update(snapshot(1e308), .1, overflow).quality, "error")

    def test_only_selected_source_is_read_and_unselected_bpm_is_never_emitted(self):
        calls = []
        def measured(key):
            calls.append(key)
            if key == "body.heart_rate":
                raise AssertionError("Unselected private sensor was read")
            return sample(28)
        frame = BindingEngine().update(SimpleNamespace(metric=measured), .1, BINDING_BY_ID["online"].config)
        self.assertEqual(calls, ["site.active_sessions_60s"])
        self.assertNotIn("pulse_bpm", frame.parameters)

    def test_stream_view_hides_even_cached_body_derived_energy(self):
        config = BindingConfig("body.heart_rate", "energy", input_min=30, input_max=240)
        engine = BindingEngine()
        engine.update(snapshot(140, key="body.heart_rate", unit="bpm"), .1, config)
        hidden = engine.update(snapshot(140, key="body.heart_rate", unit="bpm"), 0, config, stream_view=True)
        self.assertEqual(dict(hidden.parameters), {})
        self.assertIsNone(hidden.raw)
        self.assertIsNone(hidden.smoothed)
        self.assertIn("Private", hidden.missing_reason)

    def test_latest_canonical_test_run_supersedes_old_counts_and_unknown_is_not_success(self):
        known = event(1, "demo.build.finished", payload={"tests_passed": 14, "tests_total": 15})
        actual = event(2, "task.test.completed", payload={"exit_code": 0, "state": "completed"})
        config = BINDING_BY_ID["tests"].config
        engine = BindingEngine()
        old = engine.update(TelemetrySnapshot(events=(known,)), 0, config)
        self.assertEqual((old.parameters["test_passed"], old.parameters["test_total"]), (14, 15))
        new = engine.update(TelemetrySnapshot(events=(known, actual)), 0, config)
        self.assertEqual(new.raw, 1)
        self.assertNotIn("test_passed", new.parameters)
        unknown = event(3, "task.test.completed", payload={"state": "cancelled", "exit_code": -15})
        frame = engine.update(TelemetrySnapshot(events=(known, actual, unknown)), .1, config)
        self.assertIsNone(frame.raw)
        self.assertEqual(frame.quality, "error")

    def test_confirmed_zero_queue_is_distinct_from_missing_arena_and_counts_recipients(self):
        config, engine = BINDING_BY_ID["queue"].config, BindingEngine()
        self.assertIsNone(engine.update(None, .1, config).raw)
        arena = SimpleNamespace(mode="demo", handoffs=())
        self.assertEqual(engine.update(None, .1, config, arena).raw, 0)
        arena.handoffs = (Handoff("1", 1, "codex", "claude", "review", "reply", "ready"),
                          Handoff("2", 2, "claude", "codex", "fix", "patch", "delivered"))
        frame = engine.update(None, .1, config, arena)
        self.assertEqual((frame.raw, frame.parameters["queue_left"], frame.parameters["queue_right"]), (2, 1, 1))
        arena.mode = "replay"
        self.assertEqual(engine.update(None, .1, config, arena).mode, "replay")


class DirectorTests(unittest.TestCase):
    def test_canonical_visual_cue_alias_duration_and_strength_are_supported_and_bounded(self):
        cue = event(1, "visual.cue", 0, {"cue": "traffic-surge", "duration_ms": 1200, "strength": .7})
        director = EventDirector()
        frame = director.update((cue,), .2)
        self.assertEqual(frame.cue.kind, "surge")
        self.assertAlmostEqual(frame.cue.duration, 1.2)
        self.assertAlmostEqual(frame.parameters["cue_strength"], .8 * .7 * (1 - .2 / 1.2))
        self.assertIsNone(director.update((cue,), 1.3).cue)
        oversized = event(2, "visual.cue", 0, {"cue": "testfailed", "duration_ms": 1e100, "strength": 100})
        frame = EventDirector().update((oversized,), 0)
        self.assertEqual((frame.cue.kind, frame.cue.duration, frame.cue.strength), ("failure", 5, 1))

    def test_cue_is_id_based_finite_and_duplicate_does_not_restart_it(self):
        director = EventDirector()
        events = (event(1, offset=2),)
        frame = director.update(events, 2.5)
        self.assertEqual(frame.cue.id, "event-1")
        self.assertEqual(frame.parameters["burst_age"], .5)
        self.assertEqual(director.update(events, 3).parameters["burst_age"], 1)
        self.assertIsNone(director.update(events, 4).cue)

    def test_failure_wins_over_handoff_and_repeated_type_is_debounced(self):
        director = EventDirector()
        events = (event(1, offset=1), event(2, offset=1.05),
                  event(3, "task.test.completed", 1.1, {"exit_code": 1}))
        frame = director.update(events, 1.2, spectator=True)
        self.assertEqual(frame.cue.kind, "failure")
        self.assertEqual(frame.cue.priority, 100)
        handoff = EventDirector().update(events[:2], 1.1)
        self.assertEqual(handoff.cue.count, 2)

    def test_reconstruction_and_backward_seek_are_frame_independent(self):
        events = (event(1, offset=1), event(2, "task.test.completed", 12, {"exit_code": 1}),
                  event(3, "site.surge", 24))
        director = EventDirector()
        for tick in range(251):
            stepped = director.update(events, tick / 10, spectator=True)
        direct = EventDirector().reconstruct(events, 25, spectator=True)
        self.assertEqual(stepped, direct)
        self.assertEqual(direct.recommended_visual, "gravity-well")
        self.assertEqual(director.update(events, 1.5, spectator=True),
                         EventDirector().reconstruct(events, 1.5, spectator=True))

    def test_manual_visual_and_terminal_focus_suppress_scene_recommendations(self):
        for options in ({"terminal_focused": True}, {"manual_visual": "avatar"}, {"spectator": False}):
            director = EventDirector()
            frame = director.update((event(1),), .1, spectator=options.pop("spectator", True), **options)
            self.assertIsNone(frame.recommended_visual)
            self.assertIsNotNone(frame.cue)

    def test_surge_hysteresis_does_not_retrigger_near_threshold(self):
        director = EventDirector(surge_high=100, surge_low=70)
        def online(sequence, value, offset):
            return event(sequence, "metric.sample", offset,
                         {"key": "site.active_sessions_60s", "value": value, "quality": "ok"})
        all_events = []
        for sequence, (value, at) in enumerate(((101, 0), (99, 2), (101, 4), (60, 6), (105, 8)), 1):
            all_events.append(online(sequence, value, at))
            frame = director.update(all_events, at)
            self.assertEqual(frame.cue is not None, at in (0, 8))

    def test_state_is_bounded_and_unchanged_journal_does_not_replay_evicted_ids(self):
        director = EventDirector(max_seen=16)
        events = tuple(event(index, offset=index / 10) for index in range(1, 1001))
        director.update(events, 100)
        before = director.state_size
        frame = director.update(events, 105)
        self.assertIsNone(frame.cue)
        self.assertLessEqual(before["seen"], 16)
        self.assertLessEqual(before["sources"], 256)
        self.assertLessEqual(before["active"], 5)
        self.assertEqual(frame.parameters["event_sequence"], 1000)
        with self.assertRaises(TypeError):
            frame.parameters["burst_age"] = 0

    def test_private_body_events_do_not_drive_any_cue_or_sequence(self):
        frame = EventDirector().update((event(1, "body.signal"), event(2, "metric.sample", payload={
            "key": "body.heart_rate", "value": 170})), .1, spectator=True)
        self.assertIsNone(frame.cue)
        self.assertEqual(frame.parameters["event_sequence"], 0)

    def test_profiles_and_invalid_time_are_explicit(self):
        self.assertLess(INTENSITY_PROFILES["calm"].particle_limit, INTENSITY_PROFILES["showcase"].particle_limit)
        for value in (math.nan, math.inf, -1, True):
            with self.assertRaises(ValueError):
                EventDirector().update((), value)
        with self.assertRaises(ValueError):
            EventDirector().update((), 0, intensity="loud")
        for kind in (None, [], {}, 12):
            malformed = event(1, "visual.cue", payload={"kind": kind})
            self.assertIsNone(EventDirector().update((malformed,), 0).cue)


if __name__ == "__main__":
    unittest.main()
