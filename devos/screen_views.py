"""Pure console screen compositions over immutable snapshots.

The application collects data and owns keyboard input. These views only draw;
they never create a terminal, advance an agent, seek a replay or query a source.
"""

import math
import textwrap

from .graphics import Pixel, line_cells
from .screen_registry import SCREEN_BY_ID, screen_layout
from .visuals import ANIMATION_BY_ID, render_animation


BINDINGS = (
    ("online", "ACTIVE SESSIONS", "site.active_sessions_60s", "particle_count", "log1p / clamped density"),
    ("pulse", "BODY PULSE", "body.heart_rate", "pulse_bpm", "linear / 30..240 BPM"),
    ("tests", "TEST SHIELD", "task.test.completed", "test_passed", "run ID / exact outcome"),
    ("queue", "AGENT QUEUES", "agent.handoff.ready", "queue_left", "one satellite / queued task"),
)


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
    if getattr(session, "terminal_focused", False):
        owner = "TERMINAL " + getattr(session, "terminal_slot", "left").upper()
    canvas.put(0, 2, "DEV/OS / " + spec.title, "main", True, cols - 4)
    badge = "INPUT: " + owner + " / " + mode_label(session)
    if cols >= 100:
        canvas.put(0, max(2, cols - len(badge) - 3), badge, "accent", True)
    canvas.put(1, 2, "1 COCKPIT  2 LAB  3 ARENA  4 SITE  5 BODY  6 ATLAS  7 REPLAY", "muted", limit=cols - 4)
    status = "STREAM VIEW / PRIVATE CONTENT HIDDEN" if getattr(session, "stream_view", False) else getattr(session, "status_message", "")
    hint = status or "P commands / O focus / Z zoom / ? help"
    if cols < 100:
        hint = badge + " | " + hint
    canvas.put(2, 2, safe(hint), "accent" if status else "muted", limit=cols - 4)


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


def event_tape(renderer, session, rect, title="EVENT TAPE", topic=None):
    snapshot = data(session)
    redacted = getattr(session, "stream_view", False)
    events = tuple(event for event in (snapshot.events if snapshot else ())
                   if (topic is None or event.topic.startswith(topic))
                   and (not redacted or not event.topic.startswith("body.")
                        and not str(event.payload.get("key", "")).startswith("body.")))
    p = renderer.canvas.panel(rect, title, mode_label(session))
    if not events:
        p.put(0, 0, "No matching events received.", "muted")
        p.put(1, 0, "Sources remain independent of visual pause.", "muted")
        return
    for row, event in enumerate(events[-p.height:]):
        detail = "[PAYLOAD HIDDEN]" if redacted else event.payload.get("key") or event.payload.get("summary") or event.payload.get("status") or ""
        suffix = " " + number(event.payload.get("value")) if "value" in event.payload and not redacted else ""
        p.put(row, 0, "{:04d} {} {} {}{}".format(event.sequence, event.mode.upper(), event.topic, safe(detail), suffix),
              "warn" if "fail" in event.topic or event.payload.get("status") == "failed" else "accent")


def cockpit(renderer, session, rows, cols):
    renderer.cockpit(session, rows, cols)


def visual_lab(renderer, session, rows, cols):
    header(renderer, session, rows, cols)
    redacted = getattr(session, "stream_view", False)
    selected = getattr(session, "lab_binding", "online")
    binding = next((item for item in BINDINGS if item[0] == selected), BINDINGS[0])
    for panel_id, rect in panels(session, rows, cols):
        if panel_id == "preview":
            visual(renderer, session, rect, "PATCHBAY / PREVIEW",
                   subtitle=binding[1] + " -> " + binding[3])
        elif panel_id == "bindings":
            p = renderer.canvas.panel(rect, "SIGNAL PATCHBAY", "V cycle binding")
            row = 0
            for identifier, title, key, target, transform in BINDINGS:
                chosen = identifier == selected
                p.put(row, 0, ("> " if chosen else "  ") + title, "main" if chosen else "muted", chosen, chosen)
                p.put(row + 1, 2, key, "accent" if chosen else "muted")
                p.put(row + 2, 2, "-> " + target, "hot" if chosen else "muted")
                row += 4
            if p.height >= 20:
                wrapped(p, row, "Bindings change the picture. Metric cards keep exact source values.", "muted")
        elif panel_id == "signals":
            p = renderer.canvas.panel(rect, "BINDING INSPECTOR", mode_label(session))
            sample = metric(session, binding[2])
            p.put(0, 0, binding[2], "main", True)
            snapshot = data(session)
            topics = (("task.test.completed", "demo.build.finished", "build.finished", "test.finished") if selected == "tests"
                      else ("agent.handoff.ready", "agent.handoff.updated") if selected == "queue" else (binding[2],))
            matching = tuple(event for event in (snapshot.events if snapshot else ()) if event.topic in topics)
            if selected == "pulse" and redacted:
                p.put(1, 0, "PRIVATE / STREAM VIEW", "hot", True)
                row = 3
            elif selected in ("tests", "queue") and matching:
                event = matching[-1]
                outcome = event.payload.get("status", event.payload.get("exit_code", "received"))
                p.put(1, 0, safe(outcome), "hot", True)
                row = wrapped(p, 2, event.mode.upper() + " / EVENT / source: " + ("[HIDDEN]" if redacted else event.source_id), "muted", 2)
                row = wrapped(p, row, "ID " + ("[HIDDEN]" if redacted else event.id), "muted", 2)
            else:
                p.put(1, 0, number(sample.value if sample else None) + (" " + sample.unit if sample else " / UNAVAILABLE"), "hot", True)
                row = source_line(p, 2, sample, redacted)
            row = wrapped(p, row, binding[4], "accent")
            params = dict(session.visual_parameters()) if hasattr(session, "visual_parameters") else {}
            for key in ANIMATION_BY_ID[session.animation].parameters:
                if row >= p.height:
                    break
                p.put(row, 0, key + " = " + ("[PRIVATE]" if redacted and key == "pulse_bpm" else number(params.get(key))), "muted")
                row += 1
            if row < p.height:
                wrapped(p, row, "Missing: neutral idle, exact value --. No automatic LIVE -> DEMO fallback.", "muted")


def agent_card(renderer, session, rect, slot):
    arena = getattr(session, "arena", None)
    terminals = getattr(session, "terminal_slots", ())
    terminal = next((item for item in terminals if item.id == slot), None)
    if terminal is not None and (getattr(session, "terminal_visible", False) or arena is not None and arena.mode == "watch"):
        renderer.shell(session, rect, slot=slot)
        return
    card_index = 0 if slot == "left" else 1
    card = arena.cards[card_index] if arena is not None and len(arena.cards) > card_index else None
    mode = arena.mode.upper() if arena is not None else "WATCH"
    name = card.name if card else "CODEX" if slot == "left" else "CLAUDE"
    p = renderer.canvas.panel(rect, name.upper() + " / " + slot.upper(), mode)
    if card is None:
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
        wrapped(p, 1, card.summary or "Waiting for an explicit task.", "accent", max(1, p.height - 2))
        p.put(p.height - 1, 0, "DEMO / NO MODEL CALLS" if arena.mode == "demo" else "COST " + (str(card.cost) if card.cost is not None else "unknown"), "muted")
        return
    p.put(1, 0, "SESSION " + (card.session_id or "--"), "muted")
    usage = " / ".join(str(key) + "=" + str(value) for key, value in card.usage) if card.usage else "unknown"
    p.put(2, 0, "USAGE " + usage, "muted")
    p.put(3, 0, "COST " + (str(card.cost) + " (reported)" if card.cost is not None else "unknown"), "muted")
    row = wrapped(p, 5, card.summary or "Waiting for an explicit task.", "accent", 5)
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
    p.put(p.height - 1, 0, "DEMO TRANSCRIPT / NO MODEL CALLS" if arena.mode == "demo" else "VISIBLE OUTPUT / NO HIDDEN THOUGHTS", "muted")


def handoffs(renderer, session, rect):
    snapshot = getattr(session, "arena", None)
    p = renderer.canvas.panel(rect, "HANDOFF BRIDGE", "! stop mission")
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


def audience_pixels(pages, total, elapsed, width, height):
    """Stable page nodes and aggregate clouds, never synthetic session paths."""
    if width < 8 or height < 3:
        return {}
    pixels, cx, cy = {}, width // 2, height // 2
    selected = tuple(sorted(pages, key=lambda item: item[0]))[:8]
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
        text_at(pixels, max(0, min(width - len(label), x - len(label) // 2)), y, label, width, height, "main", True)
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
    redacted = getattr(session, "stream_view", False)
    if redacted:
        page_data = tuple(("PAGE-{:02d}".format(index + 1), count) for index, (_, count) in enumerate(page_data))
    for panel_id, rect in panels(session, rows, cols):
        if panel_id == "audience":
            p = renderer.canvas.panel(rect, "AUDIENCE ORBIT", "AGGREGATES")
            p.put(0, 0, number(online.value if online else None) + " sessions / 60s / " +
                  (online.mode.upper() + " / " + online.quality.upper() if online else "UNAVAILABLE"), "main", True)
            if page_data:
                total = online.value if online else None
                if p.height < 7:
                    for row, (path, count) in enumerate(sorted(page_data, key=lambda item: -item[1])[:max(0, p.height - 2)], 1):
                        p.put(row, 0, safe(path) + " : " + number(count) + " sessions", "accent")
                else:
                    draw_pixels(p, audience_pixels(page_data, total, session.visual_time,
                                                   p.width, max(0, p.height - 3)), 1)
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
            for key, title in (("site.pageviews_per_min", "VIEWS / MIN"), ("site.requests_per_sec", "REQUESTS / SEC"),
                               ("site.active_users_30m", "ACTIVE USERS / 30m")):
                sample = metric(session, key)
                if row < p.height:
                    p.put(row, 0, title + " " + number(sample.value if sample else None), "accent")
                    row += 1
            for path, count in sorted(page_data, key=lambda item: -item[1])[:max(0, p.height - row - 2)]:
                p.put(row + 1, 0, safe(path, max(1, p.width - 8)), "muted")
                p.put(row + 1, max(0, p.width - 7), number(count), "main")
                row += 1
            p.put(p.height - 1, 0, "Top pages may be a partial aggregate.", "muted")
        else:
            event_tape(renderer, session, rect, "SITE EVENT TAPE", "site.")


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


def repository_graph(repository, width, height):
    if repository is None or not repository.files or width < 8 or height < 3:
        return {}
    groups = {}
    for item in repository.files:
        directory = item.path.split("/", 1)[0] if "/" in item.path else "."
        groups.setdefault(directory, []).append(item)
    selected = tuple(sorted(groups.items()))[:8]
    pixels, cx, cy = {}, width // 2, height // 2
    for index, (name, files) in enumerate(selected):
        angle = math.tau * index / max(1, len(selected))
        x, y = round(cx + math.cos(angle) * width * .27), round(cy + math.sin(angle) * height * .29)
        edge(pixels, (cx, cy), (x, y), "muted")
        for j, item in enumerate(files[:12]):
            phase = j * 2.399963
            fx, fy = round(x + math.cos(phase) * (2 + math.sqrt(j)) * 1.5), round(y + math.sin(phase) * (1 + math.sqrt(j) * .4))
            if 0 <= fx < width and 0 <= fy < height:
                pixels[fx, fy] = Pixel("*" if item.changed else ".", "hot" if item.changed else "muted", item.changed)
        label = "[{} {}]".format(safe(name, 14), len(files))
        text_at(pixels, max(0, min(width - len(label), x - len(label) // 2)), y, label, width, height)
    text_at(pixels, max(0, cx - 5), cy, "[GIT TREE]", width, height, "accent", True)
    return pixels


def atlas(renderer, session, rows, cols):
    header(renderer, session, rows, cols)
    repository_mode = getattr(session, "atlas_view", "assets") == "repository"
    snapshot = data(session)
    repository = snapshot.repository if snapshot else None
    redacted = getattr(session, "stream_view", False)
    frame = session.protocol
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
                    draw_pixels(p, repository_graph(repository, p.width, max(0, p.height - 2)), 1)
                if repository is None:
                    wrapped(p, 3, "Choose --git-root to collect tracked and untracked files. Directory edges are not inferred dependencies.", "muted")
            else:
                p.put(0, 0, "DEMO FIXTURE / EKZA LIFECYCLE / " + frame.consumer.title.upper(), "muted")
                if p.height < 7:
                    wrapped(p, 1, "[ID] -> [REV] -> [MODEL] -> [APPROVE] -> [SHA256] -> [USE]", "main", max(1, p.height - 2))
                else:
                    draw_pixels(p, render_animation("protocol", session.visual_time, p.width,
                                                   max(0, p.height - 2), frame), 1)
                p.put(p.height - 1, 0, "Approval applies to a consumer/profile and exact bytes.", "muted")
        elif panel_id == "atlas-details":
            p = renderer.canvas.panel(rect, "SELECTION / PROVENANCE", "GIT" if repository_mode else "FIXTURE")
            if repository_mode:
                if repository:
                    p.put(0, 0, repository.mode.upper() + " / " + repository.quality.upper(), "main", True)
                    p.put(1, 0, "BRANCH [HIDDEN]" if redacted else "BRANCH " + safe(repository.branch) + " #" + safe(repository.revision), "accent")
                    p.put(2, 0, "{} files / {} changed".format(len(repository.files), repository.changed_files), "main")
                    for row, item in enumerate(sorted(repository.files, key=lambda item: (not item.changed, item.path))[:max(0, p.height - 5)] if not redacted else (), 4):
                        p.put(row, 0, item.status + " " + safe(item.path), "hot" if item.changed else "muted")
                    if repository.error and not redacted:
                        wrapped(p, p.height - 2, repository.error, "warn", 2)
                else:
                    p.put(0, 0, "UNAVAILABLE / no Git source", "muted")
            else:
                items = ("DEMO FIXTURE / " + frame.kind.upper(), frame.identity,
                         "REVISION " + frame.revision, "SHA256 " + frame.digest,
                         "CONSUMER " + frame.consumer.project_id, "PROFILE " + frame.profile,
                         frame.explanation)
                row = 0
                for index, item in enumerate(items):
                    row = wrapped(p, row, item, "main" if index == 0 else "accent" if index in (2, 4) else "muted", 2)
        else:
            if repository_mode:
                event_tape(renderer, session, rect, "WORKSPACE EVENTS", "git.")
            else:
                p = renderer.canvas.panel(rect, "PROTOCOL CONTRACT", "DEMO FIXTURE")
                wrapped(p, 0, "IDENTITY -> IMMUTABLE REVISION -> RENDITION -> APPROVAL -> SHA256 VERIFY -> CONSUMER", "main")
                wrapped(p, 2, "SPACE + MIRROR: avatars / OMOBA: avatars + weapons. Actual consumer execution is separate.", "muted")


def replay(renderer, session, rows, cols):
    header(renderer, session, rows, cols)
    replay_state = getattr(session, "replay_status", None)
    recording = getattr(session, "recording_status", None)
    snapshot = data(session)
    events = snapshot.events if snapshot else ()
    if getattr(session, "stream_view", False):
        events = tuple(event for event in events if not event.topic.startswith("body.")
                       and not str(event.payload.get("key", "")).startswith("body."))
    for panel_id, rect in panels(session, rows, cols):
        if panel_id == "replay-stage":
            visual(renderer, session, rect, "FLIGHT RECORDER / STAGE",
                   subtitle="REPLAY / NO LIVE ACTIONS" if replay_state else "CURRENT VISUAL / NOT A REPLAY")
        elif panel_id == "replay-timeline":
            p = renderer.canvas.panel(rect, "TIMELINE", "[ ] seek / SPACE play")
            duration = replay_state.duration if replay_state else max(1, snapshot.elapsed if snapshot else 0)
            head = replay_state.playhead if replay_state else duration
            p.put(0, 0, "{:.1f}/{:.1f}s / {} / x{:g}".format(head, duration,
                  "PLAYING" if replay_state and replay_state.playing else "PAUSED" if replay_state else "PREVIEW", replay_state.speed if replay_state else 1), "main", True)
            if p.height >= 3:
                p.put(2, 0, "-" * p.width, "muted")
                for event in events:
                    offset = event.recorded_offset_ms / 1000 if event.recorded_offset_ms is not None else 0
                    x = round(min(1, offset / max(.001, duration)) * max(0, p.width - 1))
                    glyph = "x" if "fail" in event.topic else ">" if "handoff" in event.topic else "o"
                    p.put(2, x, glyph, "warn" if glyph == "x" else "accent")
                x = round(min(1, head / max(.001, duration)) * max(0, p.width - 1))
                p.put(2, x, "|", "hot", True)
            p.put(p.height - 1, 0, "NO LIVE ACTIONS / terminal text is not recorded by default", "muted")
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
            if not replay_state and not recording:
                row = wrapped(p, row, "Record normalized events with --record FILE; open them with --replay FILE.", "main", 3)
                row = wrapped(p, row + 1, "Terminal content not recorded. Prompts and body signals require separate opt-in categories.", "muted", 3)
            limit = max(0, p.height - row - 1)
            for event in events[-limit:] if limit else ():
                label = "REPLAY / ORIGIN " + (event.origin_mode or event.mode).upper() if replay_state else event.mode.upper()
                row = wrapped(p, row + 1, label + " " + event.topic, "accent", 1)
