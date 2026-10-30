"""Command-line entry point. The terminal is initialized only after validation."""

import argparse
import math
import os
import re
from pathlib import Path
import shlex
import sys
from urllib.parse import urlsplit

from . import __version__
from .modes import DEFAULT_MODE, MODES, MODE_BY_ID
from .session import PALETTES
from .terminal import curses
from .protocol import CONSUMER_IDS
from .visuals import ANIMATION_BY_ID
from .bindings import BINDING_BY_ID
from .screen_registry import SCREEN_BY_ID
from .math_scope import SCOPE_BY_ID


class DevosParser(argparse.ArgumentParser):
    """Remember explicit preferences so defaults cannot overwrite a preset."""
    def parse_args(self, args=None, namespace=None):
        arguments = list(sys.argv[1:] if args is None else args)
        parsed = super().parse_args(arguments, namespace)
        parsed._explicit = frozenset(self._option_string_actions[token.split("=", 1)[0]].dest
                                     for token in arguments if token.split("=", 1)[0] in self._option_string_actions)
        return parsed


def positive_float(value):
    try:
        result = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a positive number")
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("expected a finite positive number")
    return result


def command_words(value):
    try:
        words = shlex.split(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc))
    if not words or any("\0" in word for word in words):
        raise argparse.ArgumentTypeError("expected a command, for example codex")
    return tuple(words)


def file_path(value):
    if "\0" in value:
        raise argparse.ArgumentTypeError("path contains a null character")
    return str(Path(value).expanduser().resolve())


def http_url(value):
    try:
        url = urlsplit(value)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            raise ValueError()
        url.port
    except ValueError:
        raise argparse.ArgumentTypeError("expected an HTTP(S) URL without embedded credentials")
    return value


def working_directory(value):
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise argparse.ArgumentTypeError("working directory does not exist")
    return str(path)


def environment_name(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise argparse.ArgumentTypeError("expected an environment variable name, not a token value")
    return value


def parser():
    result = DevosParser(
        prog="devos",
        description="DEV/OS: terminal cockpit, live signals and a bounded agent arena. Python 3.9+, no runtime packages.",
        epilog="TAB: scenes/options | G: graphics | 1..8: screens / 8 Arcade | P: commands | F2/Ctrl+]: terminal | F3: source | ?: help | Q: confirm exit")
    result.add_argument("--version", action="version", version="DEV/OS " + __version__)
    result.add_argument("--list-modes", action="store_true", help="list scenes without opening the terminal UI")
    result.add_argument("--list-presets", action="store_true", help="list safe preference presets without a terminal")
    result.add_argument("--integrate", nargs=4, metavar=("BASE", "LEFT", "RIGHT", "DEST"),
                        help="create a new source-code candidate from three folders; no TUI or model calls")
    result.add_argument("--screen", choices=SCREEN_BY_ID, default="cockpit", help="initial product screen")
    result.add_argument("--preset", metavar="NAME_OR_JSON", help="apply a bundled preset or preference JSON file")
    config = result.add_mutually_exclusive_group()
    config.add_argument("--config", type=file_path, metavar="JSON", help="read and save local preferences in this JSON file")
    config.add_argument("--no-config", action="store_true", help="ignore saved preferences and disable saving for this session")
    result.add_argument("--mode", choices=MODE_BY_ID, default=DEFAULT_MODE, help="initial scene (default: ekza)")
    result.add_argument("--ekza-root", help="Ekza umbrella folder (default: sibling ekza folder)")
    result.add_argument("--project-root", type=working_directory, metavar="DIR",
                        help="scan this selected project's source files for 01 / SOURCE; remembered locally")
    result.add_argument("--bundled", action="store_true", help="use packaged source snapshots for this session instead of local repositories")
    result.add_argument("--theme", choices=("auto", *PALETTES), help="override each scene's default color palette")
    result.add_argument("--animation", choices=ANIMATION_BY_ID, help="choose a visual independently of the code scene")
    result.add_argument("--consumer", choices=CONSUMER_IDS, default="all", help="protocol example consumer (default: all)")
    result.add_argument("--auto-cycle", type=positive_float, metavar="SECONDS", help="automatically rotate visuals after this many active seconds")
    result.add_argument("--no-scope", action="store_true", help="hide the math panel")
    result.add_argument("--scope-mode", choices=SCOPE_BY_ID, default="harmonics", help="animated mathematical function in the math panel")
    scope_cycle = result.add_mutually_exclusive_group()
    scope_cycle.add_argument("--scope-auto-cycle", type=positive_float, metavar="SECONDS",
                             help="rotate math functions after 0.25 to 86400 active seconds")
    scope_cycle.add_argument("--no-scope-auto-cycle", dest="scope_auto_cycle", action="store_const", const=0,
                             help="disable automatic math function rotation, including saved settings")
    result.set_defaults(scope_auto_cycle=0)
    result.add_argument("--intensity", choices=("calm", "cinematic", "showcase"), default="cinematic", help="visual activity preset")
    result.add_argument("--binding", dest="lab_binding", choices=BINDING_BY_ID, default="online",
                        help="signal driving Visual Lab")
    result.add_argument("--data-mode", choices=("auto", "demo", "live"), default="auto", help="select signal provenance; unavailable live data stays unavailable")
    sources = result.add_mutually_exclusive_group()
    sources.add_argument("--source-file", type=file_path, metavar="JSON", help="poll an aggregate site/metric JSON file")
    sources.add_argument("--source-url", type=http_url, metavar="URL", help="poll an HTTP(S) aggregate metric endpoint")
    result.add_argument("--source-token-env", type=environment_name, metavar="NAME",
                        help="read the HTTP Bearer token from this environment variable; never saved in presets")
    sources.add_argument("--source-stream", type=file_path, metavar="JSONL", help="tail a local NDJSON event file or FIFO")
    result.add_argument("--heart-file", type=file_path, metavar="JSON", help="read an explicit local heart-rate bridge")
    result.add_argument("--git-root", type=working_directory, metavar="DIR", help="inspect this real repository in Atlas")
    result.add_argument("--asset-manifest", type=file_path, metavar="JSON", help="explicit local asset catalog for Atlas")
    result.add_argument("--export-dir", type=file_path, metavar="DIR", help="destination parent for explicit report/capsule/context exports")
    result.add_argument("--director", dest="director_enabled", action="store_true", help="enable finite spectator visual recommendations")
    result.set_defaults(binding_config=None, visual_time=0, protocol_time=0, paused=False)
    recording = result.add_mutually_exclusive_group()
    recording.add_argument("--record", type=file_path, metavar="JSONL", help="record normalized events to a new file")
    recording.add_argument("--replay", type=file_path, metavar="JSONL", help="open an event recording with live actions disabled")
    result.add_argument("--record-pulse", action="store_true", help="explicitly include personal pulse measurements when recording")
    result.add_argument("--task", type=command_words, metavar="COMMAND", help="configure an explicit local check; run from the command palette")
    result.add_argument("--terminal", action="store_true", help="open a real shell in SOURCE and focus it (requires tmux)")
    result.add_argument("--command", dest="terminal_command", type=command_words, metavar="COMMAND",
                        help="open a real terminal running this command, e.g. 'codex' (no shell expansion)")
    result.add_argument("--right-command", type=command_words, metavar="COMMAND", help="command for the independent right Arena terminal")
    result.add_argument("--agents", choices=("demo", "watch", "relay", "mission"), default="demo", help="Arena mode; managed modes require an explicit mission")
    result.add_argument("--mission", metavar="PROMPT", help="explicit managed task prompt; never loaded from a preset")
    result.add_argument("--workspace-left", type=working_directory, metavar="DIR", help="isolated Codex managed working directory")
    result.add_argument("--workspace-right", type=working_directory, metavar="DIR", help="isolated Claude managed working directory")
    result.add_argument("--prepare-workspaces", type=file_path, metavar="DEST", help="explicitly prepare isolated roots outside the current project")
    result.add_argument("--workspace-mode", choices=("snapshot", "worktree"), default="snapshot", help="workspace preparation strategy; worktree needs a base commit")
    result.add_argument("--scenario", choices=("design-rally", "builder-challenger", "split"), default="design-rally", help="finite managed task graph")
    result.add_argument("--handoffs", type=int, choices=range(1, 21), default=6, metavar="1..20", help="maximum managed deliveries per mission")
    result.add_argument("--mission-timeout", type=positive_float, default=900, metavar="SECONDS", help="managed wall-clock limit, at most 3600 seconds")
    result.add_argument("--cwd", type=working_directory, help="working directory for the real terminal (default: current directory)")
    result.add_argument("--background", choices=("terminal", "black"), default="terminal",
                        help="keep the terminal's own background, or force black (default: terminal)")
    result.add_argument("--speed", type=positive_float, default=1.0, help="animation speed, 0.25 to 8")
    result.add_argument("--fps", type=int, choices=range(10, 121), metavar="10..120", default=60, help="frame cap (default: 60)")
    result.add_argument("--duration", type=positive_float, help="exit after this many wall-clock seconds")
    result.add_argument("--seed", type=int, help="repeatable simulation seed")
    return result


def runtime_options(args):
    """Resolve defaults < config < preset < explicit CLI without starting jobs.

    Resolved attributes remain on ``args`` for the app boundary. The returned
    dictionary contains only Session preferences, never commands or credentials.
    """
    from . import presets, settings
    explicit = getattr(args, "_explicit", frozenset())
    preference_path = Path(args.config) if args.config else presets.config_path()
    preferences = {}
    local = {}
    args.config_warning = ""
    if not args.no_config:
        try:
            if args.config or preference_path.exists():
                local = settings.load(preference_path)
                preferences.update({key: value for key, value in local.items() if key in presets.SAFE_KEYS})
        except (OSError, ValueError, TypeError) as exc:
            if args.config:
                raise
            args.config_warning = "Saved settings ignored: " + str(exc)
    if "project_root" in local and "project_root" not in explicit:
        args.project_root = local["project_root"]
    if args.preset:
        preferences.update(presets.resolve(args.preset))
    mapping = {"screen": "screen", "scene": "mode", "visual": "animation", "theme": "theme",
               "background": "background", "fps_cap": "fps", "speed": "speed", "seed": "seed",
               "auto_cycle": "auto_cycle", "intensity": "intensity", "lab_binding": "lab_binding",
               "scope_mode": "scope_mode", "scope_auto_cycle": "scope_auto_cycle",
               "binding_config": "binding_config", "director_enabled": "director_enabled", "consumer": "consumer",
               "visual_time": "visual_time", "protocol_time": "protocol_time", "paused": "paused",
               "arena_mode": "agents", "data_mode": "data_mode"}
    for key, destination in mapping.items():
        if key in preferences and destination not in explicit:
            setattr(args, destination, preferences[key])
    if "scope" in preferences and "no_scope" not in explicit:
        args.no_scope = not preferences["scope"]
    args.config_path = str(preference_path)
    if args.source_token_env and not args.source_url:
        raise ValueError("--source-token-env requires an explicit --source-url")
    if not 0.25 <= args.speed <= 8:
        raise ValueError("--speed must be between 0.25 and 8")
    if args.scope_auto_cycle != 0 and not .25 <= args.scope_auto_cycle <= 86400:
        raise ValueError("--scope-auto-cycle must be between 0.25 and 86400 seconds")
    if args.mission_timeout > 3600:
        raise ValueError("--mission-timeout must be at most 3600 seconds")
    if args.replay:
        if "project_root" in explicit:
            raise ValueError("--replay cannot be combined with --project-root")
        # Remembered project access is not necessary to reconstruct a recording.
        args.project_root = None
        if any((args.terminal, args.terminal_command, args.right_command, args.task, args.mission, args.record,
                args.source_file, args.source_url, args.source_stream, args.heart_file, args.git_root,
                args.agents in ("relay", "mission"), args.asset_manifest)):
            raise ValueError("--replay cannot be combined with live sources, terminals, tasks, missions or recording")
        if "screen" not in explicit:
            args.screen = "replay"
    if args.mission is not None:
        if not args.mission.strip() or len(args.mission.encode("utf-8")) > 12000:
            raise ValueError("--mission needs a nonempty prompt of at most 12,000 bytes")
        if "agents" not in explicit:
            args.agents = "mission"
    if args.agents in ("relay", "mission"):
        if not args.mission:
            raise ValueError("Managed --agents mode requires an explicit --mission prompt")
        if args.prepare_workspaces:
            if args.workspace_left or args.workspace_right:
                raise ValueError("Choose --prepare-workspaces or two explicit workspaces")
            source = Path(args.cwd or Path.cwd()).resolve()
            destination = Path(args.prepare_workspaces).resolve()
            if destination == source or source in destination.parents:
                raise ValueError("--prepare-workspaces must be outside the source project")
        elif args.workspace_left and args.workspace_right:
            from .agents import validate_workspaces
            validate_workspaces({"codex": args.workspace_left, "claude": args.workspace_right})
        else:
            raise ValueError("Managed mode needs --workspace-left and --workspace-right, or --prepare-workspaces")
    elif args.mission:
        raise ValueError("--mission requires --agents relay or mission")
    if bool(args.workspace_left) != bool(args.workspace_right):
        raise ValueError("Provide both isolated managed workspace paths")
    return {"mode": args.mode, "theme": args.theme, "animation": args.animation,
            "background": args.background, "fps": args.fps, "speed": args.speed,
            "seed": args.seed, "auto_cycle": args.auto_cycle or 0, "scope": not args.no_scope,
            "scope_mode": args.scope_mode, "scope_auto_cycle": args.scope_auto_cycle,
            "consumer": args.consumer, "screen": args.screen, "intensity": args.intensity,
            "lab_binding": args.lab_binding, "data_mode": args.data_mode, "arena_mode": args.agents,
            "binding_config": args.binding_config, "director_enabled": args.director_enabled,
            "visual_time": args.visual_time, "protocol_time": args.protocol_time, "paused": args.paused}


def main(argv=None):
    command = parser()
    args = command.parse_args(argv)
    if args.integrate:
        if any((args.terminal, args.terminal_command, args.right_command, args.task, args.mission,
                args.source_file, args.source_url, args.source_stream, args.heart_file, args.record, args.replay, args.asset_manifest)):
            command.error("--integrate is a standalone operation; do not combine it with live actions")
        from .integration import integrate_workspaces
        try:
            candidate = integrate_workspaces(*args.integrate)
        except (OSError, ValueError, RuntimeError) as exc:
            command.error(str(exc))
        print("Candidate: " + candidate.root)
        print("Report: " + candidate.report_path)
        print("Applied: {} / conflicts: {} / NOT VERIFIED".format(len(candidate.applied), len(candidate.conflicts)))
        if candidate.conflicts:
            command.exit(2, "Resolve candidate conflicts and run your explicit checks before integration.\n")
        return
    if args.list_modes:
        for mode in MODES:
            print("{:<12} {:<12} {}".format(mode.id, mode.title, mode.description))
        return
    if args.list_presets:
        from .presets import PRESETS
        for name, settings in PRESETS.items():
            print("{:<14} {:<12} {}".format(name, settings.get("screen", "cockpit"), settings.get("visual", "")))
        return
    try:
        runtime_options(args)
    except (OSError, ValueError, TypeError) as exc:
        command.error(str(exc))
    if curses is None:
        command.exit(1, "Python with curses is required (included on macOS and most Linux systems).\n")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        command.exit(1, "Run directly in an interactive terminal: python3 -m devos\n")
    if not os.environ.get("TERM") or os.environ.get("TERM") == "dumb":
        os.environ["TERM"] = "xterm-256color"
    from .app import run

    try:
        curses.wrapper(lambda screen: run(screen, args))
    except KeyboardInterrupt:
        pass
    except curses.error as exc:
        command.exit(1, "Terminal initialization failed: {}\n".format(exc))
    except (OSError, ValueError, RuntimeError) as exc:
        command.exit(1, "DEV/OS could not start: {}\n".format(exc))
