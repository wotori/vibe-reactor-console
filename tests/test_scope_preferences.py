"""Math panel preferences survive restarts and remain passive portable data."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from devos import commands, presets, settings
from devos.cli import parser, runtime_options
from devos.math_scope import SCOPES
from devos.playback import export_scene_capsule, presentation_document, safe_parameters
from devos.session import Session
from devos.sources import SourceProvider


class ScopePreferencesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "settings.json"

    def session(self, **preferences):
        return Session(sources=SourceProvider(bundled=True), **preferences)

    def test_auto_saved_math_selection_restores_from_actual_cli_startup(self):
        session = self.session(scope_mode="parabola", scope_auto_cycle=12)
        store = settings.SettingsStore(self.path)
        store.close(session)
        document = json.loads(self.path.read_text())
        self.assertEqual(document["scope_mode"], "parabola")
        self.assertEqual(document["scope_auto_cycle"], 12)

        args = parser().parse_args(["--config", str(self.path)])
        restarted = self.session(**runtime_options(args))
        self.assertEqual(restarted.options.scope_mode, "parabola")
        self.assertEqual(restarted.options.scope_auto_cycle, 12)
        restarted.options.scope_mode = "epicycles"
        restarted.options.scope_auto_cycle = 0
        settings.SettingsStore(self.path).close(restarted)
        final = settings.load(self.path)
        self.assertEqual(final["scope_mode"], "epicycles")
        self.assertEqual(final["scope_auto_cycle"], 0)

    def test_legacy_settings_keep_default_function_and_rotation_off(self):
        settings.save(self.path, {"visual": "reactor", "scope": False})
        args = parser().parse_args(["--config", str(self.path)])
        restarted = self.session(**runtime_options(args))
        self.assertEqual(restarted.options.scope_mode, "harmonics")
        self.assertEqual(restarted.options.scope_auto_cycle, 0)
        self.assertFalse(restarted.options.scope)
        self.assertEqual(restarted.animation, "reactor")

    def test_scope_settings_are_portable_through_a_scene_capsule(self):
        parameters = presets.preferences(self.session(scope_mode="wave-packet", scope_auto_cycle=7.5))
        path = self.root / "scene.capsule.json"
        with patch("subprocess.Popen", side_effect=AssertionError("Import must be passive")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("Import must stay offline")):
            export_scene_capsule(path, presentation_document(parameters, 14, 28))
            imported = presets.load(path)
            self.assertEqual(imported["scope_mode"], "wave-packet")
            self.assertEqual(imported["scope_auto_cycle"], 7.5)
            args = parser().parse_args(["--no-config", "--preset", str(path)])
            restarted = self.session(**runtime_options(args))
        self.assertEqual(restarted.options.scope_mode, "wave-packet")
        self.assertEqual(restarted.options.scope_auto_cycle, 7.5)

    def test_preset_validation_rejects_unknown_functions_and_unbounded_intervals(self):
        for value in ("unknown", "sin(x)", "__import__('os')", "HARMONICS", "", None, [], True):
            with self.subTest(mode=value), self.assertRaises(ValueError):
                presets.validate({"scope_mode": value})
        for value in (None, True, "8", -1, 5e-324, .1, 86400.01, float("nan"), float("inf"), -float("inf")):
            with self.subTest(interval=value), self.assertRaises(ValueError):
                presets.validate({"scope_auto_cycle": value})
        for interval in (0, .25, 86400):
            self.assertEqual(presets.validate({"scope_auto_cycle": interval}), {"scope_auto_cycle": interval})

    def test_imports_keep_catalog_ids_without_executable_function_definitions(self):
        for scope in SCOPES:
            with self.subTest(scope=scope.id):
                parameters = {"scope_mode": scope.id, "scope_auto_cycle": 9,
                              "scope_function": "__import__('os').system('never')", "command": "never"}
                expected = {"scope_mode": scope.id, "scope_auto_cycle": 9}
                self.assertEqual(presets.validate(parameters), expected)
                self.assertEqual(safe_parameters(parameters), expected)


class ScopeCliAndCatalogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "settings.json"
        settings.save(self.path, {"scope_mode": "parabola", "scope_auto_cycle": 10})
        self.preset = self.root / "preset.json"
        presets.save(self.preset, {"scope_mode": "rose", "scope_auto_cycle": 8})

    def resolve(self, *arguments):
        return runtime_options(parser().parse_args(list(arguments)))

    def test_scope_precedence_is_defaults_then_saved_then_preset_then_explicit(self):
        self.assertEqual(self.resolve("--no-config")["scope_mode"], "harmonics")
        self.assertEqual(self.resolve("--no-config")["scope_auto_cycle"], 0)
        saved = self.resolve("--config", str(self.path))
        self.assertEqual((saved["scope_mode"], saved["scope_auto_cycle"]), ("parabola", 10))
        preset = self.resolve("--config", str(self.path), "--preset", str(self.preset))
        self.assertEqual((preset["scope_mode"], preset["scope_auto_cycle"]), ("rose", 8))
        explicit = self.resolve("--config", str(self.path), "--preset", str(self.preset),
                                "--scope-mode", "fourier", "--scope-auto-cycle", "3.5")
        self.assertEqual((explicit["scope_mode"], explicit["scope_auto_cycle"]), ("fourier", 3.5))

    def test_explicit_rotation_off_overrides_saved_or_imported_interval(self):
        for extra in ([], ["--preset", str(self.preset)]):
            with self.subTest(extra=extra):
                values = self.resolve("--config", str(self.path), *extra, "--no-scope-auto-cycle")
                self.assertEqual(values["scope_auto_cycle"], 0)
                self.assertTrue(values["scope"])
        values = self.resolve("--config", str(self.path), "--no-scope")
        self.assertFalse(values["scope"])
        self.assertEqual(values["scope_mode"], "parabola")
        self.assertEqual(values["scope_auto_cycle"], 10)

    def test_invalid_cli_values_fail_before_the_terminal_and_flags_are_exclusive(self):
        for arguments in (("--scope-mode", "expression(x)"), ("--scope-auto-cycle", "nan"),
                          ("--scope-auto-cycle", "inf"), ("--scope-auto-cycle", "0"),
                          ("--scope-auto-cycle", "-1"),
                          ("--scope-auto-cycle", "8", "--no-scope-auto-cycle")):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser().parse_args(list(arguments))
        with self.assertRaisesRegex(ValueError, "86400"):
            self.resolve("--no-config", "--scope-auto-cycle", "86401")
        for interval in ("5e-324", ".1"):
            with self.subTest(interval=interval), self.assertRaisesRegex(ValueError, "0.25"):
                self.resolve("--no-config", "--scope-auto-cycle", interval)

    def test_command_search_discovers_all_functions_and_independent_controls(self):
        with patch("subprocess.Popen", side_effect=AssertionError("Catalog must be passive")):
            catalog = commands.catalog()
            math = commands.search("Math")
        choices = tuple(item for item in catalog if item.kind == "scope-mode")
        self.assertEqual({item.value for item in choices}, {scope.id for scope in SCOPES})
        self.assertTrue(all(item in math for item in choices))
        self.assertIn("parabola", {item.value for item in commands.search("math parabola")})
        actions = {item.value for item in catalog if item.kind == "action"}
        self.assertTrue({"scope-next", "scope-auto", "scope-zoom"}.issubset(actions))
        ids = [item.id for item in catalog]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
