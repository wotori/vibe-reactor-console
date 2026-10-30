"""Scene panels and the mode picker. Receives state, never advances simulation."""

import math
import textwrap

from .graphics import tesseract_angles, wave
from .layout import arrange
from .protocol import STAGES
from .session import OPTION_LABELS
from .simulation import clamp
from .syntax import spans
from .terminal import Rect
from .visuals import ANIMATION_BY_ID, render_animation, scope_pixels
from .screen_registry import render_screen, screen_layout, SCREEN_BY_ID


SPINNER = "|/-\\"
LOGO = (
    " ___  _____ _   __ /  ____  ____ ",
    "|   \\| ____| | / //  / __ \\/ ___|",
    "| |) |  _| | |/ //  | |  | \\___ \\",
    "|___/|_____|___//    \\____/|____/",
)
EKZA_LOGO = (
    " _____ _  __ ______    _    ",
    "| ____| |/ /|__  /   / \\   ",
    "|  _| | ' /   / /   / _ \\  ",
    "|_____|_|\\_\\ /____| /_/ \\_\\ ",
)


def bar(fraction, width, full="#", empty="."):
    width = max(0, width)
    filled = int(clamp(fraction, 0.0, 1.0) * width)
    return full * filled + empty * (width - filled)


def compact_path(path, width):
    if len(path) <= width:
        return path
    if width < 8:
        return path[-max(1, width):]
    head = min(len(path.split("/")[0]), width // 3)
    return path[:head] + "..." + path[-(width - head - 3):]


class Renderer:
    def __init__(self, canvas):
        self.canvas = canvas

    def draw(self, session):
        self.cursor_position = None
        canvas, sim = self.canvas, session.sim
        canvas.configure(session.theme, session.background)
        screen = canvas.screen
        rows, cols = screen.getmaxyx()
        screen.erase()
        if rows < 16 or cols < 48:
            canvas.put(0, 1, "DEV/OS // " + sim.mode.title, "main", True)
            canvas.put(2, 1, "Enlarge terminal to at least 48 x 16.", "accent")
            canvas.put(4, 1, "Ctrl+] dashboard / Q goes to child" if session.terminal_focused
                       else "F2 terminal | TAB options | Q quit", "muted")
        else:
            render_screen(self, session, rows, cols)
            self.footer(session, rows, cols)
        if session.project_editor:
            self.project_editor(session, rows, cols)
        elif session.binding_editor:
            self.binding_editor(session, rows, cols)
        elif session.inspector_kind:
            self.inspector(session, rows, cols)
        elif session.composer_open:
            self.handoff_composer(session, rows, cols)
        elif session.palette_open:
            self.command_palette(session, rows, cols)
        elif session.help_open:
            self.help(session, rows, cols)
        elif session.menu_kind:
            self.picker(session, rows, cols)
        canvas.cursor(self.cursor_position if not session.menu_open else None)
        screen.refresh()

    def cockpit(self, session, rows, cols):
        sim = session.sim
        layout = arrange(rows, cols, sim.mode.hardware, geometry_focus=True, scope=session.options.scope)
        if session.stream_view:
            self.canvas.put(0, 2, "DEV/OS // STREAM VIEW / personal panels hidden", "main", True)
        else:
            self.header(session, layout.header, cols)
        panels = screen_layout("cockpit", rows, cols, session.focused_panel, True,
                               session.options.scope) if session.zoomed else tuple(
            (name, rect) for name, rect in (("source", layout.editor), ("visual", layout.geometry),
                ("diagnostics", layout.diagnostics), ("events", layout.logs),
                ("memory", layout.memory), ("scope", layout.scope)) if rect is not None)
        for name, rect in panels:
            if name == "source":
                if session.terminal_visible:
                    self.shell(session, rect)
                elif session.stream_view:
                    self.canvas.panel(rect, "SOURCE / HIDDEN", "STREAM VIEW")
                else:
                    self.editor(sim, rect)
            elif name == "visual":
                self.geometry(session, rect, layout.diagnostics is not None or session.zoomed)
            elif name == "diagnostics":
                self.diagnostics(session, rect)
            elif name == "events":
                if not session.stream_view:
                    self.logs(sim, rect)
                else:
                    self.canvas.panel(rect, "EVENTS / HIDDEN", "STREAM VIEW")
            elif name == "scope":
                self.scope(session, rect)
            elif name == "memory":
                self.memory(sim, rect)

    def command_palette(self, session, rows, cols):
        rect = Rect(max(0, (rows - min(rows - 2, 25)) // 2), 2, min(rows - 2, 25), max(5, cols - 5))
        self.canvas.fill(rect)
        p = self.canvas.panel(rect, "COMMANDS / P", "ESC closes")
        p.put(0, 0, "> " + session.palette_query + "_", "main", True)
        commands = session.palette_commands
        count = max(1, p.height - 4)
        index = session.palette_index % max(1, len(commands))
        start = max(0, index - count + 1)
        for row, item in enumerate(commands[start:start + count]):
            selected = start + row == index
            p.put(row + 2, 0, ("> " if selected else "  ") + item.title,
                  "main" if selected else "text", selected, selected)
        if not commands:
            p.put(2, 0, "No matching commands", "muted")
        p.put(p.height - 1, 0, "Type to filter / arrows select / ENTER run / no shell execution", "muted")

    def binding_editor(self, session, rows, cols):
        rect = Rect(1, 1, max(3, rows - 3), max(5, cols - 3))
        self.canvas.fill(rect)
        p = self.canvas.panel(rect, "VISUAL LAB / BINDING SETTINGS", "ESC returns")
        config = session.current_binding_config
        fields = (("source_key", "Source"), ("parameter", "Visual parameter"), ("normalization", "Normalization"),
                  ("input_min", "Input minimum"), ("input_max", "Input maximum"),
                  ("output_min", "Output minimum"), ("output_max", "Output maximum"),
                  ("smoothing_seconds", "EMA seconds"), ("missing", "Missing signal"), ("clamp", "Clamp"))
        p.put(0, 0, "UP/DOWN select / LEFT/RIGHT edit / S save / R reset", "main", True)
        count = max(1, min(len(fields), p.height - 4))
        start = max(0, session.binding_row - count + 1)
        for row, (field, label) in enumerate(fields[start:start + count]):
            selected = start + row == session.binding_row
            value = getattr(config, field)
            text = "{} {:<20} {}".format(">" if selected else " ", label, value)
            p.put(2 + row, 0, text, "main" if selected else "text", selected, selected)
        frame = session.binding_frame
        if frame:
            private = session.stream_view and config.source_key.startswith("body.")
            text = "PRIVATE / STREAM VIEW" if private else "{} / {} / raw {} -> smoothed {}".format(
                frame.mode.upper(), frame.quality.upper(), frame.raw, None if frame.smoothed is None else round(frame.smoothed, 3))
            p.put(p.height - 2, 0, text, "accent")
        p.put(p.height - 1, 0, session.status_message or "Raw metrics remain exact; only graphics are smoothed.", "muted")

    def project_editor(self, session, rows, cols):
        width, height = max(5, min(100, cols - 2)), max(3, min(13, rows - 2))
        rect = Rect(max(0, (rows - height) // 2), max(0, (cols - width) // 2), height, width)
        self.canvas.fill(rect)
        p = self.canvas.panel(rect, "SOURCE / PROJECT FOLDER", "ESC cancels")
        p.put(0, 0, "Type or paste a project folder; ENTER selects it.", "main", True)
        if session.stream_view:
            p.put(2, 0, "STREAM VIEW / private path hidden", "muted")
        else:
            value = session.project_text + "_"
            lines = [value[index:index + max(1, p.width)] for index in range(0, len(value), max(1, p.width))]
            visible = max(1, p.height - 6)
            for row, line in enumerate(lines[-visible:]):
                p.put(2 + row, 0, line, "accent")
        p.put(p.height - 3, 0, "Blank = scene sources / Ctrl+U clear / Backspace edit", "muted")
        p.put(p.height - 2, 0, "Random code only / skipped: secrets, binaries, generated folders", "muted")
        note = "Local folder and visual options are saved automatically." if session.settings_enabled else "Session only / --no-config disables settings saving."
        p.put(p.height - 1, 0, session.project_error or note,
              "warn" if session.project_error else "accent")

    def handoff_composer(self, session, rows, cols):
        rect = Rect(1, 1, max(3, rows - 3), max(5, cols - 3))
        self.canvas.fill(rect)
        p = self.canvas.panel(rect, "HANDOFF / EDIT PUBLIC REPLY", "ENTER sends / ESC cancels")
        actor = "CODEX -> CLAUDE" if session.terminal_slot == "left" else "CLAUDE -> CODEX"
        p.put(0, 0, actor + " / Ctrl+U clear / Backspace edit", "main", True)
        if not session.stream_view:
            lines = textwrap.wrap(session.composer_text + "_", max(1, p.width))
            for row, line in enumerate(lines[-max(0, p.height - 3):]):
                p.put(row + 2, 0, line, "text")
        else:
            p.put(2, 0, "STREAM VIEW / public reply hidden", "muted")
        p.put(p.height - 1, 0, "Finite managed delivery / public text only / no commands are inferred", "accent")

    def help(self, session, rows, cols):
        rect = Rect(1, 1, max(3, rows - 3), max(5, cols - 3))
        self.canvas.fill(rect)
        p = self.canvas.panel(rect, "DEV/OS / CONTROLS", "ANY KEY closes")
        lines = (
            "1 Cockpit / 2 Visual Lab / 3 Arena / 4 Site / 5 Body / 6 Atlas / 7 Replay",
            "P searchable commands / TAB scenes + options / G visual picker",
            "O next panel / Z zoom / V signal binding / I intensity / D Atlas view",
            "L binding settings: source/ranges/linear/log/EMA/missing; S saves",
            "U choose SOURCE project folder / N random file / R reread source",
            "F2 or Ctrl+] terminal input / F4 other terminal / F3 source",
            "While INPUT: all text goes to child. Ctrl+] returns to dashboard.",
            "B native/black background / T palette / W scope / A auto visuals",
            "E demo event / H handoff / S save now / settings autosave / SPACE pause",
            "Replay: SPACE play, [ ] seek, P > Replay / next failure",
            "Replay: comma/period speed / M bookmark / } next marker / P capsule",
            "Atlas: arrows select / ENTER source+diff / H prepare context / R refresh",
            "Tasks: supply --task, then P > Task / run configured check",
            "Arena Demo is simulated. Watch terminals are human-owned.",
            "Managed: explicit --mission + separate workspaces, finite exchanges.",
            "LIVE unavailable stays unavailable; the UI never invents a reading.",
            "Stream View hides personal panels. Recordings exclude pulse by default.",
            "Q quit / ? help",
        )
        for row, line in enumerate(lines[:p.height]):
            p.put(row, 0, line, "main" if row < 2 else "text")

    def inspector(self, session, rows, cols):
        rect = Rect(1, 1, max(3, rows - 3), max(5, cols - 3))
        self.canvas.fill(rect)
        p = self.canvas.panel(rect, "INSPECT / " + session.inspector_kind.upper(), "arrows scroll / ESC closes")
        if session.stream_view:
            p.put(0, 0, "STREAM VIEW / result content hidden", "muted")
            return
        lines = []
        if session.inspector_kind == "task":
            task = session.task_snapshot
            if task:
                lines = ["REAL TASK / " + task.state.upper(), "RUN " + task.run_id,
                         "ARGV " + repr(task.argv), "CWD " + task.cwd,
                         "REVISION " + task.revision, "EXIT " + str(task.exit_code),
                         "ERROR " + task.error, "OUTPUT" + (" / TRUNCATED" if task.truncated else ""), task.output]
        elif session.inspector_kind == "atlas":
            item = session.atlas_inspection
            if item:
                lines = [item.provenance, "STATE " + item.state + " / " + item.quality,
                         "PATH " + item.path, "REVISION " + item.revision, "STATUS " + item.status_code,
                         "BYTES " + str(item.size_bytes), "SHA256 " + (item.sha256 or "unknown")
                         + (" / COMPLETE" if item.hash_complete else " / NOT A COMPLETE HASH"),
                         "VERIFICATION " + item.verification, item.error]
                for key, value in item.metadata:
                    lines.append(str(key) + ": " + str(value))
                lines += ["UNSTAGED DIFF", item.unstaged_diff or "[no unstaged diff]",
                          "STAGED DIFF", item.staged_diff or "[no staged diff]",
                          "SOURCE PREVIEW" + (" / TRUNCATED" if item.truncated else ""),
                          item.source_text or ("[binary content]" if item.binary else "[no text source]"),
                          "H prepares this selection for the next editable Arena handoff."]
        elif session.arena:
            arena = session.arena
            lines = [arena.mode.upper() + " / " + arena.status,
                     "PHASE " + getattr(arena, "phase", "idle") + " / FIX {}/2".format(getattr(arena, "fix_cycles", 0)),
                     "CHECKS " + ("VERIFIED" if arena.verified else "NOT VERIFIED"), arena.reason]
            for report in getattr(arena, "validation_reports", ()):
                lines += ["INDEPENDENT CHECK / " + repr(report.argv), "CWD " + report.cwd,
                          "ARTIFACT HASH " + report.artifact_hash, "EXIT " + str(report.exit_code),
                          "STATE " + report.status, "STDOUT", report.stdout, "STDERR", report.stderr]
            for artifact in arena.artifacts:
                lines += [artifact.kind.upper() + " / " + artifact.id + " / " + artifact.producer,
                          "SHA256 " + artifact.sha256, artifact.text]
        wrapped = [line for item in lines for raw in str(item).splitlines() for line in (
            textwrap.wrap(raw, max(1, p.width), replace_whitespace=False) or [""])]
        if not wrapped:
            wrapped = ["No completed result yet. Configure --task or start an explicit mission."]
        start = min(session.report_scroll, max(0, len(wrapped) - max(1, p.height - 1)))
        for row, line in enumerate(wrapped[start:start + max(0, p.height - 1)]):
            p.put(row, 0, line, "text")
        p.put(p.height - 1, 0, "LINE {}/{} / output is bounded / claims are separate from check evidence".format(start + 1, len(wrapped)), "muted")

    def header(self, session, height, cols):
        c, sim = self.canvas, session.sim
        if height == 5:
            for i, line in enumerate(EKZA_LOGO if sim.mode.id == "ekza" else LOGO):
                c.put(i, 2, line, "main", True)
            c.put(0, 40, sim.mode.title + " // " + sim.mode.subtitle, "accent", True, cols - 42)
            c.put(1, 40, "workspace: " + sim.mode.workspace, "muted", limit=cols - 42)
            controls = "[TAB] SCENES/OPTIONS  [G] VISUAL: {}  [F2] TERMINAL".format(session.animation.upper())
            c.put(2, 40, controls, "main", limit=cols - 42)
            label = sim.source.kind + " CODE / SIM BUILD" if sim.snippet.source_path else "SIMULATED"
            fps = "{:.1f}".format(session.actual_fps) if session.actual_fps else "--"
            c.put(3, 40, label + " / FPS {}/{} / {:.1f}ms render / {:.2g}x".format(
                fps, session.options.fps, session.render_ms, session.speed), "muted", limit=cols - 42)
        else:
            c.put(0, 2, "DEV/OS // " + sim.mode.title, "main", True, cols - 4)
            c.put(1, 2, "[TAB] SCENES  [G] VISUALS  [F2] TERMINAL | " + sim.mode.subtitle, "muted", limit=cols - 4)

    def editor(self, sim, rect):
        p = self.canvas.panel(rect, "01 / SOURCE", "U FOLDER / " + sim.snippet.language)
        p.put(0, 0, compact_path(sim.snippet.path, p.width), "main", True)
        label = sim.source.kind + " / " if sim.snippet.source_path else ""
        p.put(1, 0, getattr(sim, "source_note", "") or label + sim.snippet.description,
              "accent" if sim.snippet.source_path else "muted")
        lines = sim.typed_lines
        available = max(0, p.height - 3)
        start = max(0, len(lines) - available)
        assembly = sim.snippet.language == "NASM" and sim.source.kind != "PROJECT"
        gutter = 11 if assembly else 6
        for offset, line in enumerate(lines[start:start + available]):
            row, number = 2 + offset, start + offset
            if assembly:
                p.put(row, 0, "{:08x}".format(0x80001000 + number * 4), "muted")
            else:
                p.put(row, 0, "{:>3}".format(sim.source.first_line + number), "muted")
            p.put(row, gutter - 2, ">" if number == sim.line_index else " ", "main")
            p.put(row, gutter, line)
            for col, token, tone in spans(line, sim.snippet.language):
                p.put(row, gutter + col, token, tone)
            if number == sim.line_index and int(sim.elapsed * 3) % 2 == 0:
                p.put(row, gutter + len(line), "_", "main", True)
        status = "FREESTANDING / ELF64" if sim.mode.hardware else "LSP CONNECTED"
        if sim.snippet.source_path:
            status = "{} L{}-{} #{}".format(sim.source.kind, sim.source.first_line,
                                             sim.source.last_line, sim.source.digest)
        p.put(p.height - 1, 0, "{} Ln {:03d} Col {:02d} | {}".format(
            SPINNER[int(sim.elapsed * 8) % 4], sim.source.first_line + min(sim.line_index, len(sim.lines) - 1),
            sim.char_index + 1, status), "accent")

    def shell(self, session, rect, slot=None):
        focused = session.terminal_focused and (slot is None or slot == session.terminal_slot)
        frame = session.terminal_frame
        if slot:
            frame = next((item.frame for item in session.terminal_slots if item.id == slot), None)
        p = self.canvas.panel(rect, "01 / TERMINAL" + (" / " + slot.upper() if slot else ""), "INPUT" if focused else "VIEW")
        if session.stream_view:
            p.put(0, 0, "STREAM VIEW / terminal text hidden", "muted")
            return
        p.put(0, 0, "REAL SHELL / F2 or Ctrl+] dashboard" if focused else "REAL SHELL / F2 type / F3 source",
              "main" if focused else "muted", focused)
        height = max(0, p.height - 2)
        if frame:
            for y, runs in enumerate(frame.lines[:height]):
                for run in runs:
                    if run.column < p.width:
                        self.canvas.put_terminal(rect.y + 2 + y, rect.x + 2 + run.column,
                                                 run.text, run.style, p.width - run.column)
            if frame.dead and not frame.lines:
                for y, line in enumerate(textwrap.wrap(frame.status, max(1, p.width))[:height]):
                    p.put(1 + y, 0, line, "warn")
            if (focused and frame.cursor_visible and not frame.dead
                    and 0 <= frame.cursor_y < height and 0 <= frame.cursor_x < p.width):
                self.cursor_position = rect.y + 2 + frame.cursor_y, rect.x + 2 + frame.cursor_x
        p.put(p.height - 1, 0, frame.status if frame else "Starting terminal...", "muted")

    def geometry(self, session, rect, expanded):
        sim = session.sim
        animation, elapsed = session.animation, session.visual_time
        hyper = animation == "tesseract"
        title = "EKZA CORE" if sim.mode.id == "ekza" else "HYPERCORE" if hyper else "RUNTIME"
        p = self.canvas.panel(rect, "02 / " + title, animation.upper() + " / {}FPS".format(session.options.fps))
        caption = "EKZA {} / {}".format(session.protocol.kind.upper(), STAGES[session.protocol.stage][0]) \
            if animation == "protocol" else ANIMATION_BY_ID[animation].caption
        p.put(0, 0, caption, "muted")
        reserved = (3 if hyper else 2) if expanded else 4
        height = max(0, p.height - reserved)
        for (x, y), pixel in render_animation(animation, elapsed, p.width, height, session.protocol,
                                              session.visual_parameters()).items():
            p.put(y + 1, x, pixel.char, pixel.tone, pixel.bold)
        if not expanded and p.height >= 7:
            if sim.mode.hardware:
                r = sim.machine.registers
                p.put(p.height - 3, 0, "RIP {:016x}".format(r["RIP"]), "accent")
                p.put(p.height - 2, 0, "RAX {:016x}".format(r["RAX"]), "main")
            elif sim.mode.id == "ekza":
                p.put(p.height - 3, 0, "EKZA / " + session.protocol.kind.upper(), "main")
                p.put(p.height - 2, 0, "IDENTITY + SHA256 + PROFILE", "accent")
            else:
                p.put(p.height - 3, 0, "LOAD [" + bar(sim.history[-1], p.width - 12) + "]", "main")
                p.put(p.height - 2, 0, "{} tests / {} builds".format(sim.tests, sim.builds), "accent")
        if p.height >= 4:
            row = p.height - 2 if hyper and expanded else p.height - 1
            p.put(row, 0, "XW:{:03.0f}  ZW:{:03.0f}  XY:{:03.0f}".format(
                *(math.degrees(angle) % 360 for angle in tesseract_angles(elapsed)))
                if hyper else "G: VISUALS / AUTO " + ("{:g}s".format(session.options.auto_cycle)
                    if session.options.auto_cycle else "OFF"), "muted")
            if hyper and expanded:
                p.put(p.height - 1, 0, "+ CELL A", "accent")
                p.put(p.height - 1, 12, "o CELL B", "main")
                p.put(p.height - 1, 24, "W LINKS", "hot")

    def diagnostics(self, session, rect):
        sim = session.sim
        if sim.mode.id == "ekza":
            self.protocol(session, rect)
            return
        if sim.mode.hardware:
            self.registers(sim, rect)
            return
        p = self.canvas.panel(rect, "03 / TELEMETRY", "SYNTHETIC")
        p.put(0, 0, "LOAD HISTORY" if sim.mode.id == "lab" else "PROJECTION / CLOCK TRACE", "muted")
        if sim.mode.id == "hyperspace":
            for (x, y), pixel in wave(sim.elapsed, p.width, 3).items():
                p.put(y + 1, x, pixel.char, pixel.tone)
        else:
            history = list(sim.history)[-p.width:]
            for row in range(3):
                p.put(row + 1, 0, "".join("#" if v > (2 - row) / 3 else " " for v in history), "main")
        p.put(4, 0, "CPU [" + bar(sim.history[-1], p.width - 13) + "] {:2.0f}%".format(sim.history[-1] * 100), "accent")
        p.put(5, 0, "MEM [" + bar(0.43 + math.sin(sim.elapsed * 0.13) * 0.07, p.width - 13) + "] 512M", "main")
        p.put(6, 0, "{} tests passed / {} builds".format(sim.tests, sim.builds), "muted")
        p.put(7, 0, "LATENCY 0.04ms / ALL SYSTEMS IMAGINARY", "muted")

    def protocol(self, session, rect):
        frame = session.protocol
        p = self.canvas.panel(rect, "03 / ASSET ROUTE", "EKZA PROTOCOL")
        p.put(0, 0, "{:02d}/06 {} / {} [DEMO]".format(frame.stage + 1, STAGES[frame.stage][0], frame.kind.upper()), "main", True)
        p.put(1, 0, frame.identity, "accent")
        p.put(2, 0, "{} | SHA256 {}...".format(frame.revision, frame.digest[:16]), "muted")
        p.put(3, 0, "PROJECT {} / {}".format(frame.consumer.project_id, frame.consumer.platform), "accent")
        p.put(4, 0, "PROFILE " + frame.profile, "main")
        p.put(5, 0, "[" + bar((frame.stage + frame.fraction) / 6, p.width - 2, "=", "-") + "]", "hot")
        for row, line in enumerate(textwrap.wrap(frame.explanation, max(1, p.width))[:2], 6):
            p.put(row, 0, line, "muted")

    def scope(self, session, rect):
        p = self.canvas.panel(rect, "06 / SINE SCOPE", "0.65Hz")
        p.put(0, 0, "sin(t) / cos(t)   [W] toggle", "muted")
        for (x, y), pixel in scope_pixels(session.visual_time, p.width, max(0, p.height - 1)).items():
            p.put(y + 1, x, pixel.char, pixel.tone, pixel.bold)

    def registers(self, sim, rect):
        p = self.canvas.panel(rect, "03 / CPU STATE", "CPL 0")
        items = list(sim.machine.registers.items())
        pair = p.width >= 43
        count = 8 if pair else 4
        for index, (name, value) in enumerate(items[:count]):
            row, col = (index // 2, (index % 2) * 23) if pair else (index, 0)
            p.put(row, col, name, "muted")
            p.put(row, col + 4, "{:016x}".format(value), "main" if name == "RAX" else "accent")
        p.put(4, 0, "RIP {:016x}  CR3 00001000".format(sim.machine.registers["RIP"]), "hot")
        p.put(5, 0, "RFLAGS [ IF=1 ZF={} ]  L1D 99.2%".format(int(sim.machine.zero)), "muted")
        for index, (_, encoded, assembly) in enumerate(list(sim.machine.trace)[-2:]):
            p.put(6 + index, 0, "{:11s} {}".format(encoded, assembly), "main" if index else "muted")

    def memory(self, sim, rect):
        p = self.canvas.panel(rect, "05 / MEMORY", "HEX / ASCII")
        columns = 16 if p.width >= 78 else 8 if p.width >= 46 else 4
        p.put(0, 0, "ADDRESS   " + " ".join("{:02X}".format(i) for i in range(columns)), "muted")
        for row, (address, data) in enumerate(sim.machine.memory_rows(p.height - 1, columns), 1):
            encoded = " ".join("{:02x}".format(byte) for byte in data)
            ascii_text = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in data)
            p.put(row, 0, "{:08x}".format(address & 0xFFFFFFFF), "muted")
            p.put(row, 10, encoded, "main")
            p.put(row, 11 + columns * 3, "|" + ascii_text + "|", "accent")

    def logs(self, sim, rect):
        p = self.canvas.panel(rect, "04 / BUILD STREAM", "#{:07x}".format(sim.build_id))
        labels = []
        for index, phase in enumerate(sim.mode.phases):
            labels.append(("+" if index < sim.phase else ">" if index == sim.phase else ".") + phase)
        pipeline = "  ".join(labels)
        p.put(0, 0, pipeline if len(pipeline) <= p.width else "> " + sim.mode.phases[sim.phase], "main", True)
        p.put(1, 0, "[{}] {:3.0f}%".format(bar(sim.progress, min(32, p.width - 9), "=", "-"),
                                           sim.progress * 100), "accent")
        visible = max(0, p.height - 3)
        for row, entry in enumerate(list(sim.logs)[-visible:] if visible else (), 3):
            p.put(row, 0, entry.timestamp, "muted")
            tone = "main" if entry.kind in ("PASS", "READY", "LIVE") else "warn" if entry.kind == "NOTE" else "accent"
            p.put(row, 9, "{:5s}".format(entry.kind), tone)
            p.put(row, 16, entry.message, "warn" if entry.kind == "NOTE" else "text")

    def footer(self, session, rows, cols):
        if session.screen_id == "replay" and session.replay_status:
            provenance = "REPLAY / " + ("PLAYING" if session.replay_status.playing else "PAUSED")
        elif session.screen_id == "arena" and session.arena:
            provenance = session.arena.mode.upper()
        elif session.screen_id == "cockpit":
            provenance = "SIM BUILD"
        else:
            provenance = session.telemetry.mode.upper()
        state = "VISUAL PAUSED / " + provenance if session.paused and session.screen_id != "replay" else provenance
        if session.terminal_focused:
            text = " TERMINAL INPUT | F2 / Ctrl+] dashboard | F3 source | Ctrl+C interrupts child | Tab/arrows go to shell"
        elif session.screen_id == "replay":
            text = " {} | SPACE play [ ] seek , . speed M bookmark }} next | P commands ? help Q quit".format(state)
        elif cols < 90:
            text = " {} | 1-7 screens P commands ? help F2 input G visuals Q quit".format(state)
        else:
            text = " {} | 1-7 screens P commands ? help O panel Z zoom F2 input F4 other | TAB scenes G visuals W sine A auto T color B bg Q quit | {:.2g}x | {} lines".format(
                state, session.speed, session.sim.total_lines)
        if session.status_message and not session.stream_view and not session.terminal_focused:
            text = " " + session.status_message + " | P commands / ? help"
        self.canvas.put(rows - 1, 0, text, "warn" if session.paused else "main", limit=cols - 1)

    def picker(self, session, rows, cols):
        width, height = min(116, cols - 4), min(23, rows - 2)
        if width < 16 or height < 5:
            self.canvas.put(0, 0, "SELECT: " + session.menu_entries[session.picker_index].title, "main", True)
            return
        rect = Rect((rows - height) // 2, (cols - width) // 2, height, width)
        self.canvas.fill(rect)
        title = "SWITCH ANIMATION" if session.menu_kind == "animations" else "SWITCH MODE"
        p = self.canvas.panel(rect, title, "TAB SCENES / G VISUALS")
        wide = p.width >= 86
        split = p.width // 2 if wide else p.width
        if wide:
            for row in range(p.height - 1):
                p.put(row, split, "|", "muted")
        show_list = wide or session.menu_focus == "list"
        show_options = wide or session.menu_focus == "options"
        if show_list:
            self.picker_list(session, p, split - 2 if wide else p.width)
        if show_options:
            self.picker_options(session, p, split + 3 if wide else 0)
        p.put(p.height - 1, 0, "LEFT/RIGHT column | UP/DOWN browse | ENTER select/change | ESC close", "muted")

    def picker_list(self, session, p, width):
        p.put(0, 0, "ANIMATIONS [G]" if session.menu_kind == "animations" else "CODE SCENES [TAB]",
              "main" if session.menu_focus == "list" else "muted", True)
        entries, selected_index = session.menu_entries, session.picker_index
        stride = 3 if p.height >= 18 else 2 if p.height >= 9 else 1
        count = min(len(entries), max(1, (p.height - 3) // stride))
        start = max(0, selected_index - count + 1)
        for row, index in enumerate(range(start, min(len(entries), start + count))):
            item = entries[index]
            selected = index == selected_index
            active = item.id == (session.animation if session.menu_kind == "animations" else session.mode_id)
            title = " {} {:02d}  {}{}".format(">" if selected else " ", index + 1, item.title, " *" if active else "")
            focused = selected and session.menu_focus == "list"
            p.put(2 + row * stride, 0, title[:width], "main" if selected else "text", focused, focused)
            if stride > 1:
                description = getattr(item, "subtitle", item.description)
                p.put(3 + row * stride, 0, description[:width], "accent" if selected else "muted")
        preview_row = 2 + count * stride
        if p.height - preview_row >= 2:
            choice = entries[selected_index]
            for i, line in enumerate(textwrap.wrap(choice.description, max(1, width))[:p.height - preview_row - 1]):
                p.put(preview_row + i, 0, line, "accent")

    def picker_options(self, session, p, col):
        p.put(0, col, "SHARED OPTIONS [RIGHT]", "main" if session.menu_focus == "options" else "muted", True)
        width = p.width - col
        stride = 2 if p.height >= 18 else 1
        count = min(len(OPTION_LABELS), max(1, (p.height - 3) // stride))
        start = max(0, session.option_index - count + 1)
        for row, index in enumerate(range(start, min(len(OPTION_LABELS), start + count))):
            key, label = OPTION_LABELS[index]
            selected = index == session.option_index and session.menu_focus == "options"
            text = "{} {:<17} {}".format(">" if selected else " ", label, session.option_text(key))
            p.put(2 + row * stride, col, text[:width], "main" if selected else "accent", selected, selected)
        if p.height >= 19:
            p.put(p.height - 3, col, "ENTER changes the selected option", "muted")
            p.put(p.height - 2, col, "Settings apply to every scene", "muted")
