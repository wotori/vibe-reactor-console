"""Scene timelines, typewriter, build stages and synthetic telemetry."""

from collections import deque
import math
import random
import time

from .domain import LogEntry
from .machine import Machine
from .sources import SourceProvider


def clamp(value, low, high):
    return max(low, min(high, value))


class Simulation:
    def __init__(self, mode, seed=None, wall_clock=None, sources=None):
        self.mode = mode
        self.sources = sources if sources is not None else SourceProvider()
        self.rng = random.Random(seed)
        self.wall_clock = time.time() if wall_clock is None else wall_clock
        self.elapsed = 0.0
        self.builds = self.tests = self.total_lines = 0
        self.logs = deque(maxlen=240)
        self.history = deque([0.3] * 160, maxlen=160)
        self.machine = Machine()
        self.next_log = self.next_sample = 0.0
        self.file_index = -1
        self.load_file()
        self.log("SYSTEM", mode.title + " online | all activity simulated")

    def log(self, kind, message):
        stamp = time.strftime("%H:%M:%S", time.localtime(self.wall_clock + self.elapsed))
        self.logs.append(LogEntry(stamp, kind, message))

    def load_file(self, advance=True):
        if advance:
            self.file_index = (self.file_index + 1) % len(self.mode.snippets)
        self.snippet = self.mode.snippets[self.file_index]
        self.source = self.sources.load(self.snippet)
        self.lines = self.source.lines
        self.line_index = self.char_index = 0
        self.credit = self.phase_time = self.file_time = 0.0
        self.phase = 0
        self.build_id = self.rng.getrandbits(28)
        self.log("EDIT", "Opening " + self.snippet.path)
        if self.snippet.source_path:
            self.log("SOURCE", "{} L{}-{} / {}".format(
                self.source.kind, self.source.first_line, self.source.last_line, self.source.digest))
            if self.source.note:
                self.log("SOURCE", self.source.note)

    @property
    def typed_lines(self):
        result = list(self.lines[:self.line_index])
        if self.line_index < len(self.lines):
            result.append(self.lines[self.line_index][:self.char_index])
        return result

    @property
    def progress(self):
        if self.phase == 0:
            return self.line_index / max(1, len(self.lines)) * 0.55
        return min(1.0, 0.55 + (self.phase - 1 + self.phase_time / 1.7) * 0.1125)

    def update(self, dt):
        if dt <= 0:
            return
        self.elapsed += dt
        self.file_time += dt
        if self.phase == 0:
            self.credit += dt * (108 if self.mode.hardware else 85)
            while self.credit >= 1 and self.line_index < len(self.lines):
                self.credit -= 1
                if self.char_index < len(self.lines[self.line_index]):
                    self.char_index += 1
                else:
                    self.line_index += 1
                    self.total_lines += 1
                    self.char_index = 0
                    self.credit -= self.rng.uniform(0.4, 2.0)
            if self.line_index == len(self.lines):
                self.phase = 1
                self.phase_time = 0.0
                self.log("TYPED", self.snippet.path + " [display only]")
                self.log(self.mode.phases[1], self.mode.phase_messages[1])
        else:
            self.phase_time += dt
            while self.phase_time >= 1.7:
                self.phase_time -= 1.7
                self.phase += 1
                if self.phase >= len(self.mode.phases):
                    self.load_file()
                    break
                if self.phase == 3:
                    self.tests += 24
                if self.phase == 5:
                    self.builds += 1
                self.log(self.mode.phases[self.phase], self.mode.phase_messages[self.phase])

        while self.elapsed >= self.next_sample:
            load = clamp(0.47 + math.sin(self.next_sample * 0.83) * 0.23
                         + self.rng.uniform(-0.1, 0.1), 0.08, 0.98)
            self.history.append(load)
            self.machine.step()
            self.next_sample += 0.16
        if self.elapsed >= self.next_log:
            kind, message = self.rng.choice(self.mode.events)
            self.log(kind, message)
            self.next_log = self.elapsed + self.rng.uniform(0.25, 0.75)
