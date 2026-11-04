"""An ASCII anime pianist: a procedural bust portrait that scales to any viewport.

Everything here is synthetic illustration.  The portrait, the keyboard, the
melody, the hands and the floating notes are pure functions of time, viewport
size and the current numeric bindings.  Nothing is loaded, sampled or played.

The figure lives in a 100 x 100 "art unit" box (y grows downwards).  One unit
is ``scale`` rows tall and ``2 * scale`` columns wide, because terminal cells
are roughly twice as tall as they are wide.  Static portrait layers are cached
per viewport; blinking, swaying strands, arms, keys, hands and notes are drawn
every frame on top of that cache.
"""

from functools import lru_cache
import math

from .effects import value
from .graphics import Pixel, line_cells, line_char


HEAD = (50.0, 37.5, 23.5, 26.5)           # centre x, centre y, half width, half height
HAIR = (50.0, 39.0, 26.5, 33.0)           # back hair ellipse; the fall widens below cy
EYES = ((40.0, 43.5), (60.0, 43.5))       # left and right eye centres
EYE_RX, EYE_RY = 6.2, 7.0
NECK_HALF, NECK_TOP, SHOULDER_TOP, SHOULDER_BOTTOM = 5.6, 61.0, 71.0, 83.0
SLEEVE_HALF = 2.4
SHOULDERS = ((50 - 31.2 + SLEEVE_HALF, 82.0), (50 + 31.2 - SLEEVE_HALF, 82.0))  # sleeve centres
CROWN = (50.0, 14.0)                      # hair strands radiate from here
HAIR_TONE, SKIN_TONE, DRESS_TONE = "accent", "text", "muted"
NOTE_GLYPHS = "dpdbdpJ#"
EIGHTHS = 16

# White-key steps relative to each hand's home key; None holds the previous key.
RIGHT_HAND = (0, 2, 4, 7, 4, 2, 0, None, 1, 3, 5, 3, 1, None, 2, 4)
LEFT_HAND = (-4, None, 0, None, -2, None, 0, None, -5, None, -1, None, -3, None, -1, None)
BLACK_AFTER = (0, 1, 3, 4, 5)             # white keys of an octave followed by a black key
PORTRAIT_MIN_ROWS = 8


def smoothstep(fraction):
    fraction = max(0.0, min(1.0, fraction))
    return fraction * fraction * (3 - 2 * fraction)


class Sheet:
    """A bounded cell buffer with the art-unit mapping of one viewport."""

    def __init__(self, width, height, scale, offset_x, pixels=None):
        self.width, self.height, self.scale = width, height, scale
        self.offset_x = offset_x
        self.pixels = {} if pixels is None else pixels

    def col(self, x):
        return self.offset_x + x * self.scale * 2

    def row(self, y):
        return y * self.scale

    def x_at(self, col):
        return (col + .5 - self.offset_x) / (self.scale * 2)

    def y_at(self, row):
        return (row + .5) / self.scale

    def cell(self, x, y):
        return round(self.col(x)), round(self.row(y))

    def put(self, col, row, char, tone=SKIN_TONE, bold=False):
        if 0 <= col < self.width and 0 <= row < self.height:
            self.pixels[col, row] = Pixel(char, tone, bold)

    def span(self, row, left, right, pixel_at):
        """Fill one row between two columns using a per-cell pixel factory."""
        if 0 <= row < self.height:
            for col in range(max(0, left), min(self.width, right + 1)):
                self.pixels[col, row] = pixel_at(col, col == left, col == right)

    def stroke(self, cells, tone=SKIN_TONE, bold=False, char=None):
        """Draw connected cells with slope-derived glyphs."""
        for index, (col, row) in enumerate(cells):
            before, after = cells[max(0, index - 1)], cells[min(len(cells) - 1, index + 1)]
            self.put(col, row, char or line_char(before, after), tone, bold)

    def curve(self, points, tone=SKIN_TONE, bold=False, char=None):
        """Connect art-unit sample points into one thin eight-connected line."""
        cells = []
        for x, y in points:
            current = self.cell(x, y)
            steps = line_cells(*cells[-1], *current)[1:] if cells and cells[-1] != current else (
                [current] if not cells else [])
            for cell in steps:
                if len(cells) >= 2 and max(abs(cells[-2][0] - cell[0]), abs(cells[-2][1] - cell[1])) <= 1:
                    cells[-1] = cell  # the previous cell only padded a staircase corner
                else:
                    cells.append(cell)
        self.stroke(cells, tone, bold, char)


def head_half_width(y):
    cx, cy, rx, ry = HEAD
    t = (y - cy) / ry
    if abs(t) >= 1:
        return 0.0
    if t <= 0:
        return rx * math.sqrt(1 - t * t)
    # A superellipse with exponent below two gives the tapered anime chin.
    return rx * (1 - t ** 1.5) ** (1 / 1.5) * (1 - .2 * t)


def hair_half_width(y):
    cx, cy, rx, ry = HAIR
    if y < cy:
        t = (y - cy) / ry
        return rx * math.sqrt(1 - t * t) if abs(t) < 1 else 0.0
    return rx + .09 * (y - cy)


def torso_half_width(y):
    if y < NECK_TOP:
        return 0.0
    if y < SHOULDER_TOP:
        return NECK_HALF
    slope_end = SHOULDER_TOP + 7.5
    if y < slope_end:
        # The trapezius line leaves the neck at roughly thirty degrees.
        return NECK_HALF + 19.4 * (y - SHOULDER_TOP) / 7.5
    if y < SHOULDER_BOTTOM:
        fraction = (y - slope_end) / (SHOULDER_BOTTOM - slope_end)
        return 25 + 6.2 * math.sin(fraction * math.pi / 2)
    return 31.2


def bang_edge(x):
    """Lower edge of the fringe: pointed strands, a parting and longer temples."""
    d = x - 50
    strand = abs((d % 9) - 4.5) / 4.5  # 0 at strand tips, 1 between strands
    return 32.5 - 4.5 * strand - 3.5 * math.exp(-(d / 3.4) ** 2) + (d / 23) ** 4 * 9


def hair_pixel(x, y, edge=None, front=False):
    """Strands radiate from the crown; a shine band crosses below the top."""
    if edge:
        return Pixel(edge, HAIR_TONE, True)
    dx, dy = x - CROWN[0], y - CROWN[1]
    if dy > 0 and 8.5 <= math.hypot(dx * .8, dy) <= 11 and abs(dx) < 19:
        return Pixel("'", SKIN_TONE, False)
    if dy <= 0 or abs(dx) < .1 * dy:
        glyph = "|"
    elif abs(dx) > 2.4 * dy:
        glyph = "~"
    else:
        glyph = "/" if dx < 0 else "\\"
    return Pixel(glyph, HAIR_TONE, front)


def hair_span(sheet, row, y, centre, half, front=False):
    left, right = round(sheet.col(centre - half)), round(sheet.col(centre + half))
    sheet.span(row, left, right, lambda col, first, last: hair_pixel(
        sheet.x_at(col), y, None if front else "(" if first else ")" if last else None, front))


def dress_pixel(col, row):
    return Pixel("." if (col + 2 * row) % 4 == 0 else " ", DRESS_TONE, False)


@lru_cache(maxsize=8)
def portrait_layer(width, height, scale, offset_x):
    """Hair, face, torso and the features that never move."""
    sheet = Sheet(width, height, scale, offset_x)
    for row in range(max(0, math.floor(sheet.row(HAIR[1] - HAIR[3]))), height):
        y = sheet.y_at(row)
        half = hair_half_width(y)
        if half > 0:
            hair_span(sheet, row, y, 50, half)
        half = torso_half_width(y)
        if half > 0:
            left, right = round(sheet.col(50 - half)), round(sheet.col(50 + half))
            if y < SHOULDER_TOP:
                sheet.span(row, left, right, lambda col, first, last: Pixel("|" if first or last else " ", SKIN_TONE))
            else:
                sheet.span(row, left, right, lambda col, first, last: dress_pixel(col, row))
        half = head_half_width(y)
        if half > 0:
            left, right = round(sheet.col(50 - half)), round(sheet.col(50 + half))
            sheet.span(row, left, right, lambda col, first, last: hair_pixel(sheet.x_at(col), y, front=True)
                       if y < bang_edge(sheet.x_at(col)) else Pixel(" ", SKIN_TONE))
    # Contours: the hair silhouette, an ahoge, the lower face, the neck and the dress.
    cx, cy, rx, ry = HAIR
    sheet.curve([(cx + rx * math.cos(a), cy + ry * math.sin(a))
                 for a in (math.pi + math.pi * i / 60 for i in range(61))], HAIR_TONE, True)
    sheet.curve([(52.5, 6.4), (54, 4.6), (56, 3.3), (58.5, 2.8)], HAIR_TONE, True)
    for side in (-1, 1):
        sheet.curve([(50 + side * head_half_width(y), y) for y in (34 + i * .25 for i in range(117))], SKIN_TONE)
        sheet.curve([(50 + side * NECK_HALF, y) for y in (NECK_TOP + i * .5 for i in range(21))], SKIN_TONE)
        sheet.curve([(50 + side * torso_half_width(y), y)
                     for y in (SHOULDER_TOP + i * .5 for i in range(int((SHOULDER_BOTTOM - SHOULDER_TOP) * 2) + 1))],
                    DRESS_TONE)
        sheet.curve([(50 + side * 4.2 * (1 - f), SHOULDER_TOP + 1.6 + 9.5 * f) for f in (i / 20 for i in range(21))],
                    SKIN_TONE)
        # Flat anime eyebrows above the fringe tips, the outer end slightly higher.
        ex, ey = EYES[0 if side < 0 else 1]
        brow_row = min(round(sheet.row(ey - 12)), round(sheet.row(ey - EYE_RY)) - 2)
        left, right = round(sheet.col(ex - 5.6)), round(sheet.col(ex + 5.6))
        for col in range(left, right + 1):
            outer = (col - left) / max(1, right - left) if side < 0 else (right - col) / max(1, right - left)
            sheet.put(col, brow_row - (1 if outer < .25 and scale >= .42 else 0), "-", SKIN_TONE)
        # Blush marks on the cheeks.
        bx, by = 50 + side * 15, 52.5
        sheet.put(*sheet.cell(bx - .5, by), "/", "hot")
        if scale >= .3:
            sheet.put(*sheet.cell(bx + .5, by), "/", "hot")
    sheet.put(*sheet.cell(50.6, 52.8), "'", SKIN_TONE)
    bow_col, bow_row = sheet.cell(50, SHOULDER_TOP + 12.1)
    for i, glyph in enumerate(">o<"):
        sheet.put(bow_col - 1 + i, bow_row, glyph, "hot", True)
    return sheet.pixels


def blink(elapsed):
    """Eye openness in (0, 1]; a deterministic blink every few seconds."""
    period = 4.3
    cycle = math.floor(elapsed / period)
    phase = elapsed - cycle * period - 2.0 - (cycle * .618034) % 1 * 1.4
    if phase < -.08 or phase > .24:
        return 1.0
    if phase < 0:
        return max(.1, 1 + phase / .08)
    if phase < .16:
        return .1
    return min(1.0, .1 + (phase - .16) / .08 * .9)


def draw_eye(sheet, ex, ey, openness, gaze):
    ry = EYE_RY * openness
    top, bottom = round(sheet.row(ey - ry)), round(sheet.row(ey + ry))
    if openness < .3 or bottom - top < 1:
        # A closed lid: a gentle curve with lifted corners.
        row = round(sheet.row(ey + .6))
        left, right = round(sheet.col(ex - EYE_RX * .8)), round(sheet.col(ex + EYE_RX * .8))
        for col in range(left, right + 1):
            sheet.put(col, row, "\\" if col == left else "/" if col == right else "_", "main", True)
        return
    icx, icy, irx, iry = ex + gaze, ey + .7, EYE_RX * .66, ry * .92
    for row in range(top, bottom + 1):
        v = (sheet.y_at(row) - ey) / ry
        half = EYE_RX * math.sqrt(max(.1, 1 - v * v))
        left, right = round(sheet.col(ex - half)), round(sheet.col(ex + half))
        if row == top:
            for col in range(left + 1, right):
                sheet.put(col, row - 1, "_", "main", True)
        for col in range(left, right + 1):
            if col == left or col == right:
                glyph = ("/" if col == left else "\\") if row == top else \
                    ("\\" if col == left else "/") if row == bottom else "|"
                sheet.put(col, row, glyph, "main", True)
                continue
            if row == bottom:
                sheet.put(col, row, "_", "main")
                continue
            x, y = sheet.x_at(col), sheet.y_at(row)
            iu, iv = (x - icx) / irx, (y - icy) / iry
            if iu * iu + iv * iv > 1:
                sheet.put(col, row, " ", SKIN_TONE)
                continue
            hu, hv = (x - icx + irx * .42) / (irx * .3), (y - icy + iry * .4) / (iry * .32)
            if hu * hu + hv * hv <= 1:
                sheet.put(col, row, "o", SKIN_TONE, True)
            elif iv < -.25:
                sheet.put(col, row, "@", "main", True)
            elif iv < .45:
                sheet.put(col, row, "#", "main")
            else:
                sheet.put(col, row, "%", "main")


def draw_mouth(sheet, open_mouth):
    row = round(sheet.row(57.6))
    if open_mouth:
        sheet.put(*sheet.cell(50, 57.8), "o", SKIN_TONE, True)
        return
    left, right = round(sheet.col(48.3)), round(sheet.col(51.7))
    if right - left < 2:
        sheet.put(left, row, "_", SKIN_TONE)
        return
    for col in range(left, right + 1):
        sheet.put(col, row, "\\" if col == left else "/" if col == right else "_", SKIN_TONE)


def draw_strands(sheet, elapsed, energy):
    """Long front strands fall over the shoulders and sway with the music."""
    for side, phase in ((-1, 0.0), (1, 1.9)):
        for row in range(max(0, math.floor(sheet.row(31))), sheet.height):
            y = sheet.y_at(row)
            if y > 97:
                break
            sway = (.6 + 1.6 * energy) * math.sin(elapsed * 1.1 + phase + y * .07) * smoothstep((y - 55) / 35)
            centre = 50 + side * (22.6 - .03 * (y - 34)) + sway
            half = 2.7 - 2.2 * smoothstep((y - 82) / 15)
            hair_span(sheet, row, y, centre, half, front=True)


def melody_state(elapsed, tempo):
    """Where each hand is, which key it presses and when every recent note started."""
    eighth = 30.0 / tempo
    index = math.floor(elapsed / eighth)
    fraction = (elapsed - index * eighth) / eighth
    hands = []
    for pattern in (LEFT_HAND, RIGHT_HAND):
        def key_at(step):
            for back in range(len(pattern)):
                offset = pattern[(step - back) % len(pattern)]
                if offset is not None:
                    return offset
            return 0
        current, previous = key_at(index), key_at(index - 1)
        position = previous + (current - previous) * smoothstep(fraction / .38)
        pressed = pattern[index % len(pattern)] is not None and fraction < .62
        notes = tuple((key_at(step), elapsed - step * eighth, step) for step in range(index - 7, index + 1)
                      if step >= 0 and pattern[step % len(pattern)] is not None)
        hands.append((position, current if pressed else None, notes))
    return tuple(hands), index, fraction


def draw_keyboard(sheet, top, key_width, pressed):
    height = sheet.height - top
    black_rows = max(1, round((height - 1) * .55))
    for col in range(sheet.width):
        sheet.put(col, top, "=", DRESS_TONE, True)
    keys = sheet.width // key_width + 1
    for key in range(keys):
        left = key * key_width
        hot = key in pressed
        for row in range(top + 1, sheet.height):
            bottom = row == sheet.height - 1
            for col in range(left, min(sheet.width, left + key_width)):
                if col == left:
                    sheet.put(col, row, "|", "text")
                else:
                    sheet.put(col, row, "_" if bottom else "=" if hot else " ", "hot" if hot else "text", hot)
    black_width = key_width - 1 if key_width <= 4 else 3
    for key in range(keys):
        if key % 7 not in BLACK_AFTER:
            continue
        centre = (key + 1) * key_width
        for row in range(top + 1, min(sheet.height - 1, top + 1 + black_rows)):
            for col in range(centre - black_width // 2, centre - black_width // 2 + black_width):
                sheet.put(col, row, "#", DRESS_TONE, True)


def draw_arm(sheet, shoulder, hand_col, keyboard_top, side, scale):
    """A sleeve hangs from the shoulder; the forearm reaches along the keys to the hand."""
    sx, sy = shoulder
    half = max(1, round(SLEEVE_HALF * scale * 2))
    elbow_row = keyboard_top - 3
    elbow_col = sx
    for col, row in line_cells(sx, sy, elbow_col, elbow_row):
        cuff = row == elbow_row
        for offset in range(-half, half + 1):
            edge = abs(offset) == half
            glyph = "|" if edge else "." if (col * 3 + row * 5) % 11 == 0 else " "
            sheet.put(col + offset, row, "'" if cuff and not edge else glyph, DRESS_TONE, edge)
    start, end = sorted((elbow_col, hand_col))
    for col in range(start - half, end + half + 1):
        sheet.put(col, keyboard_top - 3, "_", SKIN_TONE)
        for row in (keyboard_top - 2, keyboard_top - 1):
            sheet.put(col, row, " ", SKIN_TONE)


def draw_hand(sheet, col, row, scale):
    sprite = ((-2, ".---."), (-1, "(     )"), (0, "/|||||\\")) if scale >= .35 else ((-1, ".--."), (0, "/|||\\"))
    for dy, text in sprite:
        start = col - len(text) // 2
        for index, glyph in enumerate(text):
            sheet.put(start + index, row + dy, glyph, SKIN_TONE, True)


def draw_notes(sheet, hands, key_width, homes, keyboard_top, energy):
    rise = max(3.0, (keyboard_top + 1) * .3)
    life = 3.2
    for (position, pressed, notes), home, side in zip(hands, homes, (-1, 1)):
        for key, age, step in notes:
            if age <= .05 or age >= life:
                continue
            col = (home + key) * key_width + key_width // 2 + side * age * (2.5 + 2 * energy) \
                + 2.2 * math.sin(age * 2.1 + step * 1.7)
            row = keyboard_top - 3 - age * rise
            tone = "hot" if age < .7 else "warn" if age < 1.6 else "accent" if age < 2.4 else "muted"
            sheet.put(round(col), round(row), NOTE_GLYPHS[step % len(NOTE_GLYPHS)], tone, age < 1.6)


def draw_staves(sheet, keyboard_top):
    """Faint staff lines fill the space beside the portrait on wide terminals."""
    margin = 6
    left_edge, right_edge = math.floor(sheet.col(50 - HAIR[2]) - margin), math.ceil(sheet.col(50 + HAIR[2]) + margin)
    if left_edge < 12 or keyboard_top < 12:
        return
    gap = 2 if keyboard_top < 30 else 3
    first = round(keyboard_top * .42) - 2 * gap
    for line in range(5):
        row = first + line * gap
        for col in range(1, left_edge):
            sheet.put(col, row, "-", DRESS_TONE)
        for col in range(right_edge, sheet.width - 1):
            sheet.put(col, row, "-", DRESS_TONE)


def draw_sparkles(sheet, elapsed, energy):
    threshold = 3 + int(5 * energy)
    for row in range(sheet.height):
        for col in range(sheet.width):
            if (col, row) in sheet.pixels:
                continue
            seed = (col * 73856093 ^ row * 19349663) & 0xFFFFFF
            if seed % 1000 >= threshold:
                continue
            glow = math.sin(elapsed * (.8 + (seed >> 12) % 7 * .15) + (seed >> 8) % 100 * .0628)
            if glow > .75:
                sheet.put(col, row, "*", "muted", True)
            elif glow > .2:
                sheet.put(col, row, ".", "muted")


def render_pianist(elapsed, width, height, frame=None, parameters=None):
    energy = value(parameters, "energy", .5, 0, 1)
    tempo = value(parameters, "pulse_bpm", 76 + 50 * energy, 40, 180)
    keyboard_rows = 3 if height < 20 else 4 if height < 36 else 5
    keyboard_top = height - keyboard_rows
    key_width = 3 if width < 100 else 4 if width < 180 else 5
    scale = min(keyboard_top / 100, width / 200)
    offset_x = (width - 200 * scale) / 2
    portrait = scale * 100 >= PORTRAIT_MIN_ROWS
    hands, index, fraction = melody_state(elapsed, tempo)
    pixels = dict(portrait_layer(width, height, round(scale, 6), round(offset_x, 4))) if portrait else {}
    sheet = Sheet(width, height, scale, offset_x, pixels)
    if portrait:
        draw_staves(sheet, keyboard_top)
        gaze = 1.1 * math.sin(elapsed * .37)
        openness = blink(elapsed)
        for ex, ey in EYES:
            draw_eye(sheet, ex, ey, openness, gaze)
        draw_mouth(sheet, RIGHT_HAND[index % EIGHTHS] == 7 and fraction < .8)
        draw_strands(sheet, elapsed, energy)
    homes = [round(sheet.col(x)) // key_width for x in (36, 64)]
    wrists = [round((home + position) * key_width + key_width / 2) for (position, key, notes), home in zip(hands, homes)]
    if portrait:
        for shoulder, wrist, side in zip(SHOULDERS, wrists, (-1, 1)):
            draw_arm(sheet, sheet.cell(*shoulder), wrist, keyboard_top, side, scale)
    pressed = {home + key for (position, key, notes), home in zip(hands, homes) if key is not None}
    draw_keyboard(sheet, keyboard_top, key_width, pressed)
    for wrist in wrists:
        draw_hand(sheet, wrist, keyboard_top - 1, scale)
    draw_notes(sheet, hands, key_width, homes, keyboard_top, energy)
    draw_sparkles(sheet, elapsed, energy)
    return sheet.pixels
