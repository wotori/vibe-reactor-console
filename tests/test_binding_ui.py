"""Session/UI preference integration for the configurable visual patchbay."""

from dataclasses import replace
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from devos.bindings import BINDING_BY_ID, BindingConfig
from devos.presets import load, preferences, save, validate
from devos.renderer import Renderer
from devos.session import Session
from devos.sources import SourceProvider
from devos.telemetry import DataEvent, MetricSample, TelemetrySnapshot
from devos.terminal import Canvas
from tests.test_rendering import Screen


def measured(value, key="site.active_sessions_60s", unit="sessions", quality="ok", source="fixture"):
    return TelemetrySnapshot(metrics=(MetricSample(key, value, unit, source, "run-1", 1,
        None, 0, "live", quality=quality, age_seconds=2, window_seconds=60),))


class BindingUIIntegrationTests(unittest.TestCase):
    def session(self, **options):
        return Session(seed=41, sources=SourceProvider(bundled=True), screen="visual-lab", **options)

    def render(self, session):
        screen = Screen(44, 160)
        Renderer(Canvas(screen, color=False)).draw(session)
        return screen.text()

    def test_session_updates_ema_once_per_publish_and_render_is_read_only(self):
        config = BindingConfig("site.active_sessions_60s", "energy", input_max=100, smoothing_seconds=2)
        session = self.session(binding_config=config.to_dict())
        session.publish_telemetry(measured(0), dt=0)
        session.publish_telemetry(measured(100, quality="stale"), dt=2)
        self.assertAlmostEqual(session.visual_parameters()["energy"], 1 - math.exp(-1))
        self.assertEqual((session.binding_frame.raw, session.binding_frame.quality), (100, "stale"))
        before = session.binding_frame
        parameters = dict(session.visual_parameters())
        self.render(session)
        self.render(session)
        self.assertIs(session.binding_frame, before)
        self.assertEqual(dict(session.visual_parameters()), parameters)

    def test_preference_roundtrip_keeps_custom_binding_ranges_units_and_consumer(self):
        config = BindingConfig("git.changed_files", "energy", "log", 0, 100, 0, .8,
                               smoothing_seconds=1.5, missing="fade", expected_unit="files")
        session = self.session(binding_config=config.to_dict(), consumer="mirror", director_enabled=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            with patch("subprocess.Popen", side_effect=AssertionError("Preset must not start a process")):
                save(path, preferences(session))
                restored = self.session()
                restored.apply_preferences(load(path))
        self.assertEqual(restored.current_binding_config, config)
        self.assertEqual(restored.options.consumer, "mirror")
        self.assertTrue(restored.director_enabled)
        self.assertEqual(tuple(restored.pending_actions), ())

    def test_editor_cycles_valid_fields_resets_and_never_queues_execution(self):
        session = self.session()
        session.terminal_focused = True
        session.handle("binding-settings")
        self.assertTrue(session.binding_editor)
        self.assertFalse(session.terminal_focused)
        session.binding_row = 2
        session.binding_key("accept")
        self.assertEqual(session.current_binding_config.normalization, "linear")
        session.binding_row = 7
        session.binding_key("right")
        self.assertEqual(session.current_binding_config.smoothing_seconds, 1.5)
        session.binding_key("reload")
        self.assertEqual(session.current_binding_config, BINDING_BY_ID["online"].config)
        session.binding_key("escape")
        self.assertFalse(session.binding_editor)
        self.assertEqual(tuple(session.pending_actions), ())

    def test_invalid_imported_binding_fails_before_applying_preferences(self):
        for replacement in ({"smoothing_seconds": math.nan}, {"input_min": 10, "input_max": 1},
                            {"parameter": "shell_command"}, {"command": "never execute"}):
            config = BINDING_BY_ID["online"].config.to_dict()
            config.update(replacement)
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                validate({"schema_version": 1, "binding_config": config})

    def test_lab_inspector_uses_actual_configured_source_and_quality(self):
        config = BindingConfig("git.changed_files", "energy", input_max=50, expected_unit="files")
        session = self.session(binding_config=config.to_dict())
        session.publish_telemetry(measured(7, "git.changed_files", "files", "stale", "git-fixture"))
        text = self.render(session)
        self.assertIn("git.changed_files", text)
        self.assertIn("git-fixture", text)
        self.assertIn("files", text)
        self.assertIn("STALE", text)
        self.assertEqual(session.binding_frame.raw, 7)

    def test_stream_view_hides_custom_body_source_and_derived_graphics(self):
        config = BindingConfig("body.heart_rate", "energy", input_min=30, input_max=240, expected_unit="bpm")
        session = self.session(binding_config=config.to_dict())
        session.publish_telemetry(measured(146, "body.heart_rate", "bpm", source="private-device"))
        session.handle("stream")
        first = self.render(session)
        session.publish_telemetry(measured(200, "body.heart_rate", "bpm", source="private-device"))
        second = self.render(session)
        self.assertNotIn("146 bpm", first)
        self.assertNotIn("200 bpm", second)
        self.assertNotIn("private-device", second)
        self.assertEqual(first, second)

    def test_spectator_never_takes_terminal_focus_or_immediate_manual_visual(self):
        session = self.session(director_enabled=True, animation="avatar")
        session.choose_animation("cube")
        cue = DataEvent("cue-1", "visual.cue", "fixture", "run-1", 1, "replay", None,
                        "2026-10-31T12:00:00Z", {"cue": "traffic-surge"},
                        origin_mode="demo", recorded_offset_ms=1000)
        session.visual_time = 1
        session.publish_telemetry(TelemetrySnapshot(events=(cue,)))
        self.assertEqual(session.animation, "cube")
        session.terminal_focused = True
        session.visual_time = 10
        later = replace(cue, id="cue-2", sequence=2, recorded_offset_ms=10000)
        session.publish_telemetry(TelemetrySnapshot(events=(cue, later)))
        self.assertTrue(session.terminal_focused)
        self.assertEqual(session.animation, "cube")


if __name__ == "__main__":
    unittest.main()
