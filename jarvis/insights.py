"""코딩 에이전트의 절차 교훈(insight) — 결과 기록에서 뽑아 다음 수정안 요청에 붙인다 (2026-09-30 claude, ADR-070).

Codex 조사(docs/ALEX_LEARNING_RESEARCH_20260930.md) 2순위. 방식은 ExpeL(arXiv 2308.10144)을 따른다:
- 같은 과제의 실패 회차와 통과한 최종 수정안을 비교하고, 여러 과제의 성공을 모아 '절차' 를 글로 뽑는다.
- 목록은 연산으로만 바꾼다 — ADD(점수 2로 시작), EDIT(+1), UPVOTE(+1), DOWNVOTE(-1). 점수가 0 이 되면 지운다
  (성공한 시도도 최적이 아닐 수 있어 한 번의 추출이 목록을 망치지 않게).
- 쓸 때는 목록 전체를 수정안 요청에 붙인다. 성공 사례를 예시로 검색해 붙이는 것은 문맥 예산 때문에 아직 하지 않는다.
ALEX 규칙: 모델 가중치는 바뀌지 않는다. 기본은 꺼짐 — 효과는 평가 과제(학습/평가 분리)로 켜고 끈 비교로 재고, 켜는 것은 사용자가 정한다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil

INSIGHTS_FILE = "coding-insights.json"
MAX_INSIGHTS = 12
MAX_INSIGHT_CHARS = 220
PROMPT_BUDGET = 1600
_OPERATION = re.compile(r"^\s*(ADD|EDIT|UPVOTE|DOWNVOTE)\b\s*(\d+)?\s*[:：]?\s*(.*)$", re.I)  # \b: ADDITION 은 연산이 아니다


@dataclass
class Insight:
    id: int
    text: str
    count: int = 2
    sources: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


class InsightStore:
    """DB 옆 JSON 한 파일. 사람이 읽고 지울 수 있다."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @classmethod
    def beside(cls, storage) -> "InsightStore | None":
        db_path = getattr(storage, "db_path", None)
        return cls(Path(db_path).parent / INSIGHTS_FILE) if db_path else None

    def _read(self) -> tuple[dict, bool]:
        """(내용, 손상 여부). 파일이 없으면 빈 내용·손상 아님. 읽을 수 없거나 dict 가 아니면 손상."""
        if not self.path.exists():
            return {}, False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}, True
        return (data, False) if isinstance(data, dict) else ({}, True)

    def load(self) -> list[Insight]:
        """형식이 맞는 교훈만 읽는다(Codex 리뷰 R06 — null·문자열 점수 등이 정렬·연산을 깼다). 건너뛴 수는 self.skipped."""
        data, _damaged = self._read()
        items = data.get("insights")
        self.skipped = 0
        if not isinstance(items, list):
            return []
        loaded: list[Insight] = []
        seen: set[int] = set()
        for item in items:
            insight = _valid_insight(item)
            if insight is None or insight.id in seen:
                self.skipped += 1
                continue
            seen.add(insight.id)
            loaded.append(insight)
        return loaded

    def learned(self) -> list[str]:
        """이미 교훈 추출에 쓴 결과 기록(auto 사건)의 키."""
        keys = self._read()[0].get("learned")
        return [k for k in keys if isinstance(k, str)] if isinstance(keys, list) else []

    def problem(self) -> str:
        """파일이 그대로 다시 써지지 않는 까닭(없으면 ""). 문법이 맞는 JSON 이라도 틀린 항목·틀린 형식이면 문제다(Codex 2차 R2-03)."""
        data, damaged = self._read()
        if damaged:
            return "JSON 으로 읽을 수 없거나 최상위가 객체가 아님"
        if "insights" in data and not isinstance(data["insights"], list):
            return "insights 가 목록이 아님"
        if "learned" in data and not (isinstance(data["learned"], list) and all(isinstance(k, str) for k in data["learned"])):
            return "learned 가 문자열 목록이 아님"
        self.load()
        return f"형식이 틀린 교훈 {self.skipped}개" if self.skipped else ""

    def save(self, insights: list[Insight], learned: list[str] | None = None) -> None:
        keys = self.learned() if learned is None else learned
        if self.path.exists() and self.problem():  # 항목을 잃는 저장 전에 원본 바이트를 옆에 남긴다 — 사람이 확인할 수 있게
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            shutil.copy2(self.path, self.path.with_name(f"{self.path.name}.damaged-{stamp}"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"insights": [asdict(i) for i in insights], "learned": keys[-500:]},
                                        ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(self.path)

    def remove(self, insight_id: int) -> bool:
        insights = self.load()
        kept = [i for i in insights if i.id != insight_id]
        if len(kept) == len(insights):
            return False
        self.save(kept)
        return True


def _valid_insight(item: object) -> Insight | None:
    if not isinstance(item, dict):
        return None
    ident, text, count = item.get("id"), item.get("text"), item.get("count", 2)
    if type(ident) is not int or ident <= 0 or not isinstance(text, str) or not text.strip() or type(count) is not int:
        return None
    sources = [s for s in item.get("sources", []) if isinstance(s, str)] if isinstance(item.get("sources"), list) else []
    updated = item.get("updated_at") if isinstance(item.get("updated_at"), str) else ""
    return Insight(ident, " ".join(text.split())[:MAX_INSIGHT_CHARS], count, sources[-5:], updated)


def has_operations(text: str) -> bool:
    """모델 출력에 연산 줄이 하나라도 있나 — 없으면 해석 실패로 보고 그 기록을 '쓴 것' 으로 표시하지 않는다(Codex 리뷰)."""
    return any(_OPERATION.match(line) for line in text.splitlines())


def apply_operations(insights: list[Insight], text: str, source: str) -> tuple[list[Insight], list[str]]:
    """모델 출력의 연산 줄만 읽는다(그 밖의 글은 버린다). 돌려주는 것: 새 목록, 적용한 연산 기록."""
    items, applied, _skipped = apply_operations_detailed(insights, text, source)
    return items, applied


def learning_consumed(applied: list[str], skipped: list[str]) -> bool:
    """이 출력으로 기록을 '썼다' 고 표시해도 되나(Codex 2차 R2-04). 하나라도 적용됐거나, 건너뛴 것이 모두 '이미 있는 교훈' 이면
    의도된 무변경이다. 내용 없는 ADD, 없는 번호, 목록이 찬 경우만 있으면 해석 실패로 보고 다음에 다시 쓴다."""
    return bool(applied) or (bool(skipped) and all(reason == "duplicate" for reason in skipped))


def apply_operations_detailed(insights: list[Insight], text: str, source: str) -> tuple[list[Insight], list[str], list[str]]:
    """apply_operations 와 같고, 건너뛴 연산의 이유(duplicate·empty·unknown_id·full)도 돌려준다."""
    skipped: list[str] = []
    by_id = {i.id: i for i in insights}
    next_id = max(by_id, default=0) + 1
    applied: list[str] = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for line in text.splitlines():
        match = _OPERATION.match(line)
        if not match:
            continue
        operation, number, body = match.group(1).upper(), match.group(2), " ".join(match.group(3).split())
        body = body[:MAX_INSIGHT_CHARS]
        if operation == "ADD":
            if not body:
                skipped.append("empty")
                continue
            if any(body == i.text for i in by_id.values()):
                skipped.append("duplicate")
                continue
            if len(by_id) >= MAX_INSIGHTS:
                skipped.append("full")
                continue
            by_id[next_id] = Insight(next_id, body, 2, [source], now)
            applied.append(f"ADD {next_id}")
            next_id += 1
            continue
        if not number or int(number) not in by_id:
            skipped.append("unknown_id")
            continue
        insight = by_id[int(number)]
        if operation == "EDIT" and not body:
            skipped.append("empty")
            continue
        if operation == "EDIT" and body == insight.text:
            skipped.append("duplicate")
            continue
        if operation == "EDIT":
            insight.text, insight.count = body, insight.count + 1
        elif operation == "UPVOTE":
            insight.count += 1
        elif operation == "DOWNVOTE":
            insight.count -= 1
        else:
            continue
        insight.updated_at = now
        if source not in insight.sources:
            insight.sources = (insight.sources + [source])[-5:]
        applied.append(f"{operation} {insight.id}")
        if insight.count <= 0:
            del by_id[insight.id]
    return sorted(by_id.values(), key=lambda i: (-i.count, i.id)), applied, skipped


def learning_key(auto_event: dict, evidence: str = "") -> str:
    """'이 기록을 이 사후 확인으로 배웠다' 는 표시 — 사건과 사후 확인 글의 지문(Codex 3차 R3-02)."""
    return f"{auto_event.get('run_id')}@{auto_event.get('at')}#" + hashlib.sha1(evidence.encode("utf-8")).hexdigest()[:10]


def unlearned(autos: list[dict], learned: list[str], evidence_of=lambda _event: "") -> list[tuple[dict, bool]]:
    """아직 쓰지 않은 (auto 사건, 전에 다른 사후 확인으로 반영한 적 있음).

    같은 사건·같은 사후 확인은 한 번만 쓴다(Codex 2차 R2-05 — 다시 돌리면 재투표했다). 배운 뒤 전체 검사 결과 같은 사후 확인이
    바뀌면 한 번 더 내놓아 기존 교훈을 고치게 한다(3차 R3-02). 지문이 없는 옛 표시(run@at)는 사후 확인이 없던 것으로 본다.
    """
    empty = hashlib.sha1(b"").hexdigest()[:10]
    done = {key if "#" in key else f"{key}#{empty}" for key in learned}
    before = {key.split("#")[0] for key in done}
    seen, fresh = set(), []
    for event in autos:
        key = learning_key(event, evidence_of(event))
        if key in done or key in seen:
            continue
        seen.add(key)
        fresh.append((event, key.split("#")[0] in before))
    return fresh


REVISIT_NOTE = "(이미 한 번 반영한 기록이다 — 사후 확인이 바뀌었다. 같은 근거로 다시 올리지 말고 바뀐 사실에 맞게 기존 교훈을 고치거나 내려라)"


def prompt_block(insights: list[Insight], budget: int = PROMPT_BUDGET) -> str:
    """수정안 요청에 붙일 글. 점수 높은 것부터 예산까지."""
    if not insights:
        return ""
    lines = ["과거 코딩 작업에서 뽑은 절차 교훈(참고용, 이번 과제에 맞을 때만 따른다):"]
    for insight in sorted(insights, key=lambda i: (-i.count, i.id)):
        line = f"- {insight.text}"
        if sum(len(x) + 1 for x in lines) + len(line) > budget:
            continue
        lines.append(line)
    return "\n".join(lines) + "\n" if len(lines) > 1 else ""


EXTRACTION_RULES = (
    "너는 코딩 에이전트의 작업 기록을 보고 다음 작업에 도움이 될 **절차 교훈**을 관리한다. 특정 파일·함수·값에만 맞는 사실은 교훈이 아니다 —\n"
    "다른 버그에도 옮겨 쓸 수 있는 방법(무엇을 먼저 보고, 어떻게 고치고, 무엇을 피하는지)만 적는다. 실패 회차와 통과한 최종 수정을 비교해라.\n"
    "출력은 아래 연산 줄만 쓴다(설명 금지). 한 줄에 하나, 교훈은 한국어 한 문장 200자 이내.\n"
    "ADD: 새 교훈\nEDIT 번호: 고친 교훈\nUPVOTE 번호\nDOWNVOTE 번호\n"
    f"기존 교훈이 기록과 맞으면 UPVOTE, 틀리거나 해로우면 DOWNVOTE, 겹치면 새로 ADD 하지 말고 EDIT 한다. 목록은 최대 {MAX_INSIGHTS}개다.\n"
    "'사후 확인' 이 전체 검사 FAIL 이면 그 수정을 성공으로 배우지 않는다. 원인이 그 수정인지 환경·간헐 실패인지는 기록만으로 단정하지 않는다.\n"
)


def after_evidence(run_events: list[dict], auto_event: dict) -> str:
    """자동 수정 뒤 **그 수정안에 대해** 실제로 확인된 것(적용·선택 테스트·전체 검사) 한 줄.

    교훈이 '복사본에서 선택 테스트 통과' 를 최종 성공으로 착각하지 않게 한다(Codex 리뷰 R05). 같은 작업의 다른 수정안 결과가
    넘어오지 않게, 이 수정안의 적용 사건의 소스 지문(digest)과 같은 지문의 검사만 쓰고 다음 수정안 사건에서 멈춘다(Codex 2차 R2-01).
    연결을 증명할 수 없으면 '확인 안 됨' 으로 남긴다.
    """
    if auto_event.get("type") == "code":  # 코딩 세션(2026-10-23): 작업 폴더에서 바로 고침 — 독립 관련 검사가 사후 확인
        return ("사후 확인: 코딩 세션에서 바로 적용 · 독립 관련 검사 " + ("통과" if auto_event.get("result") == "verified" else "미통과")
                + " · 전체 검사 확인 안 됨")
    if auto_event.get("result") != "copy_passed":
        return "사후 확인: 복사본에서 통과하지 못해 제안하지 않음"
    position = next((index for index, event in enumerate(run_events) if event is auto_event), None)
    later = run_events[position + 1:] if position is not None else []
    proposal = auto_event.get("proposal_id") or ""  # 없음(None)과 빈 문자열은 같은 것 — 수정안 ID 가 없는 옛 기록
    same = lambda event: (event.get("proposal_id") or "") == proposal  # noqa: E731
    boundary = next((i for i, e in enumerate(later)
                     if e.get("type") == "auto"
                     or (e.get("type") in ("proposal", "applied", "discarded") and not same(e))), len(later))
    window = later[:boundary]
    applied = next((e for e in window if e.get("type") == "applied" and same(e)), None)
    if applied is None:
        discarded = any(e.get("type") == "discarded" and same(e) for e in window)
        return "사후 확인: " + ("폐기함" if discarded else "적용 안 됨") + " · 전체 검사 확인 안 됨"
    digest = applied.get("digest")
    after_apply = window[window.index(applied) + 1:]

    def mine(event: dict) -> bool:
        if event.get("valid") is False:
            return False
        if digest and event.get("digest"):
            return event["digest"] == digest
        return not digest and not event.get("digest")  # 지문 없는 옛 기록끼리만, 이 수정안의 경계 안에서

    parts = ["적용함"]
    tests = [e for e in after_apply if e.get("type") == "tests" and mine(e)]
    if tests:
        parts.append("선택 테스트 " + ("통과" if tests[-1].get("exit") == 0 else f"실패(종료 코드 {tests[-1].get('exit')})"))
    suites = [e for e in after_apply if e.get("type") == "suite" and mine(e)]
    if suites:
        failures = suites[-1].get("new_failures") or []
        parts.append(f"전체 검사 {suites[-1].get('result')}"
                     + (f"(새 실패 {len(failures)}: {', '.join(failures[:2])[:160]})" if failures else ""))
    else:
        parts.append("전체 검사 확인 안 됨")
    return "사후 확인: " + " · ".join(parts)


def extraction_prompt(insights: list[Insight], trajectories: list[str]) -> str:
    current = "\n".join(f"{i.id}. (점수 {i.count}) {i.text}" for i in insights) or "(없음)"
    return f"지금 교훈 목록:\n{current}\n\n작업 기록:\n" + "\n\n".join(trajectories)


def trajectory_text(auto_event: dict, limit: int = 2400, after: str = "") -> str:
    """결과 기록의 auto 사건 하나를 추출용 글로. 예산을 넘으면 **뒤쪽 회차(통과한 수정)를 먼저** 지키고 앞 회차는 한 줄로 줄인다.

    2026-09-30 Codex 리뷰: 앞에서부터 붙이고 자르면 마지막 수정·통과 증거가 빠졌다.
    """
    header = (f"[{auto_event.get('run_id', '')}] 목표: {auto_event.get('goal', '')[:160]} / 결과: {auto_event.get('result')}"
              f" / 회차 {len(auto_event.get('rounds', []))}" + (f"\n{after[:300]}" if after else ""))
    full, short = [], []
    for item in auto_event.get("rounds", []):
        line = f"- {item.get('round')}회차 종료 코드 {item.get('exit')}"
        brief = line + (" (수정안 거부)" if item.get("rejected") else " (수정 적용)" if item.get("diff") else "")
        if item.get("failure"):
            line += f"; 실패 끝: {item['failure'][-300:]}"
        if item.get("rejected"):
            line += f"; 수정안 거부: {item['rejected'][:200]}"
        if item.get("diff"):
            line += f"; 적용한 수정:\n{item['diff'][:700]}"
        full.append(line)
        short.append(brief)
    tail = ["- 덜어내기: " + "; ".join(auto_event["notes"])[:200]] if auto_event.get("notes") else []
    room = limit - len(header) - sum(len(x) + 1 for x in tail) - 1
    chosen: list[str] = []
    for index in range(len(full) - 1, -1, -1):  # 뒤에서부터: 넣을 수 있으면 자세히, 아니면 한 줄로
        text = full[index] if len(full[index]) + 1 <= room else short[index]
        if len(text) + 1 > room:
            break
        chosen.insert(0, text)
        room -= len(text) + 1
    return "\n".join([header, *chosen, *tail])[:limit]
