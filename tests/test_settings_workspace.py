"""Persist real Runtime preferences without starting terminals or model jobs."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from devos import settings
from devos.cli import parser, runtime_options
from devos.recordings import Recorder
from devos.runtime import Runtime
from devos.session import Session
from devos.sources import SourceProvider
from tests.test_runtime import FakeArena, FakeHub, FakeTasks, FakeTerminals


class SettingsWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.config = self.root / "preferences.json"
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "main.py").write_text("def selected_project():\n    return 'local source'\n")
        services = patch.multiple("devos.runtime", TelemetryHub=FakeHub, TerminalManager=FakeTerminals,
                                  AgentCoordinator=FakeArena, TaskRunner=FakeTasks)
        services.start()
        self.addCleanup(services.stop)
        default_path = patch("devos.presets.config_path", return_value=self.config)
        default_path.start()
        self.addCleanup(default_path.stop)

    def runtime(self, *arguments):
        args = parser().parse_args(list(arguments))
        options = runtime_options(args)
        provider = SourceProvider(bundled=args.bundled, project_root=args.project_root)
        self.addCleanup(provider.close)
        session = Session(sources=provider, **options)
        runtime = Runtime(args, session)
        self.addCleanup(runtime.close)
        return runtime, session

    def test_immediate_exit_flushes_last_visual_and_controls_then_next_start_restores_them(self):
        runtime, session = self.runtime("--bundled")
        session.choose_animation("reactor")
        session.choose_screen("visual-lab")
        session.options.palette = "ember"
        session.options.background = "black"
        session.options.fps = 90
        session.options.auto_cycle = 12
        session.options.scope = False
        session.options.consumer = "space"
        session.speed = 1.5
        session.intensity = "showcase"
        session.director_enabled = True
        # There has been no update/debounce interval: shutdown must flush the edit.
        runtime.close()
        restored, actual = self.runtime("--config", str(self.config), "--bundled")
        self.assertEqual(actual.animation, "reactor")
        self.assertEqual(actual.screen_id, "visual-lab")
        self.assertEqual(actual.options.palette, "ember")
        self.assertEqual(actual.background, "black")
        self.assertEqual(actual.options.fps, 90)
        self.assertEqual(actual.options.auto_cycle, 12)
        self.assertFalse(actual.options.scope)
        self.assertEqual(actual.options.consumer, "space")
        self.assertEqual((actual.speed, actual.intensity, actual.director_enabled), (1.5, "showcase", True))
        restored.close()

    def test_no_config_disables_manual_and_automatic_writes_and_stops_source_worker(self):
        runtime, session = self.runtime("--no-config", "--project-root", str(self.project))
        worker = session.sources._project
        self.assertIsNotNone(worker)
        session.choose_animation("reactor")
        with patch("devos.settings.save", side_effect=AssertionError("No-config autosave")), \
                patch("devos.runtime.save_settings", side_effect=AssertionError("No-config manual save")):
            runtime.action("save")
            self.assertIn("disabled", session.status_message)
            runtime.update(0)
            runtime.close()
        self.assertFalse(self.config.exists())
        self.assertTrue(worker._closed)
        self.assertFalse(worker._thread.is_alive())

    def test_replay_presentation_does_not_overwrite_local_preferences_even_after_manual_save(self):
        settings.save(self.config, {"visual": "reactor", "background": "black", "project_root": str(self.project)})
        original = self.config.read_bytes()
        recording = self.root / "flight.jsonl"
        recorder = Recorder(recording, parameters={"visual": "cube", "theme": "ice"})
        recorder.close()
        runtime, session = self.runtime("--config", str(self.config), "--replay", str(recording))
        self.assertEqual(session.animation, "cube")
        self.assertIsNone(session.sources.project_root)
        runtime.update(0)
        runtime.action("save")
        self.assertIn("disabled", session.status_message)
        session.choose_animation("sphere")
        runtime.close()
        self.assertEqual(self.config.read_bytes(), original)

    def test_bundled_launch_preserves_remembered_project_without_starting_scan(self):
        settings.save(self.config, {"visual": "reactor", "project_root": str(self.project)})
        with patch("devos.sources.ProjectSourceWorker", side_effect=AssertionError("Bundled source scan")):
            runtime, session = self.runtime("--config", str(self.config), "--bundled")
            self.assertFalse(session.sources.project_active)
            self.assertEqual(session.sources.project_root, str(self.project))
            session.choose_animation("reactor")
            runtime.update(0)
            runtime.close()
        saved = settings.load(self.config)
        self.assertEqual(saved["project_root"], str(self.project))
        self.assertEqual(saved["visual"], "reactor")


if __name__ == "__main__":
    unittest.main()
