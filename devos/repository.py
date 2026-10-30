"""Bounded, read-only repository facts. All functions belong on a worker thread."""

from dataclasses import dataclass
import os
from pathlib import Path
import selectors
import subprocess
import time
from typing import Optional, Tuple


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    output: str
    truncated: bool = False
    timed_out: bool = False


def run_bounded(argv, cwd=None, timeout=2.0, limit=131072, env=None):
    """Drain a child without an unbounded communicate() buffer or a shell."""
    process = subprocess.Popen(tuple(argv), cwd=cwd, env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    parts, size, truncated, timed_out = [], 0, False, False
    deadline = time.monotonic() + max(0.05, timeout)
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                process.kill()
                break
            for key, _ in selector.select(min(remaining, 0.05)):
                chunk = os.read(key.fileobj.fileno(), 16384)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                allowed = max(0, limit - size)
                if allowed:
                    parts.append(chunk[:allowed])
                    size += min(len(chunk), allowed)
                if len(chunk) > allowed:
                    truncated = True
                    process.kill()
                    break
            if truncated:
                break
        try:
            code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            code = process.wait()
        return CommandResult(code, b"".join(parts).decode("utf-8", "replace"),
                             truncated, timed_out)
    finally:
        selector.close()
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()


@dataclass(frozen=True)
class RepoFile:
    path: str
    status: str = "  "
    previous_path: Optional[str] = None

    @property
    def changed(self):
        return self.status != "  "


@dataclass(frozen=True)
class RepoSnapshot:
    root: str
    branch: str = "--"
    revision: str = "--"
    files: Tuple[RepoFile, ...] = ()
    changed_files: int = 0
    quality: str = "unavailable"
    error: str = ""
    truncated: bool = False
    source_id: str = "git"
    mode: str = "live"


def _git(root, *args, timeout=2.0, limit=131072):
    environment = os.environ.copy()
    environment.update(GIT_OPTIONAL_LOCKS="0", GIT_PAGER="cat", LC_ALL="C")
    return run_bounded(("git", "--no-optional-locks", "-c", "core.fsmonitor=false",
                        "-c", "core.hooksPath=/dev/null", "-c", "core.quotePath=false")
                       + args, cwd=str(root), timeout=timeout, limit=limit,
                       env=environment)


def inspect_repository(root, max_files=1024, timeout=2.0):
    """Inspect tracked/untracked files; never fetch, update an index, or write."""
    root = Path(root).expanduser().resolve()
    try:
        check = _git(root, "rev-parse", "--show-toplevel", timeout=timeout)
        if check.returncode or check.timed_out:
            return RepoSnapshot(str(root), error="Not an accessible Git repository")
        actual_root = Path(check.output.strip()).resolve()
        branch = _git(actual_root, "symbolic-ref", "--short", "-q", "HEAD",
                      timeout=timeout, limit=4096)
        revision = _git(actual_root, "rev-parse", "--short", "HEAD",
                        timeout=timeout, limit=4096)
        status = _git(actual_root, "status", "--porcelain=v1", "-z",
                      "--untracked-files=all", timeout=timeout)
        listing = _git(actual_root, "ls-files", "--cached", "--others",
                       "--exclude-standard", "-z", timeout=timeout, limit=262144)
        failed = status.returncode or listing.returncode
        if failed:
            reason = "Git inspection timed out" if status.timed_out or listing.timed_out else "Git inspection failed"
            return RepoSnapshot(str(actual_root), error=reason)
        changed, chunks, index = {}, status.output.split("\0"), 0
        while index < len(chunks):
            chunk = chunks[index]
            index += 1
            if len(chunk) < 4:
                continue
            state, path = chunk[:2], chunk[3:]
            previous = None
            if "R" in state or "C" in state:
                if index < len(chunks):
                    previous = chunks[index]
                    index += 1
            changed[path] = RepoFile(path, state, previous)
        paths = set(path for path in listing.output.split("\0") if path)
        paths.update(changed)
        ordered = sorted(paths)
        truncated = status.truncated or listing.truncated or len(ordered) > max_files
        files = tuple(changed.get(path, RepoFile(path)) for path in ordered[:max_files])
        return RepoSnapshot(str(actual_root), branch.output.strip() or "detached",
                            revision.output.strip() if not revision.returncode else "unborn",
                            files, len(changed), "ok", "", truncated)
    except (OSError, ValueError) as error:
        return RepoSnapshot(str(root), error=str(error)[:160])


def read_diff(root, path, limit=32768, timeout=2.0):
    """Read staged + working changes, or a bounded untracked-file preview."""
    root = Path(root).expanduser().resolve()
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or "\0" in str(path):
        raise ValueError("Diff path must stay inside the repository")
    try:
        resolved = (root / candidate).resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        raise ValueError("Diff path must stay inside the repository")
    result = _git(root, "diff", "--no-ext-diff", "--no-textconv", "--", str(candidate),
                  timeout=timeout, limit=limit)
    staged = _git(root, "diff", "--cached", "--no-ext-diff", "--no-textconv", "--",
                  str(candidate), timeout=timeout, limit=limit)
    text = result.output + staged.output
    if not text and resolved.is_file():
        with resolved.open("rb") as handle:
            raw = handle.read(limit + 1)
        if b"\0" in raw:
            text = "[binary file]"
        else:
            text = raw.decode("utf-8", "replace")
    truncated = result.truncated or staged.truncated or len(text) > limit
    text = text[:limit]
    # Repository contents can contain terminal controls; previews are plain text.
    text = "".join(ch if ch in "\n\t" or ord(ch) >= 32 and ord(ch) != 127 else "?" for ch in text)
    return text + ("\n[preview truncated]" if truncated else "")
