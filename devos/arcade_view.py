"""Read-only ASCII Arcade compositions over the games' frozen snapshots.

The drawing code never handles input or advances a game. Small terminals use
vertical cell pairs so every logical row stays visible after a resize.
"""

import math
import textwrap

from .terminal import Rect


ART = {
    "snake": (
        "       .----.       ",
        "   .---'    `--O    ",
        "   `---.           *",
        "       `--------'   ",
    ),
    "blocks": (
        "       []          ",
        "     [][][]        ",
        "   []    [][]      ",
        " [][][][][][][][]  ",
    ),
    "runner": (
        "      o         |>>",
        "     /|\\    *   |  ",
        " ____/ \\___     |  ",
        "#########  ####### ",
    ),
}

DESCRIPTIONS = {
    "snake": "Eat bytes. Dodge your own stack.",
    "blocks": "Rotate, drop, clear the memory.",
    "runner": "Jump gaps. Collect bytes. Reach the flag.",
}

SHORT_CONTROLS = {
    "snake": "Arrows/WASD move | SPACE/P pause",
    "blocks": "Arrows move/rotate | SPACE drop | P pause",
    "runner": "A/D move | SPACE/W jump | P pause",
}


def clean(value, limit=512):
    return "".join(char if 32 <= ord(char) < 127 else "?"
                   for char in str(value))[:limit]


def centered(canvas, row, value, cols, tone="main", bold=False):
    value = clean(value, max(0, cols - 4))
    canvas.put(row, max(2, (cols - len(value)) // 2), value, tone, bold,
               max(0, cols - 4))


def game_specs():
    # Lazy import keeps Session's registry construction independent of curses.
    from .games import GAME_SPECS
    return GAME_SPECS


def draw_arcade(renderer, session, rows, cols):
    """Draw the hub or a chosen game with no writes outside the viewport."""
    canvas = renderer.canvas
    frame = session.arcade_game.snapshot() if session.arcade_game is not None else None
    if frame is None:
        draw_lobby(canvas, session, rows, cols)
    else:
        draw_game(canvas, session, frame, rows, cols)


def draw_lobby(canvas, session, rows, cols):
    specs = game_specs()
    choice = session.arcade_choice % len(specs)
    canvas.put(0, 2, "DEV/OS / ARCADE", "main", True, cols - 4)
    owner = "INPUT: DEVOS / LOCAL GAMES"
    if cols >= 80:
        canvas.put(0, max(22, cols - len(owner) - 3), owner, "accent", limit=cols - 4)
    else:
        canvas.put(1, 2, owner, "accent", limit=cols - 4)
    if cols >= 80:
        canvas.put(1, 2, "1 COCKPIT  2 LAB  3 ARENA  4 SITE  5 BODY  6 ATLAS  7 REPLAY  8 ARCADE",
                   "muted", limit=cols - 4)
    canvas.put(2, 2, "A SMALL BREAK INSIDE THE MACHINE", "hot", True, cols - 4)

    if cols >= 100 and rows >= 24:
        width = (cols - 8) // 3
        height = min(23, rows - 7)
        top = max(4, (rows - height) // 2)
        for index, spec in enumerate(specs):
            selected = index == choice
            panel = canvas.panel(Rect(top, 2 + index * (width + 2), height, width),
                                 ("> " if selected else "  ") + clean(spec.title).upper(),
                                 "SELECTED" if selected else "LOCAL")
            panel.put(1, 0, "0{} / {}".format(index + 1, spec.title),
                      "main" if selected else "text", selected, selected)
            for offset, line in enumerate(ART[spec.id]):
                panel.put(4 + offset, max(0, (panel.width - len(line)) // 2), line,
                          "hot" if selected else "muted", selected)
            panel.put(10, 0, DESCRIPTIONS[spec.id], "accent")
            panel.put(12, 0, "SAVED IN THIS SESSION" if spec.id in session.arcade_games else "READY WHEN YOU ARE", "muted")
            panel.put(panel.height - 2, 0, "[ ENTER / PLAY ]" if selected else "ARROWS TO SELECT",
                      "main" if selected else "muted", selected)
    else:
        # Three compact cards remain discoverable at the minimum 48 x 16 size.
        step = max(3, min(5, (rows - 6) // len(specs)))
        for index, spec in enumerate(specs):
            row = 4 + index * step
            selected = index == choice
            marker = ">" if selected else " "
            state = "RESUME" if spec.id in session.arcade_games else "PLAY"
            canvas.put(row, 2, "{} 0{} / {}  [{}]".format(marker, index + 1, spec.title.upper(), state),
                       "main" if selected else "muted", selected, cols - 4, selected)
            canvas.put(row + 1, 5, DESCRIPTIONS[spec.id], "accent" if selected else "muted", limit=cols - 7)
            if step > 3:
                canvas.put(row + 2, 5, SHORT_CONTROLS[spec.id], "muted", limit=cols - 7)
    canvas.put(rows - 2, 2, "ARROWS / J K / TAB select | ENTER play", "accent", limit=cols - 4)
    canvas.put(rows - 1, 2, "ESC cockpit | Q exit? | Games pause while away", "muted", limit=cols - 4)


def _project_cells(frame, row_group):
    """Merge vertical pairs without losing player, food or goal symbols."""
    projected = {}
    # Priority symbols remain readable over terrain at compact resolutions.
    priorities = {"@": 9, "O": 9, "o": 8, "*": 8, "$": 8, ">": 7,
                  "<": 7, "!": 7, "P": 9, "A": 9, "^": 7}
    buckets = {}
    for cell in frame.cells:
        if not 0 <= cell.x < frame.width or not 0 <= cell.y < frame.height:
            continue
        char = clean(cell.char, 1)
        if not char or char == " ":
            continue
        key = (cell.x, cell.y // row_group)
        buckets.setdefault(key, []).append(cell)
    for key, cells in buckets.items():
        chosen = max(cells, key=lambda item: (priorities.get(item.char, 0), item.bold,
                                             item.tone not in ("muted", "text")))
        char = clean(chosen.char, 1)
        if row_group == 2 and len(cells) == 1 and char in ("#", "=", "_", "|"):
            char = "'" if chosen.y % 2 == 0 else ","
        projected[key] = (char, chosen.tone, chosen.bold)
    return projected


def draw_game(canvas, session, frame, rows, cols):
    spec = next(item for item in game_specs() if item.id == session.arcade_game_id)
    title = "DEV/OS / ARCADE / " + spec.title.upper()
    canvas.put(0, 2, title, "main", True, cols - 4)
    status = clean(frame.status).upper()
    lives = " / LIVES {}".format(frame.lives) if spec.id == "runner" else ""
    canvas.put(1, 2, "{} / SCORE {} / LEVEL {}{}".format(status, frame.score, frame.level, lives),
               "hot" if status in ("GAME OVER", "GAME_OVER", "WON", "VICTORY") else "accent",
               True, cols - 4)
    details = " / ".join(clean(value) for value in frame.details)
    canvas.put(2, 2, details or "LOCAL GAME / INPUT: DEVOS", "muted", limit=cols - 4)

    # The board is always complete. An ASCII glyph can represent two logical
    # vertical cells when the terminal is short; wider layouts double columns.
    top = 3
    available_height = max(1, rows - top - 3)
    row_group = max(1, math.ceil(frame.height / available_height))
    board_rows = math.ceil(frame.height / row_group)
    side_width = 34 if cols >= 120 and rows >= 24 else 0
    board_area = max(1, cols - 4 - side_width)
    col_scale = 2 if frame.width * 2 <= board_area - 2 else 1
    # Engine boards fit the minimum width. This final compression also makes
    # arbitrary future larger boards safe without discarding their right edge.
    col_group = max(1, math.ceil(frame.width / max(1, board_area - 2)))
    board_cols = math.ceil(frame.width / col_group) * col_scale
    x = max(1, (cols - side_width - board_cols - 2) // 2)
    canvas.put(top, x, "+" + "-" * board_cols + "+", "muted")
    for row in range(board_rows):
        canvas.put(top + row + 1, x, "|", "muted")
        canvas.put(top + row + 1, x + board_cols + 1, "|", "muted")
    canvas.put(top + board_rows + 1, x, "+" + "-" * board_cols + "+", "muted")
    for (cell_x, cell_y), (char, tone, bold) in _project_cells(frame, row_group).items():
        canvas.put(top + cell_y + 1, x + 1 + (cell_x // col_group) * col_scale,
                   char * col_scale, tone, bold, col_scale)

    overlay = None
    if status == "READY":
        overlay = "ENTER TO START"
    elif status == "PAUSED":
        overlay = "PAUSED / ENTER RESUME"
    elif status in ("GAME OVER", "GAME_OVER", "OVER", "LOST"):
        overlay = "GAME OVER / R RESTART"
    elif status in ("WON", "WIN", "VICTORY", "COMPLETE"):
        overlay = "LEVEL CLEAR / R REPLAY"
    if overlay and board_cols >= 16 and board_rows >= 3:
        if board_cols < 26:
            overlay = {"READY": "ENTER START", "PAUSED": "PAUSED / ENTER"}.get(status,
                       "GAME OVER / R" if "OVER" in status or status in ("OVER", "LOST") else "LEVEL CLEAR / R")
        overlay = overlay[:board_cols - 2]
        canvas.put(top + 1 + board_rows // 2, x + 1 + max(0, (board_cols - len(overlay)) // 2),
                   overlay, "hot", True, board_cols)
    if side_width:
        left = cols - side_width + 1
        canvas.put(5, left, "HOW TO PLAY", "main", True, side_width - 3)
        controls = frame.controls if isinstance(frame.controls, (tuple, list)) else clean(frame.controls).replace("   ", "|").split("|")
        lines = [line for value in controls for line in textwrap.wrap(clean(value).strip(), side_width - 3)]
        for row, line in enumerate(lines[:max(0, min(7, rows - 14))]):
            canvas.put(7 + row, left, clean(line), "accent", limit=side_width - 3)
        canvas.put(min(rows - 5, 15), left, "R restart / ESC lobby", "muted", limit=side_width - 3)
        canvas.put(min(rows - 4, 17), left, "Your terminals keep running.", "muted", limit=side_width - 3)
        if rows >= 32:
            canvas.put(20, left, "GAME DETAILS", "main", True, side_width - 3)
            for index, value in enumerate(frame.details[:max(0, rows - 25)]):
                canvas.put(22 + index, left, clean(value), "accent", limit=side_width - 3)

    if rows >= 24:
        canvas.put(rows - 2, 2, SHORT_CONTROLS[spec.id] + " | ENTER start/resume | R restart",
                   "accent", limit=cols - 4)
        footer = "ESC lobby / 1-8 screens / Q exit? / LOCAL GAMES / INPUT: DEVOS"
    else:
        short = {"snake": "Arrows move SPACE pause", "blocks": "Arrows/SPACE drop P pause", "runner": "AD/SPACE jump P pause"}[spec.id]
        footer = short + " | R reset ESC hub Q?"
    canvas.put(rows - 1, 1, footer, "muted", limit=cols - 2)
