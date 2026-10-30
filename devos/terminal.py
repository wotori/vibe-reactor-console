"""The curses boundary: colors and clipped drawing primitives."""

from dataclasses import dataclass

try:
    import curses
except ImportError:
    curses = None

from .session import PALETTES
from .shell import clip_cells


@dataclass(frozen=True)
class Rect:
    y: int
    x: int
    height: int
    width: int


class Pane:
    """All writes are local to the panel's interior, including tiny layouts."""

    def __init__(self, canvas, rect):
        self.canvas = canvas
        self.rect = rect
        self.width = max(0, rect.width - 4)
        self.height = max(0, rect.height - 2)

    def put(self, row, col, text, tone="text", bold=False, selected=False):
        if 0 <= row < self.height and 0 <= col < self.width:
            self.canvas.put(self.rect.y + 1 + row, self.rect.x + 2 + col,
                            text, tone, bold, self.width - col, selected)


class Canvas:
    def __init__(self, screen, color=True, background="terminal"):
        self.screen = screen
        self.color = color
        self.theme = None
        self.background = background
        self._configured = None
        self.colors = {}
        self._shell_colors = {}
        self._background_color = -1

    def configure(self, theme, background=None):
        background = self.background if background is None else background
        if self._configured == (theme, background):
            return
        self.theme = theme
        self.background = background
        self._configured = (theme, background)
        self._shell_colors = {}
        self.colors = {name: 0 for name in ("main", "accent", "hot", "muted", "text", "warn", "error")}
        if not self.color or curses is None or not curses.has_colors():
            return
        curses.start_color()
        palette = [*PALETTES[theme], 244, 252, 220, 203]
        if curses.COLORS < 256:
            primary = curses.COLOR_YELLOW if theme in ("ember", "amber") else (
                curses.COLOR_CYAN if theme == "ice" else curses.COLOR_GREEN)
            palette = [primary, curses.COLOR_CYAN, curses.COLOR_RED if theme == "ember" else curses.COLOR_MAGENTA,
                       curses.COLOR_WHITE, curses.COLOR_WHITE, curses.COLOR_YELLOW, curses.COLOR_RED]
        # Slot 0 follows the user's terminal palette and may be grey or tinted.
        # The 256-color cube starts with a dedicated RGB(0, 0, 0) at slot 16.
        background_color = 16 if curses.COLORS >= 256 else curses.COLOR_BLACK
        if background == "terminal":
            try:
                curses.use_default_colors()
                background_color = -1
            except curses.error:
                pass
        for index, (name, color) in enumerate(zip(self.colors, palette), 1):
            if index < curses.COLOR_PAIRS:
                curses.init_pair(index, color, background_color)
                self.colors[name] = curses.color_pair(index)
        self._background_color = background_color
        self.screen.attrset(self.colors["text"])
        self.screen.bkgd(" ", self.colors["text"])

    def put(self, y, x, text, tone="text", bold=False, limit=None, selected=False):
        rows, cols = self.screen.getmaxyx()
        if y < 0 or y >= rows or x < 0 or x >= cols - 1:
            return
        length = max(0, min(cols - x - 1, cols if limit is None else limit))
        if not length:
            return
        attr = self.colors.get(tone, 0)
        if curses is not None:
            attr |= curses.A_BOLD if bold else 0
            if selected:
                attr |= curses.A_BOLD | curses.A_UNDERLINE
        try:
            # Source/event text must never introduce cursor moves or terminal escapes.
            safe = "".join(char if char.isprintable() else " " for char in str(text))
            self.screen.addnstr(y, x, safe, length, attr)
        except Exception as exc:
            if curses is None or not isinstance(exc, curses.error):
                raise
            # A resize can occur between getmaxyx() and the actual write.

    def fill(self, rect):
        for row in range(rect.height):
            self.put(rect.y + row, rect.x, " " * max(0, rect.width))

    def cursor(self, position=None):
        if not self.color or curses is None or not hasattr(self.screen, "move"):
            return
        try:
            curses.curs_set(1 if position else 0)
            if position:
                self.screen.move(*position)
        except curses.error:
            pass

    def put_terminal(self, y, x, text, style, limit):
        rows, cols = self.screen.getmaxyx()
        if y < 0 or y >= rows or x < 0 or x >= cols - 1:
            return
        text = clip_cells(text, max(0, min(limit, cols - x - 1)))
        if not text:
            return
        attr = self.colors.get("text", 0)
        if self.color and curses is not None and curses.has_colors() and style.foreground is not None:
            color = style.foreground
            if isinstance(color, tuple):
                r, g, b = (max(0, min(255, component)) for component in color)
                levels = (0, 95, 135, 175, 215, 255)
                red, green, blue = (min(range(6), key=lambda i: abs(levels[i] - v)) for v in (r, g, b))
                cube = 16 + 36 * red + 6 * green + blue
                grey = max(0, min(23, round(((r + g + b) / 3 - 8) / 10)))
                cube_error = sum((v - levels[i]) ** 2 for v, i in zip((r, g, b), (red, green, blue)))
                grey_error = sum((v - (8 + grey * 10)) ** 2 for v in (r, g, b))
                color = 232 + grey if grey_error < cube_error else cube
            color = max(0, min(255, color))
            if curses.COLORS < 256:
                color = color % 8 if color < 16 else curses.COLOR_WHITE
            if color not in self._shell_colors:
                pair = 8 + len(self._shell_colors)
                if pair < curses.COLOR_PAIRS:
                    curses.init_pair(pair, color, self._background_color)
                    self._shell_colors[color] = curses.color_pair(pair)
            attr = self._shell_colors.get(color, attr)
        if curses is not None:
            attr |= curses.A_BOLD if style.bold else 0
            attr |= curses.A_UNDERLINE if style.underline else 0
        try:
            self.screen.addnstr(y, x, text, len(text), attr)
        except Exception as exc:
            if curses is None or not isinstance(exc, curses.error):
                raise

    def panel(self, rect, title, detail=""):
        y, x, height, width = rect.y, rect.x, rect.height, rect.width
        if height >= 3 and width >= 5:
            self.put(y, x, "+" + "-" * (width - 2) + "+", "muted")
            self.put(y, x + 2, " " + title + " ", "accent", True, width - 4)
            if detail and width > len(title) + len(detail) + 9:
                self.put(y, x + width - len(detail) - 3, " " + detail + " ", "muted")
            for row in range(y + 1, y + height - 1):
                self.put(row, x, "|", "muted")
                self.put(row, x + width - 1, "|", "muted")
            self.put(y + height - 1, x, "+" + "-" * (width - 2) + "+", "muted")
        return Pane(self, rect)
