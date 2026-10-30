"""Read-only, bounded excerpts from explicitly selected files, with portable snapshots."""

from functools import lru_cache
import hashlib
from importlib import resources
from pathlib import Path
import unicodedata

from .domain import SourceDocument


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
    def __init__(self, ekza_root=None, bundled=False):
        # Relative to the checkout, so launching from another working directory works.
        default = Path(__file__).resolve().parents[2] / "ekza"
        self.root = Path(ekza_root).expanduser().resolve() if ekza_root is not None else default
        self.bundled = bundled

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
