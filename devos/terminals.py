"""Two independent human-owned terminals; view changes never restart jobs."""

from dataclasses import dataclass
from pathlib import Path

from .shell import EmbeddedTerminal, TerminalFrame


SLOTS = ("left", "right")


@dataclass(frozen=True)
class TerminalSlotSnapshot:
    id: str
    frame: TerminalFrame
    focused: bool
    visible: bool
    command: tuple
    cwd: str


class TerminalManager:
    def __init__(self, width=80, height=24, cwd=None, terminal_factory=EmbeddedTerminal):
        self.width, self.height = width, height
        self.cwd = str(Path(cwd or Path.cwd()).resolve())
        self.factory = terminal_factory
        self.focused = "left"
        self.visible = set(SLOTS)
        self._terminals = {}
        self._configuration = {}
        self._errors = {}

    def _slot(self, slot):
        slot = slot or self.focused
        if slot not in SLOTS:
            raise ValueError("Terminal slot must be left or right")
        return slot

    def configure(self, slot, command=None, cwd=None):
        slot = self._slot(slot)
        if slot in self._terminals:
            raise RuntimeError("Close this terminal before reconfiguring it")
        self._configuration[slot] = (tuple(command or ()), str(Path(cwd or self.cwd).resolve()))

    def create(self, slot="left", command=None, cwd=None):
        slot = self._slot(slot)
        if slot in self._terminals:
            return self._terminals[slot]
        if command is not None or cwd is not None or slot not in self._configuration:
            self.configure(slot, command, cwd)
        command, cwd = self._configuration[slot]
        try:
            terminal = self.factory(self.width, self.height, cwd=cwd, command=command or None)
            terminal.set_visible(slot in self.visible)
            self._terminals[slot] = terminal
            self._errors.pop(slot, None)
            return terminal
        except (OSError, RuntimeError) as exc:
            self._errors[slot] = TerminalFrame(dead=True, status=str(exc))
            raise

    def focus(self, slot):
        self.focused = self._slot(slot)
        return self.focused

    def cycle_focus(self):
        return self.focus("right" if self.focused == "left" else "left")

    def send(self, data, slot=None):
        terminal = self._terminals.get(self._slot(slot))
        return terminal.send(data) if terminal is not None else False

    def resize(self, width, height, slot=None):
        width, height = max(2, width), max(1, height)
        if slot is None:
            self.width, self.height = width, height
            terminals = self._terminals.values()
        else:
            terminal = self._terminals.get(self._slot(slot))
            terminals = (terminal,) if terminal else ()
        for terminal in terminals:
            terminal.resize(width, height)

    def set_visible(self, slots):
        slots = set(slots)
        if not slots.issubset(SLOTS):
            raise ValueError("Unknown terminal slot")
        self.visible = slots
        for slot, terminal in self._terminals.items():
            terminal.set_visible(slot in slots)

    def snapshot(self, slot=None):
        slot = self._slot(slot)
        terminal = self._terminals.get(slot)
        return terminal.snapshot() if terminal else self._errors.get(
            slot, TerminalFrame(status="NOT STARTED / human-owned terminal"))

    def snapshots(self):
        return tuple(TerminalSlotSnapshot(slot, self.snapshot(slot), slot == self.focused,
                                         slot in self.visible, *self._configuration.get(slot, ((), self.cwd)))
                     for slot in SLOTS)

    def close(self, slot=None):
        slots = (self._slot(slot),) if slot else SLOTS
        for name in slots:
            terminal = self._terminals.pop(name, None)
            if terminal is not None:
                terminal.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
