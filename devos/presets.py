"""Versioned preference-only presets. Loading a preset never executes commands."""

import json
import math
import os
from pathlib import Path
import tempfile


PRESETS = {
    "night-shift": {"screen": "cockpit", "visual": "gravity-well", "theme": "neon", "intensity": "calm"},
    "showcase": {"screen": "visual-lab", "visual": "torus-knot", "theme": "ice", "intensity": "showcase", "auto_cycle": 8},
    "duet": {"screen": "arena", "visual": "twin-core", "theme": "neon", "arena_mode": "demo"},
    "audience": {"screen": "site", "visual": "gravity-well", "theme": "ice", "lab_binding": "online"},
    "biolink": {"screen": "body", "visual": "reactor", "theme": "ember", "lab_binding": "pulse"},
    "atlas": {"screen": "atlas", "visual": "asset-orrery", "theme": "neon"},
}
SAFE_KEYS = {"screen", "scene", "visual", "theme", "background", "fps_cap", "speed", "auto_cycle",
             "scope", "seed", "intensity", "lab_binding", "arena_mode", "data_mode"}


def config_path():
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "devos" / "settings.json"


def validate(document):
    from .modes import MODE_BY_ID
    from .screen_registry import SCREEN_BY_ID
    from .visuals import ANIMATION_BY_ID
    if (not isinstance(document, dict) or type(document.get("schema_version", 1)) is not int
            or document.get("schema_version", 1) != 1):
        raise ValueError("Preset must be an object with schema_version 1")
    result = {key: value for key, value in document.items() if key in SAFE_KEYS}
    if isinstance(result.get("visual"), dict):
        visual = result["visual"]
        if "seed" in visual:
            result["seed"] = visual["seed"]
        result["visual"] = visual.get("id")
    choices = {"screen": SCREEN_BY_ID, "scene": MODE_BY_ID, "visual": ANIMATION_BY_ID,
               "theme": ("auto", "neon", "matrix", "ice", "ember", "amber"),
               "background": ("terminal", "black"), "intensity": ("calm", "cinematic", "showcase"),
               "lab_binding": ("online", "pulse", "tests", "queue"),
               "arena_mode": ("demo", "watch"), "data_mode": ("auto", "demo", "live")}
    for key, values in choices.items():
        if key in result and (not isinstance(result[key], str) or result[key] not in values):
            raise ValueError("Unknown preset " + key + ": " + str(result[key]))
    for key, lower, upper in (("fps_cap", 10, 120), ("speed", .25, 8), ("auto_cycle", 0, 86400)):
        value = result.get(key)
        if key in result and (type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper):
            raise ValueError("Invalid preset " + key)
    if "fps_cap" in result and type(result["fps_cap"]) is not int:
        raise ValueError("fps_cap must be an integer")
    if "seed" in result and type(result["seed"]) is not int:
        raise ValueError("Preset seed must be an integer")
    if "scope" in result and type(result["scope"]) is not bool:
        raise ValueError("Preset scope must be boolean")
    return result


def load(path):
    path = Path(path).expanduser()
    if path.stat().st_size > 65536:
        raise ValueError("Preset exceeds 64 KiB")
    return validate(json.loads(path.read_text(encoding="utf-8")))


def resolve(name):
    return validate(dict(PRESETS[name], schema_version=1)) if name in PRESETS else load(name)


def preferences(session):
    return {"schema_version": 1, "screen": session.screen_id, "scene": session.mode_id,
            "visual": session.animation, "theme": session.options.palette,
            "background": session.background, "fps_cap": session.options.fps,
            "speed": session.speed, "auto_cycle": session.options.auto_cycle,
            "scope": session.options.scope, "seed": session.seed if session.seed is not None else 41,
            "intensity": session.intensity, "lab_binding": session.lab_binding,
            "arena_mode": "demo" if session.arena_mode != "watch" else "watch", "data_mode": session.data_mode}


def save(path, document):
    safe = dict(validate(document), schema_version=1)
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".devos-", delete=False, encoding="utf-8") as stream:
            temporary = Path(stream.name)
            json.dump(safe, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return path
