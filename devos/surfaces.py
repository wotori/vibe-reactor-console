"""Small procedural meshes and an ASCII triangle rasterizer. No 3D assets required."""

from dataclasses import dataclass
from functools import lru_cache
from itertools import product
import math

from .graphics import Pixel


@dataclass(frozen=True)
class Face:
    indices: tuple
    normal: tuple
    tone: str


@dataclass(frozen=True)
class Mesh:
    points: tuple
    faces: tuple


def box(points, faces, low, high, tone="main"):
    offset = len(points)
    points.extend(tuple(high[axis] if bit else low[axis] for axis, bit in enumerate(bits))
                  for bits in product((0, 1), repeat=3))
    sides = (
        ((0, 2, 3, 1), (-1, 0, 0)), ((4, 5, 7, 6), (1, 0, 0)),
        ((0, 1, 5, 4), (0, -1, 0)), ((2, 6, 7, 3), (0, 1, 0)),
        ((0, 4, 6, 2), (0, 0, -1)), ((1, 3, 7, 5), (0, 0, 1)),
    )
    for (a, b, c, d), normal in sides:
        faces.append(Face((offset + a, offset + b, offset + c), normal, tone))
        faces.append(Face((offset + a, offset + c, offset + d), normal, tone))


@lru_cache(maxsize=3)
def mesh_for(kind):
    points, faces = [], []
    if kind == "cube":
        box(points, faces, (-1, -1, -1), (1, 1, 1), "accent")
    elif kind == "avatar":
        box(points, faces, (-.18, 1.50, -.16), (.18, 1.92, .16), "accent")
        box(points, faces, (-.10, 1.35, -.10), (.10, 1.50, .10), "muted")
        box(points, faces, (-.30, .65, -.17), (.30, 1.35, .17), "main")
        box(points, faces, (-.28, .37, -.16), (.28, .65, .16), "accent")
        for sign in (-1, 1):
            for x0, x1, y0, y1, depth, tone in (
                (.30, .73, 1.11, 1.33, .11, "main"),
                (.73, 1.14, 1.15, 1.30, .085, "accent"),
                (1.14, 1.30, 1.16, 1.29, .07, "hot"),
                (.09, .285, -.85, .37, .13, "main"),
            ):
                a, b = sorted((sign * x0, sign * x1))
                box(points, faces, (a, y0, -depth), (b, y1, depth), tone)
            a, b = sorted((sign * .075, sign * .30))
            box(points, faces, (a, -1.00, -.14), (b, -.82, .30), "accent")
        # Spin about the body's centre, with a fixed upright T-pose.
        points = [(x, y - .46, z) for x, y, z in points]
    elif kind == "sphere":
        slices, stacks = 28, 14
        for latitude in range(stacks + 1):
            theta = math.pi * latitude / stacks
            for longitude in range(slices):
                phi = math.tau * longitude / slices
                points.append((math.sin(theta) * math.cos(phi), math.cos(theta),
                               math.sin(theta) * math.sin(phi)))
        for row in range(stacks):
            for col in range(slices):
                a, b = row * slices + col, row * slices + (col + 1) % slices
                c, d = a + slices, b + slices
                tone = "hot" if row == stacks // 2 else "accent" if col % 7 == 0 else "main"
                for triangle in ((a, c, d), (a, d, b)):
                    centre = tuple(sum(points[index][axis] for index in triangle) / 3 for axis in range(3))
                    length = math.sqrt(sum(v * v for v in centre))
                    normal = tuple(v / max(.001, length) for v in centre)
                    faces.append(Face(triangle, normal, tone))
    else:
        raise ValueError("unknown surface: " + kind)
    return Mesh(tuple(points), tuple(faces))


def render_surface(kind, elapsed, width, height):
    if width < 3 or height < 3:
        return {}
    mesh = mesh_for(kind)
    yaw = elapsed * (.43 if kind == "sphere" else .60) + (.32 if kind == "cube" else .05)
    pitch = .28 if kind == "cube" else .06
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)

    def turn(point):
        x, y, z = point
        x, z = x * cy - z * sy, x * sy + z * cy
        return x, y * cp - z * sp, y * sp + z * cp

    world = [turn(point) for point in mesh.points]
    projected = [(x * 6 / (8 - z), y * 6 / (8 - z), z) for x, y, z in world]
    ex = max(abs(x) for x, _, _ in projected)
    ey = max(abs(y) for _, y, _ in projected)
    scale = min((width - 2) / (4 * ex), (height - 1) / (2 * ey))
    screen = [((width - 1) / 2 + x * scale * 2, (height - 1) / 2 - y * scale, z)
              for x, y, z in projected]
    pixels, depths = {}, {}
    ramp = ".:-=+*#%@"
    light = (-.36, .52, .77)
    for face in mesh.faces:
        normal = turn(face.normal)
        centre = tuple(sum(world[index][axis] for index in face.indices) / 3 for axis in range(3))
        if normal[0] * -centre[0] + normal[1] * -centre[1] + normal[2] * (8 - centre[2]) <= 0:
            continue
        intensity = .18 + .82 * max(0, sum(a * b for a, b in zip(normal, light)))
        char = ramp[min(len(ramp) - 1, int(intensity * (len(ramp) - 1)))]
        tone = face.tone if intensity > .28 else "muted"
        a, b, c = (screen[index] for index in face.indices)
        denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(denominator) < 1e-9:
            continue
        x0, x1 = max(0, math.floor(min(a[0], b[0], c[0]))), min(width - 1, math.ceil(max(a[0], b[0], c[0])))
        y0, y1 = max(0, math.floor(min(a[1], b[1], c[1]))), min(height - 1, math.ceil(max(a[1], b[1], c[1])))
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                u = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / denominator
                v = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / denominator
                w = 1 - u - v
                if min(u, v, w) < -1e-7:
                    continue
                depth = 8 - 1 / (u / (8 - a[2]) + v / (8 - b[2]) + w / (8 - c[2]))
                if depth >= depths.get((x, y), -math.inf):
                    depths[x, y] = depth
                    pixels[x, y] = Pixel(char, tone, intensity > .72)
    return pixels
