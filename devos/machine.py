"""In-memory register/opcode animation. Never accesses actual machine state."""

from collections import deque


BASE_ADDRESS = 0xFFFFFFFF80001000
MASK64 = (1 << 64) - 1
PROGRAM = (
    ("55", "push rbp"),
    ("48 89 e5", "mov rbp, rsp"),
    ("48 83 ec 20", "sub rsp, 0x20"),
    ("48 31 c0", "xor rax, rax"),
    ("48 8b 1f", "mov rbx, [rdi]"),
    ("48 01 d8", "add rax, rbx"),
    ("48 ff c1", "inc rcx"),
    ("0f ae f0", "mfence"),
    ("48 83 c4 20", "add rsp, 0x20"),
    ("5d", "pop rbp"),
    ("c3", "ret"),
)


class Machine:
    def __init__(self):
        self.registers = {
            "RAX": 0, "RBX": 0x1000, "RCX": 0, "RDX": 0x3F8,
            "RSI": 0xFFFFFFFF80002000, "RDI": 0xFFFFFFFF80003000,
            "RSP": 0xFFFF80000000FFF0, "RBP": 0xFFFF80000000FFF0,
            "RIP": BASE_ADDRESS, "CR3": 0x1000,
        }
        self.memory = bytearray([0x90] * 4096)
        self.program = []
        offset = 0
        for encoded, assembly in PROGRAM:
            raw = bytes.fromhex(encoded)
            self.program.append((BASE_ADDRESS + offset, encoded, assembly))
            self.memory[offset:offset + len(raw)] = raw
            offset += len(raw)
        label = b"DEV/OS::KERNEL\x00RING0\x00SIMULATED\x00"
        self.memory[32:32 + len(label)] = label
        self.trace = deque(maxlen=32)
        self.ticks = 0
        self.zero = True

    def step(self):
        index = self.ticks % len(self.program)
        address, encoded, assembly = self.program[index]
        r = self.registers
        r["RIP"] = address
        # A little scripted state machine, not an x86 emulator.
        if index == 0:
            r["RSP"] -= 8
        elif index == 1:
            r["RBP"] = r["RSP"]
        elif index == 2:
            r["RSP"] -= 0x20
        elif index == 3:
            r["RAX"] = 0
        elif index == 4:
            r["RBX"] = 0x1000 + (self.ticks // len(self.program)) * 0x40
        elif index == 5:
            r["RAX"] = (r["RAX"] + r["RBX"]) & MASK64
        elif index == 6:
            r["RCX"] = (r["RCX"] + 1) & MASK64
        elif index == 8:
            r["RSP"] += 0x20
        elif index == 9:
            r["RSP"] += 8
        self.zero = r["RAX"] == 0
        self.trace.append((address, encoded, assembly))
        self.ticks += 1
        # The writable data page changes; instruction bytes stay intact.
        self.memory[256:264] = r["RCX"].to_bytes(8, "little")
        self.memory[264:272] = r["RAX"].to_bytes(8, "little")

    def memory_rows(self, count, columns):
        """Return (address, bytes) rows around the code and the writable data page."""
        offset = 0 if (self.ticks // 48) % 2 == 0 else 224
        for row in range(count):
            start = offset + row * columns
            yield BASE_ADDRESS + start, bytes(self.memory[start:start + columns])
