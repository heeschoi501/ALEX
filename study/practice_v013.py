"""Project JARVIS v0.1.3 Python practice.

정답을 일부러 구현하지 않은 연습 파일입니다.
본체 코드를 복사하기보다 STUDY_GUIDE.md의 v0.1.3 단계를 읽고 직접 구현하세요.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


def choose_profile_model(profile: str, laptop_model: str, desktop_model: str) -> str:
    """문제 1 — Profile 분기.

    profile이 'DESKTOP'이면 desktop_model, 그 외에는 laptop_model을 반환하세요.
    힌트: 먼저 `.strip().upper()`로 입력을 정규화하세요.
    """
    raise NotImplementedError("직접 구현해 보세요.")


def is_inside_root(root: Path, candidate: Path) -> bool:
    """문제 2 — Path traversal 방어의 핵심.

    root와 candidate를 resolve한 뒤 candidate가 root 자체이거나 root 아래인지 판정하세요.
    힌트: `Path.parents`.
    """
    raise NotImplementedError("직접 구현해 보세요.")


def redact_simple_secret(text: str) -> str:
    """문제 3 — 아주 작은 redaction.

    `token=ABC123` 또는 `password=ABC123`처럼 공백 전까지 이어진 값을
    `token=[REDACTED]` 형태로 바꾸세요.

    실제 JARVIS 보안 함수는 더 다양한 패턴을 다룹니다. 여기서는 Regex 학습용입니다.
    """
    raise NotImplementedError("직접 구현해 보세요.")


def top_counts(values: Iterable[str], limit: int = 3) -> list[tuple[str, int]]:
    """문제 4 — Router feedback 통계.

    문자열 빈도를 세고 count 내림차순, 동률이면 이름 오름차순으로 상위 limit개를 반환하세요.
    힌트: `collections.Counter` 또는 dict.
    """
    raise NotImplementedError("직접 구현해 보세요.")


def bounded_tail(items: list[str], max_items: int, max_chars: int) -> list[str]:
    """문제 5 — 최근 Context budget.

    가장 최신 항목(리스트 뒤쪽)을 우선 보존하세요.
    반환 항목 수는 max_items 이하, 총 문자 수는 max_chars 이하가 되게 하세요.
    단, 단일 최신 항목 하나가 max_chars보다 긴 경우 뒤쪽 max_chars만 남겨도 됩니다.
    """
    raise NotImplementedError("직접 구현해 보세요.")


def should_use_cache(old_size: int, old_mtime_ns: int, new_size: int, new_mtime_ns: int) -> bool:
    """문제 6 — 파일 cache invalidation.

    크기와 mtime_ns가 모두 같을 때만 True를 반환하세요.
    """
    raise NotImplementedError("직접 구현해 보세요.")


def human_bytes(value: int) -> str:
    """문제 7 — 진단 출력.

    1024 미만은 B, 그 다음 KiB, MiB, GiB 순으로 읽기 쉽게 반환하세요.
    예: 1536 -> '1.5 KiB'. 음수는 ValueError.
    """
    raise NotImplementedError("직접 구현해 보세요.")


def unique_preserve_order(values: Iterable[str]) -> list[str]:
    """문제 8 — 모델 목록 중복 제거.

    첫 등장 순서를 유지하면서 중복만 제거하세요.
    `list(set(...))`는 순서 의도를 표현하지 못하므로 다른 방법을 써보세요.
    """
    raise NotImplementedError("직접 구현해 보세요.")
