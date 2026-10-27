"""Terminal UI for Project ALEX v0.2.0.

Study focus:
- while 루프와 문자열 명령 처리
- 설정 -> 객체 조립(build) -> 실행 의 흐름
- callback 으로 Core 와 사용자 입력(UI)을 분리하는 방법
"""
from __future__ import annotations

import logging
import json
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import SimpleNamespace

from .config import ConfigError, Settings, load_settings, local_config_path
from .core import MEMBER_BLOCKED, DOWNLOAD_REASON, CloudApprovalRequest, JarvisCore
from .device import DeviceError, VSCodeLauncher, WakeOnLanTool
from .embeddings import EmbeddingClient, MemoryVectors
from .permissions import is_alex_program
from .videolink import Transcriber, VideoReader
from .router import Destination, RouteDecision, explain, route_request
from .hardware import HardwareInfo, PerformanceProfile, detect_hardware
from .lessons import LESSON_KINDS, usable as lesson_usable
from .portable import PortableStateError, export_portable_state, import_portable_state
from .providers import (
    AnthropicMessagesProvider,
    BaseLLMProvider,
    CloudStubProvider,
    FailingProvider,
    GeminiGenerateContentProvider,
    MockLLMProvider,
    OllamaProvider,
    StrataProvider,
    OpenAIResponsesProvider,
)
from .storage import OWNER, Storage, StorageError
from .tools import SafeFileReadTool, SafeFileSearchTool, SafeProjectContextTool, SafeProjectTreeTool, ToolError
from .webread import WebReader
from .workspace import SafeWorkspaceEditor
from .developer import DeveloperMode
from .opencode_worker import OpenCodeWorker
from .cloudagent import CloudAgents
from .imagegen import ComfyImageGenerator
from .documents import DocumentError, command_export
from .knowledge import KnowledgeError

# Hardware Profile 에 따라 context 길이를 자동으로 확장한다.
NUM_CTX_BY_PROFILE = {
    PerformanceProfile.LAPTOP: 4096,
    PerformanceProfile.DESKTOP: 16384,
    PerformanceProfile.UNKNOWN: 2048,
}

# tokenizer 의존성 없이 context overflow를 줄이기 위한 보수적 2차 제한.
# 한국어/코드의 token 비율이 일정하지 않으므로 실제 token 수 보장은 아니며 안전 margin이다.
CONTEXT_CHAR_BUDGET_BY_PROFILE = {
    PerformanceProfile.LAPTOP: 6_000,
    PerformanceProfile.DESKTOP: 24_000,
    PerformanceProfile.UNKNOWN: 3_000,
}

# 시작 배너. 세로로 A-L-E-X, 가로로 그 약자를 편다.
# 두 의미가 A/L/E 를 공유하고 X 만 갈린다 (PROJECT_ALEX.md 1절):
#   기술적 - Adaptive Learning & Evolving eXperience
#   철학적 - Adaptive Learning & Evolving eXistence
# cp949 콘솔에서 깨지지 않도록 ASCII 로만 그린다. 한글이나 박스 문자를 넣지 말 것.
BANNER = """
  A  Adaptive
  L  Learning &
  E  Evolving
  X  eXperience / eXistence

Project ALEX v0.2.0"""

HELP_TEXT = """사용 가능한 명령
  /help                    이 도움말
  /new                     새 세션 시작
  /sessions                최근 세션 목록
  /use 세션ID               저장된 세션으로 전환
  /rename 제목              현재 세션에 사람이 읽기 쉬운 제목 지정
  /delete-session ID --yes  세션/대화/route 기록을 로컬 DB에서 삭제
  /export                  현재 세션을 Markdown 으로 로컬 export
  /export 형식 [last|all] [제목]  최신 답변/전체 대화를 xlsx·pdf·pptx·docx로 저장(기본 last)
  /document 형식 제목 | 내용     직접 입력한 내용을 문서로 저장(모델 호출 없음)
  /unload                  Ollama 에 올라간 모델을 지금 내려 그래픽카드를 비움(게임 등, 다음 질문 때 다시 올라옴)
  /image 설명               그림 그리기(ComfyUI). 그냥 '노을 지는 바닷가 그려줘' 라고 말해도 된다
  /image-prompt 프롬프트    입력한 문장을 그대로 그림 모델에 전달(기본 1024×1024)
                           앞에 --model=zimage|qwen|anime 를 붙이면 그 모델로(없으면 자동 선택)
  /video-prompt 프롬프트    입력한 문장을 그대로 영상 모델에 전달(기본 720p·5초)
  /archive                 월별 대화 기록(data/archive/26년 11월.md …) 상태 — 답할 때마다 자동으로 적힘
  /archive-year 연도        그해 월별 기록으로 연간 보고서(YY년 대화.md)
  /video-swap 영상주소 [:: 설명]  영상의 움직임은 그대로 주인공만 바꾸기(먼저 '참고 그림 올리기' 로 새 주인공 사진, 최대 12초)
  /video-long 초 설명 [| 다음 장면 | …]  5초 조각을 이어 10~60초 영상(조각마다 약 4분)
  /characters              저장한 캐릭터 목록. 그림 뒤 '이 캐릭터를 은비로 저장해줘' 라고 하면 저장된다
  /character save 이름      방금 그림을 그 이름의 캐릭터로 저장(이후 '은비가 ~ 장면 그려줘' 로 같은 모습 유지)
  /character delete 이름    저장한 캐릭터 지우기(그림 원본은 남는다)
  /character add 이름 주소   그림 주소(URL)나 이 PC 파일 경로의 그림을 캐릭터로 저장(채팅창 '그림 올리기' 버튼도 된다)
  /summary                 현재 세션의 압축 요약 확인
  /remember key=value      장기 메모 저장
  /recall key              장기 메모 조회
  /forget key              장기 메모 삭제
  /memories                장기 메모 전체 보기
  /good                    직전 답변을 좋은 예로 기록 (학습 후보)
  /bad [이유]              직전 답변을 나쁜 예로 기록 (학습 후보)
  /experiences             경험 저장소 현황 (단계/분할별 건수)
  /growth                  성장 루프 현황 (경험 → 사람 검토 → 교훈·학습 후보)
  /review [ID [승인|거절]]   경험을 읽고 승인/거절 (승인한 train 경험은 비슷한 질문에서 교훈으로 쓰임)
  /undo-learning ID         자동으로 배운 항목을 취소 (화면 버튼도 사용)
  /gpu                     모델이 GPU/RAM 에 어떻게 나뉘어 있는지 실측 조회
  /task-add [1|2|3] 내용     구조화된 로컬 할 일 추가 (1=높음, 2=보통, 3=낮음)
  /tasks [all]              할 일 목록 (all은 완료 항목 포함)
  /task-done ID             할 일 완료 처리
  /task-open ID             완료한 할 일 다시 열기
  /task-delete ID --yes     할 일 삭제
  /project [상대경로]       승인된 workspace 안에서 현재 프로젝트 루트 전환/확인
  /tree [깊이]              현재 프로젝트 구조를 bounded tree로 표시
  /find 검색어              현재 프로젝트에서 파일 이름 검색
  /read 상대/경로.py        승인된 루트에서 작은 텍스트 파일 읽기
  /ask-file 경로 :: 질문    선택한 파일을 Local 모델에게만 질문 (Cloud 금지)
  /learn-file 경로          PDF·텍스트 자료를 로컬 지식으로 저장(가중치 학습 아님)
  /knowledge               저장한 자료 목록(새 대화·재시작 후에도 유지)
  /knowledge-search 검색어  모델 호출 없이 관련 원문과 쪽수 조회
  /ask-knowledge [ID] :: 질문  저장한 자료를 Local 모델에게 질문(Cloud 금지)
  /forget-knowledge ID --yes  저장한 자료·검색 본문·사본 삭제(원본 파일 유지)
  /grep 검색어              프로젝트 내용에서 관련 코드/문서 조각 찾기
  /ask-project 질문         관련 여러 파일 조각을 찾아 Local 모델에게 질문
  /search 검색어            웹 검색 + 상위 결과 한 쪽을 읽고 Local 모델이 답 (출처 표시)
  /research 주제            딥서치 직접 요청(복합 비교·근거 조사는 일반 채팅에서도 자동 선택, /deepsearch 도 가능)
  /web 주소 :: 질문         웹 페이지 한 쪽을 읽고 Local 모델에게 질문 (읽기 전용)
  /edit-file 경로 :: 지시    Local 모델이 수정안을 만들고 diff만 표시 (자동 적용 없음)
  /edit-files 경로1, 경로2 :: 지시   여러 파일 부분 수정·새 파일을 한 묶음 수정안으로 (자동 적용 없음)
  /apply-edit ID --yes       diff 확인 후 편집 제안(묶음은 전부 또는 전무)을 적용 + 원본 백업
  /discard-edit ID           적용하지 않을 편집 제안 폐기
  /undo-file 경로 --yes      해당 파일의 가장 최근 ALEX 백업으로 복구
  /undo-bundle ID --yes      적용한 묶음을 통째로 되돌림 (그 뒤 바뀐 파일이 있으면 거부)
  /build 요청               코딩 모델이 작업 폴더(data/builds)에 파일을 직접 만들고 검사가 통과할 때까지 고침
  /download 주소 [--yes]    인터넷 파일을 data/downloads 에(말로 '내려받아줘' 도 됨). 실행 파일·1GB 넘으면 --yes 승인, 실행은 안 함
  /cloud-default [claude|codex]   이름을 말하지 않고 넘길 때 먼저 맡길 쪽(지금 값 보기·바꾸기)
  /cloud [claude|codex] [@하위폴더] 요청   어려운 일을 클라우드에(말로 '클라우드로 넘겨줘'도 됨). 프로젝트 폴더 복사본에서 고치고 ALEX 가 확인 → 승인해야 적용
  /rtl-tests 폴더 [회수]   검증 쓰기: 규격·포트만 보고 시험기(verify/alex_tb.v)를 쓰고, 설계에 넣은 고장을 몇 개 잡는지로 확인·보강
  /eda 폴더 [synth|formal|fpga]   RTL 프로젝트 합성(셀·래치)·형식 검증(*.sby)·FPGA 최고 주파수 — 종류를 빼면 셋 다
  /rtl 폴더 [회수] [--continue]  Verilog 설계: 모델이 쓰고 린트·시뮬레이션 결과로 스스로 고침(fabless 폴더의 규격·시험기 기준)
                           (채팅의 '~ 만들어줘' 는 코드를 답으로 주고 뒤에서 실행만 확인)
  /dev-start 파일[, 파일2…] :: 테스트[, 테스트2…] :: 목표   개발 작업 시작 (여러 파일이면 묶음 수정안, 테스트는 모두 통과해야 함)
  /dev-next                테스트 → 실패 분석 → 수정안 생성 (최대 3회)
  /dev-auto                격리 복사본에서 테스트→수정→재시험 최대 3회, 통과하면 최종 묶음 수정안만 제시
                           (coding_auto_attempts 가 2 이상이면 실패 때 처음 상태에서 새로 풀기를 그 횟수까지,
                            coding_regression_check 를 켜면 통과 뒤 전체 테스트로 다른 테스트를 깨뜨렸는지도 본다)
  /dev-propose             통과한 기준 테스트 뒤 목표에 따른 수정안 생성
  /dev-verify              선택한 테스트 통과 뒤 프로젝트 전체 unittest 확인
  /dev-changes             승인된 프로젝트의 Git 상태·diff 요약 읽기
  /dev-check [이름]        프로젝트의 alex-checks.json 에 사용자가 적은 검사 명령 목록/실행
  /dev-outcomes            최근 개발 작업의 결과 기록(시험·수정안·적용·전체 검사) 요약
  /dev-learn               결과 기록에서 코딩 절차 교훈을 뽑는다(로컬 모델, ADR-070)
  /self-check [개수]       스스로 점검: 자기 기능 질문을 스스로 풀고 틀린 것을 교훈으로(기본 5·최대 20, 그래픽카드 80℃ 넘으면 멈춤)
  /dev-insights [remove N|skill N [이름]] 코딩 절차 교훈 목록 / 지우기 / 골라서 스킬로 (붙이기: coding_insights_enabled)
  /dev-list                현재 프로젝트의 저장된 개발 작업 목록
  /dev-resume ID           저장된 작업 선택 (자동 실행 없음)
  /dev-status              저장된 개발 작업 상태·검증 이력
  /route 질문               실행하지 않고 Router 판정만 미리 보기
  /ask-local 질문           Cloud 금지: 질문을 Local General/Coder로 강제
  /ask-cloud 질문           승인 Gate 유지: 질문을 Cloud Coder/Reasoning으로 강제
  /wrong [올바른목적지]      직전 Router 판정을 오분류로 기록
  /route-stats             누적 오분류 수 요약
  /status                  현재 세션/프로젝트/할 일/프로필 한눈에 보기
  /wake-desktop --yes      설정된 고정 MAC으로 Wake-on-LAN (기본 비활성)
  /open-vscode --yes       현재 승인 project를 VS Code로 열기 (기본 비활성)
  /doctor                  DB/Local Provider/설정 상태 점검
  /backup                  SQLite Memory/Session DB 안전 백업
  /state-export [경로]       Memory+Task portable JSON export (기기 이동용)
  /state-import 경로 --yes   portable JSON을 보수적으로 merge (기존 Memory 우선)
  /profile                 하드웨어/프로필 정보
  /exit                    종료"""


def configure_logging(log_dir: Path, max_bytes: int = 2_000_000, backup_count: int = 3) -> str | None:
    """파일 로그를 우선 사용하되 경로 문제가 있어도 콘솔 모드로 계속 시작한다."""
    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "jarvis.log",
            maxBytes=max(100_000, int(max_bytes)),
            backupCount=max(1, int(backup_count)),
            encoding="utf-8",
        )
    except OSError as exc:
        logging.basicConfig(level=logging.INFO, format=fmt, handlers=[logging.StreamHandler()])
        return f"파일 로그를 만들지 못해 콘솔 로그만 사용합니다: {exc}"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[file_handler, logging.StreamHandler()],
    )
    return None



def apply_profile_override(hardware: HardwareInfo, force_profile: str) -> str | None:
    value = force_profile.strip().upper()
    if not value:
        return None
    original = hardware.profile
    hardware.profile = PerformanceProfile(value)
    return f"Hardware profile 강제 지정: {original.value} -> {hardware.profile.value}"

def effective_local_models(settings: Settings, profile: PerformanceProfile) -> tuple[str, str]:
    """Resolve profile-aware model names without mutating Settings."""
    if profile == PerformanceProfile.DESKTOP and settings.auto_profile_models:
        return settings.desktop_general_model, settings.desktop_coder_model
    return settings.general_model, settings.coder_model


# RAM 오프로딩 실측(PERFORMANCE.md 2절, 2026-09-19 정정판). (RAM 비율, 남는 tok/s 비율).
#
# 처음에는 "12% 만 넘어가도 6.6배" 를 인용했는데 **그 값은 모델 로딩을 포함한 콜드 스타트**
# 였다. 모델이 상주한 뒤(둘째 턴부터)를 같은 모델로 다시 재니 훨씬 싸다. 첫 글자까지는
# 어느 경우에도 0.5초 미만이었다.
OFFLOAD_THROUGHPUT = ((0.12, 0.89), (0.24, 0.55), (0.50, 0.29))
GIB = 1024 ** 3


def _remaining_throughput(ratio: float) -> float:
    """RAM 비율에서 남는 생성 속도 비율을 실측 표로 추정한다(사이는 선형 보간)."""
    points = ((0.0, 1.0),) + OFFLOAD_THROUGHPUT
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if ratio <= x1:
            span = x1 - x0
            return y0 + (y1 - y0) * ((ratio - x0) / span if span else 0)
    return OFFLOAD_THROUGHPUT[-1][1]


def offload_report(placement: dict | None, budget_gb: float) -> tuple[str, str] | None:
    """실측 배치를 읽고 (심각도, 문장) 을 돌려준다. 넘어간 것이 없으면 None.

    `placement` 는 `OllamaProvider.placement()` 가 `/api/ps` 에서 읽어온 실측값이다.
    여기서 추정하지 않는다 — 모델 크기로 VRAM 을 계산하는 방식은 quant/KV 구현에 따라
    틀리고, 틀린 추정으로 경고하면 경고를 무시하게 된다.
    """
    if not placement:
        return None
    total = placement.get("total_bytes") or 0
    ram = placement.get("ram_bytes") or 0
    if total <= 0 or ram <= 0:
        return None
    ratio = ram / total
    ram_gb = ram / GIB
    left = _remaining_throughput(ratio)
    cost = f"실측으로 이 비율이면 생성 속도가 약 {1 - left:.0%} 느려집니다"
    where = f"{ram_gb:.2f}GB({ratio:.0%})가 시스템 RAM"
    if budget_gb <= 0:
        return ("warn", (
            f"모델의 {where}에 올라가 있습니다. RAM 넘김을 허용하지 않은 설정인데 "
            f"넘어갔습니다 — {cost}. num_ctx 를 낮추거나 더 작은 모델을 쓰십시오. "
            "의도한 것이라면 JARVIS_RAM_OFFLOAD_BUDGET_GB 를 올리십시오."
        ))
    if ram_gb > budget_gb:
        return ("warn", (
            f"모델의 {where}에 올라가 허용치 {budget_gb:.1f}GB 를 넘었습니다. {cost}."
        ))
    return ("info", (
        f"모델의 {where}에 올라가 있습니다(허용 {budget_gb:.1f}GB 안). {cost} — "
        "대화는 견디지만 코드 생성은 토큰 수가 많아 그대로 곱해집니다(PERFORMANCE.md 2절)."
    ))


# 출력 예산은 컨텍스트 창 안에서 프롬프트와 자리를 나눠 쓴다. 프롬프트 몫을 남기지 않으면
# 대화가 조금만 길어져도 답이 잘린다. 2026-09-19 측정의 벤치마크 프롬프트는 327 토큰뿐이었고
# 실사용 프롬프트는 그보다 훨씬 길다.
MIN_PROMPT_RESERVE = 1024
PROMPT_RESERVE_DIVISOR = 4


def clamp_output_budget(num_ctx: int, num_predict: int) -> int:
    """`num_ctx` 안에서 안전한 `num_predict` 를 돌려준다.

    UNKNOWN 프로필은 `num_ctx` 가 2048 인데, 사용자가 랩탑 실측으로 정한 6144 를 그대로
    쓰면 **출력 예산이 컨텍스트 창보다 크다.** 검증이 두 값을 각각만 보고 서로 비교하지
    않아 조용히 그 상태로 돌았다. 여기서 막는다.
    """
    reserve = max(MIN_PROMPT_RESERVE, num_ctx // PROMPT_RESERVE_DIVISOR)
    return max(MIN_PROMPT_RESERVE // 4, min(num_predict, num_ctx - reserve))


def coding_char_budget(settings: Settings, chat_num_ctx: int, context_char_budget: int) -> int:
    """코딩 수정안의 글자 예산(ADR-076). 코딩 문맥이 꺼져 있으면 0(대화 예산을 쓴다).

    직접 적은 값이 우선이고, 없으면 대화 예산을 문맥 비율만큼 키운다 — 대화 예산(16K 에 2만 4천 자)은 한국어 기준이라
    코드에는 보수적이다. core.py 를 통째로 보이려면 토큰 수를 잰 뒤 coding_context_char_budget 을 직접 적는다.
    """
    if not settings.coding_num_ctx:
        return 0
    if settings.coding_context_char_budget:
        return settings.coding_context_char_budget
    return min(500_000, context_char_budget * settings.coding_num_ctx // max(1, chat_num_ctx))


def effective_runtime_limits(
    settings: Settings, profile: PerformanceProfile
) -> tuple[int, int, int]:
    """프로필에 맞는 (num_ctx, num_predict, request_timeout) 을 고른다. Settings 는 안 바꾼다.

    전역값(`num_ctx`, `local_max_output_tokens`, `request_timeout`)은 RTX 4070 Laptop /
    8GB VRAM / 8B 에서 실측해 정한 것이다(`PERFORMANCE.md` 12~13절). 그대로 Desktop 에
    가져가면 16GB 장비를 8GB 기준으로 묶는다 — 특히 `num_ctx` 는 설정되어 있으면
    `NUM_CTX_BY_PROFILE[DESKTOP]` 을 덮어쓴다.

    그래서 Desktop 전용 값을 따로 둔다. 0 이면 기존 동작 그대로다(전역값 → 프로필 자동값).
    **여기에 값을 적기 전에 그 장비에서 실측하라.** 다른 장비의 상한을 옮겨 적으면 안 된다 —
    2026-09-19 에 프롬프트 하나 바꿨더니 필요한 예산이 4,712 에서 5,715 토큰으로 변했다.
    """
    if profile == PerformanceProfile.DESKTOP:
        num_ctx = (
            settings.desktop_num_ctx
            or settings.num_ctx
            or NUM_CTX_BY_PROFILE.get(profile, 2048)
        )
        num_predict = settings.desktop_max_output_tokens or settings.local_max_output_tokens
        timeout = settings.desktop_request_timeout or settings.request_timeout
        return num_ctx, clamp_output_budget(num_ctx, num_predict), timeout
    num_ctx = settings.num_ctx or NUM_CTX_BY_PROFILE.get(profile, 2048)
    return (
        num_ctx,
        clamp_output_budget(num_ctx, settings.local_max_output_tokens),
        settings.request_timeout,
    )


def engine_context_limits(settings: Settings, profile: PerformanceProfile) -> tuple[int, int, int]:
    """고른 엔진에 맞는 (대화 글자 예산, 최근 대화 수, 요약을 시작하는 메시지 수). 2026-10-11 사용자: "선택에 따라서
    자동으로 설정을 바뀌게끔". Strata 는 64K 문맥 창에 맞춰 키우고, Ollama 로 바꾸면 기존 값(16K 기준)으로 돌아간다.

    Strata 자동 예산 = (문맥 창 - 답 자리 - 지시문 자리 4096) × 0.9 글자 — 한국어 최악(1글자 1토큰)에도 넘치지 않는다.
    64K·답 16K 이면 40,550자(기존 24,000자). 코드는 1토큰에 3~4글자라 여유가 더 크다.
    """
    if settings.local_backend == "strata":
        budget = settings.strata_context_char_budget or max(
            CONTEXT_CHAR_BUDGET_BY_PROFILE[PerformanceProfile.UNKNOWN],
            int((settings.strata_num_ctx - settings.strata_max_output_tokens - 4096) * 0.9))
        history = settings.strata_history_limit
        return budget, history, max(settings.summary_trigger_messages, history * 2)
    budget = settings.context_char_budget or CONTEXT_CHAR_BUDGET_BY_PROFILE[profile]
    return budget, settings.history_limit, settings.summary_trigger_messages


def num_gpu_for(settings: Settings, model: str) -> int:
    """그 모델에 줄 GPU 층 수. `num_gpu_layers_model` 이 정해져 있으면 그 모델에만 주고 다른 모델은 자동(0)이다."""
    if settings.num_gpu_layers_model and model != settings.num_gpu_layers_model:
        return 0
    return settings.num_gpu_layers


def provider_runtime_settings(provider: BaseLLMProvider) -> dict:
    """Report the live provider, including profile overrides and output clamping."""
    result = {"model": str(getattr(provider, "model", "") or provider.name)}
    if isinstance(provider, OllamaProvider):
        result.update(
            think=provider.think and not provider._thinking_unsupported, num_ctx=provider.num_ctx,
            num_predict=provider.num_predict, timeout=provider.timeout,
            num_gpu=provider.num_gpu, keep_alive=provider.keep_alive,
        )
    return result


def build_providers(
    settings: Settings, hardware: HardwareInfo
) -> tuple[BaseLLMProvider, BaseLLMProvider, list[str]]:
    """설정에 따라 local provider 를 만든다. 실패해도 프로그램 조립 자체는 계속된다."""
    notes: list[str] = []
    if not settings.use_ollama:
        notes.append("Mock 모드입니다. 실제 모델을 쓰려면 JARVIS_USE_OLLAMA=1 로 실행하세요.")
        return (
            MockLLMProvider("mock-general", "LOCAL GENERAL"),
            MockLLMProvider("mock-coder", "LOCAL CODER"),
            notes,
        )

    num_ctx, num_predict, timeout = effective_runtime_limits(settings, hardware.profile)
    requested = settings.local_max_output_tokens
    if hardware.profile == PerformanceProfile.DESKTOP and settings.desktop_max_output_tokens:
        requested = settings.desktop_max_output_tokens
    if num_predict < requested:
        notes.append(
            f"출력 예산을 {requested} 에서 {num_predict} 로 줄였습니다. 이 장비의 컨텍스트 창이 "
            f"{num_ctx} 라서 원래 값은 프롬프트 자리를 남기지 않습니다. "
            "이 등급에서는 thinking 을 끄십시오 — 코드 생성 성공 회차가 최소 2,650 토큰을 "
            "썼습니다(PERFORMANCE.md 12절)."
        )
    if hardware.profile == PerformanceProfile.DESKTOP and not (
        settings.desktop_num_ctx or settings.desktop_max_output_tokens
        or settings.desktop_request_timeout
    ) and (settings.num_ctx or settings.local_max_output_tokens != 1024):
        notes.append(
            "Desktop 인데 Desktop 전용 상한이 비어 있어 전역값을 씁니다. 전역값은 8GB VRAM "
            "랩탑에서 실측한 것이라 이 장비를 과소하게 묶을 수 있습니다. "
            "HARDWARE_VALIDATION.md B절 순서로 측정한 뒤 desktop_num_ctx / "
            "desktop_max_output_tokens / desktop_request_timeout 에 적으십시오."
        )
    if settings.local_backend == "strata":
        # ADR-086: 대화·코딩이 같은 Strata 서버의 한 모델(Qwen3.8-Flash-Next)을 쓴다 — 모델을 바꿔 올리는 일이 없다.
        strata = StrataProvider(
            model=settings.strata_model, host=settings.strata_host, timeout=timeout, num_ctx=settings.strata_num_ctx,
            think=settings.ollama_think, num_predict=settings.strata_max_output_tokens,
            reasoning_effort=settings.strata_reasoning_effort,
        )
        notes.append(f"Strata 모드: {settings.strata_host} ({settings.strata_model}, num_ctx={settings.strata_num_ctx}, "
                     f"num_predict={settings.strata_max_output_tokens}, "
                     f"think={settings.ollama_think}/{settings.strata_reasoning_effort})")
        if not strata.health_check():
            notes.append(f"주의: Strata 서버({settings.strata_host})가 응답하지 않습니다 — Strata 를 켰는지 확인하세요.")
        return strata, strata, notes
    general_model, coder_model = effective_local_models(settings, hardware.profile)
    if hardware.profile == PerformanceProfile.DESKTOP and settings.auto_profile_models:
        notes.append(
            f"Desktop auto model profile: general={general_model}, coder={coder_model}"
        )
    general = OllamaProvider(
        model=general_model,
        host=settings.ollama_host,
        timeout=timeout,
        num_ctx=num_ctx,
        think=settings.ollama_think,
        num_predict=num_predict,
        num_gpu=num_gpu_for(settings, general_model),
        keep_alive=settings.ollama_keep_alive,
    )
    if hardware.profile != PerformanceProfile.DESKTOP and settings.single_model_on_laptop:
        # 8GB VRAM laptop에서 두 7B 모델을 번갈아 load/unload 하는 비용을 피한다.
        coder = general
        notes.append(
            "Laptop single-model 모드: General 모델 하나를 General/Coder route가 공유합니다. "
            "분리하려면 JARVIS_SINGLE_MODEL_ON_LAPTOP=0"
        )
    else:
        coder = OllamaProvider(
            model=coder_model,
            host=settings.ollama_host,
            timeout=timeout,
            num_ctx=num_ctx,
            think=settings.ollama_think,
            num_predict=num_predict,
            num_gpu=num_gpu_for(settings, coder_model),
            keep_alive=settings.ollama_keep_alive,
        )
    notes.append(
        f"Ollama 모드: {settings.ollama_host} (num_ctx={num_ctx}, num_predict={num_predict}, "
        f"think={settings.ollama_think}, keep_alive={settings.ollama_keep_alive})"
    )
    for provider in dict.fromkeys((general, coder)):
        if not provider.health_check():
            notes.append(
                f"주의: {provider.name} health_check 실패 — 서버/정확한 model tag 를 확인하세요."
            )
    return general, coder, notes


def build_cloud_providers(settings: Settings) -> tuple[BaseLLMProvider, BaseLLMProvider, list[str]]:
    """Build opt-in Cloud adapters without ever storing API-key values in Settings/logs."""
    if settings.local_only:
        return FailingProvider("cloud-disabled"), FailingProvider("cloud-disabled"), ["LOCAL ONLY: Cloud 호출 차단. API 토큰 비용 없음."]
    provider = settings.cloud_provider
    if provider == "stub":
        return (
            CloudStubProvider("cloud-coder-stub", "CLOUD CODER STUB"),
            CloudStubProvider("cloud-reasoning-stub", "CLOUD REASONING STUB"),
            ["Cloud mode: STUB (실제 외부 전송 없음)"],
        )

    if provider == "openai":
        key = os.getenv("OPENAI_API_KEY", "")
        cls = OpenAIResponsesProvider
        key_name = "OPENAI_API_KEY"
    elif provider == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY", "")
        cls = AnthropicMessagesProvider
        key_name = "ANTHROPIC_API_KEY"
    else:
        key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
        cls = GeminiGenerateContentProvider
        key_name = "GOOGLE_API_KEY 또는 GEMINI_API_KEY"

    if not key.strip():
        raise ConfigError(
            f"JARVIS_CLOUD_PROVIDER={provider} 이지만 {key_name} 환경 변수가 없습니다. "
            "키를 코드/설정 파일에 적지 말고 환경 변수로만 제공하세요."
        )

    common = dict(
        api_key=key,
        timeout=settings.request_timeout,
        max_output_tokens=settings.cloud_max_output_tokens,
    )
    coder = cls(model=settings.cloud_coder_model, **common)
    reasoning = cls(model=settings.cloud_reasoning_model, **common)
    return coder, reasoning, [
        f"Cloud mode: {provider} (coder={settings.cloud_coder_model}, reasoning={settings.cloud_reasoning_model})",
        "Cloud 전송은 승인 Gate 통과 뒤에만 실행됩니다.",
    ]


def cloud_default(core: JarvisCore, choice: str) -> str:
    """이름을 말하지 않고 클라우드로 넘길 때 먼저 맡길 쪽(ADR-102). jarvis.local.json 의 cloud_agent 한 칸만 바꾼다."""
    labels = {"claude": "Claude Code", "codex": "Codex"}
    current = core.cloud_agents.default
    if not choice:
        return (f"클라우드 기본: {labels[current]} — 이름을 말하지 않으면 먼저 맡기고, 한도·오류면 다른 쪽이 대신합니다.\n"
                f"바꾸기: /cloud-default {'codex' if current == 'claude' else 'claude'}")
    if choice not in labels:
        return "사용법: /cloud-default claude 또는 /cloud-default codex"
    path = local_config_path()
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data["cloud_agent"] = choice
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    core.cloud_agents.default = choice
    return f"클라우드 기본을 {labels[choice]} 로 바꿨습니다(다시 시작해도 유지)."


def build_summary_provider(general: BaseLLMProvider, settings: Settings) -> BaseLLMProvider:
    """Same local model, cheaper request settings for periodic conversation summaries."""
    if not isinstance(general, OllamaProvider) or not general.think:
        return general
    if isinstance(general, StrataProvider):
        return StrataProvider(model=general.model, host=general.host, timeout=general.timeout, num_ctx=general.num_ctx,
                              name=f"{general.name}:summary", health_ttl=general.health_ttl, think=False,
                              num_predict=min(general.num_predict, max(2048, settings.summary_max_chars)))
    return OllamaProvider(
        model=general.model,
        host=general.host,
        timeout=general.timeout,
        num_ctx=general.num_ctx,
        name=f"{general.name}:summary",
        health_ttl=general.health_ttl,
        think=False,
        num_predict=min(general.num_predict, max(2048, settings.summary_max_chars)),
        num_gpu=general.num_gpu,
        keep_alive=general.keep_alive,
    )


def build_memory_vectors(settings: Settings) -> MemoryVectors | None:
    """기억 뜻 검색(ADR-094). 꺼져 있으면 None — 낱말 검색만. 서버가 안 떠 있어도 만든다(질문 때 없으면 낱말로 대신)."""
    if not settings.embedding_enabled:
        return None
    return MemoryVectors(settings.data_dir / "embeddings.db", EmbeddingClient(settings.embedding_host))


def apply_coding_worker(core, settings: Settings, notes: list[str] | None = None) -> None:
    """설정대로 /dev-auto·/build 의 코딩 일꾼을 단다 — 시작할 때와 설정 화면에서 무료 모델을 바꿀 때 같이 쓴다(2026-10-23).
    무료 모델(coding_free_model) > ALEX 하네스(coding_worker=harness) > 이 PC 모델의 오픈코드(coding_worker=opencode)."""
    from .router import Destination as _Destination
    home = Path(settings.opencode_dir) if settings.opencode_dir else Path(__file__).resolve().parents[2] / "tools" / "opencode"
    notes = notes if notes is not None else []
    if settings.coding_free_model and settings.coding_worker in ("opencode", "harness"):
        # OpenCode 무료 모델 — 무료 단계는 OpenCode 안에서만 되므로 OpenCode 일꾼으로. 이 PC GPU 는 쓰지 않는다.
        worker = OpenCodeWorker(home, settings.coding_free_model, timeout=settings.opencode_timeout)
        worker.label = "오픈코드 무료 모델"
        notes.append(f"Coding worker: OpenCode free model {settings.coding_free_model} (코드가 opencode.ai 로 전송)")
    elif settings.coding_worker == "harness":  # ALEX 코딩 하네스(ADR-112) — OpenCode 방식을 ALEX 코드로, 모델은 지금 코딩 모델
        from .harness.worker import HarnessWorker
        worker = HarnessWorker(lambda: core.providers.get(_Destination.LOCAL_CODER), timeout=settings.opencode_timeout,
                               lessons=core.coding_insight_text)  # 절차 교훈(ADR-070, 설정으로 켤 때만)
        worker.progress = core._progress
        worker.logger = core.logger
        notes.append("Coding worker: ALEX harness (ADR-112)")
    elif settings.coding_worker == "opencode":
        strata = settings.local_backend == "strata"
        worker = OpenCodeWorker(home, settings.strata_model if strata else settings.opencode_model,
                                context=settings.strata_num_ctx if strata else settings.opencode_context,
                                timeout=settings.opencode_timeout,
                                host=settings.strata_host if strata else settings.ollama_host,
                                backend=settings.local_backend)
    else:
        worker = None
    core.opencode_worker = worker
    if worker is not None and hasattr(core, "_release_media_for_worker"):
        worker.before_run = core._release_media_for_worker


def build_core(
    settings: Settings | None = None,
    approval_callback=None,
    response_chunk_callback=None,
) -> tuple[JarvisCore, list[str]]:
    settings = settings or load_settings()
    logging_warning = configure_logging(settings.log_dir, settings.log_max_bytes, settings.log_backup_count)
    hardware = detect_hardware()
    profile_note = apply_profile_override(hardware, settings.force_profile)
    storage = Storage(settings.database_path)
    general, coder, notes = build_providers(settings, hardware)
    summarizer = build_summary_provider(general, settings)
    if summarizer is not general:
        notes.append(f"Local summary: {general.model}, think off, output cap {summarizer.num_predict}")
    cloud_coder, cloud_reasoning, cloud_notes = build_cloud_providers(settings)
    notes.extend(cloud_notes)
    if profile_note:
        notes.insert(0, profile_note)
    if logging_warning:
        notes.append(logging_warning)

    context_char_budget, history_limit, summary_trigger = engine_context_limits(settings, hardware.profile)
    notes.append(f"Context char budget: {context_char_budget}, history {history_limit}")
    coding_budget = coding_char_budget(settings, effective_runtime_limits(settings, hardware.profile)[0], context_char_budget)
    if settings.coding_num_ctx:
        notes.append(f"Coding context (ADR-076): num_ctx={settings.coding_num_ctx}, char budget {coding_budget}, "
                     f"GPU layers {settings.coding_num_gpu_layers or 'auto'}")

    core = JarvisCore(
        storage=storage,
        hardware=hardware,
        local_general=general,
        local_summarizer=summarizer,
        local_only=settings.local_only,
        ram_offload_budget_gb=settings.ram_offload_budget_gb,
        local_coder=coder,
        cloud_coder=cloud_coder,
        cloud_reasoning=cloud_reasoning,
        history_limit=history_limit,
        context_char_budget=context_char_budget,
        file_search_tool=SafeFileSearchTool(settings.file_search_root),
        web_reader=WebReader() if settings.web_enabled else None,
        web_tool_enabled=settings.web_tool_enabled,
        file_read_tool=SafeFileReadTool(settings.file_search_root, settings.file_read_max_bytes),
        project_tree_tool=SafeProjectTreeTool(settings.file_search_root),
        project_context_tool=SafeProjectContextTool(
            settings.file_search_root,
            max_files=settings.project_context_max_files,
            max_total_bytes=settings.project_context_max_bytes,
            max_context_chars=settings.project_context_max_chars,
        ),
        workspace_editor=SafeWorkspaceEditor(
            settings.file_search_root,
            settings.data_dir / "workspace_backups",
            max_bytes=max(settings.file_read_max_bytes, 200_000),
            storage=storage,
        ),
        wake_on_lan_tool=(
            WakeOnLanTool(settings.wol_mac, settings.wol_broadcast, settings.wol_port)
            if settings.wol_enabled else None
        ),
        vscode_launcher=(
            VSCodeLauncher(settings.file_search_root, settings.vscode_executable)
            if settings.vscode_launch_enabled else None
        ),
        memory_context_limit=settings.memory_context_limit,
        lesson_context_limit=settings.lesson_context_limit,
        auto_lessons=settings.auto_lessons_enabled,
        coding_insights=settings.coding_insights_enabled,
        coding_auto_learn=settings.coding_auto_learn_enabled,
        fact_reminder=settings.fact_reminder_enabled,
        stable_prompt_prefix=settings.stable_prompt_prefix,
        coding_api_map=settings.coding_api_map_enabled,
        coding_num_ctx=settings.coding_num_ctx,
        coding_num_gpu=settings.coding_num_gpu_layers,
        coding_context_char_budget=coding_budget,
        coding_auto_attempts=settings.coding_auto_attempts,
        coding_regression_check=settings.coding_regression_check,
        coding_diagnostic_context=settings.coding_diagnostic_context,
        build_loop=settings.coding_build_loop_enabled,
        code_check=settings.coding_code_check_enabled,
        coding_worker=settings.coding_worker,
        opencode_worker=OpenCodeWorker(
            Path(settings.opencode_dir) if settings.opencode_dir else Path(__file__).resolve().parents[2] / "tools" / "opencode",
            settings.strata_model if settings.local_backend == "strata" else settings.opencode_model,
            context=settings.strata_num_ctx if settings.local_backend == "strata" else settings.opencode_context,
            timeout=settings.opencode_timeout,
            host=settings.strata_host if settings.local_backend == "strata" else settings.ollama_host,
            backend=settings.local_backend) if settings.coding_worker == "opencode" else None,  # harness 는 아래에서
        rtl_root=Path(settings.rtl_root) if settings.rtl_root else Path(__file__).resolve().parents[2] / "fabless",
        rtl_tools_dir=Path(settings.rtl_tools_dir) if settings.rtl_tools_dir else Path("C:/oss-cad-suite"),
        image_generator=build_image_generator(settings),
        memory_vectors=build_memory_vectors(settings),
        require_cloud_approval=settings.require_cloud_approval,
        approval_callback=approval_callback,
        response_chunk_callback=response_chunk_callback,
        cloud_include_memory=settings.cloud_include_memory,
        cloud_include_summary=settings.cloud_include_summary,
        cloud_block_sensitive=settings.cloud_block_sensitive,
        summary_enabled=settings.summary_enabled,
        summary_trigger_messages=summary_trigger,
        summary_max_chars=settings.summary_max_chars,
        misroute_log_path=settings.log_dir / "misroute.jsonl",
    )
    apply_coding_worker(core, settings, notes)
    from .selfknow import SelfKnowledge  # 자기 기능 알기 — 명령 목록과 화면에서(2026-10-23)
    core.self_knowledge = SelfKnowledge(HELP_TEXT, Path(__file__).resolve().parent / "static" / "index.html", extra=(
        "말로 파일 고치기·수정 — 채팅에 '프로젝트에 있는 파일 이름 + 고쳐줘·수정해줘·추가해줘·바꿔줘' 라고 하면 코딩 세션이 그 파일을 "
        "직접 읽고 쓰고 고친다(폴더는 지금 프로젝트, 권한은 입력창 선택, 관리자만). 허락은 채팅에서 '응'·'항상'·'아니' 로 답하고, "
        "'되돌려줘' 로 되돌린다. '코딩 세션에서 ~해줘' 라고 해도 된다. 새 프로그램을 짜 달라는 말은 코드를 답으로 준다(파일로 만들려면 /build).",
        # 권한·스스로 하는 일(2026-10-23 사용자 "alex 가 권한 생긴 거 인지하게") — 화면의 권한 메뉴는 title 이 없어 따로 적는다
        "권한 — 입력창의 권한 고르기: 읽기 전용(읽기·찾기·답만, 파일 바꾸기·코드 실행·그림 없음) · 확인 후 실행(기본 — 파일을 바꾸기 전에 "
        "diff 를 보이고 매번 허락을 받음) · 자동 실행(관리자만 — 묻지 않고 적용, 원본 백업·되돌리기). 이 PC 의 파일·명령·코딩 세션·클라우드 "
        "넘기기는 관리자만. 설치·삭제·오래 걸리는 GPU 작업·게시는 늘 사람 승인, 비밀번호·결제·권한 규칙 수정은 늘 금지.",
        "웹 검색·딥서치 — 대화 중 모르는 최신 사실은 ALEX 가 스스로 웹 검색을 하고(사용자가 '검색해줘' 라고 안 해도 됨), 여러 출처를 "
        "비교·근거 조사해야 하는 질문은 스스로 딥서치로 넘어가 여러 검색·본문을 읽고 출처와 함께 보고한다(/research 로 직접 요청도 가능). "
        "'검색 없이' 라고 하면 검색하지 않는다.",
        "문서 — 워드·엑셀·PDF·PPT 만들기('워드로 만들어줘')와 올린 문서(PDF·워드·엑셀·PPT·한글) 읽기·요약은 된다. 올린 워드·엑셀·PPT 를 "
        "고친 사본 다운로드도 된다(자기 대화 첨부만, 원본 유지). 관리자 PC 문서는 프로젝트 안에서 권한에 따라 수정안을 승인한 뒤 "
        "백업하고 그 자리에서 고친다(자동 실행 권한이면 바로 적용). '되돌려줘'로 백업을 복원한다. 읽기 전용에서는 편집하지 않는다. "
        "표·그림·레이아웃은 유지하며 바꾼 문단의 혼합 글자 서식은 일부 달라질 수 있다. "
        "글로 된 파일(.md·.txt·코드)도 말로 파일 고치기로 그 자리에서 고칠 수 있다.",))
    # 영상 링크 읽기(ADR-098): C:\ALEX\tools\video 에 yt-dlp·Deno 가 있으면 켠다
    tools = Path(__file__).resolve().parents[2] / "tools"
    video_reader = VideoReader(tools / "video", transcriber=Transcriber(tools / "whisper"))  # 받아쓰기(ADR-108)는 있으면 켬
    core.video_reader = video_reader if video_reader.available() else None
    if core.video_reader is not None:
        notes.append("Video links: captions via yt-dlp (ADR-098)"
                     + (", speech via whisper.cpp (ADR-108)" if video_reader.can_listen() else ""))
    saved_project = storage.get_state("active_project_relative")
    if saved_project and saved_project not in {"", "."}:
        try:
            restored = core.set_project_root(saved_project)
            notes.append(f"Restored project root: {restored}")
        except (ToolError, StorageError) as exc:
            notes.append(f"Saved project root restore skipped: {exc}")
    core.cloud_agents = CloudAgents(settings.cloud_agent, settings.cloud_agent_timeout)
    return core, notes


def format_cloud_approval(request: CloudApprovalRequest) -> str:
    lines = [
        "\n[ALEX Cloud 승인]",
        f"목적지: {request.destination.value}",
        f"Provider: {request.provider_name}",
        f"이유: {request.reason}",
        f"전송 Context 메시지 수: {request.message_count}",
        f"전송 Context 문자 수(근사): {request.context_chars}",
        f"장기 메모 포함: {'예' if request.memory_included else '아니오'}",
        f"이전 대화 요약 포함: {'예' if request.summary_included else '아니오'}",
        f"최근 요청 미리보기: {request.last_user_preview}",
    ]
    if request.sensitive_warnings:
        lines.append("주의: 민감정보로 보이는 패턴 감지 — " + ", ".join(request.sensitive_warnings))
    if request.hard_blocked:
        lines.append(
            "보안 정책으로 Cloud 전송을 차단했습니다. 정말 필요하면 "
            "JARVIS_CLOUD_BLOCK_SENSITIVE=0 을 명시적으로 설정해야 합니다."
        )
    else:
        lines.append("Cloud 로 보내시겠습니까? [y/N]: ")
    return "\n".join(lines)


def cli_cloud_approval(request: CloudApprovalRequest) -> bool:
    """CLI 에만 있는 사용자 상호작용. Core 는 input() 을 모른다."""
    if request.hard_blocked:
        print(format_cloud_approval(request))
        return False
    try:
        answer = input(format_cloud_approval(request)).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in {"y", "yes", "예", "ㅇ"}


def lesson_pool(core: JarvisCore) -> list:
    """다음 대화에 교훈으로 쓰일 수 있는 경험: 승인된 답 경험과 자동으로 찾은 사실, 분할 무관 (ADR-055·057)."""
    return [e for e in core.storage.list_experiences(limit=100_000) if lesson_usable(e)]


def growth_report(core: JarvisCore) -> str:
    """성장 루프(PROJECT_ALEX 9절)가 지금 어디까지 돌았는지. 숫자만 보인다 — 판단은 사람이 한다."""
    counts = core.storage.experience_counts()
    by_stage: dict[str, int] = {}
    held_out = 0
    for key, n in counts.items():
        stage, _, split = key.partition("/")
        by_stage[stage] = by_stage.get(stage, 0) + n
        if split == "eval":
            held_out += n
    total = sum(counts.values())
    waiting = by_stage.get("raw", 0) + by_stage.get("candidate", 0)
    stages = " · ".join(f"{s} {by_stage[s]}" for s in ("raw", "candidate", "approved", "rejected") if by_stage.get(s))
    used = ", ".join(i[:8] for i in core.last_lessons) or "없음"
    pool = lesson_pool(core)
    origin = {"user(chat)": 0, "user(/bad)": 0, "auto(found)": 0}
    for e in pool:
        origin[e.reviewed_by] = origin.get(e.reviewed_by, 0) + 1
    reviewed = len(pool) - origin["user(chat)"] - origin["user(/bad)"] - origin["auto(found)"]
    return "\n".join([
        "ALEX 성장 루프 — 모델을 바꾸지 않고 쌓이는 것 (ADR-037·055·057)",
        f"1. 기록된 경험        {total}건 ({stages or '-'})",
        f"2. 사람 검토 대기     {waiting}건 → /review 로 읽고 승인·거절",
        f"3. 배워서 쓰는 것      {len(pool)}건 — 대화에서 바로잡으신 것 {origin['user(chat)'] + origin['user(/bad)']} · "
        f"웹에서 찾은 것 {origin['auto(found)']} · 검토해 승인한 것 {reviewed}. 비슷한 질문에 최대 {core.lesson_context_limit}개 "
        f"(자동 학습 {'on' if core.auto_lessons else 'off'})",
        f"4. 평가용으로 떼어 둠  {held_out}건 (eval — 가중치 학습에서 빠지고, 모델을 잴 때는 교훈을 끄고 잰다)",
        f"5. 학습(LoRA) 후보    {len(core.storage.training_pool())}건 · 실행 기록 {core.storage.training_run_count()}건"
        f"(그중 실제 LoRA 학습 {core.storage.lora_trained_run_count()}건)",
        "LoRA 학습은 사람이 scripts/lora.py 로 실행합니다(이 PC 는 작은 4B 모델만 — 본 모델 27B 는 16GB 로 불가). "
        "학습 모델은 ALEX 설정에 자동으로 들어가지 않습니다. 내보내기 기록은 학습 완료 증거가 아닙니다.",
        f"직전 답에 쓰인 경험: {used}",
    ])


REVIEW_VERDICTS = {"승인": "approved", "approve": "approved", "approved": "approved",
                   "거절": "rejected", "reject": "rejected", "rejected": "rejected"}


def review_command(core: JarvisCore, args: str) -> str:
    """사람이 경험을 읽고 승인·거절한다. ALEX(모델)는 명령을 실행할 수 없고, 에이전트는 쓰지 않는다."""
    parts = args.split()
    if not parts:
        waiting = [e for e in core.storage.list_experiences(limit=100_000) if e.stage in {"raw", "candidate"}]
        if not waiting:
            return "검토를 기다리는 경험이 없습니다."
        lines = [f"검토 대기 {len(waiting)}건 (최근 순, 10건까지)"]
        for e in waiting[:10]:
            situation = " ".join(e.situation.split())[:60]
            lines.append(f"  {e.id[:8]}  {e.kind:<8} {e.verdict:<7} {e.split:<5} {situation}")
        lines += ["/review ID 로 전체를 읽고, /review ID 승인 또는 /review ID 거절.",
                  "승인한 답 교정은 비슷한 질문에서 참고합니다. eval 로 떼어 둔 것은 가중치 학습에서만 빠집니다."]
        return "\n".join(lines)
    prefix = parts[0].lower()
    if len(prefix) < 6:
        return "ID 는 6자 이상 적어 주세요. /review 로 목록을 볼 수 있습니다."
    matches = [e for e in core.storage.list_experiences(limit=100_000) if e.id.startswith(prefix)]
    if not matches:
        return f"경험을 찾지 못했습니다: {prefix}"
    if len(matches) > 1:
        return f"{prefix} 로 시작하는 경험이 {len(matches)}건입니다. 더 길게 적어 주세요."
    e = matches[0]
    if len(parts) == 1:
        return "\n".join([
            f"경험 {e.id}  ({e.kind}, {e.verdict}, {e.split}, 단계 {e.stage})",
            f"상황: {e.situation}", f"판단: {e.judgment}", f"이유: {e.rationale or '-'}",
            f"선택: {e.choice or '-'}", f"결과: {e.outcome or '-'}", f"바로잡은 것: {e.correction or '-'}",
            f"출처: {e.source or '-'}  모델: {e.model or '-'}",
            f"승인: /review {e.id[:8]} 승인   거절: /review {e.id[:8]} 거절",
        ])
    stage = REVIEW_VERDICTS.get(parts[1].lower())
    if stage is None:
        return "승인 또는 거절로 적어 주세요. 예: /review " + e.id[:8] + " 승인"
    core.storage.review_experience(e.id, stage, reviewed_by="user(app)")
    if stage == "rejected":
        return f"{e.id[:8]} 거절했습니다. 교훈·학습에 쓰이지 않습니다."
    if e.split == "eval" and e.kind in LESSON_KINDS:
        return f"{e.id[:8]} 승인했습니다. 비슷한 질문부터 참고합니다(eval 이라 가중치 학습에서는 빠집니다)."
    if e.split == "eval":
        return f"{e.id[:8]} 승인했습니다. eval 로 떼어 둔 경험이라 가중치 학습에는 쓰지 않고 성장을 잴 때 씁니다."
    if e.kind not in LESSON_KINDS:
        return f"{e.id[:8]} 승인했습니다. 학습 후보가 됩니다({e.kind} 경험은 대화 교훈으로는 쓰지 않습니다)."
    return f"{e.id[:8]} 승인했습니다. 다음 비슷한 질문부터 교훈으로 쓰이고, 학습 후보도 됩니다."


def _bundle_as_proposal(bundle):
    """ADR-037 편집 경험 기록은 파일 하나짜리 모양을 받는다 — 묶음은 경로를 합치고 전체 diff 를 쓴다."""
    return SimpleNamespace(proposal_id=bundle.proposal_id,
                           relative_path=", ".join(c.relative_path for c in bundle.changes),
                           diff=bundle.diff, proposed_text=bundle.diff)



# 자동 실행 권한(ADR-095): 이번 답의 수정안을 바로 적용한다. 단계 정의는 permissions.py.
_APPLY_LINE = re.compile(r"^적용: /apply-edit ([A-Za-z0-9_-]+) --yes$", re.M)


def auto_apply_proposals(core: JarvisCore, response: str) -> str:
    """'자동 실행' 권한: 이번 답에 나온 수정안을 바로 적용하고, 답을 '적용함 + 되돌리는 법' 으로 바꿔 돌려준다.

    적용 전에 원본을 백업한다(/apply-edit 과 같은 길). 사람이 diff 를 보고 승인한 것이 아니므로 '좋은 예' 경험으로는
    기록하지 않는다. ALEX 자기 프로그램 파일을 바꾸는 수정안은 적용하지 않고 사람 확인으로 남긴다(permissions.py).
    """
    if core.workspace_editor is None:
        return response
    notes, applied = [], []
    root = core.workspace_editor.allowed_root
    for proposal_id in dict.fromkeys(_APPLY_LINE.findall(response)):
        bundle = core.workspace_editor.get_bundle(proposal_id)
        proposal = None if bundle is not None else core.workspace_editor.get(proposal_id)
        paths = [c.relative_path for c in bundle.changes] if bundle is not None else [proposal.relative_path] if proposal else []
        if any(is_alex_program(root / path) for path in paths):
            notes.append(f"[자동 실행] 수정안 {proposal_id} 은 ALEX 자기 프로그램 파일을 바꾸므로 자동으로 적용하지 않았습니다. "
                         "diff 를 확인한 뒤 위의 적용 명령으로 직접 적용하세요.")
            continue
        try:
            if bundle is not None:
                targets, backup = core.workspace_editor.apply_bundle(proposal_id)
                notes.append(f"[자동 실행] 묶음 적용 — 파일 {len(targets)}개, 원본 백업: {backup}\n"
                             f"되돌리기: /undo-bundle {proposal_id} --yes")
            else:
                target, backup = core.workspace_editor.apply(proposal_id)
                notes.append(f"[자동 실행] 적용 — {target}, 원본 백업: {backup}\n"
                             f"되돌리기: /undo-file {proposal.relative_path} --yes")
        except ToolError as exc:
            notes.append(f"[자동 실행] 수정안 {proposal_id} 을 적용하지 못했습니다: {exc}")
            continue
        applied.append(proposal_id)
        DeveloperMode(core).approved(proposal_id)
    if not notes:
        return response
    for proposal_id in applied:  # 이미 적용했으니 '적용·폐기' 안내는 지운다
        response = re.sub(rf"^(?:적용: /apply-edit {proposal_id} --yes|폐기: /discard-edit {proposal_id})\n?", "",
                          response, flags=re.M)
    if applied and not _APPLY_LINE.search(response):
        response = response.replace("아직 파일에는 적용되지 않았습니다. diff를 확인하세요.\n", "")
    return response.rstrip() + "\n\n" + "\n".join(notes)


def build_image_generator(settings: Settings) -> ComfyImageGenerator | None:
    """그림 그리기(ADR-073). 꺼져 있으면 None. 폴더 설정이 비면 ALEX 폴더 옆 comfyui\\ComfyUI_windows_portable."""
    if not settings.image_generation_enabled:
        return None
    comfy_dir = Path(settings.comfyui_dir) if settings.comfyui_dir else (
        Path(__file__).resolve().parents[2] / "comfyui" / "ComfyUI_windows_portable")
    try:
        return ComfyImageGenerator(settings.data_dir / "images", url=settings.comfyui_url, comfy_dir=comfy_dir,
                                   log_dir=settings.log_dir)
    except ValueError as exc:  # 127.0.0.1 이 아닌 주소 — 밖으로 보내지 않는다
        logging.getLogger(__name__).warning("image generation disabled: %s", exc)
        return None


def handle_command(core: JarvisCore, session_id: str, text: str) -> tuple[str, bool, str]:
    """CLI 전용 명령을 처리한다. 반환: (출력, 계속 실행 여부, session_id)."""
    if (text == "/knowledge" or text.startswith(("/learn-file", "/knowledge-search", "/ask-knowledge", "/forget-knowledge"))):
        try:
            if text == "/knowledge":
                docs = core.knowledge.documents()
                output = "저장한 지식 자료가 없습니다. /learn-file 경로로 저장하세요."
                if docs:
                    output = "로컬 지식 자료(원문 참고용·가중치 학습 아님):\n" + "\n".join(
                        f"- {d['id'][:12]} · {d['title']} · {d['pages']}쪽 · {d['chars']}자" for d in docs)
            elif text == "/learn-file" or text.startswith("/learn-file "):
                path = text.removeprefix("/learn-file").strip()
                if not path:
                    raise KnowledgeError('사용법: /learn-file "C:\\자료\\강의.pdf"')
                output = core.learn_file(path)
            elif text == "/knowledge-search" or text.startswith("/knowledge-search "):
                query = text.removeprefix("/knowledge-search").strip()
                if not query:
                    raise KnowledgeError("사용법: /knowledge-search 검색어")
                passages = core.knowledge.search(query)
                output = "관련 자료를 찾지 못했습니다." if not passages else "\n\n".join(
                    f"[{n}] {p.source()}\n{p.text}" for n, p in enumerate(passages, 1))
            elif text == "/ask-knowledge" or text.startswith("/ask-knowledge "):
                payload = text.removeprefix("/ask-knowledge").strip()
                if "::" in payload:
                    did, question = (s.strip() for s in payload.split("::", 1))
                else:
                    did, question = "", payload
                output = core.ask_knowledge_local(session_id, question, did)
            elif text.startswith("/forget-knowledge "):
                payload = text.removeprefix("/forget-knowledge").strip().split()
                if len(payload) != 2 or payload[1] != "--yes":
                    raise KnowledgeError("삭제: /forget-knowledge 자료ID --yes (원본 파일은 유지)")
                core.knowledge.forget(payload[0])
                output = "저장한 지식 자료·검색 본문·사본을 삭제했습니다. 원본 파일은 유지합니다."
            else:
                raise KnowledgeError("자료 명령은 /help를 확인하세요.")
        except (KnowledgeError, OSError) as exc:
            output = f"자료 처리 실패: {exc}"
        return output, True, session_id
    if text == "/exit":
        return "종료합니다.", False, session_id

    if text == "/help":
        return HELP_TEXT, True, session_id

    if text == "/build" or text.startswith("/build "):
        request = text.removeprefix("/build").strip()
        if not request:
            return ("사용법: /build 파이썬으로 테트리스 게임 만들어줘\n"
                    "코딩 모델이 data/builds 의 작업 폴더에 파일을 만들고 검사 파일이 통과할 때까지 고칩니다. "
                    "이어서 '고쳐줘' 라고 하면 같은 폴더에서 이어서 고칩니다."), True, session_id
        answer, _ = core.handle_build(session_id, request)
        return answer, True, session_id

    # 어려운 일은 클라우드로(ADR-102). 웹은 이 명령을 뒤 작업으로 돌리며 클라우드가 일하는 동안 잠금을 푼다.
    # 말로 내려받기(ADR-104)와 같은 길 — 승인 버튼은 '/download 주소 --yes'
    if text == "/download" or text.startswith("/download "):
        rest = text.removeprefix("/download").strip()
        url = rest.removesuffix("--yes").strip()
        if not url:
            return ("사용법: /download 주소 [--yes] — data/downloads 에 받습니다. 실행 파일·1GB 넘는 파일은 --yes 로 승인해야 받고, "
                    "받은 파일은 실행하지 않습니다. 말로 '이 파일 내려받아줘 주소' 도 됩니다."), True, session_id
        matched = url + (" --yes" if rest.endswith("--yes") else "")
        return core._handle_decision(session_id, text, RouteDecision(Destination.LOCAL_GENERAL, DOWNLOAD_REASON, matched))[0], \
            True, session_id

    if text == "/cloud" or text.startswith("/cloud "):
        return core.handle_cloud(session_id, text), True, session_id
    if text == "/cloud-default" or text.startswith("/cloud-default "):
        return cloud_default(core, text.removeprefix("/cloud-default").strip().lower()), True, session_id

    if text == "/rtl-tests" or text.startswith("/rtl-tests "):
        return core.handle_rtl_tests(session_id, text), True, session_id

    if text == "/eda" or text.startswith("/eda "):
        return core.handle_eda(session_id, text), True, session_id

    if text == "/rtl" or text.startswith("/rtl "):
        parts = text.removeprefix("/rtl").split()
        resume = "--continue" in parts
        parts = [p for p in parts if p != "--continue"]
        if not parts:
            return ("사용법: /rtl 프로젝트폴더 [최대회수] [--continue]\n"
                    "rtl_root(기본 C:\\ALEX\\fabless) 아래 폴더에 rtl.json·spec.md·시험기를 두면, 코딩 모델이 Verilog 를 쓰고 "
                    "린트·시뮬레이션 결과를 보며 스스로 고칩니다. --continue 는 지금 설계 파일에서 이어서 고칩니다."), True, session_id
        rounds = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        return core.handle_rtl(session_id, parts[0], rounds=rounds, resume=resume), True, session_id

    if text.startswith("/dev-start "):
        parts = [part.strip() for part in text.removeprefix("/dev-start ").split("::", maxsplit=2)]
        if len(parts) != 3:
            return ("사용법: /dev-start jarvis/router.py :: tests/test_router.py :: 수정 목표\n"
                    "여러 파일·테스트: /dev-start jarvis/router.py, jarvis/core.py :: tests/test_router.py, tests/test_core.py :: 수정 목표"), True, session_id
        try:
            return DeveloperMode(core).start(*parts), True, session_id
        except ToolError as exc:
            return str(exc), True, session_id

    if text.startswith("/dev-resume "):
        try:
            return DeveloperMode(core).resume(text.removeprefix("/dev-resume ")), True, session_id
        except ToolError as exc:
            return str(exc), True, session_id

    if text == "/dev-check" or text.startswith("/dev-check "):
        try:
            return DeveloperMode(core).check(text.removeprefix("/dev-check")), True, session_id
        except ToolError as exc:
            return str(exc), True, session_id

    if text == "/dev-outcomes":
        return DeveloperMode(core).outcomes(), True, session_id

    if text == "/self-check" or text.startswith("/self-check "):  # 스스로 점검(2026-10-23 준비 — 사람이 부를 때만)
        if getattr(core, "member_mode", False):
            return MEMBER_BLOCKED, True, session_id
        raw = text.removeprefix("/self-check").strip() or "5"
        if not raw.isdigit():
            return "사용법: /self-check [개수 1~20] — ALEX 가 자기 기능 질문을 스스로 풀고 틀린 것을 교훈으로 남김", True, session_id
        from .selfcheck import run as self_check
        return self_check(core, int(raw), say=lambda note: core._progress(note)), True, session_id

    if text == "/dev-insights" or text.startswith("/dev-insights ") or text == "/dev-learn":
        try:
            mode = DeveloperMode(core)
            if text == "/dev-learn":
                return mode.learn(), True, session_id
            return mode.insights(text.removeprefix("/dev-insights").strip()), True, session_id
        except ToolError as exc:
            return str(exc), True, session_id

    if text in ("/dev-status", "/dev-next", "/dev-propose", "/dev-verify", "/dev-changes", "/dev-list", "/dev-auto"):
        try:
            mode = DeveloperMode(core)
            if text == "/dev-status":
                output = mode.status()
            elif text == "/dev-propose":
                output = mode.propose(session_id)
            elif text == "/dev-verify":
                output = mode.verify()
            elif text == "/dev-changes":
                output = mode.changes()
            elif text == "/dev-list":
                output = mode.history()
            elif text == "/dev-auto":
                output = mode.auto(session_id)
            else:
                output = mode.next(session_id)
            return output, True, session_id
        except ToolError as exc:
            return str(exc), True, session_id

    if text == "/new":
        session_id = core.get_or_create_session(resume=False, owner=core.storage.session_owner(session_id) or OWNER)
        core.last_route_event_id = None
        return f"새 세션: {session_id}", True, session_id

    if text == "/profile":
        return core.system_info_tool.run(), True, session_id

    if text == "/open-vscode" or text.startswith("/open-vscode "):
        if core.vscode_launcher is None:
            return (
                "VS Code 실행은 기본 비활성입니다. JARVIS_VSCODE_LAUNCH_ENABLED=1 을 설정하세요.",
                True, session_id,
            )
        if text.strip() != "/open-vscode --yes":
            return "실행 확인이 필요합니다. 사용법: /open-vscode --yes", True, session_id
        try:
            return core.vscode_launcher.run(), True, session_id
        except DeviceError as exc:
            return str(exc), True, session_id

    if text == "/wake-desktop" or text.startswith("/wake-desktop "):
        if core.wake_on_lan_tool is None:
            return (
                "Wake-on-LAN은 기본 비활성입니다. JARVIS_WOL_ENABLED=1 과 "
                "JARVIS_WOL_MAC을 설정하세요.", True, session_id
            )
        if text.strip() != "/wake-desktop --yes":
            return "전송 확인이 필요합니다. 사용법: /wake-desktop --yes", True, session_id
        try:
            return core.wake_on_lan_tool.run(), True, session_id
        except DeviceError as exc:
            return str(exc), True, session_id

    if text == "/status":
        title = core.storage.session_title(session_id)
        project = core.current_project_root()
        tasks = core.storage.list_tasks(limit=20, owner=core.storage.session_owner(session_id) or OWNER)
        high = sum(1 for item in tasks if item.priority == 1)
        # 실제로 무엇이 대답하고 있는지. 2026-09-21: 설정의 coder_model 이 laptop
        # single-model 모드에서 조용히 무시되는데 어느 화면에도 안 나와서, ADR-047 이
        # 정한 모델이 몇 시간 동안 안 쓰이고 있는 것을 아무도 몰랐다.
        def model_name(provider: object) -> str:
            # core._environment_line 과 같은 규칙: OllamaProvider 는 .model, Mock/Cloud 는 .name
            return str(getattr(provider, "model", "") or getattr(provider, "name", "") or "-")

        general_provider = core.providers.get(Destination.LOCAL_GENERAL)
        coder_provider = core.providers.get(Destination.LOCAL_CODER)
        coder_line = f"coder_model: {model_name(coder_provider)}"
        if general_provider is not None and general_provider is coder_provider:
            coder_line += "  (general 공유 - 설정의 coder_model 은 쓰이지 않습니다)"
        lines = [
            f"profile: {core.hardware.profile.value}",
            f"general_model: {model_name(general_provider)}",
            coder_line,
            f"session: {session_id}",
            f"session_title: {title or '-'}",
            f"project: {project}",
            f"open_tasks: {len(tasks)} (high={high})",
            # 켜져 있는지 모르면 쓸 수 없다(ADR-049 와 같은 이유).
            f"web: {('on (/search, /web, 스스로 검색)' if core._web_tool_active() else 'on (/search, /web)') if core.web_reader is not None else 'off'}",
            # 교훈은 프롬프트를 바꾼다 — 측정 전에 읽는 줄에 있어야 한다(ADR-055).
            (f"lessons: 최대 {core.lesson_context_limit}개 / 배운 것 {len(lesson_pool(core))}건 · "
             f"자동 학습 {'on' if core.auto_lessons else 'off'} (/growth)"
             if core.lesson_context_limit > 0 else "lessons: off"),
        ]
        for label, provider in (("general", general_provider), ("coder", coder_provider)):
            if provider is None or (label == "coder" and provider is general_provider):
                continue
            runtime = provider_runtime_settings(provider)
            if "num_ctx" in runtime:
                lines.append(f"{label}_runtime: " + " ".join(
                    f"{key}={value}" for key, value in runtime.items() if key != "model"
                ))
        if getattr(core, "coding_auto_attempts", 1) > 1:  # ADR-077
            lines.append(f"dev_auto: 독립 풀이 최대 {core.coding_auto_attempts}번(풀이마다 최대 3회차)")
        if getattr(core, "coding_regression_check", False):  # ADR-078
            lines.append("dev_auto: 회귀 검사 켬(합격 테스트 통과 뒤 전체 테스트)")
        if getattr(core, "coding_diagnostic_context", False):  # ADR-080
            lines.append("dev_auto: 읽기 전용 진단 근거 켬(실패 문구의 조건·테스트 준비 코드)")
        if getattr(core, "coding_num_ctx", 0):  # ADR-076 — 코딩 수정안만 다른 문맥으로 돈다
            lines.append(f"coding_runtime: num_ctx={core.coding_num_ctx} num_gpu={core.coding_num_gpu or 'auto'} "
                         f"chars={core.coding_context_char_budget or core.context_char_budget}")
        if tasks:
            lines.append("next_tasks:")
            lines.extend(f"  #{item.task_id} p{item.priority} {item.text}" for item in tasks[:5])
        return "\n".join(lines), True, session_id

    if text == "/gpu":
        # 추정하지 않고 Ollama 가 보고하는 실제 배치를 읽는다.
        lines: list[str] = [f"profile: {core.hardware.profile.value}"]
        seen: set[int] = set()
        found = False
        pairs = (
            ("general", core.providers.get(Destination.LOCAL_GENERAL)),
            ("coder", core.providers.get(Destination.LOCAL_CODER)),
        )
        for label, provider in pairs:
            if provider is None:
                continue
            place = getattr(provider, "placement", None)
            if id(provider) in seen:
                # 같은 객체를 두 번 재지 않는다. 다만 조용히 빼면 coder 설정이
                # 무시되고 있다는 사실까지 같이 사라진다(2026-09-21).
                lines.append(f"{label}: general 과 같은 모델을 씁니다 - 설정의 coder_model 은 쓰이지 않습니다")
                continue
            if place is None:
                continue
            seen.add(id(provider))
            info = place()
            if not info:
                lines.append(f"{label}: {getattr(provider, 'model', '?')} — 로드되어 있지 않습니다")
                continue
            found = True
            total = info["total_bytes"] / GIB
            vram = info["vram_bytes"] / GIB
            ram = info["ram_bytes"] / GIB
            gpu_pct = (info["vram_bytes"] / info["total_bytes"]) if info["total_bytes"] else 0
            lines.append(
                f"{label}: {info['model']}  전체 {total:.2f}GB  "
                f"GPU {vram:.2f}GB({gpu_pct:.0%})  RAM {ram:.2f}GB"
            )
            report = offload_report(info, core.ram_offload_budget_gb)
            if report:
                lines.append(f"  [{report[0]}] {report[1]}")
        if found and all("RAM 0.00GB" in line for line in lines if line.startswith(("general:", "coder:"))):
            lines.append("전부 GPU 에 올라가 있습니다.")
        lines.append(f"RAM 넘김 허용치: {core.ram_offload_budget_gb:.1f}GB "
                     f"(JARVIS_RAM_OFFLOAD_BUDGET_GB)")
        return "\n".join(lines), True, session_id

    if text == "/doctor":
        return core.diagnostics(), True, session_id

    if text == "/image-prompt" or text.startswith("/image-prompt ") or text.startswith("/image-prompt\n"):
        prompt = text.removeprefix("/image-prompt").strip()
        model = re.match(r"--model=(zimage|qwen|anime)(?:\s+|$)", prompt)
        if model:
            prompt = prompt[model.end():].strip()
        if not prompt:
            return "사용법: /image-prompt A cat sitting beside a window at sunset", True, session_id
        if len(prompt) > 4000:
            return "직접 입력 프롬프트는 4000자 이하로 적어 주세요.", True, session_id
        return core.handle_forced(session_id, prompt, Destination.TOOL_IMAGE_GEN, reason="direct media prompt",
                                  matched=model.group(1) if model else "manual")[0], True, session_id

    # 월별 대화 기록(ADR-100)
    if text == "/archive":
        added = core.archive.sync()
        files = sorted(core.archive.folder(OWNER).glob("*.md"))
        listing = "\n".join(f"  {path.name} ({path.stat().st_size // 1024:,} KB)" for path in files[-14:]) or "  (아직 없음)"
        return (f"월별 대화 기록: {core.archive.folder(OWNER)}\n방금 {added}개 메시지를 덧붙였습니다.\n{listing}\n"
                "한 달에 파일 하나(글만). 1월 1일이 지나면 /archive-year 연도 로 연간 보고서를 만듭니다."), True, session_id
    if text.startswith("/archive-year"):
        year_text = text.removeprefix("/archive-year").strip()
        if not year_text.isdigit():
            return "사용법: /archive-year 2026", True, session_id
        if not core.archive.months(int(year_text)):
            return f"{year_text}년 월별 기록이 없습니다.", True, session_id
        core.archive.sync()
        summarizer = core.local_summarizer
        path = core.archive.year_report(int(year_text), lambda messages: summarizer.generate(messages),
                                        progress=getattr(core, "_progress", None))
        return f"연간 보고서를 만들었습니다: {path}", True, session_id

    # 영상 명령(ADR-099): 영상 속 대상 바꾸기·긴 영상. 대화 기록에 남도록 일반 영상과 같은 길(handle_forced)로.
    for command, reason in (("/video-swap", "video swap"), ("/video-long", "video long")):
        if text == command or text.startswith(command + " ") or text.startswith(command + "\n"):
            return core.handle_forced(session_id, text, Destination.TOOL_VIDEO_GEN, reason=reason,
                                      matched=command)[0], True, session_id

    if text == "/video-prompt" or text.startswith("/video-prompt ") or text.startswith("/video-prompt\n"):
        prompt = text.removeprefix("/video-prompt").strip()
        model = re.match(r"--model=(ltx|wan)(?:\s+|$)", prompt)
        if model:
            prompt = prompt[model.end():].strip()
        elif prompt.startswith("--model="):
            return "영상 모델은 ltx 또는 wan을 선택하세요: /video-prompt --model=ltx 프롬프트", True, session_id
        if not prompt:
            return "사용법: /video-prompt [--model=ltx|wan] A cat runs through a garden, camera follows", True, session_id
        if len(prompt) > 4000:
            return "직접 입력 프롬프트는 4000자 이하로 적어 주세요.", True, session_id
        return core.handle_forced(session_id, prompt, Destination.TOOL_VIDEO_GEN,
                                  reason="direct media prompt", matched=model.group(1) if model else "manual")[0], True, session_id

    if text == "/image" or text.startswith("/image "):
        description = text.removeprefix("/image").strip()
        if not description:
            return "무엇을 그릴까요? 예: /image 노을 지는 바닷가 — 그냥 '노을 지는 바닷가 그려줘' 라고 말해도 됩니다.", True, session_id
        return core.handle_forced(session_id, description, Destination.TOOL_IMAGE_GEN, reason="/image command")[0], True, session_id

    if text == "/characters" or text == "/character" or text.startswith("/character "):
        generator = core.image_generator
        if generator is None:
            return "그림 그리기가 꺼져 있습니다.", True, session_id
        parts = text.split(maxsplit=2)
        if len(parts) == 3 and parts[1] == "save":
            return core.save_character(session_id, parts[2].strip()), True, session_id
        if len(parts) == 3 and parts[1] == "add":
            name, _, source = parts[2].strip().partition(" ")
            if not source.strip():
                return "사용법: /character add 이름 그림주소(또는 파일 경로) — 예: /character add 흑태자 https://…/image.png", True, session_id
            return core.add_character(session_id, name, source.strip()), True, session_id
        if len(parts) == 3 and parts[1] == "delete":
            deleted = generator.characters.delete(parts[2].strip())
            return (f"'{parts[2].strip()}' 캐릭터를 지웠습니다(그림 원본은 data/images 에 남아 있습니다)." if deleted
                    else f"'{parts[2].strip()}' 라는 캐릭터가 없습니다. /characters 로 목록을 보세요."), True, session_id
        names = generator.characters.names()
        if not names:
            return "저장한 캐릭터가 없습니다. 그림을 그린 뒤 '이 캐릭터를 은비로 저장해줘'라고 해 보세요.", True, session_id
        rows = [f"- {name} (저장 {generator.characters.get(name).get('saved_at', '')})" for name in names]
        return "저장한 캐릭터\n" + "\n".join(rows) + "\n이름을 넣어 말하면 같은 모습으로 그립니다. 예: '" + names[0] + " 바닷가에 있는 장면 그려줘'", True, session_id

    if text == "/unload":
        from .providers import OllamaProvider, ProviderError, loaded_local_models, unload_local_models
        # 그림 모델(ComfyUI)도 그래픽카드를 쓴다 — 게임 전에 누르는 버튼이니 RAM 까지 비운다(ADR-073)
        if core.image_generator is not None and core.image_generator.free(release_ram=True):
            comfy_note = " 그림 모델(ComfyUI)도 내렸습니다."
        else:
            comfy_note = ""
        servers = {p.host: p for p in core.providers.values() if isinstance(p, OllamaProvider)}  # Ollama 또는 Strata
        if not servers:
            return "로컬 Ollama 모델을 쓰지 않는 설정입니다." + comfy_note, True, session_id
        backend_label = " / ".join(sorted({"Strata" if isinstance(p, StrataProvider) else "Ollama"
                                            for p in servers.values()}))
        try:
            names = [name for host in sorted(servers) for name in unload_local_models(servers[host])]
            remaining = [name for host in sorted(servers) for name in loaded_local_models(servers[host])]
        except ProviderError as exc:
            return str(exc) + comfy_note, True, session_id
        # 요청만으로 비었다고 단정하지 않는다 — /api/ps 로 다시 확인하고, 그 결과를 먼저 알린다(Codex 리뷰·2차 R2-06:
        # 처음엔 비었는데 그사이 다른 요청이 모델을 올린 경우도 있다)
        if remaining:
            asked = f"내리기를 요청했습니다: {', '.join(names)}. " if names else "처음 확인할 때는 올라간 모델이 없었습니다. "
            return (f"{asked}지금 올라가 있는 모델: {', '.join(remaining)} — 답을 만드는 중이거나 다른 요청이 올린 것입니다. "
                    "잠시 뒤 다시 누르세요." + comfy_note, True, session_id)
        if not names:
            return (f"{backend_label} 에 올라간 모델이 없습니다(방금 확인한 {backend_label} 목록 기준 — 다른 프로그램이 쓰는 그래픽카드 메모리는 따로입니다)."
                    + comfy_note, True, session_id)
        return (f"모델을 내렸습니다: {', '.join(names)} — {backend_label} 가 쓰던 그래픽카드 메모리를 비웠습니다(다른 프로그램 몫은 따로). "
                "다음 질문 때 다시 올라옵니다(수십 초)." + comfy_note, True, session_id)

    if text == "/backup":
        path = core.storage.backup_database()
        return f"백업 완료: {path}", True, session_id

    if text == "/state-export" or text.startswith("/state-export "):
        raw = text.removeprefix("/state-export").strip()
        destination = Path(raw) if raw else core.storage.db_path.parent / "exports" / "jarvis-portable-state.json"
        try:
            path = export_portable_state(core.storage, destination)
        except PortableStateError as exc:
            return str(exc), True, session_id
        return f"Portable state export 완료: {path}", True, session_id

    if text.startswith("/state-import "):
        payload = text.removeprefix("/state-import ").strip()
        if not payload.endswith(" --yes"):
            return "Import 확인이 필요합니다. 사용법: /state-import 파일.json --yes", True, session_id
        source = Path(payload[:-6].strip())
        try:
            stats = import_portable_state(core.storage, source)
        except PortableStateError as exc:
            return str(exc), True, session_id
        return "Portable state import 완료: " + ", ".join(f"{k}={v}" for k, v in stats.items()), True, session_id

    if text == "/summary":
        summary, through_id = core.storage.get_session_summary(session_id)
        if not summary:
            return "아직 생성된 대화 요약이 없습니다.", True, session_id
        return f"summary_through_id={through_id}\n{summary}", True, session_id

    if text.startswith("/use "):
        target = text.removeprefix("/use ").strip()
        # 다른 사용자의 대화는 없는 것과 같다(여러 사람이 쓰기, 2026-10-11)
        if core.storage.session_owner(target) != (core.storage.session_owner(session_id) or OWNER):
            return f"세션을 찾을 수 없습니다: {target}", True, session_id
        core.last_route_event_id = None
        return f"세션 전환: {target}", True, target

    if text.startswith("/rename "):
        title = text.removeprefix("/rename ").strip()
        try:
            core.storage.rename_session(session_id, title)
        except StorageError as exc:
            return str(exc), True, session_id
        return f"세션 제목 변경: {title}", True, session_id

    if text.startswith("/delete-session "):
        payload = text.removeprefix("/delete-session ").strip()
        if not payload.endswith(" --yes"):
            return "삭제 확인이 필요합니다. 사용법: /delete-session 세션ID --yes", True, session_id
        target = payload[:-6].strip()
        if target == "current":
            target = session_id
        if not target:
            return "삭제할 세션 ID가 필요합니다.", True, session_id
        owner = core.storage.session_owner(session_id) or OWNER
        try:
            removed = core.storage.session_owner(target) == owner and core.storage.delete_session(target)
        except StorageError as exc:
            return str(exc), True, session_id
        if not removed:
            return f"세션을 찾을 수 없습니다: {target}", True, session_id
        core.forget_session_documents(target)  # 그 대화에만 올린 문서도(ADR-096)
        if target == session_id:
            session_id = core.get_or_create_session(resume=False, owner=owner)
            core.last_route_event_id = None
            return f"현재 세션을 삭제하고 새 세션을 만들었습니다: {session_id}", True, session_id
        return f"세션 삭제 완료: {target}", True, session_id

    if text == "/export":
        path = core.storage.export_session_markdown(session_id)
        return f"세션 export 완료: {path}", True, session_id

    if text.startswith("/export ") or text == "/document" or text.startswith("/document "):
        try:
            output = command_export(core.storage, session_id, text)
        except (DocumentError, StorageError, OSError) as exc:
            output = f"문서 저장 실패: {exc}"
        return output, True, session_id

    if text == "/sessions":
        sessions = core.storage.list_sessions(owner=core.storage.session_owner(session_id) or OWNER)
        if not sessions:
            return "저장된 세션이 없습니다.", True, session_id
        lines = [
            f"{s.session_id}  title={s.title or '-'}  messages={s.message_count}  "
            f"summary={'Y' if s.has_summary else 'N'}  updated={s.updated_at}"
            for s in sessions
        ]
        return "\n".join(lines), True, session_id

    if text == "/memories":
        memories = core.storage.list_memories(owner=core.storage.session_owner(session_id) or OWNER)
        if not memories:
            return "저장된 메모가 없습니다.", True, session_id
        return "\n".join(f"{k} = {v}" for k, v in memories.items()), True, session_id

    if text.startswith("/remember "):
        payload = text.removeprefix("/remember ")
        if "=" not in payload:
            return "사용법: /remember key=value", True, session_id
        key, value = payload.split("=", maxsplit=1)
        try:
            core.storage.remember(key, value, owner=core.storage.session_owner(session_id) or OWNER)
        except StorageError as exc:
            return str(exc), True, session_id
        return "기억했습니다.", True, session_id

    if text.startswith("/recall "):
        value = core.storage.recall(text.removeprefix("/recall "), owner=core.storage.session_owner(session_id) or OWNER)
        return (value if value is not None else "저장된 값이 없습니다."), True, session_id

    if text.startswith("/forget "):
        removed = core.storage.forget(text.removeprefix("/forget "), owner=core.storage.session_owner(session_id) or OWNER)
        return ("삭제했습니다." if removed else "해당 key 가 없습니다."), True, session_id

    if text.startswith("/task-add "):
        payload = text.removeprefix("/task-add ").strip()
        parts = payload.split(maxsplit=1)
        priority = 2
        task_text = payload
        if parts and parts[0] in {"1", "2", "3"}:
            priority = int(parts[0])
            task_text = parts[1] if len(parts) > 1 else ""
        try:
            task_id = core.storage.add_task(task_text, priority, owner=core.storage.session_owner(session_id) or OWNER)
        except StorageError as exc:
            return str(exc), True, session_id
        return f"할 일 추가: #{task_id} (priority={priority}) {task_text}", True, session_id

    if text == "/tasks" or text == "/tasks all":
        items = core.storage.list_tasks(include_done=text.endswith(" all"), owner=core.storage.session_owner(session_id) or OWNER)
        if not items:
            return "할 일이 없습니다.", True, session_id
        labels = {1: "HIGH", 2: "NORMAL", 3: "LOW"}
        return "\n".join(
            f"#{item.task_id} [{'x' if item.status == 'done' else ' '}] {labels[item.priority]} {item.text}"
            for item in items
        ), True, session_id

    if text.startswith("/task-done ") or text.startswith("/task-open "):
        reopen = text.startswith("/task-open ")
        raw = text.removeprefix("/task-open " if reopen else "/task-done ").strip()
        try:
            task_id = int(raw)
            changed = core.storage.set_task_status(task_id, "open" if reopen else "done", owner=core.storage.session_owner(session_id) or OWNER)
        except (ValueError, StorageError) as exc:
            return f"Task 처리 오류: {exc}", True, session_id
        return (f"Task #{task_id} {'다시 열기' if reopen else '완료'}" if changed else f"Task #{task_id} 를 찾을 수 없습니다."), True, session_id

    if text.startswith("/task-delete "):
        payload = text.removeprefix("/task-delete ").strip()
        if not payload.endswith(" --yes"):
            return "삭제 확인이 필요합니다. 사용법: /task-delete ID --yes", True, session_id
        try:
            task_id = int(payload[:-6].strip())
            changed = core.storage.delete_task(task_id, owner=core.storage.session_owner(session_id) or OWNER)
        except (ValueError, StorageError) as exc:
            return f"Task 삭제 오류: {exc}", True, session_id
        return (f"Task #{task_id} 삭제" if changed else f"Task #{task_id} 를 찾을 수 없습니다."), True, session_id

    if text == "/project":
        root = core.current_project_root()
        return f"현재 프로젝트 루트: {root}", True, session_id

    if text.startswith("/project "):
        try:
            root = core.set_project_root(text.removeprefix("/project "))
        except ToolError as exc:
            return str(exc), True, session_id
        return f"프로젝트 루트 전환: {root}", True, session_id

    if text == "/tree" or text.startswith("/tree "):
        if core.project_tree_tool is None:
            return "프로젝트 tree 도구가 비활성 상태입니다.", True, session_id
        raw = text.removeprefix("/tree").strip()
        try:
            depth = int(raw) if raw else core.project_tree_tool.max_depth
            return core.project_tree_tool.run(depth=depth), True, session_id
        except (ValueError, ToolError) as exc:
            return f"tree 사용 오류: {exc}", True, session_id

    if text.startswith("/find "):
        if core.file_search_tool is None:
            return "파일 검색 도구가 비활성 상태입니다.", True, session_id
        try:
            return core.file_search_tool.run(query=text.removeprefix("/find ")), True, session_id
        except ToolError as exc:
            return str(exc), True, session_id

    if text.startswith("/read "):
        if core.file_read_tool is None:
            return "파일 읽기 도구가 비활성 상태입니다.", True, session_id
        try:
            return core.file_read_tool.run(path=text.removeprefix("/read ")), True, session_id
        except ToolError as exc:
            return str(exc), True, session_id

    if text.startswith("/ask-file "):
        payload = text.removeprefix("/ask-file ").strip()
        if "::" not in payload:
            return "사용법: /ask-file jarvis/router.py :: 이 코드 설명해줘", True, session_id
        relative_path, question = (part.strip() for part in payload.split("::", maxsplit=1))
        try:
            return core.ask_file_local(session_id, relative_path, question), True, session_id
        except ToolError as exc:
            return str(exc), True, session_id

    if text in ("/research", "/deepsearch") or text.startswith(("/research ", "/deepsearch ")):
        return core.research_local(session_id, text.partition(" ")[2]), True, session_id

    if text == "/search" or text.startswith("/search "):
        return core.web_search_local(session_id, text.removeprefix("/search")), True, session_id

    if text == "/web" or text.startswith("/web "):
        payload = text.removeprefix("/web").strip()
        if not payload:
            return "사용법: /web https://example.com/docs :: 설치 방법 알려줘", True, session_id
        # 주소에는 공백이 없다. 첫 토큰을 주소로 보고 나머지에서 '::' 를 뗀다.
        # 공백 없이 붙여 쓴 'url::질문' 도 받되, IPv6 주소([::1])의 '::' 는 건드리지 않는다.
        url, _, rest = payload.partition(" ")
        if not rest and "::" in url and "[" not in url:
            url, _, rest = url.partition("::")
        question = rest.strip().removeprefix("::").strip()
        return core.web_read_local(session_id, url, question), True, session_id

    if text.startswith("/grep "):
        if core.project_context_tool is None:
            return "프로젝트 컨텍스트 도구가 비활성 상태입니다.", True, session_id
        try:
            return core.project_context_tool.run(query=text.removeprefix("/grep ")), True, session_id
        except ToolError as exc:
            return str(exc), True, session_id

    if text.startswith("/ask-project "):
        question = text.removeprefix("/ask-project ").strip()
        try:
            return core.ask_project_local(session_id, question), True, session_id
        except ToolError as exc:
            return str(exc), True, session_id

    if text.startswith("/edit-file "):
        payload = text.removeprefix("/edit-file ").strip()
        if "::" not in payload:
            return "사용법: /edit-file jarvis/router.py :: 수정 지시", True, session_id
        relative_path, instruction = (part.strip() for part in payload.split("::", maxsplit=1))
        try:
            proposal = core.propose_file_edit_local(session_id, relative_path, instruction)
        except ToolError as exc:
            return str(exc), True, session_id
        return (
            f"편집 제안 ID: {proposal.proposal_id}\n"
            "아직 파일에는 적용되지 않았습니다. diff를 확인하세요.\n"
            f"{proposal.diff}\n"
            f"적용: /apply-edit {proposal.proposal_id} --yes\n"
            f"폐기: /discard-edit {proposal.proposal_id}",
            True,
            session_id,
        )

    if text.startswith("/edit-files "):
        payload = text.removeprefix("/edit-files ").strip()
        if "::" not in payload:
            return "사용법: /edit-files jarvis/router.py, tests/test_router.py :: 수정 지시", True, session_id
        paths, instruction = (part.strip() for part in payload.split("::", maxsplit=1))
        try:
            bundle = core.propose_bundle_local(session_id, paths.split(","), instruction)
        except ToolError as exc:
            return str(exc), True, session_id
        listing = "\n".join(f"  {'새 파일' if c.action == 'create' else '수정'}: {c.relative_path}" for c in bundle.changes)
        return (
            f"편집 제안 ID: {bundle.proposal_id}\n"
            f"묶음 수정안 — 파일 {len(bundle.changes)}개. 적용하면 전부 바뀌고, 하나라도 실패하면 모두 되돌립니다.\n"
            f"{listing}\n아직 파일에는 적용되지 않았습니다. diff를 확인하세요.\n{bundle.diff}\n"
            f"적용: /apply-edit {bundle.proposal_id} --yes\n"
            f"폐기: /discard-edit {bundle.proposal_id}\n"
            f"적용 뒤 통째로 되돌리기: /undo-bundle {bundle.proposal_id} --yes",
            True,
            session_id,
        )

    if text.startswith("/apply-edit "):
        if core.workspace_editor is None:
            return "Workspace 편집 기능이 비활성 상태입니다.", True, session_id
        payload = text.removeprefix("/apply-edit ").strip()
        if not payload.endswith(" --yes"):
            return "적용 확인이 필요합니다. 사용법: /apply-edit 제안ID --yes", True, session_id
        proposal_id = payload[:-6].strip()
        # ADR-037. apply 가 제안을 소비하므로 미리 잡아 둔다.
        try:
            bundle = core.workspace_editor.get_bundle(proposal_id)
            if bundle is not None:
                targets, backup = core.workspace_editor.apply_bundle(proposal_id)
            else:
                proposal = core.workspace_editor.get(proposal_id)
                target, backup = core.workspace_editor.apply(proposal_id)
        except ToolError as exc:
            return str(exc), True, session_id
        if bundle is not None:
            core.record_edit_experience(_bundle_as_proposal(bundle), "good", note="사용자가 묶음을 적용함")
            DeveloperMode(core).approved(proposal_id)
            return (f"묶음 적용 완료 — 파일 {len(targets)}개:\n" + "\n".join(f"  {t}" for t in targets)
                    + f"\n원본 백업: {backup}\n통째로 되돌리기: /undo-bundle {proposal_id} --yes"), True, session_id
        core.record_edit_experience(proposal, "good", note="사용자가 적용함")
        DeveloperMode(core).approved(proposal_id)
        return f"편집 적용 완료: {target}\n원본 백업: {backup}", True, session_id

    if text.startswith("/undo-bundle "):
        if core.workspace_editor is None:
            return "Workspace 편집 기능이 비활성 상태입니다.", True, session_id
        payload = text.removeprefix("/undo-bundle ").strip()
        if not payload.endswith(" --yes"):
            return "되돌리기 확인이 필요합니다. 사용법: /undo-bundle 제안ID --yes", True, session_id
        try:
            restored = core.workspace_editor.undo_bundle(payload[:-6].strip())
        except ToolError as exc:
            return str(exc), True, session_id
        return "묶음을 되돌렸습니다:\n" + "\n".join(f"  {line}" for line in restored), True, session_id

    if text.startswith("/discard-edit "):
        if core.workspace_editor is None:
            return "Workspace 편집 기능이 비활성 상태입니다.", True, session_id
        proposal_id = text.removeprefix("/discard-edit ").strip()
        try:
            bundle = core.workspace_editor.get_bundle(proposal_id)
            proposal = _bundle_as_proposal(bundle) if bundle else core.workspace_editor.get(proposal_id)  # discard 전에 잡는다
            discarded = core.workspace_editor.discard(proposal_id)
        except ToolError as exc:
            return str(exc), True, session_id
        if discarded:
            core.record_edit_experience(proposal, "bad", note="사용자가 폐기함")
            DeveloperMode(core).discarded(proposal_id)
        return ("편집 제안을 폐기했습니다." if discarded else "편집 제안을 찾을 수 없습니다."), True, session_id

    if text.startswith("/undo-file "):
        if core.workspace_editor is None:
            return "Workspace 편집 기능이 비활성 상태입니다.", True, session_id
        payload = text.removeprefix("/undo-file ").strip()
        if not payload.endswith(" --yes"):
            return "복구 확인이 필요합니다. 사용법: /undo-file 상대/경로.py --yes", True, session_id
        relative_path = payload[:-6].strip()
        try:
            target, restored_from = core.workspace_editor.restore_latest(relative_path)
        except ToolError as exc:
            return str(exc), True, session_id
        return f"파일 복구 완료: {target}\n복구 원본: {restored_from}", True, session_id

    if text.startswith("/route "):
        query = text.removeprefix("/route ").strip()
        return explain(route_request(query)), True, session_id

    if text.startswith("/ask-local "):
        query = text.removeprefix("/ask-local ").strip()
        if not query:
            return "사용법: /ask-local 질문", True, session_id
        routed = route_request(query).destination
        destination = (
            Destination.LOCAL_CODER
            if routed in {Destination.LOCAL_CODER, Destination.CLOUD_CODER}
            else Destination.LOCAL_GENERAL
        )
        response, _ = core.handle_forced(session_id, query, destination)
        return response, True, session_id

    if text.startswith("/ask-cloud "):
        query = text.removeprefix("/ask-cloud ").strip()
        if not query:
            return "사용법: /ask-cloud 질문", True, session_id
        routed = route_request(query).destination
        destination = (
            Destination.CLOUD_CODER
            if routed in {Destination.LOCAL_CODER, Destination.CLOUD_CODER}
            else Destination.CLOUD_REASONING
        )
        response, _ = core.handle_forced(session_id, query, destination)
        return response, True, session_id

    if text == "/good":
        return core.record_answer_feedback("good"), True, session_id

    if text == "/bad" or text.startswith("/bad "):
        note = text.removeprefix("/bad").strip()
        return core.record_answer_feedback("bad", note), True, session_id

    if text == "/experiences":
        counts = core.storage.experience_counts()
        if not counts:
            return "아직 기록된 경험이 없습니다. /good 또는 /bad 로 남겨 보세요.", True, session_id
        broken = core.storage.verify_experience_integrity()
        lines = ["경험 저장소 (ADR-037)"]
        lines += [f"  {k:<18} {v}건" for k, v in sorted(counts.items())]
        lines.append(f"  학습 가능(approved/train)  {len(core.storage.training_pool())}건")
        lines.append("  무결성  " + ("이상 없음" if not broken else f"변조 {len(broken)}건: {broken[:3]}"))
        return "\n".join(lines), True, session_id

    if text == "/growth":
        return growth_report(core), True, session_id

    if text == "/review" or text.startswith("/review "):
        return review_command(core, text.removeprefix("/review").strip()), True, session_id

    if text == "/undo-learning" or text.startswith("/undo-learning "):
        return core.undo_auto_learning(text.removeprefix("/undo-learning").strip()), True, session_id

    if text == "/wrong" or text.startswith("/wrong "):
        correction = text.removeprefix("/wrong").strip() or "unspecified"
        return core.mark_last_route_wrong(correction), True, session_id

    if text == "/route-stats":
        counts = core.storage.route_feedback_counts()
        if not counts:
            return "아직 기록된 오분류가 없습니다.", True, session_id
        return "\n".join(f"{destination}: {count}" for destination, count in counts.items()), True, session_id

    return f"알 수 없는 명령입니다: {text} (/help 참고)", True, session_id


def _make_cli_chunk_callback(state: dict[str, bool]):
    """Return a tiny UI callback so Ollama can stream without coupling Core to print()."""
    def emit(chunk: str) -> None:
        if not state.get("used"):
            print("\n[ALEX] ", end="", flush=True)
            state["used"] = True
        print(chunk, end="", flush=True)

    return emit


def main() -> None:
    stream_state = {"used": False}
    try:
        settings = load_settings()
        chunk_callback = (
            _make_cli_chunk_callback(stream_state)
            if settings.use_ollama and settings.stream_output
            else None
        )
        core, notes = build_core(
            settings,
            approval_callback=cli_cloud_approval,
            response_chunk_callback=chunk_callback,
        )
    except (ConfigError, StorageError) as exc:
        print(f"시작할 수 없습니다.\n{exc}")
        return

    session_id = core.get_or_create_session(resume=True)
    print(BANNER)
    print(f"Profile: {core.hardware.profile.value} | Session: {session_id}")
    for note in notes:
        print(f"- {note}")
    print("명령어는 /help 로 확인하세요.")

    while True:
        try:
            text = input("\nYou > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n종료합니다.")
            break

        if not text:
            continue

        if text.startswith("/"):
            output, keep_going, session_id = handle_command(core, session_id, text)
            print(output)
            if not keep_going:
                break
            continue

        stream_state["used"] = False
        try:
            response, decision = core.handle(session_id, text)
        except StorageError as exc:
            print(f"저장소 오류: {exc}")
            continue
        if stream_state["used"]:
            print(f"\n[route={decision.destination.value}]")
        else:
            print(f"\n[{decision.destination.value}] {response}")


if __name__ == "__main__":
    main()
