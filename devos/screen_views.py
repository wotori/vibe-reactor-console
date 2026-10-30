"""Pure console screen compositions over immutable snapshots.

The application collects data and owns keyboard input. These views only draw;
they never create a terminal, advance an agent, seek a replay or query a source.
"""

import math
import textwrap

from .atlas import AtlasCatalog
from .bindings import BINDINGS, BINDING_BY_ID, BindingEngine
from .graphics import Pixel, line_cells
from .screen_registry import SCREEN_BY_ID, screen_layout
from .visuals import ANIMATION_BY_ID, render_animation


def safe(value, limit=512):
    """Do not allow a path, server label or message to inject terminal controls."""
    return "".join(char if 32 <= ord(char) < 127 else "?" for char in str(value))[:limit]


def number(value):
    if value is None:
        return "--"
    if isinstance(value, (int, float)):
        return "{:g}".format(value)
    return safe(value)


def wrapped(panel, row, value, tone="text", limit=None):
    lines = textwrap.wrap(safe(value), max(1, panel.width)) or [""]
    for offset, line in enumerate(lines[:max(0, panel.height - row) if limit is None else limit]):
        panel.put(row + offset, 0, line, tone)
    return row + min(len(lines), max(0, panel.height - row) if limit is None else limit)


def data(session):
    return getattr(session, "telemetry", None)


def metric(session, key):
    snapshot = data(session)
    return snapshot.metric(key) if snapshot is not None else None


def mode_label(session):
    if getattr(session, "screen_id", None) == "arena" and getattr(session, "arena", None) is not None:
        return session.arena.mode.upper()
    if getattr(session, "screen_id", None) == "replay" and getattr(session, "replay_status", None) is not None:
        return "REPLAY"
    snapshot = data(session)
    if snapshot is None:
        return "NO SOURCE"
    modes = {item.mode for item in snapshot.metrics}
    if not modes:
        modes = {item.mode for item in snapshot.providers}
    return next(iter(modes)).upper() if len(modes) == 1 else "MIXED" if modes else "NO SOURCE"


def provenance(sample):
    if sample is None:
        return "NO SOURCE / UNAVAILABLE"
    age = "{:.1f}s".format(sample.age_seconds)
    basis = "source age" if sample.time_basis != "received_only" else "received age; source unknown"
    window = " / {}s window".format(sample.window_seconds) if sample.window_seconds else ""
    return "{} / {} / {} {}{}".format(sample.mode.upper(), sample.quality.upper(), basis, age, window)


def source_line(panel, row, sample, redacted=False):
    row = wrapped(panel, row, provenance(sample), "muted", 2)
    if sample is not None:
        row = wrapped(panel, row, "source: " + ("[HIDDEN]" if redacted else sample.source_id) + " / unit: " + sample.unit, "muted", 2)
    return row


def header(renderer, session, rows, cols):
    canvas = renderer.canvas
    spec = SCREEN_BY_ID[session.screen_id]
    owner = "DEVOS"
    if session.screen_id != "replay" and getattr(session, "terminal_focused", False):
        owner = "TERMINAL " + getattr(session, "terminal_slot", "left").upper()
    canvas.put(0, 2, "DEV/OS / " + spec.title, "main", True, cols - 4)
    badge = "INPUT: " + owner + " / " + mode_label(session)
    if cols >= 100:
        canvas.put(0, max(2, cols - len(badge) - 3), badge, "accent", True)
    canvas.put(1, 2, "1 COCKPIT  2 LAB  3 ARENA  4 SITE  5 BODY  6 ATLAS  7 REPLAY  8 ARCADE", "muted", limit=cols - 4)
    status = "STREAM VIEW / PRIVATE CONTENT HIDDEN" if getattr(session, "stream_view", False) else getattr(session, "status_message", "")
    hint = status or "P commands / O focus / Z zoom / ? help"
    if cols < 100:
        hint = badge + " | " + hint
    canvas.put(2, 2, safe(hint), "accent" if status else "muted", limit=cols - 4)


def arcade(renderer, session, rows, cols):
    from .arcade_view import draw_arcade
    draw_arcade(renderer, session, rows, cols)


def panels(session, rows, cols):
    focused = getattr(session, "focused_panel", None)
    # The selected narrow Arena tab must match the actual owner of PTY input.
    if session.screen_id == "arena" and focused not in ("handoffs", "arena-status"):
        focused = "terminal-" + getattr(session, "terminal_slot", "left")
    return screen_layout(session.screen_id, rows, cols, focused,
                         getattr(session, "zoomed", False), getattr(session.options, "scope", True))


def visual(renderer, session, rect, title="VISUAL PREVIEW", animation=None, parameters=None, subtitle=None):
    animation = animation or session.animation
    spec = ANIMATION_BY_ID[animation]
    p = renderer.canvas.panel(rect, title, spec.title)
    p.put(0, 0, subtitle or spec.caption, "muted")
    params = dict(session.visual_parameters()) if hasattr(session, "visual_parameters") else {}
    if parameters:
        params.update(parameters)
    if getattr(session, "stream_view", False):
        params.pop("pulse_bpm", None)
    for (x, y), pixel in render_animation(animation, session.visual_time, p.width,
                                         max(0, p.height - 2), session.protocol, params).items():
        p.put(y + 1, x, pixel.char, pixel.tone, pixel.bold)
    p.put(p.height - 1, 0, "G graphics / " + mode_label(session) + " INPUTS / " + getattr(session, "intensity", "calm").upper(), "muted")


def draw_pixels(panel, pixels, row=0):
    for (x, y), pixel in pixels.items():
        panel.put(y + row, x, pixel.char, pixel.tone, pixel.bold)


def text_at(pixels, x, y, text, width, height, tone="main", bold=False):
    for offset, char in enumerate(safe(text)):
        if 0 <= x + offset < width and 0 <= y < height:
            pixels[x + offset, y] = Pixel(char, tone, bold)


def edge(pixels, first, second, tone="muted", char="."):
    for x, y in line_cells(*first, *second):
        pixels[x, y] = Pixel(char, tone)


def event_tape(renderer, session, rect, title="EVENT TAPE", topic=None, page=None):
    snapshot = data(session)
    redacted = getattr(session, "stream_view", False)
    events = tuple(event for event in (snapshot.events if snapshot else ())
                   if (topic is None or event.topic.startswith(topic))
                   and (page is None or event.payload.get("page") == page or event.payload.get("path") == page)
                   and (not redacted or not event.topic.startswith("body.")
                        and not str(event.payload.get("key", "")).startswith("body.")))
    p = renderer.canvas.panel(rect, title, mode_label(session))
    if not events:
        p.put(0, 0, "No page-scoped events received." if page is not None else "No matching events received.", "muted")
        p.put(1, 0, "Sources remain independent of visual pause.", "muted")
        return
    for row, event in enumerate(events[-p.height:]):
        detail = "[PAYLOAD HIDDEN]" if redacted else event.payload.get("path") or event.payload.get("page") or event.payload.get("key") or event.payload.get("summary") or event.payload.get("status") or ""
        suffix = " " + number(event.payload.get("value")) if "value" in event.payload and not redacted else ""
        p.put(row, 0, "{:04d} {} {} {}{}".format(event.sequence, event.mode.upper(), event.topic, safe(detail), suffix),
              "warn" if "fail" in event.topic or event.payload.get("status") == "failed" else "accent")


def cockpit(renderer, session, rows, cols):
    renderer.cockpit(session, rows, cols)


def visual_lab(renderer, session, rows, cols):
    header(renderer, session, rows, cols)
    redacted = getattr(session, "stream_view", False)
    selected = getattr(session, "lab_binding", "online")
    binding = BINDING_BY_ID.get(selected, BINDINGS[0])
    config = session.current_binding_config
    private = redacted and config.source_key.startswith("body.")
    frame = getattr(session, "binding_frame", None)
    if frame is None or frame.config != config or private:
        frame = BindingEngine().update(data(session), 0, config, arena=getattr(session, "arena", None), stream_view=redacted)
    custom = getattr(session, "binding_config", None) is not None
    for panel_id, rect in panels(session, rows, cols):
        if panel_id == "preview":
            visual(renderer, session, rect, "PATCHBAY / PREVIEW",
                   subtitle=("CUSTOM" if custom else binding.title) + " -> " + config.parameter)
        elif panel_id == "bindings":
            p = renderer.canvas.panel(rect, "SIGNAL PATCHBAY", "L settings / V built-ins")
            p.put(0, 0, "CUSTOM PATCH" if custom else "SELECTED / " + binding.title, "main", True)
            row = wrapped(p, 1, config.source_key + " -> " + config.parameter, "accent", 2)
            stride = 3 if p.height >= 23 else 2 if p.height >= 14 else 1
            count = max(0, (p.height - row - 3) // stride)
            index = next((index for index, item in enumerate(BINDINGS) if item.id == selected), 0)
            start = max(0, index - max(1, count) + 1)
            for item in BINDINGS[start:start + count]:
                chosen = item.id == selected and not custom
                p.put(row, 0, ("> " if chosen else "  ") + item.title, "main" if chosen else "muted", chosen, chosen)
                if stride > 1:
                    p.put(row + 1, 2, item.config.source_key, "accent" if chosen else "muted")
                if stride > 2:
                    p.put(row + 2, 2, "-> " + item.config.parameter, "muted")
                row += stride
            p.put(p.height - 2, 0, "L edit source / ranges / EMA", "accent")
            p.put(p.height - 1, 0, "V built-ins / S save / P commands", "muted")
        elif panel_id == "signals":
            p = renderer.canvas.panel(rect, "BINDING INSPECTOR", frame.mode.upper() + " / " + frame.quality.upper())
            p.put(0, 0, config.source_key, "main", True)
            if private:
                p.put(1, 0, "PRIVATE / STREAM VIEW", "hot", True)
                wrapped(p, 3, "Raw, mapped and smoothed personal values remain hidden.", "muted")
                continue
            p.put(1, 0, "RAW " + number(frame.raw) + " " + (frame.unit or "") + " / " + frame.mode.upper() + " / " + frame.quality.upper(), "hot", True)
            p.put(2, 0, "MAPPED " + number(frame.transformed) + " / EMA " + number(frame.smoothed), "accent")
            row = wrapped(p, 3, "INPUT {}..{} -> OUTPUT {}..{}".format(number(config.input_min), number(config.input_max),
                           number(config.output_min), number(config.output_max)), "muted", 2)
            row = wrapped(p, row, "{} / tau {}s / clamp {} / missing {}".format(config.normalization.upper(),
                           number(config.smoothing_seconds), "ON" if config.clamp else "OFF", config.missing), "accent", 2)
            row = wrapped(p, row, "TARGET " + config.parameter, "main", 1)
            source = "[HIDDEN]" if redacted else frame.source_id or "--"
            row = wrapped(p, row, "SOURCE " + source, "muted", 2)
            if not redacted and frame.source_session_id:
                row = wrapped(p, row, "SESSION " + frame.source_session_id, "muted", 2)
            if frame.age_seconds is not None:
                row = wrapped(p, row, "AGE {:.1f}s{}".format(frame.age_seconds,
                              " / {}s window".format(frame.window_seconds) if frame.window_seconds else ""), "muted", 1)
            if frame.missing_reason:
                row = wrapped(p, row, frame.missing_reason, "warn", 2)
            wrapped(p, row + 1, "Raw cards stay exact; EMA changes only the picture. L settings / Z zoom.", "muted", 3)


def agent_card(renderer, session, rect, slot):
    arena = getattr(session, "arena", None)
    terminals = getattr(session, "terminal_slots", ())
    terminal = next((item for item in terminals if item.id == slot), None)
    recorded = getattr(session, "screen_id", None) == "replay" or arena is not None and arena.mode == "replay"
    if not recorded and terminal is not None and (getattr(session, "terminal_visible", False) or arena is not None and arena.mode == "watch"):
        renderer.shell(session, rect, slot=slot)
        return
    card_index = 0 if slot == "left" else 1
    card = arena.cards[card_index] if arena is not None and len(arena.cards) > card_index else None
    mode = arena.mode.upper() if arena is not None else "WATCH"
    name = card.name if card else "CODEX" if slot == "left" else "CLAUDE"
    p = renderer.canvas.panel(rect, name.upper() + " / " + slot.upper(), mode)
    if card is None:
        if recorded:
            p.put(0, 0, "NO RECORDED AGENT STATE", "main", True)
            wrapped(p, 2, "Terminal content was not recorded. Replay never attaches a live process.", "muted")
            return
        p.put(0, 0, "INDEPENDENT TERMINAL / NOT STARTED", "main", True)
        p.put(2, 0, "F2 enters selected terminal; Ctrl+] returns.", "accent")
        wrapped(p, 4, "Run each CLI in its own slot. Switching screens keeps the process alive. Watch mode sends no automatic prompts.", "muted")
        return
    p.put(0, 0, card.role.upper() + " / " + card.status, "main", True)
    if getattr(session, "stream_view", False):
        wrapped(p, 2, "STREAM VIEW / MANAGED OUTPUT HIDDEN", "accent")
        p.put(p.height - 1, 0, "Role and state remain visible; content is private.", "muted")
        return
    if p.height < 10:
        wrapped(p, 1, card.summary or ("No public reply recorded." if recorded else "Waiting for an explicit task."), "accent", max(1, p.height - 2))
        p.put(p.height - 1, 0, "REPLAY / NO LIVE ACTIONS" if recorded else "DEMO / NO MODEL CALLS" if arena.mode == "demo" else "COST " + (str(card.cost) if card.cost is not None else "unknown"), "muted")
        return
    p.put(1, 0, "SESSION " + (card.session_id or "--"), "muted")
    usage = " / ".join(str(key) + "=" + str(value) for key, value in card.usage) if card.usage else "unknown"
    p.put(2, 0, "USAGE " + usage, "muted")
    p.put(3, 0, "COST " + (str(card.cost) + " (reported)" if card.cost is not None else "unknown"), "muted")
    row = wrapped(p, 5, card.summary or ("No public reply recorded." if recorded else "Waiting for an explicit task."), "accent", 5)
    events = [event for event in arena.events if event.actor in (card.id, card.name.lower())]
    visual_h = min(12, p.height // 2) if p.height >= 20 else 0
    limit = max(0, p.height - row - visual_h - 3)
    for event in events[-limit:] if limit else ():
        row = wrapped(p, row + 1, "{:03d} {} / {}".format(event.sequence, event.kind, event.text),
                      "warn" if "fail" in event.kind else "muted", 2)
        if row >= p.height - visual_h - 2:
            break
    if visual_h:
        params = dict(session.visual_parameters()) if hasattr(session, "visual_parameters") else {}
        for (x, y), pixel in render_animation("reactor", session.visual_time + card_index * 2.1,
                                             p.width, visual_h, session.protocol, params).items():
            p.put(p.height - visual_h - 1 + y, x, pixel.char, pixel.tone, pixel.bold)
    p.put(p.height - 1, 0, "RECORDED PUBLIC OUTPUT / NO LIVE ACTIONS" if recorded else "DEMO TRANSCRIPT / NO MODEL CALLS" if arena.mode == "demo" else "VISIBLE OUTPUT / NO HIDDEN THOUGHTS", "muted")


def handoffs(renderer, session, rect):
    snapshot = getattr(session, "arena", None)
    recorded = getattr(session, "screen_id", None) == "replay" or snapshot is not None and snapshot.mode == "replay"
    p = renderer.canvas.panel(rect, "RECORDED HANDOFFS" if recorded else "HANDOFF BRIDGE", "READ-ONLY" if recorded else "! stop mission")
    if snapshot is None:
        p.put(0, 0, "WATCH / independent human-owned terminals", "main")
        p.put(1, 0, "No automatic message relay is active.", "muted")
        return
    p.put(0, 0, "{} / {} / HANDOFF {}/{} / {:.0f}s".format(snapshot.mode.upper(), snapshot.status,
          len(snapshot.handoffs), snapshot.max_handoffs, snapshot.elapsed) + " / " + getattr(snapshot, "phase", "idle").upper()
          + " / FIX {}/2".format(getattr(snapshot, "fix_cycles", 0)), "main", True)
    if snapshot.handoffs:
        handoff = snapshot.handoffs[-1]
        p.put(1, 0, "#{} {} -> {} / {} / {}".format(handoff.sequence, handoff.sender.upper(),
              handoff.recipient.upper(), handoff.type, handoff.status.upper()), "hot", True)
        if getattr(session, "stream_view", False):
            p.put(2, 0, "STREAM VIEW / MESSAGE AND ARTIFACT CONTENT HIDDEN", "muted")
            return
        row = wrapped(p, 2, handoff.summary, "accent", 2)
        if handoff.artifacts:
            row = wrapped(p, row, "ARTIFACTS " + ", ".join(str(item) for item in handoff.artifacts), "muted", 1)
        wrapped(p, row, "EXPECT " + handoff.expected_result, "muted", 2)
    else:
        wrapped(p, 1, "STREAM VIEW / DETAILS HIDDEN" if getattr(session, "stream_view", False)
                else snapshot.reason or "Handoffs appear only after a completed, validated message.", "muted")


def arena(renderer, session, rows, cols):
    header(renderer, session, rows, cols)
    for panel_id, rect in panels(session, rows, cols):
        if panel_id.startswith("terminal-"):
            agent_card(renderer, session, rect, panel_id.rsplit("-", 1)[1])
        elif panel_id == "handoffs":
            handoffs(renderer, session, rect)
        else:
            p = renderer.canvas.panel(rect, "MISSION CONTROL", "BOUNDED")
            snapshot = getattr(session, "arena", None)
            p.put(0, 0, "DEMO / WATCH / RELAY / MISSION", "main", True)
            row = wrapped(p, 2, "PROFILES: implement + review; parallel tasks; propose + critique.", "accent")
            if snapshot:
                row = wrapped(p, row + 1, "STATE " + snapshot.status + " / " + ("VERIFIED" if snapshot.verified else "NOT VERIFIED"), "main")
                redacted = getattr(session, "stream_view", False)
                row = wrapped(p, row + 1, "STREAM VIEW / DETAILS HIDDEN" if redacted else snapshot.reason or "Every transfer has a message ID and an explicit result.", "muted")
                for artifact in snapshot.artifacts[-3:] if not redacted else ():
                    row = wrapped(p, row + 1, artifact.kind.upper() + " " + artifact.id + " #" + artifact.sha256[:12], "accent", 2)
            wrapped(p, row + 1, "! stop / no new turns; active turns receive cancellation. Input has one owner.", "muted")


def audience_pixels(pages, total, elapsed, width, height, selected_path=None):
    """Stable page nodes and aggregate clouds, never synthetic session paths."""
    if width < 8 or height < 3:
        return {}
    pixels, cx, cy = {}, width // 2, height // 2
    selected = list(sorted(pages, key=lambda item: item[0]))[:8]
    chosen = next((item for item in pages if item[0] == selected_path), None)
    if chosen is not None and chosen not in selected:
        selected[-1] = chosen
    positions = []
    for index, (path, count) in enumerate(selected):
        phase = math.tau * index / max(1, len(selected)) - math.pi / 2
        x = round(cx + math.cos(phase) * max(2, width * .32))
        y = round(cy + math.sin(phase) * max(1, height * .32))
        positions.append((x, y))
        edge(pixels, (cx, cy), (x, y))
        if total is not None and total > 0:
            dots = min(25, math.ceil(count / max(1, total / 100)))
            for point in range(dots):
                angle = point * 2.399963 + elapsed * .12
                radius = 1.2 + math.sqrt(point) * .7
                px, py = round(x + math.cos(angle) * radius * 1.6), round(y + math.sin(angle) * radius * .6)
                if 0 <= px < width and 0 <= py < height:
                    pixels[px, py] = Pixel("*" if point < 2 else ".", "accent" if point % 3 else "hot", point < 2)
    for (path, count), (x, y) in zip(selected, positions):
        label = "[{} {}]".format(safe(path, 16), number(count))
        text_at(pixels, max(0, min(width - len(label), x - len(label) // 2)), y, label, width, height,
                "hot" if path == selected_path else "main", True)
    text_at(pixels, max(0, cx - 6), cy, "[PAGE MAP]", width, height, "accent", True)
    return pixels


def history_chart(panel, samples, row, height):
    if height <= 0 or panel.width <= 0:
        return
    samples = tuple(samples)[-panel.width:]
    values = [sample.value for sample in samples if sample.value is not None]
    if not values:
        panel.put(row, 0, "NO MEASURED HISTORY", "muted")
        return
    low, high = min(values), max(values)
    span = high - low or 1
    for x, sample in enumerate(samples):
        if sample.value is not None:
            y = round((height - 1) * (1 - (sample.value - low) / span))
            panel.put(row + y, x, "+", "muted" if sample.quality != "ok" else "main")


def site(renderer, session, rows, cols):
    header(renderer, session, rows, cols)
    snapshot, online = data(session), metric(session, "site.active_sessions_60s")
    active_available = online is not None and online.value is not None and online.quality in ("ok", "stale")
    # Pages are retained in the snapshot for history/replay. They do not carry
    # an independent freshness clock, so never portray them as active traffic
    # after the corresponding online sample has expired or become unavailable.
    page_data = snapshot.pages if snapshot and active_available else ()
    selected_index = getattr(session, "site_index", 0) % len(page_data) if page_data else 0
    selected_page = page_data[selected_index] if page_data else None
    selected_path = selected_page[0] if selected_page else None
    redacted = getattr(session, "stream_view", False)
    if redacted:
        page_data = tuple(("PAGE-{:02d}".format(index + 1), count) for index, (_, count) in enumerate(page_data))
    selected_label = page_data[selected_index][0] if page_data else None
    for panel_id, rect in panels(session, rows, cols):
        if panel_id == "audience":
            p = renderer.canvas.panel(rect, "AUDIENCE ORBIT", "AGGREGATES")
            p.put(0, 0, number(online.value if online else None) + " sessions / 60s / " +
                  (online.mode.upper() + " / " + online.quality.upper() if online else "UNAVAILABLE"), "main", True)
            if page_data:
                total = online.value if online else None
                if p.height < 7:
                    for row, (path, count) in enumerate(sorted(page_data, key=lambda item: -item[1])[:max(0, p.height - 2)], 1):
                        p.put(row, 0, ("> " if path == selected_label else "  ") + safe(path) + " : " + number(count) + " sessions",
                              "hot" if path == selected_label else "accent")
                else:
                    draw_pixels(p, audience_pixels(page_data, total, session.visual_time,
                                                   p.width, max(0, p.height - 4), selected_label), 1)
                    p.put(p.height - 2, 0, "SELECTED " + safe(selected_label) + " / " + number(selected_page[1]) + " sessions / arrows browse", "accent")
                scale = max(1, math.ceil(total / 100)) if total is not None else None
                p.put(p.height - 1, 0, "1 dot ~ {} sessions / page topology, not geography".format(number(scale)), "muted")
            else:
                expired_pages = bool(snapshot and snapshot.pages and not active_available)
                p.put(2, 0, "CURRENT PAGE ACTIVITY UNAVAILABLE" if expired_pages else "NO PAGE AGGREGATES RECEIVED", "main", True)
                wrapped(p, 4, "Previous aggregates are retained in history. Active page counts stay hidden until an available online sample arrives."
                        if expired_pages else "Connect --source-file or --source-url. Page nodes reflect actual aggregate labels; no visitor trajectories are inferred.", "muted")
        elif panel_id == "site-metrics":
            p = renderer.canvas.panel(rect, "SITE METRICS", mode_label(session))
            p.put(0, 0, "ACTIVE SESSIONS / 60s", "main", True)
            p.put(1, 0, number(online.value if online else None), "hot", True)
            row = source_line(p, 2, online, redacted)
            if selected_page:
                row = wrapped(p, row, "PAGE " + safe(selected_label), "main", 2)
                row = wrapped(p, row, number(selected_page[1]) + " sessions / selected aggregate", "hot", 1)
            for key, title in (("site.pageviews_per_min", "VIEWS / MIN"), ("site.requests_per_sec", "REQUESTS / SEC"),
                               ("site.active_users_30m", "ACTIVE USERS / 30m")):
                sample = metric(session, key)
                if row < p.height:
                    p.put(row, 0, title + " " + number(sample.value if sample else None), "accent")
                    row += 1
            for path, count in sorted(page_data, key=lambda item: -item[1])[:max(0, p.height - row - 2)]:
                p.put(row + 1, 0, ("> " if path == selected_label else "  ") + safe(path, max(1, p.width - 10)), "hot" if path == selected_label else "muted")
                p.put(row + 1, max(0, p.width - 7), number(count), "main")
                row += 1
            p.put(p.height - 1, 0, "UP/DOWN pages / top-N may be partial", "muted")
        else:
            event_tape(renderer, session, rect, "SELECTED PAGE EVENTS" if selected_path else "SITE EVENT TAPE",
                       None if selected_path else "site.", page=selected_path)


def body(renderer, session, rows, cols):
    header(renderer, session, rows, cols)
    snapshot, heart = data(session), metric(session, "body.heart_rate")
    redacted = getattr(session, "stream_view", False)
    if redacted:
        heart = None
    valid = heart is not None and heart.value is not None and heart.quality in ("ok", "stale")
    for panel_id, rect in panels(session, rows, cols):
        if panel_id == "pulse":
            params = {"pulse_bpm": heart.value, "energy": .7} if valid else {"energy": .2, "pulse_bpm": 60}
            visual(renderer, session, rect, "BIOLINK / ARTISTIC PULSE", "reactor", params,
                   "STREAM VIEW / PERSONAL SIGNAL HIDDEN" if redacted else "BPM-DRIVEN VISUAL / NOT ECG" if valid else "NO SIGNAL / NEUTRAL IDLE / NOT ECG")
        elif panel_id == "body-metrics":
            p = renderer.canvas.panel(rect, "BODY SIGNAL", mode_label(session))
            p.put(0, 0, "PRIVATE / STREAM VIEW" if redacted else number(heart.value if heart else None) + " BPM", "hot", True)
            if redacted:
                wrapped(p, 2, "BPM, RR intervals and sensor details are hidden.", "muted")
                continue
            row = source_line(p, 2, heart)
            p.put(row, 0, "CONTACT " + (snapshot.heart_contact.upper() if snapshot else "UNKNOWN"), "accent")
            row = wrapped(p, row + 2, "TIME BASIS: " + (snapshot.heart_timing_basis if snapshot else "unknown"), "muted", 2)
            row = wrapped(p, row + 1, "RR: {} measured intervals".format(len(snapshot.rr_intervals) if snapshot else 0), "main")
            wrapped(p, row + 1, "BPM controls a decorative pulse. This is not a measured ECG waveform.", "muted")
        else:
            p = renderer.canvas.panel(rect, "MEASURED RR INTERVALS", "milliseconds")
            intervals = snapshot.rr_intervals if snapshot and not redacted else ()
            if redacted:
                p.put(0, 0, "STREAM VIEW / MEASURED RR VALUES HIDDEN", "muted")
            elif not intervals:
                p.put(0, 0, "NO RR SAMPLES / BPM alone does not create measured beats", "muted")
            else:
                low, high = min(intervals), max(intervals)
                p.put(0, 0, "{}..{} ms / {} samples / timing: {}".format(number(low), number(high), len(intervals), snapshot.heart_timing_basis), "muted")
                for index, interval in enumerate(intervals[-max(1, p.width):]):
                    x = round(index * max(0, p.width - 1) / max(1, min(len(intervals), p.width) - 1))
                    y = 1 + round(max(0, p.height - 2) * (1 - (interval - low) / (high - low or 1)))
                    p.put(min(p.height - 1, y), x, "+", "main", True)


def repository_graph(repository, width, height, selected_path=None):
    if repository is None or not repository.files or width < 8 or height < 3:
        return {}
    groups = {}
    for item in repository.files:
        directory = item.path.split("/", 1)[0] if "/" in item.path else "."
        groups.setdefault(directory, []).append(item)
    selected = list(sorted(groups.items()))[:8]
    selected_group = selected_path.split("/", 1)[0] if selected_path and "/" in selected_path else "."
    if selected_path and selected_group in groups and selected_group not in (name for name, _ in selected):
        selected[-1] = (selected_group, groups[selected_group])
    pixels, cx, cy = {}, width // 2, height // 2
    for index, (name, files) in enumerate(selected):
        angle = math.tau * index / max(1, len(selected))
        x, y = round(cx + math.cos(angle) * width * .27), round(cy + math.sin(angle) * height * .29)
        edge(pixels, (cx, cy), (x, y), "muted")
        visible_files = list(files[:12])
        chosen = next((item for item in files if item.path == selected_path), None)
        if chosen is not None and chosen not in visible_files:
            visible_files[-1] = chosen
        for j, item in enumerate(visible_files):
            phase = j * 2.399963
            fx, fy = round(x + math.cos(phase) * (2 + math.sqrt(j)) * 1.5), round(y + math.sin(phase) * (1 + math.sqrt(j) * .4))
            if 0 <= fx < width and 0 <= fy < height:
                focused = item.path == selected_path
                pixels[fx, fy] = Pixel("@" if focused else "*" if item.changed else ".", "hot" if focused or item.changed else "muted", focused or item.changed)
        label = "[{} {}]".format(safe(name, 14), len(files))
        text_at(pixels, max(0, min(width - len(label), x - len(label) // 2)), y, label, width, height,
                "hot" if name == selected_group and selected_path else "main", bool(name == selected_group and selected_path))
    text_at(pixels, max(0, cx - 5), cy, "[GIT TREE]", width, height, "accent", True)
    return pixels


def asset_graph(catalog, width, height, selected_id=None):
    """Draw only declared graph edges; include the selected node's neighbours."""
    if catalog is None or width < 8 or height < 3:
        return {}
    limit = max(6, min(24, width * height // 80))
    wanted = [selected_id] if selected_id else []
    for relation in catalog.edges:
        if selected_id in (relation.source, relation.target):
            wanted.extend((relation.source, relation.target))
    wanted.extend(node.id for node in catalog.nodes)
    identifiers = tuple(dict.fromkeys(identifier for identifier in wanted if identifier is not None))[:limit]
    by_id = {node.id: node for node in catalog.nodes}
    nodes = tuple(by_id[identifier] for identifier in identifiers if identifier in by_id)
    kinds = ("identity", "revision", "rendition", "approval", "verify", "consumer")
    positions, pixels = {}, {}
    for column, kind in enumerate(kinds):
        siblings = tuple(node for node in nodes if node.kind == kind)
        for index, node in enumerate(siblings):
            positions[node.id] = (round(2 + column * max(0, width - 5) / 5),
                                   round(1 + (index + 1) * max(0, height - 3) / (len(siblings) + 1)))
    for relation in catalog.edges:
        if relation.source in positions and relation.target in positions:
            edge(pixels, positions[relation.source], positions[relation.target], "accent" if selected_id in (relation.source, relation.target) else "muted")
    abbreviations = {"identity": "ID", "revision": "REV", "rendition": "MODEL", "approval": "APPR", "verify": "HASH", "consumer": "USE"}
    for node in nodes:
        if node.id not in positions:
            continue
        x, y = positions[node.id]
        label = (">" if node.id == selected_id else "[") + abbreviations.get(node.kind, "?") + ("<" if node.id == selected_id else "]")
        text_at(pixels, max(0, min(width - len(label), x - len(label) // 2)), y, label,
                width, height, "hot" if node.id == selected_id else "main", node.id == selected_id)
    return pixels


def _atlas_entries(session, repository, catalog):
    if getattr(session, "atlas_view", "assets") == "repository":
        return tuple(sorted(repository.files, key=lambda item: (not item.changed, item.path))) if repository else ()
    return catalog.nodes if catalog else ()


def _selected_inspection(session, identifier):
    inspection = getattr(session, "atlas_inspection", None)
    selected = getattr(inspection, "selection", None)
    return inspection if selected is not None and selected.id == identifier else None


def _atlas_facts(panel, inspection, row, repository_mode):
    if inspection is None:
        return wrapped(panel, row, "UNINSPECTED / ENTER opens source + diff", "muted", 2)
    row = wrapped(panel, row, inspection.provenance, "accent" if inspection.state == "ready" else "warn", 2)
    if inspection.error:
        return wrapped(panel, row, inspection.error, "warn", 3)
    if inspection.pending:
        return wrapped(panel, row, "Worker is collecting bounded local evidence; input stays responsive.", "muted", 3)
    if inspection.state != "ready":
        return row
    if inspection.size_bytes is not None:
        row = wrapped(panel, row, "BYTES " + number(inspection.size_bytes) + (" / BINARY" if inspection.binary else " / UTF-8 PREVIEW"), "main", 2)
    row = wrapped(panel, row, "SHA256 " + (inspection.sha256 if inspection.hash_complete else "UNKNOWN / not fully hashed"), "accent", 3)
    if not repository_mode:
        row = wrapped(panel, row, inspection.verification, "warn" if "MISMATCH" in inspection.verification else "main", 3)
    if inspection.truncated:
        row = wrapped(panel, row, "BOUNDED PREVIEW / TRUNCATED", "warn", 1)
    if repository_mode:
        row = wrapped(panel, row, "DIFF PREVIEW / staged {} lines / working {} lines".format(
                      len(inspection.staged_diff.splitlines()), len(inspection.unstaged_diff.splitlines())), "muted", 2)
    if inspection.state == "ready" and inspection.consistent:
        row = wrapped(panel, row, "H prepares editable agent context; nothing is sent automatically.", "muted", 2)
    return row


def atlas(renderer, session, rows, cols):
    header(renderer, session, rows, cols)
    repository_mode = getattr(session, "atlas_view", "assets") == "repository"
    snapshot = data(session)
    repository = snapshot.repository if snapshot else None
    redacted = getattr(session, "stream_view", False)
    catalog = getattr(session, "asset_catalog", None)
    load = getattr(session, "atlas_inspection", None)
    expected = getattr(session, "asset_manifest_expected", False)
    manifest_pending = expected and (catalog is None or catalog.origin == "fixture" or
        getattr(getattr(load, "selection", None), "kind", None) == "manifest" and load.state != "ready")
    if catalog is None and not expected:
        catalog = AtlasCatalog.from_protocol(session.protocol)
    if manifest_pending:
        catalog = None
    entries = _atlas_entries(session, repository, catalog)
    index = getattr(session, "atlas_index", 0) % len(entries) if entries else 0
    selected = entries[index] if entries else None
    identifier = selected.path if repository_mode and selected else selected.id if selected else None
    inspection = _selected_inspection(session, identifier)
    origin = "GIT" if repository_mode else "LOCAL MANIFEST" if (catalog and catalog.origin != "fixture" or expected) else "DEMO FIXTURE"
    for panel_id, rect in panels(session, rows, cols):
        if panel_id == "atlas-graph":
            p = renderer.canvas.panel(rect, "REPOVERSE" if repository_mode else "EKZA ASSET ATLAS",
                                      "D assets / repository")
            if repository_mode:
                p.put(0, 0, "DIRECTORY EDGES / REAL GIT SNAPSHOT" if repository else "NO REPOSITORY SNAPSHOT", "muted")
                if redacted:
                    wrapped(p, 2, "STREAM VIEW / DIRECTORY AND FILE LABELS HIDDEN", "muted")
                    if repository:
                        p.put(4, 0, "{} files / {} changed".format(len(repository.files), repository.changed_files), "main")
                else:
                    draw_pixels(p, repository_graph(repository, p.width, max(0, p.height - 4), identifier), 2)
                    if selected:
                        p.put(1, 0, "SELECTED " + safe(selected.path), "hot", True)
                if repository is None:
                    wrapped(p, 3, "Choose --git-root to collect tracked and untracked files. Directory edges are not inferred dependencies.", "muted")
            else:
                p.put(0, 0, origin + (" / DECLARATIONS" if expected else " / EKZA LIFECYCLE"), "muted")
                if manifest_pending:
                    wrapped(p, 2, "UNAVAILABLE / asset manifest is loading or invalid.", "warn")
                    if load and load.error and not redacted:
                        wrapped(p, 4, load.error, "warn", 3)
                elif redacted:
                    wrapped(p, 2, "STREAM VIEW / ASSET LABELS AND METADATA HIDDEN", "muted")
                elif selected:
                    p.put(1, 0, "SELECTED " + selected.kind.upper() + " / " + safe(selected.label), "hot", True)
                    draw_pixels(p, asset_graph(catalog, p.width, max(0, p.height - 4), identifier), 2)
            p.put(p.height - 1, 0, "UP/DOWN select / ENTER inspect / H context / P export", "muted")
        elif panel_id == "atlas-details":
            p = renderer.canvas.panel(rect, "SELECTION / PROVENANCE", origin)
            if redacted:
                wrapped(p, 0, "STREAM VIEW / SELECTED CONTENT AND PATHS HIDDEN", "muted")
                continue
            if repository_mode:
                if repository:
                    p.put(0, 0, repository.mode.upper() + " / " + repository.quality.upper(), "main", True)
                    p.put(1, 0, "BRANCH [HIDDEN]" if redacted else "BRANCH " + safe(repository.branch) + " #" + safe(repository.revision), "accent")
                    p.put(2, 0, "{} files / {} changed".format(len(repository.files), repository.changed_files), "main")
                    if selected:
                        row = wrapped(p, 4, selected.status + " " + selected.path, "hot", 3)
                        _atlas_facts(p, inspection, row, True)
                    if repository.error and not redacted:
                        wrapped(p, p.height - 2, repository.error, "warn", 2)
                else:
                    p.put(0, 0, "UNAVAILABLE / no Git source", "muted")
            else:
                row = wrapped(p, 0, origin + (" / UNAVAILABLE" if manifest_pending else " / DECLARED GRAPH"), "main", 2)
                if selected:
                    row = wrapped(p, row, selected.kind.upper() + " / " + selected.label, "hot", 2)
                    row = wrapped(p, row, "ID " + selected.id, "muted", 2)
                    priority = ("status", "scope", "consumer", "profile", "revision", "declared_size_bytes", "declared_sha256", "verification", "execution")
                    values = dict(selected.metadata)
                    for key in priority:
                        if key in values and row < p.height - 1:
                            row = wrapped(p, row, key.upper().replace("_", " ") + " " + str(values[key]), "accent" if key in ("status", "scope", "execution") else "muted", 2)
                    _atlas_facts(p, inspection, row, False)
                elif load and load.error:
                    wrapped(p, row, load.error, "warn", 3)
            p.put(p.height - 1, 0, "ENTER full source/diff / H prepare / P export", "muted")
        else:
            p = renderer.canvas.panel(rect, "ATLAS NODES / UP-DOWN", "{} / {}".format(index + 1 if entries else 0, len(entries)))
            if redacted:
                wrapped(p, 0, "STREAM VIEW / NODE LABELS HIDDEN", "muted")
            elif not entries:
                wrapped(p, 0, "UNAVAILABLE / no selected source nodes", "muted")
            else:
                count = max(1, p.height - 1)
                start = max(0, index - count // 2)
                start = min(start, max(0, len(entries) - count))
                for row, item in enumerate(entries[start:start + count]):
                    chosen = start + row == index
                    label = item.status + " " + item.path if repository_mode else item.kind.upper() + " / " + item.label
                    p.put(row, 0, ("> " if chosen else "  ") + safe(label), "hot" if chosen else "muted", chosen, chosen)
                p.put(p.height - 1, 0, "Approval is consumer-scoped; no consumer is executed." if not repository_mode else "ENTER inspect / H prepare agent context / P export", "muted")


class _RecordedArenaView:
    """Read-only view adapter; archived output can never attach a live PTY."""

    def __init__(self, session, snapshot):
        self._session, self.arena = session, snapshot
        self.screen_id, self.terminal_slots = "replay", ()
        self.terminal_visible = self.terminal_focused = False

    def __getattr__(self, name):
        return getattr(self._session, name)


def replay_arena_stage(renderer, session, rect):
    from .terminal import Rect
    archive = getattr(session, "replay_arena", None)
    p = renderer.canvas.panel(rect, "RECORDED AGENT ARENA", "REPLAY / NO LIVE ACTIONS")
    if archive is None or archive.status == "NO_RECORDED_STATE":
        p.put(0, 0, "NO RECORDED ARENA STATE", "main", True)
        wrapped(p, 2, "Terminal content was not recorded. This view never connects a live terminal or asks an agent to continue.", "muted")
        return
    p.put(0, 0, archive.status + " / " + ("RECORDED VERIFIED" if archive.verified else "NOT VERIFIED") +
          " / phase " + getattr(archive, "phase", "unknown"), "main", True)
    view = _RecordedArenaView(session, archive)
    if p.height < 10 or p.width < 70:
        row = 1
        for card in archive.cards[:2]:
            p.put(row, 0, card.name.upper() + " / " + card.status, "accent", True)
            row += 1
            if row < p.height - 1:
                row = wrapped(p, row, "STREAM VIEW / PUBLIC TEXT HIDDEN" if session.stream_view else card.summary or "No public reply recorded.", "muted", 1)
        if archive.handoffs and row < p.height - 1:
            handoff = archive.handoffs[-1]
            wrapped(p, row, handoff.sender.upper() + " -> " + handoff.recipient.upper() + " / " + handoff.status, "hot", 1)
        p.put(p.height - 1, 0, "REPLAY / NO PTY / P stage / Z zoom", "muted")
        return
    inner = Rect(rect.y + 2, rect.x + 1, rect.height - 3, rect.width - 2)
    bridge_height = 5 if inner.height >= 17 else 3
    card_height = inner.height - bridge_height
    left = (inner.width - 1) // 2
    agent_card(renderer, view, Rect(inner.y, inner.x, card_height, left), "left")
    agent_card(renderer, view, Rect(inner.y, inner.x + left + 1, card_height, inner.width - left - 1), "right")
    handoffs(renderer, view, Rect(inner.y + card_height, inner.x, bridge_height, inner.width))


def _failed_event(event):
    return ("fail" in event.topic or event.payload.get("result") in ("failed", "error")
            or event.payload.get("exit_code") not in (None, 0))


def replay(renderer, session, rows, cols):
    header(renderer, session, rows, cols)
    replay_state = getattr(session, "replay_status", None)
    recording = getattr(session, "recording_status", None)
    snapshot = data(session)
    events = snapshot.events if snapshot else ()
    redacted = getattr(session, "stream_view", False)
    archive = getattr(session, "replay_arena", None)
    presentation = getattr(session, "replay_presentation", None)
    bookmarks = getattr(session, "replay_bookmarks", ()) or getattr(replay_state, "bookmarks", ())
    source_screen = getattr(presentation, "source_screen", None) or getattr(replay_state, "source_screen", "cockpit")
    if redacted:
        events = tuple(event for event in events if not event.topic.startswith("body.")
                       and not str(event.payload.get("key", "")).startswith("body."))
    for panel_id, rect in panels(session, rows, cols):
        if panel_id == "replay-stage":
            if getattr(session, "replay_stage", "visual") == "arena" and replay_state:
                replay_arena_stage(renderer, session, rect)
            else:
                visual(renderer, session, rect, "FLIGHT RECORDER / STAGE",
                       subtitle="REPLAY / SOURCE " + safe(source_screen).upper() + " / NO LIVE ACTIONS" if replay_state else "CURRENT VISUAL / NOT A REPLAY")
        elif panel_id == "replay-timeline":
            p = renderer.canvas.panel(rect, "TIMELINE / BOOKMARKS", "[ ] seek / , . speed")
            duration = replay_state.duration if replay_state else max(1, snapshot.elapsed if snapshot else 0)
            head = replay_state.playhead if replay_state else duration
            p.put(0, 0, "{:.1f}/{:.1f}s / {} / x{:g}".format(head, duration,
                  "PLAYING" if replay_state and replay_state.playing else "PAUSED" if replay_state else "PREVIEW", replay_state.speed if replay_state else 1), "main", True)
            if p.height >= 3:
                track_row = 1 if p.height < 5 else 2
                p.put(track_row, 0, "-" * p.width, "muted")
                markers = {}
                for event in events:
                    offset = event.recorded_offset_ms / 1000 if event.recorded_offset_ms is not None else 0
                    x = round(min(1, offset / max(.001, duration)) * max(0, p.width - 1))
                    glyph = "x" if _failed_event(event) else ">" if "handoff" in event.topic else "o"
                    priority = 3 if glyph == "x" else 2 if glyph == ">" else 0
                    if priority >= markers.get(x, (-1, "", ""))[0]:
                        markers[x] = (priority, glyph, "warn" if glyph == "x" else "accent")
                for bookmark in bookmarks:
                    x = round(min(1, bookmark.offset / max(.001, duration)) * max(0, p.width - 1))
                    glyph = "x" if bookmark.kind == "failure" else ">" if bookmark.kind == "handoff" else "*" if bookmark.kind == "surge" else "M"
                    priority = 4 if glyph == "x" else 3
                    if priority >= markers.get(x, (-1, "", ""))[0]:
                        markers[x] = (priority, glyph, "warn" if glyph == "x" else "hot")
                for x, (_, glyph, tone) in sorted(markers.items()):
                    p.put(track_row, x, glyph, tone, True)
                x = round(min(1, head / max(.001, duration)) * max(0, p.width - 1))
                p.put(track_row, x, "|", "hot", True)
                if p.height >= 5 and bookmarks:
                    marker = next((item for item in bookmarks if item.offset >= head), bookmarks[-1])
                    label = marker.kind.upper() if redacted else marker.label
                    p.put(3, 0, "MARKER {:.1f}s / ".format(marker.offset) + safe(label), "accent")
            p.put(p.height - 1, 0, "SPACE play / [ ] seek / , . speed / M bookmark / P export", "muted")
        else:
            p = renderer.canvas.panel(rect, "RECORDER / CUES", "EXPLICIT OPT-IN")
            row = 0
            if recording:
                row = wrapped(p, row, "RECORDING" if recording.active else "RECORDER STOPPED", "hot" if recording.active else "main")
                row = wrapped(p, row, "{} events / {:.1f} KiB / {} queued".format(recording.events, recording.bytes_written / 1024, recording.pending), "accent", 2)
                if recording.error and not getattr(session, "stream_view", False):
                    row = wrapped(p, row, recording.error, "warn", 2)
            if replay_state:
                row = wrapped(p, row, "REPLAY / {} events / seed {}".format(replay_state.event_count, replay_state.seed), "main")
                row = wrapped(p, row, "NO LIVE ACTIONS / seeks never execute recorded commands.", "muted", 2)
                row = wrapped(p, row, "SOURCE SCREEN " + safe(source_screen).upper() + " / STAGE " +
                              getattr(session, "replay_stage", "visual").upper(), "accent", 2)
                if presentation is not None:
                    row = wrapped(p, row, "RECORDED VISUAL CLOCK " + ("PAUSED" if presentation.paused else "RUNNING"), "muted", 1)
                row = wrapped(p, row, "{} bookmarks / P > Replay stage or export capsule".format(len(bookmarks)), "muted", 2)
                if archive is not None:
                    row = wrapped(p, row, "RECORDED CHECKS / " + ("VERIFIED" if archive.verified else "NOT VERIFIED"), "main", 1)
                    for report in getattr(archive, "validation_reports", ())[-3:]:
                        row = wrapped(p, row, report.status + " / EXIT " + number(report.exit_code) +
                                      " / HASH " + report.artifact_hash[:12], "accent" if report.exit_code == 0 else "warn", 2)
            if not replay_state and not recording:
                row = wrapped(p, row, "Record normalized events with --record FILE; open them with --replay FILE.", "main", 3)
                row = wrapped(p, row + 1, "Terminal content not recorded. Prompts and body signals require separate opt-in categories.", "muted", 3)
            limit = max(0, p.height - row - 1)
            for event in events[-limit:] if limit else ():
                label = "REPLAY / ORIGIN " + (event.origin_mode or event.mode).upper() if replay_state else event.mode.upper()
                row = wrapped(p, row + 1, label + " " + event.topic, "accent", 1)
