"""Screen descriptors and the shared layout contract for rendering and PTYs.

Imports of the curses boundary are deliberately lazy: Session can use this
registry while the terminal palette is still being initialized.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ScreenSpec:
    id: str
    title: str
    description: str
    panels: tuple
    render: object


def lazy_render(name):
    def render(renderer, session, rows, cols):
        from . import screen_views
        return getattr(screen_views, name)(renderer, session, rows, cols)
    return render


SCREENS = (
    ScreenSpec("cockpit", "COCKPIT", "Source or real terminal with the visual flight instruments.",
               ("source", "visual", "diagnostics", "events", "scope"), lazy_render("cockpit")),
    ScreenSpec("visual-lab", "VISUAL LAB", "Patch a measured signal into a pure visual parameter.",
               ("bindings", "preview", "signals"), lazy_render("visual_lab")),
    ScreenSpec("arena", "AGENT ARENA", "Two independent agents, explicit handoffs and bounded missions.",
               ("terminal-left", "terminal-right", "handoffs", "arena-status"), lazy_render("arena")),
    ScreenSpec("site", "SITE OBSERVATORY", "Aggregate audience intensity and page constellations.",
               ("audience", "site-metrics", "site-events"), lazy_render("site")),
    ScreenSpec("body", "BODY SIGNAL", "Measured BPM and RR intervals beside an artistic pulse.",
               ("pulse", "body-metrics", "rr"), lazy_render("body")),
    ScreenSpec("atlas", "EKZA ATLAS", "Asset protocol fixture or the actual workspace Git tree.",
               ("atlas-graph", "atlas-details", "atlas-events"), lazy_render("atlas")),
    ScreenSpec("replay", "REPLAY STUDIO", "Recorded events, a visible playhead and no live actions.",
               ("replay-stage", "replay-timeline", "replay-events"), lazy_render("replay")),
)
SCREEN_BY_ID = {screen.id: screen for screen in SCREENS}


def screen_layout(screen_id, rows, cols, focused_panel=None, zoomed=False, scope=True):
    """Return only visible panels, identically for UI drawing and child resize.

    A hidden panel's process and state remain alive. On compact screens the
    focused panel wins; choosing the other Arena terminal exposes its own PTY.
    """
    from .terminal import Rect
    spec = SCREEN_BY_ID[screen_id]
    if rows < 16 or cols < 48:
        return ()
    height, width = rows - 4, cols - 2
    full = Rect(3, 1, height, width)
    focused_panel = focused_panel if focused_panel in spec.panels else None
    if zoomed:
        return ((focused_panel or spec.panels[0], full),)
    if screen_id == "cockpit":
        from .layout import arrange
        layout = arrange(rows, cols, geometry_focus=True, scope=scope)
        return tuple((name, rect) for name, rect in (
            ("source", layout.editor), ("visual", layout.geometry),
            ("diagnostics", layout.diagnostics), ("events", layout.logs),
            ("scope", layout.scope)) if rect is not None)
    bottom_h = max(5, min(10, rows // 5))
    main_h = height - bottom_h
    if screen_id == "arena":
        if cols >= 160 and rows >= 36:
            side = 36 if cols >= 200 else 0
            space = width - (side + 1 if side else 0)
            left = (space - 1) // 2
            panels = [("terminal-left", Rect(3, 1, main_h, left)),
                      ("terminal-right", Rect(3, left + 2, main_h, space - left - 1)),
                      ("handoffs", Rect(3 + main_h, 1, bottom_h, width))]
            if side:
                panels.append(("arena-status", Rect(3, space + 2, main_h, side)))
            return tuple(panels)
        selected = focused_panel or "terminal-left"
        if selected in ("handoffs", "arena-status"):
            return ((selected, full),)
        return ((selected, Rect(3, 1, main_h, width)),
                ("handoffs", Rect(3 + main_h, 1, bottom_h, width)))
    if screen_id == "visual-lab":
        if cols >= 160 and rows >= 30:
            side = max(32, width // 5)
            middle = width - side * 2 - 2
            return (("bindings", Rect(3, 1, height, side)),
                    ("preview", Rect(3, side + 2, height, middle)),
                    ("signals", Rect(3, side + middle + 3, height, side)))
        if cols >= 100 and rows >= 24:
            left = max(30, width // 3)
            return (("bindings", Rect(3, 1, main_h, left)),
                    ("preview", Rect(3, left + 2, main_h, width - left - 1)),
                    ("signals", Rect(3 + main_h, 1, bottom_h, width)))
        selected = focused_panel or "preview"
        return ((selected, full),)
    main, side, bottom = {
        "site": ("audience", "site-metrics", "site-events"),
        "body": ("pulse", "body-metrics", "rr"),
        "atlas": ("atlas-graph", "atlas-details", "atlas-events"),
        "replay": ("replay-stage", "replay-events", "replay-timeline"),
    }[screen_id]
    if cols >= 100 and rows >= 24:
        side_w = max(30, min(52, width // 3))
        return ((main, Rect(3, 1, main_h, width - side_w - 1)),
                (side, Rect(3, width - side_w + 1, main_h, side_w)),
                (bottom, Rect(3 + main_h, 1, bottom_h, width)))
    selected = focused_panel or main
    if selected == bottom:
        return ((bottom, full),)
    return ((selected, Rect(3, 1, main_h, width)),
            (bottom, Rect(3 + main_h, 1, bottom_h, width)))


def render_screen(renderer, session, rows, cols):
    return SCREEN_BY_ID[getattr(session, "screen_id", "cockpit")].render(renderer, session, rows, cols)
