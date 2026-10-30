"""Curses input ownership, worker orchestration and accumulated frame pacing."""

import time

from .cli import runtime_options
from .layout import arrange
from .renderer import Renderer
from .runtime import Runtime
from .screen_registry import SCREENS, screen_layout
from .session import Session
from .sources import SourceProvider
from .terminal import Canvas, curses


def action_for_key(key):
    actions = {
        9: "tab", 10: "accept", 13: "accept", 27: "escape",
        ord("q"): "quit", ord("Q"): "quit", ord(" "): "pause",
        ord("+"): "faster", ord("="): "faster", ord("-"): "slower", ord("_"): "slower",
        ord("t"): "theme", ord("T"): "theme", ord("n"): "next", ord("N"): "next",
        ord("r"): "reload", ord("R"): "reload", ord("b"): "background", ord("B"): "background",
        ord("g"): "animation", ord("G"): "animation", ord("c"): "consumer", ord("C"): "consumer",
        ord("w"): "scope", ord("W"): "scope", ord("a"): "auto", ord("A"): "auto",
        ord("j"): "down", ord("k"): "up", ord("p"): "palette", ord("P"): "palette",
        ord("/"): "palette",
        ord("l"): "binding-settings", ord("L"): "binding-settings",
        ord("m"): "replay-bookmark", ord("M"): "replay-bookmark",
        ord(","): "replay-speed-down", ord("."): "replay-speed-up", ord("}"): "replay-next-bookmark",
        ord("?"): "help", ord("o"): "panel", ord("O"): "panel-prev", ord("z"): "zoom", ord("Z"): "zoom",
        ord("v"): "binding", ord("V"): "binding", ord("i"): "intensity", ord("I"): "intensity",
        ord("d"): "atlas", ord("D"): "atlas", ord("e"): "event", ord("E"): "event",
        ord("h"): "handoff", ord("H"): "handoff", ord("s"): "save", ord("S"): "save",
        ord("["): "replay-back", ord("]"): "replay-forward", ord("!"): "mission-stop",
    }
    actions.update({ord(str(index)): "screen:" + item.id for index, item in enumerate(SCREENS, 1)})
    if curses is not None:
        actions.update({curses.KEY_DOWN: "down", curses.KEY_UP: "up",
                        curses.KEY_LEFT: "left", curses.KEY_RIGHT: "right",
                        curses.KEY_BTAB: "up", curses.KEY_ENTER: "accept",
                        curses.KEY_F2: "terminal", curses.KEY_F3: "source", curses.KEY_F4: "terminal-right"})
    return actions.get(key)


def terminal_key(key):
    if 0 <= key < 256:
        return bytes((key,))
    named = {
        curses.KEY_UP: "Up", curses.KEY_DOWN: "Down", curses.KEY_LEFT: "Left", curses.KEY_RIGHT: "Right",
        curses.KEY_HOME: "Home", curses.KEY_END: "End", curses.KEY_DC: "DC", curses.KEY_IC: "IC",
        curses.KEY_BACKSPACE: "BSpace", curses.KEY_PPAGE: "PPage", curses.KEY_NPAGE: "NPage",
        curses.KEY_BTAB: "BTab", curses.KEY_ENTER: "Enter",
    }
    named.update({curses.KEY_F0 + number: "F" + str(number) for number in range(1, 13)})
    return named.get(key)


def terminal_rectangles(screen, session):
    rows, cols = screen.getmaxyx()
    if rows < 16 or cols < 48:
        return ()
    if session.screen_id == "cockpit":
        layout = arrange(max(16, rows), max(48, cols), session.sim.mode.hardware,
                         geometry_focus=True, scope=session.options.scope)
        if session.terminal_visible and (not session.zoomed or session.focused_panel == "source"):
            if session.zoomed:
                return ((session.terminal_slot, dict(screen_layout("cockpit", rows, cols, "source", True))["source"]),)
            return ((session.terminal_slot, layout.editor),)
    elif session.screen_id == "arena":
        focus = session.focused_panel
        if focus.startswith("terminal-"):
            focus = "terminal-" + session.terminal_slot
        return tuple((name.split("-", 1)[1], rect) for name, rect in screen_layout(
            "arena", rows, cols, focus, session.zoomed) if name.startswith("terminal-"))
    return ()


def terminal_size(screen, session):
    rectangles = terminal_rectangles(screen, session)
    rect = rectangles[0][1] if rectangles else arrange(24, 80).editor
    return max(2, rect.width - 4), max(1, rect.height - 4)


def dispatch_key(session, runtime, key):
    """Input ownership is decided before any dashboard character binding."""
    if key == curses.KEY_RESIZE:
        return
    action = action_for_key(key)
    if session.binding_editor:
        if action:
            session.binding_key(action)
        return
    if session.inspector_kind:
        if key == 27 or key in (ord("q"), ord("Q")):
            session.inspector_kind = None
        elif key in (curses.KEY_DOWN, ord("j")):
            session.report_scroll += 1
        elif key in (curses.KEY_UP, ord("k")):
            session.report_scroll = max(0, session.report_scroll - 1)
        elif key in (curses.KEY_NPAGE, curses.KEY_PPAGE):
            session.report_scroll = max(0, session.report_scroll + (10 if key == curses.KEY_NPAGE else -10))
        return
    if session.composer_open:
        session.composer_key(key)
        return
    if session.palette_open:
        if key in (curses.KEY_UP, curses.KEY_DOWN):
            session.handle(action)
        else:
            session.palette_key(key)
            if session.terminal_focused:
                runtime.ensure_terminal()
        return
    if session.help_open:
        session.help_open = False
        return
    if key == 29 or action in ("terminal", "source", "terminal-right"):
        if session.screen_id == "replay":
            session.status_message = "Replay is read-only; switch screens for a live terminal."
            return
        if session.screen_id not in ("cockpit", "arena"):
            session.choose_screen("cockpit")
        session.handle("terminal" if key == 29 else action)
        runtime.ensure_terminal()
    elif session.terminal_focused:
        data = terminal_key(key)
        if data:
            runtime.terminals.send(data, session.terminal_slot)
    elif key == 3:
        session.handle("quit")
    elif action:
        session.handle(action)


def run(screen, args):
    screen.keypad(True)
    screen.nodelay(True)
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    if hasattr(curses, "set_escdelay"):
        curses.set_escdelay(25)
    settings = runtime_options(args)
    session = Session(sources=SourceProvider(args.ekza_root, args.bundled), **settings)
    renderer = Renderer(Canvas(screen))
    raw_input = False
    with_services = Runtime(args, session)
    start = last = time.monotonic()
    sample_start, sample_frames, sample_render = start, 0, 0.0
    deadline, previous_fps = start, session.options.fps
    try:
        while session.running:
            now = time.monotonic()
            if args.duration is not None and now - start >= args.duration:
                break
            dt, last = min(now - last, .2), now
            if session.terminal_focused != raw_input:
                raw_input = session.terminal_focused
                if raw_input:
                    curses.raw()
                else:
                    curses.noraw()
                    curses.cbreak()
            for _ in range(256):
                key = screen.getch()
                if key == -1:
                    break
                dispatch_key(session, with_services, key)
                if not session.running:
                    break
            if not session.running:
                break
            rectangles = terminal_rectangles(screen, session)
            with_services.terminals.set_visible(tuple(slot for slot, rect in rectangles) if not session.stream_view else ())
            for slot, rect in rectangles:
                with_services.terminals.resize(max(2, rect.width - 4), max(1, rect.height - 4), slot)
            session.update(dt)
            with_services.update(dt)
            render_started = time.perf_counter()
            renderer.draw(session)
            sample_render += (time.perf_counter() - render_started) * 1000
            sample_frames += 1
            session.frames += 1
            if now - sample_start >= .5:
                session.actual_fps = sample_frames / (now - sample_start)
                session.render_ms = sample_render / sample_frames
                sample_start, sample_frames, sample_render = now, 0, 0.0
            if previous_fps != session.options.fps:
                deadline, previous_fps = now, session.options.fps
            interval = 1 / session.options.fps
            deadline += interval
            finished = time.monotonic()
            if finished - deadline > interval:
                deadline = finished
            time.sleep(max(0., deadline - finished))
    finally:
        with_services.close()
        if raw_input:
            curses.noraw()
            curses.cbreak()
    return session
