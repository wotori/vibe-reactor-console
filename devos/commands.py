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
    commands = [Command("screen:" + item.id, "Screen / " + item.title, item.description, "screen", item.id) for item in SCREENS]
    commands += [Command("visual:" + item.id, "Visual / " + item.title, item.description, "visual", item.id) for item in ANIMATIONS]
    commands += [Command("scene:" + item.id, "Code scene / " + item.title, item.description, "scene", item.id) for item in MODES]
    commands += [Command("preset:" + key, "Preset / " + key, "Apply preferences; no commands are launched.", "preset", key) for key in PRESETS]
    for kind, values in (("binding", ("online", "pulse", "tests", "queue")),
                         ("intensity", ("calm", "cinematic", "showcase")),
                         ("data", ("auto", "demo", "live")), ("arena", ("demo", "watch"))):
        commands += [Command(kind + ":" + value, kind.title() + " / " + value,
                             "Change " + kind + " selection.", kind, value) for value in values]
    actions = (("save", "Save preferences", "Write safe preferences to your config file."),
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
               ("replay-play", "Replay / play or pause", "Replay events without invoking live actions."),
               ("replay-next", "Replay / next failure", "Seek to the next recorded failure."),
               ("help", "Help / shortcuts", "Show controls for the current screen."))
    commands += [Command("action:" + key, title, description, "action", key) for key, title, description in actions]
    return tuple(commands)


def search(query):
    words = query.casefold().split()
    values = [item for item in catalog() if all(word in (item.title + " " + item.description + " " + item.id).casefold() for word in words)]
    return tuple(sorted(values, key=lambda item: (not item.title.casefold().startswith(query.casefold()), item.title))) if query else tuple(values)
