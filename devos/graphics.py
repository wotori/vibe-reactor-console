"""Pure wireframe geometry: actual 4D rotations and two perspective projections."""

from itertools import product
import math
from typing import NamedTuple


HYPER_CAMERA_DISTANCE = 12.0


class Pixel(NamedTuple):
    char: str
    tone: str
    bold: bool = False


def vertices(dimensions):
    return tuple(product((-1.0, 1.0), repeat=dimensions))


def edges(dimensions):
    """Adjacent vertices of an n-cube differ in exactly one bit."""
    return tuple((i, i ^ (1 << bit))
                 for i in range(1 << dimensions) for bit in range(dimensions)
                 if i < (i ^ (1 << bit)))


def rotate(point, first, second, angle):
    result = list(point)
    c, s = math.cos(angle), math.sin(angle)
    result[first] = point[first] * c - point[second] * s
    result[second] = point[first] * s + point[second] * c
    return tuple(result)


def tesseract_angles(elapsed):
    """Dwell near nested-cube views while continuously turning through 4D."""
    phase = elapsed * 0.16
    return (phase - 0.16 * math.sin(phase * 4),
            0.08 * math.sin(elapsed * 0.09),
            0.04 * math.sin(elapsed * 0.11))


def project(point, elapsed):
    if len(point) == 4:
        xw, zw, xy = tesseract_angles(elapsed)
        point = rotate(point, 0, 3, xw)
        point = rotate(point, 2, 3, zw)
        point = rotate(point, 0, 1, xy)
        # At XW=ZW=0 this is the familiar cube inside a cube, joined at corners.
        factor = 2.2 / (3.0 - point[3])
        point = tuple(coordinate * factor for coordinate in point[:3])
        yaw = 0.50 + 0.10 * math.sin(elapsed * 0.12)
        pitch = 0.28 + 0.06 * math.sin(elapsed * 0.10)
        camera_distance, focal_length = HYPER_CAMERA_DISTANCE, 9.0
    else:
        yaw, pitch = elapsed * 0.31 + 0.5, elapsed * 0.21 + 0.3
        camera_distance, focal_length = 6.0, 4.5
    point = rotate(point, 0, 2, yaw)
    point = rotate(point, 1, 2, pitch)
    factor = focal_length / (camera_distance - point[2])
    return point[0] * factor, point[1] * factor, point[2]


def line_cells(x0, y0, x1, y1):
    """An unbroken ASCII staircase; shallow diagonals keep horizontal runs."""
    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
    return [(round(x0 + (x1 - x0) * i / steps), round(y0 + (y1 - y0) * i / steps))
            for i in range(steps + 1)]


def line_char(before, after):
    dx, dy = after[0] - before[0], after[1] - before[1]
    if dy == 0:
        return "-"
    if dx == 0:
        return "|"
    return "\\" if dx * dy > 0 else "/"


def rasterize(screen, connections, dimensions, width, height, camera_distance=6.0):
    """Resolve crossings per cell with a depth buffer, including vertex markers."""
    pixels, depths = {}, {}

    def plot(x, y, depth, pixel, priority=0):
        if 0 <= x < width and 0 <= y < height:
            key = (round(depth, 8), priority)
            if key >= depths.get((x, y), (-math.inf, -1)):
                depths[x, y] = key
                pixels[x, y] = pixel

    for first, second in connections:
        x0, y0, z0 = screen[first]
        x1, y1, z1 = screen[second]
        cells = line_cells(x0, y0, x1, y1)
        if dimensions == 4:
            # Last coordinate is W: two opposite cubic cells plus eight W struts.
            bridge = (first ^ second) == 1
            tone = "hot" if bridge else "accent" if first & 1 else "main"
            if not bridge and z0 + z1 < -0.5:
                tone = "muted"
        else:
            tone = "accent" if z0 + z1 > 0 else "muted"
        for i, (x, y) in enumerate(cells):
            f = i / max(1, len(cells) - 1)
            # Reciprocal camera distance interpolates linearly after perspective.
            inverse_distance = (1 - f) / (camera_distance - z0) + f / (camera_distance - z1)
            depth = camera_distance - 1 / inverse_distance
            before, after = cells[max(0, i - 1)], cells[min(len(cells) - 1, i + 1)]
            plot(x, y, depth, Pixel(line_char(before, after), tone, depth > 0))
    for index, (x, y, depth) in enumerate(screen):
        outer = dimensions == 4 and (index & 1) == 1
        tone = "muted" if dimensions == 4 and depth < -0.25 else "accent" if outer else "main"
        plot(x, y, depth, Pixel("+" if outer else "o", tone, True), priority=1)
    return pixels


def wireframe(geometry, elapsed, width, height):
    """Fit a perspective projection to the viewport without clipping any vertex."""
    if width < 3 or height < 3:
        return {}
    dimensions = 4 if geometry == "tesseract" else 3
    projected = [project(point, elapsed) for point in vertices(dimensions)]
    extent_x = max(abs(x) for x, _, _ in projected)
    extent_y = max(abs(y) for _, y, _ in projected)
    # Terminal cells are roughly twice as tall as they are wide.
    scale = min((width - 3) / (4 * extent_x), (height - 2) / (2 * extent_y))
    screen = [(round((width - 1) / 2 + x * scale * 2),
               round((height - 1) / 2 - y * scale), z) for x, y, z in projected]
    return rasterize(screen, edges(dimensions), dimensions, width, height,
                     HYPER_CAMERA_DISTANCE if dimensions == 4 else 6.0)


def wave(elapsed, width, height=3):
    """An ASCII oscilloscope trace for the simulated bus clock."""
    pixels = {}
    for x in range(max(0, width)):
        value = math.sin(x * 0.28 - elapsed * 4) * 0.7 + math.sin(x * 0.11 + elapsed) * 0.3
        y = round((height - 1) * (0.5 - value * 0.45))
        pixels[x, y] = Pixel("~", "main")
    return pixels
