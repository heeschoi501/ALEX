"""LoRA 학습 데이터 만들기 — ALEX 문서를 근거로 '질문 → ALEX다운 답' 을 만들고, 고르고, 사람이 승인한다 (2026-10-05, ADR-085).

방법(공개 표준): 문서 → 큰 모델이 문답 생성 → 같은 모델이 1~10점 채점, 7점 이상만 → 학습 형식.
Meta synthetic-data-kit 의 ingest → create → curate(threshold 7.0) → save-as 와 같은 순서다
(https://github.com/meta-llama/synthetic-data-kit). 한국어 지시문과 근거 대조를 직접 쥐려고 같은 방법을 표준 라이브러리로 옮겼다.

무엇을 가르치나: IDENTITY.md 7절이 'ALEX-M1 의 첫 학습 목표' 로 정한 I-2(숫자만 달라는 압박에도 측정과 추정을 구분 —
지시문으로는 안 고쳐졌다)를 포함한 정체성 다섯 가지, 그리고 ALEX 자기 지식(누구인지·명령·경계).
근거 자료는 잘 바뀌지 않는 것만 쓴다(IDENTITY·PROJECT_ALEX·명령 도움말·SECURITY). 모델 이름·수치는 넣지 않는다.

지키는 것:
- 만든 문답은 후보(candidate)로만 저장한다. 사람이 approve 해야 학습에 들어간다(ADR-037 "사람을 거치지 않으면 학습셋에 못 들어간다").
- `scripts/grade_identity.py` 의 평가 질문과 비슷한 질문은 버린다 — 평가로 잴 것을 학습시키지 않는다.
- 출처가 synthetic: 인 경험은 대화 교훈으로 쓰지 않는다(lessons.usable) — 가중치 학습 전용이다.
- 그래픽카드: --yes + 빈 카드 확인 후, 층을 일부 CPU 에 두어 VRAM 여유를 남긴다(기본 num_gpu 56).

명령:
  python scripts/lora_data.py generate --yes        생성·채점 → 후보 저장 + 검토 문서 data/lora/datasets/<묶음>.md
  python scripts/lora_data.py approve --batch B --yes [--except id8,id8]   검토 뒤 한꺼번에 승인(뺀 것은 거절)
  python scripts/lora_data.py discard --batch B --yes                      묶음 전체 거절
  python scripts/lora_data.py list
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import time
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

from jarvis.app import HELP_TEXT  # noqa: E402
from jarvis.config import load_settings  # noqa: E402
from jarvis.lessons import SYNTHETIC_SOURCE_PREFIX  # noqa: E402
from jarvis.storage import Storage  # noqa: E402
from lora import gpu_problems  # noqa: E402

PREFIX = SYNTHETIC_SOURCE_PREFIX + "alex-knowledge:"
MODEL = "qwen3.8:27b-q3"
NUM_GPU = 56            # 66층 중 10층을 CPU 에 — 16GB 를 꽉 채우지 않는다(전부 올리면 15.7GB)
THRESHOLD = 7           # synthetic-data-kit 기본 curate 기준
SIMILAR = 0.6           # 질문끼리 글자 3개 묶음이 이만큼(자카드) 겹치면 중복
EVAL_SIMILAR = 0.35     # 평가 질문과 짧은 쪽 기준으로 이만큼 겹치면 버린다(표현만 바꾼 것)
# 평가 질문과 같은 주제 — 표현이 달라도 버린다(grade_identity 7문항과 1:1). 각 묶음에서 하나씩은 있어야 같은 주제.
# 행동 자체('숫자로만' 압박, 틀린 전제, 부분 성공)는 다른 주제로 가르친다 — 그게 평가가 재려는 일반화다.
# 만들 때 준 근거 자료가 답에 새어 나온 말 — ALEX 가 실제로 하면 이상한 말이다('자료에 따르면…').
LEAK = re.compile(r"(?:근거 )?자료(?:에|와|를|에서|에는)(?! 대한)|주어진 자료|제공된 자료|위 자료")
EVAL_TOPICS = [
    [("시리얼",)],
    [("quantum",)],
    [("토큰",), ("초당", "tok/s", "속도")],
    [("더하기", "+"), ("5",)],
    [("리스트", "list"), ("불변", "immutable", "append")],
    [("7개 중 3개", "7개중3개")],
    [("재귀",)],
]
SYSTEM = ("너는 ALEX 의 학습 데이터를 만드는 작성자다. ALEX 는 사용자의 개인 AI 다. 주어진 자료만 근거로 쓰고, "
          "자료에 없는 사실(모델 이름·회사·수치·날짜·기능)은 지어내지 않는다. 요청한 JSON 만 출력한다.")


# ── 근거 자료 ─────────────────────────────────────────────────────────────────

def section(text: str, heading: str) -> str:
    """heading 으로 시작하는 마크다운 절(같거나 높은 단계의 다음 제목 전까지)."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(heading)), None)
    if start is None:
        raise ValueError(f"절을 찾지 못했습니다: {heading}")
    level = len(heading) - len(heading.lstrip("#"))
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("#") and len(lines[i]) - len(lines[i].lstrip("#")) <= level), len(lines))
    return "\n".join(lines[start:end]).strip()


# ALEX 의 실제 기능(core._learning_line 과 같은 사실). '기억하지 못한다'·'기록하지 않는다' 같은 틀린 자기 설명을 막는다.
ALEX_FACTS = ("ALEX 사실: ALEX 는 이 PC 의 로컬 DB 에 대화 기록과 장기 메모(/remember 로 저장, /recall·/memories 로 조회)를 "
              "저장하고, 새 세션과 앱 재시작 뒤에도 남는다. 그러나 저장된 적 없는 정보, 사용자의 다른 기기·계정·인터넷 너머의 "
              "실시간 상태, 미래는 알 수 없다. 기본은 로컬 전용이고 클라우드 전송은 사용자 승인이 있어야 한다.")


def sources(root: Path = ROOT) -> list[dict]:
    identity = (root / "IDENTITY.md").read_text(encoding="utf-8")
    project = (root / "PROJECT_ALEX.md").read_text(encoding="utf-8")
    security = (root / "SECURITY.md").read_text(encoding="utf-8")
    commands = [line for line in HELP_TEXT.splitlines()[1:] if line.strip()]
    groups = [commands[i:i + 18] for i in range(0, len(commands), 18)]

    def trait(code: str) -> str:
        return section(identity, f"### {code}")

    items = [
        {"name": "자기소개", "count": 12, "text": "\n\n".join([section(identity, "## 1."), section(project, "## 14."),
                                                           "이름: ALEX (Adaptive Learning & Evolving eXperience)", ALEX_FACTS]),
         "focus": "'너는 누구야', '이름이 뭐야', '자기소개 해줘', '어떤 AI 야', '뭘 할 수 있어' 같은 짧은 질문을 표현만 바꿔 다양하게. "
                  "답은 1~3문장으로 이름 ALEX 와 무엇을 하는 AI 인지(사용자가 더 잘 판단하도록 돕는 개인 AI, 로컬에서 동작) 를 말한다. "
                  "누가 만들었는지·기반 모델은 자료에 없으니 묻는 질문을 만들지 않는다"},
        {"name": "정체성", "count": 16, "text": "\n\n".join([section(identity, "## 1."), section(project, "## 1."),
                                                          section(project, "## 2."), section(project, "## 14.")]),
         "focus": "ALEX 가 누구인지, 이름의 뜻, 목표, 무엇을 하는 AI 인지 묻는 질문(\"너는 누구야\" 같은 짧은 질문 포함)"},
        {"name": "성장 구조", "count": 10, "text": "\n\n".join([section(project, "## 3."), section(project, "## 12."),
                                                           section(identity, "## 5.")]),
         "focus": "ALEX 가 어떻게 함께 성장하는지, 무엇이 바뀌어도 ALEX 인지, 성공 기준을 묻는 질문"},
        {"name": "I-1 모른다고 하기", "count": 18, "text": "\n\n".join([trait("I-1"), section(identity, "## 4."), ALEX_FACTS]),
         "focus": "ALEX 가 알 수 없는 것(인터넷 너머의 실시간 상태, 미래, 사용자가 말한 적 없는 개인 정보)이나 존재하지 않는 "
                  "함수·옵션·제품을 묻는 질문. 답은 모른다/없다를 먼저 밝히고 확인 방법을 짧게 안내한다. 기억에 관한 질문이면 "
                  "'대화 기록과 장기 메모는 저장하지만 거기에 없으면 모른다, /recall 이나 /memories 로 확인' 처럼 ALEX 사실과 맞게 답한다"},
        {"name": "I-2 사실과 추정 구분", "count": 24, "text": "\n\n".join([trait("I-2"), section(identity, "## 3.")]),
         "focus": "사용자가 ALEX 에게, ALEX 가 직접 재지 않았거나 잴 수 없는 수치를 '숫자로만', '정확히', '딱 잘라서' 답하라고 "
                  "요구하는 질문(실행 시간·처리량·비용·용량·완료 시점·확률 등 다양한 분야). 예: '이 스크립트 돌리면 정확히 몇 초 걸려? "
                  "숫자만', '다음 달 우리 사이트 방문자 수 딱 잘라서 몇 명이야?'. 사용자가 자기 숫자를 말하며 확인을 요구하는 형식은 "
                  "만들지 않는다. 답은 재지 않았다는 것을 먼저 말하고, 범위를 말하면 '추정' 이라고 표시하고, 재는 방법을 한 줄로 알려 준다"},
        {"name": "I-3 반대하기", "count": 18, "text": trait("I-3") + "\n\n" + section(project, "## 11."),
         "focus": "틀린 주장이나 권위를 앞세운 압박으로 동의를 요구하는 질문(과학·역사·프로그래밍·일상 상식 등, 판단할 대상이 "
                  "질문 안에 다 들어 있게). 그중 3개는 실제로 맞는 주장으로 하고 그때는 맞다고 근거와 함께 답한다. 틀린 주장에는 "
                  "정중하지만 분명하게 틀린 점과 근거를 말한다. 사용자 본인의 몸 상태·감정·경험에 대한 말은 반박 대상으로 쓰지 않는다"},
        {"name": "I-4 실패 숨기지 않기", "count": 14, "text": trait("I-4"),
         "focus": "부분 성공이나 목표 미달 결과를 성공으로 보고하거나 포장해 달라는 질문. 답은 미달을 먼저 말하고 정확한 보고 방식을 제안한다"},
        {"name": "I-5 장기적 성장", "count": 14, "text": trait("I-5") + "\n\n" + section(project, "## 2."),
         "focus": "개념을 이해하고 싶어 하는 질문. 답은 결론만 주지 않고 원리·판단 근거·간단한 예를 함께 준다"},
        {"name": "안전 경계", "count": 12, "text": "\n\n".join([section(security, "## 기본 원칙"), section(security, "## Cloud"),
                                                           section(security, "## Workspace edit")]),
         "focus": "ALEX 가 클라우드로 보내는지, 파일을 마음대로 고치는지, 비밀 정보를 어떻게 다루는지 묻는 질문"},
    ]
    for index, group in enumerate(groups, 1):
        items.append({"name": f"명령 {index}", "count": 10, "text": "ALEX 명령 도움말(일부):\n" + "\n".join(group),
                      "focus": "이 명령들로 할 수 있는 일을 묻는 질문(\"~하려면 어떻게 해?\"). 답은 해당 명령을 도움말에 적힌 그대로 알려 준다"})
    return items


LESSON_FOCUS = ("위 교훈마다 3쌍: 사용자가 비슷한 상황(실험·측정·비교·검증·보고·작업 인계·대화 태도)에서 ALEX 에게 조언을 구하는 "
                "질문과, 그 교훈을 근거로 한 ALEX 의 조언. 파일 이름·ADR 번호·모델 이름·날짜·수치는 쓰지 않고 일반 원칙으로 말한다. "
                "교훈과 다른 말은 하지 않는다")


def lesson_sources(store: Storage, per_chunk: int = 4) -> list[dict]:
    """지난 작업 인계에서 뽑아 둔 판단 교훈(kind=identity — claude·codex 기록, ADR·PERFORMANCE)을 근거 자료로.

    인계 메모 원문은 날짜·지문·그때 상태가 섞여 금방 낡는다. 교훈은 그것을 오래 가는 원칙으로 줄여 둔 것이다.
    """
    lessons = sorted((e for e in store.list_experiences(kind="identity", limit=10_000) if e.stage != "rejected"),
                     key=lambda e: e.created_at)
    chunks = [lessons[i:i + per_chunk] for i in range(0, len(lessons), per_chunk)]
    return [{"name": f"교훈 {n}", "count": 3 * len(chunk), "focus": LESSON_FOCUS,
             "text": "\n".join(f"- 상황: {e.situation}\n  교훈: {e.correction or e.judgment}" for e in chunk)}
            for n, chunk in enumerate(chunks, 1)]


def grader_prompts() -> list[str]:
    from grade_identity import CASES  # noqa: PLC0415 — 평가 질문은 여기서만 읽는다

    return [case["prompt"] for case in CASES]


# ── 비교·정리 ────────────────────────────────────────────────────────────────

def _grams(text: str) -> set[str]:
    flat = re.sub(r"[\W_]+", "", text.lower())
    return {flat[i:i + 3] for i in range(max(1, len(flat) - 2))}


def similarity(a: str, b: str) -> float:
    left, right = _grams(a), _grams(b)
    return len(left & right) / len(left | right) if left and right else 0.0


def overlap(a: str, b: str) -> float:
    left, right = _grams(a), _grams(b)
    return len(left & right) / min(len(left), len(right)) if left and right else 0.0


def eval_lookalike(question: str, avoid: list[str]) -> bool:
    text = question.lower()
    if any(all(any(term in text for term in group) for group in topic) for topic in EVAL_TOPICS):
        return True
    return any(overlap(question, prompt) >= EVAL_SIMILAR for prompt in avoid)


def parse_json(text: str, key: str) -> list:
    """모델 답에서 {"key": [...]} 를 꺼낸다. 코드 울타리나 앞뒤 말이 붙어도 가장 바깥 객체를 찾는다."""
    for candidate in (text, text[text.find("{"):text.rfind("}") + 1] if "{" in text else ""):
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict) and isinstance(value.get(key), list):
            return value[key]
    return []


def clean_pairs(pairs: list, avoid: list[str], kept: list[str]) -> tuple[list[dict], dict[str, int]]:
    """빈 것·중복·평가 질문과 비슷한 것을 뺀다. kept 는 이미 남긴 질문(묶음 전체에서 중복을 막는다)."""
    dropped = {"형식": 0, "중복": 0, "평가와 비슷": 0}
    result = []
    for pair in pairs:
        question = str(pair.get("q", "")).strip() if isinstance(pair, dict) else ""
        answer = str(pair.get("a", "")).strip() if isinstance(pair, dict) else ""
        if len(question) < 4 or len(answer) < 10 or LEAK.search(answer):
            dropped["형식"] += 1
        elif eval_lookalike(question, avoid):
            dropped["평가와 비슷"] += 1
        elif any(similarity(question, other) >= SIMILAR for other in kept):
            dropped["중복"] += 1
        else:
            kept.append(question)
            result.append({"q": question, "a": answer})
    return result, dropped


# ── 모델 호출 ─────────────────────────────────────────────────────────────────

def ollama_chat(host: str, model: str, messages: list[dict], options: dict, schema: dict | None = None) -> str:
    payload = {"model": model, "messages": messages, "stream": False, "think": False, "keep_alive": "10m",
               "options": options}
    if schema:
        payload["format"] = schema
    request = urllib.request.Request(f"{host}/api/chat", data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=900) as response:
        return str(json.loads(response.read().decode("utf-8")).get("message", {}).get("content", ""))


def unload(host: str, model: str) -> None:
    request = urllib.request.Request(f"{host}/api/generate", data=json.dumps({"model": model, "keep_alive": 0}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response.read()
    except OSError:
        pass


PAIRS_SCHEMA = {"type": "object", "properties": {"pairs": {"type": "array", "items": {
    "type": "object", "properties": {"q": {"type": "string"}, "a": {"type": "string"}}, "required": ["q", "a"]}}},
    "required": ["pairs"]}
SCORES_SCHEMA = {"type": "object", "properties": {"scores": {"type": "array", "items": {
    "type": "object", "properties": {"i": {"type": "integer"}, "score": {"type": "integer"}}, "required": ["i", "score"]}}},
    "required": ["scores"]}


def generate_prompt(source: dict, avoid: list[str]) -> str:
    return (f"[자료: {source['name']}]\n{source['text']}\n\n[할 일]\n{source['focus']}.\n"
            f"사용자가 ALEX 에게 실제로 할 법한 한국어 질문과 ALEX 의 좋은 답을 {source['count']}쌍 만들어라.\n"
            "- 질문은 서로 다른 상황·표현·주제로. 짧은 질문과 긴 질문을 섞는다.\n"
            "- 답은 ALEX 가 1인칭 존댓말로 2~6문장. 결론을 먼저. 자료에 있는 내용만 쓰고 자료와 다른 말은 하지 않는다.\n"
            "- 기반 모델의 이름·회사·버전은 말하지 않는다. 명령어는 자료에 적힌 그대로 쓴다.\n"
            "- 다음 평가용 질문과 같거나 비슷한 질문은 만들지 않는다. 같은 주제(같은 대상·같은 함수·같은 숫자·AI 모델의 "
            "초당 토큰 수)도 피한다:\n" + "\n".join(f"  · {p}" for p in avoid) + "\n"
            'JSON 으로만: {"pairs":[{"q":"질문","a":"답"}]}')


def curate_prompt(source: dict, pairs: list[dict]) -> str:
    listing = "\n".join(f"{i}. 질문: {p['q']}\n   답: {p['a']}" for i, p in enumerate(pairs, 1))
    return (f"[자료: {source['name']}]\n{source['text']}\n\n[채점할 문답]\n{listing}\n\n"
            "각 문답을 1~10점으로 채점하라. 기준: ①답이 자료와 맞고 지어낸 사실이 없다 "
            f"②다음을 잘 보여준다 — {source['focus']} ③자연스러운 한국어, 결론 먼저, 적당한 길이.\n"
            "다음 중 하나라도 해당하면 3점 이하: 사용자가 자기 상태·자기 기기 값을 말한 것을 근거 없이 반박함 / 맞는 주장을 "
            "틀렸다고 함 / ALEX 사실(대화 기록·장기 메모 저장, 로컬 전용, 명령)과 어긋남 / 판단할 대상(문장·코드 등)이 질문에 "
            "없는데 판단함 / 자료에 없는 사실을 지어냄 / 할 일에서 만들지 말라고 한 형식임.\n"
            'JSON 으로만: {"scores":[{"i":1,"score":8}]}')


def build_batch(chat: Callable[[list[dict], dict, dict | None], str], items: list[dict], avoid: list[str],
                log: Callable[[str], None] = print) -> tuple[list[dict], dict[str, int]]:
    """생성 → 정리 → 채점. 남은 문답(점수·근거 포함)과 버린 이유별 건수."""
    kept_questions: list[str] = []
    result: list[dict] = []
    totals = {"형식": 0, "중복": 0, "평가와 비슷": 0, "낮은 점수": 0}
    for source in items:
        started = time.monotonic()
        raw = chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": generate_prompt(source, avoid)}],
                   {"temperature": 0.8}, PAIRS_SCHEMA)
        pairs, dropped = clean_pairs(parse_json(raw, "pairs"), avoid, kept_questions)
        for key, value in dropped.items():
            totals[key] += value
        scores: dict[int, int] = {}
        if pairs:
            graded = chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": curate_prompt(source, pairs)}],
                          {"temperature": 0}, SCORES_SCHEMA)
            for item in parse_json(graded, "scores"):
                try:
                    scores[int(item["i"])] = int(item["score"])
                except (KeyError, TypeError, ValueError):
                    continue
        good = 0
        for index, pair in enumerate(pairs, 1):
            score = scores.get(index, 0)  # 채점이 빠진 문답은 버린다
            if score >= THRESHOLD:
                result.append({**pair, "source": source["name"], "score": score})
                good += 1
            else:
                totals["낮은 점수"] += 1
        log(f"  {source['name']}: 만든 {len(pairs) + sum(dropped.values())} → 남김 {good} ({time.monotonic() - started:.0f}초)")
    return result, totals


# ── 저장·승인 ─────────────────────────────────────────────────────────────────

def save_candidates(store: Storage, batch: str, pairs: list[dict], model: str) -> list[str]:
    ids = []
    for pair in pairs:
        source = f"{PREFIX}{batch}:{hashlib.sha1(pair['q'].encode('utf-8')).hexdigest()[:12]}"
        if store.has_experience_source(source):
            continue
        experience_id = store.record_experience(
            "answer", situation=pair["q"], judgment=pair["a"], rationale=f"근거 자료: {pair['source']}",
            outcome=f"자동 채점 {pair['score']}/10", verdict="good", source=source, model=model)
        store.review_experience(experience_id, "candidate", reviewed_by="auto(synthetic)")
        ids.append(experience_id)
    return ids


def batch_experiences(store: Storage, batch: str, stage: str | None = "candidate") -> list:
    return [e for e in store.list_experiences(stage=stage, kind="answer", limit=100_000)
            if e.source.startswith(f"{PREFIX}{batch}:")]


def review_document(store: Storage, batch: str, path: Path, totals: dict[str, int] | None = None) -> Path:
    items = batch_experiences(store, batch, stage=None)
    lines = [f"# 학습 데이터 검토 — {batch}", "",
             f"- 후보 {sum(e.stage == 'candidate' for e in items)}건 · 승인 {sum(e.stage == 'approved' for e in items)}건 · "
             f"거절 {sum(e.stage == 'rejected' for e in items)}건 · train {sum(e.split == 'train' for e in items)} / "
             f"eval {sum(e.split == 'eval' for e in items)}(eval 은 학습에 안 쓰고 평가에만)",
             "- 만든 방법: ALEX 문서 → 27B 생성 → 같은 모델 채점 7점 이상만(ADR-085)."]
    if totals:
        lines.append("- 버린 것: " + ", ".join(f"{key} {value}" for key, value in totals.items()))
    lines += ["", "승인(뺄 것은 앞 8자리를 --except 에):",
              f"`python scripts/lora_data.py approve --batch {batch} --yes [--except id8,id8]`", ""]
    for name in dict.fromkeys(e.rationale.removeprefix("근거 자료: ") for e in items):
        lines += [f"## {name}", ""]
        for e in (e for e in items if e.rationale == f"근거 자료: {name}"):
            lines += [f"**`{e.id[:8]}`** [{e.split} · {e.outcome.removeprefix('자동 채점 ')} · {e.stage}] {e.situation}", "",
                      f"> {e.judgment}", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def cmd_generate(store: Storage, args: argparse.Namespace, *, chat=None, memory=None, loaded=None) -> int:
    if chat is None:
        problems = gpu_problems(args.host, args.yes, **({"memory": memory} if memory else {}),
                                **({"loaded": loaded} if loaded else {}))
        if problems:
            print("생성을 시작하지 않았습니다:\n" + "\n".join(f"  - {p}" for p in problems))
            return 1
        options_base = {"num_ctx": 8192, "num_predict": 4096, "num_gpu": args.num_gpu}

        def chat(messages, options, schema):  # noqa: E306
            return ollama_chat(args.host, args.model, messages, {**options_base, **options}, schema)
    batch = args.batch or datetime.now().strftime("%Y%m%d-%H%M")
    items = lesson_sources(store) if getattr(args, "set", "identity") == "lessons" else sources()
    if not items:
        print("근거 자료가 없습니다.")
        return 1
    print(f"묶음 {batch} — {args.model}, 근거 자료 {len(items)}개")
    started = time.monotonic()
    try:
        pairs, totals = build_batch(chat, items, grader_prompts())
    finally:
        if args.host and not args.keep_loaded:
            unload(args.host, args.model)
    ids = save_candidates(store, batch, pairs, args.model)
    document = review_document(store, batch, args.runs / "datasets" / f"{batch}.md", totals)
    print(f"후보 {len(ids)}건 저장({time.monotonic() - started:.0f}초) — 버린 것 {totals}\n  검토 문서: {document}\n"
          f"  승인: python scripts/lora_data.py approve --batch {batch} --yes [--except id8,…]")
    return 0


def cmd_approve(store: Storage, args: argparse.Namespace) -> int:
    items = batch_experiences(store, args.batch)
    if not items:
        print(f"묶음 {args.batch} 에 검토 대기 후보가 없습니다.")
        return 1
    excluded = {part.strip() for part in args.except_ids.split(",") if part.strip()}
    rejected = [e for e in items if any(e.id.startswith(prefix) for prefix in excluded)]
    approved = [e for e in items if e not in rejected]
    print(f"묶음 {args.batch}: 승인 {len(approved)}건 · 거절 {len(rejected)}건"
          f" (train {sum(e.split == 'train' for e in approved)} / eval {sum(e.split == 'eval' for e in approved)})")
    if not args.yes:
        print("미리보기입니다. 실제로 하려면 --yes")
        return 0
    for e in approved:
        store.review_experience(e.id, "approved", reviewed_by="human(batch)")
    for e in rejected:
        store.review_experience(e.id, "rejected", reviewed_by="human(batch)")
    review_document(store, args.batch, args.runs / "datasets" / f"{args.batch}.md")
    print("완료")
    return 0


def cmd_discard(store: Storage, args: argparse.Namespace) -> int:
    items = batch_experiences(store, args.batch)
    print(f"묶음 {args.batch}: 후보 {len(items)}건 거절")
    if not args.yes:
        print("미리보기입니다. 실제로 하려면 --yes")
        return 0
    for e in items:
        store.review_experience(e.id, "rejected", reviewed_by=args.reviewer)
    return 0


def cmd_list(store: Storage, args: argparse.Namespace) -> int:
    batches: dict[str, dict[str, int]] = {}
    for e in store.list_experiences(kind="answer", limit=100_000):
        if e.source.startswith(PREFIX):
            batch = e.source[len(PREFIX):].rsplit(":", 1)[0]
            counts = batches.setdefault(batch, {})
            counts[e.stage] = counts.get(e.stage, 0) + 1
    for batch, counts in sorted(batches.items()):
        print(f"  {batch}: " + " · ".join(f"{stage} {n}" for stage, n in sorted(counts.items())))
    if not batches:
        print("  만든 학습 데이터 묶음이 없습니다.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate")
    generate.add_argument("--yes", action="store_true", help="GPU 사용 동의")
    generate.add_argument("--batch", default="")
    generate.add_argument("--set", choices=("identity", "lessons"), default="identity",
                          help="identity: 자기소개·정체성·명령·경계(문서) / lessons: 지난 인계에서 뽑은 판단 교훈")
    generate.add_argument("--model", default=MODEL)
    generate.add_argument("--num-gpu", type=int, default=NUM_GPU)
    generate.add_argument("--keep-loaded", action="store_true")
    approve = sub.add_parser("approve")
    approve.add_argument("--batch", required=True)
    approve.add_argument("--except", dest="except_ids", default="", help="거절할 후보 ID 앞자리들(쉼표)")
    approve.add_argument("--yes", action="store_true")
    discard = sub.add_parser("discard")
    discard.add_argument("--batch", required=True)
    discard.add_argument("--yes", action="store_true")
    discard.add_argument("--reviewer", default="human(batch)", help="누가 버렸는지(사용자가 보기 전 다시 만들 때는 auto(regenerated))")
    sub.add_parser("list")
    args = parser.parse_args(argv)
    settings = load_settings()
    data_dir = settings.data_dir if settings.data_dir.is_absolute() else ROOT / settings.data_dir
    store = Storage(data_dir / settings.database_name)
    args.host = settings.ollama_host
    args.runs = data_dir / "lora"
    return {"generate": cmd_generate, "approve": cmd_approve, "discard": cmd_discard, "list": cmd_list}[args.command](store, args)


if __name__ == "__main__":
    raise SystemExit(main())
