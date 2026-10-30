"""Declarative scene registry. Add content here, not in the renderer."""

from .domain import Mode, Snippet
from .ekza import EKZA_MODE
from .sources import packaged_lines as source_lines


DEFAULT_MODE = "ekza"


MODES = (
    EKZA_MODE,
    Mode(
        id="bare-metal", title="BARE METAL", subtitle="RING 0 / X86-64 / NO ABSTRACTIONS",
        description="C + assembly. Registers, opcodes, memory and a rotating 4D core.",
        palette="ember", geometry="tesseract", workspace="/sys/forge", hardware=True,
        snippets=(
            Snippet("kernel/mm/paging.c", "C", "four-level virtual memory", "paging.c.txt"),
            Snippet("arch/x86_64/entry.asm", "NASM", "long-mode kernel entry", "entry.asm.txt"),
            Snippet("drivers/nvme/queue.c", "C", "lock-free submission queue", "nvme.c.txt"),
            Snippet("arch/x86_64/context.asm", "NASM", "context switch / register state", "context.asm.txt"),
        ),
        phases=("EDIT", "ASM", "CC", "LD", "BOOT", "LIVE"),
        phase_messages=("", "nasm -f elf64 arch/x86_64/entry.asm",
                        "cc -ffreestanding -mno-red-zone -O2 -c kernel/mm/paging.c",
                        "ld -nostdlib -T kernel.ld -> kernel.elf | .text aligned",
                        "[qemu] long mode active | IDT loaded | interrupts enabled",
                        "[kernel] all cores online | boot self-tests passed"),
        events=(
            ("MMU", "PML4[511] -> PDPT 0x00002000 | TLB shootdown complete"),
            ("IRQ", "APIC vector 0x20 acknowledged | EOI written"),
            ("CACHE", "L1D hit 99.2% | memory fence retired"),
            ("SMP", "cpu3: spinlock released | runqueue balanced"),
            ("DMA", "NVMe SQ tail advanced | completion queue drained"),
            ("TRACE", "CR0.PG=1 CR4.PAE=1 EFER.LME=1 | CPL=0"),
            ("PASS", "page_fault_handler: guard page isolated successfully"),
            ("ASM", "push rbp ; mov rbp, rsp ; and rsp, -16"),
            ("BUS", "PCIe link trained | BAR0 mapped uncacheable"),
            ("NOTE", "No runtime. No mercy. Just silicon."),
        ),
    ),
    Mode(
        id="lab", title="DEV LAB", subtitle="BUILD / TEST / REPEAT",
        description="The original lab: Python, Rust, TypeScript, Go and a wireframe cube.",
        palette="neon", geometry="cube", workspace="/dev/imagination", hardware=False,
        snippets=(
            Snippet("src/core/scheduler.py", "Python", "async task scheduler", "scheduler.py.txt"),
            Snippet("src/engine/pipeline.rs", "Rust", "parallel render pipeline", "pipeline.rs.txt"),
            Snippet("src/stream/transport.ts", "TypeScript", "realtime transport", "transport.ts.txt"),
            Snippet("internal/mesh/router.go", "Go", "distributed mesh", "router.go.txt"),
        ),
        phases=("EDIT", "PARSE", "BUILD", "TEST", "LINK", "READY"),
        phase_messages=("", "AST indexed | symbols resolved", "Compiling module graph | 0 errors",
                        "24 tests passed | coverage 99.8%", "Resolving symbols | chunks optimized",
                        "Build ready. Ship it (in spirit)."),
        events=(
            ("CACHE", "Incremental cache hit | 128 objects reused"),
            ("LSP", "Type inference complete | all symbols happy"),
            ("PASS", "test_parallel_dispatch ... ok (1.42ms)"),
            ("TRACE", "Worker 03: batch acknowledged | queue drained"),
            ("OPT", "Eliminated 64 redundant allocations"),
            ("WATCH", "Hot reload ready | state preserved"),
            ("NOTE", "Turning caffeine into abstractions..."),
            ("NOTE", "It works on this imaginary machine."),
        ),
    ),
    Mode(
        id="hyperspace", title="HYPERSPACE", subtitle="16 VERTICES / 32 EDGES / 4 DIMENSIONS",
        description="A geometry lab: 4D rotations, projection kernels and a large tesseract.",
        palette="ice", geometry="tesseract", workspace="/opt/hypercore", hardware=False,
        snippets=(
            Snippet("geometry/project4d.c", "C", "4D -> 3D -> 2D projection", "project4d.c.txt"),
            Snippet("shaders/neon_field.glsl", "GLSL", "procedural light field", "neon_field.glsl.txt"),
            Snippet("src/engine/pipeline.rs", "Rust", "parallel render pipeline", "pipeline.rs.txt"),
        ),
        phases=("EDIT", "ROTATE", "PROJECT", "SHADE", "RASTER", "READY"),
        phase_messages=("", "SO(4) rotation planes XY / XW / ZW initialized",
                        "Perspective divide 4D -> 3D -> terminal coordinates",
                        "Depth shading | front edges isolated", "ASCII rasterizer | 32 edges emitted",
                        "Hypercube stable | all 16 vertices accounted for"),
        events=(
            ("MATH", "R(xw) * R(zw) * R(xy) | determinant = +1.0000"),
            ("TRACE", "4D clipping plane stable | no singularities"),
            ("PASS", "rotation_norm_preserved ... ok"),
            ("GPU", "Depth buffer resolved | 32 edges / 16 vertices"),
            ("PROJ", "Perspective W=3.0 | camera Z=12.0"),
            ("NOTE", "One more dimension. Same number of deadlines."),
        ),
    ),
)

MODE_BY_ID = {mode.id: mode for mode in MODES}
