"""Deterministic golden-corpus evaluation for the transparent rule router.

This is not a benchmark of language understanding. It prevents accidental regressions in the
small set of intents JARVIS v0.1.x promises to route automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.router import Destination, route_request


@dataclass(frozen=True)
class Case:
    text: str
    expected: Destination


CASES = (
    Case("안녕 오늘 뭐부터 할까?", Destination.LOCAL_GENERAL),
    Case("내일 계획을 같이 정리해줘", Destination.LOCAL_GENERAL),
    Case("이 문장을 자연스럽게 설명해줘", Destination.LOCAL_GENERAL),
    Case("Python 리스트 중복 제거 코드 알려줘", Destination.LOCAL_CODER),
    Case("파이썬 함수 하나 만들어줘", Destination.LOCAL_CODER),
    Case("Pandas groupby 예제 보여줘", Destination.LOCAL_CODER),
    Case("SQL join 쿼리 고쳐줘", Destination.LOCAL_CODER),
    Case("이 regex 왜 매칭이 안돼?", Destination.LOCAL_CODER),
    Case("traceback 보고 에러 원인 알려줘", Destination.LOCAL_CODER),
    Case("테스트 코드 하나 작성해줘", Destination.LOCAL_CODER),
    Case("3만 줄 Python 프로젝트 전체 구조를 분석해서 리팩터링해줘", Destination.CLOUD_CODER),
    Case("이 코드베이스 전체 구조를 리팩토링해줘", Destination.CLOUD_CODER),
    Case("저장소 아키텍처 설계 리뷰를 해줘", Destination.CLOUD_CODER),
    Case("프로젝트 전면 재설계하고 코드 구조도 바꿔줘", Destination.CLOUD_CODER),
    Case("이 수학 명제를 논리적으로 증명해줘", Destination.CLOUD_REASONING),
    Case("이 문제를 깊게 분석해서 결론을 내줘", Destination.CLOUD_REASONING),
    Case("논문 수준으로 정밀하게 분석해줘", Destination.CLOUD_REASONING),
    Case("현재 시스템 RAM과 GPU 알려줘", Destination.TOOL_SYSTEM_INFO),
    Case("내 컴퓨터 사양 알려줘", Destination.TOOL_SYSTEM_INFO),
    Case("VRAM 얼마나 잡혔어?", Destination.TOOL_SYSTEM_INFO),
    Case("지금 프로필 알려줘", Destination.TOOL_SYSTEM_INFO),
    Case("router 파일 찾아줘", Destination.TOOL_FILE_SEARCH),
    Case("config 파일 검색해줘", Destination.TOOL_FILE_SEARCH),
    Case("README 파일 어디 있어?", Destination.TOOL_FILE_SEARCH),
    Case("지난 세션 이어서 해", Destination.SESSION_RESUME),
    Case("어제 하던 거 계속해", Destination.SESSION_RESUME),
    Case("아까 하던 프로젝트 이어서 하자", Destination.SESSION_RESUME),
    Case("이어서 설명해줘", Destination.LOCAL_GENERAL),
    Case("계속해서 설명해줘", Destination.LOCAL_GENERAL),
    # 웹 (ADR-051) — 자연어 요청만 웹으로, 능력 질문·이 기계 안의 일은 웹으로 가지 않는다.
    Case("iverilog 최신 버전 검색해줘", Destination.TOOL_WEB_SEARCH),
    Case("iverilog 최신 버전 검색해줘 RTX 5080 VRAM 용량 좀 찾아봐 https://steveicarus.github.io/iverilog/ 여기서 최신 버전 알려줘", Destination.TOOL_WEB_READ),
    Case("RTX 5080 가격 좀 찾아봐", Destination.TOOL_WEB_SEARCH),
    Case("인터넷에서 sky130 PDK 문서 찾아줘", Destination.TOOL_WEB_SEARCH),
    Case("요즘 반도체 뉴스 알아봐줘", Destination.TOOL_WEB_SEARCH),
    Case("gpt-oss 120b 크기를 검색해줄 수 있어?", Destination.TOOL_WEB_SEARCH),
    Case("최신 pandas 버전 찾아줘", Destination.TOOL_WEB_SEARCH),
    Case("https://steveicarus.github.io/iverilog/ 이거 요약해줘", Destination.TOOL_WEB_READ),
    Case("http://127.0.0.1:8765 이거 왜 안 열려?", Destination.LOCAL_GENERAL),
    Case("이 코드에서 버그 찾아줘", Destination.LOCAL_CODER),
    Case("최근 에러 로그 찾아줘", Destination.LOCAL_CODER),
    Case("프로젝트에서 검색해줘 build_core", Destination.LOCAL_GENERAL),
    Case("인터넷에서 찾아줄 수 있어?", Destination.LOCAL_GENERAL),
    Case("인터넷 접속이 금지돼 있는 거야?", Destination.LOCAL_GENERAL),
    Case("검색 기능 설명해줘", Destination.LOCAL_GENERAL),
    Case("너 검색할 수 있어?", Destination.LOCAL_GENERAL),
    # 2026-09-25 실측에서 틀린 것 — 'vram' 한 단어로 이 기계 정보로 갔다.
    Case("RTX 5080 VRAM 용량 좀 찾아봐", Destination.TOOL_WEB_SEARCH),
    Case("내 VRAM 좀 찾아봐", Destination.TOOL_SYSTEM_INFO),
    Case("sky130 데이터시트 찾아줘", Destination.TOOL_WEB_SEARCH),
    Case("이 함수 이름 좋은 거 찾아줘", Destination.LOCAL_CODER),
    Case("아까 말한 거 찾아줘", Destination.LOCAL_GENERAL),
    Case("왜 안 되는지 알아봐줘", Destination.LOCAL_GENERAL),
    # 알아서 검색 (ADR-053) — 2026-09-25 데스크톱에서 사용자가 실제로 보낸 문장이 앞 세 줄이다.
    Case("26년 현재기준 rtx5090 가격은?", Destination.TOOL_WEB_SEARCH),
    Case("상하이 여행 호텔 추천해줘", Destination.TOOL_WEB_SEARCH),
    Case("그 명령어 없어도 너가 알아서 검색할 수있도록 설정해뒀는데?", Destination.LOCAL_GENERAL),
    Case("모델 변경됐는데, 지금 최신 학습은 언제가 마지막이야?", Destination.LOCAL_GENERAL),
    Case("요즘 반도체 동향 어때?", Destination.TOOL_WEB_SEARCH),
    Case("2025년 노벨 물리학상은 누가 받았어?", Destination.TOOL_WEB_SEARCH),
    Case("최신 pandas 버전은?", Destination.TOOL_WEB_SEARCH),
    # 겹치는 자리 — 같은 단어가 있어도 검색하면 안 되는 것.
    Case("가격 계산하는 함수 짜줘", Destination.LOCAL_CODER),
    Case("최신 코드 리뷰해줘", Destination.LOCAL_CODER),
    Case("변수 이름 추천해줘", Destination.LOCAL_GENERAL),
    Case("이 가격이면 괜찮아?", Destination.LOCAL_GENERAL),
    Case("26년 계획 세워줘", Destination.LOCAL_GENERAL),
    Case("좋은 소식이 있어", Destination.LOCAL_GENERAL),
    Case("최신 정보 찾아줄 수 있어?", Destination.LOCAL_GENERAL),
    # 그림 그리기(ADR-073, 2026-10-03 "자연어로 말하면 이미지 생성") — 부탁 말이 끝에 있어야 한다.
    Case("고양이 그려줘", Destination.TOOL_IMAGE_GEN),
    Case("바닷가에서 노을을 보는 고양이를 수채화 느낌으로 그려줘.", Destination.TOOL_IMAGE_GEN),
    Case("귀여운 강아지 이미지 만들어줘", Destination.TOOL_IMAGE_GEN),
    Case("폰 배경화면 만들어줘", Destination.TOOL_IMAGE_GEN),
    Case("그림 그려줄 수 있어?", Destination.TOOL_IMAGE_GEN),
    Case("draw a cat on the moon", Destination.TOOL_IMAGE_GEN),
    # 겹치는 자리 — 그림이라는 말이 있어도 그리면 안 되는 것.
    Case("그림 그릴 수 있어?", Destination.LOCAL_GENERAL),
    Case("이미지 생성 기능 있어?", Destination.LOCAL_GENERAL),
    Case("구조도 그려줘", Destination.LOCAL_GENERAL),
    Case("파이썬으로 그래프 그려줘", Destination.LOCAL_CODER),
    Case("이미지 생성 코드 짜줘", Destination.LOCAL_CODER),
    Case("이 이미지 설명해줘", Destination.LOCAL_GENERAL),
    Case("고양이 만들어줘", Destination.LOCAL_GENERAL),
    Case("how to draw a cat", Destination.LOCAL_GENERAL),
    # 영상(ADR-075)
    Case("고양이가 뛰어노는 영상 만들어줘", Destination.TOOL_VIDEO_GEN),
    Case("방금 그림 움직이게 해줘", Destination.TOOL_VIDEO_GEN),
    Case("이 영상 요약해줘", Destination.LOCAL_GENERAL),
    Case("영상 편집 코드 짜줘", Destination.LOCAL_CODER),
)


def evaluate() -> tuple[int, int, list[tuple[Case, Destination]]]:
    failures: list[tuple[Case, Destination]] = []
    for case in CASES:
        actual = route_request(case.text).destination
        if actual != case.expected:
            failures.append((case, actual))
    return len(CASES) - len(failures), len(CASES), failures


def main() -> int:
    passed, total, failures = evaluate()
    print(f"Router golden corpus: {passed}/{total} ({passed / total * 100:.1f}%)")
    for case, actual in failures:
        print(f"FAIL expected={case.expected.value} actual={actual.value} text={case.text!r}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
