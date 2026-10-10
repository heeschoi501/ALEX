"""ALEX LoRA 학습 파이프라인 — 승인된 경험 → 작은 모델 LoRA → 평가 → Ollama 모델 (2026-10-05, ADR-084).

왜 작은 모델인가: 본 모델 Qwen3.8-27B 는 4비트 QLoRA 도 24GB 이상 필요하다(Unsloth Qwen3.8 안내). 이 PC 는 16GB 다.
4B 는 LoRA 학습에 약 10GB(Unsloth 요구 사양 표) — VRAM 여유를 남기고 돈다. 같은 파이프라인이 나중에 더 큰 장비에서 본 모델에도 쓰인다.

흐름(상태는 data/lora/<실행 ID>/run.json):
  prepare  승인된 '질문 → 좋은 답' 쌍(/good)을 train/eval 로 내보낸다(`experiences.py export` 와 같은 분할 — eval 은 학습에
           절대 섞이지 않는다). 채팅 교정·개발 교훈은 답이 아니라서 뺀다(sft_ready).
  train    학습 전용 환경(C:\\ALEX\\tools\\lora\\venv)에서 LoRA 학습. GPU 는 --yes 와 사전 점검을 통과해야 시작한다
           (그래픽카드 사용 2GB 이하, Ollama 에 올라간 모델 없음, 이 프로세스 VRAM 상한 11GB). --device cpu 는 GPU 를 아예 안 본다.
  export   어댑터를 바탕 모델에 합쳐 GGUF(q8_0) → Ollama 모델 alex-lora-<실행 ID>. 비교용 바탕 모델도 같은 방식으로 한 번 만든다.
           Ollama 0.34.1 부터 Modelfile ADAPTER 가 없어져(ollama#14969) 합친 뒤 넣는다.
  eval     평가셋과 고정 점검 문장을 바탕 모델·학습 모델로 나란히 생성해 보고서(eval-report.md)를 남긴다.
           --engine hf 는 CPU 만 쓴다. 채택(ALEX 설정 변경)은 사용자가 한다 — 이 스크립트는 설정을 바꾸지 않는다.
  status   학습 환경·데이터·실행 목록

예:
  python scripts/lora.py status
  python scripts/lora.py prepare --run-id m1
  python scripts/lora.py train --run-id m1 --yes
  python scripts/lora.py export --run-id m1
  python scripts/lora.py eval --run-id m1
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Callable
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass

from experiences import to_chat  # noqa: E402
from jarvis.config import load_settings  # noqa: E402
from jarvis.storage import Storage  # noqa: E402

WORKER = ROOT / "scripts" / "lora_worker.py"
DEFAULT_BASE = "Qwen/Qwen3-4B-Instruct-2507"
CHATML = (
    "{{- if .Messages }}\n"
    "{{- if .System }}<|im_start|>system\n{{ .System }}<|im_end|>\n{{ end }}\n"
    "{{- range $i, $_ := .Messages }}\n"
    "{{- $last := eq (len (slice $.Messages $i)) 1 -}}\n"
    "{{- if eq .Role \"user\" }}<|im_start|>user\n{{ .Content }}<|im_end|>\n"
    "{{ else if eq .Role \"assistant\" }}<|im_start|>assistant\n{{ .Content }}{{ if not $last }}<|im_end|>\n{{ end }}\n"
    "{{- end }}\n"
    "{{- if and (ne .Role \"assistant\") $last }}<|im_start|>assistant\n{{ end }}\n"
    "{{- end }}\n"
    "{{- else }}\n"
    "{{- if .System }}<|im_start|>system\n{{ .System }}<|im_end|>\n{{ end }}"
    "{{ if .Prompt }}<|im_start|>user\n{{ .Prompt }}<|im_end|>\n{{ end }}<|im_start|>assistant\n"
    "{{ end }}{{ .Response }}{{ if .Response }}<|im_end|>{{ end }}"
)
# 바탕 모델별 Ollama 채팅 틀·권장 샘플링(Qwen3-4B-Instruct-2507 모델 카드: temperature 0.7, top_p 0.8, top_k 20, min_p 0).
# GGUF 를 직접 넣으면 Ollama 가 틀을 모를 수 있어 적어 둔다. 여기 없는 바탕 모델은 받지 않는다.
BASES = {
    "Qwen/Qwen3-4B-Instruct-2507": {"template": CHATML, "stop": ["<|im_end|>", "<|im_start|>"],
                                    "parameters": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0}},
}
MIN_TRAIN = 50          # 이보다 적으면 효과를 기대할 수 없다 — --allow-small 은 '연결 확인용' 으로만 돌린다
GPU_IDLE_MIB = 2048     # 학습 전에 그래픽카드가 이만큼 이하로만 쓰이고 있어야 한다(모니터·바탕화면 몫)
VRAM_LIMIT_GB = 11.0    # 학습 프로세스 VRAM 상한 — 16GB 를 꽉 채운 때 PC 가 꺼졌다(gpu-load-safety)
RUN_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,39}")
SANITY_PROMPTS = [
    "너는 누구야? 한두 문장으로 소개해줘.",
    "파이썬에서 딕셔너리를 값 기준으로 정렬하는 코드를 짧게 보여줘.",
    "오늘 할 일을 정리하는 좋은 방법 세 가지만 알려줘.",
    "RAM 과 VRAM 의 차이를 쉽게 설명해줘.",
]


# ── 경로·환경 ────────────────────────────────────────────────────────────────

def lora_home() -> Path:
    return Path(os.getenv("JARVIS_LORA_HOME") or (ROOT.parent / "tools" / "lora"))


def venv_python(home: Path) -> Path:
    return home / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def worker_env(home: Path, device: str, *, online: bool = False) -> dict[str, str]:
    """학습 환경 프로세스의 환경. 바탕 모델은 fetch 때만 내려받고 나머지는 오프라인 — 몰래 새 판을 받지 않는다."""
    env = dict(os.environ, HF_HOME=str(home / "hf-cache"), PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               HF_HUB_DISABLE_TELEMETRY="1", TOKENIZERS_PARALLELISM="false")
    if not online:
        env["HF_HUB_OFFLINE"] = "1"
    if device == "cpu":
        env["CUDA_VISIBLE_DEVICES"] = ""  # 그래픽카드를 아예 보지 않는다
    return env


def fetch_base(python: Path, worker: Path, home: Path, base: str) -> Path | None:
    """바탕 모델을 학습 환경 캐시에 받아 두고(이미 있으면 확인만) 로컬 경로를 돌려준다."""
    done = subprocess.run([str(python), str(worker), "fetch", "--base", base], capture_output=True, text=True,
                          env=worker_env(home, "cpu", online=True), timeout=7200, encoding="utf-8", errors="replace")
    try:
        return Path(json.loads(done.stdout.strip().splitlines()[-1])["path"])
    except (ValueError, KeyError, IndexError):
        print(f"바탕 모델을 받지 못했습니다:\n{done.stdout[-800:]}{done.stderr[-800:]}")
        return None


def ollama_command() -> list[str]:
    bundled = ROOT / "runtime" / "ollama" / ("ollama.exe" if os.name == "nt" else "ollama")
    return [str(bundled)] if bundled.is_file() else [shutil.which("ollama") or "ollama"]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", text.lower()).strip("-")


def base_model_name(base: str) -> str:
    return f"alex-lora-base-{slug(base.split('/')[-1])}"


def tuned_model_name(run_id: str) -> str:
    return f"alex-lora-{run_id}"


def _kill_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
    else:
        process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def run_logged(command: list[str], log_path: Path, env: dict[str, str] | None, timeout: float,
               cwd: Path | None = None) -> int:
    """명령을 돌려 출력을 기록 파일에 덧붙인다. 시간을 넘기면 프로세스 트리를 끊고 124."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n$ {subprocess.list2cmdline(command)}\n")
        log.flush()
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, creationflags=flags)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            log.write(f"\n시간 초과({timeout:.0f}초) — 멈췄습니다\n")
            return 124
        except KeyboardInterrupt:
            _kill_tree(process)
            raise


def log_tail(path: Path, lines: int = 12) -> str:
    if not path.is_file():
        return ""
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


# ── 그래픽카드 사전 점검 ──────────────────────────────────────────────────────

def gpu_memory() -> tuple[int, int] | None:
    """(사용 MiB, 전체 MiB). 읽지 못하면 None."""
    exe = shutil.which("nvidia-smi") or (r"C:\Windows\System32\nvidia-smi.exe" if os.name == "nt" else "")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15)
        used, total = (int(part.strip()) for part in out.stdout.strip().splitlines()[0].split(","))
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
    return used, total


def ollama_loaded(host: str) -> list[str] | None:
    """Ollama 에 올라간 모델 이름. Ollama 가 꺼져 있으면 None(올라간 것도 없다)."""
    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return [str(m.get("name", "")) for m in data.get("models", []) if isinstance(m, dict)]


def gpu_problems(host: str, yes: bool, memory: Callable[[], tuple[int, int] | None] = gpu_memory,
                 loaded: Callable[[str], list[str] | None] = ollama_loaded) -> list[str]:
    problems = []
    if not yes:
        problems.append("GPU 를 쓰는 작업은 --yes 가 있어야 시작합니다(사용자 동의 — ALEX GPU 작업 중 PC 가 꺼진 기록 때문).")
    usage = memory()
    if usage is None:
        problems.append("nvidia-smi 로 그래픽카드 메모리를 읽지 못했습니다.")
    elif usage[0] > GPU_IDLE_MIB:
        problems.append(f"그래픽카드를 이미 {usage[0]}MiB 쓰고 있습니다(시작 조건 {GPU_IDLE_MIB}MiB 이하) — "
                        "다른 GPU 작업(대화 모델·그림·다른 에이전트)이 끝난 뒤에 하세요.")
    names = loaded(host) or []
    if names:
        problems.append(f"Ollama 에 모델이 올라가 있습니다({', '.join(names)}) — ALEX 에서 /unload 하거나 내려간 뒤에 하세요.")
    return problems


# ── 실행 상태 ─────────────────────────────────────────────────────────────────

def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_run(path: Path) -> dict:
    file = path / "run.json"
    if not file.is_file():
        raise SystemExit(f"실행을 찾지 못했습니다: {path.name} — 먼저 prepare 하세요.")
    return json.loads(file.read_text(encoding="utf-8"))


def save_run(path: Path, run: dict) -> None:
    path.mkdir(parents=True, exist_ok=True)
    temp = path / "run.json.tmp"
    temp.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path / "run.json")


def mark(run: dict, state: str, note: str = "") -> None:
    run["state"] = state
    run.setdefault("history", []).append({"at": now(), "state": state, "note": note})


def note(run: dict, text: str) -> None:
    """상태는 그대로 두고 기록만 남긴다(내보내기·평가 실패 — 어댑터는 멀쩡하니 다시 할 수 있다)."""
    run.setdefault("history", []).append({"at": now(), "state": run.get("state"), "note": text})


def require(run: dict, *states: str) -> None:
    if run.get("state") not in states:
        raise SystemExit(f"지금 상태({run.get('state')})에서는 할 수 없습니다. 가능한 상태: {', '.join(states)}")


# ── 명령 ──────────────────────────────────────────────────────────────────────

def sft_ready(experience) -> bool:
    """대화 학습에 쓸 수 있는 경험 — '질문 → 사용자가 좋다고 한 답' 쌍만(/good).

    채팅 교정(verdict bad)의 correction 은 고친 답이 아니라 사용자가 한 말('사용자가 바로잡았다: 그게 아니야')이고,
    개발 교훈(identity)도 답이 아니라 원칙이다. 이것들은 교훈 문맥(ADR-055)에는 맞지만 그대로 학습하면 모델이
    그 문장을 따라 말하도록 배운다(2026-10-05 연결 확인에서 확인).
    """
    return experience.kind == "answer" and experience.verdict == "good" and not experience.correction.strip()


def cmd_prepare(store: Storage, runs: Path, args: argparse.Namespace) -> int:
    run_id = (args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")).strip()
    if not RUN_ID.fullmatch(run_id):
        print("실행 ID 는 소문자·숫자·.-_ 로 40자 이내여야 합니다(Ollama 모델 이름에 들어간다).")
        return 2
    if args.base not in BASES:
        print(f"지원하는 바탕 모델: {', '.join(BASES)}")
        return 2
    path = runs / run_id
    if (path / "run.json").exists():
        print(f"이미 있는 실행 ID 입니다: {run_id}")
        return 2
    # 학습은 반드시 training_pool 뷰를 거친다(ADR-037 결정 6). eval 은 승인된 평가용만. 둘 다 '질문 → 좋은 답' 쌍만 쓴다.
    pool = store.training_pool()
    held = store.list_experiences(stage="approved", split="eval", limit=10_000)
    if {e.id for e in pool} & {e.id for e in held}:
        print("FATAL: train 과 eval 에 같은 경험이 있습니다 — 저장소 구조가 깨졌습니다.")
        return 1
    train, evals = [e for e in pool if sft_ready(e)], [e for e in held if sft_ready(e)]
    skipped = len(pool) + len(held) - len(train) - len(evals)
    small = len(train) < args.min_train
    if not train or (small and not args.allow_small):
        print(f"학습할 '질문 → 좋은 답' 쌍이 {len(train)}건입니다(최소 {args.min_train}건). 효과를 기대할 수 없어 시작하지 않습니다.\n"
              f"  승인됐지만 교정·교훈 형식이라 대화 학습에서 뺀 경험 {skipped}건\n"
              "  학습 데이터 쌓기: 좋은 답에 /good → /review 로 승인\n"
              "  연결만 확인하려면 --allow-small(1건 이상 있을 때)")
        return 1
    dataset = path / "dataset"
    dataset.mkdir(parents=True)
    for name, rows in (("train.jsonl", train), ("eval.jsonl", evals)):
        with (dataset / name).open("w", encoding="utf-8", newline="\n") as fh:
            for e in rows:
                fh.write(json.dumps(to_chat(e), ensure_ascii=False) + "\n")
    run = {"run_id": run_id, "created_at": now(), "base_model": args.base,
           "dataset": {"train": len(train), "eval": len(evals), "small": small, "skipped_not_answers": skipped,
                       "train_ids": [e.id for e in train], "eval_ids": [e.id for e in evals]}}
    mark(run, "prepared", "연결 확인용(데이터 부족)" if small else "")
    save_run(path, run)
    print(f"준비 완료: {run_id} — 학습 {len(train)}건 · 평가 {len(evals)}건{' (연결 확인용)' if small else ''}"
          f" · 교정·교훈 형식이라 뺀 경험 {skipped}건\n  {dataset}")
    if not evals:
        print("  평가용 승인 경험이 0건이라 학습 전후 손실을 잴 수 없습니다.")
    return 0


def cmd_train(store: Storage, runs: Path, args: argparse.Namespace, *, python: Path | None = None,
              worker: Path = WORKER, memory=gpu_memory, loaded=ollama_loaded) -> int:
    path = runs / args.run_id
    run = load_run(path)
    require(run, "prepared", "trained", "failed")
    if args.device == "cuda":
        problems = gpu_problems(args.host, args.yes, memory, loaded)
        if problems:
            print("GPU 학습을 시작하지 않았습니다:\n" + "\n".join(f"  - {p}" for p in problems))
            return 1
    home = lora_home()
    python = python or venv_python(home)
    if not Path(python).is_file():
        print(f"학습 환경이 없습니다: {python}\n  설치: docs/ALEX_LORA_PIPELINE_20261005.md")
        return 1
    snapshot = fetch_base(Path(python), worker, home, run["base_model"])
    if snapshot is None:
        return 1
    run["base_snapshot"] = snapshot.name  # 어느 판으로 학습했는지(Hugging Face 커밋)
    out = path / "train"
    shutil.rmtree(out, ignore_errors=True)
    command = [str(python), str(worker), "train", "--base", run["base_model"], "--data", str(path / "dataset"),
               "--out", str(out), "--device", args.device, "--epochs", str(args.epochs), "--lr", str(args.lr),
               "--rank", str(args.rank), "--alpha", str(args.alpha), "--grad-accum", str(args.grad_accum),
               "--max-len", str(args.max_len), "--vram-limit-gb", str(args.vram_limit_gb)]
    if args.max_steps:
        command += ["--max-steps", str(args.max_steps)]
    print(f"학습 시작({args.device}) — 기록: {path / 'train.log'}")
    code = run_logged(command, path / "train.log", worker_env(home, args.device), args.timeout)
    result_file = out / "result.json"
    if code != 0 or not result_file.is_file() or not (out / "adapter").is_dir():
        mark(run, "failed", f"학습 실패(종료 코드 {code}) — train.log")
        save_run(path, run)
        print(f"학습 실패(종료 코드 {code}). 마지막 기록:\n{log_tail(path / 'train.log')}")
        return 1
    result = json.loads(result_file.read_text(encoding="utf-8"))
    run["train"] = result
    summary = (f"{result.get('device')} {result.get('steps')}단계 {result.get('seconds')}초, "
               f"평가 손실 {result.get('eval_loss_before')} → {result.get('eval_loss_after')}")
    mark(run, "trained", summary)
    save_run(path, run)
    if args.record:
        # 어느 경험이 어느 모델 버전에 들어갔는가(ADR-037 결정 7) — 실제 학습이 끝났을 때만 남긴다.
        store.record_training_run(run["run_id"], run["base_model"], run["dataset"]["train_ids"],
                                  notes=f"{Storage.LORA_TRAINED_NOTE}: {summary}, 어댑터 {out / 'adapter'}")
    print(f"학습 완료: {summary}\n  어댑터 {out / 'adapter'}")
    return 0


def modelfile_text(gguf: Path, base: str) -> str:
    spec = BASES[base]
    lines = [f"FROM {gguf}", f'TEMPLATE """{spec["template"]}"""']
    lines += [f'PARAMETER stop "{stop}"' for stop in spec["stop"]]
    lines += [f"PARAMETER {key} {value}" for key, value in spec["parameters"].items()]
    return "\n".join(lines) + "\n"


def _ollama_create(name: str, gguf: Path, base: str, folder: Path, log: Path, host: str, ollama: list[str]) -> int:
    modelfile = folder / "Modelfile"
    modelfile.write_text(modelfile_text(gguf, base), encoding="utf-8")
    env = dict(os.environ, OLLAMA_HOST=host)
    return run_logged(ollama + ["create", name, "-f", str(modelfile)], log, env, 1800, cwd=folder)


def _convert(python: Path, home: Path, model_dir: Path, gguf: Path, outtype: str, log: Path) -> int:
    converter = home / "llama.cpp" / "convert_hf_to_gguf.py"
    return run_logged([str(python), str(converter), str(model_dir), "--outfile", str(gguf), "--outtype", outtype],
                      log, worker_env(home, "cpu"), 3600)


def cmd_export(store: Storage, runs: Path, args: argparse.Namespace, *, python: Path | None = None,
               worker: Path = WORKER, ollama: list[str] | None = None) -> int:
    """GPU 를 쓰지 않는다 — 합치기·변환은 CPU, ollama create 는 파일만 등록한다."""
    path = runs / args.run_id
    run = load_run(path)
    require(run, "trained", "exported", "evaluated")
    home = lora_home()
    python = python or venv_python(home)
    ollama = ollama or ollama_command()
    base, log = run["base_model"], path / "export.log"
    merged, gguf = path / "merged", path / f"model-{args.outtype}.gguf"
    shutil.rmtree(merged, ignore_errors=True)
    steps = [
        ("합치기", lambda: run_logged([str(python), str(worker), "merge", "--base", base, "--adapter",
                                    str(path / "train" / "adapter"), "--out", str(merged)], log, worker_env(home, "cpu"), 3600)),
        ("GGUF 변환", lambda: _convert(python, home, merged, gguf, args.outtype, log)),
        ("Ollama 등록", lambda: _ollama_create(tuned_model_name(run["run_id"]), gguf, base, path, log, args.host, ollama)),
    ]
    for label, step in steps:
        print(f"{label}…")
        code = step()
        if code != 0:
            note(run, f"내보내기 실패: {label}(종료 코드 {code}) — export.log")
            save_run(path, run)
            print(f"{label} 실패(종료 코드 {code}). 마지막 기록:\n{log_tail(log)}")
            return 1
    if not args.keep_merged:
        shutil.rmtree(merged, ignore_errors=True)  # 16비트 합친 모델(4B 는 약 8GB) — GGUF 가 있으면 필요 없다

    # 비교용 바탕 모델 — 같은 변환(같은 양자화)을 거쳐야 차이가 학습 때문인지 알 수 있다. 한 번만 만든다.
    base_dir = home / "base" / slug(base.split("/")[-1])
    marker = base_dir / f"ollama-{args.outtype}.json"
    if not marker.is_file():
        base_dir.mkdir(parents=True, exist_ok=True)
        base_log = base_dir / "export.log"
        print("비교용 바탕 모델 준비…")
        snapshot = fetch_base(Path(python), worker, home, base)
        if snapshot is None:
            return 1
        base_gguf = base_dir / f"model-{args.outtype}.gguf"
        for label, step in (("바탕 GGUF 변환", lambda: _convert(python, home, snapshot, base_gguf, args.outtype, base_log)),
                            ("바탕 Ollama 등록", lambda: _ollama_create(base_model_name(base), base_gguf, base, base_dir,
                                                                    base_log, args.host, ollama))):
            print(f"{label}…")
            code = step()
            if code != 0:
                print(f"{label} 실패(종료 코드 {code}). 마지막 기록:\n{log_tail(base_log)}")
                return 1
        marker.write_text(json.dumps({"model": base_model_name(base), "gguf": str(base_gguf), "at": now()},
                                     ensure_ascii=False), encoding="utf-8")
    run["export"] = {"gguf": str(gguf), "outtype": args.outtype, "ollama_model": tuned_model_name(run["run_id"]),
                     "base_ollama_model": base_model_name(base)}
    mark(run, "exported", tuned_model_name(run["run_id"]))
    save_run(path, run)
    print(f"내보내기 완료: Ollama 모델 {tuned_model_name(run['run_id'])} (비교용 {base_model_name(base)})\n"
          f"  써 보기: ollama run {tuned_model_name(run['run_id'])}  — ALEX 설정은 바꾸지 않았습니다")
    return 0


def ollama_chat(host: str, model: str, prompt: str, options: dict) -> str:
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False,
                       "keep_alive": 0, "options": options}).encode("utf-8")
    request = urllib.request.Request(f"{host}/api/chat", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=900) as response:
        return str(json.loads(response.read().decode("utf-8")).get("message", {}).get("content", "")).strip()


def answer_flags(answer: str) -> list[str]:
    """눈으로 보기 전에 걸러 낼 것 — 빈 답, 채팅 틀 찌꺼기, 한글 질문에 한글이 거의 없는 답."""
    flags = []
    if not answer.strip():
        flags.append("빈 답")
    if "<|im_" in answer or "<|endoftext|>" in answer:
        flags.append("틀 찌꺼기")
    prose = re.sub(r"```.*?(?:```|$)", " ", answer, flags=re.S)
    letters = [c for c in prose if c.isalpha()]
    if letters and sum(1 for c in letters if "가" <= c <= "힣") / len(letters) < 0.2:
        flags.append("한국어 아님")
    return flags


def cmd_eval(store: Storage, runs: Path, args: argparse.Namespace, *, python: Path | None = None,
             worker: Path = WORKER, chat: Callable[[str, str, str, dict], str] = ollama_chat,
             memory=gpu_memory, loaded=ollama_loaded) -> int:
    path = runs / args.run_id
    run = load_run(path)
    require(run, *(("exported", "evaluated") if args.engine == "ollama" else ("trained", "exported", "evaluated")))
    rows = [json.loads(line) for line in (path / "dataset" / "eval.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    references = {r["messages"][0]["content"]: r["messages"][-1]["content"] for r in rows}
    # 정체성 회귀 신호(IDENTITY.md 6절, 키워드 채점 — 판정자가 아니다). 학습 데이터는 이 질문과 같은 주제를 뺐다(lora_data).
    from grade_identity import CASES, score_case  # noqa: PLC0415 — 앱 전체를 불러오므로 평가 때만

    prompts = list(dict.fromkeys(list(references) + SANITY_PROMPTS + [case["prompt"] for case in CASES]))
    answers: dict[str, list[str]] = {}
    if args.engine == "hf":
        home = lora_home()
        python = python or venv_python(home)
        (path / "eval").mkdir(exist_ok=True)
        prompt_file = path / "eval" / "prompts.json"
        prompt_file.write_text(json.dumps(prompts, ensure_ascii=False), encoding="utf-8")
        for label, extra in (("base", []), ("tuned", ["--adapter", str(path / "train" / "adapter")])):
            out = path / "eval" / f"{label}.json"
            code = run_logged([str(python), str(worker), "sample", "--base", run["base_model"], "--prompts", str(prompt_file),
                               "--out", str(out), "--max-new", str(args.max_new)] + extra,
                              path / "eval.log", worker_env(home, "cpu"), args.timeout)
            if code != 0 or not out.is_file():
                print(f"생성 실패({label}, 종료 코드 {code}):\n{log_tail(path / 'eval.log')}")
                return 1
            answers[label] = [item["answer"] for item in json.loads(out.read_text(encoding="utf-8"))]
    else:
        if not args.cpu:
            problems = gpu_problems(args.host, args.yes, memory, loaded)
            if problems:
                print("GPU 평가를 시작하지 않았습니다(--cpu 면 GPU 없이):\n" + "\n".join(f"  - {p}" for p in problems))
                return 1
        options = {"num_predict": args.max_new, "temperature": 0, **({"num_gpu": 0} if args.cpu else {})}
        export = run.get("export", {})
        for label, model in (("base", export.get("base_ollama_model")), ("tuned", export.get("ollama_model"))):
            answers[label] = [chat(args.host, str(model), prompt, options) for prompt in prompts]
    train = run.get("train", {})
    identity = {label: [(case, *score_case(case, answers[label][prompts.index(case["prompt"])])) for case in CASES]
                for label in ("base", "tuned")}
    totals = {label: sum(passed for _, passed, _ in identity[label]) for label in identity}
    lines = [f"# LoRA 평가 — {run['run_id']}", "",
             f"- 바탕 모델: {run['base_model']} · 학습 {run['dataset']['train']}건 · 평가 {run['dataset']['eval']}건"
             f"{' · 연결 확인용(데이터 부족)' if run['dataset'].get('small') else ''}",
             f"- 평가 손실(낮을수록 기준 답에 가깝다): {train.get('eval_loss_before')} → {train.get('eval_loss_after')}",
             f"- 생성: {args.engine}{' (CPU)' if args.engine == 'hf' or args.cpu else ''}, 그리디, 최대 {args.max_new}토큰",
             "- 채택은 사용자가 결정합니다. 이 보고서는 ALEX 설정을 바꾸지 않습니다.", "",
             "## 정체성 채점 (grade_identity, 시스템 지시문 없이 — 키워드 채점이라 회귀 신호이지 증명은 아니다)", "",
             "| 항목 | 바탕 | 학습 |", "|---|---|---|"]
    for (case, base_ok, base_why), (_, tuned_ok, tuned_why) in zip(identity["base"], identity["tuned"]):
        lines.append(f"| {case['trait']} {case['id']} | {'통과' if base_ok else '실패'} ({base_why}) | "
                     f"{'통과' if tuned_ok else '실패'} ({tuned_why}) |")
    lines += [f"| 합계 | {totals['base']}/{len(CASES)} | {totals['tuned']}/{len(CASES)} |", ""]
    flagged = {"base": 0, "tuned": 0}
    for index, prompt in enumerate(prompts):
        lines += [f"## {index + 1}. {prompt}", ""]
        if prompt in references:
            lines += ["**기준 답(승인된 경험)**", "", references[prompt], ""]
        for label, title in (("base", "바탕 모델"), ("tuned", "학습 모델")):
            flags = answer_flags(answers[label][index])
            flagged[label] += bool(flags)
            lines += [f"**{title}**{' — ⚠ ' + ', '.join(flags) if flags else ''}", "", answers[label][index] or "(빈 답)", ""]
    report = path / "eval-report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    run["eval"] = {"engine": args.engine, "report": str(report), "prompts": len(prompts), "flagged": flagged,
                   "identity": {**totals, "cases": len(CASES)}}
    mark(run, "evaluated", f"정체성 바탕 {totals['base']}/{len(CASES)} → 학습 {totals['tuned']}/{len(CASES)}, "
                           f"점검 표시 바탕 {flagged['base']} · 학습 {flagged['tuned']} — 채택은 사용자 결정")
    save_run(path, run)
    print(f"평가 보고서: {report}\n  정체성 채점 — 바탕 {totals['base']}/{len(CASES)}, 학습 {totals['tuned']}/{len(CASES)}\n"
          f"  점검 표시 — 바탕 {flagged['base']}개, 학습 {flagged['tuned']}개 / {len(prompts)}문항")
    return 0


def cmd_status(store: Storage, runs: Path, args: argparse.Namespace) -> int:
    home = lora_home()
    python = venv_python(home)
    print("LoRA 학습 파이프라인 (ADR-084)")
    print(f"  학습 환경   {'있음' if python.is_file() else '없음'}  {python}")
    print(f"  GGUF 변환기 {'있음' if (home / 'llama.cpp' / 'convert_hf_to_gguf.py').is_file() else '없음'}")
    counts = store.experience_counts()
    pool = store.training_pool()
    held = store.list_experiences(stage="approved", split="eval", limit=10_000)
    print(f"  학습 데이터 '질문 → 좋은 답' train {sum(map(sft_ready, pool))}건 · eval {sum(map(sft_ready, held))}건 "
          f"(효과 기대 최소 {MIN_TRAIN}건) · 승인됐지만 교정·교훈 형식 {len(pool) + len(held) - sum(map(sft_ready, pool + held))}건 · "
          f"검토 대기 {sum(v for k, v in counts.items() if k.startswith(('raw/', 'candidate/')))}건")
    items = sorted(runs.glob("*/run.json")) if runs.is_dir() else []
    if not items:
        print("  실행 없음")
    for file in items:
        run = json.loads(file.read_text(encoding="utf-8"))
        train = run.get("train", {})
        print(f"  {run['run_id']:<20} {run['state']:<10} 학습 {run['dataset']['train']} · 평가 {run['dataset']['eval']}"
              f" · 손실 {train.get('eval_loss_before', '-')} → {train.get('eval_loss_after', '-')}"
              f"{' · ' + run['export']['ollama_model'] if run.get('export') else ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--run-id", default="")
    prepare.add_argument("--base", default=DEFAULT_BASE)
    prepare.add_argument("--min-train", type=int, default=MIN_TRAIN)
    prepare.add_argument("--allow-small", action="store_true", help="데이터가 적어도 연결 확인용으로 준비")
    train = sub.add_parser("train")
    train.add_argument("--run-id", required=True)
    train.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    train.add_argument("--yes", action="store_true", help="GPU 사용 동의")
    train.add_argument("--epochs", type=int, default=3)
    train.add_argument("--max-steps", type=int, default=0)
    train.add_argument("--lr", type=float, default=2e-4)
    train.add_argument("--rank", type=int, default=16)
    train.add_argument("--alpha", type=int, default=16)
    train.add_argument("--grad-accum", type=int, default=4)
    train.add_argument("--max-len", type=int, default=1024)
    train.add_argument("--vram-limit-gb", type=float, default=VRAM_LIMIT_GB)
    train.add_argument("--timeout", type=float, default=3 * 3600)
    train.add_argument("--no-record", dest="record", action="store_false", help="학습 기록(training_runs)을 남기지 않음")
    export = sub.add_parser("export")
    export.add_argument("--run-id", required=True)
    export.add_argument("--outtype", choices=("q8_0", "f16", "bf16"), default="q8_0")
    export.add_argument("--keep-merged", action="store_true")
    evaluate = sub.add_parser("eval")
    evaluate.add_argument("--run-id", required=True)
    evaluate.add_argument("--engine", choices=("hf", "ollama"), default="hf")
    evaluate.add_argument("--cpu", action="store_true", help="ollama 엔진도 GPU 없이(num_gpu 0)")
    evaluate.add_argument("--yes", action="store_true", help="GPU 사용 동의")
    evaluate.add_argument("--max-new", type=int, default=256)
    evaluate.add_argument("--timeout", type=float, default=3 * 3600)
    args = parser.parse_args(argv)

    settings = load_settings()
    data_dir = settings.data_dir if settings.data_dir.is_absolute() else ROOT / settings.data_dir
    store = Storage(data_dir / settings.database_name)
    args.host = settings.ollama_host
    runs = data_dir / "lora"
    handler = {"status": cmd_status, "prepare": cmd_prepare, "train": cmd_train, "export": cmd_export,
               "eval": cmd_eval}[args.command]
    return handler(store, runs, args)


if __name__ == "__main__":
    raise SystemExit(main())
