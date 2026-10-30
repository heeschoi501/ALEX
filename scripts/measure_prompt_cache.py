"""프롬프트 순서(ADR-119, 설정 stable_prompt_prefix)를 켜고 끈 비교 — 2026-10-26 claude.

앞부분 일치는 캐시 재사용의 후보일 뿐, 실제 캐시 적중률·속도 개선율은 아니다. 서버·토큰화·캐시 설정에 따라 다르다.

    python -X utf8 scripts/measure_prompt_cache.py            # 모델 없이: 차례마다 앞 요청과 같은 앞부분 비율(글자)
    python -X utf8 scripts/measure_prompt_cache.py --live --pairs 4  # 모델 준비 뒤 끔/켬·켬/끔 순서를 번갈아 4쌍

--live 는 임시 DB·고정 대화 기록·고정 시각 변화를 쓰고, 로컬 general 모델 하나를 스트리밍으로 직접 부른다.
각 실행의 준비 요청은 통계에서 제외한다. 서버 캐시를 초기화하지 않으므로 cold-cache 측정으로 해석하지 않는다.
결과와 답을 logs/prompt-cache-<시각>.json 에 남긴다. 답이 나빠지지 않았는지는 사람이 읽고 정한다.
"""
from __future__ import annotations

import argparse
import dataclasses
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis import core as core_module  # noqa: E402
from jarvis.core import JarvisCore  # noqa: E402
from jarvis.hardware import HardwareInfo, PerformanceProfile  # noqa: E402
from jarvis.providers import Message, MockLLMProvider  # noqa: E402
from jarvis.storage import Storage  # noqa: E402

NOTE = ("FIFO 설계 메모: 깊이 16, 폭 32비트, 동기식. 쓰기 포인터와 읽기 포인터는 5비트로 두고 최상위 비트로 가득·빔을 구분한다. "
        "리셋은 동기식 active-low. 거의 가득(almost_full)은 남은 칸이 2 이하일 때. ") * 12
QUESTIONS = (
    "아래 메모를 읽고 핵심을 세 줄로 요약해줘.\n" + NOTE,
    "포인터를 5비트로 두는 이유를 설명해줘.",
    "almost_full 조건을 Verilog 한 줄로 써줘.",
    "리셋을 비동기로 바꾸면 무엇을 조심해야 해?",
    "이 FIFO 를 검증할 테스트 시나리오 다섯 개만 적어줘.",
    "아까 요약에서 빠진 게 있으면 알려줘.",
)
MEMORY = ("선호", "답은 짧게, 코드는 SystemVerilog 로")
# Keep the input history identical across settings and repetitions; generated
# answers are saved for quality review, not fed into the following timed turn.
REPLAY_ANSWERS = (
    "깊이 16·폭 32비트인 동기식 FIFO다. 5비트 읽기·쓰기 포인터의 최상위 비트로 가득·빔을 구분한다. "
    "동기식 active-low 리셋을 쓰며 남은 칸이 2 이하이면 almost_full이다.",
    "주소 4비트와 순환 여부를 구분하는 최상위 1비트를 합쳐 5비트다.",
    "assign almost_full = ((wr_ptr - rd_ptr) >= 5'd14);",
    "비동기 리셋은 해제 시점의 동기화와 recovery/removal 조건을 확인한다.",
    "빈 상태 읽기, 가득 찬 상태 쓰기, 동시 읽기·쓰기, 포인터 순환, 리셋을 검증한다.",
    "almost_full의 경계값과 포인터 순환 시 차이 계산도 확인해야 한다.",
)


class Recorder(MockLLMProvider):
    def __init__(self):
        super().__init__("general", "G")
        self.calls: list[str] = []

    def generate(self, messages, **kwargs):
        self.calls.append("".join(f"<{m.role}>{m.content}" for m in messages))
        return "알겠습니다. " + "설명 " * 40


def reuse(previous: str, current: str) -> int:
    """앞 요청과 같은 앞부분 글자 수."""
    size = 0
    for a, b in zip(previous, current):
        if a != b:
            break
        size += 1
    return size


def offline(stable: bool) -> dict:
    with tempfile.TemporaryDirectory() as temp:
        storage = Storage(Path(temp) / "t.db")
        storage.remember(*MEMORY)
        model = Recorder()
        core = JarvisCore(storage=storage, local_general=model, local_coder=model, local_only=True,
                          hardware=HardwareInfo(os_name="Windows", cpu="x86", ram_gb=93.6, gpu="RTX 5080", vram_gb=15.9,
                                                profile=PerformanceProfile.DESKTOP),
                          cloud_coder=MockLLMProvider("cc", "X"), cloud_reasoning=MockLLMProvider("cr", "Y"),
                          stable_prompt_prefix=stable)
        minutes = iter(range(1000))
        with mock.patch.object(core_module, "clock_line", lambda: f"지금은 10시 {next(minutes) % 60:02d}분이다."):
            session = core.get_or_create_session(resume=False)
            for question in QUESTIONS:
                core.handle(session, question)
    rows = [{"turn": n + 1, "chars": len(cur), "reused": reuse(prev, cur) if n else 0}
            for n, (prev, cur) in enumerate(zip([""] + model.calls, model.calls))]
    later = rows[1:]
    return {"stable_prompt_prefix": stable, "turns": rows,
            "reused_share_after_first": round(sum(r["reused"] for r in later) / max(1, sum(r["chars"] for r in later)), 3)}


def source_revision() -> tuple[str, bool | None]:
    """git 커밋과 수정 여부. 개발 PC 원본(C:\\ALEX\\jarvis)은 git 저장소가 아니라 코드 지문으로 대신한다(2026-10-30 PC 확인)."""
    try:
        revision = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True, check=True).stdout.strip()
        return revision, bool(dirty)
    except (OSError, subprocess.CalledProcessError):
        from jarvis.launcher import code_fingerprint
        return "code:" + code_fingerprint(), None


def live(out: Path, pairs: int = 4) -> dict:
    from jarvis.app import build_core
    from jarvis.config import load_settings
    if pairs < 2 or pairs % 2:
        raise ValueError("--pairs 는 2 이상의 짝수여야 합니다(끔/켬·켬/끔 균형)")
    base = load_settings()
    runs = []
    revision, dirty = source_revision()
    result = {"at": datetime.now().isoformat(timespec="seconds"), "source_commit": revision, "source_dirty": dirty,
              "pairs": pairs, "cache_reset": False, "history": "fixed replay", "runs": runs}
    out.parent.mkdir(parents=True, exist_ok=True)
    schedule = [(pair + 1, stable) for pair in range(pairs)
                for stable in ((False, True) if pair % 2 == 0 else (True, False))]
    for pair, stable in schedule:
        with tempfile.TemporaryDirectory() as temp:
            settings = dataclasses.replace(base, data_dir=Path(temp) / "data", log_dir=Path(temp) / "logs",
                                           stable_prompt_prefix=stable, local_only=True, summary_enabled=False)
            core, _notes = build_core(settings, approval_callback=lambda _r: False)
            core.storage.remember(*MEMORY)
            provider = core.providers[core_module.Destination.LOCAL_GENERAL]
            # Streaming is required for first_token_seconds in both providers.
            provider.generate([Message("user", "준비 확인: OK 만 답해 주세요.")], on_chunk=lambda _s: None)
            session = core.get_or_create_session(resume=False)
            turns = []
            minutes = iter(range(1000))
            with mock.patch.object(core_module, "clock_line", lambda: f"지금은 10시 {next(minutes) % 60:02d}분이다."):
                for question, replay in zip(QUESTIONS, REPLAY_ANSWERS):
                    core.storage.add_message(session, "user", question)
                    messages = core._build_messages(session, core_module.Destination.LOCAL_GENERAL)
                    answer = provider.generate(messages, on_chunk=lambda _s: None)
                    metrics = dict(getattr(provider, "last_metrics", None) or {})
                    turns.append({"question": question[:40], "input_chars": sum(len(m.content) for m in messages),
                                  "first_token_seconds": metrics.get("first_token_seconds"),
                                  "prompt_tokens": metrics.get("prompt_tokens"), "output_tokens": metrics.get("output_tokens"),
                                  "total_seconds": metrics.get("total_seconds"), "answer": answer})
                    core.storage.add_message(session, "assistant", replay)
                    print(f"pair={pair} stable={stable} {question[:20]!r}: first={metrics.get('first_token_seconds')}s "
                          f"prompt={metrics.get('prompt_tokens')}", flush=True)
            runs.append({"pair": pair, "stable_prompt_prefix": stable, "model": getattr(provider, "model", "?"),
                         "parameters": {k: getattr(provider, k, None) for k in ("num_ctx", "num_predict", "think", "keep_alive")},
                         "turns": turns})
            # Preserve completed runs if a later model request fails.
            out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="이 PC 모델로 실제 시간을 잰다(임시 DB)")
    parser.add_argument("--pairs", type=int, default=4, help="끔/켬 비교 쌍 수(2 이상의 짝수, 기본 4)")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    if args.live:
        out = Path(args.out) if args.out else ROOT / "logs" / f"prompt-cache-{datetime.now():%Y%m%d-%H%M}.json"
        if args.pairs < 2 or args.pairs % 2:
            parser.error("--pairs 는 2 이상의 짝수여야 합니다")
        live(out, pairs=args.pairs)
        print(f"결과: {out}")
        return 0
    for stable in (False, True):
        result = offline(stable)
        print(f"stable_prompt_prefix={stable}: 둘째 차례부터 앞 요청과 같은 앞부분 "
              f"{result['reused_share_after_first'] * 100:.1f}% (차례별 재사용/전체 글자: "
              + ", ".join(f"{r['reused']}/{r['chars']}" for r in result["turns"][1:]) + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
