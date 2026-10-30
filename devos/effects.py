"""Bounded, deterministic ASCII worlds driven by numeric visual parameters.

These renderers consume no files, clocks, subprocesses or source adapters.  Time
and the current bindings are arguments, so a recorded snapshot can be rendered
again at any size without resetting a particle simulation.
"""

from dataclasses import dataclass
from functools import lru_cache
import math

from .graphics import Pixel, line_cells, line_char


CAMERA = 8.0
RAMP = ".:-=+*#%@"


def value(parameters, name, default, low, high):
    """Clamp display inputs, including untrusted JSON NaN/Infinity values."""
    try:
        result = float((parameters or {}).get(name, default))
    except (TypeError, ValueError, OverflowError):
        return default
    return max(low, min(high, result)) if math.isfinite(result) else default


def rotate(point, yaw, pitch):
    x, y, z = point
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    x, z = x * cy + z * sy, -x * sy + z * cy
    return x, y * cp - z * sp, y * sp + z * cp


def rotation(yaw, pitch):
    """Precompute the frame transform for large cached point clouds."""
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)

    def transform(point):
        x, y, z = point
        x, z = x * cy + z * sy, -x * sy + z * cy
        return x, y * cp - z * sp, y * sp + z * cp
    return transform


class DepthCanvas:
    """A terminal-cell-aspect perspective canvas with near-surface occlusion."""

    def __init__(self, width, height, extent=3.2):
        self.width, self.height = width, height
        self.cx, self.cy = (width - 1) / 2, (height - 1) / 2
        self.scale = max(.001, min((width - 2) / (extent * 4), (height - 2) / (extent * 2)))
        self.pixels, self.depths = {}, {}

    def project(self, point):
        x, y, z = point
        factor = CAMERA / max(.5, CAMERA - z)
        return self.cx + x * self.scale * factor * 2, self.cy - y * self.scale * factor, z

    def plot(self, x, y, z, char, tone="main", bold=False):
        x, y = round(x), round(y)
        if 0 <= x < self.width and 0 <= y < self.height and z >= self.depths.get((x, y), -math.inf):
            self.depths[x, y] = z
            self.pixels[x, y] = Pixel(char, tone, bold)

    def point(self, position, char="*", tone="main", bold=False):
        self.plot(*self.project(position), char, tone, bold)

    def line(self, first, second, tone="muted", char=None, bold=False):
        start, end = self.project(first), self.project(second)
        cells = line_cells(round(start[0]), round(start[1]), round(end[0]), round(end[1]))
        glyph = char or line_char(start, end)
        for index, (x, y) in enumerate(cells):
            fraction = index / max(1, len(cells) - 1)
            depth = CAMERA - 1 / ((1 - fraction) / (CAMERA - start[2]) + fraction / (CAMERA - end[2]))
            self.plot(x, y, depth, glyph, tone, bold)

    def sphere(self, centre, radius, elapsed=0, tone="main", hollow=False):
        """Ray-cast the visible sphere, including a black opaque event horizon."""
        px, py, pz = centre
        cx, cy, _ = self.project(centre)
        extent = radius * CAMERA / max(1, CAMERA - pz - radius)
        for y in range(max(0, math.floor(cy - extent * self.scale)),
                       min(self.height, math.ceil(cy + extent * self.scale) + 1)):
            v = (self.cy - y) / self.scale
            for x in range(max(0, math.floor(cx - extent * self.scale * 2)),
                           min(self.width, math.ceil(cx + extent * self.scale * 2) + 1)):
                u = (x - self.cx) / (self.scale * 2)
                # Camera ray = (0,0,CAMERA) + t*(u,v,-CAMERA).
                a = u * u + v * v + CAMERA * CAMERA
                b = -2 * (u * px + v * py + CAMERA * (CAMERA - pz))
                c = px * px + py * py + (CAMERA - pz) ** 2 - radius * radius
                discriminant = b * b - 4 * a * c
                if discriminant < 0:
                    continue
                distance = (-b - math.sqrt(discriminant)) / (2 * a)
                nx, ny, nz = ((u * distance - px) / radius, (v * distance - py) / radius,
                              (CAMERA * (1 - distance) - pz) / radius)
                light = max(0, -.37 * nx + .49 * ny + .79 * nz)
                filament = math.sin(math.atan2(nx, nz) * 6 + ny * 4 - elapsed * 2)
                intensity = min(1, .16 + .72 * light + .12 * filament)
                char = " " if hollow else RAMP[int(intensity * (len(RAMP) - 1))]
                color = "muted" if hollow or light < .2 else "hot" if filament > .85 else tone
                self.plot(x, y, CAMERA * (1 - distance), char, color, light > .8 and not hollow)


@lru_cache(maxsize=16)
def circle(count):
    return tuple((math.cos(math.tau * i / count), math.sin(math.tau * i / count))
                 for i in range(count + 1))


def orbit(radius, phase, tilt, yaw=0):
    return rotate((radius * math.cos(phase), radius * math.sin(phase), 0), yaw, tilt)


def render_gravity_well(elapsed, width, height, frame=None, parameters=None):
    if width < 5 or height < 3:
        return {}
    canvas = DepthCanvas(width, height, 3.35)
    energy = value(parameters, "energy", .55, 0, 1)
    particles = int(value(parameters, "particle_count", 100, 0, 200))
    tilt = 1.04 + .10 * math.sin(elapsed * .11)
    count = min(208, max(64, width * 2))
    radial_count = 5 + particles // 25
    samples = circle(count)
    flow = elapsed * (.6 + energy)
    # A thick disk has real front/back geometry; z-buffering resolves every
    # crossing with the opaque core drawn later.
    for layer in range(radial_count):
        radius = .89 + layer * 1.55 / max(1, radial_count - 1)
        for index, (cosine, sine) in enumerate(samples):
            phase = math.tau * index / count
            ripple = .035 * math.sin(phase * 5 - flow * 3 + layer)
            point = rotate((radius * cosine, radius * sine, ripple), -.22, tilt)
            luminance = (.5 + .5 * math.sin(phase * 3 + radius * 5 - flow * 4))
            hot = layer < 2 and luminance > .55
            char = "@" if hot else "*" if luminance > .75 else ":" if luminance > .3 else "."
            tone = "hot" if hot else "main" if point[2] > 0 else "accent" if luminance > .75 else "muted"
            canvas.point(point, char, tone, hot)
    # Stylised lens arcs rise above the accretion plane; they are deliberately
    # artistic, not a claim of physical black-hole simulation.
    for index in range(count + 1):
        phase = math.tau * index / count
        point = (1.03 * math.cos(phase), .94 * math.sin(phase), -.2 + .28 * math.cos(phase))
        canvas.point(point, "+" if math.sin(phase - flow) > .75 else ".", "accent")
    canvas.sphere((0, 0, .05), .73, hollow=True)
    for index, (cosine, sine) in enumerate(circle(count)):
        canvas.point((.745 * cosine, .745 * sine, .08), "+" if index % 4 == 0 else ".", "hot", index % 4 == 0)
    for index in range(particles):
        age = (elapsed * (.12 + energy * .12) + index * .61803398875) % 1
        radius = .81 + (1 - age) ** 2 * 1.7
        phase = index * 2.3999632297 + flow * 2 + age * 4
        position = rotate((radius * math.cos(phase), radius * math.sin(phase), .065), -.22, tilt)
        canvas.point(position, "@" if age > .86 else "*" if age > .6 else ".",
                     "hot" if age > .86 else "accent", age > .86)
    burst_age = value(parameters, "burst_age", 99, 0, 99)
    if burst_age < 1.2:
        radius = .8 + burst_age * 2
        for cosine, sine in circle(80):
            canvas.point((radius * cosine, radius * sine, .2), "+" if burst_age < .45 else ".",
                         "hot" if burst_age < .45 else "muted", burst_age < .45)
    return canvas.pixels


@lru_cache(maxsize=8)
def knot_samples(length, section):
    """Cache a trefoil's tube topology, normals and tracer path."""
    samples, centres = [], []
    for i in range(length):
        t = math.tau * i / length
        c2, s2, c3, s3 = math.cos(2 * t), math.sin(2 * t), math.cos(3 * t), math.sin(3 * t)
        radius = 1.62 + .55 * c3
        centre = (radius * c2, radius * s2, .62 * s3)
        tangent = (-1.65 * s3 * c2 - 2 * radius * s2,
                   -1.65 * s3 * s2 + 2 * radius * c2, 1.86 * c3)
        length_t = math.sqrt(sum(v * v for v in tangent))
        tx, ty, tz = (v / length_t for v in tangent)
        length_n = max(.001, math.hypot(tx, ty))
        normal = (-ty / length_n, tx / length_n, 0)
        nx, ny, nz = normal
        binormal = (ty * nz - tz * ny, tz * nx - tx * nz, tx * ny - ty * nx)
        centres.append(centre)
        for cosine, sine in circle(section)[:-1]:
            surface_normal = tuple(normal[k] * cosine + binormal[k] * sine for k in range(3))
            point = tuple(centre[k] + .22 * surface_normal[k] for k in range(3))
            samples.append((point, surface_normal, i))
    return tuple(samples), tuple(centres)


def render_torus_knot(elapsed, width, height, frame=None, parameters=None):
    if width < 5 or height < 3:
        return {}
    canvas = DepthCanvas(width, height, 3.1)
    length, section = min(240, max(120, width * 3)), 12
    samples, centres = knot_samples(length, section)
    energy = value(parameters, "energy", .5, 0, 1)
    yaw, pitch = elapsed * .24, .65 + .15 * math.sin(elapsed * .17)
    transform = rotation(yaw, pitch)
    passed = value(parameters, "test_passed", 0, 0, 100000)
    total = value(parameters, "test_total", 0, 0, 100000)
    progress = min(1, passed / total) if total else 0
    for point, normal, index in samples:
        transformed = transform(point)
        nx, ny, nz = transform(normal)
        light = max(0, -.38 * nx + .43 * ny + .82 * nz)
        intensity = min(1, .13 + light * .82)
        tone = "accent" if index / length < progress else "main" if light > .2 else "muted"
        canvas.point(transformed, RAMP[int(intensity * (len(RAMP) - 1))], tone, light > .8)
    tracers = 2 + int(value(parameters, "particle_count", 60, 0, 200)) // 30
    for tracer in range(tracers):
        for trail in range(8, -1, -1):
            index = int((elapsed * (.06 + energy * .08) + tracer / tracers - trail * .006) * length) % length
            point = transform(centres[index])
            # Slightly enlarged surface points put tracers on the tube's near
            # silhouette without drawing a false path through an occluding turn.
            position = (point[0], point[1], point[2] + .24)
            canvas.point(position, "@" if trail == 0 else "*" if trail < 3 else ".",
                         "hot" if trail < 3 else "accent", trail < 3)
    return canvas.pixels


@dataclass(frozen=True)
class GardenNode:
    parent: int
    born: float
    kind: str
    position: tuple


def noise(index, seed):
    number = ((index + 1) * 2654435761 + (seed + 17) * 2246822519) & 0xffffffff
    number = ((number ^ (number >> 16)) * 2246822519) & 0xffffffff
    return (number ^ (number >> 13)) / 4294967295


@lru_cache(maxsize=8)
def garden_topology(seed, count=256):
    """A fixed-size event tree; parent links and birth offsets are inspectable."""
    count = max(1, min(256, count))
    nodes = [GardenNode(-1, 0, "arrival", (0, -2.2, 0))]
    for index in range(1, count):
        parent = max(0, (index - 1) // 2)
        depth = int(math.log2(index + 1))
        side = -1 if index % 2 else 1
        spread = (1.4 / depth) * side * (.7 + noise(index, seed) * .6)
        before = nodes[parent].position
        height = .75 * .84 ** (depth - 1)
        position = (before[0] + spread, before[1] + height,
                    before[2] + (noise(index + 317, seed) - .5) * .55)
        nodes.append(GardenNode(parent, index * .19, ("arrival", "test", "handoff", "failure")[index % 4], position))
    return tuple(nodes)


def render_packet_garden(elapsed, width, height, frame=None, parameters=None):
    if width < 5 or height < 3:
        return {}
    canvas = DepthCanvas(width, height, 3.7)
    seed = int(value(parameters, "seed", 42, 0, 2147483647))
    sequence = int(value(parameters, "event_sequence", 0, 0, 1000000))
    count = 24 + int(value(parameters, "particle_count", 100, 0, 200))
    topology = garden_topology(seed, count)
    # The bounded idle garden breathes. A supplied event sequence advances the
    # same topology rather than allocating a branch for every incoming event.
    clock = 6 + (elapsed + sequence * .19) % 32
    yaw = .12 * math.sin(elapsed * .18)
    for index, node in enumerate(topology):
        age = clock - node.born
        if age < 0 or age > 32:
            continue
        position = rotate(node.position, yaw, 0)
        tone = "muted" if age > 18 else "hot" if node.kind == "failure" and age < 4 else "accent" if node.kind == "handoff" else "main"
        if node.parent >= 0:
            parent = rotate(topology[node.parent].position, yaw, 0)
            growth = min(1, age / .65)
            endpoint = tuple(a + (b - a) * growth for a, b in zip(parent, position))
            # A failure is deliberately a broken branch, not a success leaf.
            if node.kind == "failure":
                endpoint = tuple(a + (b - a) * .67 for a, b in zip(parent, endpoint))
            canvas.line(parent, endpoint, tone, ":" if node.kind == "handoff" else None)
            position = endpoint
        glyph = "x" if node.kind == "failure" else "+" if node.kind == "test" else "@" if age < .8 else "o"
        canvas.point(position, glyph, tone, age < 2)
    # Event aggregation is explicit: visual leaves never pretend to cover an
    # unbounded event history. The event tape owns complete failure details.
    if sequence > 256 and width >= 18:
        label = "+{} EVENTS".format(sequence - 256)
        for index, char in enumerate(label[:width]):
            canvas.plot(index, height - 1, 7, char, "muted")
    return canvas.pixels


def render_twin_core(elapsed, width, height, frame=None, parameters=None):
    if width < 5 or height < 3:
        return {}
    canvas = DepthCanvas(width, height, 2.65)
    bpm = value(parameters, "pulse_bpm", 72, 30, 240)
    energy = value(parameters, "energy", .5, 0, 1)
    sequence = value(parameters, "event_sequence", 0, 0, 1000000)
    passed = value(parameters, "test_passed", 0, 0, 100000)
    total = value(parameters, "test_total", 0, 0, 100000)
    progress = min(1, passed / total) if total else None
    for side, centre, tone, queue_key in ((0, (-1.5, 0, 0), "main", "queue_left"),
                                          (1, (1.5, 0, 0), "accent", "queue_right")):
        pulse = .035 * math.sin(elapsed * math.tau * bpm / 60 + side * .8)
        canvas.sphere(centre, .49 + pulse, elapsed + side, tone)
        ring = []
        for index, (cosine, sine) in enumerate(circle(80)):
            relative = rotate((.91 * cosine, .91 * sine, 0), elapsed * (.25 if side else -.25), .55 + side)
            position = tuple(centre[k] + relative[k] for k in range(3))
            ring.append(position)
            status = tone if progress is not None and index / 80 <= progress else "muted"
            if index:
                canvas.line(ring[-2], position, status)
        queue = int(value(parameters, queue_key, 3 + side, 0, 12))
        for index in range(queue):
            phase = elapsed * (.45 if side else -.45) + index * math.tau / max(1, queue)
            point = orbit(1.02, phase, .55 + side, elapsed * (.25 if side else -.25))
            position = tuple(centre[k] + point[k] for k in range(3))
            canvas.point(position, "o", tone, True)
    # Two counterflowing Bezier-like streams expose the message direction.
    for direction in (-1, 1):
        previous = None
        for index in range(49):
            fraction = index / 48
            x = -1.15 + fraction * 2.3
            point = (x, direction * .72 * math.sin(math.pi * fraction), .16 * math.sin(fraction * math.tau))
            if previous is not None:
                canvas.line(previous, point, "muted", ".")
            previous = point
        for packet in range(1 + int(energy * 3)):
            fraction = (elapsed * .28 + packet * .27 + sequence * .13) % 1
            if direction < 0:
                fraction = 1 - fraction
            for tail in range(5, -1, -1):
                f = max(0, min(1, fraction - tail * .017 * direction))
                position = (-1.15 + f * 2.3, direction * .72 * math.sin(math.pi * f),
                            .16 * math.sin(f * math.tau) + .04)
                canvas.point(position, ">" if tail == 0 and direction > 0 else "<" if tail == 0 else ".",
                             "hot" if tail == 0 else "accent", tail == 0)
    return canvas.pixels


def render_voxel_tide(elapsed, width, height, frame=None, parameters=None):
    if width < 5 or height < 3:
        return {}
    canvas = DepthCanvas(width, height, 3.55)
    energy = value(parameters, "energy", .55, 0, 1)
    resolution = 9 if width < 45 or height < 14 else 13
    step = 4.2 / (resolution - 1)
    yaw, pitch = .72 + .08 * math.sin(elapsed * .12), -.69
    for iz in range(resolution):
        for ix in range(resolution):
            x, z = -2.1 + ix * step, -2.1 + iz * step
            distance = math.hypot(x + .6, z - .3)
            wave = .5 + .27 * math.sin(distance * 3 - elapsed * 2.2) + .23 * math.cos(x * 2 + z * 1.5 + elapsed)
            level = .15 + wave * (1 + energy * 1.2)
            base, top = rotate((x, -1.1, z), yaw, pitch), rotate((x, level - 1.1, z), yaw, pitch)
            canvas.line(base, top, "muted", "|")
            tone = "hot" if wave > .82 else "main" if wave > .44 else "accent"
            # A diamond on each column gives small panels a voxel silhouette;
            # the same depth buffer resolves the nearer pillars over the rear.
            corners = [rotate((x + dx * step * .38, level - 1.1, z + dz * step * .38), yaw, pitch)
                       for dx, dz in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
            for first, second in zip(corners, corners[1:] + corners[:1]):
                canvas.line(first, second, tone)
            canvas.point(top, "#" if wave > .75 else "+", tone, wave > .75)
    return canvas.pixels


def render_asset_orrery(elapsed, width, height, frame=None, parameters=None):
    if width < 5 or height < 3:
        return {}
    canvas = DepthCanvas(width, height, 2.65)
    energy = value(parameters, "energy", .5, 0, 1)
    count = 3 + int(value(parameters, "particle_count", 100, 0, 200)) // 35
    sequence = int(value(parameters, "event_sequence", 0, 0, 1000000))
    canvas.sphere((0, 0, 0), .48, elapsed, "hot")
    for index in range(count):
        radius = 1.02 + index * .22
        tilt, yaw = .3 + index * .37, index * .63 + elapsed * .05
        previous = None
        for cosine, sine in circle(96):
            point = rotate((radius * cosine, radius * sine, 0), yaw, tilt)
            if previous is not None:
                canvas.line(previous, point, "accent" if index == sequence % count else "muted", ".")
            previous = point
        phase = index * 2.3999632297 + elapsed * (.24 + energy * .2) / (1 + index * .2)
        centre = orbit(radius, phase, tilt, yaw)
        canvas.line((0, 0, 0), centre, "muted", ":")
        size = .105 + .018 * (index % 3)
        points = [(centre[0] + dx * size, centre[1] + dy * size, centre[2] + dz * size)
                  for dx in (-1, 1) for dy in (-1, 1) for dz in (-1, 1)]
        tone = "hot" if index == sequence % count else "main" if index % 2 else "accent"
        for i, point in enumerate(points):
            for bit in (1, 2, 4):
                j = i ^ bit
                if i < j:
                    canvas.line(point, points[j], tone)
        canvas.point(centre, "@" if index == sequence % count else "o", tone, True)
        # Immutable revision satellites share their asset's stable orbital ID.
        for revision in range(1 + index % 3):
            angle = elapsed * .9 + revision * math.tau / (1 + index % 3)
            canvas.point((centre[0] + .24 * math.cos(angle), centre[1] + .24 * math.sin(angle), centre[2]),
                         "+", "accent")
    return canvas.pixels
