"""이미지·영상 생성 — 이 공개판에서는 제외했다.

원래 ALEX 는 이 PC 의 ComfyUI 로 그림·영상을 만들었다. 공개판에는 그 구현(모델·워크플로·프롬프트 작성·내용 검사)을 넣지 않았다.
다른 모듈이 쓰는 이름만 남겨 나머지 기능(대화·코딩·검색·학습 등)이 그대로 동작하게 한다. 그림·영상 부탁에는 '꺼져 있다'고 답한다
(core._image_answer·_video_answer 는 생성기가 없으면 그렇게 답한다).
"""
from __future__ import annotations

import re
from pathlib import Path

MAX_REFERENCES = 0
MARKER = "alex-image:"
MARKER_LINE = re.compile(r"^!\[[^\]\n]{0,200}\]\(" + re.escape(MARKER) + r"(\d{8}-\d{6}-\d{1,20}\.(?:png|jpg|webp))\)$", re.M)
IMAGE_TYPES: dict[str, str] = {}  # 그림 올리기도 받지 않는다
MAX_UPLOAD_BYTES = 0
REMOVED = "이 공개판에는 이미지·영상 생성 기능이 없습니다."


class ImageGenError(RuntimeError):
    """그림·영상 엔진 오류(공개판에서는 항상 '기능 없음')."""


class ComfyImageGenerator:
    """공개판에는 없다. 만들려고 하면 ValueError — app.build_image_generator 가 '꺼짐'(None)으로 처리한다."""

    def __init__(self, *args, **kwargs) -> None:
        raise ValueError(REMOVED)


def blocked_reason(*texts: str) -> str | None:
    return None


def character_save_name(text: str) -> str | None:
    return None


def clean_prompt(reply: str) -> str:
    return reply.strip()


def edit_prompt_messages(text: str, references, previous: str | None = None) -> list:
    return []


def fetch_image_url(url: str) -> bytes:
    raise ImageGenError(REMOVED)


def has_hangul_text_request(text: str) -> bool:
    return False


def image_size(text: str) -> tuple[int, int]:
    return 1024, 1024


def image_subject(text: str) -> str:
    return text


def is_change_request(text: str) -> bool:
    return False


def is_followup(text: str) -> bool:
    return False


def looks_like_refusal(reply: str) -> bool:
    return False


def marker_line(caption: str, path: Path) -> str:
    return ""


def prompt_messages(text: str, previous: str | None = None) -> list:
    return []


def video_marker_line(caption: str, path: Path) -> str:
    return ""


def video_prompt_messages(text: str, start_prompt: str | None = None) -> list:
    return []


def wants_same_character(text: str) -> bool:
    return False
