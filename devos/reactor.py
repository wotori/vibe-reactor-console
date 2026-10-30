"""A depth-tested plasma core and precessing particle orbits, drawn in ASCII."""

from functools import lru_cache
import math

from .graphics import Pixel, line_char, line_cells


CAMERA = 6.0


@lru_cache(maxsize=8)
def circle_samples(count):
    """Keep trigonometry for the unchanging ring topology out of the frame loop."""
    return tuple((math.cos(math.tau * i / count), math.sin(math.tau * i / count))
                 for i in range(count + 1))


def render_reactor(elapsed, width, height):
    if width < 5 or height < 3:
        return {}
    centre_x, centre_y = (width - 1) / 2, (height - 1) / 2
    # All three rings fit a fixed bounding sphere. Rotation never pumps the zoom.
    # Characters are roughly twice as tall as they are wide.
    scale = min((width - 3) / 8.8, (height - 1) / 4.4)
    pixels, depths = {}, {}

    def plot(x, y, z, char, tone, bold=False):
        if 0 <= x < width and 0 <= y < height and z >= depths.get((x, y), -math.inf):
            depths[x, y] = z
            pixels[x, y] = Pixel(char, tone, bold)

    def project(x, y, z):
        perspective = CAMERA / (CAMERA - z)
        return (centre_x + x * perspective * scale * 2,
                centre_y - y * perspective * scale, z)

    # A ray/sphere intersection gives the core a curved silhouette and real depth
    # at each cell, so near orbits cross its face and far orbits disappear behind it.
    radius = .78 + .025 * math.sin(elapsed * 2.2)
    extent = radius / math.sqrt(1 - (radius / CAMERA) ** 2)
    ramp = ".:-=+*#%@"
    for y in range(max(0, math.floor(centre_y - extent * scale)),
                   min(height, math.ceil(centre_y + extent * scale) + 1)):
        v = (centre_y - y) / scale
        for x in range(max(0, math.floor(centre_x - extent * scale * 2)),
                       min(width, math.ceil(centre_x + extent * scale * 2) + 1)):
            u = (x - centre_x) / (scale * 2)
            a = u * u + v * v + CAMERA * CAMERA
            discriminant = CAMERA ** 4 - a * (CAMERA * CAMERA - radius * radius)
            if discriminant < 0:
                continue
            distance = (CAMERA * CAMERA - math.sqrt(discriminant)) / a
            px, py, pz = u * distance, v * distance, CAMERA * (1 - distance)
            nx, ny, nz = px / radius, py / radius, pz / radius
            light = max(0, -.38 * nx + .45 * ny + .81 * nz)
            # Advect narrow luminous filaments over the sphere rather than moving
            # a flat texture in screen space. The terminator remains shaded.
            longitude = math.atan2(nx, nz) + elapsed * .65
            latitude = math.asin(max(-1, min(1, ny)))
            filament = math.sin(longitude * 7 + math.sin(latitude * 5 - elapsed) * 1.5)
            intensity = min(1, .13 + .72 * light + .15 * filament)
            char = ramp[max(0, min(len(ramp) - 1, int(intensity * (len(ramp) - 1))))]
            tone = "hot" if filament > .80 and light > .35 else "main" if light > .25 else "muted"
            plot(x, y, pz, char, tone, light > .8 or tone == "hot")

    samples = circle_samples(min(320, max(96, width * 3)))
    for index in range(3):
        radius = 1.65 + index * .13
        tilt = .38 + index * .92 + .16 * math.sin(elapsed * .31 + index)
        yaw = elapsed * .19 + index * 1.05
        roll = elapsed * (.12 if index != 1 else -.16) + index * .8
        ct, st, cy, sy, cr, sr = (math.cos(tilt), math.sin(tilt), math.cos(yaw),
                                 math.sin(yaw), math.cos(roll), math.sin(roll))

        def orbit(cosine, sine):
            x, y, z = radius * cosine, radius * sine * ct, radius * sine * st
            x, z = x * cy + z * sy, -x * sy + z * cy
            return project(x * cr - y * sr, x * sr + y * cr, z)

        ring = [orbit(cosine, sine) for cosine, sine in samples]
        tone = "accent" if index == 1 else "main"
        for start, end in zip(ring, ring[1:]):
            x0, y0, z0 = start
            x1, y1, z1 = end
            cells = line_cells(round(x0), round(y0), round(x1), round(y1))
            dx, dy = x1 - x0, y1 - y0
            char = "-" if abs(dy) < abs(dx) * .35 else "|" if abs(dx) < abs(dy) * .45 else line_char(start, end)
            for step, (x, y) in enumerate(cells):
                fraction = step / max(1, len(cells) - 1)
                # Interpolate reciprocal camera distance, as in a perspective
                # rasterizer, to preserve occlusion even at ring intersections.
                z = CAMERA - 1 / ((1 - fraction) / (CAMERA - z0) + fraction / (CAMERA - z1))
                plot(x, y, z, char if z > -.3 else ".", tone if z > -.3 else "muted")

        # Two counterbalanced tracers per orbit, with time-based fading tails.
        head = elapsed * (1.15 + index * .24) * (-1 if index == 1 else 1) + index * 2.1
        direction = -1 if index == 1 else 1
        for offset in (0, math.pi):
            for trail in range(11, -1, -1):
                phase = head + offset - trail * .065 * direction
                x, y, z = orbit(math.cos(phase), math.sin(phase))
                char = "@" if trail == 0 else "*" if trail < 3 else "+" if trail < 6 else ":" if trail < 9 else "."
                # A minute bias separates particles from their own ring surface.
                plot(round(x), round(y), z + .015, char,
                     "hot" if trail < 3 else "accent" if trail < 7 else "muted", trail < 3)

    # Sparse deterministic sparks spiral outwards; no RNG, stored trail history
    # or frame-count dependence, so pause and resize do not reset the effect.
    for index in range(22):
        age = (elapsed * .24 + index * .61803398875) % 1
        angle = index * 2.3999632297 + elapsed * .23
        radius = .95 + age * 1.1
        z = math.sin(angle * 1.7 + index) * .7
        x, y, z = project(math.cos(angle) * radius, math.sin(angle) * radius, z)
        plot(round(x), round(y), z, "+" if age < .12 else ".",
             "accent" if age < .45 else "muted", age < .12)
    return pixels
