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
             "scope", "scope_mode", "scope_auto_cycle", "seed", "intensity", "lab_binding", "arena_mode", "data_mode", "binding_config",
             "visual_time", "protocol_time", "paused", "director_enabled", "consumer"}


def config_path():
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "devos" / "settings.json"


def validate(document):
    from .modes import MODE_BY_ID
    from .screen_registry import SCREEN_BY_ID
    from .visuals import ANIMATION_BY_ID
    from .bindings import BindingConfig, BINDING_BY_ID
    from .protocol import CONSUMER_IDS
    from .math_scope import SCOPE_BY_ID
    if (not isinstance(document, dict) or type(document.get("schema_version", 1)) is not int
            or document.get("schema_version", 1) != 1):
        raise ValueError("Preset must be an object with schema_version 1")
    if document.get("format") == "devos-scene-capsule":
        presentation = document.get("presentation")
        if not isinstance(presentation, dict) or not isinstance(presentation.get("parameters"), dict):
            raise ValueError("Scene capsule requires presentation parameters")
        flattened = {key: value for key, value in presentation["parameters"].items() if key in SAFE_KEYS}
        for key in ("visual_time", "protocol_time", "paused"):
            if key in presentation:
                flattened[key] = presentation[key]
        flattened["seed"] = document.get("seed", flattened.get("seed", 42))
        return validate(flattened)
    result = {key: value for key, value in document.items() if key in SAFE_KEYS}
    if isinstance(result.get("visual"), dict):
        visual = result["visual"]
        if "seed" in visual:
            result["seed"] = visual["seed"]
        result["visual"] = visual.get("id")
    choices = {"screen": SCREEN_BY_ID, "scene": MODE_BY_ID, "visual": ANIMATION_BY_ID,
               "theme": ("auto", "neon", "matrix", "ice", "ember", "amber"),
               "background": ("terminal", "black"), "intensity": ("calm", "cinematic", "showcase"),
               "lab_binding": BINDING_BY_ID, "consumer": CONSUMER_IDS, "scope_mode": SCOPE_BY_ID,
               "arena_mode": ("demo", "watch"), "data_mode": ("auto", "demo", "live")}
    for key, values in choices.items():
        if key in result and (not isinstance(result[key], str) or result[key] not in values):
            raise ValueError("Unknown preset " + key + ": " + str(result[key]))
    for key, lower, upper in (("fps_cap", 10, 120), ("speed", .25, 8), ("auto_cycle", 0, 86400),
                              ("scope_auto_cycle", 0, 86400),
                              ("visual_time", 0, 1e12), ("protocol_time", 0, 1e12)):
        value = result.get(key)
        if key in result and (type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper):
            raise ValueError("Invalid preset " + key)
    if "fps_cap" in result and type(result["fps_cap"]) is not int:
        raise ValueError("fps_cap must be an integer")
    if 0 < result.get("scope_auto_cycle", 0) < .25:
        raise ValueError("scope_auto_cycle must be zero or between 0.25 and 86400 seconds")
    if "seed" in result and type(result["seed"]) is not int:
        raise ValueError("Preset seed must be an integer")
    for key in ("scope", "paused", "director_enabled"):
        if key in result and type(result[key]) is not bool:
            raise ValueError("Preset " + key + " must be boolean")
    if "binding_config" in result:
        result["binding_config"] = BindingConfig.from_dict(result["binding_config"]).to_dict()
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
            "scope": session.options.scope, "scope_mode": session.options.scope_mode,
            "scope_auto_cycle": session.options.scope_auto_cycle,
            "seed": session.seed if session.seed is not None else 41,
            "intensity": session.intensity, "lab_binding": session.lab_binding,
            "arena_mode": "demo" if session.arena_mode != "watch" else "watch", "data_mode": session.data_mode,
            "binding_config": session.current_binding_config.to_dict(), "consumer": session.options.consumer,
            "director_enabled": session.director_enabled}


def save(path, document):
    safe = dict(validate(document), schema_version=1)
    return _atomic_save(path, safe)


def _atomic_save(path, document):
    """Write an already validated document without truncating the previous file."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".devos-", delete=False, encoding="utf-8") as stream:
            temporary = Path(stream.name)
            json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return path
