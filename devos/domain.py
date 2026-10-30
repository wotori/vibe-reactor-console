"""Immutable scene definitions; no terminal or filesystem side effects."""

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class Snippet:
    path: str
    language: str
    description: str
    resource: str
    source_path: str = ""
    anchor: str = ""
    first_line: int = 1
    max_lines: int = 56


@dataclass(frozen=True)
class SourceDocument:
    lines: Tuple[str, ...]
    first_line: int
    kind: str
    digest: str
    note: str = ""

    @property
    def last_line(self):
        return self.first_line + len(self.lines) - 1


@dataclass(frozen=True)
class Mode:
    id: str
    title: str
    subtitle: str
    description: str
    palette: str
    geometry: str
    workspace: str
    hardware: bool
    snippets: Tuple[Snippet, ...]
    phases: Tuple[str, ...]
    phase_messages: Tuple[str, ...]
    events: Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class LogEntry:
    timestamp: str
    kind: str
    message: str
