"""Input mapping and frame pacing; all application state lives in Session."""

import time

from .renderer import Renderer
from .layout import arrange
from .session import Session
from .shell import EmbeddedTerminal, TerminalFrame
from .sources import SourceProvider
from .terminal import Canvas, curses


def action_for_key(key):
    actions = {
        9: "tab", 10: "accept", 13: "accept", 27: "escape",
        ord("q"): "quit", ord("Q"): "quit", ord(" "): "pause",
        ord("+"): "faster", ord("="): "faster", ord("-"): "slower", ord("_"): "slower",
        ord("t"): "theme", ord("T"): "theme", ord("n"): "next", ord("N"): "next",
        ord("r"): "reload", ord("R"): "reload",
        ord("b"): "background", ord("B"): "background",
        ord("g"): "animation", ord("G"): "animation", ord("c"): "consumer", ord("C"): "consumer",
        ord("w"): "scope", ord("W"): "scope", ord("a"): "auto", ord("A"): "auto",
        ord("j"): "down", ord("k"): "up",
    }
    if curses is not None:
        actions.update({curses.KEY_DOWN: "down", curses.KEY_UP: "up",
                        curses.KEY_LEFT: "left", curses.KEY_RIGHT: "right",
                        curses.KEY_BTAB: "up", curses.KEY_ENTER: "accept",
                        curses.KEY_F2: "terminal", curses.KEY_F3: "source"})
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


def terminal_size(screen, session):
    rows, cols = screen.getmaxyx()
    layout = arrange(max(16, rows), max(48, cols), session.sim.mode.hardware,
                     geometry_focus=True, scope=session.options.scope)
    return max(2, layout.editor.width - 4), max(1, layout.editor.height - 4)


def run(screen, args):
    screen.keypad(True)
    screen.nodelay(True)
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    if hasattr(curses, "set_escdelay"):
        curses.set_escdelay(25)
    session = Session(args.mode, args.theme, args.speed, args.seed,
                      sources=SourceProvider(args.ekza_root, args.bundled), background=args.background,
                      animation=args.animation, consumer=args.consumer, fps=args.fps,
                      auto_cycle=args.auto_cycle or 0, scope=not args.no_scope)
    renderer = Renderer(Canvas(screen))
    embedded, raw_input = None, False

    def ensure_terminal():
        nonlocal embedded
        if embedded and session.terminal_focused and embedded.snapshot().dead:
            embedded.close()
            embedded = None
        if embedded is None and session.terminal_visible:
            try:
                embedded = EmbeddedTerminal(*terminal_size(screen, session), cwd=args.cwd,
                                            command=args.terminal_command)
            except (OSError, RuntimeError) as exc:
                session.terminal_frame = TerminalFrame(dead=True, status=str(exc))
                session.terminal_focused = False

    if args.terminal or args.terminal_command:
        session.handle("terminal")
        ensure_terminal()
    start = last = time.monotonic()
    sample_start, sample_frames, sample_render = start, 0, 0.0
    deadline, previous_fps = start, session.options.fps
    try:
        while session.running:
            now = time.monotonic()
            if args.duration is not None and now - start >= args.duration:
                break
            dt, last = min(now - last, 0.2), now
            # Raw mode makes Ctrl+C/Ctrl+Z ordinary input for the child, instead
            # of signals delivered to this dashboard. Outside input focus, keep
            # normal curses signal handling (including Ctrl+C to exit).
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
                if key == curses.KEY_RESIZE:
                    continue
                action = action_for_key(key)
                if action in ("terminal", "source"):
                    session.handle(action)
                    if action == "terminal" and session.terminal_frame and session.terminal_frame.dead:
                        session.terminal_focused = True
                    ensure_terminal()
                elif key == 29:  # Ctrl+], also works without function keys
                    session.handle("terminal")
                    ensure_terminal()
                elif session.terminal_focused:
                    data = terminal_key(key)
                    if embedded and data:
                        embedded.send(data)
                elif key == 3:
                    session.handle("quit")
                elif action:
                    session.handle(action)
                if not session.running:
                    break
            if not session.running:
                break
            if embedded:
                embedded.resize(*terminal_size(screen, session))
                session.terminal_frame = embedded.snapshot()
            session.update(dt)
            render_started = time.perf_counter()
            renderer.draw(session)
            sample_render += (time.perf_counter() - render_started) * 1000
            sample_frames += 1
            session.frames += 1
            if now - sample_start >= .5:
                session.actual_fps = sample_frames / (now - sample_start)
                session.render_ms = sample_render / sample_frames
                sample_start, sample_frames, sample_render = now, 0, 0.0
            # Accumulated deadlines compensate for scheduler oversleep. Sleeping a
            # fresh relative interval every frame otherwise drifts well below 60 FPS.
            if previous_fps != session.options.fps:
                deadline, previous_fps = now, session.options.fps
            interval = 1 / session.options.fps
            deadline += interval
            finished = time.monotonic()
            if finished - deadline > interval:
                deadline = finished
            time.sleep(max(0.0, deadline - finished))
    finally:
        if embedded:
            embedded.close()
        if raw_input:
            curses.noraw()
            curses.cbreak()
    return session
