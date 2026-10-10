"""STUDY_GUIDE 문제를 푸는 과정을 경험으로 남긴다 (ADR-037).

바탕화면의 학습 팩(`scripts/build_study_pack.py` 가 만든다)에서 문제를 풀면, 매 시도가
여기를 거쳐 경험 저장소에 들어간다. 이 경로가 값어치 있는 이유는 **판정이 모델 바깥에서
오기 때문**이다 — 파이썬이 실제로 실행해서 PASS/FAIL 을 정한다. 모델이 자기 답을
채점하면 자기참조 루프이고 학습 신호가 아니다.

**틀린 시도를 지우지 않는다.** 처음 무엇을 틀렸고 어떻게 고쳤는지가 교재이고, 정답만
모으면 가르칠 것이 없다. 본문 5필드는 불변이므로 시도마다 별개 경험으로 쌓인다.

기록을 끄려면 `JARVIS_STUDY_RECORD=0`.
"""
from __future__ import annotations

import os
from pathlib import Path

from .config import load_settings
from .storage import Storage, StorageError

MAX_CODE_CHARS = 4000


def _default_db() -> Path:
    """앱의 DB. **실행 위치가 아니라 이 패키지 위치를 기준으로** 찾는다.

    학습 팩은 바탕화면에서 돌아간다. 설정의 data_dir 이 상대 경로라 cwd 기준으로 풀리면
    바탕화면에 빈 DB 가 새로 생기고 기록이 거기로 사라진다 — 실제로 그렇게 됐다.
    """
    configured = load_settings().database_path
    if configured.is_absolute():
        return configured
    return Path(__file__).resolve().parent.parent / configured


def recording_enabled() -> bool:
    return os.getenv("JARVIS_STUDY_RECORD", "1").strip().lower() not in {"0", "false", "no"}


def _same_as_last(store: Storage, source: str, judgment: str) -> bool:
    """같은 코드를 고치지 않고 다시 돌린 경우는 새 시도가 아니다."""
    for item in store.list_experiences(kind="codegen", limit=200):
        if item.source == source:
            return item.judgment.strip() == judgment.strip()
    return False


def record_attempt(*, number: int, title: str, statement: str, code: str,
                   passed: bool, message: str, db_path: Path | None = None) -> str | None:
    """한 번의 풀이 시도를 경험으로 남긴다. 기록하지 않았으면 None.

    `code` 는 학습자가 실제로 쓴 본문이다. 문제 설명이 아니라 **판단**이 judgment 다.
    """
    if not recording_enabled():
        return None
    body = code.strip()
    if not body:
        return None
    source = f"study:{number:02d}"
    try:
        store = Storage(db_path or _default_db())
        if _same_as_last(store, source, body):
            return None
        return store.record_experience(
            "codegen",
            situation=f"학습 문제 {number} — {title}\n{statement.strip()[:1200]}",
            judgment=body[:MAX_CODE_CHARS],
            rationale="study_pack",
            outcome=("통과" if passed else "실패") + (f": {message}" if message else ""),
            verdict="good" if passed else "bad",
            correction="" if passed else message,
            source=source,
        )
    except StorageError:
        # 기록에 실패해도 공부를 막지 않는다.
        return None
