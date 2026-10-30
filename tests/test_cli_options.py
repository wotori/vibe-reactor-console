import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from devos.cli import main, parser, runtime_options


class EvolutionCliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()

    def resolve(self, *arguments):
        args = parser().parse_args(["--no-config", *arguments])
        return args, runtime_options(args)

    def write(self, name, data):
        path = self.root / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    def test_old_defaults_and_new_defaults_are_inactive(self):
        args, settings = self.resolve()
        self.assertEqual(args.mode, "ekza")
        self.assertEqual(args.fps, 60)
        self.assertEqual(args.agents, "demo")
        self.assertEqual(args.screen, "cockpit")
        self.assertEqual(args.data_mode, "auto")
        self.assertIsNone(args.task)
        self.assertIsNone(args.mission)
        self.assertIsNone(args.source_url)
        self.assertIsNone(args.record)
        self.assertTrue(settings["scope"])

    def test_config_preset_cli_precedence_is_independent_for_each_preference(self):
        config = self.write("config.json", {"schema_version": 1, "scene": "bare-metal", "screen": "body",
                                             "visual": "sphere", "theme": "ember", "fps_cap": 30,
                                             "scope": False, "speed": .5, "data_mode": "live"})
        args = parser().parse_args(["--config", config, "--preset", "showcase", "--fps=120", "--theme", "amber"])
        settings = runtime_options(args)
        self.assertEqual(settings["mode"], "bare-metal")
        self.assertEqual(settings["screen"], "visual-lab")
        self.assertEqual(settings["animation"], "torus-knot")
        self.assertEqual(settings["theme"], "amber")
        self.assertEqual(settings["fps"], 120)
        self.assertFalse(settings["scope"])
        self.assertEqual(settings["speed"], .5)
        self.assertEqual(settings["data_mode"], "live")
        self.assertEqual(args.config_path, config)

    def test_explicit_old_default_values_override_different_saved_values(self):
        config = self.write("config.json", {"fps_cap": 30, "speed": .5, "background": "black", "scene": "lab"})
        args = parser().parse_args(["--config", config, "--fps", "60", "--speed", "1", "--background", "terminal", "--mode", "ekza"])
        runtime_options(args)
        self.assertEqual((args.fps, args.speed, args.background, args.mode), (60, 1., "terminal", "ekza"))

    def test_no_config_skips_even_invalid_default_preferences_and_commands_are_not_loaded(self):
        config = self.root / "broken.json"
        config.write_text("not-json")
        with patch("devos.presets.config_path", return_value=config):
            args, _ = self.resolve()
        self.assertEqual(args.mode, "ekza")
        malicious = self.write("malicious.json", {"screen": "arena", "arena_mode": "watch", "mission": "run me",
                                                   "terminal_command": ["sh", "-c", "touch bad"],
                                                   "task": "bad", "source_url": "http://evil.example"})
        args, settings = self.resolve("--preset", malicious)
        self.assertEqual(args.agents, "watch")
        self.assertIsNone(args.terminal_command)
        self.assertIsNone(args.task)
        self.assertIsNone(args.mission)
        self.assertIsNone(args.source_url)
        self.assertNotIn("task", settings)

    def test_command_arguments_are_literal_and_sources_are_exclusive(self):
        args, _ = self.resolve("--task", "python3 -m unittest 'a b' '$(literal)'", "--right-command", "claude --model 'a b'")
        self.assertEqual(args.task, ("python3", "-m", "unittest", "a b", "$(literal)"))
        self.assertEqual(args.right_command, ("claude", "--model", "a b"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            parser().parse_args(["--source-file", "one.json", "--source-stream", "two.jsonl"])
        self.assertEqual(error.exception.code, 2)

    def test_paths_expand_without_opening_files_or_starting_any_process(self):
        with patch("devos.agents.subprocess.Popen") as popen, patch("devos.agents.subprocess.run") as run:
            args, _ = self.resolve("--source-stream", str(self.root / "future.jsonl"),
                                   "--heart-file", str(self.root / "future-heart.json"),
                                   "--record", str(self.root / "new-recording.jsonl"))
            self.assertEqual(args.source_stream, str(self.root / "future.jsonl"))
            self.assertFalse(Path(args.record).exists())
            popen.assert_not_called()
            run.assert_not_called()

    def test_managed_prompt_and_separate_workspaces_are_explicit(self):
        left, right = self.root / "left", self.root / "right"
        left.mkdir()
        right.mkdir()
        with patch("devos.agents.subprocess.Popen") as popen, patch("devos.agents.subprocess.run") as run:
            args, _ = self.resolve("--mission", "Review this bounded task", "--workspace-left", str(left), "--workspace-right", str(right))
            self.assertEqual(args.agents, "mission")
            self.assertEqual(args.scenario, "design-rally")
            self.assertEqual(args.handoffs, 6)
            popen.assert_not_called()
            run.assert_not_called()
        for arguments in (("--agents", "mission"),
                          ("--agents", "relay", "--mission", "task"),
                          ("--agents", "demo", "--mission", "task"),
                          ("--mission", "task", "--workspace-left", str(left), "--workspace-right", str(left))):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.resolve(*arguments)

    def test_prepare_only_validates_destination_and_never_creates_it(self):
        source = self.root / "source"
        source.mkdir()
        destination = self.root / "mission"
        args, _ = self.resolve("--mission", "Explicit task", "--cwd", str(source), "--prepare-workspaces", str(destination))
        self.assertEqual(args.workspace_mode, "snapshot")
        self.assertFalse(destination.exists())
        with self.assertRaisesRegex(ValueError, "outside"):
            self.resolve("--mission", "task", "--cwd", str(source), "--prepare-workspaces", str(source / "nested"))

    def test_replay_disables_live_actions_and_selects_replay_screen(self):
        args, _ = self.resolve("--replay", str(self.root / "record.jsonl"))
        self.assertEqual(args.screen, "replay")
        for extra in (("--task", "echo test"), ("--terminal",), ("--source-url", "https://example.test/stats"),
                      ("--command", "codex"), ("--right-command", "claude"), ("--heart-file", "heart.json"),
                      ("--mission", "task"), ("--agents", "mission")):
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, "replay"):
                self.resolve("--replay", str(self.root / "record.jsonl"), *extra)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            parser().parse_args(["--replay", "one.jsonl", "--record", "two.jsonl"])
        self.assertEqual(error.exception.code, 2)

    def test_startup_errors_are_concise_after_curses_restoration(self):
        for failure in (OSError("file unavailable"), ValueError("invalid recording"), RuntimeError("worker startup failed")):
            output = io.StringIO()
            with self.subTest(failure=failure), contextlib.redirect_stderr(output), \
                    patch("devos.cli.sys.stdin.isatty", return_value=True), \
                    patch("devos.cli.sys.stdout.isatty", return_value=True), \
                    patch("devos.cli.curses.wrapper", side_effect=failure), \
                    self.assertRaises(SystemExit) as error:
                main(["--no-config"])
            self.assertEqual(error.exception.code, 1)
            self.assertIn(str(failure), output.getvalue())
            self.assertNotIn("Traceback", output.getvalue())

    def test_list_presets_does_not_need_terminal_or_load_user_config(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch("devos.presets.load") as load:
            main(["--list-presets"])
        self.assertIn("night-shift", output.getvalue())
        self.assertIn("duet", output.getvalue())
        load.assert_not_called()

    def test_bad_options_have_clear_preterminal_errors(self):
        for arguments in (("--source-url", "file:///private"), ("--source-url", "https://user:secret@example.test"),
                          ("--source-url", "https://example.test:bad"), ("--handoffs", "0"), ("--handoffs", "21"),
                          ("--task", "'unclosed"), ("--config", "one.json", "--no-config")):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                parser().parse_args(arguments)
            self.assertEqual(error.exception.code, 2)
        for arguments in (("--mission-timeout", "3601"), ("--mission", " "), ("--speed", "9")):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.resolve(*arguments)
