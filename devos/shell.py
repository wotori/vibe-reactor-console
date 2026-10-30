"""Optional real terminal: an isolated tmux server emulates the child PTY.

Only captured text and SGR foreground styles reach curses. tmux owns cursor
movement, alternate screens, UTF-8, scrolling and application key sequences.
Subprocess calls run on a worker so a busy child never blocks the graphics loop.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import queue
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
import uuid


@dataclass(frozen=True)
class Style:
    foreground: object = None
    bold: bool = False
    underline: bool = False


@dataclass(frozen=True)
class Run:
    column: int
    text: str
    style: Style


@dataclass(frozen=True)
class TerminalFrame:
    lines: tuple = ()
    cursor_x: int = 0
    cursor_y: int = 0
    cursor_visible: bool = False
    dead: bool = False
    status: str = "Starting terminal..."


ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b.")


def cell_width(char):
    if unicodedata.category(char) in ("Cc", "Cs"):
        return 0
    if unicodedata.combining(char) or unicodedata.category(char) == "Cf":
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def clip_cells(text, width):
    """Clip by terminal cells rather than UTF-8 bytes or Python string length."""
    result, used = [], 0
    for char in text:
        if unicodedata.category(char) in ("Cc", "Cs"):
            continue
        size = cell_width(char)
        if used + size > width:
            break
        if size or result:
            result.append(char)
            used += size
    return "".join(result)


def sgr(style, parameters):
    values = [int(value or 0) for value in parameters.split(";")] if parameters else [0]
    foreground, bold, underline = style.foreground, style.bold, style.underline
    index = 0
    while index < len(values):
        value = values[index]
        if value == 0:
            foreground, bold, underline = None, False, False
        elif value == 1:
            bold = True
        elif value == 22:
            bold = False
        elif value in (4, 7):
            # Selection remains legible without replacing the native background.
            underline = True
        elif value in (24, 27):
            underline = False
        elif 30 <= value <= 37:
            foreground = value - 30
        elif 90 <= value <= 97:
            foreground = value - 90 + 8
        elif value == 39:
            foreground = None
        elif value in (38, 48) and index + 1 < len(values):
            mode = values[index + 1]
            count = 2 if mode == 5 else 4 if mode == 2 else 0
            if count and index + count < len(values):
                if value == 38:
                    color = values[index + 2:index + count + 1]
                    foreground = color[0] if mode == 5 else tuple(color)
                index += count
        index += 1
    return Style(foreground, bold, underline)


def styled_lines(text, width, height):
    """tmux has already emulated VT sequences; retain only safe text and SGR."""
    lines, style = [], Style()
    for line in text.split("\n")[:height]:
        runs, column, start = [], 0, 0
        for match in ESCAPE.finditer(line):
            chunk = clip_cells(line[start:match.start()], max(0, width - column))
            if chunk:
                runs.append(Run(column, chunk, style))
                column += sum(cell_width(char) for char in chunk)
            escape = match.group()
            if escape.startswith("\x1b[") and escape.endswith("m"):
                parameters = escape[2:-1]
                if all(char in "0123456789;" for char in parameters):
                    style = sgr(style, parameters)
            start = match.end()
        chunk = clip_cells(line[start:], max(0, width - column))
        if chunk:
            runs.append(Run(column, chunk, style))
        lines.append(tuple(runs))
    return tuple(lines)


class EmbeddedTerminal:
    """A disposable, private tmux session. Never touches existing tmux sessions."""

    def __init__(self, width, height, cwd=None, command=None):
        self.executable = shutil.which("tmux")
        if not self.executable:
            raise RuntimeError("Real terminal needs tmux. Install tmux, then restart DEV/OS.")
        self.cwd = str(Path(cwd or Path.cwd()).resolve())
        self.command = tuple(command or (os.environ.get("SHELL") or "/bin/sh", "-l"))
        self.socket = "devos-" + uuid.uuid4().hex
        self.target = "work:0.0"
        self.environment = dict(os.environ)
        self.environment.pop("TMUX", None)
        self.environment.pop("TMUX_PANE", None)
        self._size = max(2, width), max(1, height)
        self._frame = TerminalFrame()
        self._lock = threading.Lock()
        self._input = queue.Queue(maxsize=8192)
        self._queued_bytes = 0
        self._visible = True
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="devos-terminal", daemon=True)
        self._thread.start()

    def snapshot(self):
        with self._lock:
            return self._frame

    def resize(self, width, height):
        with self._lock:
            self._size = max(2, width), max(1, height)

    def send(self, data):
        """Bytes are literal UTF-8/control input; strings name tmux keys, e.g. Up."""
        if not data:
            return True
        if not isinstance(data, (bytes, str)):
            raise TypeError("terminal input must be bytes or a named key")
        size = len(data)
        if size > 16384:
            raise ValueError("Paste exceeds 16 KiB; split it into smaller chunks.")
        with self._lock:
            if self._stop.is_set() or self._queued_bytes + size > 65536:
                return False
            try:
                self._input.put_nowait(data)
            except queue.Full:
                return False
            self._queued_bytes += size
        return True

    def set_visible(self, visible):
        """Keep hidden jobs running while reducing captures to two per second."""
        with self._lock:
            self._visible = bool(visible)

    def close(self):
        self._stop.set()
        self._thread.join(timeout=5)

    def _invoke(self, *arguments, timeout=1.0, check=True):
        result = subprocess.run([self.executable, "-L", self.socket, *arguments],
                                env=self.environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout)
        if check and result.returncode:
            raise RuntimeError(result.stderr.decode("utf-8", "replace").strip() or "tmux command failed")
        return result.stdout.decode("utf-8", "replace")

    def _start(self, size):
        # This config belongs only to our private server. In particular, no user
        # config, hooks, key bindings or tmux plugins are loaded.
        with tempfile.NamedTemporaryFile(mode="w", prefix="devos-tmux-", suffix=".conf") as config:
            config.write("set -g status off\nset -g history-limit 2000\n"
                         "setw -g remain-on-exit on\nsetw -g remain-on-exit-format ''\n"
                         "set -g set-clipboard off\nset -g allow-rename off\n"
                         "set -g default-terminal screen-256color\n")
            config.flush()
            self._invoke("-f", config.name, "new-session", "-d", "-s", "work",
                         "-x", str(size[0]), "-y", str(size[1]), "-c", self.cwd,
                         "exec " + shlex.join(self.command), timeout=3)

    def _send_pending(self):
        literal = bytearray()

        def flush():
            while literal and not self._stop.is_set():
                chunk = literal[:2048]
                self._invoke("send-keys", "-t", self.target, "-H", *("{:02x}".format(byte) for byte in chunk))
                del literal[:2048]

        for _ in range(128):
            if self._stop.is_set():
                break
            try:
                item = self._input.get_nowait()
            except queue.Empty:
                break
            with self._lock:
                self._queued_bytes -= len(item)
            if isinstance(item, bytes):
                literal.extend(item)
                if len(literal) >= 2048:
                    flush()
            else:
                flush()
                self._invoke("send-keys", "-t", self.target, item)
        flush()

    def _capture(self):
        output = self._invoke("display-message", "-p", "-t", self.target,
                              "#{pane_width} #{pane_height} #{cursor_x} #{cursor_y} #{cursor_flag} #{pane_dead}",
                              ";", "capture-pane", "-p", "-e", "-t", self.target)
        metadata, _, text = output.partition("\n")
        width, height, x, y, visible, dead = map(int, metadata.split())
        status = "PROCESS EXITED / F2 restart / F3 source" if dead else "LIVE / " + Path(self.command[0]).name
        return TerminalFrame(styled_lines(text, width, height), x, y, bool(visible), bool(dead), status)

    def _loop(self):
        try:
            with self._lock:
                size = self._size
            self._start(size)
            captured = 0.0
            while not self._stop.is_set():
                started = time.monotonic()
                with self._lock:
                    requested = self._size
                    visible = self._visible
                if requested != size:
                    self._invoke("resize-window", "-t", self.target, "-x", str(requested[0]), "-y", str(requested[1]))
                    size = requested
                self._send_pending()
                if started - captured >= (1 / 30 if visible else .5):
                    frame = self._capture()
                    with self._lock:
                        self._frame = frame
                    captured = started
                self._stop.wait(max(0, 1 / 30 - (time.monotonic() - started)))
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            with self._lock:
                self._frame = TerminalFrame(dead=True, status="Terminal error: " + str(exc))
        finally:
            try:
                self._invoke("kill-server", check=False)
            except (OSError, subprocess.SubprocessError):
                pass
