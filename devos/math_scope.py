"""Pure, clock-driven mathematical traces for the terminal oscilloscope.

Every frame is a function of the supplied time.  Trails are sampled from that
same clock, so pausing, resizing and replay never require a mutable framebuffer.
Only visible ASCII cells are returned; the terminal's background stays intact.
"""

from dataclasses import dataclass
import math

from .graphics import Pixel


@dataclass(frozen=True)
class ScopeSpec:
    id: str
    title: str
    formula: str
    description: str


SCOPES = (
    ScopeSpec("harmonics", "Harmonic interference", ".55 sin(7x-2.5t)+b(t) sin(19x+1.525t)",
              "Scrolling waves, beating amplitudes and a moving scan head."),
    ScopeSpec("parabola", "Moving parabola", "y = a(t)(x-h(t))^2 + k(t)",
              "An upward parabola with a moving vertex and tangent."),
    ScopeSpec("lissajous", "Lissajous orbit", "x=.92 sin(3u+d), y=.87 sin(2u)",
              "A 3:2 parametric orbit with a bright head and fading trail."),
    ScopeSpec("fourier", "Fourier synthesis", ".58(4/pi) SUM sin(n(2pi x-1.6t))/n, n odd",
              "Odd harmonics progressively reconstruct a square wave."),
    ScopeSpec("wave-packet", "Travelling wave packet", ".88 exp(-(x-c)^2/(2s^2)) cos(27x-6t)",
              "A travelling Gaussian envelope carries a faster oscillation."),
    ScopeSpec("rose", "Polar rose", "r = 0.9 cos(5 theta)",
              "A rotating five-petal polar curve with a luminous tracer."),
    ScopeSpec("spiral", "Archimedean spiral", "r = b theta",
              "A rotating three-turn spiral is traced from its centre."),
    ScopeSpec("epicycles", "Epicycle machine", "SUM exp(i n w t)/(n Z), n=1,3,5,7",
              "Four rotating vectors draw their actual summed waveform."),
)
SCOPE_BY_ID = {scope.id: scope for scope in SCOPES}
_TAU = math.tau
_PRIORITY = {"muted": 0, "accent": 1, "main": 2, "hot": 3}


def _time(elapsed):
    try:
        value = float(elapsed)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    # Avoid overflow before calling trigonometric functions for a corrupt clock.
    return math.fmod(value, 360000.0) if math.isfinite(value) else 0.0


def _parabola_parameters(elapsed):
    return (1.0 + 0.25 * math.sin(elapsed * 0.55),
            0.38 * math.sin(elapsed * 0.6),
            -0.64 + 0.12 * math.cos(elapsed * 0.8))


def _fourier_terms(elapsed):
    return 1 + int((elapsed * 0.65) % 8)


def scope_caption(mode, elapsed):
    """A short readout of the actual parameters used in this frame."""
    elapsed = _time(elapsed)
    if mode == "parabola":
        a, h, k = _parabola_parameters(elapsed)
        return "a=%.2f  h=%+.2f  k=%+.2f  | moving vertex + tangent" % (a, h, k)
    if mode == "fourier":
        count = _fourier_terms(elapsed)
        return "%d odd harmonic%s  | n=1..%d  | square-wave synthesis" % (
            count, "" if count == 1 else "s", count * 2 - 1)
    if mode == "wave-packet":
        return "carrier x envelope  | group motion + phase motion"
    if mode == "epicycles":
        return "4 vectors  | radii 1, 1/3, 1/5, 1/7  | actual sum -> trace"
    return SCOPE_BY_ID.get(mode, SCOPES[0]).formula


class _Canvas:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.pixels = {}

    def point(self, x, y, char=".", tone="main", bold=False):
        if not (math.isfinite(x) and math.isfinite(y)):
            return
        x, y = round(x), round(y)
        if 0 <= x < self.width and 0 <= y < self.height:
            previous = self.pixels.get((x, y))
            if previous is None or _PRIORITY[tone] >= _PRIORITY[previous.tone]:
                self.pixels[x, y] = Pixel(char, tone, bold)

    def project(self, x, y):
        return ((x + 1.0) * (self.width - 1) * 0.5,
                (1.0 - y) * (self.height - 1) * 0.5)

    def mark(self, x, y, char="@", tone="hot"):
        self.point(*self.project(x, y), char, tone, True)

    def line(self, first, last, tone="main", char=None):
        x0, y0 = first
        x1, y1 = last
        dx, dy = x1 - x0, y1 - y0
        # A bounded DDA is sufficient for adjacent curve samples.  Offscreen
        # parabolas cannot turn a steep segment into unbounded work.
        steps = min(max(self.width, self.height) * 2,
                    max(1, math.ceil(max(abs(dx), abs(dy)))))
        glyph = char or ("-" if abs(dy) < 0.35 else "|" if abs(dx) < 0.35
                         else "\\" if dx * dy > 0 else "/")
        for index in range(steps + 1):
            amount = index / steps
            self.point(x0 + dx * amount, y0 + dy * amount, glyph, tone)

    def curve(self, function, tone="main", samples=None):
        samples = samples or self.width
        previous = self.project(-1.0, function(-1.0))
        for index in range(1, samples + 1):
            x = index * 2.0 / samples - 1.0
            current = self.project(x, function(x))
            self.line(previous, current, tone)
            previous = current

    def grid(self):
        middle_x, middle_y = (self.width - 1) / 2, (self.height - 1) / 2
        for x in range(0, self.width, 4):
            self.point(x, middle_y, ".", "muted")
        if self.height >= 8:
            for y in range(0, self.height, 3):
                self.point(middle_x, y, ":", "muted")
        self.point(middle_x, middle_y, "+", "muted")


def _harmonics(canvas, elapsed):
    phase = elapsed * 2.5
    beat = 0.13 + 0.10 * math.sin(elapsed * 0.72)

    def signal(x, lag=0.0):
        return (0.55 * math.sin(x * 7.0 - phase + lag)
                + beat * math.sin(x * 19.0 + phase * 0.61 - lag))

    canvas.curve(lambda x: signal(x, 0.38), "muted")
    canvas.curve(lambda x: 0.55 * math.sin(x * 7.0 - phase), "accent")
    canvas.curve(signal)
    sweep = (elapsed * 0.58) % 2.0 - 1.0
    canvas.mark(sweep, signal(sweep))


def _parabola(canvas, elapsed):
    a, h, k = _parabola_parameters(elapsed)
    old_a, old_h, old_k = _parabola_parameters(elapsed - 0.22)
    canvas.curve(lambda x: old_a * (x - old_h) ** 2 + old_k, "muted")
    contact = h + 0.55 * math.sin(elapsed * 0.77 + 0.8)
    ordinate = a * (contact - h) ** 2 + k
    slope = 2.0 * a * (contact - h)
    canvas.curve(lambda x: ordinate + slope * (x - contact), "accent")
    canvas.curve(lambda x: a * (x - h) ** 2 + k)
    canvas.mark(contact, ordinate, "+", "accent")
    canvas.mark(h, k)


def _tracer(canvas, point, elapsed, rate, cycles=1.0):
    samples = max(80, min(640, canvas.width * 2))
    head = elapsed * rate
    start = head - _TAU * cycles
    previous = canvas.project(*point(start))
    for index in range(1, samples + 1):
        progress = index / samples
        current = canvas.project(*point(start + _TAU * cycles * progress))
        tone = "muted" if progress < 0.52 else "accent" if progress < 0.84 else "main"
        canvas.line(previous, current, tone)
        previous = current
    canvas.mark(*point(head))


def _lissajous(canvas, elapsed):
    delta = 0.5 + 0.45 * math.sin(elapsed * 0.27)
    _tracer(canvas, lambda u: (0.92 * math.sin(3.0 * u + delta),
                              0.87 * math.sin(2.0 * u)), elapsed, 0.82)


def _fourier(canvas, elapsed):
    phase = elapsed * 1.6
    count = _fourier_terms(elapsed)
    odd = tuple(range(1, count * 2, 2))

    def fundamental(x):
        return 0.58 * 4.0 / math.pi * math.sin(x * _TAU - phase)

    def signal(x):
        angle = x * _TAU - phase
        return 0.58 * 4.0 / math.pi * sum(math.sin(n * angle) / n for n in odd)

    # The square-wave target is dotted; its edges are deliberately not joined.
    for column in range(0, canvas.width, 3):
        x = 2.0 * column / max(1, canvas.width - 1) - 1.0
        canvas.mark(x, 0.58 if math.sin(x * _TAU - phase) >= 0 else -0.58,
                    ".", "muted")
    if count > 1:
        canvas.curve(fundamental, "accent")
    canvas.curve(signal, samples=max(canvas.width, count * 48))
    sweep = (elapsed * 0.43) % 2.0 - 1.0
    canvas.mark(sweep, signal(sweep))


def _wave_packet(canvas, elapsed):
    centre = 0.82 * math.sin(elapsed * 0.39)
    sigma = 0.29 + 0.06 * math.sin(elapsed * 0.31)

    def envelope(x):
        return 0.88 * math.exp(-((x - centre) / sigma) ** 2 / 2.0)

    def signal(x):
        return envelope(x) * math.cos(x * 27.0 - elapsed * 6.0)

    canvas.curve(envelope, "accent")
    canvas.curve(lambda x: -envelope(x), "muted")
    canvas.curve(signal)
    canvas.mark(centre, signal(centre))


def _rose(canvas, elapsed):
    angle = elapsed * 0.16

    def point(theta):
        radius = 0.90 * math.cos(5.0 * theta)
        return radius * math.cos(theta + angle), radius * math.sin(theta + angle)

    _tracer(canvas, point, elapsed, 0.72)


def _spiral(canvas, elapsed):
    turns = 3.0 * _TAU
    rotation = elapsed * 0.34
    head = (elapsed * 1.9 + turns * 0.47) % turns
    samples = max(90, min(640, canvas.width * 2))

    def point(theta):
        radius = theta * 0.92 / turns
        return radius * math.cos(theta + rotation), radius * math.sin(theta + rotation)

    previous = canvas.project(*point(0.0))
    for index in range(1, samples + 1):
        theta = turns * index / samples
        tone = "muted" if theta > head else "accent" if theta < head - 2.0 else "main"
        current = canvas.project(*point(theta))
        canvas.line(previous, current, tone)
        previous = current
    canvas.mark(*point(head))
    canvas.mark(0.0, 0.0, "+", "accent")


def _epicycles(canvas, elapsed):
    odd = (1, 3, 5, 7)
    total = sum(1.0 / n for n in odd)
    phase = elapsed * 1.45
    scale_x = (canvas.width - 1) * 0.205
    scale_y = (canvas.height - 1) * 0.43
    centre_x = (canvas.width - 1) * 0.24
    centre_y = (canvas.height - 1) * 0.5
    x, y = 0.0, 0.0
    for n in odd:
        radius = 1.0 / (n * total)
        previous = (centre_x + (x + radius) * scale_x,
                    centre_y - y * scale_y)
        for step in range(1, 33):
            angle = _TAU * step / 32
            current = (centre_x + (x + radius * math.cos(angle)) * scale_x,
                       centre_y - (y + radius * math.sin(angle)) * scale_y)
            canvas.line(previous, current, "muted", ".")
            previous = current
        new_x = x + radius * math.cos(n * phase)
        new_y = y + radius * math.sin(n * phase)
        canvas.line((centre_x + x * scale_x, centre_y - y * scale_y),
                    (centre_x + new_x * scale_x, centre_y - new_y * scale_y), "accent")
        x, y = new_x, new_y
    end = centre_x + x * scale_x, centre_y - y * scale_y
    trace_x = round((canvas.width - 1) * 0.51)
    canvas.line(end, (trace_x, end[1]), "muted", ".")
    previous = (trace_x, end[1])
    for column in range(trace_x + 1, canvas.width):
        angle = phase - (column - trace_x) / max(1, canvas.width - trace_x - 1) * _TAU * 1.5
        value = sum(math.sin(n * angle) / n for n in odd) / total
        current = (column, centre_y - value * scale_y)
        canvas.line(previous, current, "main")
        previous = current
    canvas.point(*end, "@", "hot", True)
    canvas.point(trace_x, end[1], "+", "hot", True)


_DRAW = {"harmonics": _harmonics, "parabola": _parabola,
         "lissajous": _lissajous, "fourier": _fourier,
         "wave-packet": _wave_packet, "rose": _rose,
         "spiral": _spiral, "epicycles": _epicycles}


def render_scope(mode, elapsed, width, height):
    """Return a bounded ASCII frame without mutating clocks or reading sources."""
    if width <= 0 or height <= 0:
        return {}
    canvas = _Canvas(width, height)
    canvas.grid()
    _DRAW.get(mode, _harmonics)(canvas, _time(elapsed))
    return canvas.pixels
