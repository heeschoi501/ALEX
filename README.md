> **2026-10-23 추가 개선:** 검사 캐시 정확성·긴 요청 stdin 전달·Office 편집 연결·조사 발췌·검사 결과 UI. [적용 내역과 검증 범위](docs/CODEX_IMPROVEMENTS_20261023.md).

# ALEX — 내 PC에서 도는 개인 AI

**Adaptive Learning & Evolving eXperience.** 클라우드에 기대지 않고 집 PC(RTX 5080 16GB · RAM 96GB)에서 로컬 LLM으로 대화하고,
자기 코드를 고치고, 경험에서 배우는 개인 AI 를 직접 설계·구현·검증한 프로젝트입니다. *A local-first personal AI that chats,
fixes its own code under test-based guardrails, and learns from approved experience — built and measured on a single desktop PC.*

## 무엇을 만들었나

| 영역 | 내용 |
|---|---|
| **로컬 우선 AI 코어** | 규칙 라우터가 질문을 일반·코딩·클라우드로 나누고, 기본은 로컬 전용. 클라우드는 사용자 승인 게이트 + 비밀 정보 자동 차단(API 키·토큰·자격 증명) 뒤에서만 |
| **기억과 성장** | SQLite 세션·장기 메모·할 일. 경험 저장소(불변 본문 + 해시 무결성, train/eval 결정적 분할) → 사람이 승인한 교정만 다음 답의 교훈으로 재사용 |
| **자기 코드 고치기** | `/dev-auto`·`/build`: ALEX 자체 하네스 또는 외부 OpenCode를 선택. 격리 수정 → 관련 테스트 독립 확인 → 실패 시 재수정 → 전체 회귀 → 사용자 승인. 별도 `/code`에서 권한·계획·질문·되돌리기 지원 |
| **채팅 코딩** | 코드를 답으로 주고, 보이지 않는 임시 폴더에서 실행해 시작 오류를 스스로 고침(Python 감사 훅 기반 실행 가드 — OS 격리는 아님: 작업 폴더 밖 쓰기·네트워크·하위 프로세스·레지스트리 쓰기 차단) |
| **가중치 학습** | LoRA 파이프라인(transformers + PEFT) — 승인된 '질문 → 좋은 답'만 학습, 어댑터를 합쳐 GGUF → Ollama 등록, 정체성 회귀 채점. 학습 데이터는 문서·작업 교훈에서 큰 모델이 만들고 채점(7점 이상)한 뒤 사람이 묶음 승인 |
| **모델 서버** | Ollama(Qwen3.8-27B, 3비트 양자화 · KV 8비트로 16GB 에 전부 올려 약 65 tok/s) 또는 Strata(Qwen3.8-Flash-Next 125B MoE 를 RAM·GPU 에 나눠 실행) 중 선택 |
| **도구** | 안전한 파일 검색·읽기(루트 밖·심볼릭 링크 탈출 차단), 승인 기반 파일 편집(diff → 적용 → 백업 → 되돌리기), 웹 검색 읽기, 문서 내보내기(xlsx·pdf·pptx·docx), 로컬 웹 UI + CLI |
| **프로젝트·사용자 구분** | 프로젝트별 지침·문서·대화, 사용자별 대화·기억·할 일·생성 파일, 모델 요청 대기열. 로컬에서는 주인 계정으로 시작하며 사용자 관리 메뉴는 아직 숨김 |

## 어떻게 만들었나

- **측정으로 결정** — 설계 결정과 후속 검토를 근거·검증과 함께 기록(`DECISIONS.md`), 실측 기록(`PERFORMANCE.md`). 예: 모델을 바꾸기 전에 지시문 유무 A/B 로
  원인이 지시문이 아니라 모델·구조임을 가렸고, 공개 벤치마크와 이 PC 실측을 함께 보고 모델·양자화를 골랐다.
- **검사가 기준** — 최신 공개본 테스트 1,615개(실패 0·skip 13), Python 3.11 릴리스 PASS(2026-10-23; 3.10 문법 검사 통과, 이번 버전의 다른 Python 실제 실행은 미검증), 릴리스 검사(`scripts/verify_release.py`: Python 3.10 문법 호환, 의존성 감사, 라우터 골든 코퍼스, 보안 스모크, 알파·베타 스모크).
  Core 기본 실행은 **표준 라이브러리만** 쓴다. 학습·PDF 일꾼과 생성 코드 검사 일꾼의 선택 패키지는 파일별로 감사한다.
- **여러 AI 와 협업** — Claude Code·Codex 가 번갈아 구현·리뷰하고, 인계 문서와 결정 기록으로 이어 갔다. 사람(나)은 방향·승인·검증 기준을 정했다.

## 구조

```
jarvis/        앱 (router · core · providers · storage · developer(/dev-auto) · harness · opencode_worker · code_check · build_loop · web · launcher …)
tests/         단위·통합 검사 (가짜 Ollama·Strata·OpenCode 서버로 실모델 없이)
scripts/       릴리스 검사, 평가(코드·정체성·라우터), LoRA 학습·데이터, 경험 관리
docs/ · *.md   설계 결정, 성능 실측, 보안 경계, 학습 연구, 리뷰 기록
logs/probe/    코딩 에이전트 평가 도구와 과제
```

**빠른 실행:** `python -m jarvis.web` (모델 없이 Mock 모드) · 실제 모델은 아래 "빠른 시작" 참고.

> **2026-10-23 오후 갱신:** 자동 딥서치, 말로 파일 고치기(허락은 채팅에서), 자기 기능 알기·스스로 점검 준비, 입력창 오픈코드 API, 문서 서식 유지 고치기 엔진(미연결). [인수인계](docs/HANDOFF_20261023.md#오후-갱신같은-날).

> **2026-10-23 갱신:** 코딩 하네스 검사 속도 보강, 코딩 경험으로 배우기(코딩 세션 기록·쉬는 중 자동 학습·교훈→스킬), OpenCode 무료 모델(OpenCode 를 통해, 고를 때만), 설정의 '코딩 에이전트 · API' 칸과 웹 '다시 켜기'. [인수인계](docs/HANDOFF_20261023.md).

> **2026-10-22 갱신:** ALEX 자체 코딩 하네스·독립 관련 검사·실패 재수정과 최신 연결 기능을 반영했습니다. [설정·검사·남은 작업 인수인계](docs/HANDOFF_20261022.md) · [하네스 검토와 실제 측정의 한계](docs/CODEX_HARNESS_REVIEW_20261022.md). 공개 기본값은 `opencode`이며 `coding_worker=harness`로 선택합니다.

> **2026-10-12 보완:** 대기 중 요청의 취소·연결 종료가 실행 중 응답의 중단 신호를 바꾸지 않도록 수정하고, wheel에 PWA manifest·아이콘을 포함했습니다. [검증 결과·업데이트·롤백 안내](docs/CODEX_HARDENING_20261012.md).
> **2026-10-12 추가 검증:** 쿠키·동시 로그인, 업로드 응답 경쟁, 프로젝트 첫 전송, Python 3.10 검사 집계·Linux 글꼴·공개 배포를 보완했습니다. [전체 검증·복구 기록](docs/FULL_VALIDATION_20261012.md) · [GPT-5.6 Luna 비교 도구(실모델 미실측)](docs/MODEL_COMPARISON_GPT56_LUNA_20261012.md).
> **2026-10-11 갱신:** ComfyUI 이미지·영상 연결과 LTX 소리 포함 영상 코드를 공개본에 반영했습니다. 직접 영상 모델 선택·자연어 생성·그림→영상·GPU 반환 보호를 지원합니다. 설치·사용·실측은 [LTX 영상 안내](docs/LTX_VIDEO.md)를 참고하세요.
> 개인 데이터(`data/`·`runtime/`·`logs/`·`jarvis.local.json`·대화 DB)와 작업 인계 메모도 빠져 있습니다. 문서는 한국어입니다.

---

## 운영 안내 (v0.2.0 README)

> 프로젝트 이름은 **Project ALEX** 다(`PROJECT_ALEX.md`). 코드 식별자는 아직 `jarvis` 다 —
> 패키지 디렉터리, `JARVIS_*` 환경변수, `jarvis.local.json`, `data/jarvis.db`.
> 옮기려면 데이터 마이그레이션이 필요해 사용자 결정 전까지 그대로 둔다.

Project ALEX는 특정 LLM이 아니라 **Memory / Session / Safe Tools / Hardware Profile / Model Router / Provider / Workspace / Task state**를 묶는 개인용 AI Core다.

목표는 단순 챗봇이 아니라 평상시는 Local AI가 처리하고, 어려운 요청만 사용자의 명시적 승인을 거쳐 Cloud AI로 넘기는 개인용 시스템이다.

## 현재 실사용 범위

v0.2.0은 Mock skeleton을 넘어 다음 흐름을 실제 코드로 제공한다.

- RTX 4070 Laptop 최소 사양 / RTX 5080 Desktop 자동 확장 Profile
- Ollama Local LLM + Mock fallback
- 현재 `jarvis.local.json`의 Laptop/Desktop 실행 모델은 general·coder 모두 `gpt-oss:20b` (`/status`에서 실효값 확인)
- Rule Router + Local/Cloud fallback
- OpenAI / Anthropic / Gemini Cloud adapter (opt-in)
- Cloud 전송 전 승인 Gate + secret hard block
- SQLite Session / Summary / Long-term Memory / Task / App state
- 긴 대화 Local rolling summary + context budget
- 안전한 파일 검색/읽기/tree/lexical project retrieval
- 선택 파일/프로젝트 Local-only 분석
- **승인 기반 Workspace Edit**: Local 수정안 → diff → 명시 적용 → 자동 백업 → undo
- 구조화된 Task 관리
- 현재 project sub-root 재시작 복원
- Memory + Task portable state export/import
- CLI + localhost Web chat UI
- DB backup/export/doctor/status
- opt-in Wake-on-LAN (`/wake-desktop --yes`)
- opt-in fixed-argv VS Code launcher (`/open-vscode --yes`)
- Router 오분류 feedback 수집
- release/security/router/beta smoke 자동 검증

## 빠른 시작

Python 코딩을 직접 연습하려면 [JupyterLab 실습 안내](study/JUPYTERLAB_GUIDE.md)를
따르십시오. [실습 노트북](study/ALEX_PYTHON_LAB.ipynb)에 작은 함수 문제와 실행 검사가 있습니다.

### 1. Mock 모드 — 모델 설치 없이 구조 확인

Windows:

```bat
start_mock.bat
```

WSL/Linux:

```bash
./start_mock.sh
```

또는:

```bash
python -m jarvis
```

### 2. 실제 Local LLM 준비

Ollama 설치 후 먼저 필요한 모델을 확인한다.

```bash
python scripts/check_environment.py
python scripts/setup_models.py
```

실제 모델을 pull하려면 명시적으로:

```bash
python scripts/setup_models.py --pull
```

Local 모드:

Windows:

```bat
start_local.bat
```

WSL/Linux:

```bash
./start_local.sh
```

### 3. Web UI

```bash
python -m jarvis.web
```

기본 Web UI는 `127.0.0.1`에만 bind한다. LAN 공개용 서버가 아니다.

## 주요 명령

```text
/status
/doctor
/profile
/wake-desktop --yes
/open-vscode --yes

/new
/sessions
/use <session-id>
/rename <title>
/export
/summary

/remember key=value
/recall key
/forget key
/memories

/task-add [1|2|3] <text>
/tasks [all]
/task-done <id>
/task-open <id>
/task-delete <id> --yes

/project [relative-path]
/tree [depth]
/find <name>
/read <path>
/grep <query>
/ask-file <path> :: <question>
/ask-project <question>

/search <검색어>                 웹 검색 + 상위 결과 한 쪽을 읽고 Local 모델이 답 (출처 표시)
                                 명령 없이 채팅에서 "iverilog 최신 버전 검색해줘" 도 같다
/web <url> :: <question>         웹 페이지 한 쪽을 읽고 Local 모델에게 질문 (읽기 전용)

채팅에서 검색 요청 여러 개와 주소를 함께 보내면 최대 3개를 읽어 한 번에 답합니다.
예: "iverilog 최신 버전 검색해줘 RTX 5080 VRAM 용량 찾아봐 https://steveicarus.github.io/iverilog/ 여기서 확인해줘"
처리하지 못한 요청은 답에 이유를 표시합니다.

/edit-file <path> :: <instruction>
/apply-edit <proposal-id> --yes
/discard-edit <proposal-id>
/undo-file <path> --yes

/route <question>
/ask-local <question>
/ask-cloud <question>
/wrong [correct-route]
/route-stats

/backup
/state-export [file]
/state-import <file> --yes
```

## Workspace Edit 안전 흐름

JARVIS가 모델 출력만 보고 파일을 자동 수정하지 않는다.

```text
/edit-file jarvis/router.py :: 이 중복 조건을 함수로 정리해줘
        ↓
Local Coding Model
        ↓
unified diff + proposal id
        ↓
사용자가 diff 확인
        ↓
/apply-edit <id> --yes
        ↓
원본 자동 백업 + atomic replace
```

제안 뒤 파일이 사람이 직접 바뀌면 stale proposal을 거부한다. 큰 파일은 전체 원문이 Local context에 들어가지 않으면 전체 교체 편집을 거부한다.

## Laptop / Desktop 기본 모델

### Laptop / UNKNOWN

- General: `qwen3:8b-q4_K_M`
- Coder route: 기본적으로 General 모델 공유
- Context: 4096 (프로필 기본값)
- Thinking: 기본 OFF

### Desktop

- General: `qwen3:14b-q4_K_M`
- Coder: `qwen2.5-coder:14b`
- Context: 16384 (프로필 기본값)

환경변수로 전부 변경 가능하다. 실제 성능값은 `scripts/benchmark_local.py`로 사용자 4070/5080에서 측정해야 한다.

## Cloud 경계

**기본값은 로컬 전용이다.** `JARVIS_LOCAL_ONLY` 가 기본 참이고, 이때 Router가 Cloud 목적지를
고르면 Core가 로컬로 되돌린다(`local-only` 사유로 기록된다). 승인 Gate보다 앞선 경계라서
이 값이 참인 동안에는 승인해도 외부로 나가지 않는다. `validate_local.py` 도 이 모드로만 돈다.

Cloud adapter는 있어도 기본 `JARVIS_CLOUD_PROVIDER=stub`이다. 실제 Provider를 켜더라도:

1. Router가 Cloud 후보를 선택
2. Persistent Memory/Summary는 기본 제외
3. 민감정보 scanner가 먼저 hard block
4. CLI에서 Provider/목적지별 사용자 승인
5. 승인된 요청만 외부 API 호출

Web UI에서는 Cloud 승인을 강제로 거부한다.

**로컬 전용은 "인터넷 금지" 가 아니다.** 위 경계는 대화·기억을 **클라우드 LLM** 으로 보내지 않는
것이다. `/search` `/web` 은 웹 페이지를 읽어 **로컬 모델에게** 주는 것이라 이 경계에 걸리지 않고
토큰도 쓰지 않는다(ADR-051). 경계와 차단 규칙은 `SECURITY.md` "Web reading" 절.

## 기기 이동

v0.2.0은 live SQLite DB를 네트워크 공유하지 않는다. 대신 기억·할 일·경험을 portable JSON으로 이동할 수 있다. 대화는 이 형식으로 병합되지 않는다.

```text
/state-export transfer.json
/state-import transfer.json --yes
```

Import는 기존 Memory key를 조용히 덮어쓰지 않는다.

### 노트북에서 데스크톱 ALEX 쓰기 (ADR-052)

집에서든 밖에서든 노트북에서 데스크톱 ALEX 를 씁니다. Tailscale + SSH 터널이고, ALEX 는 계속 127.0.0.1
에서만 듣습니다. 대화·기억·코드 업데이트를 한곳에 모으는 운영 방법과 설치 순서는
**[REMOTE_SETUP.md](REMOTE_SETUP.md)**의 "두 PC의 업데이트를 한곳에 모으려면" 절에 있습니다.

```text
connect_desktop.cmd setup --host <데스크톱 100.x 주소> --user <데스크톱 사용자>   (노트북, 처음 한 번)
scripts\remote_setup_desktop.ps1                                               (데스크톱, 관리자, 처음 한 번)
connect_desktop.cmd                                                            (노트북, 매번)
connect_desktop.cmd check                                                      (안 될 때)
```

## 검증

```bash
python scripts/verify_release.py
```

릴리스 전에는 다음을 모두 통과해야 한다.

- compileall
- Unit test + ResourceWarning guard
- Python 3.10 syntax compatibility
- stdlib dependency audit
- Router golden corpus
- Security smoke
- Alpha E2E smoke
- Beta E2E smoke

## 운영 도구

앱 명령(`/help`)과 별개로, 저장소에는 설치·측정·검토용 스크립트가 있다.

| 도구 | 하는 일 |
|---|---|
| `jarvis/launcher.py` | 창이 뜨는 런처. 로컬 모델을 확인하고 백그라운드 서비스를 띄운 뒤 채팅을 연다. 인터넷을 쓰는 것은 최초 설치 다운로드뿐이고, 평상시 실행은 loopback 요청만 한다. |
| `scripts/install_ollama.py` | 공식 Windows 포터블 런타임을 `runtime/` 에 설치한다(`jarvis/runtime.py`). SHA-256 으로 검증하며 관리자 권한·레지스트리·PATH·자동시작 서비스를 건드리지 않는다. |
| `scripts/setup_models.py` | 프로필에 필요한 모델을 보여주거나 내려받는다. **기본은 dry run** 이고 실제 내려받기는 `--pull` 을 요구한다 — 수 GB 다운로드가 소리 없이 시작되지 않게 하기 위해서다. |
| `scripts/validate_local.py` | 실제 GPU 로 채팅/편집/undo/기억을 한 번에 확인한다. 격리된 fixture 만 쓰고 사용자 파일을 건드리지 않는다. **모델 순위표가 아니라 smoke test 다.** |
| `scripts/benchmark_local.py` | 설정된 Ollama 모델을 실제 장비에서 측정한다(지연·처리량). |
| `scripts/grade_codegen.py` | 생성된 계산기 코드를 **실제로 실행해** 7케이스로 채점한다. 모델이 쓴 코드를 실행하므로 `--execute` 없이는 채점하지 않는다. |
| `scripts/grade_identity.py` | `IDENTITY.md` I-1~I-5 를 채점한다. 키워드 채점이라 판정자가 아니다 — 점수가 이상하면 `--show` 로 원문부터 본다. |
| `scripts/experiences.py` | 경험 저장소 조회·검토·내보내기(`status` / `import-routes` / `list` / `review` / `export`). |
| `scripts/misroute_stats.py` | `logs/misroute.jsonl` 을 요약한다. **라우터 규칙을 바꾸지는 않는다.** |
| `scripts/check_environment.py` | 실제 장비를 쓰기 전 읽기 전용 환경 점검. |
| `scripts/package_release.py` | 인계용 ZIP·tar.gz 와 SHA-256 체크섬을 만든다. |

`runtime/` 은 이 도구들이 만드는 디렉터리다 — 번들 Ollama 실행 파일, 내려받은 모델,
설치 검증 기록(`ollama-install.json`), 실행 중 PID 파일이 들어간다. 저장소 코드가 아니다.

### 생성 중단

Web UI 의 중단 버튼은 `/api/cancel` 로 `cancel_event` 를 세운다. Provider 가 이를 보고
`GenerationCancelled` 를 올리며, **그때까지 생성된 부분 답변은 버리지 않고**
`[사용자가 응답 생성을 중단했습니다.]` 를 붙여 세션에 저장한다. 중단해도 대화 이력이
끊기지 않는다.

## 실측 현황과 남은 검증

RTX 4070 Laptop 은 실측이 끝났다 — first-token latency, tokens/sec, VRAM, 모델별 E2E 가
`HARDWARE_VALIDATION.md` A절에 있고, 설정별 비교는 `PERFORMANCE.md` 에 있다. 이 문서들의
수치는 전부 실측이며 추정치를 섞지 않는다.

아직 측정하지 않은 것:

- **RTX 5080 Desktop 전체** — 장비가 없어 B절이 비어 있다. 8B ↔ 14B 전환 지연도 여기에 달려 있다.
- **KV cache 양자화**(`OLLAMA_KV_CACHE_TYPE=q8_0`)로 `num_ctx` 를 더 올릴 수 있는지
- **30+ turn 장시간 체감** — rolling summary 가 실제로 버티는지
- **WSL 경유 Ollama 연결 체감** — 현재 검증은 전부 Windows 네이티브 Python 이다

새 장비나 새 모델로 옮기면 `HARDWARE_VALIDATION.md` 순서대로 다시 측정한다.
**모델을 바꾸면 이전 측정값은 무효다.**

## 아직 v0.2.0에 포함하지 않은 장기 목표

- Wake word / STT / TTS 완성형 Voice
- LAN/Internet 공개 Web UI
- 인증된 Desktop Remote Worker (Wake-on-LAN magic packet 자체는 구현됨)
- 실시간 Laptop↔Desktop 양방향 동기화 서버
- Camera/Vision
- Home automation
- 임의 shell/terminal 자동 실행
- 복잡한 Multi-Agent / LangGraph

이들은 “완성되지 않은 결함”이라기보다, 실제 장비와 보안 경계가 필요한 다음 단계다. 현재 Beta는 **로컬 대화·코딩·파일/프로젝트 분석·기억·Task·안전 편집을 매일 시험 사용할 수 있는 범위**를 우선한다.
