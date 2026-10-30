"""Replay EMA is event-anchored, age-aware and independent of playback frames."""

from dataclasses import replace
import math
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from devos.agents import Handoff
from devos.binding_replay import ReplayBindings
from devos.bindings import BINDING_BY_ID, BindingConfig
from devos.telemetry import DataEvent


STAMP = "2026-10-31T12:00:00Z"


def event(sequence, offset, value=None, topic="metric.sample", payload=None, source="fixture", session="s1", origin="live"):
    if payload is None:
        payload = {"key": "site.active_sessions_60s", "value": value, "unit": "sessions", "quality": "ok", "window_seconds": 60}
    return DataEvent("event-" + str(sequence), topic, source, session, sequence, "replay", None,
                     STAMP, payload, origin_mode=origin, recorded_offset_ms=round(offset * 1000))


class ReplayBindingTests(unittest.TestCase):
    def replay(self, events, config=None, preferences=None):
        document = dict(preferences or {})
        if config is not None:
            document["binding_config"] = config.to_dict()
        replay = ReplayBindings(events, tuple(item.recorded_offset_ms / 1000 for item in events), document)
        self.addCleanup(replay.close)
        return replay

    def config(self, **values):
        return BindingConfig("site.active_sessions_60s", "energy", input_max=100, smoothing_seconds=2, **values)

    def test_exact_zero_order_hold_ema_is_the_same_for_seek_and_different_frame_rates(self):
        events = (event(1, 0, 0), event(2, 10, 100), event(3, 20, 50))
        direct = self.replay(events, self.config()).seek(23)
        expected = .5 + ((1 - math.exp(-5)) - .5) * math.exp(-1.5)
        self.assertAlmostEqual(direct.smoothed, expected)
        for step in (.25, .5, 1, 2, 4, 8):
            replay = self.replay(events, self.config())
            head = 0
            while head < 23:
                replay.seek(head)
                head += step
            self.assertEqual(replay.seek(23), direct)

    def test_rewind_and_repeat_do_not_accumulate_smoothing_or_reset_measurement_age(self):
        events = (event(1, 0, 0), event(2, 10, 100), event(3, 20, 50))
        replay = self.replay(events, self.config())
        first = replay.seek(18)
        replay.seek(23)
        self.assertEqual(replay.seek(18), first)
        self.assertEqual(replay.seek(18), first)
        self.assertEqual(first.age_seconds, 8)

    def test_ttl_crossing_splits_hold_then_fade_instead_of_fading_the_whole_gap(self):
        replay = self.replay((event(1, 0, 100),), self.config(missing="fade"))
        self.assertEqual(replay.seek(16).quality, "stale")
        expired = replay.seek(62)
        self.assertIsNone(expired.raw)
        self.assertEqual(expired.quality, "unavailable")
        self.assertEqual(expired.age_seconds, 62)
        self.assertAlmostEqual(expired.smoothed, math.exp(-1))

    def test_initial_source_age_and_short_live_pulse_ttl_survive_replay(self):
        payload = {"key": "body.heart_rate", "value": 90, "unit": "bpm", "quality": "ok"}
        sample = event(1, 0, payload=payload)
        sample = replace(sample, observed_at="2026-10-31T11:59:55Z")
        replay = self.replay((sample,), BINDING_BY_ID["pulse"].config)
        frame = replay.seek(1)
        self.assertEqual((frame.mode, frame.raw, frame.quality, frame.age_seconds), ("replay", 90, "stale", 6))
        expired = replay.seek(6)
        self.assertIsNone(expired.raw)
        self.assertNotIn("pulse_bpm", expired.parameters)

    def test_presentations_change_config_at_the_recorded_anchor(self):
        initial = self.config()
        changed = replace(initial, output_max=2)
        presentation = event(3, 12, topic="presentation.changed", payload={
            "parameters": {"binding_config": changed.to_dict()}})
        events = (event(1, 0, 0), event(2, 10, 100), presentation)
        replay = self.replay(events, initial)
        self.assertAlmostEqual(replay.seek(11).smoothed, 1 - math.exp(-.5))
        at_change = replay.seek(12)
        self.assertEqual((at_change.config, at_change.smoothed), (changed, 2))
        self.assertEqual(replay.seek(14).smoothed, 2)
        # An explicit analysis override deliberately ignores historical config edits.
        override = replay.seek(14, config=initial)
        self.assertAlmostEqual(override.smoothed, 1 - math.exp(-2))

    def test_source_session_change_snaps_instead_of_blending_unrelated_measurements(self):
        events = (event(1, 0, 0), event(2, 10, 100, session="s2"))
        frame = self.replay(events, self.config()).seek(10)
        self.assertEqual((frame.smoothed, frame.source_session_id), (1, "s2"))

    def test_private_body_is_only_available_if_recorded_and_stream_view_never_leaks_it(self):
        online_only = self.replay((event(1, 0, 28),), BINDING_BY_ID["pulse"].config)
        self.assertIsNone(online_only.seek(1).raw)
        self.assertNotIn("pulse_bpm", online_only.seek(1).parameters)
        sample = event(1, 0, payload={"key": "body.heart_rate", "value": 140, "unit": "bpm", "quality": "ok"})
        body = self.replay((sample,), BINDING_BY_ID["pulse"].config)
        self.assertEqual(body.seek(1).raw, 140)
        hidden = body.seek(1, stream_view=True)
        self.assertIsNone(hidden.raw)
        self.assertEqual(dict(hidden.parameters), {})
        self.assertEqual(body.seek(1, stream_view=False).raw, 140)

    def test_queue_projection_reconstructs_handoffs_and_empty_is_unknown_before_any_state(self):
        ready = event(1, 1, topic="agent.handoff.ready", payload={
            "message_id": "handoff-1", "from": "codex", "to": "claude", "status": "ready"})
        replied = event(2, 4, topic="agent.handoff.updated", payload={
            "message_id": "handoff-1", "status": "replied"})
        replay = self.replay((ready, replied), BINDING_BY_ID["queue"].config)
        self.assertIsNone(replay.seek(0).raw)
        frame = replay.seek(2)
        self.assertEqual((frame.mode, frame.raw, frame.parameters["queue_right"]), ("replay", 1, 1))
        self.assertEqual(replay.seek(5).raw, 0)
        external = SimpleNamespace(mode="watch", status="WATCH", cards=(), handoffs=(
            Handoff("external", 1, "claude", "codex", "public", "reply", "ready"),))
        frame = replay.seek(5, arena=external)
        self.assertEqual((frame.mode, frame.raw, frame.parameters["queue_left"]), ("replay", 1, 1))

    def test_latest_run_is_retained_after_more_than_display_journal_events(self):
        completed = event(1, 0, topic="task.test.completed", payload={"tests_passed": 3, "tests_total": 4})
        events = (completed,) + tuple(event(index, index / 100, index % 100) for index in range(2, 2105))
        replay = self.replay(events, BINDING_BY_ID["tests"].config)
        frame = replay.seek(22)
        self.assertEqual((frame.raw, frame.parameters["test_passed"], frame.parameters["test_total"]), (.75, 3, 4))
        self.assertLessEqual(replay.cache_info["checkpoints"], 8)

    def test_checkpoint_restore_matches_a_fresh_seek(self):
        events = tuple(event(index, index / 10, index % 100) for index in range(1, 2400))
        replay = self.replay(events, self.config())
        replay.seek(239)
        rewind = replay.seek(175)
        self.assertEqual(rewind, self.replay(events, self.config()).seek(175))
        advanced = replay.seek(180)
        self.assertEqual(advanced, self.replay(events, self.config()).seek(180))
        self.assertLessEqual(replay.cache_info["checkpoints"], 8)

    def test_no_external_jobs_and_bounded_input_validation(self):
        with patch("subprocess.Popen", side_effect=AssertionError("Replay must not launch")), \
             patch("urllib.request.urlopen", side_effect=AssertionError("Replay must not connect")):
            replay = self.replay((event(1, 0, 0),), self.config())
            replay.seek(10)
        with self.assertRaises(ValueError):
            ReplayBindings((event(1, 0, 0),), ())
        with self.assertRaises(ValueError):
            ReplayBindings((event(1, 1, 0), event(2, 0, 0)), (1, 0))
        for value in (math.inf, math.nan, -1, True):
            with self.assertRaises(ValueError):
                replay.seek(value)


if __name__ == "__main__":
    unittest.main()
