"""Selectable visuals, independent of code scenes and the consumer selector."""

from dataclasses import dataclass
import math
from types import MappingProxyType

from .effects import (render_asset_orrery, render_gravity_well, render_packet_garden,
                      render_torus_knot, render_twin_core, render_voxel_tide)
from .graphics import Pixel, line_cells, wireframe
from .reactor import render_reactor
from .surfaces import render_surface


@dataclass(frozen=True)
class VisualSpec:
    id: str
    title: str
    description: str
    renderer: object = None
    caption: str = ""
    min_width: int = 5
    min_height: int = 3
    parameters: tuple = ()


Animation = VisualSpec  # Compatibility for integrations using the original name.


def render_flow(frame, width, height):
    if width < 10 or height < 5:
        return {}
    pixels = {}

    def text(x, y, value, tone="muted", bold=False):
        for i, char in enumerate(value):
            if 0 <= x + i < width and 0 <= y < height:
                pixels[x + i, y] = Pixel(char, tone, bold)

    # Clockwise path through two rows; it fits even in a compact sidebar.
    columns = [round((width - 1) * fraction) for fraction in (.16, .50, .84)]
    top, bottom = max(1, height // 4), min(height - 2, height * 3 // 4)
    positions = [(columns[0], top), (columns[1], top), (columns[2], top),
                 (columns[2], bottom), (columns[1], bottom), (columns[0], bottom)]
    for index, start in enumerate(positions):
        end = positions[(index + 1) % len(positions)]
        for x, y in line_cells(*start, *end):
            text(x, y, "-" if start[1] == end[1] else "|", "accent" if index < frame.stage else "muted")
    start, end = positions[frame.stage], positions[(frame.stage + 1) % len(positions)]
    x = round(start[0] + (end[0] - start[0]) * frame.fraction)
    y = round(start[1] + (end[1] - start[1]) * frame.fraction)
    text(x, y, "@", "hot", True)
    labels = ("ID", "REV", "MODEL", "APPROVE", "SHA256", "USE")
    for index, ((x, y), label) in enumerate(zip(positions, labels)):
        label = "[" + label + "]"
        tone = "main" if index == frame.stage else "accent" if index < frame.stage else "muted"
        text(x - len(label) // 2, y, label, tone, index == frame.stage)
        if height >= 11:
            text(x - 1, y + 1, "{:02d}".format(index + 1), "muted")
    return pixels


def surface_renderer(kind):
    def render(elapsed, width, height, frame, parameters):
        # The binding changes the turntable, not the underlying source value.
        energy = parameters.get("energy", .5)
        try:
            energy = max(0, min(1, float(energy))) if math.isfinite(float(energy)) else .5
        except (TypeError, ValueError, OverflowError):
            energy = .5
        return render_surface(kind, elapsed * (.5 + energy), width, height)
    return render


def flow_renderer(elapsed, width, height, frame, parameters):
    return render_flow(frame, width, height)


def tesseract_renderer(elapsed, width, height, frame, parameters):
    return wireframe("tesseract", elapsed, width, height)


def reactor_renderer(elapsed, width, height, frame, parameters):
    return render_reactor(elapsed, width, height, parameters)


ANIMATIONS = (
    VisualSpec("avatar", "T-POSE AVATAR", "A shaded procedural humanoid on a rotating turntable.",
               surface_renderer("avatar"), "HUMANOID / T-POSE / VERIFIED ASSET", parameters=("energy",)),
    VisualSpec("cube", "SOLID CUBE", "A rigid shaded cube, calm rotation and clear faces.",
               surface_renderer("cube"), "SOLID CUBE / FLAT SHADING / 12 TRIANGLES", parameters=("energy",)),
    VisualSpec("sphere", "ASSET SPHERE", "A shaded globe with a rotating equator and meridians.",
               surface_renderer("sphere"), "ASSET SPHERE / LIGHT + NORMALS + UV GRID", parameters=("energy",)),
    VisualSpec("protocol", "EKZA ASSET FLOW", "Identity -> revision -> rendition -> approval -> verify -> consumer.",
               flow_renderer, "IDENTITY / REVISION / RENDITION / APPROVAL / VERIFY / USE", 10, 5),
    VisualSpec("tesseract", "4D TESSERACT", "Two cubic cells, depth-tested edges and fourth-axis links.",
               tesseract_renderer, "16 VERTICES / 32 EDGES / SO(4)"),
    VisualSpec("reactor", "PLASMA REACTOR", "A luminous plasma core, precessing 3D orbits and particle trails.",
               reactor_renderer, "PLASMA CORE / 3 ORBITS / DEPTH + PARTICLE TRAILS",
               parameters=("energy", "particle_count", "pulse_bpm")),
    VisualSpec("gravity-well", "GRAVITY WELL", "An opaque horizon, luminous accretion disk and falling tracers.",
               render_gravity_well, "EVENT HORIZON / ACCRETION / LENS ARCS",
               parameters=("energy", "particle_count", "burst_age")),
    VisualSpec("torus-knot", "TORUS KNOT", "A shaded trefoil tube with test fronts and luminous tracers.",
               render_torus_knot, "TREFOIL / SHADED TUBE / TRACER FLOW",
               parameters=("energy", "particle_count", "test_passed", "test_total")),
    VisualSpec("packet-garden", "PACKET GARDEN", "Events grow a bounded deterministic tree of living branches.",
               render_packet_garden, "EVENT SEEDS / BOUNDED BRANCHES / LIVING GRAPH",
               parameters=("particle_count", "event_sequence", "seed")),
    VisualSpec("twin-core", "TWIN CORE", "Two agent cores exchange packets across counterflowing bridges.",
               render_twin_core, "DUAL CORES / MESSAGE BRIDGE / QUEUE SATELLITES",
               parameters=("energy", "pulse_bpm", "event_sequence", "queue_left", "queue_right", "test_passed", "test_total")),
    VisualSpec("voxel-tide", "VOXEL TIDE", "A shaded perspective field of rising voxel columns.",
               render_voxel_tide, "ISOMETRIC FIELD / HEIGHT WAVES / DEPTH",
               parameters=("energy",)),
    VisualSpec("asset-orrery", "ASSET ORRERY", "Asset cubes and immutable revisions on stable nested orbits.",
               render_asset_orrery, "ASSET IDENTITIES / REVISION SATELLITES / ORBITS",
               parameters=("energy", "particle_count", "event_sequence")),
)
ANIMATION_BY_ID = {item.id: item for item in ANIMATIONS}


def render_animation(animation, elapsed, width, height, frame, parameters=None):
    """Render a catalog entry using an immutable copy of the current bindings."""
    spec = ANIMATION_BY_ID[animation]
    if width < spec.min_width or height < spec.min_height:
        return {}
    return spec.renderer(elapsed, width, height, frame, MappingProxyType(dict(parameters or {})))


def scope_pixels(elapsed, width, height):
    if height <= 0:
        return {}
    pixels = {(x, height // 2): Pixel(".", "muted") for x in range(0, width, 2)}
    for x in range(width):
        phase = x * .26 - elapsed * math.tau * .65
        for shift, amplitude, char, tone in ((math.pi / 2, .28, "+", "accent"), (0, .45, "~", "main")):
            y = round((height - 1) * (.5 - amplitude * math.sin(phase + shift)))
            pixels[x, y] = Pixel(char, tone, tone == "main")
    return pixels
