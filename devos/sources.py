"""Read-only, bounded excerpts from explicitly selected files, with portable snapshots."""

from functools import lru_cache
import hashlib
from importlib import resources
from pathlib import Path
import unicodedata

from .domain import SourceDocument
from .project_sources import ProjectSourceWorker, ProjectStatus, normalized_root


MAX_SOURCE_BYTES = 512 * 1024


def display_lines(source):
    """Keep line numbers and indentation; escape terminal control characters."""
    result = []
    for line in source.splitlines():
        line = line.expandtabs(4)
        result.append("".join("\\x{:02x}".format(ord(char)) if unicodedata.category(char) in ("Cc", "Cf")
                              else char for char in line))
    return tuple(result)


def document(lines, first_line, kind, note=""):
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:8]
    return SourceDocument(lines, first_line, kind, digest, note)


@lru_cache(maxsize=None)
def packaged_lines(snippet):
    source = resources.files("devos").joinpath("snippets").joinpath(snippet.resource).read_text(encoding="utf-8")
    return display_lines(source.rstrip())


class SourceProvider:
    def __init__(self, ekza_root=None, bundled=False, project_root=None):
        # Relative to the checkout, so launching from another working directory works.
        default = Path(__file__).resolve().parents[2] / "ekza"
        self.root = Path(ekza_root).expanduser().resolve() if ekza_root is not None else default
        self.bundled = bundled
        self.project_root = normalized_root(project_root)
        self.project_generation = 0
        self._project = ProjectSourceWorker(self.project_root) if self.project_active else None

    @property
    def project_active(self):
        return bool(self.project_root and not self.bundled)

    @property
    def project_status(self):
        if self._project is not None:
            return self._project.status
        return ProjectStatus("inactive", "bundled sources selected" if self.bundled else "default scene sources")

    def set_project_root(self, path):
        """A control action: changing the path schedules work, never reads files."""
        self.project_root = normalized_root(path)
        self.bundled = False
        self.project_generation += 1
        if self._project is not None:
            self._project.set_root(self.project_root)
        elif self.project_root:
            self._project = ProjectSourceWorker(self.project_root)

    def poll_project(self, rng, current=None, refresh=False):
        """Return a prepared ProjectSelection or None, without filesystem I/O.

        Call while waiting for a next/reloaded document, then stop after receiving
        a selection. A worker prepares only one outstanding selection at a time.
        """
        if not self.project_active or self._project is None:
            return None
        return self._project.poll(rng, current, refresh)

    def request_project(self, rng, current=None, refresh=False):
        """Explicit next/reload; retries errors and supersedes older requests."""
        if self.project_active and self._project is not None:
            self._project.request(rng, current, refresh)

    def close(self):
        if self._project is not None:
            self._project.close()

    def load(self, snippet):
        if not snippet.source_path:
            return document(packaged_lines(snippet), 1, "BUNDLED")
        if self.bundled:
            return self.snapshot(snippet, "bundled sources selected")
        try:
            relative = Path(snippet.source_path)
            if relative.is_absolute() or ".." in relative.parts:
                return self.snapshot(snippet, "source path rejected")
            path = (self.root / relative).resolve()
            path.relative_to(self.root.resolve())
            if not path.is_file():
                return self.snapshot(snippet, "local source unavailable")
            with path.open("rb") as source:
                raw = source.read(MAX_SOURCE_BYTES + 1)
            if len(raw) > MAX_SOURCE_BYTES:
                return self.snapshot(snippet, "source exceeds excerpt limit")
            lines = display_lines(raw.decode("utf-8"))
            hit = next((i for i, line in enumerate(lines) if snippet.anchor in line), None)
            if hit is None:
                return self.snapshot(snippet, "source anchor changed")
            start = max(0, hit - 2)
            excerpt = lines[start:start + snippet.max_lines]
            if not excerpt or not any(line.strip() for line in excerpt):
                return self.snapshot(snippet, "local excerpt empty")
            return document(excerpt, start + 1, "LOCAL")
        except (OSError, ValueError, RuntimeError):
            return self.snapshot(snippet, "local source unreadable")

    @staticmethod
    def snapshot(snippet, note):
        return document(packaged_lines(snippet), snippet.first_line, "SNAPSHOT", note)
