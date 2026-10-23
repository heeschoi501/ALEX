"""스스로 점검(2026-10-23, 준비만 — 사용자 "언제든 실행할 수 있도록 준비"). `/self-check [개수]` 로 사람이 돌릴 때만 돈다.

ALEX 가 정답을 아는 질문을 스스로 풀어 보고, 틀린 것을 교훈으로 바꾼다. 정답은 모델이 아니라 **코드에서** 나온다:
자기 기능 목록(jarvis/selfknow.py — 명령 목록 /help·화면 설명)의 줄마다 "○○ 하려면 어떻게 해?" 를 만들고, 답에 그 명령(또는
화면 이름)이 들어 있으면 통과. 틀리면 '사용자가 물었다 / ALEX 는 이렇게 답했다 / 정답 근거' 교훈(경험 answer, 출처 selfcheck:)을
남기고 승인한다 — 다음 비슷한 질문에서 교훈으로 쓰인다(ADR-055). 같은 질문의 교훈은 한 번만.

지키는 것: 한 번에 최대 20개, 질문 사이에 그래픽카드 온도(80℃ 이상이면 멈춤)를 본다, 실행할 때마다 다음 질문부터(돌아가며),
결과는 data/selfcheck/runs.jsonl. 일반 대화 기록(세션)에는 남기지 않는다. 코딩 과제 점검은 다음 단계(자동 수정 시험 묶음).
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Callable

MAX_PER_RUN = 20
SOURCE_PREFIX = "selfcheck:"
HOT_CELSIUS = 80


def questions(lines: list[str]) -> list[dict]:
    """기능 줄 → (질문, 답에 들어 있어야 할 것, 근거 줄)."""
    out = []
    for line in lines:
        head, _, what = line.partition(" — ")
        what = re.sub(r"\s*\(.*?\)\s*", " ", what).strip(" .") or head
        if head.startswith("명령 /"):
            command = head.split()[1]
            out.append({"question": f"ALEX 에서 {what[:80]} — 이거 하려면 어떻게 해?", "expect": [command], "line": line})
        elif head.startswith("화면 '"):
            name = head[len("화면 '"):].rstrip("'")
            out.append({"question": f"ALEX 화면에서 {what[:80]} — 이건 어디서 해?", "expect": [name], "line": line})
    return out


def gpu_cool(limit: int = HOT_CELSIUS) -> bool:
    """nvidia-smi 로 온도를 본다. 못 읽으면(그래픽카드 이상일 수 있음) 멈추는 쪽으로."""
    try:
        done = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, timeout=10)
        return done.returncode == 0 and int(done.stdout.split()[0]) < limit
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return False


def _answer(core, question: str) -> str:
    """대화와 같은 지시문(기본·자기 기능·교훈)으로 이 PC 일반 모델에게 묻는다. 세션에는 남기지 않는다."""
    from .lessons import format_lessons
    from .providers import Message
    from .router import Destination
    messages = [Message("system", core._system_base(destination=Destination.LOCAL_GENERAL))]
    knowing = getattr(core, "self_knowledge", None)
    if knowing is not None and (block := knowing.block(question)):
        messages.append(Message("system", block))
    if lessons := core._select_lessons(question):
        messages.append(Message("system", format_lessons(lessons)))
    messages.append(Message("user", question))
    return str(core.providers[Destination.LOCAL_GENERAL].generate(messages))


def run(core, count: int = 5, *, folder: Path | None = None, answer: Callable[[str], str] | None = None,
        cool: Callable[[], bool] = gpu_cool, say: Callable[[str], None] = lambda _text: None) -> str:
    knowing = getattr(core, "self_knowledge", None)
    if knowing is None:
        return "자기 기능 목록이 없어 점검할 수 없습니다."
    bank = questions(knowing.lines)
    if not bank:
        return "점검할 질문이 없습니다."
    count = max(1, min(int(count), MAX_PER_RUN))
    folder = Path(folder) if folder else Path(core.storage.db_path).parent / "selfcheck"
    folder.mkdir(parents=True, exist_ok=True)
    state_path = folder / "state.json"
    try:
        start = int(json.loads(state_path.read_text(encoding="utf-8")).get("next", 0))
    except (OSError, ValueError, AttributeError):
        start = 0
    answer = answer or (lambda question: _answer(core, question))
    results, learned, stopped = [], 0, ""
    for step in range(count):
        if not cool():
            stopped = f"그래픽카드 온도가 {HOT_CELSIUS}℃ 이상이거나 읽지 못해 멈췄습니다."
            break
        item = bank[(start + step) % len(bank)]
        say(f"[스스로 점검 {step + 1}/{count}] {item['question'][:60]}\n")
        reply = answer(item["question"])
        passed = any(expect.lower() in reply.lower() for expect in item["expect"])
        results.append({"question": item["question"], "expect": item["expect"], "passed": passed})
        if not passed and _learn(core, item, reply):
            learned += 1
    done = len(results)
    state_path.write_text(json.dumps({"next": (start + done) % len(bank)}), encoding="utf-8")
    record = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "checked": done,
              "passed": sum(r["passed"] for r in results), "learned": learned, "stopped": stopped, "results": results}
    with (folder / "runs.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    lines = [f"스스로 점검 {done}개 — 통과 {record['passed']}, 틀려서 교훈으로 남김 {learned}" + (f" ({stopped})" if stopped else "")]
    lines += [f"- {'통과' if r['passed'] else '틀림'}: {r['question'][:70]} (정답 {', '.join(r['expect'])})" for r in results]
    return "\n".join(lines)


def _learn(core, item: dict, reply: str) -> bool:
    """틀린 답 → 승인된 교훈 하나(같은 질문은 한 번만). 정답 근거는 코드에서 뽑은 기능 줄이다."""
    source = SOURCE_PREFIX + hashlib.sha1(item["question"].encode("utf-8")).hexdigest()[:12]
    storage = core.storage
    if storage.has_experience_source(source):
        return False
    experience_id = storage.record_experience(
        "answer", situation=f"사용자가 물었다: {item['question']}", judgment=f"ALEX 는 이렇게 답했다: {reply[:600]}",
        rationale="스스로 점검 — 답에 실제 기능이 빠졌다", correction=f"정답 근거(ALEX 기능 목록): {item['line'][:300]}",
        verdict="bad", source=source)
    storage.review_experience(experience_id, "approved", reviewed_by="selfcheck")
    return True
