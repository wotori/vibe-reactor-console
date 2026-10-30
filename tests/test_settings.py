"""Local startup state and debounce remain separate from portable scene imports."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from devos import presets, settings
from devos.cli import parser, runtime_options
from devos.session import Session
from devos.sources import SourceProvider


class LocalSettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.path = self.root / "config" / "settings.json"

    def session(self):
        result = Session(animation="reactor", theme="ember", background="black", fps=90,
                         speed=2, auto_cycle=12, scope=False, sources=SourceProvider(bundled=True))
        result.sources.project_root = str(self.root)
        return result

    def test_selected_visual_controls_and_project_are_saved_locally(self):
        session = self.session()
        snapshot = settings.local_preferences(session)
        settings.save(self.path, snapshot)
        loaded = settings.load(self.path)
        for key in ("visual", "theme", "background", "fps_cap", "speed", "auto_cycle", "scope", "project_root"):
            self.assertEqual(loaded[key], snapshot[key])
        document = json.loads(self.path.read_text())
        self.assertEqual(document["format"], "devos-local-settings")
        self.assertEqual(loaded["visual"], "reactor")
        self.assertNotIn("project_root", presets.preferences(session))
        self.assertNotIn("project_root", presets.validate(document))

    def test_local_save_strips_private_actions(self):
        settings.save(self.path, {"visual": "reactor", "project_root": str(self.root), "command": "run",
                                  "source_url": "https://private.invalid", "heart_file": "/private/pulse",
                                  "token": "private", "mission": "private task"})
        self.assertEqual(settings.load(self.path), {"visual": "reactor", "project_root": str(self.root)})

    def test_relative_local_root_is_resolved_against_config_file(self):
        self.path.parent.mkdir()
        self.path.write_text(json.dumps({"project_root": "../project", "visual": "sphere"}))
        self.assertEqual(settings.load(self.path)["project_root"], str(self.root / "project"))

    def test_imported_scene_cannot_declare_local_project_access(self):
        capsule = {"format": "devos-scene-capsule", "schema_version": 1, "project_root": "/private/project",
                   "presentation": {"parameters": {"visual": "reactor", "project_root": "/private/other"}}}
        self.assertEqual(settings.validate(capsule), {"visual": "reactor", "seed": 42})
        for value in (True, 12, "", "   ", "bad\x00path", "bad\npath", "x" * 4097):
            with self.subTest(value=repr(value)[:40]), self.assertRaises(ValueError):
                settings.validate({"project_root": value})
        self.assertEqual(settings.validate({"project_root": None}), {"project_root": None})

    def test_atomic_failure_keeps_previous_local_root_and_cleans_temporary(self):
        settings.save(self.path, {"visual": "reactor", "project_root": str(self.root)})
        original = self.path.read_bytes()
        with patch("devos.presets.os.replace", side_effect=OSError("disk unavailable")), self.assertRaises(OSError):
            settings.save(self.path, {"visual": "sphere", "project_root": None})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(tuple(self.path.parent.glob(".devos-*")), ())

    def test_load_is_bounded(self):
        self.path.parent.mkdir()
        self.path.write_text(" " * 65537)
        with self.assertRaisesRegex(ValueError, "64 KiB"):
            settings.load(self.path)

    def test_changes_debounce_deduplicate_and_flush_on_close(self):
        session = self.session()
        now = [0.]
        store = settings.SettingsStore(self.path, clock=lambda: now[0])
        with patch("devos.settings.save", wraps=settings.save) as save:
            self.assertIsNone(store.update(session))
            self.assertFalse(self.path.exists())
            now[0] = .8
            self.assertEqual(store.update(session), self.path)
            self.assertEqual(save.call_count, 1)
            now[0] = 1.
            self.assertIsNone(store.update(session))
            session.choose_animation("sphere")
            self.assertIsNone(store.update(session))
            now[0] = 1.2
            session.choose_animation("cube")
            self.assertIsNone(store.update(session))
            now[0] = 1.9
            self.assertIsNone(store.update(session))
            self.assertEqual(store.close(session), self.path)
            self.assertEqual(save.call_count, 2)
            self.assertIsNone(store.close(session))
        self.assertEqual(settings.load(self.path)["visual"], "cube")

    def test_failed_autosave_backs_off_but_shutdown_retries_last_values(self):
        session = self.session()
        now = [0.]
        store = settings.SettingsStore(self.path, debounce=0, clock=lambda: now[0])
        with patch("devos.settings.save", side_effect=OSError("disk full")) as save:
            with self.assertRaisesRegex(OSError, "disk full"):
                store.update(session)
            now[0] = 1.
            self.assertIsNone(store.update(session))
            session.choose_animation("sphere")
            self.assertIsNone(store.update(session))
            self.assertEqual(save.call_count, 1)
        store.close(session)
        self.assertEqual(settings.load(self.path)["visual"], "sphere")

    def test_disabled_store_never_writes_even_on_explicit_flush(self):
        store = settings.SettingsStore(self.path, enabled=False)
        with patch("devos.settings.save") as save:
            self.assertIsNone(store.update(self.session(), force=True))
            self.assertIsNone(store.close(self.session()))
        save.assert_not_called()


class SettingsCliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.path = self.root / "settings.json"
        settings.save(self.path, {"visual": "reactor", "project_root": str(self.project), "background": "black"})

    def test_trusted_local_root_is_separate_from_session_preferences(self):
        args = parser().parse_args(["--config", str(self.path)])
        preferences = runtime_options(args)
        self.assertEqual(args.project_root, str(self.project))
        self.assertEqual(preferences["animation"], "reactor")
        self.assertEqual(preferences["background"], "black")
        self.assertNotIn("project_root", preferences)

    def test_explicit_root_wins_but_preset_cannot_replace_local_access(self):
        preset = self.root / "scene.json"
        preset.write_text(json.dumps({"visual": "sphere", "project_root": "/private/untrusted"}))
        args = parser().parse_args(["--config", str(self.path), "--preset", str(preset)])
        self.assertEqual(runtime_options(args)["animation"], "sphere")
        self.assertEqual(args.project_root, str(self.project))
        args = parser().parse_args(["--config", str(self.path), "--project-root", str(self.root), "--preset", str(preset)])
        runtime_options(args)
        self.assertEqual(args.project_root, str(self.root))
        args = parser().parse_args(["--no-config", "--preset", str(preset)])
        runtime_options(args)
        self.assertIsNone(args.project_root)

    def test_bundled_override_keeps_preferred_root_without_expanding_access(self):
        args = parser().parse_args(["--config", str(self.path), "--bundled"])
        runtime_options(args)
        self.assertTrue(args.bundled)
        self.assertEqual(args.project_root, str(self.project))

    def test_bad_default_settings_warn_and_explicit_bad_config_fails(self):
        self.path.write_text("not JSON")
        with patch("devos.presets.config_path", return_value=self.path):
            args = parser().parse_args([])
            self.assertEqual(runtime_options(args)["animation"], None)
            self.assertIn("Saved settings ignored", args.config_warning)
        with self.assertRaises(ValueError):
            runtime_options(parser().parse_args(["--config", str(self.path)]))
        with patch("devos.settings.load", side_effect=AssertionError("No-config must skip read")):
            args = parser().parse_args(["--no-config"])
            runtime_options(args)
            self.assertFalse(args.config_warning)

    def test_replay_does_not_scan_remembered_project_and_rejects_explicit_root(self):
        args = parser().parse_args(["--config", str(self.path), "--replay", "flight.jsonl"])
        runtime_options(args)
        self.assertIsNone(args.project_root)
        with self.assertRaisesRegex(ValueError, "replay"):
            runtime_options(parser().parse_args(["--no-config", "--replay", "flight.jsonl", "--project-root", str(self.project)]))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser().parse_args(["--project-root", str(self.root / "absent")])


if __name__ == "__main__":
    unittest.main()
