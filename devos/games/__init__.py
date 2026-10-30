"""A passive local arcade registry; selecting a game explicitly creates it."""

from dataclasses import dataclass


@dataclass(frozen=True)
class GameSpec:
    id: str
    title: str
    description: str


GAME_SPECS = (
    GameSpec("snake", "Snake", "Collect sparks, grow your snake and avoid the walls."),
    GameSpec("blocks", "Falling Blocks", "Rotate seven block shapes, clear rows and climb levels."),
    GameSpec("runner", "Byte Runner", "Run, jump, collect coins and reach the flag in an original ASCII world."),
)
GAME_BY_ID = {item.id: item for item in GAME_SPECS}


def create_game(identifier, seed=42):
    if identifier == "snake":
        from .snake import SnakeGame
        return SnakeGame(seed=seed)
    if identifier == "blocks":
        from .blocks import BlocksGame
        return BlocksGame(seed=seed)
    if identifier == "runner":
        from .platformer import PlatformerGame
        return PlatformerGame(seed=seed)
    raise ValueError("Unknown arcade game: " + str(identifier))
