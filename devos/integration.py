"""Explicit three-way integration into a new, reviewable candidate directory.

This is a conservative file merge, not a Git merge driver: divergent bytes keep
the base and produce visible conflict artifacts. No original workspace changes,
commands, commits, model calls or deployment are involved.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import unicodedata

from .agents import snapshot_manifest


MAX_FILE_BYTES = 1024 * 1024
EXCLUDED = frozenset((".git", ".env", ".codex", ".claude", "node_modules", ".venv",
                      ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", "build", "dist", "target",
                      "__pycache__", ".devos", ".npmrc", ".pypirc", "credentials",
                      "credentials.json", "secrets", "secrets.json", "id_rsa", "id_ed25519"))
SECRET_SUFFIXES = frozenset((".pem", ".key", ".p12", ".pfx", ".keystore"))


@dataclass(frozen=True)
class IntegrationResult:
    root: str
    conflicts: tuple
    applied: tuple
    report_path: str


def excluded(relative):
    parts = PurePosixPath(str(relative).replace(os.sep, "/")).parts
    return any(part.casefold() in EXCLUDED or part.casefold().startswith(".env") or part.endswith(".egg-info")
               for part in parts) or Path(parts[-1]).suffix.casefold() in SECRET_SUFFIXES


def source_root(value):
    path = Path(value).expanduser()
    if path.is_symlink():
        raise ValueError("Integration roots must not be symbolic links")
    path = path.resolve(strict=True)
    if not path.is_dir():
        raise ValueError("Integration roots must be directories")
    return path


def preflight(root):
    """Reject special files before snapshot_manifest can read a FIFO/device."""
    for directory, directories, files in os.walk(str(root), followlinks=False):
        kept = []
        for name in directories:
            path = Path(directory) / name
            if excluded(path.relative_to(root)):
                continue
            if path.is_symlink():
                raise ValueError("Integration does not follow symbolic links: " + str(path.relative_to(root)))
            kept.append(name)
        directories[:] = kept
        for name in files:
            path = Path(directory) / name
            if excluded(path.relative_to(root)):
                continue
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode):
                raise ValueError("Integration accepts regular files only: " + str(path.relative_to(root)))


def read_verified(root, relative, expected):
    """Open every path component without following links, then verify the hash."""
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or ".." in parts:
        raise ValueError("Invalid snapshot path")
    directory = os.open(str(root), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    handle = None
    try:
        for part in parts[:-1]:
            following = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
            os.close(directory)
            directory = following
        handle = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), dir_fd=directory)
        info = os.fstat(handle)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise ValueError("Source changed or is not a bounded regular file: " + relative)
        chunks, size = [], 0
        while size <= MAX_FILE_BYTES:
            chunk = os.read(handle, min(65536, MAX_FILE_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        content = b"".join(chunks)
        if size > MAX_FILE_BYTES or hashlib.sha256(content).hexdigest() != expected:
            raise ValueError("Source changed during integration: " + relative)
        return content, info.st_mode & 0o777
    finally:
        if handle is not None:
            os.close(handle)
        os.close(directory)


def capture(root):
    preflight(root)
    manifest, digest = snapshot_manifest(root)
    entries = {relative: fingerprint for relative, fingerprint in manifest if not excluded(relative)}
    content = {relative: read_verified(root, relative, fingerprint) for relative, fingerprint in entries.items()}
    return entries, content, digest


def under(path, parent):
    return path == parent or path.startswith(parent + "/")


def write_file(root, relative, content):
    target = root / relative
    if target.is_dir():
        # Only our newly created empty directory can be replaced. A nonempty
        # directory indicates an unresolved planning error and is never removed.
        target.rmdir()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content[0])
    target.chmod(content[1])


def integrate_workspaces(base, left, right, destination):
    """Build a new candidate; return relative paths needing user resolution.

    All three source captures complete before creating the destination. Each
    capture uses the same bounded workspace manifest policy as mission snapshots,
    with additional credential-name exclusions and no symbolic/special files.
    """
    roots = tuple(source_root(value) for value in (base, left, right))
    candidate = Path(destination).expanduser()
    if candidate.exists() or candidate.is_symlink():
        raise ValueError("Integration destination must not already exist")
    candidate = candidate.resolve()
    if any(candidate == root or root in candidate.parents for root in roots):
        raise ValueError("Integration destination must be outside all source roots")
    captures = tuple(capture(root) for root in roots)
    base_map, left_map, right_map = (item[0] for item in captures)
    contents = tuple(item[1] for item in captures)
    decisions, reasons = {}, {}
    all_paths = sorted(set(base_map) | set(left_map) | set(right_map))
    for relative in all_paths:
        before, first, second = base_map.get(relative), left_map.get(relative), right_map.get(relative)
        if first == second:
            decisions[relative] = (first, 1)
        elif first == before:
            decisions[relative] = (second, 2)
        elif second == before:
            decisions[relative] = (first, 1)
        else:
            reasons[relative] = "divergent bytes or delete-versus-edit"
            decisions[relative] = (before, 0)
    # A directory can be replaced by a file on one side while the other side
    # adds a child. These independent path decisions conflict as a subtree.
    final_paths = {relative for relative, (fingerprint, _) in decisions.items() if fingerprint is not None}
    for relative in sorted(final_paths):
        parts = relative.split("/")
        for depth in range(1, len(parts)):
            parent = "/".join(parts[:depth])
            if parent in final_paths:
                reasons[parent] = "file-versus-directory collision"
    # A candidate can later be opened on a case-insensitive filesystem. Never
    # silently overwrite Foo with foo, including a directory/file alias.
    aliases = {}
    for relative in sorted(final_paths):
        normalized = unicodedata.normalize("NFC", relative).casefold()
        aliases.setdefault(normalized, []).append(relative)
    for normalized, paths in aliases.items():
        if len(paths) > 1:
            for relative in paths:
                reasons[relative] = "case or Unicode filename collision"
        parts = normalized.split("/")
        for depth in range(1, len(parts)):
            parent = "/".join(parts[:depth])
            if parent in aliases:
                for relative in paths + aliases[parent]:
                    reasons[relative] = "file-versus-directory collision"
    conflict_roots = []
    for relative in sorted(reasons, key=lambda path: (len(path.split("/")), path)):
        if not any(under(relative, parent) for parent in conflict_roots):
            conflict_roots.append(relative)
    conflict_roots = tuple(sorted(conflict_roots))
    changed = tuple(relative for relative, (fingerprint, _) in sorted(decisions.items())
                    if fingerprint != base_map.get(relative)
                    and not any(under(relative, parent) for parent in conflict_roots))
    # mkdir is the exclusive reservation; no existing destination is overwritten
    # even if another process creates it between validation and this point.
    candidate.mkdir(parents=True, exist_ok=False)
    for relative, content in sorted(contents[0].items()):
        write_file(candidate, relative, content)
    for relative in changed:
        if decisions[relative][0] is None:
            target = candidate / relative
            if target.is_file():
                target.unlink()
    # Removing files can leave several nested empty directories. Remove only
    # those empty directories in this new candidate before a file replaces one.
    for directory, _, _ in os.walk(str(candidate), topdown=False):
        path = Path(directory)
        if path != candidate and not any(path.iterdir()):
            path.rmdir()
    for relative in changed:
        fingerprint, source = decisions[relative]
        if fingerprint is not None:
            write_file(candidate, relative, contents[source][relative])
    report_directory = candidate / ".devos"
    report_directory.mkdir(exist_ok=True)
    conflict_reports = []
    for relative in conflict_roots:
        versions = {}
        for side, index in (("left", 1), ("right", 2)):
            paths = tuple(path for path in sorted(contents[index]) if under(path, relative))
            for path in paths:
                write_file(report_directory / "integration-conflicts" / side, path, contents[index][path])
            versions[side] = {"files": paths, "deleted": not bool(paths),
                              "directory": ".devos/integration-conflicts/" + side}
        conflict_reports.append({"path": relative, "reason": reasons[relative], "versions": versions})
    report_path = report_directory / "integration-report.json"
    report = {"schema_version": 1, "status": "needs-user" if conflict_roots else "ready-for-review",
              "strategy": "conservative-file-three-way", "root": str(candidate),
              "sources": {name: {"root": str(root), "manifest_sha256": item[2]}
                          for name, root, item in zip(("base", "left", "right"), roots, captures)},
              "applied": changed, "conflicts": conflict_reports,
              "policy": {"max_files_per_source": 2000, "max_bytes_per_source": 32 * 1024 * 1024,
                         "max_file_bytes": MAX_FILE_BYTES, "symlinks": "rejected", "credentials": "excluded"},
              "note": "Review this candidate and run your checks. Original roots are unchanged. No commit or deployment occurred."}
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return IntegrationResult(str(candidate), conflict_roots, changed, str(report_path))
