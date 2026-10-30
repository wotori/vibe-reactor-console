"""Discoverable dashboard commands. Descriptors contain no side effects."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    id: str
    title: str
    description: str
    kind: str
    value: str


def catalog():
    from .modes import MODES
    from .presets import PRESETS
    from .screen_registry import SCREENS
    from .visuals import ANIMATIONS
    from .bindings import BINDING_BY_ID
    from .math_scope import SCOPES
    commands = [Command("screen:" + item.id, "Screen / " + item.title, item.description, "screen", item.id) for item in SCREENS]
    commands += [Command("visual:" + item.id, "Visual / " + item.title, item.description, "visual", item.id) for item in ANIMATIONS]
    commands += [Command("scene:" + item.id, "Code scene / " + item.title, item.description, "scene", item.id) for item in MODES]
    commands += [Command("scope:" + item.id, "Math scope / " + item.title, item.description,
                         "scope-mode", item.id) for item in SCOPES]
    commands += [Command("preset:" + key, "Preset / " + key, "Apply preferences; no commands are launched.", "preset", key) for key in PRESETS]
    for kind, values in (("binding", tuple(BINDING_BY_ID)),
                         ("intensity", ("calm", "cinematic", "showcase")),
                         ("data", ("auto", "demo", "live")), ("arena", ("demo", "watch"))):
        commands += [Command(kind + ":" + value, kind.title() + " / " + value,
                             "Change " + kind + " selection.", kind, value) for value in values]
    actions = (("save", "Save preferences", "Write safe preferences to your config file."),
               ("scope-next", "Math scope / next function", "Animate the next mathematical function; synthetic graphics, not measured signals."),
               ("scope-auto", "Math scope / auto-cycle", "Toggle automatic rotation of synthetic mathematical functions."),
               ("scope-zoom", "Math scope / fullscreen", "Open the mathematical panel at full size; Esc returns."),
               ("visual-fullscreen", "Visual / fullscreen", "Expand the current 3D animation; Y or Esc returns."),
               ("source-folder", "Source / choose project folder", "Read random code excerpts from a local project; U opens the path editor."),
               ("binding-settings", "Visual Lab / edit binding", "Edit source, ranges, linear/log transform, smoothing and missing policy."),
               ("director", "Spectator / event director", "Toggle finite event-driven visual changes; terminal focus stays yours."),
               ("capsule-export", "Scene / export capsule", "Save a portable composition without local paths or launch commands."),
               ("inspect-atlas", "Atlas / inspect selected source and diff", "Read the selected node; Git and files are inspected on a worker."),
               ("atlas-context", "Atlas / prepare agent context", "Include the selected source/diff in your next editable managed handoff."),
               ("atlas-export", "Atlas / export selected context", "Write the selected context to a new local text file."),
               ("atlas-next", "Atlas / next node", "Select the next repository file or asset node."),
               ("atlas-prev", "Atlas / previous node", "Select the previous repository file or asset node."),
               ("record", "Recording / start or stop", "Record normalized events; excludes pulse and terminal text."),
               ("stream", "Stream View / toggle", "Hide terminal text, paths and personal measurements."),
               ("zoom", "Panel / zoom", "Expand the selected panel."),
               ("terminal", "Terminal / focus", "Open or focus the selected real shell."),
               ("event", "Demo / next event", "Advance the finite demonstration."),
               ("handoff", "Arena / send handoff", "Send the selected completed managed reply or advance demo."),
               ("mission-pause", "Mission / pause or resume", "Stop dispatching new managed turns."),
               ("mission-stop", "Mission / stop", "Cancel managed turns and prevent new dispatch."),
               ("mission-validate", "Mission / validate selected workspace", "Run the explicitly configured --task against selected isolated artifacts."),
               ("task", "Task / run configured check", "Execute the command explicitly supplied using --task."),
               ("inspect-task", "Task / inspect last result", "Read actual output, exit code and revision."),
               ("inspect-mission", "Arena / inspect artifacts and checks", "Read public results, actual patches and independent check evidence."),
               ("mission-export", "Arena / export public mission report", "Save results, artifacts, check evidence and remaining work."),
               ("replay-play", "Replay / play or pause", "Replay events without invoking live actions."),
               ("replay-next", "Replay / next failure", "Seek to the next recorded failure."),
               ("replay-bookmark", "Replay / add bookmark", "Mark the current playhead."),
               ("replay-next-bookmark", "Replay / next bookmark", "Seek to a manual or automatic marker."),
               ("replay-speed-up", "Replay / faster", "Increase playhead speed; source measurements stay unchanged."),
               ("replay-speed-down", "Replay / slower", "Decrease playhead speed."),
               ("replay-stage", "Replay / toggle Arena reconstruction", "Inspect recorded cards, handoffs and independent check evidence."),
               ("help", "Help / shortcuts", "Show controls for the current screen."))
    commands += [Command("action:" + key, title, description, "action", key) for key, title, description in actions]
    return tuple(commands)


def search(query):
    words = query.casefold().split()
    values = [item for item in catalog() if all(word in (item.title + " " + item.description + " " + item.id).casefold() for word in words)]
    return tuple(sorted(values, key=lambda item: (not item.title.casefold().startswith(query.casefold()), item.title))) if query else tuple(values)
