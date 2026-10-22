"""대화별 권한 3단계(ADR-095, 2026-10-18 사용자 "권한은 클라우드 LLM 처럼 선택해서 쓰게").

- 읽기 전용(read): 답변·파일 읽기·검색만. 프로젝트 파일 적용·되돌리기, 코드·검사 명령 실행, 기기 조작, 그림·영상 생성을 하지 않는다.
- 확인 후 실행(ask, 기본): 지금까지와 같다 — 수정안은 사람이 diff 를 보고 적용한다.
- 자동 실행(auto, 관리자만, 그 대화에만): 수정안을 바로 적용한다(적용 전 원본 백업, 되돌리기 명령을 답에 붙임).

어느 단계에서도 바뀌지 않는 것: 설치·삭제·장시간 GPU·게시는 따로 승인, 비밀번호·결제는 다루지 않는다.
그리고 ALEX 자기 프로그램(이 폴더의 jarvis/ 와 설정·실행 파일)은 자동 실행에서도 바로 적용하지 않는다 —
권한 규칙을 ALEX 가 스스로 넓히지 못하게(사용자와 합의한 '금지' 항목). 사람이 확인하면 적용할 수 있다.
"""
from __future__ import annotations

from pathlib import Path

PERMISSION_MODES = ("read", "ask", "auto")
DEFAULT_PERMISSION = "ask"
PERMISSION_LABELS = {"read": "읽기 전용", "ask": "확인 후 실행", "auto": "자동 실행"}
READ_ONLY_BLOCKED_COMMANDS = frozenset({
    "/apply-edit", "/undo-file", "/undo-bundle", "/build", "/rtl", "/dev-next", "/dev-auto", "/dev-propose",
    "/dev-verify", "/dev-check", "/wake-desktop", "/open-vscode", "/image", "/image-prompt", "/video-prompt",
    "/state-import", "/video-swap", "/video-long", "/archive-year", "/cloud", "/cloud-default", "/eda", "/rtl-tests", "/download",
})
READ_ONLY_BLOCKED = ("지금 이 대화는 '읽기 전용' 권한이라 파일을 바꾸거나 코드를 실행하거나 그림을 만들지 않습니다. "
                     "입력창 아래 권한을 '확인 후 실행' 이나 '자동 실행' 으로 바꾼 뒤 다시 보내 주세요.")
ALEX_ROOT = Path(__file__).resolve().parent.parent


def clean_permission(value: object) -> str:
    if value not in PERMISSION_MODES:
        raise ValueError("권한은 read(읽기 전용)·ask(확인 후 실행)·auto(자동 실행) 중 하나여야 합니다.")
    return str(value)


def is_alex_program(path: Path, root: Path = ALEX_ROOT) -> bool:
    """ALEX 자기 프로그램 파일인가: jarvis/ 아래 전부, 그리고 ALEX 폴더 바로 아래 파일(설정 jarvis.local.json·실행 파일)."""
    target, root = Path(path).resolve(), root.resolve()
    package = root / "jarvis"
    return target == package or package in target.parents or target.parent == root
