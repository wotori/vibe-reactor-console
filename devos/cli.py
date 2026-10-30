"""Command-line entry point. The terminal is initialized only after validation."""

import argparse
import math
import os
from pathlib import Path
import shlex
import sys

from . import __version__
from .modes import DEFAULT_MODE, MODES, MODE_BY_ID
from .session import PALETTES
from .terminal import curses
from .protocol import CONSUMER_IDS
from .visuals import ANIMATION_BY_ID


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
    if not words:
        raise argparse.ArgumentTypeError("expected a command, for example codex")
    return tuple(words)


def working_directory(value):
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise argparse.ArgumentTypeError("working directory does not exist")
    return str(path)


def parser():
    result = argparse.ArgumentParser(
        prog="devos",
        description="DEV/OS: a cinematic ASCII coding simulator. Python 3.9+, no runtime packages.",
        epilog="TAB: scenes/options | G: animations/options | F2/Ctrl+]: terminal focus | F3: source | W: scope | A: auto visuals | Q: quit")
    result.add_argument("--version", action="version", version="DEV/OS " + __version__)
    result.add_argument("--list-modes", action="store_true", help="list scenes without opening the terminal UI")
    result.add_argument("--mode", choices=MODE_BY_ID, default=DEFAULT_MODE, help="initial scene (default: ekza)")
    result.add_argument("--ekza-root", help="Ekza umbrella folder (default: sibling ekza folder)")
    result.add_argument("--bundled", action="store_true", help="use packaged source snapshots instead of local repositories")
    result.add_argument("--theme", choices=PALETTES, help="override each scene's default color palette")
    result.add_argument("--animation", choices=ANIMATION_BY_ID, help="choose a visual independently of the code scene")
    result.add_argument("--consumer", choices=CONSUMER_IDS, default="all", help="protocol example consumer (default: all)")
    result.add_argument("--auto-cycle", type=positive_float, metavar="SECONDS", help="automatically rotate visuals after this many active seconds")
    result.add_argument("--no-scope", action="store_true", help="hide the sine-wave panel")
    result.add_argument("--terminal", action="store_true", help="open a real shell in SOURCE and focus it (requires tmux)")
    result.add_argument("--command", dest="terminal_command", type=command_words, metavar="COMMAND",
                        help="open a real terminal running this command, e.g. 'codex' (no shell expansion)")
    result.add_argument("--cwd", type=working_directory, help="working directory for the real terminal (default: current directory)")
    result.add_argument("--background", choices=("terminal", "black"), default="terminal",
                        help="keep the terminal's own background, or force black (default: terminal)")
    result.add_argument("--speed", type=positive_float, default=1.0, help="animation speed, 0.25 to 8")
    result.add_argument("--fps", type=int, choices=range(10, 121), metavar="10..120", default=60, help="frame cap (default: 60)")
    result.add_argument("--duration", type=positive_float, help="exit after this many wall-clock seconds")
    result.add_argument("--seed", type=int, help="repeatable simulation seed")
    return result


def main(argv=None):
    command = parser()
    args = command.parse_args(argv)
    if not 0.25 <= args.speed <= 8:
        command.error("--speed must be between 0.25 and 8")
    if args.list_modes:
        for mode in MODES:
            print("{:<12} {:<12} {}".format(mode.id, mode.title, mode.description))
        return
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
