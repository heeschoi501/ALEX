"""Small configuration layer for Project ALEX.

Study focus:
- dataclass
- pathlib.Path
- environment variables
- 입력값 검증(validation)과 사용자 친화적 오류 메시지
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse


class ConfigError(ValueError):
    """설정값이 잘못되었을 때 사용자에게 보여줄 오류."""


@dataclass(slots=True)
class Settings:
    """Runtime settings kept intentionally small."""

    app_name: str = "Project ALEX"
    data_dir: Path = Path("data")
    log_dir: Path = Path("logs")
    log_max_bytes: int = 2_000_000
    log_backup_count: int = 3
    database_name: str = "jarvis.db"
    history_limit: int = 12
    context_char_budget: int = 0  # 0 = Hardware Profile 자동값
    force_profile: str = ""

    # Local provider
    use_ollama: bool = False
    local_only: bool = True
    # 웹 읽기(/search, /web). local_only 와 별개다 — local_only 는 클라우드 LLM 경계이고
    # 웹 페이지를 읽는 것은 LLM 호출이 아니다(ADR-051).
    web_enabled: bool = True
    # 웹 읽기가 켜져 있을 때 모델이 web_search 도구를 스스로 부를 수 있게 한다(ADR-056).
    web_tool_enabled: bool = True
    # 노트북에서 데스크톱 ALEX 에 붙기(ADR-052). SSH 터널이 노트북 127.0.0.1:remote_local_port 를
    # 데스크톱 127.0.0.1:remote_alex_port 로 잇는다. ALEX 는 여전히 127.0.0.1 에서만 듣는다.
    remote_host: str = ""
    remote_user: str = ""
    remote_local_port: int = 8766
    remote_alex_port: int = 8765
    # 휴대폰·밖에서 앱처럼 쓰기(2026-10-11, ADR-052 보충): Tailscale Serve 가 이 PC 의 127.0.0.1:8765 를 내 사설망(tailnet)
    # 안에서만 HTTPS 로 연다. ALEX 는 여전히 127.0.0.1 에서만 듣고, 이 주소(…ts.net)로 들어온 요청은 Tailscale 이 붙여 주는
    # 로그인 머리글(Tailscale-User-Login)이 아래 계정일 때만 받는다. 비우면 꺼짐(예전과 같다).
    remote_web_host: str = ""
    remote_web_users: str = ""     # 허용할 Tailscale 로그인, 쉼표로 여러 개
    ollama_host: str = "http://127.0.0.1:11434"
    allow_remote_ollama: bool = False
    general_model: str = "qwen3:8b-q4_K_M"
    coder_model: str = "qwen2.5-coder:7b"
    auto_profile_models: bool = True
    desktop_general_model: str = "qwen3:14b-q4_K_M"
    desktop_coder_model: str = "qwen2.5-coder:14b"
    request_timeout: int = 120
    local_max_output_tokens: int = 1024
    # 0 = Hardware Profile 자동값. 실측으로 VRAM 여유가 확인된 장비만 직접 올린다.
    num_ctx: int = 0
    # Desktop 전용 상한. 0 이면 위의 전역값을 그대로 쓴다.
    # 위 세 값은 RTX 4070 Laptop / 8GB VRAM / 8B 에서 실측해 정한 것이라, 그대로 Desktop 에
    # 가져가면 16GB 장비를 8GB 기준으로 묶는다. Desktop 에서 실측한 뒤 여기에 적는다.
    desktop_num_ctx: int = 0
    desktop_max_output_tokens: int = 0
    desktop_request_timeout: int = 0
    # GPU 에 다 안 들어갈 때 시스템 RAM 으로 넘겨도 되는 최대치(GB). 0 이면 넘김을 허용하지
    # 않는다는 뜻이고, 넘어간 것이 감지되면 경고한다.
    #
    # **속도를 얻는 설정이 아니라 용량을 위해 속도를 내주는 설정이다.** 실측(PERFORMANCE.md
    # 2절): 층의 12% 만 RAM 으로 가도 벽시계가 6.6배, 절반이면 12배 느려진다. 대화형으로
    # 쓰려면 0 이 맞고, 큰 모델로 학습 데이터를 일괄 생성하는 것처럼 지연을 감수할 수 있는
    # 작업에서만 올린다.
    ram_offload_budget_gb: float = 0.0
    # GPU 에 올릴 층 수. 0 이면 Ollama 자동 배치.
    #
    # 자동 배치는 보수적이라 VRAM 을 남긴다. 2026-09-19 실측(qwen3:14b / ctx 8192 / 8GB VRAM):
    #   auto -> VRAM 5.86G / RAM 4.22G / 10.3 tok/s   (VRAM 1.7G 를 놀림)
    #   32   -> VRAM 7.61G / RAM 2.47G / 15.2 tok/s   <- 최적, 48% 빠름
    #   36   -> VRAM 8.48G / RAM 1.60G / 14.4 tok/s   <- 물리 VRAM 8.0G 초과
    #   40   -> VRAM 9.39G / RAM 0.67G / 10.7 tok/s
    #
    # **최댓값이 최적이 아니다.** 물리 VRAM 을 넘기면 Windows 드라이버가 초과분을 시스템
    # RAM 으로 스필하는데, 그것이 Ollama 가 명시적으로 CPU 층을 두는 것보다 느리다.
    # 모델·컨텍스트마다 최적값이 다르므로 **반드시 그 조합에서 재고 넣어라.**
    num_gpu_layers: int = 0
    # num_gpu_layers 를 이 모델에만 적용한다(비우면 모든 모델) — 2026-09-30. 층 수 최적값은 모델마다 다르다:
    # qwen3.8:27b 는 54(PERFORMANCE 29, 자동 41층보다 1.6배), gpt-oss:120b(37층·MoE)에 같은 값을 주면 안 된다.
    num_gpu_layers_model: str = ""
    ollama_keep_alive: str = "10m"
    single_model_on_laptop: bool = True
    stream_output: bool = True
    ollama_think: bool = False

    # Safe local tools
    file_search_root: Path = field(default_factory=Path.cwd)
    memory_context_limit: int = 5
    # 승인된 경험(approved·train)을 비슷한 요청의 프롬프트에 몇 개까지 넣을지 (ADR-055). 0 = 끔.
    lesson_context_limit: int = 3
    # 사용자가 바로잡은 것·웹에서 찾은 사실을 자동으로 승인해 교훈으로 쓴다(ADR-057). 가중치 학습과는 무관하다.
    auto_lessons_enabled: bool = True
    # 코딩 수정안 요청에 절차 교훈(jarvis/insights.py, ADR-070)을 붙인다. 기본 꺼짐 — 평가로 효과를 잰 뒤 사용자가 켠다.
    coding_insights_enabled: bool = False
    # 배울 코딩 기록(자동 수정·코딩 세션)이 몇 건 쌓이면 쉬는 중에 /dev-learn 을 뒤 작업 차례로 자동 실행(2026-10-23). 기본 꺼짐.
    coding_auto_learn_enabled: bool = False
    # 앞선 답이 있는 대화에서 마지막 질문 바로 앞에 핵심 사실을 한 번 더 준다(ADR-071, Lost in the Middle). 기본 꺼짐 — 실측 뒤 켠다.
    fact_reminder_enabled: bool = False
    # 프롬프트 캐시를 살리는 순서(2026-10-26, Hermes Agent 의 고정 시스템 프롬프트 참고): 바뀌지 않는 지시 → 대화 기록 → 이번 차례에만
    # 바뀌는 것(시각·메모·교훈·자료)은 마지막 질문 바로 앞. 모델 서버가 앞서 읽은 대화를 다시 읽지 않게 한다. 기본 꺼짐 — PC 에서
    # scripts/measure_prompt_cache.py 로 속도·답을 비교한 뒤 켠다.
    stable_prompt_prefix: bool = False
    # 코딩 수정안 요청에 '이 파일이 쓰는 객체의 API'(시그니처만)를 붙인다(ADR-072, Aider repo map 을 좁힌 것). 기본 꺼짐 — 실측 뒤 켠다.
    coding_api_map_enabled: bool = False
    # 코딩 묶음 수정안(찾기→바꾸기)만 더 긴 문맥으로(ADR-076, docs/CODING_AGENT_SONNET_PLAN_20261003.md 1번). 0 이면 대화와 같다(기본).
    # 27b 는 큰 파일(core.py 11만 자)을 16K 에 못 넣어 발췌만 보고, 발췌 밖 API 를 지어내 실패했다(PERFORMANCE 31 ⑦).
    # 문맥을 바꾸면 Ollama 가 모델을 다시 올린다(대화 ↔ 코딩 전환마다 한 번). **실측 전에는 켜지 않는다** — VRAM·속도는 장비마다 다르다.
    coding_num_ctx: int = 0
    # coding_num_ctx 를 쓸 때 그래픽카드에 올릴 층 수. 0 이면 Ollama 자동 배치 — 대화용 num_gpu_layers(16K 에 맞춘 54)는 긴 문맥에서 넘친다.
    coding_num_gpu_layers: int = 0
    # coding_num_ctx 를 쓸 때 수정안 프롬프트의 글자 예산. 0 이면 context_char_budget 을 문맥 비율만큼 키운다.
    coding_context_char_budget: int = 0
    # /dev-auto 독립 풀이 횟수(ADR-077, 계획 2번). 한 풀이(최대 3회차)가 통과하지 못하면 처음 상태에서 새로 푼다 — 첫 통과에서 멈춘다.
    # 1 이면 예전과 같다(기본). 늘리면 실패 과제의 시간이 그만큼 늘어난다(GPU·팬). 실측 뒤 정한다.
    coding_auto_attempts: int = 1
    # /dev-auto 회귀 검사(ADR-078, 계획 7절 ⑩): 합격 테스트가 통과하면 복사본에서 전체 테스트도 돌려, 이 수정이 새로 깨뜨린 테스트가 있으면
    # 그 실패를 주고 다시 고치게 한다. 전체 검사 한 번에 약 2분(CPU). 기본 꺼짐 — 실측 뒤 켠다.
    coding_regression_check: bool = False
    # /dev-auto 수정 전 읽기 전용 진단 근거(ADR-080, Codex 가이드 우선순위 2): 실패 문구가 나오는 조건·실패한 테스트의 준비 코드를 찾아
    # 남는 자리에 붙인다. 모델 호출은 늘지 않는다. 기본 꺼짐 — 켬/끔 한 변수 비교로 효과를 잰 뒤 켠다.
    coding_diagnostic_context: bool = False
    # 만들기 루프(2026-10-05): /build 로 명시하면 코딩 모델이 작업 폴더(data/builds)에 파일을 쓰고 검사가 통과할 때까지 고친다
    # (jarvis/build_loop.py). 채팅의 "~ 만들어줘" 는 이것을 쓰지 않는다 — 사용자가 코드를 답으로 받기를 원했다.
    coding_build_loop_enabled: bool = True
    # 코드 답 실행 확인(2026-10-05): "~ 만들어줘"·코딩 후속 답의 코드를 보이지 않는 임시 폴더에서 몇 초 돌려 보고, 시작하자마자
    # 오류가 나면 고친 코드로 바꿔 준다(jarvis/code_check.py). 답 끝에 실행 확인 결과를 적는다.
    coding_code_check_enabled: bool = True
    # 코딩 일꾼(2026-10-05, ADR-083): /dev-auto 의 수정은 오픈코드가 하고 판정·적용은 ALEX 가 한다. 'alex' 면 예전처럼 모델 수정안.
    # 오픈코드는 opencode_dir(비우면 C:\ALEX\tools\opencode) 의 실행 파일과, 문맥을 박은 Ollama 모델 별칭(opencode_model)을 쓴다.
    coding_worker: str = "opencode"
    opencode_dir: str = ""
    opencode_model: str = "qwen3.8-q3-32k"
    opencode_context: int = 32768
    opencode_timeout: int = 900
    # OpenCode 무료 모델(2026-10-23, jarvis/freemodels.py) — 'opencode/<모델>' 이면 /dev-auto·/build 일꾼이 OpenCode + 그 모델(코드가
    # opencode.ai 로 간다 — 사용자가 고를 때만). 비우면 이 PC 모델. 무료 단계는 OpenCode 안에서만 되므로 OpenCode 일꾼으로 돈다.
    coding_free_model: str = ""
    # 로컬 모델 서버(2026-10-10, ADR-086): 'ollama'(기본) 또는 'strata'. strata 면 대화·코딩·요약·오픈코드 일꾼이 모두 이 PC 의
    # Strata 서버(Qwen3.8-Flash-Next 125B MoE, C:\ALEX\tools\strata)를 쓴다. 주소는 이 PC(loopback)만 허용한다.
    local_backend: str = "ollama"
    strata_host: str = "http://127.0.0.1:8080"
    strata_model: str = "qwen3.8-flash-next"
    strata_num_ctx: int = 65536            # Strata 설치 때 고른 문맥 창(이 PC: 64K)과 맞춘다
    strata_reasoning_effort: str = "medium"  # 생각을 켤 때(ollama_think) 쓰는 정도: low|medium|high
    # Strata 를 고르면 쓰는 상한(2026-10-11 사용자 "선택에 따라서 자동으로 설정을 바뀌게끔"). Ollama 로 바꾸면 위의
    # Desktop/전역 값(16K 문맥 기준)으로 돌아간다. 문맥 글자 예산 0 = 문맥 창에서 계산(app.engine_context_limits).
    strata_max_output_tokens: int = 16384   # 생각 토큰 포함. 실측 초당 59~76토큰 → 다 쓰면 약 4분
    strata_context_char_budget: int = 0
    strata_history_limit: int = 24
    # RTL 모드(2026-10-10, ADR-087): /rtl 프로젝트 폴더의 뿌리(비우면 C:\ALEX\fabless)와 칩 도구(oss-cad-suite) 위치
    rtl_root: str = ""
    rtl_tools_dir: str = ""
    # 어려운 일은 클라우드로(ADR-102): 요청할 때만, 이름을 말하지 않으면 이쪽(claude|codex). 한도면 다른 쪽이 대신.
    cloud_agent: str = "claude"
    cloud_agent_timeout: int = 1800
    # 자연어 그림 그리기(ADR-073): 이 PC 의 ComfyUI(127.0.0.1)로 Z-Image Turbo 를 돌린다. 기본 꺼짐 — 설치한 PC 에서 켠다.
    image_generation_enabled: bool = False
    # 비우면 ALEX 폴더 옆 comfyui\ComfyUI_windows_portable (C:\ALEX\comfyui\...) 을 쓴다.
    comfyui_dir: str = ""
    comfyui_url: str = "http://127.0.0.1:8188"
    # 기억을 뜻으로 찾기(ADR-094): 이 PC 의 CPU 임베딩 서버(llama-server + Qwen3-Embedding 0.6B, C:\ALEX\tools\embed).
    # 기본 꺼짐 — 설치한 PC 에서 켠다. 꺼져 있거나 서버가 없으면 낱말 검색만 쓴다. 그래픽카드는 쓰지 않는다.
    embedding_enabled: bool = False
    embedding_host: str = "http://127.0.0.1:8081"
    embedding_threads: int = 4
    file_read_max_bytes: int = 100_000
    project_context_max_files: int = 1500
    project_context_max_bytes: int = 5_000_000
    project_context_max_chars: int = 12_000

    # v0.1.2 privacy: any CLOUD_* route needs explicit user approval.
    require_cloud_approval: bool = True
    # Cloud context minimization: persistent memory/old summary stay local unless explicitly opted in.
    cloud_include_memory: bool = False
    cloud_include_summary: bool = False
    cloud_block_sensitive: bool = True
    cloud_provider: str = "stub"  # stub | openai | anthropic | gemini
    cloud_coder_model: str = ""
    cloud_reasoning_model: str = ""
    cloud_max_output_tokens: int = 2048

    # Explicit device control (disabled by default)
    wol_enabled: bool = False
    wol_mac: str = ""
    wol_broadcast: str = "255.255.255.255"
    wol_port: int = 9
    vscode_launch_enabled: bool = False
    vscode_executable: str = "code"

    # v0.1.2 conversation compaction. Old messages are summarized locally.
    summary_enabled: bool = True
    summary_trigger_messages: int = 24
    summary_max_chars: int = 3000

    @property
    def database_path(self) -> Path:
        return self.data_dir / self.database_name


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} 은(는) 1/0, true/false, yes/no, on/off 중 하나여야 합니다. 현재 값: {raw!r}")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 은(는) 정수여야 합니다. 현재 값: {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} 은 숫자여야 합니다. 현재 값: {raw!r}") from exc


def validate(settings: Settings) -> Settings:
    """설정을 검증한다. 문제가 있으면 ConfigError를 던진다."""
    if settings.log_max_bytes < 100_000 or settings.log_max_bytes > 100_000_000:
        raise ConfigError("JARVIS_LOG_MAX_BYTES 는 100000~100000000 사이여야 합니다.")
    if settings.log_backup_count < 1 or settings.log_backup_count > 20:
        raise ConfigError("JARVIS_LOG_BACKUP_COUNT 는 1~20 사이여야 합니다.")
    if settings.force_profile.strip().upper() not in {"", "LAPTOP", "DESKTOP", "UNKNOWN"}:
        raise ConfigError("JARVIS_FORCE_PROFILE 은 LAPTOP, DESKTOP, UNKNOWN 중 하나여야 합니다.")
    if settings.context_char_budget < 0 or settings.context_char_budget > 500_000:
        raise ConfigError("JARVIS_CONTEXT_CHAR_BUDGET 은 0~500000 사이여야 합니다. 0은 자동 설정입니다.")
    if 0 < settings.context_char_budget < 1000:
        raise ConfigError("JARVIS_CONTEXT_CHAR_BUDGET 을 직접 지정할 때는 1000 이상이어야 합니다.")
    if settings.history_limit < 2:
        raise ConfigError("JARVIS_HISTORY_LIMIT 은 2 이상이어야 합니다.")
    if settings.history_limit > 200:
        raise ConfigError("JARVIS_HISTORY_LIMIT 이 너무 큽니다. 200 이하로 설정하세요.")
    if settings.memory_context_limit < 0:
        raise ConfigError("JARVIS_MEMORY_CONTEXT_LIMIT 은 0 이상이어야 합니다.")
    if settings.memory_context_limit > 100:
        raise ConfigError("JARVIS_MEMORY_CONTEXT_LIMIT 은 100 이하로 설정하세요.")
    if not 0 <= settings.lesson_context_limit <= 10:
        raise ConfigError("lesson_context_limit(JARVIS_LESSON_CONTEXT_LIMIT) 은 0~10 이어야 합니다.")
    if settings.request_timeout < 1:
        raise ConfigError("JARVIS_REQUEST_TIMEOUT 은 1 이상이어야 합니다.")
    if settings.request_timeout > 3600:
        raise ConfigError("JARVIS_REQUEST_TIMEOUT 은 3600 이하로 설정하세요.")
    if settings.local_max_output_tokens < 64 or settings.local_max_output_tokens > 8192:
        raise ConfigError("JARVIS_LOCAL_MAX_OUTPUT_TOKENS 는 64~8192 사이여야 합니다.")
    if settings.num_ctx != 0 and (settings.num_ctx < 1024 or settings.num_ctx > 131_072):
        raise ConfigError("JARVIS_NUM_CTX 는 0(프로필 자동) 또는 1024~131072 사이여야 합니다.")
    if settings.desktop_num_ctx != 0 and (
        settings.desktop_num_ctx < 1024 or settings.desktop_num_ctx > 131_072
    ):
        raise ConfigError(
            "JARVIS_DESKTOP_NUM_CTX 는 0(전역값 사용) 또는 1024~131072 사이여야 합니다."
        )
    if settings.coding_num_ctx != 0 and not 1024 <= settings.coding_num_ctx <= 131_072:
        raise ConfigError("coding_num_ctx(JARVIS_CODING_NUM_CTX) 는 0(대화와 같음) 또는 1024~131072 사이여야 합니다.")
    if not 0 <= settings.coding_num_gpu_layers <= 256:
        raise ConfigError("coding_num_gpu_layers(JARVIS_CODING_NUM_GPU_LAYERS) 는 0(자동)~256 사이여야 합니다.")
    if settings.coding_context_char_budget != 0 and not 1000 <= settings.coding_context_char_budget <= 500_000:
        raise ConfigError("coding_context_char_budget(JARVIS_CODING_CONTEXT_CHAR_BUDGET) 는 0(자동) 또는 1000~500000 사이여야 합니다.")
    if not 1 <= settings.coding_auto_attempts <= 5:
        raise ConfigError("coding_auto_attempts(JARVIS_CODING_AUTO_ATTEMPTS) 는 1~5 사이여야 합니다.")
    if settings.coding_free_model and not re.fullmatch(r"opencode/[A-Za-z0-9._-]{1,80}", settings.coding_free_model):
        raise ConfigError("coding_free_model 은 비우거나 'opencode/<모델>' 이어야 합니다(OpenCode 무료 모델).")
    if settings.coding_worker not in ("alex", "opencode", "harness"):
        raise ConfigError("coding_worker(JARVIS_CODING_WORKER) 는 alex·opencode·harness(ALEX 코딩 하네스, ADR-112) 중 하나여야 합니다.")
    if not 4096 <= settings.opencode_context <= 262144:
        raise ConfigError("opencode_context(JARVIS_OPENCODE_CONTEXT) 는 4096~262144 사이여야 합니다.")
    if not 60 <= settings.opencode_timeout <= 7200:
        raise ConfigError("opencode_timeout(JARVIS_OPENCODE_TIMEOUT) 는 60~7200 초 사이여야 합니다.")
    if settings.cloud_agent not in ("claude", "codex"):
        raise ConfigError("cloud_agent 는 claude 또는 codex 여야 합니다.")
    if not 60 <= settings.cloud_agent_timeout <= 4 * 3600:
        raise ConfigError("cloud_agent_timeout 은 60~14400 초여야 합니다.")
    if settings.local_backend not in ("ollama", "strata"):
        raise ConfigError("local_backend(JARVIS_LOCAL_BACKEND) 는 ollama 또는 strata 여야 합니다.")
    if not settings.strata_host.startswith("http://") or \
            (urlparse(settings.strata_host).hostname or "").lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise ConfigError(f"strata_host 는 이 PC 주소(http://127.0.0.1:포트)만 허용합니다. 현재 값: {settings.strata_host!r}")
    if not settings.strata_model.strip():
        raise ConfigError("strata_model 은 비어 있을 수 없습니다.")
    if not 4096 <= settings.strata_num_ctx <= 1_048_576:
        raise ConfigError("strata_num_ctx 는 4096~1048576 사이여야 합니다.")
    if settings.strata_reasoning_effort not in ("low", "medium", "high"):
        raise ConfigError("strata_reasoning_effort 는 low, medium, high 중 하나여야 합니다.")
    if settings.remote_web_host:
        if not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net", settings.remote_web_host):
            raise ConfigError("remote_web_host 는 Tailscale 주소(…ts.net)만 허용합니다. 예: my-pc.tail1234.ts.net")
        if not [u for u in settings.remote_web_users.split(",") if u.strip()]:
            raise ConfigError("remote_web_host 를 쓰려면 remote_web_users 에 허용할 Tailscale 로그인을 적어야 합니다.")
    if not 64 <= settings.strata_max_output_tokens <= settings.strata_num_ctx // 2:
        raise ConfigError("strata_max_output_tokens 는 64 이상, 문맥 창(strata_num_ctx)의 절반 이하여야 합니다.")
    if settings.strata_context_char_budget != 0 and not 1000 <= settings.strata_context_char_budget <= 500_000:
        raise ConfigError("strata_context_char_budget 는 0(자동) 또는 1000~500000 사이여야 합니다.")
    if not 2 <= settings.strata_history_limit <= 200:
        raise ConfigError("strata_history_limit 는 2~200 사이여야 합니다.")
    if not settings.embedding_host.startswith("http://") or \
            (urlparse(settings.embedding_host).hostname or "").lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise ConfigError(f"embedding_host 는 이 PC 주소(http://127.0.0.1:포트)만 허용합니다. 현재 값: {settings.embedding_host!r}")
    if not 1 <= settings.embedding_threads <= 32:
        raise ConfigError("embedding_threads 는 1~32 사이여야 합니다.")
    if settings.desktop_max_output_tokens != 0 and (
        settings.desktop_max_output_tokens < 64 or settings.desktop_max_output_tokens > 8192
    ):
        raise ConfigError(
            "JARVIS_DESKTOP_MAX_OUTPUT_TOKENS 는 0(전역값 사용) 또는 64~8192 사이여야 합니다."
        )
    if settings.desktop_request_timeout != 0 and (
        settings.desktop_request_timeout < 1 or settings.desktop_request_timeout > 3600
    ):
        raise ConfigError(
            "JARVIS_DESKTOP_REQUEST_TIMEOUT 은 0(전역값 사용) 또는 1~3600 사이여야 합니다."
        )
    # 명시적으로 지정한 조합이 자기모순이면 조용히 돌리지 않고 막는다.
    # 출력 예산이 컨텍스트 창 이상이면 프롬프트가 들어갈 자리가 아예 없다.
    if settings.num_ctx and settings.local_max_output_tokens >= settings.num_ctx:
        raise ConfigError(
            f"JARVIS_LOCAL_MAX_OUTPUT_TOKENS({settings.local_max_output_tokens}) 는 "
            f"JARVIS_NUM_CTX({settings.num_ctx}) 보다 작아야 합니다. "
            "출력 예산은 컨텍스트 창 안에서 프롬프트와 자리를 나눠 씁니다."
        )
    _desktop_ctx = settings.desktop_num_ctx or settings.num_ctx
    _desktop_out = settings.desktop_max_output_tokens or settings.local_max_output_tokens
    if _desktop_ctx and _desktop_out >= _desktop_ctx:
        raise ConfigError(
            f"Desktop 설정이 모순입니다: 출력 예산({_desktop_out}) 이 "
            f"컨텍스트 창({_desktop_ctx}) 보다 작아야 합니다."
        )
    if settings.num_gpu_layers < 0 or settings.num_gpu_layers > 999:
        raise ConfigError("JARVIS_NUM_GPU_LAYERS 는 0(자동) 또는 1~999 사이여야 합니다.")
    if settings.ram_offload_budget_gb < 0 or settings.ram_offload_budget_gb > 512:
        raise ConfigError("JARVIS_RAM_OFFLOAD_BUDGET_GB 는 0~512 사이여야 합니다.")
    if not re.fullmatch(r"(?:0|[1-9]\d*(?:ms|s|m|h))", settings.ollama_keep_alive.strip()):
        raise ConfigError("JARVIS_OLLAMA_KEEP_ALIVE 는 0, 30s, 10m, 1h 같은 형식이어야 합니다.")
    if not settings.ollama_host.startswith(("http://", "https://")):
        raise ConfigError(
            f"JARVIS_OLLAMA_HOST 는 http:// 또는 https:// 로 시작해야 합니다. 현재 값: {settings.ollama_host!r}"
        )
    parsed_host = (urlparse(settings.ollama_host).hostname or "").lower()
    loopback_hosts = {"localhost", "127.0.0.1", "::1"}
    if parsed_host not in loopback_hosts and not settings.allow_remote_ollama:
        raise ConfigError(
            "Remote Ollama 주소는 기본적으로 차단됩니다. 정말 원격 Local-LLM 서버를 사용할 때만 "
            "JARVIS_ALLOW_REMOTE_OLLAMA=1 을 명시하세요."
        )
    if not settings.general_model.strip() or not settings.coder_model.strip():
        raise ConfigError("모델 이름은 비어 있을 수 없습니다.")
    if settings.local_only and any("cloud" in model.lower() for model in (
        settings.general_model, settings.coder_model, settings.desktop_general_model, settings.desktop_coder_model
    )):
        raise ConfigError("로컬 전용 모드에서는 Cloud 모델을 사용할 수 없습니다.")
    if not settings.desktop_general_model.strip() or not settings.desktop_coder_model.strip():
        raise ConfigError("Desktop 모델 이름은 비어 있을 수 없습니다.")
    if not settings.file_search_root.exists():
        raise ConfigError(
            f"JARVIS_FILE_SEARCH_ROOT 경로가 존재하지 않습니다: {settings.file_search_root}"
        )
    if not settings.file_search_root.is_dir():
        raise ConfigError(
            f"JARVIS_FILE_SEARCH_ROOT 는 디렉터리여야 합니다: {settings.file_search_root}"
        )
    if settings.file_read_max_bytes < 1 or settings.file_read_max_bytes > 5_000_000:
        raise ConfigError("JARVIS_FILE_READ_MAX_BYTES 는 1~5000000 사이여야 합니다.")
    if settings.project_context_max_files < 10 or settings.project_context_max_files > 50_000:
        raise ConfigError("JARVIS_PROJECT_CONTEXT_MAX_FILES 는 10~50000 사이여야 합니다.")
    if settings.project_context_max_bytes < 100_000 or settings.project_context_max_bytes > 100_000_000:
        raise ConfigError("JARVIS_PROJECT_CONTEXT_MAX_BYTES 는 100000~100000000 사이여야 합니다.")
    if settings.project_context_max_chars < 1000 or settings.project_context_max_chars > 100_000:
        raise ConfigError("JARVIS_PROJECT_CONTEXT_MAX_CHARS 는 1000~100000 사이여야 합니다.")
    if settings.cloud_provider not in {"stub", "openai", "anthropic", "gemini"}:
        raise ConfigError("JARVIS_CLOUD_PROVIDER 는 stub/openai/anthropic/gemini 중 하나여야 합니다.")
    if settings.cloud_provider != "stub" and not settings.local_only:
        if not settings.cloud_coder_model.strip() or not settings.cloud_reasoning_model.strip():
            raise ConfigError(
                "실제 Cloud Provider를 사용할 때 JARVIS_CLOUD_CODER_MODEL 과 "
                "JARVIS_CLOUD_REASONING_MODEL 을 명시해야 합니다."
            )
    if settings.cloud_max_output_tokens < 64 or settings.cloud_max_output_tokens > 100_000:
        raise ConfigError("JARVIS_CLOUD_MAX_OUTPUT_TOKENS 는 64~100000 사이여야 합니다.")
    if settings.wol_enabled:
        if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}|[0-9A-Fa-f]{12}", settings.wol_mac.strip()):
            raise ConfigError("JARVIS_WOL_ENABLED=1 일 때 올바른 JARVIS_WOL_MAC 이 필요합니다.")
    if settings.wol_port < 1 or settings.wol_port > 65535:
        raise ConfigError("JARVIS_WOL_PORT 는 1~65535 범위여야 합니다.")
    if settings.wol_enabled and not settings.wol_broadcast.strip():
        raise ConfigError("JARVIS_WOL_BROADCAST 는 비어 있을 수 없습니다.")
    if settings.vscode_launch_enabled and not settings.vscode_executable.strip():
        raise ConfigError("JARVIS_VSCODE_EXECUTABLE 은 비어 있을 수 없습니다.")
    if settings.summary_trigger_messages < settings.history_limit + 2:
        raise ConfigError(
            "JARVIS_SUMMARY_TRIGGER_MESSAGES 는 JARVIS_HISTORY_LIMIT 보다 최소 2 커야 합니다."
        )
    if settings.summary_max_chars < 500 or settings.summary_max_chars > 20_000:
        raise ConfigError("JARVIS_SUMMARY_MAX_CHARS 는 500~20000 사이여야 합니다.")
    return settings


LOCAL_FIELDS = {
    "use_ollama": bool, "general_model": str, "coder_model": str,
    "auto_profile_models": bool, "desktop_general_model": str, "desktop_coder_model": str,
    "single_model_on_laptop": bool, "ollama_think": bool,
    "local_max_output_tokens": int, "request_timeout": int, "num_ctx": int,
    "desktop_num_ctx": int, "desktop_max_output_tokens": int, "desktop_request_timeout": int,
    "ram_offload_budget_gb": float, "num_gpu_layers": int, "num_gpu_layers_model": str,
    "context_char_budget": int, "force_profile": str, "web_enabled": bool,
    "remote_host": str, "remote_user": str, "remote_local_port": int, "remote_alex_port": int,
    "remote_web_host": str, "remote_web_users": str,
    "lesson_context_limit": int, "web_tool_enabled": bool, "auto_lessons_enabled": bool, "coding_insights_enabled": bool, "coding_auto_learn_enabled": bool, "fact_reminder_enabled": bool, "stable_prompt_prefix": bool, "coding_api_map_enabled": bool,
    "coding_num_ctx": int, "coding_num_gpu_layers": int, "coding_context_char_budget": int, "coding_auto_attempts": int, "coding_regression_check": bool, "coding_diagnostic_context": bool, "coding_build_loop_enabled": bool, "coding_code_check_enabled": bool, "coding_worker": str, "opencode_dir": str, "opencode_model": str, "opencode_context": int, "opencode_timeout": int, "coding_free_model": str,
    "local_backend": str, "strata_host": str, "strata_model": str, "strata_num_ctx": int, "strata_reasoning_effort": str,
    "strata_max_output_tokens": int, "strata_context_char_budget": int, "strata_history_limit": int,
    "rtl_root": str, "rtl_tools_dir": str, "cloud_agent": str, "cloud_agent_timeout": int,
    "image_generation_enabled": bool, "comfyui_dir": str, "comfyui_url": str,
    "embedding_enabled": bool, "embedding_host": str, "embedding_threads": int,
}


def local_config_path() -> Path:
    return Path(os.getenv("JARVIS_CONFIG_FILE", "jarvis.local.json"))


def load_settings() -> Settings:
    """환경 변수에서 설정을 읽고 검증한다."""
    history_limit = _env_int("JARVIS_HISTORY_LIMIT", 12)
    summary_trigger_default = max(24, history_limit * 2)
    settings = Settings(
        data_dir=Path(os.getenv("JARVIS_DATA_DIR", "data")),
        log_dir=Path(os.getenv("JARVIS_LOG_DIR", "logs")),
        log_max_bytes=_env_int("JARVIS_LOG_MAX_BYTES", 2_000_000),
        log_backup_count=_env_int("JARVIS_LOG_BACKUP_COUNT", 3),
        history_limit=history_limit,
        context_char_budget=_env_int("JARVIS_CONTEXT_CHAR_BUDGET", 0),
        force_profile=os.getenv("JARVIS_FORCE_PROFILE", "").strip().upper(),
        use_ollama=_env_flag("JARVIS_USE_OLLAMA", False),
        local_only=_env_flag("JARVIS_LOCAL_ONLY", True),
        web_enabled=_env_flag("JARVIS_WEB_ENABLED", True),
        web_tool_enabled=_env_flag("JARVIS_WEB_TOOL", _env_flag("JARVIS_WEB_TOOL_ENABLED", True)),
        ollama_host=os.getenv("JARVIS_OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/"),
        allow_remote_ollama=_env_flag("JARVIS_ALLOW_REMOTE_OLLAMA", False),
        general_model=os.getenv("JARVIS_GENERAL_MODEL", "qwen3:8b-q4_K_M"),
        coder_model=os.getenv("JARVIS_CODER_MODEL", "qwen2.5-coder:7b"),
        auto_profile_models=_env_flag("JARVIS_AUTO_PROFILE_MODELS", True),
        desktop_general_model=os.getenv("JARVIS_DESKTOP_GENERAL_MODEL", "qwen3:14b-q4_K_M"),
        desktop_coder_model=os.getenv("JARVIS_DESKTOP_CODER_MODEL", "qwen2.5-coder:14b"),
        request_timeout=_env_int("JARVIS_REQUEST_TIMEOUT", 120),
        local_max_output_tokens=_env_int("JARVIS_LOCAL_MAX_OUTPUT_TOKENS", 1024),
        num_ctx=_env_int("JARVIS_NUM_CTX", 0),
        desktop_num_ctx=_env_int("JARVIS_DESKTOP_NUM_CTX", 0),
        desktop_max_output_tokens=_env_int("JARVIS_DESKTOP_MAX_OUTPUT_TOKENS", 0),
        desktop_request_timeout=_env_int("JARVIS_DESKTOP_REQUEST_TIMEOUT", 0),
        ram_offload_budget_gb=_env_float("JARVIS_RAM_OFFLOAD_BUDGET_GB", 0.0),
        num_gpu_layers=_env_int("JARVIS_NUM_GPU_LAYERS", 0),
        num_gpu_layers_model=os.getenv("JARVIS_NUM_GPU_LAYERS_MODEL", "").strip(),
        ollama_keep_alive=os.getenv("JARVIS_OLLAMA_KEEP_ALIVE", "10m").strip(),
        single_model_on_laptop=_env_flag("JARVIS_SINGLE_MODEL_ON_LAPTOP", True),
        stream_output=_env_flag("JARVIS_STREAM_OUTPUT", True),
        ollama_think=_env_flag("JARVIS_OLLAMA_THINK", False),
        file_search_root=Path(os.getenv("JARVIS_FILE_SEARCH_ROOT", str(Path.cwd()))),
        memory_context_limit=_env_int("JARVIS_MEMORY_CONTEXT_LIMIT", 5),
        lesson_context_limit=_env_int("JARVIS_LESSON_CONTEXT_LIMIT", 3),
        auto_lessons_enabled=_env_flag("JARVIS_AUTO_LESSONS", _env_flag("JARVIS_AUTO_LESSONS_ENABLED", True)),
        coding_insights_enabled=_env_flag("JARVIS_CODING_INSIGHTS_ENABLED", False),
        coding_auto_learn_enabled=_env_flag("JARVIS_CODING_AUTO_LEARN_ENABLED", False),
        fact_reminder_enabled=_env_flag("JARVIS_FACT_REMINDER_ENABLED", False),
        stable_prompt_prefix=_env_flag("JARVIS_STABLE_PROMPT_PREFIX", False),
        coding_api_map_enabled=_env_flag("JARVIS_CODING_API_MAP_ENABLED", False),
        coding_num_ctx=_env_int("JARVIS_CODING_NUM_CTX", 0),
        coding_num_gpu_layers=_env_int("JARVIS_CODING_NUM_GPU_LAYERS", 0),
        coding_context_char_budget=_env_int("JARVIS_CODING_CONTEXT_CHAR_BUDGET", 0),
        coding_auto_attempts=_env_int("JARVIS_CODING_AUTO_ATTEMPTS", 1),
        coding_regression_check=_env_flag("JARVIS_CODING_REGRESSION_CHECK", False),
        coding_diagnostic_context=_env_flag("JARVIS_CODING_DIAGNOSTIC_CONTEXT", False),
        coding_build_loop_enabled=_env_flag("JARVIS_CODING_BUILD_LOOP", True),
        coding_code_check_enabled=_env_flag("JARVIS_CODING_CODE_CHECK", True),
        coding_worker=os.getenv("JARVIS_CODING_WORKER", "opencode").strip().lower(),
        coding_free_model=os.getenv("JARVIS_CODING_FREE_MODEL", "").strip(),
        opencode_dir=os.getenv("JARVIS_OPENCODE_DIR", ""),
        opencode_model=os.getenv("JARVIS_OPENCODE_MODEL", "qwen3.8-q3-32k").strip(),
        opencode_context=_env_int("JARVIS_OPENCODE_CONTEXT", 32768),
        opencode_timeout=_env_int("JARVIS_OPENCODE_TIMEOUT", 900),
        local_backend=os.getenv("JARVIS_LOCAL_BACKEND", "ollama").strip().lower(),
        strata_host=os.getenv("JARVIS_STRATA_HOST", "http://127.0.0.1:8080").strip().rstrip("/"),
        strata_model=os.getenv("JARVIS_STRATA_MODEL", "qwen3.8-flash-next").strip(),
        strata_num_ctx=_env_int("JARVIS_STRATA_NUM_CTX", 65536),
        strata_reasoning_effort=os.getenv("JARVIS_STRATA_REASONING_EFFORT", "medium").strip().lower(),
        strata_max_output_tokens=_env_int("JARVIS_STRATA_MAX_OUTPUT_TOKENS", 16384),
        strata_context_char_budget=_env_int("JARVIS_STRATA_CONTEXT_CHAR_BUDGET", 0),
        strata_history_limit=_env_int("JARVIS_STRATA_HISTORY_LIMIT", 24),
        rtl_root=os.getenv("JARVIS_RTL_ROOT", ""),
        rtl_tools_dir=os.getenv("JARVIS_RTL_TOOLS_DIR", ""),
        cloud_agent=os.getenv("JARVIS_CLOUD_AGENT", "claude").strip().lower(),
        cloud_agent_timeout=_env_int("JARVIS_CLOUD_AGENT_TIMEOUT", 1800),
        image_generation_enabled=_env_flag("JARVIS_IMAGE_GENERATION_ENABLED", False),
        comfyui_dir=os.getenv("JARVIS_COMFYUI_DIR", ""),
        comfyui_url=os.getenv("JARVIS_COMFYUI_URL", "http://127.0.0.1:8188"),
        embedding_enabled=_env_flag("JARVIS_EMBEDDING_ENABLED", False),
        embedding_host=os.getenv("JARVIS_EMBEDDING_HOST", "http://127.0.0.1:8081").strip().rstrip("/"),
        embedding_threads=_env_int("JARVIS_EMBEDDING_THREADS", 4),
        file_read_max_bytes=_env_int("JARVIS_FILE_READ_MAX_BYTES", 100_000),
        project_context_max_files=_env_int("JARVIS_PROJECT_CONTEXT_MAX_FILES", 1500),
        project_context_max_bytes=_env_int("JARVIS_PROJECT_CONTEXT_MAX_BYTES", 5_000_000),
        project_context_max_chars=_env_int("JARVIS_PROJECT_CONTEXT_MAX_CHARS", 12_000),
        require_cloud_approval=_env_flag("JARVIS_REQUIRE_CLOUD_APPROVAL", True),
        cloud_include_memory=_env_flag("JARVIS_CLOUD_INCLUDE_MEMORY", False),
        cloud_include_summary=_env_flag("JARVIS_CLOUD_INCLUDE_SUMMARY", False),
        cloud_block_sensitive=_env_flag("JARVIS_CLOUD_BLOCK_SENSITIVE", True),
        cloud_provider=os.getenv("JARVIS_CLOUD_PROVIDER", "stub").strip().lower(),
        cloud_coder_model=os.getenv(
            "JARVIS_CLOUD_CODER_MODEL", os.getenv("JARVIS_CLOUD_MODEL", "")
        ).strip(),
        cloud_reasoning_model=os.getenv(
            "JARVIS_CLOUD_REASONING_MODEL", os.getenv("JARVIS_CLOUD_MODEL", "")
        ).strip(),
        cloud_max_output_tokens=_env_int("JARVIS_CLOUD_MAX_OUTPUT_TOKENS", 2048),
        wol_enabled=_env_flag("JARVIS_WOL_ENABLED", False),
        wol_mac=os.getenv("JARVIS_WOL_MAC", "").strip(),
        wol_broadcast=os.getenv("JARVIS_WOL_BROADCAST", "255.255.255.255").strip(),
        wol_port=_env_int("JARVIS_WOL_PORT", 9),
        vscode_launch_enabled=_env_flag("JARVIS_VSCODE_LAUNCH_ENABLED", False),
        vscode_executable=os.getenv("JARVIS_VSCODE_EXECUTABLE", "code").strip(),
        summary_enabled=_env_flag("JARVIS_SUMMARY_ENABLED", True),
        summary_trigger_messages=_env_int("JARVIS_SUMMARY_TRIGGER_MESSAGES", summary_trigger_default),
        summary_max_chars=_env_int("JARVIS_SUMMARY_MAX_CHARS", 3000),
    )
    path = local_config_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("object required")
            for key, value in data.items():
                if key not in LOCAL_FIELDS or type(value) is not LOCAL_FIELDS[key]:
                    raise ValueError(f"unsupported setting/type: {key}")
                env_names = {"JARVIS_" + key.upper()}
                if key == "web_tool_enabled":
                    env_names.add("JARVIS_WEB_TOOL")
                elif key == "auto_lessons_enabled":
                    env_names.add("JARVIS_AUTO_LESSONS")
                if not any(name in os.environ for name in env_names):
                    setattr(settings, key, value)
        except (OSError, ValueError) as exc:
            raise ConfigError(f"로컬 설정 파일 오류 ({path}): {exc}") from exc
    return validate(settings)
