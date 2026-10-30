"""Responsive panel layout, kept independent of the terminal renderer."""

from dataclasses import dataclass
from typing import Optional

from .terminal import Rect


@dataclass(frozen=True)
class Layout:
    header: int
    editor: Rect
    geometry: Optional[Rect]
    diagnostics: Optional[Rect]
    logs: Rect
    memory: Optional[Rect]
    scope: Optional[Rect] = None


def arrange(rows, cols, hardware=False, geometry_focus=False, scope=False):
    header = 5 if rows >= 34 and cols >= 100 else 3
    bottom = max(6, min(12, rows // 4))
    if geometry_focus and rows >= 34:
        bottom = max(6, min(10, rows // 5))
    body = rows - header - bottom - 1
    width = cols - 2
    if cols < 80:
        return Layout(header, Rect(header, 1, body, width), None, None,
                      Rect(header + body, 1, bottom, width), None)
    right = max(28, min(64, int(width * 0.38)))
    left = width - right - 1
    right_x = left + 2
    expanded = body >= 21 and right >= 34
    diagnostics_h = 10 if expanded else 0
    geometry = Rect(header, right_x, body - diagnostics_h, right)
    diagnostics = Rect(header + body - diagnostics_h, right_x, diagnostics_h, right) if expanded else None
    memory = Rect(header + body, right_x, bottom, right) if hardware and cols >= 106 else None
    logs = Rect(header + body, 1, bottom, left if memory else width)
    oscilloscope = None
    if scope:
        if memory:
            log_width = max(44, int(width * .45))
            remainder = width - log_width - 2
            memory_width = remainder // 2
            logs = Rect(header + body, 1, bottom, log_width)
            memory = Rect(header + body, log_width + 2, bottom, memory_width)
            oscilloscope = Rect(header + body, log_width + memory_width + 3, bottom,
                                remainder - memory_width)
        else:
            logs = Rect(header + body, 1, bottom, left)
            oscilloscope = Rect(header + body, right_x, bottom, right)
    return Layout(header, Rect(header, 1, body, left), geometry, diagnostics, logs, memory, oscilloscope)
