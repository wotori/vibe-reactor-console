"""Selectable visuals, independent of code scenes and the consumer selector."""

from dataclasses import dataclass
import math

from .graphics import Pixel, line_cells, wireframe
from .reactor import render_reactor
from .surfaces import render_surface


@dataclass(frozen=True)
class Animation:
    id: str
    title: str
    description: str


ANIMATIONS = (
    Animation("avatar", "T-POSE AVATAR", "A shaded procedural humanoid on a rotating turntable."),
    Animation("cube", "SOLID CUBE", "A rigid shaded cube, calm rotation and clear faces."),
    Animation("sphere", "ASSET SPHERE", "A shaded globe with a rotating equator and meridians."),
    Animation("protocol", "EKZA ASSET FLOW", "Identity -> revision -> rendition -> approval -> verify -> consumer."),
    Animation("tesseract", "4D TESSERACT", "Two cubic cells, depth-tested edges and fourth-axis links."),
    Animation("reactor", "PLASMA REACTOR", "A luminous plasma core, precessing 3D orbits and particle trails."),
)
ANIMATION_BY_ID = {item.id: item for item in ANIMATIONS}


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


def render_animation(animation, elapsed, width, height, frame):
    if animation == "protocol":
        return render_flow(frame, width, height)
    if animation == "tesseract":
        return wireframe(animation, elapsed, width, height)
    if animation == "reactor":
        return render_reactor(elapsed, width, height)
    return render_surface(animation, elapsed, width, height)


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
