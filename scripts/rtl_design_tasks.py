"""Prepare and independently check three RTL *design* tasks: a tiny CPU, the simplest GPU and a graphics memory.

2026-11-08 사용자: "이제ALEX의 베릴로그 코딩능력을 확인해야할거같은데, CPU 간단한거랑 GPU가장 간단한거,
그리고 그래픽메모리 간단한거 만들어봐야될거 같네".

rtl_curriculum.py(고치기 과제)와 같은 방식이다. 다른 점은 처음 코드가 없다는 것 — ALEX 는 규격만 보고 처음부터 쓴다.
Python 상태 모델이 정답을 정하고, 평가받는 모델은 그것을 볼 수도 바꿀 수도 없다. 시험기는 클록 직후 값과 함께 클록 직전
값도 본다(출력은 상승 에지에서만 바뀌어야 한다). 복사된 이 스크립트는 ALEX 없이 혼자 돈다(iverilog·vvp 만 있으면).

  python scripts/rtl_design_tasks.py prepare --out <rtl_root>     # cpu8-design · gpu_fill-design · vram16-design
  /rtl cpu8-design                                                 # PC 의 ALEX 가 쓰고 시험 결과로 고친다
  python <폴더>/verify/run_tests.py check --task cpu8 --file <폴더>/cpu8.v --report r.json --execute

Never calls an LLM. check requires --execute to run a design in the simulator.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess

TASKS = {
    "cpu8": {
        "top": "cpu8",
        "groups": ["reset", "alu", "zero_flag", "move", "memory", "branch", "halt", "reset_mid", "random"],
        "outputs": ("pc", "regs", "zero", "halted"),
        "spec": """# cpu8 — 아주 작은 8비트 CPU (tiny single-cycle CPU)
Write synthesizable Verilog-2001:

module cpu8(input wire clk, input wire rst,
            output wire [7:0] pc, input wire [15:0] instr,
            output wire [31:0] regs, output wire zero, output wire halted);

State: four 8-bit registers r0..r3, a 16-byte data memory inside the CPU, a zero flag Z, a halted flag and an 8-bit pc.
`pc` is the address of the instruction executed in this cycle. The testbench drives `instr` = program[pc] combinationally.
`regs` = {r3, r2, r1, r0}. `zero` = Z. `halted` = the halted flag. All four outputs come straight from state registers.

Timing: single cycle. Every instruction completes at one rising edge of clk; all state changes only at rising edges.
Reset: synchronous, active-high, highest priority. At a rising edge with rst=1: pc=0, r0..r3=0, Z=0, halted=0 and all
16 data-memory bytes become 0.
While halted=1 nothing changes (pc, registers, Z, memory) until a reset.

Instruction: op=instr[15:12], rd=instr[11:10], rs=instr[9:8], imm=instr[7:0]. Unless stated, pc <= pc+1 (8-bit, 255 wraps to 0).
- 0 NOP
- 1 LDI  rd <= imm
- 2 MOV  rd <= rs
- 3 ADD  rd <= rd + rs (8-bit, carry discarded); Z <= (result == 0)
- 4 SUB  rd <= rd - rs (8-bit, wraps); Z <= (result == 0)
- 5 AND  rd <= rd & rs; Z <= (result == 0)
- 6 OR   rd <= rd | rs; Z <= (result == 0)
- 7 XOR  rd <= rd ^ rs; Z <= (result == 0)
- 8 ADDI rd <= rd + imm (8-bit); Z <= (result == 0)
- 9 LD   rd <= mem[imm[3:0]]
- A ST   mem[imm[3:0]] <= rs
- B JMP  pc <= imm
- C JZ   pc <= Z ? imm : pc+1
- D JNZ  pc <= Z ? pc+1 : imm
- E NOP (reserved)
- F HALT halted <= 1; pc stays the same
Only ops 3..8 change Z; every other instruction keeps Z. rd and rs may be the same register (ADD r1,r1 doubles r1).
LD/ST use only imm[3:0] as the address. Do not change the ports, this specification or the testbench.
""",
    },
    "gpu_fill": {
        "top": "gpu_fill",
        "groups": ["reset", "single_pixel", "rectangle", "full_screen", "clipping", "zero_size", "busy_ignores_start",
                   "back_to_back", "reset_during", "random"],
        "outputs": ("busy", "we", "waddr", "wdata"),
        "spec": """# gpu_fill — 가장 간단한 GPU: 사각형 채우기 (rectangle fill engine)
Write synthesizable Verilog-2001:

module gpu_fill(input wire clk, input wire rst, input wire start,
                input wire [3:0] x0, input wire [3:0] y0, input wire [4:0] w, input wire [4:0] h,
                input wire [7:0] color,
                output reg busy, output reg we, output reg [7:0] waddr, output reg [7:0] wdata);

The screen is 16x16 pixels; the framebuffer address of pixel (x, y) is y*16 + x. The engine draws a filled rectangle
by emitting one pixel write per clock on (we, waddr, wdata). All outputs are registered and change only at rising edges.

Reset: synchronous, active-high, highest priority. At a rising edge with rst=1: busy=0, we=0.
Accepting a command: at a rising edge where busy was 0 before the edge and start=1, the engine takes x0, y0, w, h and
color at that edge and keeps them; later changes of these inputs do not affect the drawing. While busy=1, start is ignored.
Pixels drawn: every (x, y) with x0 <= x < x0+w and y0 <= y < y0+h that is on the screen (x <= 15 and y <= 15).
Off-screen pixels are clipped: they are not written and take no clock. w or h may be 0..16.
If no pixel is drawn (w == 0 or h == 0), the command is accepted but nothing happens: busy stays 0 and we stays 0.
Otherwise, at the accepting edge busy becomes 1 and the first pixel is output (we=1, waddr, wdata=color). At each
following edge the next pixel is output, in row-major order: row y0 from left to right, then row y0+1, and so on.
At the edge after the last pixel was output, busy=0 and we=0. So a command with N pixels gives exactly N cycles with
we=1 and busy=1, and a new command can be accepted at the edge after that.
When we=0, waddr and wdata may hold any value (they are not checked). Do not change the ports, this specification or
the testbench.
""",
    },
    "vram16": {
        "top": "vram16",
        "groups": ["reset", "scan_order", "write_then_read", "write_first", "write_elsewhere", "reset_clears", "random"],
        "outputs": ("x", "y", "pixel", "frame_start"),
        "spec": """# vram16 — 그래픽 메모리: 16x16 프레임버퍼와 화면 내보내기 (framebuffer with scan-out)
Write synthesizable Verilog-2001:

module vram16(input wire clk, input wire rst,
              input wire we, input wire [7:0] waddr, input wire [7:0] wdata,
              output reg [3:0] x, output reg [3:0] y, output reg [7:0] pixel, output reg frame_start);

Memory: 256 bytes, one per pixel; the address of pixel (x, y) is y*16 + x. A drawing unit writes through (we, waddr,
wdata). A display reads the picture through the scan-out outputs. All outputs are registered and change only at rising edges.

Reset: synchronous, active-high, highest priority (a write requested in the same cycle is ignored). At a rising edge with
rst=1: all 256 bytes become 0, x=0, y=0, pixel=0, frame_start=1.
Scan-out: at every other rising edge (x, y) moves to the next pixel in row-major order: x+1; after x=15 comes x=0 and
y+1; after (15, 15) comes (0, 0). frame_start=1 exactly in the cycles where (x, y) = (0, 0), otherwise 0.
pixel is the byte of the pixel (x, y) shown in the same cycle: at the edge that moves to a new (x, y), pixel becomes
that address's byte. If the same edge also writes that address, pixel shows the new byte (write-first).
Write: at a rising edge with rst=0 and we=1, memory[waddr] <= wdata. Writes to other addresses do not change pixel.
Do not change the ports, this specification or the testbench.
""",
    },
}

# ── cpu8 helpers ──────────────────────────────────────────────────────────
OPS = {"NOP": 0, "LDI": 1, "MOV": 2, "ADD": 3, "SUB": 4, "AND": 5, "OR": 6, "XOR": 7, "ADDI": 8, "LD": 9, "ST": 10,
       "JMP": 11, "JZ": 12, "JNZ": 13, "RES": 14, "HALT": 15}


def asm(op: str, rd: int = 0, rs: int = 0, imm: int = 0) -> int:
    return (OPS[op] << 12) | ((rd & 3) << 10) | ((rs & 3) << 8) | (imm & 0xFF)


def cpu_program(rng: random.Random, length: int) -> list[int]:
    """Random program without HALT; jumps stay inside the program so loops and branches mix with data work."""
    program = []
    for _ in range(length):
        op = rng.choice([o for o in OPS if o != "HALT"])
        imm = rng.randrange(length) if op in ("JMP", "JZ", "JNZ") else rng.randrange(256)
        program.append(asm(op, rng.randrange(4), rng.randrange(4), imm))
    return program


def vectors(task: str, seed: int) -> list[tuple[str, tuple[int, ...], list[int] | None]]:
    """(group, inputs, program) rows. A program (cpu8 only) is loaded into the testbench ROM before its row."""
    rng = random.Random(seed)
    rows: list[tuple[str, tuple[int, ...], list[int] | None]] = []

    if task == "cpu8":
        def run(group, program, cycles, resets=()):
            for n in range(cycles):
                rows.append((group, (int(n == 0 or n in resets),), program if n == 0 else None))
        run("reset", [asm("LDI", 0, 0, 5), asm("ADDI", 0, 0, 3), asm("ST", 0, 0, 2), asm("JMP", imm=0)], 6)
        run("alu", [asm("LDI", 0, 0, 0x0F), asm("LDI", 1, 0, 0xF0), asm("ADD", 0, 1), asm("ADDI", 0, 0, 1),
                    asm("LDI", 2, 0, 0x55), asm("XOR", 2, 2), asm("OR", 2, 1), asm("AND", 2, 0), asm("LDI", 3, 0, 0x10),
                    asm("SUB", 3, 1), asm("SUB", 1, 1), asm("ADD", 3, 3), asm("AND", 3, 1), asm("HALT")], 16)
        run("zero_flag", [asm("LDI", 0, 0, 1), asm("SUB", 0, 0), asm("LDI", 1, 0, 7), asm("MOV", 2, 1), asm("ST", 1, 1, 4),
                          asm("LD", 3, 0, 4), asm("JMP", imm=7), asm("NOP"), asm("ADD", 1, 1), asm("LDI", 0, 0, 0),
                          asm("MOV", 3, 0), asm("XOR", 1, 1), asm("ADDI", 2, 0, 0xF9), asm("HALT")], 16)
        run("move", [asm("LDI", 0, 0, 0xA1), asm("MOV", 1, 0), asm("MOV", 2, 1), asm("MOV", 3, 2), asm("LDI", 0, 0, 0x3C),
                     asm("MOV", 0, 0), asm("MOV", 3, 0), asm("MOV", 1, 3), asm("HALT")], 10)
        run("memory", [asm("LDI", 0, 0, 0x11), asm("LDI", 1, 0, 0x22), asm("ST", 0, 0, 0), asm("ST", 0, 1, 15),
                       asm("ST", 0, 1, 0x13), asm("LD", 2, 0, 0), asm("LD", 3, 0, 0x0F), asm("LD", 0, 0, 3), asm("LD", 1, 0, 9),
                       asm("LD", 1, 0, 0xA3), asm("LD", 0, 0, 0x50),
                       asm("ADDI", 2, 0, 1), asm("ST", 0, 2, 0xF0), asm("LD", 3, 0, 0), asm("HALT")], 14)
        loop = [asm("LDI", 0, 0, 5), asm("LDI", 1, 0, 0), asm("ADD", 1, 0), asm("ADDI", 0, 0, 0xFF), asm("JNZ", imm=2),
                asm("JZ", imm=7), asm("HALT"), asm("LDI", 2, 0, 1), asm("SUB", 2, 2), asm("JNZ", imm=0), asm("JZ", imm=12),
                asm("HALT"), asm("JMP", imm=254)]
        wrap = loop + [asm("NOP")] * (254 - len(loop)) + [asm("LDI", 3, 0, 0x77), asm("NOP")]
        run("branch", wrap, 28)
        far = [asm("NOP")] * 256   # straight through pc 62..66 (bit 6 turns on), taken JZ/JNZ to addresses >= 128
        far[0], far[0x42], far[0x43], far[0x44] = asm("JMP", imm=0x3E), asm("LDI", 0, 0, 1), asm("SUB", 0, 0), asm("JZ", imm=0xC4)
        far[0xC4], far[0xC5], far[0xC6] = asm("LDI", 1, 0, 0x11), asm("ADDI", 1, 0, 0), asm("JNZ", imm=0x86)
        far[0x86], far[0x87], far[0xF0] = asm("LDI", 2, 0, 0x22), asm("JMP", imm=0xF0), asm("HALT")
        run("branch", far, 20)
        run("halt", [asm("LDI", 0, 0, 9), asm("HALT"), asm("LDI", 0, 0, 1), asm("ST", 0, 0, 1)], 6)
        # halted means halted even if the instruction at pc changes (a new program is loaded without a reset)
        rows.append(("halt", (0,), [asm("LDI", 0, 0, 0x99), asm("LDI", 1, 0, 0x98), asm("JMP", imm=0)]))
        rows.extend([("halt", (0,), None)] * 3)
        run("halt", [asm("LDI", 0, 0, 9), asm("ST", 0, 0, 1), asm("HALT"), asm("LD", 1, 0, 1)], 6, resets=(4,))
        fill = [asm("LDI", 0, 0, 0xFF)] + [asm("ST", 0, 0, a) for a in range(16)]  # every bit set + [asm("SUB", 1, 1), asm("JMP", imm=18)]
        run("reset_mid", fill, 21)
        # reset while that program is still running: registers, Z and every one of the 16 stored bytes must be gone
        run("reset_mid", [asm("LD", a & 3, 0, a) for a in range(16)] + [asm("ADDI", 1, 0, 0), asm("HALT")], 19)
        for _ in range(10):
            run("random", cpu_program(rng, 40), 90)
        for _ in range(8):   # the whole 256-word program space: jumps and branches to any address
            run("random", cpu_program(rng, 256), 160)
    elif task == "gpu_fill":
        idle = (0, 0, 0, 0, 0, 0)

        def reset(group):
            rows.append((group, (1, 1, 3, 3, 4, 4, 0xEE), None))  # start during reset is ignored

        def command(group, x0, y0, w, h, color, cycles, during=idle):
            rows.append((group, (0, 1, x0, y0, w, h, color), None))
            for _ in range(cycles):
                rows.append((group, (0,) + during, None))

        reset("reset")
        rows.append(("reset", (0,) + idle, None))
        reset("single_pixel")
        command("single_pixel", 7, 9, 1, 1, 0x5A, 3)
        reset("rectangle")
        command("rectangle", 2, 5, 3, 2, 0x81, 8)
        command("rectangle", 0, 0, 1, 4, 0x0C, 6)
        reset("full_screen")
        command("full_screen", 0, 0, 16, 16, 0xFF, 258)
        reset("clipping")
        command("clipping", 14, 3, 5, 2, 0x31, 6)
        command("clipping", 6, 15, 2, 3, 0x32, 4)
        command("clipping", 15, 15, 16, 16, 0x33, 3)
        reset("zero_size")
        command("zero_size", 4, 4, 0, 5, 0x44, 2)
        command("zero_size", 4, 4, 5, 0, 0x45, 2)
        command("zero_size", 1, 1, 2, 1, 0x46, 4)
        reset("busy_ignores_start")
        command("busy_ignores_start", 1, 2, 4, 2, 0x61, 10, during=(1, 9, 9, 3, 3, 0x99))
        reset("back_to_back")
        rows.append(("back_to_back", (0, 1, 0, 0, 2, 1, 0x71), None))
        for _ in range(8):
            rows.append(("back_to_back", (0, 1, 5, 6, 2, 1, 0x72), None))
        reset("reset_during")
        command("reset_during", 0, 0, 8, 8, 0xA0, 4)
        reset("reset_during")
        rows.append(("reset_during", (0,) + idle, None))
        command("reset_during", 3, 3, 1, 2, 0xA1, 3)
        reset("random")
        for _ in range(900):
            rows.append(("random", (int(rng.randrange(150) == 0), int(rng.randrange(4) == 0), rng.randrange(16),
                                    rng.randrange(16), rng.randrange(17) if rng.randrange(6) else 0, rng.randrange(1, 6),
                                    rng.randrange(256)), None))
    elif task == "vram16":
        def reset(group, we=0):
            rows.append((group, (1, we, 0x00, 0x5E), None))

        reset("reset", we=1)
        rows.append(("reset", (0, 0, 0, 0), None))
        reset("scan_order")
        for _ in range(520):
            rows.append(("scan_order", (0, 0, 0, 0), None))
        reset("write_then_read")
        for address in range(256):
            rows.append(("write_then_read", (0, 1, address, (address * 37 + 11) & 0xFF), None))
        for _ in range(260):
            rows.append(("write_then_read", (0, 0, 0, 0), None))
        reset("write_first")
        for n in range(1, 40):  # after n edges (x, y) is at address n: write that very address at the same edge
            rows.append(("write_first", (0, 1, n & 0xFF, (0xC0 + n) & 0xFF), None))
        for _ in range(260):
            rows.append(("write_first", (0, 0, 0, 0), None))
        reset("write_elsewhere")
        for n in range(1, 60):
            rows.append(("write_elsewhere", (0, 1, (n + 128) & 0xFF, 0x90 + (n & 0x0F)), None))
        for _ in range(140):
            rows.append(("write_elsewhere", (0, 0, 0, 0), None))
        reset("reset_clears")
        for address in range(256):   # every bit of every byte set, so a reset that misses any bit shows up in the scan
            rows.append(("reset_clears", (0, 1, address, 0xFF), None))
        reset("reset_clears", we=1)
        for _ in range(258):
            rows.append(("reset_clears", (0, 0, 0, 0), None))
        reset("random")
        for _ in range(800):
            rows.append(("random", (int(rng.randrange(300) == 0), rng.randrange(2), rng.randrange(256), rng.randrange(256)), None))
    else:
        raise ValueError("Unknown design task")
    return rows


def expected(task: str, rows: list) -> list[tuple[int, ...]]:
    """Reference state models. Outputs after each rising edge; gpu_fill uses None for unchecked waddr/wdata."""
    result: list[tuple[int, ...]] = []
    if task == "cpu8":
        rom = [0] * 256
        pc = z = halted = 0
        regs = [0, 0, 0, 0]
        mem = [0] * 16
        for _group, (rst,), program in rows:
            if program is not None:
                rom = (list(program) + [0] * 256)[:256]
            if rst:
                pc = z = halted = 0
                regs, mem = [0, 0, 0, 0], [0] * 16
            elif not halted:
                word = rom[pc]
                op, rd, rs, imm = word >> 12, (word >> 10) & 3, (word >> 8) & 3, word & 0xFF
                after = (pc + 1) & 0xFF
                if op == 1:
                    regs[rd] = imm
                elif op == 2:
                    regs[rd] = regs[rs]
                elif 3 <= op <= 8:
                    a, b = regs[rd], imm if op == 8 else regs[rs]
                    value = {3: a + b, 4: a - b, 5: a & b, 6: a | b, 7: a ^ b, 8: a + b}[op] & 0xFF
                    regs[rd], z = value, int(value == 0)
                elif op == 9:
                    regs[rd] = mem[imm & 15]
                elif op == 10:
                    mem[imm & 15] = regs[rs]
                elif op == 11:
                    after = imm
                elif op == 12:
                    after = imm if z else after
                elif op == 13:
                    after = after if z else imm
                elif op == 15:
                    halted, after = 1, pc
                pc = after
            result.append((pc, regs[3] << 24 | regs[2] << 16 | regs[1] << 8 | regs[0], z, halted))
    elif task == "gpu_fill":
        busy = we = 0
        address = data = 0
        pending: list[int] = []
        for _group, (rst, start, x0, y0, w, h, color), _ in rows:
            if rst:
                busy, we, pending = 0, 0, []
            elif not busy:
                pixels = [y * 16 + x for y in range(y0, min(y0 + h, 16)) for x in range(x0, min(x0 + w, 16))]
                if start and pixels:
                    busy, we, address, data, pending = 1, 1, pixels[0], color, pixels[1:]
                else:
                    we = 0
            elif pending:
                we, address = 1, pending.pop(0)
            else:
                busy, we = 0, 0
            result.append((busy, we, address if we else None, data if we else None))
    elif task == "vram16":
        mem = [0] * 256
        x = y = pixel = frame = 0
        for _group, (rst, we, waddr, wdata), _ in rows:
            if rst:
                mem, x, y, pixel, frame = [0] * 256, 0, 0, 0, 1
            else:
                x, y = (0, (y + 1) & 15) if x == 15 else (x + 1, y)
                here = y * 16 + x
                pixel = wdata if we and waddr == here else mem[here]
                if we:
                    mem[waddr] = wdata
                frame = int(x == 0 and y == 0)
            result.append((x, y, pixel, frame))
    else:
        raise ValueError("Unknown design task")
    return result


def bench(task: str, rows: list) -> str:
    """Drive each row with clk low, sample before the edge (PRE) and after it (ROW)."""
    if task == "cpu8":
        head = ("reg clk, rst; wire [7:0] pc; wire [15:0] instr; wire [31:0] regs; wire zero, halted;\n"
                "reg [15:0] rom [0:255]; integer k;\nassign instr = rom[pc];\n"
                "cpu8 dut(.clk(clk), .rst(rst), .pc(pc), .instr(instr), .regs(regs), .zero(zero), .halted(halted));")
        fields, show, fmt = ["rst"], "pc, regs, zero, halted", "%h %h %h %h"
    elif task == "gpu_fill":
        head = ("reg clk, rst, start; reg [3:0] x0, y0; reg [4:0] w, h; reg [7:0] color; wire busy, we; wire [7:0] waddr, wdata;\n"
                "gpu_fill dut(.clk(clk), .rst(rst), .start(start), .x0(x0), .y0(y0), .w(w), .h(h), .color(color),\n"
                "  .busy(busy), .we(we), .waddr(waddr), .wdata(wdata));")
        fields, show, fmt = ["rst", "start", "x0", "y0", "w", "h", "color"], "busy, we, waddr, wdata", "%h %h %h %h"
    else:
        head = ("reg clk, rst, we; reg [7:0] waddr, wdata; wire [3:0] x, y; wire [7:0] pixel; wire frame_start;\n"
                "vram16 dut(.clk(clk), .rst(rst), .we(we), .waddr(waddr), .wdata(wdata), .x(x), .y(y), .pixel(pixel),\n"
                "  .frame_start(frame_start));")
        fields, show, fmt = ["rst", "we", "waddr", "wdata"], "x, y, pixel, frame_start", "%h %h %h %h"
    statements = []
    for i, (_group, inputs, program) in enumerate(rows):
        if program is not None:
            words = "".join(f" rom[{n}]=16'h{word:04x};" for n, word in enumerate(program[:256]) if word)
            statements.append(f"for (k = 0; k < 256; k = k + 1) rom[k] = 16'h0000;{words}")
        setters = " ".join(f"{name}={value};" for name, value in zip(fields, inputs))
        statements.append(f'clk=0; {setters} #2; $display("PRE {i} {fmt}", {show}); clk=1; #1; '
                          f'$display("ROW {i} {fmt}", {show}); #1;')
    return ("`timescale 1ns/1ps\nmodule tb;\n" + head + "\ninitial begin\n" + "\n".join(statements)
            + "\n$finish;\nend\nendmodule\n")


def trace(output: str, tag: str, count: int, width: int) -> list[tuple]:
    """Do not accept truncated, duplicated or fabricated row numbering."""
    lines = [line.split() for line in output.splitlines() if line.startswith(tag + " ")]
    if (len(lines) != count or any(len(line) != width + 2 or line[1] != str(i)
                                    or any(not re.fullmatch("[0-9a-fA-FxXzZ]+", v) for v in line[2:])
                                    for i, line in enumerate(lines))):
        raise ValueError("Incomplete, duplicate or malformed simulator trace")
    return [tuple(None if re.search("[xXzZ]", v) else int(v, 16) for v in line[2:]) for line in lines]


def evaluate(task: str, rows: list, output: str) -> dict:
    """Every row must appear once in order; unknown (x/z) outputs always fail.
    After the edge the outputs must match the model; before the edge they must still hold the previous values."""
    names = TASKS[task]["outputs"]
    after = trace(output, "ROW", len(rows), len(names))
    before = trace(output, "PRE", len(rows), len(names))
    report = {name: {"ok": True, "problems": [], "vectors": 0, "about": name} for name in TASKS[task]["groups"]}
    want = expected(task, rows)

    def same(observed, model):
        return all(m is None or o == m for o, m in zip(observed, model))

    def show(values):
        return ", ".join(f"{name}={'?' if v is None else hex(v)}" for name, v in zip(names, values))

    for i, ((group, inputs, _), model, observed, early) in enumerate(zip(rows, want, after, before)):
        row = report[group]
        row["vectors"] += 1
        problem = ""
        if not same(observed, model):
            problem = f"cycle {i} after the edge: inputs={inputs}, expected {show(model)}, actual {show(observed)}"
        elif i and not same(early, want[i - 1]):
            problem = (f"cycle {i} before the edge: outputs must still be {show(want[i - 1])} "
                       f"(registered, change only at the rising edge), actual {show(early)}")
        if problem:
            row["ok"] = False
            if not row["problems"]:
                row["first_divergence"] = problem
                row["problems"].append(problem)
    return report


def tool_env() -> dict:
    env = dict(os.environ)
    tools = Path(env.get("OSS_CAD_SUITE", "C:/oss-cad-suite"))
    env["PATH"] = os.pathsep.join([str(tools / "bin"), str(tools / "lib"), env.get("PATH", "")])
    return env


def check(task: str, dut: Path, report_path: Path, seed: int, work: Path) -> dict:
    rows = vectors(task, seed)
    work.mkdir(parents=True, exist_ok=True)
    (work / "tb.v").write_text(bench(task, rows), encoding="utf-8")
    report_path.unlink(missing_ok=True)
    image = work / "sim.vvp"
    image.unlink(missing_ok=True)
    env = tool_env()

    def run(command):
        command[0] = shutil.which(command[0], path=env["PATH"]) or command[0]
        done = subprocess.run(command, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)
        if done.returncode:
            raise RuntimeError((done.stdout + done.stderr)[-2500:])
        return done.stdout
    run(["iverilog", "-g2005", "-s", "tb", "-o", str(image), str(dut.resolve()), str(work / "tb.v")])
    data = evaluate(task, rows, run(["vvp", "-n", str(image)]))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def prepare(root: Path) -> list[Path]:
    """New folders only (name-design). No starting code: the model writes the design from the specification."""
    destinations = [root / (task + "-design") for task in TASKS]
    if any(path.exists() for path in destinations):
        raise ValueError("Refusing to overwrite existing design-task folders")
    script = Path(__file__).read_text(encoding="utf-8")
    for (name, task), folder in zip(TASKS.items(), destinations):
        (folder / "verify").mkdir(parents=True)
        (folder / "spec.md").write_text(task["spec"], encoding="utf-8")
        (folder / "verify/run_tests.py").write_text(script, encoding="utf-8")
        (folder / "rtl.json").write_text(json.dumps({
            "top": task["top"], "file": task["top"] + ".v", "spec": "spec.md",
            "test": ["{python}", "verify/run_tests.py", "check", "--task", name, "--file", "{file}",
                     "--report", "{report}", "--execute"],
            "expected_tests": task["groups"], "max_rounds": 8,
        }, indent=2), encoding="utf-8")
    return destinations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser("prepare")
    builder.add_argument("--out", type=Path, required=True)
    runner = commands.add_parser("check")
    runner.add_argument("--task", choices=list(TASKS), required=True)
    runner.add_argument("--file", type=Path, required=True)
    runner.add_argument("--report", type=Path, required=True)
    runner.add_argument("--seed", type=int, default=20261108)
    runner.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            print(json.dumps([str(path) for path in prepare(args.out.resolve())]))
            return 0
        if not args.execute:
            raise ValueError("Simulation requires explicit --execute")
        data = check(args.task, args.file, args.report.resolve(), args.seed, args.file.resolve().parent / "verify/work")
        print(json.dumps({"passed": sum(row["ok"] for row in data.values()), "total": len(data),
                          "cycles": sum(row["vectors"] for row in data.values()), "seed": args.seed}))
        return int(not all(row["ok"] for row in data.values()))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
