"""Bounded background indexing and read-only selections from a chosen project.

The dashboard only exchanges immutable selections with this worker. Directory
walking, opening files, decoding and preparing excerpts never run on a frame.
"""

from dataclasses import dataclass
import os
from pathlib import PurePosixPath
import random
import stat
import threading
import time

from .domain import Snippet, SourceDocument


MAX_ENTRIES = 8000
MAX_FILES = 2048
MAX_DEPTH = 14
MAX_SCAN_SECONDS = 1.0
MAX_FILE_BYTES = 512 * 1024
MAX_EXCERPT_LINES = 56
MAX_DISPLAY_COLUMNS = 512
IGNORED_DIRECTORIES = frozenset((
    "node_modules", "vendor", "target", "build", "dist", "out", "coverage",
    "venv", "env", "__pycache__", "site-packages", "pods", "deriveddata",
    "bower_components", "generated", "third_party", "third-party",
))
LANGUAGES = {
    ".py": "Python", ".pyi": "Python", ".rs": "Rust", ".c": "C", ".h": "C",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++", ".hxx": "C++",
    ".s": "NASM", ".asm": "NASM", ".go": "Go", ".js": "JavaScript",
    ".jsx": "JavaScript", ".mjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".cs": "C#",
    ".swift": "Swift", ".m": "Objective-C", ".mm": "Objective-C++", ".rb": "Ruby",
    ".php": "PHP", ".lua": "Lua", ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".sql": "SQL", ".zig": "Zig", ".v": "Verilog", ".sv": "Verilog",
    ".vhd": "VHDL", ".vhdl": "VHDL", ".dart": "Dart", ".ex": "Elixir",
    ".exs": "Elixir", ".erl": "Erlang", ".hrl": "Erlang", ".clj": "Clojure",
    ".cljs": "Clojure", ".scala": "Scala", ".vue": "Vue", ".svelte": "Svelte",
    ".glsl": "GLSL", ".vert": "GLSL", ".frag": "GLSL", ".wgsl": "WGSL",
    ".cu": "CUDA", ".cuh": "CUDA",
}


@dataclass(frozen=True)
class ProjectSelection:
    snippet: Snippet
    source: SourceDocument


@dataclass(frozen=True)
class ProjectStatus:
    state: str = "inactive"
    note: str = "default scene sources"
    file_count: int = 0
    generation: int = 0


def normalized_root(path):
    """Normalize a user-selected path without filesystem access."""
    return os.path.abspath(os.path.expanduser(os.fspath(path))) if path else None


def _language(name):
    lower = name.lower()
    # Do not automatically put common secret-bearing files on a public display.
    if name.startswith(".") or any(word in lower for word in (
            "secret", "credential", "private_key", "private-key", "api_key", "api-key", "apikey",
            "auth_token", "access_token", "password", "keystore", "keychain", "id_rsa", "id_ed25519")):
        return None
    if PurePosixPath(lower).stem in ("keys", "key", "token", "tokens"):
        return None
    return LANGUAGES.get(PurePosixPath(lower).suffix)


def _open_directory(root, parts=()):
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(root, flags)
    try:
        for part in parts:
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _scan(root, cancelled):
    files, visited, limited = [], 0, False
    stack = [()]
    deadline = time.monotonic() + MAX_SCAN_SECONDS
    # Validate even an empty root and reject symlink roots.
    fd = _open_directory(root)
    os.close(fd)
    while stack and not cancelled():
        if visited >= MAX_ENTRIES or len(files) >= MAX_FILES or time.monotonic() >= deadline:
            limited = True
            break
        parts = stack.pop()
        try:
            directory = _open_directory(root, parts)
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        visited += 1
                        if (visited > MAX_ENTRIES or len(files) >= MAX_FILES
                                or time.monotonic() >= deadline or cancelled()):
                            limited = True
                            break
                        name = entry.name
                        if name.startswith("."):
                            continue
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        relative = parts + (name,)
                        if stat.S_ISDIR(info.st_mode):
                            if name.lower() not in IGNORED_DIRECTORIES and len(relative) < MAX_DEPTH:
                                stack.append(relative)
                        elif (stat.S_ISREG(info.st_mode) and _language(name)
                              and 0 < info.st_size <= MAX_FILE_BYTES):
                            files.append("/".join(relative))
            finally:
                os.close(directory)
        except OSError:
            continue
    return tuple(sorted(files)), limited


def _prepare(root, relative, rng, current=None):
    # Import late to keep the legacy SourceProvider API independent of its worker.
    from .sources import display_lines, document

    parts = PurePosixPath(relative).parts
    if not parts or any(part in ("..", ".") for part in parts) or relative.startswith("/"):
        return None
    directory = _open_directory(root, parts[:-1])
    try:
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                 | getattr(os, "O_CLOEXEC", 0))
        fd = os.open(parts[-1], flags, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE_BYTES:
                return None
            with os.fdopen(fd, "rb", closefd=False) as source:
                raw = source.read(MAX_FILE_BYTES + 1)
            after = os.fstat(fd)
            if (len(raw) > MAX_FILE_BYTES or b"\x00" in raw or
                    (info.st_ino, info.st_size, info.st_mtime_ns) !=
                    (after.st_ino, after.st_size, after.st_mtime_ns)):
                return None
        finally:
            os.close(fd)
    finally:
        os.close(directory)
    lines = display_lines(raw.decode("utf-8"))
    if not lines or not any(line.strip() for line in lines):
        return None
    upper = max(0, len(lines) - MAX_EXCERPT_LINES)
    start = min(upper, max(0, current.source.first_line - 1)) if current else rng.randint(0, upper)
    excerpt = lines[start:start + MAX_EXCERPT_LINES]
    if not any(line.strip() for line in excerpt):
        first = next(i for i, line in enumerate(lines) if line.strip())
        start = min(upper, first)
        excerpt = lines[start:start + MAX_EXCERPT_LINES]
    clipped = any(len(line) > MAX_DISPLAY_COLUMNS for line in excerpt)
    excerpt = tuple(line[:MAX_DISPLAY_COLUMNS] + (" …" if len(line) > MAX_DISPLAY_COLUMNS else "")
                    for line in excerpt)
    note = "read-only project excerpt; build activity is simulated"
    if clipped:
        note += "; long lines truncated for display"
    snippet = Snippet(relative, _language(parts[-1]) or "Code", "random project code / display only",
                      "", source_path=relative, first_line=start + 1, max_lines=MAX_EXCERPT_LINES)
    return ProjectSelection(snippet, document(excerpt, start + 1, "PROJECT", note))


class ProjectSourceWorker:
    """One latest request and one immutable result; no unbounded prefetch queue."""

    def __init__(self, root):
        self._condition = threading.Condition()
        self._root, self._generation = normalized_root(root), 1
        self._status = ProjectStatus("scanning", "indexing project code…", 0, 1)
        self._request = self._result = None
        self._selection_serial = 0
        self._active = self._closed = False
        self._thread = threading.Thread(target=self._run, name="devos-project-source", daemon=True)
        self._thread.start()

    @property
    def status(self):
        with self._condition:
            return self._status

    def set_root(self, root):
        with self._condition:
            self._root = normalized_root(root)
            self._generation += 1
            self._request = self._result = None
            self._selection_serial += 1
            self._status = ProjectStatus("scanning" if self._root else "inactive",
                                         "indexing project code…" if self._root else "default scene sources",
                                         0, self._generation)
            self._condition.notify_all()

    def _enqueue(self, rng, current, refresh):
        self._selection_serial += 1
        retry_scan = self._status.state in ("empty", "error")
        self._request = (self._generation, rng.getrandbits(64), current, refresh or retry_scan,
                         self._selection_serial)
        self._result = None
        self._status = ProjectStatus("reading", "preparing project code…", self._status.file_count,
                                     self._generation)
        self._condition.notify_all()

    def request(self, rng, current=None, refresh=False):
        """Explicit next/reload action; replace any older pending selection."""
        with self._condition:
            if self._root and not self._closed:
                self._enqueue(rng, current, refresh)

    def poll(self, rng, current=None, refresh=False):
        with self._condition:
            if self._result is not None:
                result = self._result
                self._result = None
                return result
            if (self._root and not self._closed and self._request is None and not self._active
                    and self._status.state not in ("empty", "error")):
                self._enqueue(rng, current, refresh)
            return None

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._thread.join(timeout=0.15)

    def _run(self):
        scanned_generation, files, limited = -1, (), False
        while True:
            with self._condition:
                while (not self._closed and self._generation == scanned_generation
                       and self._request is None):
                    self._condition.wait()
                if self._closed:
                    return
                root, generation = self._root, self._generation
                request = self._request
                self._request = None
                self._active = True
            cancelled = lambda: self._closed or self._generation != generation
            try:
                if generation != scanned_generation or (request and request[3]):
                    files, limited = _scan(root, cancelled) if root else ((), False)
                    scanned_generation = generation
                result = None
                if root and request and request[0] == generation and not cancelled():
                    _, seed, current, refresh, serial = request
                    rng = random.Random(seed)
                    preferred = current.snippet.path if current and refresh else None
                    candidates = list(files)
                    rng.shuffle(candidates)
                    if current and not refresh and len(candidates) > 1 and current.snippet.path in candidates:
                        candidates.remove(current.snippet.path)
                        candidates.append(current.snippet.path)
                    if preferred in candidates:
                        candidates.remove(preferred)
                        candidates.insert(0, preferred)
                    for relative in candidates[:32]:
                        if cancelled():
                            break
                        try:
                            result = _prepare(root, relative, rng,
                                              current if preferred == relative else None)
                        except (OSError, ValueError, UnicodeError):
                            continue
                        if result is not None:
                            break
                with self._condition:
                    if (not cancelled() and (request is None or request[4] == self._selection_serial)
                            and not (request is None and self._request is not None)):
                        if not root:
                            self._status = ProjectStatus(generation=generation)
                        elif not files:
                            self._status = ProjectStatus("empty", "no supported project code found; using scene sources",
                                                         0, generation)
                        elif request and result is None:
                            self._status = ProjectStatus("empty", "no readable project code; using scene sources",
                                                         len(files), generation)
                        elif self._request is None:
                            note = "{} project files indexed".format(len(files))
                            if limited:
                                note += " / scan limit reached"
                            self._status = ProjectStatus("ready", note, len(files), generation)
                        if request:
                            self._result = result
                    self._active = False
                    self._condition.notify_all()
            except (OSError, ValueError, RuntimeError):
                files = ()
                with self._condition:
                    if not cancelled() and (request is None or request[4] == self._selection_serial):
                        self._status = ProjectStatus("error", "project folder unavailable; using scene sources", 0,
                                                     generation)
                    scanned_generation = generation
                    self._active = False
                    self._condition.notify_all()
