"""모델이 쓴 Verilog 를 **시뮬레이터로 채점한다** (ADR-048).

ADR-048 의 결론: 일반 코딩 능력은 올릴 여지를 보이지 못했고, 학습이 값어치 있는 곳은
**사용자의 도메인**이다. 반도체 쪽 RTL 이 그 도메인이고, RTL 은 채점에 특히 유리하다 —
**판정을 사람이 하지 않는다.** 참조 모델과 대조하는 테스트벤치가 정답을 알고 있다.

`grade_python.py` 와 같은 구조다. 다른 점은 채점 방법뿐이다:

    모델이 씀  ->  iverilog 로 컴파일  ->  vvp 로 수만 벡터 대조  ->  PASS/FAIL
                -> verilator --lint-only -Wall              ->  경고 0 인지

SECURITY: 모델이 쓴 HDL 을 **시뮬레이터에서만** 돌린다. 파이썬 채점기처럼 임의 코드를
실행하지 않으므로 위험이 낮지만, 같은 규칙으로 `--execute` 를 요구한다.

  python scripts/grade_rtl.py --dry-run
  python scripts/grade_rtl.py --execute --model gpt-oss:20b
  python scripts/grade_rtl.py --execute --only ripple_adder_8 --show
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass

from jarvis.app import NUM_CTX_BY_PROFILE, apply_profile_override, effective_local_models
from jarvis.config import load_settings
from jarvis.hardware import detect_hardware

# 이 저장소는 표준 라이브러리만 쓰지만 시뮬레이터는 외부 실행 파일이다 — import 가 아니라
# subprocess 이므로 dependency_audit 의 경계를 넘지 않는다.
WINDOWS_IVERILOG = Path(r"C:\iverilog\bin")


def find_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    candidate = WINDOWS_IVERILOG / f"{name}.exe"
    return str(candidate) if candidate.exists() else None


TASKS: list[dict] = [
    {
        "name": "ripple_adder_8",
        "prompt": (
            "Verilog-2001 로 8비트 가산기를 작성하십시오.\n"
            "module ripple_adder_8(input [7:0] a, input [7:0] b, input cin,\n"
            "                      output [7:0] sum, output cout);\n"
            "포트 이름과 폭을 정확히 지키십시오."
        ),
        "tb": '''
`timescale 1ns/1ps
module tb;
  reg [7:0] a, b; reg cin; wire [7:0] sum; wire cout;
  integer i, errors; reg [8:0] expected;
  ripple_adder_8 dut(.a(a), .b(b), .cin(cin), .sum(sum), .cout(cout));
  initial begin
    errors = 0;
    for (i = 0; i < 100000; i = i + 1) begin
      a = $random; b = $random; cin = $random; #1;
      expected = a + b + cin;
      if ({cout, sum} !== expected) begin
        errors = errors + 1;
        if (errors <= 5) $display("FAIL a=%h b=%h cin=%b -> %h exp=%h", a, b, cin, {cout,sum}, expected);
      end
    end
    a = 8'hFF; b = 8'hFF; cin = 1'b1; #1;
    if ({cout, sum} !== 9'h1FF) begin errors = errors + 1; $display("FAIL max carry"); end
    if (errors == 0) $display("@@PASS@@"); else $display("@@FAIL@@ %0d", errors);
    $finish;
  end
endmodule
''',
    },
    {
        "name": "priority_encoder_8",
        "prompt": (
            "Verilog-2001 로 8입력 우선순위 인코더를 작성하십시오.\n"
            "module priority_encoder_8(input [7:0] req, output reg [2:0] idx, output reg valid);\n"
            "**가장 높은 비트(MSB)가 우선**입니다. req[7] 이 1이면 idx=3'd7 입니다.\n"
            "req 가 전부 0 이면 valid=0 이고 idx 는 3'd0 입니다. 그 외에는 valid=1 입니다."
        ),
        "tb": '''
`timescale 1ns/1ps
module tb;
  reg [7:0] req; wire [2:0] idx; wire valid;
  integer i, j, errors; reg [2:0] expect_idx; reg expect_valid;
  priority_encoder_8 dut(.req(req), .idx(idx), .valid(valid));
  initial begin
    errors = 0;
    for (i = 0; i < 256; i = i + 1) begin
      req = i[7:0]; #1;
      expect_valid = (req != 8'd0);
      expect_idx = 3'd0;
      for (j = 0; j < 8; j = j + 1)
        if (req[j]) expect_idx = j[2:0];     // 큰 인덱스가 나중에 덮어써 MSB 우선이 된다
      if (!expect_valid) expect_idx = 3'd0;
      if (valid !== expect_valid || idx !== expect_idx) begin
        errors = errors + 1;
        if (errors <= 5) $display("FAIL req=%b -> idx=%d valid=%b exp idx=%d valid=%b",
                                  req, idx, valid, expect_idx, expect_valid);
      end
    end
    if (errors == 0) $display("@@PASS@@"); else $display("@@FAIL@@ %0d", errors);
    $finish;
  end
endmodule
''',
    },
    {
        "name": "sync_counter",
        "prompt": (
            "Verilog-2001 로 동기 카운터를 작성하십시오.\n"
            "module sync_counter(input clk, input rst_n, input en, output reg [3:0] q);\n"
            "`rst_n` 은 **동기 active-low 리셋**입니다 — clk 상승엣지에서만 반영됩니다.\n"
            "`en` 이 1일 때만 1씩 증가하고 15 다음은 0 입니다. 리셋이 en 보다 우선합니다."
        ),
        "tb": '''
`timescale 1ns/1ps
module tb;
  reg clk, rst_n, en; wire [3:0] q;
  integer errors; reg [3:0] model;
  sync_counter dut(.clk(clk), .rst_n(rst_n), .en(en), .q(q));
  always #5 clk = ~clk;
  task step; begin @(posedge clk); #1;
    if (!rst_n) model = 4'd0; else if (en) model = model + 4'd1;
    if (q !== model) begin errors = errors + 1;
      if (errors <= 5) $display("FAIL t=%0t rst_n=%b en=%b q=%d exp=%d", $time, rst_n, en, q, model); end
  end endtask
  integer i;
  initial begin
    errors = 0; clk = 0; rst_n = 0; en = 0; model = 4'd0;
    step; step;
    rst_n = 1; en = 1;
    for (i = 0; i < 40; i = i + 1) step;          // 랩어라운드 두 번 이상
    en = 0; for (i = 0; i < 5; i = i + 1) step;   // enable 이 0 이면 멈춘다
    en = 1; for (i = 0; i < 3; i = i + 1) step;
    rst_n = 0; step;                              // 동기 리셋이 en 보다 우선
    rst_n = 1; for (i = 0; i < 5; i = i + 1) step;
    if (errors == 0) $display("@@PASS@@"); else $display("@@FAIL@@ %0d", errors);
    $finish;
  end
endmodule
''',
    },
    {
        "name": "gray_roundtrip",
        "prompt": (
            "Verilog-2001 로 그레이 코드 변환기 두 개를 **한 파일에** 작성하십시오.\n"
            "module bin2gray(input [7:0] bin, output [7:0] gray);\n"
            "module gray2bin(input [7:0] gray, output [7:0] bin);\n"
            "두 모듈 모두 조합논리입니다."
        ),
        "tb": '''
`timescale 1ns/1ps
module tb;
  reg [7:0] b; wire [7:0] g, back; wire [7:0] gprev;
  integer i, errors; reg [7:0] prev_g; reg first;
  bin2gray e(.bin(b), .gray(g));
  gray2bin d(.gray(g), .bin(back));
  function integer ones; input [7:0] v; integer k; begin
    ones = 0; for (k = 0; k < 8; k = k + 1) ones = ones + v[k]; end
  endfunction
  initial begin
    errors = 0; first = 1;
    for (i = 0; i < 256; i = i + 1) begin
      b = i[7:0]; #1;
      if (back !== b) begin errors = errors + 1;
        if (errors <= 5) $display("FAIL roundtrip b=%h -> g=%h -> %h", b, g, back); end
      if (!first && ones(g ^ prev_g) != 1) begin errors = errors + 1;
        if (errors <= 5) $display("FAIL adjacent b=%0d g=%h prev=%h", i, g, prev_g); end
      prev_g = g; first = 0;
    end
    if (errors == 0) $display("@@PASS@@"); else $display("@@FAIL@@ %0d", errors);
    $finish;
  end
endmodule
''',
    },
    {
        "name": "edge_detector",
        "prompt": (
            "Verilog-2001 로 상승엣지 검출기를 작성하십시오.\n"
            "module edge_detector(input clk, input rst_n, input sig, output reg pulse);\n"
            "`sig` 가 0 에서 1 로 바뀐 **다음 clk 상승엣지에 pulse 를 정확히 한 클럭** 올립니다.\n"
            "`rst_n` 은 **비동기 active-low 리셋**입니다. sig 가 계속 1 이어도 pulse 는 한 번뿐입니다."
        ),
        "tb": '''
`timescale 1ns/1ps
module tb;
  reg clk, rst_n, sig; wire pulse;
  integer errors; reg prev; reg expect_pulse;
  edge_detector dut(.clk(clk), .rst_n(rst_n), .sig(sig), .pulse(pulse));
  always #5 clk = ~clk;
  task step; begin
    @(posedge clk);
    expect_pulse = (sig === 1'b1) && (prev === 1'b0);
    prev = sig;
    #1;
    if (rst_n && pulse !== expect_pulse) begin errors = errors + 1;
      if (errors <= 6) $display("FAIL t=%0t sig=%b pulse=%b exp=%b", $time, sig, pulse, expect_pulse); end
  end endtask
  initial begin
    errors = 0; clk = 0; rst_n = 0; sig = 0; prev = 0;
    #12 rst_n = 1;
    sig = 0; step; step;
    sig = 1; step;          // 여기서 한 번
    step; step;             // 계속 1 이어도 더는 없다
    sig = 0; step;
    sig = 1; step;          // 다시 한 번
    sig = 0; step; step;
    if (errors == 0) $display("@@PASS@@"); else $display("@@FAIL@@ %0d", errors);
    $finish;
  end
endmodule
''',
    },
    {
        "name": "sync_fifo",
        "prompt": (
            "Verilog-2001 로 동기 FIFO 를 작성하십시오.\n"
            "module sync_fifo(input clk, input rst_n, input wr, input [7:0] din,\n"
            "                 input rd, output reg [7:0] dout, output full, output empty);\n"
            "깊이는 **4** 입니다. `rst_n` 은 동기 active-low 리셋입니다.\n"
            "가득 찼을 때 `wr` 은 무시하고, 비었을 때 `rd` 는 무시합니다.\n"
            "`rd` 가 1이면 그 clk 상승엣지에 `dout` 에 가장 먼저 넣은 값이 나옵니다."
        ),
        "tb": '''
`timescale 1ns/1ps
module tb;
  reg clk, rst_n, wr, rd; reg [7:0] din; wire [7:0] dout; wire full, empty;
  integer errors; integer i;
  reg [7:0] model [0:3]; integer head, tail, count;
  sync_fifo dut(.clk(clk), .rst_n(rst_n), .wr(wr), .din(din), .rd(rd),
                .dout(dout), .full(full), .empty(empty));
  always #5 clk = ~clk;
  task tick; reg [7:0] want; reg do_rd; begin
    do_rd = rd && (count > 0);
    want = model[head];
    @(posedge clk);
    if (!rst_n) begin head = 0; tail = 0; count = 0; end
    else begin
      if (do_rd) begin head = (head + 1) % 4; count = count - 1; end
      if (wr && count < 4) begin model[tail] = din; tail = (tail + 1) % 4; count = count + 1; end
    end
    #1;
    if (rst_n) begin
      if (full !== (count == 4)) begin errors = errors + 1;
        if (errors <= 6) $display("FAIL full=%b count=%0d t=%0t", full, count, $time); end
      if (empty !== (count == 0)) begin errors = errors + 1;
        if (errors <= 6) $display("FAIL empty=%b count=%0d t=%0t", empty, count, $time); end
      if (do_rd && dout !== want) begin errors = errors + 1;
        if (errors <= 6) $display("FAIL dout=%h want=%h t=%0t", dout, want, $time); end
    end
  end endtask
  initial begin
    errors = 0; clk = 0; rst_n = 0; wr = 0; rd = 0; din = 0;
    head = 0; tail = 0; count = 0;
    tick; tick; rst_n = 1; tick;
    for (i = 0; i < 6; i = i + 1) begin din = i[7:0] + 8'h10; wr = 1; tick; end  // 넘치게 쓴다
    wr = 0;
    for (i = 0; i < 6; i = i + 1) begin rd = 1; tick; end                        // 비도록 읽는다
    rd = 0;
    din = 8'hAA; wr = 1; rd = 1; tick;                                           // 동시 접근
    wr = 0; rd = 1; tick; rd = 0; tick;
    if (errors == 0) $display("@@PASS@@"); else $display("@@FAIL@@ %0d", errors);
    $finish;
  end
endmodule
''',
    },
]

_FENCE = re.compile(r"```(?:systemverilog|verilog|sv|v)?[^\S\n]*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_FENCE_OPEN = re.compile(r"```(?:systemverilog|verilog|sv|v)?[^\S\n]*\n", re.IGNORECASE)
_MODULE = re.compile(r"\bmodule\s+\w+", re.IGNORECASE)


def extract_hdl(text: str) -> tuple[str, str]:
    """`grade_codegen.extract_code` 와 같은 규칙. 잘린 응답의 여는 펜스를 남기지 않는다."""
    closed = _FENCE.findall(text)
    if closed:
        for block in closed:
            if _MODULE.search(block):
                return block, "fenced"
        return closed[0], "fenced"
    opens = list(_FENCE_OPEN.finditer(text))
    if opens:
        return text[opens[-1].end():], "unclosed-fence"
    return (text, "bare") if _MODULE.search(text) else (text, "no-code")


def simulate(hdl: str, task: dict, iverilog: str, vvp: str, timeout: int) -> tuple[bool, str]:
    if not _MODULE.search(hdl):
        return False, "no module in output"
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "dut.v").write_text(hdl, encoding="utf-8")
        (d / "tb.v").write_text(task["tb"], encoding="utf-8")
        try:
            build = subprocess.run([iverilog, "-g2005", "-o", str(d / "sim"),
                                    str(d / "dut.v"), str(d / "tb.v")],
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=timeout, cwd=tmp)
        except subprocess.TimeoutExpired:
            return False, "compile timeout"
        if build.returncode != 0:
            first = (build.stderr.strip().splitlines() or ["compile failed"])[0]
            return False, f"compile: {first[:110]}"
        try:
            run = subprocess.run([vvp, str(d / "sim")], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=timeout, cwd=tmp)
        except subprocess.TimeoutExpired:
            return False, "simulation timeout"
    if "@@PASS@@" in run.stdout:
        return True, ""
    if "@@FAIL@@" in run.stdout:
        detail = next((l for l in run.stdout.splitlines() if l.startswith("FAIL")), "")
        return False, (detail or run.stdout.split("@@FAIL@@")[1].strip())[:110]
    return False, (run.stdout.strip().splitlines() or ["no verdict"])[-1][:110]


def lint(hdl: str, verilator: str | None, timeout: int) -> str:
    """verilator 경고. 없으면 빈 문자열. verilator 가 없으면 건너뛴다."""
    if not verilator:
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        name = _MODULE.search(hdl).group(0).split()[-1]
        (d / f"{name}.v").write_text(hdl, encoding="utf-8")
        try:
            proc = subprocess.run([verilator, "--lint-only", "-Wall", str(d / f"{name}.v")],
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout, cwd=tmp)
        except (subprocess.TimeoutExpired, OSError):
            return ""
    warnings = [l for l in proc.stderr.splitlines() if l.startswith("%Warning")]
    return f"{len(warnings)} warning(s)" if warnings else ""


def generate(host, model, num_ctx, num_predict, think, prompt, timeout):
    payload = {"model": model, "stream": False, "think": think, "keep_alive": "10m",
               "messages": [{"role": "user", "content": prompt + "\n코드 블록 하나만 출력하고 설명은 붙이지 마십시오."}],
               "options": {"num_ctx": num_ctx, "num_predict": num_predict}}
    request = urllib.request.Request(f"{host.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        item = json.loads(response.read().decode("utf-8"))
    message = item.get("message") or {}
    return str(message.get("content") or ""), {
        "seconds": round(time.perf_counter() - started, 1),
        "done_reason": str(item.get("done_reason") or ""),
        "eval_count": item.get("eval_count"),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("-n", "--runs", type=int, default=1)
    p.add_argument("--only", default="", help="이름 하나만 돌린다")
    p.add_argument("--model", default="")
    p.add_argument("--num-ctx", type=int, default=0)
    p.add_argument("--num-predict", type=int, default=0)
    p.add_argument("--think", action="store_true")
    p.add_argument("--timeout", type=int, default=0)
    p.add_argument("--sim-timeout", type=int, default=120)
    p.add_argument("--show", action="store_true", help="생성된 HDL 을 출력한다")
    p.add_argument("--host", default="")
    p.add_argument("--out", default="")
    args = p.parse_args(argv)

    iverilog, vvp = find_tool("iverilog"), find_tool("vvp")
    verilator = find_tool("verilator")
    tasks = [t for t in TASKS if not args.only or t["name"] == args.only]
    if not tasks:
        print(f"그런 과제가 없습니다: {args.only}")
        return 2

    settings = load_settings()
    hardware = detect_hardware()
    apply_profile_override(hardware, settings.force_profile)
    _g, coder = effective_local_models(settings, hardware.profile)
    model = args.model or coder
    num_ctx = args.num_ctx or settings.num_ctx or NUM_CTX_BY_PROFILE[hardware.profile]
    num_predict = args.num_predict or settings.local_max_output_tokens
    host = args.host or settings.ollama_host
    timeout = args.timeout or settings.request_timeout

    print(f"model={model} num_ctx={num_ctx} num_predict={num_predict} "
          f"think={'on' if args.think else 'off'}")
    print(f"{len(tasks)} tasks: " + ", ".join(t["name"] for t in tasks))
    print(f"iverilog={iverilog or '없음'}  verilator={verilator or '없음(린트 건너뜀)'}")

    if args.dry_run:
        for t in tasks:
            print(f"\n--- {t['name']} ---\n{t['prompt']}")
        return 0
    if not args.execute:
        print("\n--execute 없이는 채점하지 않습니다.")
        return 2
    if not iverilog or not vvp:
        print("\niverilog/vvp 를 찾지 못했습니다. 설치 경로를 확인하십시오.")
        return 2

    runs: list[dict] = []
    for i in range(1, args.runs + 1):
        results = []
        for task in tasks:
            try:
                text, diag = generate(host, model, num_ctx, num_predict, args.think,
                                      task["prompt"], timeout)
            except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
                print(f"  run {i} {task['name']:<20} 생성 실패: {exc}")
                results.append({"task": task["name"], "ok": False, "why": str(exc)[:80]})
                continue
            hdl, how = extract_hdl(text)
            ok, why = simulate(hdl, task, iverilog, vvp, args.sim_timeout)
            warn = lint(hdl, verilator, args.sim_timeout) if ok else ""
            diag["extraction"] = how
            diag["lint"] = warn
            results.append({"task": task["name"], "ok": ok, "why": why,
                            "diag": diag, "hdl": hdl[:4000]})
            mark = "PASS" if ok else "FAIL"
            tail = "" if ok else f"  <- {why}"
            if ok and warn:
                tail = f"  (lint: {warn})"
            print(f"  run {i} {task['name']:<20} {mark} ({diag['seconds']:>6.1f}s){tail}")
        score = sum(1 for r in results if r["ok"])
        runs.append({"run": i, "score": score, "results": results})
        print(f"  run {i} 합계: {score}/{len(tasks)}\n")
        if args.show:
            for r in results:
                print(f"--- {r['task']} ---\n{r.get('hdl', '')[:1500]}\n")

    total = sum(r["score"] for r in runs)
    possible = len(tasks) * len(runs)
    print(f"전체 {total}/{possible}  ({100*total/possible:.0f}%)")
    per: dict[str, int] = {}
    for r in runs:
        for item in r["results"]:
            per[item["task"]] = per.get(item["task"], 0) + (1 if item["ok"] else 0)
    print("과제별:")
    for name, hits in per.items():
        print(f"  {name:<22} {hits}/{len(runs)}")

    out = Path(args.out) if args.out else ROOT / "logs" / f"rtl-grade-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": model, "num_ctx": num_ctx, "think": args.think,
                               "total": total, "possible": possible, "runs": runs},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
