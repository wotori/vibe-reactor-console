"""Immutable game frames shared by pure engines and terminal presentation."""

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class GameCell:
    x: int
    y: int
    char: str
    tone: str = "main"
    bold: bool = False


@dataclass(frozen=True)
class GameFrame:
    width: int
    height: int
    cells: Tuple[GameCell, ...]
    score: int = 0
    level: int = 1
    status: str = "READY"
    details: Tuple[str, ...] = ()
    controls: str = ""
    lives: int = 0
