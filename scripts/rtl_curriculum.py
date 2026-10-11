"""Prepare and independently check two RTL repair tasks; never call an LLM.

prepare --out creates new folders only. check requires --execute to run a DUT.
The Python state models decide expected outputs; the model never changes them.
The copied verification script also works independently of ALEX on Linux.
"""
from __future__ import annotations

import argparse
from collections import deque
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess

TASKS = {
    "fifo3": {
        "top": "fifo3",
        "groups": ["reset", "empty", "capacity", "simultaneous", "wrap", "random"],
        "spec": """# FIFO, depth 3
Verilog-2001 module fifo3(input wire clk, input wire rst, input wire wr,
input wire rd, input wire [7:0] din, output reg [7:0] dout,
output wire full, output wire empty).
Depth exactly 3, one clock, synchronous active-high reset.
Reset has priority: empty=1, full=0, dout=0 after the rising edge; ignore writes/reads.
Accept write iff wr && !full BEFORE the edge. Accept read iff rd && !empty BEFORE the edge.
Thus at full with both requests, only read is accepted; at empty with both, only write is accepted.
At partial occupancy with both, both are accepted, occupancy is unchanged and old head is read.
Accepted read sets dout to the oldest queued byte; otherwise dout holds, except reset.
Ignored requests have no side effects. Pointers wrap at depth 3, preserving FIFO order.
Do not change ports, depth, testbench or specification.
""",
        "seed": """module fifo3(input wire clk, input wire rst, input wire wr,
 input wire rd, input wire [7:0] din, output reg [7:0] dout,
 output wire full, output wire empty);
 reg [7:0] mem [0:2];
 reg [1:0] head, tail;
 reg [2:0] count;
 wire push = wr && !full;
 wire pop = rd && !empty;
 assign full = (count == 3'd3);
 assign empty = (count == 3'd0);
 always @(posedge clk) begin
   if (rst) begin head <= 2'd0; tail <= 2'd0; count <= 3'd0; dout <= 8'd0; end
   else begin
     if (pop) begin dout <= mem[head]; head <= head + 2'd1; count <= count - 3'd1; end
     if (push) begin mem[tail] <= din; tail <= tail + 2'd1; count <= count + 3'd1; end
   end
 end
endmodule
""",
    },
    "regfile": {
        "top": "regfile",
        "groups": ["reset", "zero", "write", "hold", "dual_read", "random"],
        "spec": """# Register file
Verilog-2001 module regfile(input wire clk, input wire rst, input wire we,
input wire [2:0] wa, input wire [15:0] wd, input wire [2:0] ra,
input wire [2:0] rb, output wire [15:0] qa, output wire [15:0] qb).
Eight 16-bit registers, two asynchronous read ports and one synchronous write port.
Synchronous active-high reset sets every register to zero and has priority over writes.
Address zero is read-only zero: writes there are ignored and both read ports always return zero there.
On a rising edge with !rst && we && wa!=0, register wa takes wd. Other registers are unchanged.
Without an accepted write, all registers hold. Reads show current stored state after an edge.
Do not change ports, tests or specification.
""",
        "seed": """module regfile(input wire clk, input wire rst, input wire we,
 input wire [2:0] wa, input wire [15:0] wd, input wire [2:0] ra,
 input wire [2:0] rb, output wire [15:0] qa, output wire [15:0] qb);
 reg [15:0] regs [0:7];
 integer i;
 assign qa = regs[ra];
 assign qb = regs[rb];
 always @(posedge clk) begin
   if (rst) begin
     for (i = 0; i < 8; i = i + 1) regs[i] <= 16'd0;
   end
   if (we) regs[wa] <= wd;
 end
endmodule
""",
    },
}


def vectors(task: str, seed: int) -> list[tuple[str, tuple[int, ...]]]:
    """Directed contracts plus deterministic random requests, grouped independently."""
    rng = random.Random(seed)
    rows = []
    def add(group, *values):
        rows.append((group, values))
    if task == "fifo3":
        def reset(group):
            add(group, 1, 0, 0, 0)
        reset("reset")
        add("reset", 1, 1, 1, 0xA5)
        reset("empty")
        for _ in range(5):
            add("empty", 0, 0, 1, rng.randrange(256))
        reset("capacity")
        for _ in range(6):
            add("capacity", 0, 1, 0, rng.randrange(256))
        for _ in range(6):
            add("capacity", 0, 0, 1, 0)
        reset("simultaneous")
        add("simultaneous", 0, 1, 1, 0x12)  # empty: push only
        for _ in range(8):
            add("simultaneous", 0, 1, 1, rng.randrange(256))  # partial: both
        reset("simultaneous")
        for _ in range(3):
            add("simultaneous", 0, 1, 0, rng.randrange(256))
        add("simultaneous", 0, 1, 1, 0x89)  # full: pop only
        reset("wrap")
        for _ in range(10):
            for _ in range(3):
                add("wrap", 0, 1, 0, rng.randrange(256))
            for _ in range(3):
                add("wrap", 0, 0, 1, 0)
        reset("random")
        for _ in range(400):
            add("random", int(rng.randrange(40) == 0), rng.randrange(2), rng.randrange(2), rng.randrange(256))
    elif task == "regfile":
        def reset(group):
            add(group, 1, 0, 0, 0, 0, 1)
        reset("reset")
        add("reset", 1, 1, 3, 0xCAFE, 3, 0)
        reset("zero")
        add("zero", 0, 1, 0, 0xBEEF, 0, 0)
        reset("write")
        for address in range(1, 8):
            add("write", 0, 1, address, rng.randrange(65536), address, 0)
        reset("hold")
        add("hold", 0, 1, 4, 0x1234, 4, 0)
        for _ in range(8):
            add("hold", 0, 0, 4, rng.randrange(65536), 4, 0)
        reset("dual_read")
        for address in range(1, 8):
            add("dual_read", 0, 1, address, 0x1100 + address, address, 0)
        for a in range(8):
            for b in range(8):
                add("dual_read", 0, 0, 0, 0, a, b)
        reset("random")
        for _ in range(400):
            add("random", int(rng.randrange(40) == 0), rng.randrange(2), rng.randrange(8),
                rng.randrange(65536), rng.randrange(8), rng.randrange(8))
    else:
        raise ValueError("Unknown curriculum task")
    return rows


def expected(task: str, rows: list) -> list[tuple[int, ...]]:
    result = []
    queue = deque()
    last = 0
    regs = [0] * 8
    for _group, inputs in rows:
        if task == "fifo3":
            rst, wr, rd, din = inputs
            if rst:
                queue.clear()
                last = 0
            else:
                push, pop = wr and len(queue) < 3, rd and bool(queue)
                if pop:
                    last = queue.popleft()
                if push:
                    queue.append(din)
            result.append((last, int(len(queue) == 3), int(not queue)))
        else:
            rst, we, wa, wd, ra, rb = inputs
            if rst:
                regs = [0] * 8
            elif we and wa:
                regs[wa] = wd
            result.append((regs[ra], regs[rb]))
    return result


def bench(task: str, rows: list) -> str:
    if task == "fifo3":
        ports = "reg clk,rst,wr,rd; reg [7:0] din; wire [7:0] dout; wire full,empty;"
        dut = "fifo3 dut(.clk(clk),.rst(rst),.wr(wr),.rd(rd),.din(din),.dout(dout),.full(full),.empty(empty));"
        fields = ["rst", "wr", "rd", "din"]
        values = "dout,full,empty"
        fmt = "%h %h %h"
    else:
        ports = "reg clk,rst,we; reg [2:0] wa,ra,rb; reg [15:0] wd; wire [15:0] qa,qb;"
        dut = "regfile dut(.clk(clk),.rst(rst),.we(we),.wa(wa),.wd(wd),.ra(ra),.rb(rb),.qa(qa),.qb(qb));"
        fields = ["rst", "we", "wa", "wd", "ra", "rb"]
        values = "qa,qb"
        fmt = "%h %h"
    statements = []
    for i, (_group, inputs) in enumerate(rows):
        setters = " ".join(f"{name}={value};" for name, value in zip(fields, inputs))
        statements.append(f'clk=0; {setters} #2; clk=1; #1; $display("ROW {i} {fmt}",{values}); #1;')
    return "module tb;\n" + ports + "\n" + dut + "\ninitial begin\n" + "\n".join(statements) + "\n$finish; end endmodule\n"


def trace(output: str, count: int, width: int) -> list[tuple]:
    """Do not accept truncated, duplicated or fabricated row numbering."""
    lines = [line.split() for line in output.splitlines() if line.startswith("ROW ")]
    if (len(lines) != count or any(len(line) != width + 2 or line[1] != str(i)
                                    or any(not re.fullmatch("[0-9a-fA-FxXzZ]+", v) for v in line[2:])
                                    for i, line in enumerate(lines))):
        raise ValueError("Incomplete, duplicate or malformed simulator trace")
    return [tuple(None if re.search("[xXzZ]", v) else int(v, 16) for v in line[2:]) for line in lines]


def evaluate(task: str, rows: list, output: str) -> dict:
    """Every row must appear once in order. Unknown signals always fail."""
    observed_rows = trace(output, len(rows), 3 if task == "fifo3" else 2)
    report = {name: {"ok": True, "problems": [], "vectors": 0, "about": name}
              for name in TASKS[task]["groups"]}
    for i, ((group, inputs), want, observed) in enumerate(zip(rows, expected(task, rows), observed_rows)):
        row = report[group]
        row["vectors"] += 1
        if observed != want:
            row["ok"] = False
            if not row["problems"]:
                detail = f"cycle {i}, inputs={inputs}, expected={want}, actual={observed}"
                row["first_divergence"] = detail
                row["problems"].append(detail)
    return report


def timing_samples(task: str) -> list[tuple[str, str, tuple[int, ...]]]:
    """Independent checks between clock edges, deliberately absent from repair input."""
    samples = []
    def add(group, stimulus, want):
        samples.append((group, stimulus, want))
    if task == "fifo3":
        add("initialize", "clk=0; rst=1; wr=0; rd=0; din=0; #2; clk=1; #2; clk=0; #2;", (0, 0, 1))
        add("write_waits_for_edge", "rst=0; wr=1; din=8'hA5; #2;", (0, 0, 1))
        add("accepted_write", "clk=1; #2; clk=0; #2;", (0, 0, 0))
        add("read_waits_for_edge", "wr=0; rd=1; #2;", (0, 0, 0))
        add("accepted_read", "clk=1; #2; clk=0; #2;", (0xA5, 0, 1))
        add("dout_holds", "rd=0; wr=1; din=8'h3C; #2; clk=1; #2; clk=0; #2;", (0xA5, 0, 0))
        add("synchronous_reset", "rst=1; wr=1; rd=1; din=8'hEF; #2;", (0xA5, 0, 0))
        add("reset_priority", "clk=1; #2; clk=0; #2;", (0, 0, 1))
    elif task == "regfile":
        add("initialize", "clk=0; rst=1; we=0; wa=0; wd=0; ra=0; rb=1; #2; clk=1; #2; clk=0; #2;", (0, 0))
        for address in range(1, 8):
            add("write_setup", f"rst=0; we=1; wa={address}; wd={0x1100 + address}; ra={address}; rb=0; #2; clk=1; #2; clk=0; #2;", (0x1100 + address, 0))
        for a in range(8):
            for b in range(8):
                add("asynchronous_read", f"we=0; ra={a}; rb={b}; #2;",
                    (0x1100 + a if a else 0, 0x1100 + b if b else 0))
        add("write_waits_for_edge", "we=1; wa=3; wd=16'hCAFE; ra=3; rb=7; #2;", (0x1103, 0x1107))
        add("synchronous_reset", "rst=1; #2;", (0x1103, 0x1107))
        add("reset_priority", "clk=1; #2; clk=0; #2;", (0, 0))
    else:
        raise ValueError("Unknown curriculum task")
    return samples


def timing_bench(task: str, samples: list) -> str:
    # Reuse only port declarations, not the clocked state oracle.
    header = bench(task, []).split("initial begin", 1)[0]
    fields = "dout,full,empty" if task == "fifo3" else "qa,qb"
    fmt = "%h %h %h" if task == "fifo3" else "%h %h"
    statements = [f'{stimulus} $display("ROW {i} {fmt}",{fields});'
                  for i, (_group, stimulus, _want) in enumerate(samples)]
    return header + "initial begin\n" + "\n".join(statements) + "\n$finish; end endmodule\n"


def evaluate_timing(task: str, samples: list, output: str) -> dict:
    values = trace(output, len(samples), 3 if task == "fifo3" else 2)
    report = {}
    for i, ((group, _stimulus, want), actual) in enumerate(zip(samples, values)):
        row = report.setdefault(group, {"ok": True, "problems": [], "vectors": 0, "about": group})
        row["vectors"] += 1
        if actual != want:
            row["ok"] = False
            if not row["problems"]:
                row["first_divergence"] = f"sample {i}, expected={want}, actual={actual}"
                row["problems"].append(row["first_divergence"])
    return report


def check(task: str, dut: Path, report_path: Path, seed: int, work: Path,
          timing: bool = False, include_timing: bool = False) -> dict:
    if timing and include_timing:
        raise ValueError("Use timing-only or combined checks, not both")
    rows = timing_samples(task) if timing else vectors(task, seed)
    work.mkdir(parents=True, exist_ok=True)
    (work / "tb.v").write_text(timing_bench(task, rows) if timing else bench(task, rows), encoding="utf-8")
    report_path.unlink(missing_ok=True)
    image = work / "sim.vvp"
    image.unlink(missing_ok=True)
    env = dict(os.environ)
    tools = Path(env.get("OSS_CAD_SUITE", "C:/oss-cad-suite"))
    env["PATH"] = os.pathsep.join([str(tools / "bin"), str(tools / "lib"), env.get("PATH", "")])
    def run(command):
        command[0] = shutil.which(command[0], path=env["PATH"]) or command[0]
        done = subprocess.run(command, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30)
        if done.returncode:
            raise RuntimeError((done.stdout + done.stderr)[-2500:])
        return done.stdout
    run(["iverilog", "-g2005", "-s", "tb", "-o", str(image), str(dut.resolve()), str(work / "tb.v")])
    output = run(["vvp", str(image)])
    data = evaluate_timing(task, rows, output) if timing else evaluate(task, rows, output)
    if include_timing:
        phase_report = report_path.with_name(report_path.stem + '.timing' + report_path.suffix)
        phases = check(task, dut, phase_report, seed, work / 'timing', timing=True)
        data.update({'timing.' + name: row for name, row in phases.items()})
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def prepare(root: Path) -> list[Path]:
    destinations = [root / (task + "-growth") for task in TASKS]
    if any(path.exists() for path in destinations):
        raise ValueError("Refusing to overwrite existing curriculum folders")
    script = Path(__file__).read_text(encoding="utf-8")
    for (name, task), folder in zip(TASKS.items(), destinations):
        (folder / "verify").mkdir(parents=True)
        (folder / "spec.md").write_text(task["spec"], encoding="utf-8")
        (folder / (task["top"] + ".v")).write_text(task["seed"], encoding="utf-8")
        (folder / "verify/run_tests.py").write_text(script, encoding="utf-8")
        phase_names = list(dict.fromkeys('timing.' + group for group, _, _ in timing_samples(name)))
        (folder / "rtl.json").write_text(json.dumps({
            "top": task["top"], "file": task["top"] + ".v", "spec": "spec.md",
            "test": ["{python}", "verify/run_tests.py", "check", "--task", name, "--file", "{file}",
                     "--report", "{report}", "--execute", "--include-timing"],
            "expected_tests": task["groups"] + phase_names, "max_rounds": 2,
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
    runner.add_argument("--seed", type=int, default=20261010)
    runner.add_argument("--execute", action="store_true")
    timing_options = runner.add_mutually_exclusive_group()
    timing_options.add_argument("--timing", action="store_true", help="Check clock-between-edge contracts separately")
    timing_options.add_argument("--include-timing", action="store_true", help="Require clocked and between-edge contracts")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            print(json.dumps([str(path) for path in prepare(args.out.resolve())]))
            return 0
        if not args.execute:
            raise ValueError("Simulation requires explicit --execute")
        data = check(args.task, args.file, args.report.resolve(), args.seed,
                     args.file.resolve().parent / "verify/work", timing=args.timing, include_timing=args.include_timing)
        print(json.dumps({"passed": sum(row["ok"] for row in data.values()), "total": len(data),
                          "samples" if args.timing or args.include_timing else "cycles": sum(row["vectors"] for row in data.values()),
                          "seed": None if args.timing else args.seed}))
        return int(not all(row["ok"] for row in data.values()))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
