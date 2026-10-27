"""프롬프트 순서(ADR-119, 설정 stable_prompt_prefix)를 켜고 끈 비교 — 2026-10-26 claude.

모델 서버(Ollama·llama.cpp 계열)는 앞 요청과 앞부분이 같은 만큼 다시 읽지 않는다. 예전 순서는 맨 앞 지시 안에 분 단위 시각이 있고
그 뒤에 질문마다 다시 고르는 메모·교훈·자료가 와서, 대화가 길어져도 매 차례 처음부터 다시 읽는다.

    python -X utf8 scripts/measure_prompt_cache.py            # 모델 없이: 차례마다 앞 요청과 같은 앞부분 비율(글자)
    python -X utf8 scripts/measure_prompt_cache.py --live     # 이 PC 모델로: 첫 글자까지 걸린 시간·입력 토큰·답(순서별)

--live 는 임시 폴더의 새 DB 로 돈다(사용자 대화·메모는 읽지도 쓰지도 않는다). 두 순서를 같은 질문으로 번갈아(끔→켬→끔) 재고,
결과와 답을 logs/prompt-cache-<시각>.json 에 남긴다. 답이 나빠지지 않았는지는 사람이 읽고 정한다.
"""
from __future__ import annotations

import argparse
import dataclasses
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis import core as core_module  # noqa: E402
from jarvis.core import JarvisCore  # noqa: E402
from jarvis.hardware import HardwareInfo, PerformanceProfile  # noqa: E402
from jarvis.providers import MockLLMProvider  # noqa: E402
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


def live(out: Path) -> dict:
    from jarvis.app import build_core
    from jarvis.config import load_settings
    base = load_settings()
    runs = []
    for stable in (False, True, False):   # 끔→켬→끔: 첫 실행의 모델 올리기·데우기가 한쪽에만 들어가지 않게
        with tempfile.TemporaryDirectory() as temp:
            settings = dataclasses.replace(base, data_dir=Path(temp) / "data", log_dir=Path(temp) / "logs",
                                           stable_prompt_prefix=stable)
            core, _notes = build_core(settings, approval_callback=lambda _r: False)
            core.storage.remember(*MEMORY)
            provider = core.providers[core_module.Destination.LOCAL_GENERAL]
            session = core.get_or_create_session(resume=False)
            turns = []
            for question in QUESTIONS:
                answer = core.handle(session, question)[0]
                metrics = dict(getattr(provider, "last_metrics", None) or {})
                turns.append({"question": question[:40], "first_token_seconds": metrics.get("first_token_seconds"),
                              "prompt_tokens": metrics.get("prompt_tokens"), "total_seconds": metrics.get("total_seconds"),
                              "answer": answer})
                print(f"stable={stable} {question[:20]!r}: first={metrics.get('first_token_seconds')}s "
                      f"prompt={metrics.get('prompt_tokens')}", flush=True)
            runs.append({"stable_prompt_prefix": stable, "model": getattr(provider, "model", "?"), "turns": turns})
    result = {"at": datetime.now().isoformat(timespec="seconds"), "runs": runs}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="이 PC 모델로 실제 시간을 잰다(임시 DB)")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    if args.live:
        out = Path(args.out) if args.out else ROOT / "logs" / f"prompt-cache-{datetime.now():%Y%m%d-%H%M}.json"
        live(out)
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
