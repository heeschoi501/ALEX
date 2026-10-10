"""Project JARVIS Python practice.

정답을 일부러 구현하지 않은 연습 파일입니다.
STUDY_GUIDE.md를 읽고 하나씩 채워 보세요.
"""
from __future__ import annotations

from collections.abc import Iterable


def count_routes(sentences: Iterable[str]) -> dict[str, int]:
    """문제 1: 문장들을 route_request()로 분류하고 destination별 개수를 세세요.

    힌트:
    - `from jarvis.router import route_request`
    - dict.get(key, 0)
    - decision.destination.value
    """
    raise NotImplementedError("직접 구현해 보세요.")


def memory_pass(memory_count: int) -> str:
    """문제 2: memory_count가 3 이상이면 PASS, 아니면 FAIL을 반환하세요."""
    raise NotImplementedError("직접 구현해 보세요.")


def normalize_ratio(value: float, min_value: float, max_value: float) -> float:
    """문제 3: Min-Max normalization을 구현하세요.

    공식: (value - min) / (max - min)
    단, max == min이면 0.0을 반환하도록 안전 처리하세요.
    """
    raise NotImplementedError("직접 구현해 보세요.")


def parse_route_from_log(line: str) -> str | None:
    """문제 4: 로그 한 줄에서 `route=...` 값만 추출하세요.

    예:
    '... route=local_coder reason=...' -> 'local_coder'
    route=가 없으면 None.
    """
    raise NotImplementedError("직접 구현해 보세요.")


def is_valid_memory_key(key: str) -> bool:
    """문제 5: memory key 규칙을 직접 정해 구현하세요.

    기본 제안 규칙:
    - 길이 2~30
    - 영문/숫자/underscore만 허용
    - 첫 글자는 영문

    Regex를 써도 되고 문자열 메서드로 풀어도 됩니다.
    """
    raise NotImplementedError("직접 구현해 보세요.")
