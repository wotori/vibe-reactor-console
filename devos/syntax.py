"""Small, side-effect-free syntax colorizer, including C and NASM."""

import re
from functools import lru_cache


KEYWORDS = set("from import as async await class def return try finally while for in "
               "if else elif with pass raise yield True False None self pub fn use "
               "impl struct let mut match loop move drop const export private readonly "
               "type new null void string function package func go defer select case "
               "range map chan int float vec2 vec3 vec4 mat2 uniform layout out break "
               "static inline volatile unsigned signed long short char uint8_t uint16_t "
               "uint32_t uint64_t uintptr_t size_t typedef enum union sizeof bool "
               "__attribute__ __asm__ global section bits align extern equ dq dw db "
               "default rel resb resq".split())
MNEMONICS = set("mov movq movabs lea push pop pushfq popfq call ret iretq swapgs cli sti "
                "hlt lgdt lidt ltr wrmsr rdmsr cpuid xor or and test cmp jne je jz jnz "
                "jmp jbe ja add sub inc dec shl shr sar imul mul div idiv neg not "
                "rep stosq movsq cld mfence lfence sfence lock xchg pause nop invlpg "
                "movdqa pxor syscall sysretq bts clflushopt vzeroupper".split())
REGISTERS = set("rax rbx rcx rdx rsi rdi rbp rsp rip eax ebx ecx edx esi edi esp ebp "
                "ax bx cx dx al bl cl dl cs ds es fs gs ss cr0 cr2 cr3 cr4 efer "
                "r8 r9 r10 r11 r12 r13 r14 r15 xmm0 xmm1 xmm2 xmm3".split())
ATOM = r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|\b(?:0x[\da-fA-F]+|\d+(?:\.\d+)?)\b|[A-Za-z_]\w*'
GENERAL = re.compile(r"//.*|/\*.*|\*/|" + ATOM)
ASSEMBLY = re.compile(r";.*|" + ATOM)
PYTHON = re.compile(r"\#.*|" + ATOM)


@lru_cache(maxsize=2048)
def spans(line, language):
    result = []
    assembly = language == "NASM"
    pattern = ASSEMBLY if assembly else PYTHON if language == "Python" else GENERAL
    for match in pattern.finditer(line):
        token = match.group()
        if token.startswith(("//", "/*", "*/")) or (assembly and token.startswith(";")) or (
                language == "Python" and token.startswith("#")):
            tone = "muted"
        elif token.startswith(('"', "'")):
            tone = "main"
        elif token[0].isdigit():
            tone = "warn"
        elif token.lower() in REGISTERS:
            tone = "accent"
        elif token.lower() in MNEMONICS or token in KEYWORDS:
            tone = "hot"
        elif token[0].isupper():
            tone = "accent"
        else:
            continue
        result.append((match.start(), token, tone))
    return tuple(result)
