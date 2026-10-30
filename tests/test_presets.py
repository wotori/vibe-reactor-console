"""Safe preference import, atomic persistence and discoverable commands."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from devos import commands, presets
from devos.screen_registry import SCREENS
from devos.visuals import ANIMATIONS


class PresetTests(unittest.TestCase):
    def test_every_builtin_preset_resolves_to_valid_preferences(self):
        for name, original in presets.PRESETS.items():
            with self.subTest(name=name):
                resolved = presets.resolve(name)
                self.assertEqual(resolved, original)
                resolved["visual"] = "cube"
                self.assertEqual(presets.PRESETS[name], original)

    def test_importing_untrusted_preset_only_keeps_preference_keys(self):
        document = {"schema_version": 1, "screen": "arena", "visual": {"id": "twin-core", "seed": 17},
                    "command": "touch /tmp/never-run", "cwd": "/private/project", "token": "secret",
                    "providers": [{"type": "http-json", "url": "https://private.invalid"}],
                    "agents": [{"command": "claude"}], "arena_mode": "watch"}
        original = json.loads(json.dumps(document))
        with patch("subprocess.Popen", side_effect=AssertionError("Import must not launch a process")), \
             patch("urllib.request.urlopen", side_effect=AssertionError("Import must not connect")):
            result = presets.validate(document)
        self.assertEqual(result, {"screen": "arena", "visual": "twin-core", "seed": 17, "arena_mode": "watch"})
        self.assertEqual(document, original)

    def test_unknown_ids_types_and_nonfinite_values_fail_early(self):
        for key, value in (("screen", "not-a-screen"), ("scene", "unknown"), ("visual", "unknown"),
                           ("theme", "blue"), ("arena_mode", "mission"), ("fps_cap", 121),
                           ("fps_cap", 60.0), ("speed", None), ("auto_cycle", None),
                           ("speed", float("nan")), ("auto_cycle", float("inf")),
                           ("scope", "true"), ("seed", True), ("intensity", "loud")):
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    presets.validate({"schema_version": 1, key: value})
        for version in (0, 2, "1", True, 1.0):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    presets.validate({"schema_version": version})

    def test_load_limits_size_and_reads_documented_nested_visual_format(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.json"
            path.write_text(json.dumps({"schema_version": 1, "visual": {"id": "gravity-well", "seed": 41}}))
            self.assertEqual(presets.load(path), {"visual": "gravity-well", "seed": 41})
            path.write_text(" " * 65537)
            with self.assertRaisesRegex(ValueError, "64 KiB"):
                presets.load(path)

    def test_atomic_save_sanitizes_and_preserves_previous_file_on_replace_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "settings.json"
            presets.save(path, {"schema_version": 1, "visual": "cube", "command": "never", "token": "secret"})
            original = path.read_bytes()
            saved = json.loads(original)
            self.assertEqual(saved, {"schema_version": 1, "visual": "cube"})
            self.assertEqual(presets.load(path), {"visual": "cube"})
            with patch("devos.presets.os.replace", side_effect=OSError("simulated replacement failure")):
                with self.assertRaisesRegex(OSError, "replacement failure"):
                    presets.save(path, {"schema_version": 1, "visual": "sphere"})
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(tuple(path.parent.glob(".devos-*")), ())


class CommandTests(unittest.TestCase):
    def test_catalog_includes_screens_visuals_and_safety_controls_without_actions(self):
        with patch("subprocess.Popen", side_effect=AssertionError("Catalog must be passive")):
            catalog = commands.catalog()
        ids = tuple(item.id for item in catalog)
        self.assertEqual(len(ids), len(set(ids)))
        for screen in SCREENS:
            self.assertIn("screen:" + screen.id, ids)
        for visual in ANIMATIONS:
            self.assertIn("visual:" + visual.id, ids)
        for action in ("mission-stop", "mission-pause", "stream", "record", "help"):
            self.assertIn("action:" + action, ids)

    def test_search_is_case_insensitive_supports_multiple_terms_and_no_match(self):
        self.assertEqual(commands.search("missing-unlikely-query"), ())
        self.assertTrue(all("gravity" in (item.title + item.description + item.id).lower()
                            for item in commands.search("GRAVITY")))
        self.assertIn("screen:visual-lab", {item.id for item in commands.search("screen lab")})
        self.assertEqual(len(commands.search("")), len(commands.catalog()))


if __name__ == "__main__":
    unittest.main()
