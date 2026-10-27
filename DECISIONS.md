# Architecture Decisions

## 번호 찾아보기

ADR 번호는 영구 식별자다. 문서 안의 위치가 번호 순서와 다를 수 있다.

- **ADR-001** — v0.1은 표준 라이브러리 중심
- **ADR-002** — LangChain/LangGraph 미사용
- **ADR-003** — Session + Memory를 SQLite 한 파일에 저장
- **ADR-004** — Router는 명시적 Rule 기반
- **ADR-005** — Cloud Provider는 Stub
- **ADR-006** — 위험한 System Tool 제외
- **ADR-007** — 다음 구현자는 저장소 문서 기반으로 인수인계
- **ADR-008** — Provider 실패는 Core 의 Fallback Chain 이 처리한다
- **ADR-009** — 첫 실제 Local Provider 는 Ollama native API
- **ADR-010** — 모델 이름과 num_ctx 는 config 가 결정한다
- **ADR-011** — 장기 메모는 system 메시지 한 줄로 주입한다
- **ADR-012** — SafeFileSearchTool 을 Router/CLI 에 연결하되 루트를 고정한다
- **ADR-013** — 실패 경로에도 사용자 친화적 메시지를 둔다
- **ADR-014** — Gemini 검토는 단일 패킷 파일로 한다
- **ADR-015** — Cloud 전송은 기본적으로 명시적 사용자 승인 후에만 허용
- **ADR-016** — 오래된 대화는 세션별 Rolling Summary + 최근 Window로 유지
- **ADR-017** — Router 개선은 자동 학습보다 오분류 데이터 수집을 먼저 한다
- **ADR-018** — SQLite connection은 전용 context manager에서 명시적으로 close
- **ADR-019** — Ollama health check는 정확한 model tag + 짧은 TTL cache
- **ADR-020** — 파일 내용 읽기는 explicit CLI + root/size/extension 제한
- **ADR-021** — Memory/Session 데이터는 Local backup/export 기능을 제공
- **ADR-022** — 실제 장비 검증은 스크립트로 재현 가능하게 한다
- **ADR-023** — Project code Q&A는 bounded lexical retrieval + Local-only부터 시작
- **ADR-024** — 자동 Project Context는 credential-like 파일명을 제외
- **ADR-025** — 비-Desktop은 기본 single-model, Desktop만 자동 14B 분리
- **ADR-026** — Qwen3 thinking은 기본 OFF, 출력 상한/keep-alive를 명시
- **ADR-027** — Cloud adapter를 넣어도 default Provider는 Stub
- **ADR-028** — Persistent Memory/old Summary는 Cloud 기본 제외
- **ADR-029** — Web Alpha는 loopback-only Local surface
- **ADR-030** — Router 변경 전 Golden Corpus를 통과해야 한다
- **ADR-031** — SQLite migration은 기존 DB 사전 backup 후 실행
- **ADR-032** — 릴리스 archive 자체를 안전성 대상으로 검사
- **ADR-033** — num_ctx 는 프로필 자동값을 기본으로 두되 실측 근거가 있으면 덮어쓸 수 있다
- **ADR-034** — thinking 모드는 실측 근거 없이 켜지 않는다
- **ADR-035** — 코드 경로에만 코딩 지시를 덧붙인다
- **ADR-036** — 모델이 쓴 코드의 실행은 실행마다 사람이 승인한다 (측정 결과 도입 안 함)
- **ADR-037** — 경험 저장소는 기억과 분리하고, 불변·결정적 분할·출처 추적을 스키마로 강제한다
- **ADR-038** — Model output cannot directly write source files
- **ADR-039** — Full-file editing is refused when the entire source does not fit local edit context
- **ADR-040** — Tasks are structured SQLite state, not free-form Memory
- **ADR-041** — Device transfer starts with portable Memory+Task JSON, not network-shared SQLite
- **ADR-042** — Active project persistence stores a relative path
- **ADR-043** — Voice/remote execution remain post-hardware-validation
- **ADR-044** — Device controls are fixed-target/fixed-argv and opt-in

---

## ADR-001 — v0.1은 표준 라이브러리 중심
**결정:** mandatory third-party dependency를 두지 않는다.

**이유:** Skeleton 단계에서 환경 문제보다 구조 이해와 테스트가 중요하다. SQLite, unittest, pathlib 등으로 충분하다.

## ADR-002 — LangChain/LangGraph 미사용
**결정:** v0.1에는 사용하지 않는다.

**이유:** Router와 Tool 호출이 단순하며, 지금 넣으면 학습 난도와 결합도만 증가한다.

## ADR-003 — Session + Memory를 SQLite 한 파일에 저장
**결정:** `sessions`, `messages`, `memories` 세 테이블로 시작한다.

**이유:** 세션과 장기 기억의 개념은 분리하면서 운영 파일은 하나라 이해하기 쉽다.

## ADR-004 — Router는 명시적 Rule 기반
**결정:** LLM Router를 사용하지 않는다.

**이유:** 판단 이유를 로그로 남길 수 있고 실패를 재현하기 쉽다. 추후 오분류 데이터가 쌓인 뒤 개선한다.

## ADR-005 — Cloud Provider는 Stub
**결정:** 외부 API 호출을 하지 않는다.

**이유:** 비용/비밀키/개인정보 유출 없이 routing architecture를 먼저 검증한다.

## ADR-006 — 위험한 System Tool 제외
**결정:** `system_info`와 파일명 검색 구조만 허용하고 임의 shell 실행은 넣지 않는다.

**이유:** Human Approval 모델이 생기기 전에는 기능보다 안전이 우선이다.

## ADR-007 — 다음 구현자는 저장소 문서 기반으로 인수인계
**결정:** Claude Code가 대화 기록 없이도 이어갈 수 있도록 `CLAUDE_NEXT.md`, `TODO.md`, `TEST_RESULTS.md`를 유지한다.

---

# v0.1.1 (2026-09-13, Claude)

## ADR-008 — Provider 실패는 Core 의 Fallback Chain 이 처리한다
**결정:** `core.FALLBACK_CHAIN` 딕셔너리 하나에 대체 순서를 모아두고, 호출 전에 `health_check()` 를 먼저 확인한다.

**이유:** Provider 마다 재시도 로직을 넣으면 중복되고 읽기 어려워진다. 규칙을 한 곳에 두면 "왜 이 모델로 갔는가"를 코드 한 줄로 설명할 수 있다. Fallback 이 쓰였을 때는 응답 앞에 `[fallback: A → B]` 를 붙여 사용자가 알 수 있게 한다.

**남은 위험:** Local 실패가 Cloud 자동 전송으로 이어진다. 개인정보 관점에서 Gemini 검토 대상이다.

## ADR-009 — 첫 실제 Local Provider 는 Ollama native API
**결정:** `OllamaProvider` 가 `urllib` 로 `/api/chat` 을 호출한다. `requests` 를 추가하지 않는다.

**이유:** ADR-001(표준 라이브러리 중심)을 깨지 않으면서 실제 모델을 붙일 수 있다. `BaseLLMProvider` 계약은 그대로이므로 나중에 llama.cpp/OpenAI-compatible Provider 를 추가해도 Core 는 바뀌지 않는다.

## ADR-010 — 모델 이름과 num_ctx 는 config 가 결정한다
**결정:** 모델명은 환경 변수(`JARVIS_GENERAL_MODEL`, `JARVIS_CODER_MODEL`)로 옮기고, `num_ctx` 는 Hardware Profile 로 자동 결정한다 (Laptop 4096 / Desktop 16384 / Unknown 2048).

**이유:** "같은 코드가 Desktop 에서 자동 확장된다"는 프로젝트 원칙을 코드 한 곳에서 구현하는 가장 단순한 방법이다.

## ADR-011 — 장기 메모는 system 메시지 한 줄로 주입한다
**결정:** 매 요청마다 최근 메모 상위 `memory_context_limit` 개만 `system` 메시지로 넣는다. 전체 대화는 절대 넣지 않는다.

**이유:** Vector DB 없이 Context Window 낭비를 막는 가장 작은 방법이다. 관련성 기반 검색은 v0.2 과제로 남긴다.

## ADR-012 — SafeFileSearchTool 을 Router/CLI 에 연결하되 루트를 고정한다
**결정:** `JARVIS_FILE_SEARCH_ROOT` 한 곳만 검색한다. 검색어에 `/`, `\`, `..`, `~`, `:` 가 있으면 거부하고, 루트 밖을 가리키는 symlink 는 건너뛴다. 파일 내용은 여전히 읽지 않는다.

**이유:** 기능보다 안전이 우선이라는 ADR-006 을 유지하면서도 도구를 실제로 쓸 수 있게 한다.

## ADR-013 — 실패 경로에도 사용자 친화적 메시지를 둔다
**결정:** `ConfigError`, `StorageError`, `ProviderError`, `ToolError` 네 개만 정의하고, 각 메시지에 "무엇을 하면 되는지"를 적는다.

**이유:** 개인용 도구는 스택 트레이스가 아니라 다음 행동을 알려줘야 한다. 예외 종류를 네 개로 제한해 Core 의 `except` 절이 늘어나지 않게 한다.

## ADR-014 — Gemini 검토는 단일 패킷 파일로 한다
**결정:** `scripts/make_review_packet.py` 가 문서 + 전체 소스 + 그 시점 테스트 결과를 하나의 `REVIEW_PACKET.md` 로 합친다.

**이유:** 저장소 접근 없이도 독립 검토가 가능해야 하고, 검토자가 본 코드와 실제 코드가 어긋나지 않아야 한다.

---

# v0.1.2 (2026-09-13, GPT)

## ADR-015 — Cloud 전송은 기본적으로 명시적 사용자 승인 후에만 허용
**결정:** `CLOUD_CODER`, `CLOUD_REASONING` 목적지와 Local→Cloud fallback 모두 `require_cloud_approval=True`일 때 승인 callback을 통과해야 한다. callback이 없거나 실패하면 거부로 처리한다.

**이유:** 실제 Cloud Provider가 연결된 뒤 Local 장애가 곧바로 개인정보/코드 외부 전송으로 이어지는 구조는 위험하다. UI는 `input()`을 담당하고 Core는 callback 계약만 알아 UI 결합을 피한다.

**추가 보호:** 전송 예정 context의 메시지 수, 장기 메모/요약 포함 여부, 최근 요청 미리보기와 기본 secret 패턴 경고를 승인 화면에 표시한다. 이것은 완전한 secret scanner가 아니며 최종 판단은 사용자에게 있다.

## ADR-016 — 오래된 대화는 세션별 Rolling Summary + 최근 Window로 유지
**결정:** `sessions`에 `summary`, `summary_through_id`를 추가한다. `history_limit` 밖으로 밀려나는 오래된 메시지는 Local General Provider로 요약하고, Local 요약이 실패하면 짧은 extractive summary로 fallback한다. Cloud Provider는 요약에 절대 사용하지 않는다.

**이유:** 이전 버전은 오래된 대화를 그냥 버렸다. Vector DB 없이도 `요약 1개 + 최근 N개`면 v0.1.2에서 맥락 손실과 Context 증가를 동시에 줄일 수 있다.

**Migration:** 기존 v0.1.1 DB는 시작 시 `ALTER TABLE`로 컬럼만 추가하며 세션/메시지/메모리를 삭제하지 않는다.

## ADR-017 — Router 개선은 자동 학습보다 오분류 데이터 수집을 먼저 한다
**결정:** 모든 route 판정을 SQLite `route_events`에 기록하고, 사용자가 `/wrong [올바른목적지]`을 실행한 건만 `logs/misroute.jsonl`에도 남긴다. `scripts/misroute_stats.py`는 통계만 내며 규칙을 자동 수정하지 않는다.

**이유:** 규칙을 감으로 계속 늘리면 오히려 회귀가 생긴다. 실제 사용자 오분류 사례가 쌓인 뒤 다음 버전에서 Router를 수정한다.

## ADR-018 — SQLite connection은 전용 context manager에서 명시적으로 close
**결정:** 모든 DB 접근은 `Storage._connection()`을 사용하고 `finally: conn.close()`를 보장한다.

**이유:** `with sqlite3.Connection`은 transaction commit/rollback을 관리할 뿐 connection을 close하지 않는다. v0.1.1의 88개 테스트는 통과했지만 Python 3.13에서 대량 `ResourceWarning: unclosed database`가 재현됐다. v0.1.2 테스트는 `-W error::ResourceWarning` 조건으로 검증한다.

## ADR-019 — Ollama health check는 정확한 model tag + 짧은 TTL cache
**결정:** 모델 이름에 `:`가 있으면 exact tag만 healthy로 인정한다. 예: `qwen2.5:7b` 요청 시 `qwen2.5:14b`만 설치되어 있으면 실패다. tag를 생략한 경우만 `:latest`를 허용한다. `/api/tags` 결과는 기본 30초 cache한다.

**이유:** 다른 크기/quant 모델을 잘못 healthy로 판단하면 실제 generate에서 실패한다. 반대로 매 prompt마다 `/api/tags`를 호출하는 것도 불필요하다.

## ADR-020 — 파일 내용 읽기는 explicit CLI + root/size/extension 제한
**결정:** `SafeFileReadTool`을 추가하지만 자연어 Router가 자동 실행하지 않는다. `/read relative/path.py`처럼 사용자가 명시적으로 호출할 때만 승인된 root 안의 UTF-8 텍스트/코드 파일을 읽는다. 절대경로, root 탈출, 과대 파일, 비허용 확장자는 거부한다.

**이유:** 실제 코딩 비서로 쓰려면 파일 내용을 읽을 수 있어야 하지만, v0.1.2에서 자동 Tool execution 범위를 넓히는 것은 보안상 이르다.

## ADR-021 — Memory/Session 데이터는 Local backup/export 기능을 제공
**결정:** `/backup`은 SQLite online backup API를 사용하고 `/export`는 현재 세션을 Markdown으로 로컬 저장한다. `/use <session_id>`로 과거 세션에 전환할 수 있다.

**이유:** JARVIS의 핵심 자산은 모델이 아니라 Memory/Session이다. Alpha 단계부터 손쉬운 백업과 사람이 읽을 수 있는 export가 있어야 한다.

## ADR-022 — 실제 장비 검증은 스크립트로 재현 가능하게 한다
**결정:** `scripts/check_environment.py`, `scripts/benchmark_local.py`, Windows/PowerShell/WSL launchers를 저장소에 포함한다.

**이유:** RTX 4070 Laptop/RTX 5080 Desktop 측정은 이 실행 환경에서 대신할 수 없다. 다음 작업자가 같은 명령으로 profile, Ollama readiness, first streamed content latency, 전체 응답 시간, GPU memory를 기록할 수 있게 한다.

---

# v0.1.3 (2026-09-13, GPT)

## ADR-023 — Project code Q&A는 bounded lexical retrieval + Local-only부터 시작
**결정:** `/tree`, `/grep`, `/ask-file`, `/ask-project`를 추가하되 multi-file retrieval은 파일 수/총 byte/context char를 제한한 lexical 방식으로 한다. `/ask-file`과 `/ask-project`는 Cloud fallback을 허용하지 않는다.

**이유:** 개인 코딩 비서로 실제 쓸 수 있으면서도 코드가 Cloud approval 실수로 넘어가는 경로를 줄인다. v0.1 단계에서 embedding/Vector DB dependency를 추가할 증거가 아직 없다.

## ADR-024 — 자동 Project Context는 credential-like 파일명을 제외
**결정:** credentials/secret/token/private/key 성격의 이름은 automatic project retrieval에서 제외한다. explicit `/read`는 별도 안전 규칙 아래 사용자가 경로를 직접 지정한다.

**이유:** Local-only retrieval이어도 민감 파일을 불필요하게 prompt로 복제할 이유가 없다. filename filter는 완전한 secret scanner가 아니므로 workspace boundary가 여전히 1차 방어다.

## ADR-025 — 비-Desktop은 기본 single-model, Desktop만 자동 14B 분리
**결정:** LAPTOP/UNKNOWN은 `qwen3:8b-q4_K_M` 하나를 General/Coder route가 공유한다. DESKTOP이 확실하고 auto-profile이 켜졌을 때만 `qwen3:14b-q4_K_M` + `qwen2.5-coder:14b`를 선택한다.

**이유:** 8GB Laptop에서 두 모델 swap 비용을 기본 동작으로 강요하지 않는다. Desktop 16GB도 두 14B 모델 동시 상주를 가정하지 않으며 실제 장비 benchmark로 확인한다.

## ADR-026 — Qwen3 thinking은 기본 OFF, 출력 상한/keep-alive를 명시
**결정:** 일상 Local Alpha 응답성을 위해 thinking은 기본 false, `num_predict=1024`, `keep_alive=10m` 기본을 둔다. 모두 config로 변경 가능하다.

**이유:** 모델이 무제한 생성하거나 매 요청 load되는 현상을 줄이되 실제 최적값은 사용자 장비 실측 뒤 결정한다.

## ADR-027 — Cloud adapter를 넣어도 default Provider는 Stub
**결정:** OpenAI Responses, Anthropic Messages, Gemini generateContent stdlib adapter를 구현하지만 `JARVIS_CLOUD_PROVIDER=stub`을 유지한다. 실 adapter는 provider/model/env API key를 명시해야 조립된다.

**이유:** “연결 가능”과 “자동 외부 전송”을 분리한다. 기존 approval/privacy/hard-block 경계를 provider 앞에 유지한다.

## ADR-028 — Persistent Memory/old Summary는 Cloud 기본 제외
**결정:** Cloud 승인을 받아도 장기 Memory와 old rolling summary는 각각 explicit opt-in 전에는 payload에 넣지 않는다. provider별 승인을 다시 받으며 첫 Cloud 거부는 같은 요청의 나머지 Cloud 후보도 막는다.

**이유:** Cloud 승인 하나가 과거 개인 맥락 전체 전송 승인으로 확대 해석되지 않게 한다.

## ADR-029 — Web Alpha는 loopback-only Local surface
**결정:** stdlib HTTP server 기반 Web chat을 `127.0.0.1`에만 bind하고 runtime POST token, JSON/body limit, 기본 security headers를 사용한다. interactive Cloud approval은 Web에 넣지 않는다.

**이유:** 사용 편의성을 얻되 remote service/authentication 문제를 v0.1에 끌어들이지 않는다.

## ADR-030 — Router 변경 전 Golden Corpus를 통과해야 한다
**결정:** 대표 문장 29개를 고정 평가셋으로 두고 release verification에서 100%를 요구한다. 실제 `/wrong` 데이터가 규칙 확장의 근거가 된다.

**이유:** keyword rule을 감으로 추가하면서 기존 route를 깨는 것을 방지한다.

## ADR-031 — SQLite migration은 기존 DB 사전 backup 후 실행
**결정:** 실제 schema 변경이 필요한 기존 DB는 SQLite online backup으로 `backups/pre-migration-*`를 만든 뒤 migration한다. backup 실패 시 원본 DB를 변경하지 않는다.

**이유:** Memory/Session이 JARVIS의 핵심 자산이므로 기능 upgrade보다 복구 가능성을 우선한다.

## ADR-032 — 릴리스 archive 자체를 안전성 대상으로 검사
**결정:** `package_release.py`는 전체 verification 성공 후에만 ZIP/TAR을 만들고, archive audit으로 `data/`, `logs/`, caches, compiled files가 없는지 확인한 후 SHA-256을 생성한다.

**이유:** Claude/GPT handoff ZIP에 개인 Memory DB나 로그가 실수로 포함되는 것은 코드 보안 경계와 별개의 실제 데이터 누출 위험이다.

# v0.2.0 decisions (2026-09-13, GPT)

> **2026-09-19 번호 정정.** 이 절의 ADR 은 원래 020~026 이었는데 v0.1.2/v0.1.3 절과
> **번호가 그대로 겹쳐 있었습니다**(같은 번호가 서로 다른 결정을 가리켰습니다).
> 038~044 로 옮겼습니다. 대응은 020→038, 021→039, ... 026→044 입니다.
> 저장소 어디에서도 옛 번호를 참조하지 않아 바뀐 참조는 없습니다.
> ADR 번호는 식별자이므로 문서 안의 위치가 번호 순서와 일치하지 않을 수 있습니다.
> 번호로 찾을 때는 맨 위의 찾아보기를 쓰십시오.

## ADR-038 — Model output cannot directly write source files

Local coding model may create a full-file proposal, but apply is a separate explicit operation. Proposal shows unified diff, apply requires `--yes`, original is backed up, and stale digest is rejected.

Reason: a useful coding assistant needs write capability, but model text must not itself be authority.

## ADR-039 — Full-file editing is refused when the entire source does not fit local edit context

Truncating a source then asking the model for a replacement can silently delete the unseen tail. Until structured partial edits are implemented, large files are analysis-only.

## ADR-040 — Tasks are structured SQLite state, not free-form Memory

Tasks need status/priority/listing semantics. Long-term Memory remains key/value preference/project context.

## ADR-041 — Device transfer starts with portable Memory+Task JSON, not network-shared SQLite

Copying a live SQLite DB across a network/cloud-sync folder risks locking/conflict/corruption and couples devices to filesystem layout. v0.2.0 exports an inspectable bounded JSON bundle and conservatively merges it.

## ADR-042 — Active project persistence stores a relative path

Only the path relative to startup-approved `JARVIS_FILE_SEARCH_ROOT` is persisted. This keeps the safety root authoritative and is more portable across machines than an absolute path.

## ADR-043 — Voice/remote execution remain post-hardware-validation

STT/TTS, Wake-on-LAN and remote workers depend on OS/audio/network security that cannot be meaningfully validated in this build container. Interfaces should be added only with target hardware available.


## ADR-044 — Device controls are fixed-target/fixed-argv and opt-in

Wake-on-LAN sends only to the preconfigured MAC/broadcast/port and VS Code launch uses a fixed argv with `shell=False`. Neither accepts model-generated commands or arbitrary CLI arguments. Both are disabled by default.

# v0.2.1 decisions (2026-09-13, claude)

## ADR-033 — num_ctx 는 프로필 자동값을 기본으로 두되 실측 근거가 있으면 덮어쓸 수 있다
**결정:** `Settings.num_ctx` 를 추가한다. `0` 이면 기존 `NUM_CTX_BY_PROFILE` 자동값을 그대로 쓰고,
0 이 아니면 그 값을 쓴다. `JARVIS_NUM_CTX` / `jarvis.local.json` / Web 설정 패널에서 지정할 수 있다.

**이유:** ADR-010 은 "num_ctx 는 config 가 결정한다"고 선언했지만 실제로는 `app.py` 에 하드코딩된
프로필 표가 유일한 결정자였고 사용자가 바꿀 수단이 없었다. 같은 LAPTOP 프로필이라도 VRAM 이
6GB 인 장비와 8GB 인 장비는 감당 범위가 다르다. 실측 결과 이 장비(8GB)에서 qwen3:8b 는 ctx 8192
까지 GPU 100% 를 유지했다. 프로필 기본값을 바꾸면 VRAM 이 더 적은 LAPTOP 을 깨뜨리므로,
기본값은 그대로 두고 override 만 연다.

**한계:** 값을 올릴 때 VRAM 초과는 자동으로 감지하지 않는다. 넘치면 llama.cpp 가 조용히 CPU 로
층을 넘기고 크게 느려진다(실측 6.6배). 그래서 기본은 0 이고, 직접 지정은 `PERFORMANCE.md` 의
실측 표를 근거로만 한다.

## ADR-034 — thinking 모드는 실측 근거 없이 켜지 않는다 (2026-09-19 결론 뒤집힘)

> **뒤집힘.** 아래 결정(끈다)은 **품질을 측정하지 않은 채** 속도만 보고 내린 것이다.
> 2026-09-19 에 `think` 하나만 바꿔 다시 재니 **품질이 확실히 더 좋았다.**
>
> ```text
> think ON  (10회)  평균 4.2/7   완전통과 6/10   69~101초
> think OFF ( 5회)  평균 2.8/7   완전통과 1/5    3.8~16.3초
> ```
>
> 코드 생성에서는 **켜는 것이 맞다.** 실패 4건은 전부 출력 예산 소진·타임아웃이지
> 추론 실패가 아니었다. 속도 차이는 약 10배로, 어제 적은 2.1 배는 짧은 일반 질문 기준이라
> 과제에 따라 크게 달라진다. 근거: `PERFORMANCE.md` 2026-09-19 항.
>
> **교훈:** "측정하지 않았다" 를 문서에 적어 두고도 기본값을 한쪽으로 정했다.
> 모르는 것은 모른다고 하고, 기본값을 정해야 한다면 그 사실을 결정문에 남겨야 한다.
> 후속 ADR 은 `ADR-045` 를 보라.
**결정:** 이 장비의 `ollama_think` 를 false 로 둔다.

**이유:** 채점 가능한 과제 3개에서 thinking ON 은 3.4배 느리면서 점수가 더 낮았다(1/3 대 2/3).
codegen 과제에서 실행 가능한 함수를 내놓지 못했다. 측정 근거는 `PERFORMANCE.md` 3절이다.

**2026-09-18 근거 정정:** 위 비교는 오염되어 있었다 — think ON 은 ctx 4096, OFF 는 ctx 8192 로
컨텍스트가 같이 바뀌었고, 점수 차이는 과제 3개 중 1개뿐이었다. **"점수가 더 낮았다" 는
철회한다.** `think` 만 바꿔 다시 재니 **2.1배 느렸다**(24.7초 대 51.5초, 보이지 않는 추론 5211자).

> **결정은 유지하되 근거가 바뀐다: think 를 끄는 이유는 속도이지 품질이 아니다.**
> 품질 영향은 측정되지 않았다. 코드 벤치마크의 편차(0/7~6/7)가 효과를 덮는다.
> 품질을 이유로 삼으려면 편차를 이길 표본이 먼저 필요하다.

**주의:** 이것은 이 모델/이 장비의 측정 결과다. 모델을 바꾸면 다시 재야 한다.

## ADR-035 — 코드 경로에만 코딩 지시를 덧붙인다
**결정:** `CODING_SYSTEM_ADDENDUM` 을 `LOCAL_CODER`/`CLOUD_CODER` 경로와 `/edit-file` 제안
경로에만 system 메시지로 추가한다. 일반 대화 경로에는 붙이지 않는다.

**이유:** 실측에서 이것이 단일 개선으로 가장 컸다(생성 계산기 채점 1.7/7 → 3.7/7).
내용은 관찰된 실패를 직접 겨냥한다 — 문자열 replace 로 수식 변형 금지, eval 네임스페이스
누락 금지, 지정된 단위/밑 준수, 출력 전 예시 검산. 일반 대화까지 붙이면 매 요청의 컨텍스트만
축내고 이득이 없다.

**한계:** 이것으로도 7/7 완전통과는 한 번도 나오지 않았다. 설정·프롬프트로 올릴 수 있는
천장이다. 그 위는 승인형 샌드박스 실행 루프나 더 큰 모델이 필요하다. `PERFORMANCE.md` 8절 참조.

## ADR-036 — 모델이 쓴 코드의 실행은 실행마다 사람이 승인한다 (측정 결과 도입 안 함)

**상태:** 측정으로 닫힘. 앱에 도입하지 않는다. 아래 2026-09-18 항 참조. 설계 전문은 `SANDBOX_DESIGN.md`.

**맥락:** 코드 생성 품질이 설정·프롬프트의 천장(평균 3.0/7, 완전통과 0회)에 닿았다.
남은 수단은 생성→실행→오류 되먹임→재생성 루프뿐인데, 이는 `SECURITY.md` 의
"모델 출력은 권한이 아니다" 와 정면으로 부딪친다.

**위협 모델:** 공격자는 모델이 아니라 **모델이 읽는 데이터**다. 이 프로젝트는 파일을
읽어 컨텍스트에 넣으므로, 파일에 심긴 지시가 모델을 거쳐 실행 코드가 될 수 있다.
프롬프트 인젝션이 곧 코드 실행이 된다.

**결정:**
1. 격리(임시 cwd, `python -I -S`, 환경변수 청소, 타임아웃, 출력 상한)는 **피해 완화 층이며
   보안 경계가 아니다.** 자식 프로세스는 사용자 권한으로 돌고, Windows에서 표준
   라이브러리만으로 이를 막을 수 없다. 문서와 화면에 그렇게 밝힌다.
2. 경계는 **실행 전 승인**이다. `/edit-file` → `/apply-edit ID --yes` 와 같은 모양으로,
   `/run-code ID --yes` 가 실행마다 필요하다. 한 번의 승인이 이후 반복을 덮지 않는다.
3. 승인 직전에 **실행할 코드 전문**을 보여 준다.
4. 샌드박스는 프로젝트에 쓰지 않는다. 파일로 남기려면 기존 workspace 승인 흐름을 탄다.
5. 금지 문자열 블록리스트는 넣지 않는다. 우회는 쉽고 착각만 준다.

**따라오는 결론:** 2번 때문에 되먹임 루프가 앱에서 **자동으로 돌지 않는다.**
그래서 순서를 바꾼다 — 효과를 **개발자 도구에서 먼저 측정**하고(`scripts/grade_codegen.py
--refine N`, 이미 `--execute` 를 요구함), 효과가 확인된 뒤에 앱에 넣을지 다시 논의한다.
효과가 작으면 경계를 열 이유가 없다.

**2026-09-18 측정 결과 — 질문이 닫혔다:** 되먹임 루프를 `grade_codegen.py --refine 2` 로
8회 측정했다. **8회 중 7회가 두 번 고쳐도 점수가 전혀 움직이지 않았다**(최초 평균 2.5/7 →
되먹임 후 3.0/7, 완전통과 0/8). 평균 상승분은 단 한 번의 실행 때문이며 편차 범위 안이다.
모델이 `'(' was never closed` 같은 구체적 오류를 받고도 같은 코드를 다시 쓴다.

> **그러므로 앱에 코드 실행 경계를 열지 않는다.** 얻는 것이 없다.
> 이 ADR은 "구현 보류" 가 아니라 **"측정에 근거해 도입하지 않음"** 으로 닫는다.
> 더 큰 모델(RTX 5080 / 14B급)에서 같은 측정을 다시 하기 전까지 재논의하지 않는다.
> 근거: `PERFORMANCE.md` 8절, `logs/codegen-grade-*.json`.

**대안과 기각 사유:**
- 한 번 승인으로 N회 자동 반복: 2~N회차는 사용자가 못 본 코드다. 승인의 의미가 사라진다.
- 컨테이너/VM 격리: 이 프로젝트의 "표준 라이브러리만" 제약과 로컬 우선 목표에 어긋난다.
- 블록리스트: 위 5번.

## ADR-037 — 경험 저장소는 기억과 분리하고, 불변·결정적 분할·출처 추적을 스키마로 강제한다

**맥락:** `PROJECT_ALEX.md` 4·7·9·10절이 모델 계보와 학습 파이프라인을 선언했지만 저장소에는
그것을 담을 자리가 없었다. 감사 결과: 모델 교체(Provider 추상화)와 에이전트 협업(보드·ADR·게이트)은
잘 되어 있으나, **학습 신호는 절반만 잡히고**(`route_events.correction` 은 쌓이지만 통계 출력에서
끝난다), **내보내기 경로는 0이며**, **모델 버전 개념이 없다.**

**결정 1 — 기억과 경험을 다른 테이블로 둔다.**
`memories` 는 프롬프트에 실리는 사용자 편집 가능한 키-값이다. `experiences` 는 학습·평가
코퍼스이며 사용자가 고칠 수 없다. 한 테이블에 섞으면 출처 추적이 거짓이 된다 —
학습에 들어간 레코드를 나중에 수정할 수 있으면 "어느 데이터로 학습했는지" 를 보증할 수 없다.

**결정 2 — 경험 본문은 모델 독립 필드로 저장한다.**
`situation` / `judgment` / `rationale` / `choice` / `outcome` 다섯 필드로 나눈다.
특정 모델의 채팅 템플릿으로 굳히지 않는다. 템플릿 변환은 **학습 직전에** 한다.
기반 모델을 바꿔도 축적한 경험이 그대로 살아야 `PROJECT_ALEX.md` 5절의 migration 이 성립한다.

**결정 3 — 본문은 불변이고 `content_hash` 로 강제한다.**
검토 메타데이터(`stage`, `verdict`, `correction`, `reviewed_*`)만 변경 가능하다.
본문을 고치면 해시가 어긋나 `verify_experience_integrity()` 가 잡는다.

**결정 4 — 단계를 컬럼으로 분리한다 (9절의 흐름을 스키마로).**
`stage`: `raw` → `candidate` → `approved` / `rejected`. 학습은 `approved` 만 본다.
사람 검토를 거치지 않은 것은 구조적으로 학습에 들어갈 수 없다.

**결정 5 — 학습/평가 분할은 캡처 시점 수동 태깅이 아니라 ID 해시로 결정한다.**
사람이 고르면 흥미로운 사례가 학습으로 몰려 평가셋이 실제 분포를 대표하지 못한다.
`sha256(experience_id)` 의 하위 바이트로 20% 를 `eval` 에 배정한다. 재현 가능하고 편향이 없다.

**결정 6 — 평가셋 오염을 약속이 아니라 제약으로 막는다.**
`training_pool` 뷰(`stage='approved' AND split='train'`)를 만들고 **학습 추출은 이 뷰만 쓴다.**
"평가용은 학습에 쓰지 말자" 는 규칙은 언젠가 깨지지만, 뷰는 안 깨진다.

**결정 7 — 어떤 경험이 어느 버전에 들어갔는지 남긴다.**
`training_runs`(모델 버전, 기반 모델, 시각)와 `training_members`(run × experience)를 둔다.
M3 에서 성능이 떨어졌을 때 무엇 때문인지 역추적할 수 있어야 롤백이 의미를 가진다.

**결정 8 — 답변 품질 피드백 경로를 새로 만든다.**
기존 `/wrong` 은 **라우팅만** 고친다. 답변 자체에 대한 신호가 없으면 쌓을 것이 없다.
`/good`, `/bad [이유]` 를 추가해 직전 답변을 경험으로 기록한다.

**기각한 대안:**
- `memories` 테이블 확장: 결정 1의 이유. 사용자 편집과 불변 코퍼스는 양립하지 않는다.
- 캡처 시점 수동 train/eval 태깅: 결정 5의 편향 문제.
- 대화 로그를 그대로 학습: 이 모델은 정상 코드를 버그로 지목하고 오류를 보고도 같은 코드를
  다시 쓴다(`PERFORMANCE.md` 8절). 검토 없는 학습은 오류를 증폭한다.

## ADR-045 — 코드 경로에서는 thinking 을 켠다

**결정:** 코드 생성·편집 경로에서 `think` 를 켠다. 짧은 일반 대화 경로는 별도 측정 전까지 유지한다.

**근거 (2026-09-19, `think` 만 변경):**

| | 평균 | 완전통과 | 회당 시간 |
|---|---|---|---|
| think ON (10회) | 4.2/7 | **6/10** | 69~101초 |
| think OFF (5회) | 2.8/7 | 1/5 | 3.8~16.3초 |

**대가는 속도다 — 약 10배.** 코드 생성처럼 추론이 깊은 과제일수록 thinking 이 오래 생각한다.
짧은 대화에서 잰 2.1 배를 일반화하면 안 된다.

**따라오는 문제:** 실패 4건이 전부 `num_predict` 4096 소진(잘림)과 `request_timeout` 120초
초과였다. thinking 이 예산을 먼저 먹는다.

**2026-09-19 예산 상향 측정 (n=10, `num_predict` 7000, `request_timeout` 300):**

| | 평균 | 완전통과 | 실패 원인 |
|---|---|---|---|
| np 4096 | 4.2/7 | 6/10 | 잘림 3 · 타임아웃 1 |
| **np 7000** | **5.5/7** | **7/10** | 예산소진 2 · 틀린코드 1 |

**핵심은 평균이 아니라 토큰 분포다.**

```
완전통과 7회 : 2,650 ~ 4,712 토큰  (최대 4,712)
실패     2회 : 7,000 토큰 정확히   (done_reason=length)
```

**4,712 와 7,000 사이에서 끝난 회차가 하나도 없다.** 이 과제에서 모델은 약 4,700 토큰 안에
끝내거나, 아니면 멈추지 않는다. 예산을 더 줘도 폭주 회차는 그만큼 더 쓸 뿐이다.

> **따라서 `num_predict` 는 5120 으로 정한다.** 관측된 모든 성공(최대 4,712)을 8% 여유와
> 함께 덮고, 폭주는 164.8초가 아니라 약 120초에 끊는다. 7000 은 성공률을 더 올리지 않고
> 실패에만 시간을 더 쓴다. **예산은 상한이지 성능 손잡이가 아니다.**

**남은 문제 — 예산으로 못 고친다.** 폭주 2회는 thinking 을 29,772자 / 32,046자 하고도
코드를 한 글자도 못 냈다(성공 회차는 평균 14,662자). 반복 루프가 아니다(`repetition 0%`).
그냥 생각에서 못 빠져나온 것이다. 다음에 시험할 것은 예산이 아니라 **생각을 짧게 하는
프롬프트**와 **`done_reason=="length"` 일 때의 재시도**다.

**측정 신뢰도 주의:** 이 표의 이전 판(np 4096)은 실패 사유를 잘못 적고 있었다. 채점기가
잘린 응답의 여는 ``` 를 코드에 남겨 1행 `SyntaxError` 를 만들었고, 빈 응답을 `NameError`
7개로 찍었다. 2026-09-19 에 고쳤다(`tests/test_codegen_extract.py`). **점수는 유효하지만
그때의 '원인' 기록은 믿지 말 것.**

**미측정으로 남는 것:** 일반 대화 경로에서의 thinking 품질 영향. 코드 결과를 그대로
일반화하지 않는다.

## ADR-046 — 코딩 지시에서 검산을 코드 뒤로 미룬다

**결정:** 채택했다. `CODING_SYSTEM_ADDENDUM` 에서 사전 검산 줄을 빼고, 코드를 다 쓴 뒤에
한 번만 검산하라는 세 줄을 끝에 붙였다. **이 줄들의 순서에 의미가 있다.**

**문제:** ADR-045 로 think 를 켜자 남은 실패가 한 종류로 좁혀졌다 — 성공 회차의 2배를
생각하고도 코드를 한 글자도 못 내는 폭주다(29,772 / 32,046자 대 14,662자,
`done_reason=length`, `repetition 0%`). 예산으로는 못 고친다. 예산을 올리면 그만큼 더
생각할 뿐이고, 실제로 폭주 2회가 초 단위까지 같은 164.8초였다 — 예산 천장을 채우는 시간이다.

**가설:** `CODING_SYSTEM_ADDENDUM` 의 `"코드를 내놓기 전에 사용자가 준 예시 입력을 하나씩
되짚어 본다"` 가 그 방향을 부추긴다. 검산을 코드 **뒤로** 미루면 폭주가 줄 것이다.

**측정 (n=10, 프롬프트만 변경, `PERFORMANCE.md` 13절):**

| | 기본 | +짧게 생각 |
|---|---|---|
| 완전통과 | 7/10 | 10/10 |
| `done_reason=length` | 2 | 0 |
| thinking 최댓값 | 32,046자 | 23,150자 |

중앙값은 −9% 로 거의 안 움직였고 **최댓값이 −28% 움직였다.** 모델이 전반적으로 간결해진
것이 아니라 폭주만 끊긴 것이며, 이것이 가설이 예측한 모양 그대로다.

**확인 측정 (각 팔 n=20):**

| | 기본 | +간결 |
|---|---|---|
| 평균 | 5.40/7 | **6.60/7** |
| 완전통과 | 12/20 | **18/20** |
| 예산 소진 | 4 | 1 |
| 틀린 코드 | 4 | 1 |

**완전통과 P(B ≥ 18) = 0.032.** 이것이 채택 근거다.

> **내가 세웠던 메커니즘 가설은 기각한다.** n=10 에서 thinking 최댓값이 −28% 움직인 것을
> 보고 "폭주를 끊는 지시" 라고 설명했고, **점수는 유의하지 않으니 메커니즘이 근거** 라고
> 여러 번 적었다. n=20 에서 그 −28% 는 −5% 로 사라졌고(둘째 판에서 B 도 30,338자 폭주가
> 나왔다), 대신 점수 쪽이 유의해졌다. **맞는 결론을 틀린 이유로 믿고 있었다.**
>
> 실제로 줄어든 것은 폭주만이 아니라 **틀린 코드도 4 → 1** 이다. 검산을 코드 뒤로 미루니
> 코드를 더 정확하게 쓴다. 가설의 방향은 맞았지만 작동 경로는 예상과 달랐다.
>
> **"폭주를 막으려면 이렇게 하면 된다" 로 이 ADR 을 인용하지 마라.** n=20 에서
> 예산 소진은 4 → 1 이고 단독으로는 p ≈ 0.17 이다.

**채택 시 확인한 것:**
- 코드 경로에만 붙는다(ADR-035 와 같은 경계). `CODING_SYSTEM_ADDENDUM` 은 `LOCAL_CODER`/
  `CLOUD_CODER` 경로와 `/edit-file` 제안 경로에만 들어가므로 일반 대화는 그대로다.
- **정체성 재측정: 6.0/7 유지**(`logs/identity-20260919-082055.json`). I-2 만 실패하는 것도
  ALEX-M0 기준선과 같다. 프롬프트를 바꿨으니 정체성이 흔들리지 않았는지 확인해야 했다.
- 376 tests PASS, RELEASE VERIFICATION PASS.

**미측정으로 남는 것:** 일반 대화 경로에서의 영향. 붙이지 않았으므로 영향이 없어야 하지만
잰 적은 없다. 그리고 이 결과는 `qwen3:8b-q4_K_M` 의 것이다 — **모델을 바꾸면 다시 재야 한다.**

**참조:** `scripts/grade_codegen.py`(`BRIEF_THINKING_ADDENDUM`, `--brief-thinking`) ·
`logs/codegen-A-default.json` · `logs/codegen-B-brief.json` · `PERFORMANCE.md` 13절


## ADR-047 — 기본 로컬 모델을 `gpt-oss:20b` 로 (측정 근거, 재검토 조건 있음)

**결정:** 사용자가 웹 설정에서 바꾼 `gpt-oss:20b` 를 유지한다. 근거를 뒤늦게 측정해 기록한다.

**측정 (2026-09-21, `PERFORMANCE.md` 19절):**

| | qwen3:8b-q4_K_M | **gpt-oss:20b** |
|---|---|---|
| 파이썬 6과제 × 2회 | 측정 중 | **12/12** |
| Verilog 8비트 애더 | 미측정 | **101,024 벡터 PASS, 린트 0** |
| 정체성 | 6.0/7 (**I-2 0/3**) | **7.0/7** |
| tok/s | 47.4 | 23.4 |

**이 결정의 무게는 I-2 에 있다.** `PROJECT_ALEX.md` 4절과 `IDENTITY.md` 7절이 I-2 를
"프롬프트로 닿지 않는 행동" 이라며 **ALEX-M1 의 첫 학습 목표**로 못박았다.
`DEFAULT_SYSTEM_PROMPT` 에 이미 해당 지시가 있는데도 8b 가 0/3 이었기 때문이다.

> **학습으로 풀어야 한다고 규정한 문제가 모델 교체로 해결됐다.**
> 이것은 ADR-037(경험 저장소)의 전제를 부정하지 않는다 — 저 저장소는 여전히
> **사용자의 일에 특화**시키는 수단이다. 다만 **"일반적 행동 결함을 학습으로 고친다" 는
> 계획은 먼저 더 나은 베이스 모델을 찾아본 뒤에 세워야 한다.** 학습은 비싸고 모델 교체는 싸다.

**대가:** 속도가 절반이다(47.4 → 23.4 tok/s). 그리고 13.07GB 라 8GB VRAM 에 다 안 들어가
57% 가 시스템 RAM 에 상주한다.

**구조 때문에 그 대가가 작다:** MoE 라서 매 토큰 일부 전문가만 읽는다. 57% 오프로딩인데도
42% 만 오프로딩된 밀집 14b(10.3 tok/s)보다 두 배 이상 빠르다(17절).

**최적화 여지가 없음을 확인했다:**
- `num_gpu` 강제는 **역효과**다(17절). auto 23.4, 강제 24층 8.7 tok/s.
- KV 캐시 양자화도 **이득이 없다.** ctx 8192 와 4096 에서 전체 크기가 13.07G 로 같다 —
  캐시가 0.13G 뿐이라 줄일 것이 없다. 병목은 캐시가 아니라 **모델 가중치 13.07G** 다.

**재검토 조건:**
1. ~~같은 파이썬 12과제에서 **8b 가 12/12 를 낸다면**~~ — **2026-09-21 확인: 8b 도 12/12 였다.**
   그런데 그 6과제가 세 모델을 전부 만점으로 통과시켜 **변별력이 없었다**(`PERFORMANCE.md` 20절).
   어려운 6과제를 추가해 다시 재니 **8b 3/6, 20b 6/6** 이다(21절). **조건은 해소됐고 ADR-047 을
   유지한다.** 조건을 세울 때 "어느 자로" 를 적지 않은 것이 느슨했다 — 다음부터 자까지 적는다.
2. RTX 5080 에서는 13.07G 가 16GB VRAM 에 **통째로** 들어간다. 오프로딩이 사라지므로
   데스크톱에서는 이 표가 통째로 달라진다. 반드시 다시 재라.

**2026-09-21 저녁 재확인 (`PERFORMANCE.md` 24절).** 두 가지를 더 재고 결정을 유지한다.

1. **코딩 전용 모델을 시험했고 탈락했다.** `qwen2.5-coder:14b` 는 파이썬 5/6 이지만
   **RTL 2/6** 으로 범용 20b(6/6)보다 훨씬 나쁘고, 정체성 5.0/7(n=3)로 하한선 미달이다.
   `measured_vs_guess`(I-2)를 **세 번 다** 틀렸다 — 편차가 아니라 성질이다.
   **"범용 + 코딩 전용을 합치면 되지 않나" 에 대한 측정된 답이다.**
2. **비교가 불공정했고 내가 그렇게 만들었다.** 표에서 20b 만 `think off` 였다.
   앱 설정(`think on`)으로 맞춰 다시 재니 **20b 6/6 · 147.6초**, **9b 5/6 · 545.8초** 다.
   20b 가 `tok/s` 는 낮은데(23.4) 벽시계는 3.7배 빠르다 — 9b 가 생각을 훨씬 길게 쓴다.
   **처리량은 대기시간이 아니다. tok/s 로 모델을 고르면 틀린다.**

**`gpt-oss:20b` 를 랩탑·데스크톱 양쪽 기본 모델로 설정했다**(`jarvis.local.json`,
백업 `jarvis.local.json.bak-20260921-2010`).
3. 정체성 7.0/7 은 **키워드 채점**이다. 판정자가 아니다(`tests/test_identity_scoring.py`).

**미측정:** 긴 대화에서의 요약 품질, 30턴 이상 안정성, `/edit-file` 실사용 품질.


## ADR-048 — 학습 목표를 일반 능력에서 도메인으로 옮긴다

**결정:** "학습으로 ALEX 의 일반 능력을 올린다" 는 목표를 접는다. 학습은 **사용자의
도메인과 이 저장소의 관습**에만 쓴다. 경험 저장소(ADR-037)는 그대로 두되 **무엇을 모을지가
바뀐다.**

**이유 — 올릴 여지를 보이지 못했다:**

| | qwen3:8b | gpt-oss:20b |
|---|---|---|
| 파이썬 쉬움 6과제 | 12/12 | 12/12 |
| 파이썬 어려움 6과제 | **3/6** | **6/6** |
| 파이썬 최고난도 6과제 | 미측정 | **6/6** |
| 정체성(IDENTITY.md) | 6.0/7 (**I-2 0/3**) | **7.0/7** |
| Verilog 8비트 애더 | 미측정 | 101,024 벡터 PASS |

**두 가지가 같은 날 무너졌다.**

1. **I-2.** `PROJECT_ALEX.md` 4절은 I-2 를 "프롬프트로 닿지 않는 행동" 이라며 ALEX-M1 의
   첫 학습 목표로 삼았다. 모델을 바꾸니 7.0/7 이다. **학습이 필요 없었다.**
2. **코딩.** 세 단계를 만들어도 `gpt-oss:20b` 의 한계가 나오지 않았다.
   **올릴 여지가 있다는 것을 보이지 못했으므로 올리겠다는 계획은 근거가 없다.**

> **"학습으로 고친다" 를 계획하기 전에 더 나은 베이스 모델을 먼저 찾아라.**
> 학습은 비싸고(데이터 수백~수천 건, QLoRA, 평가 체계) 모델 교체는 다운로드 한 번이다.
> 2026-09-21 까지 이 저장소는 그 순서를 반대로 잡고 있었다.

**그래서 무엇을 모으는가 — 바뀐 것:**

| 이전 계획 | 지금 |
|---|---|
| I-2 같은 일반 행동 결함 | **접는다.** 모델 교체로 해결됨 |
| 일반 파이썬 코딩 | **접는다.** 한계를 못 찾음 |
| — | **RTL·리소그래피 등 사용자 도메인** |
| — | **이 저장소의 관습**(경계, 승인 흐름, 문서 규칙) |
| — | **개발 중의 판단과 그 정정**(`import-corrections`) |

도메인이 값어치 있는 이유는 **베이스 모델이 알 수 없는 것**이기 때문이다. 더 나은 모델을
받아도 사용자의 RTL 관습은 여전히 모른다. 반면 일반 코딩은 다음 모델이 그냥 더 잘한다.

**ADR-037 을 폐기하지 않는다.** 경험 저장소·분할·계보 추적은 그대로 필요하다.
바뀐 것은 **목적어**뿐이다.

**재검토 조건:**
1. 최고난도 6과제에서 **실패가 나오면** 그 실패 종류가 새 학습 목표 후보다.
2. ~~사용자 도메인 과제(RTL 채점 하네스)에서 실패가 나오면~~ — **2026-09-21 확인: RTL 6과제도
   6/6 이다**(`PERFORMANCE.md` 23절). 교과서 과제로는 학습 목표가 나오지 않는다.
   **과제가 사용자의 실제 코드에서 와야 한다** — 베이스 모델이 학습 데이터에서 본 적 없는
   것에만 학습할 거리가 있다. 리소그래피 진단, 사내 RTL 관습, 공정 제약 같은 것이다.
3. 이 표는 8GB VRAM 노트북 기준이다. 데스크톱에서 더 큰 모델이 돌면 다시 재라.

**한계:** 파이썬 18과제와 Verilog 1과제로 "한계가 없다" 를 말할 수는 없다. **한계를 찾지
못했다**가 정확한 표현이다. 그리고 이 벤치마크는 전부 **짧고 자족적인 함수**다 —
긴 파일 수정, 여러 파일에 걸친 변경, 기존 코드베이스 이해는 재지 않았다.


## ADR-049 — 덮어쓰이는 설정은 사용자가 보는 곳에서 그렇다고 말해야 한다

**결정:** 앱이 사용자 설정을 무시하거나 덮어쓸 때, 그 사실이 사용자가 실제로 보는
화면에 나와야 한다. 로그나 진단 전용 출력만으로는 부족하다.

**계기 (2026-09-21):** `jarvis.local.json` 의 `coder_model` 이 ADR-047 이 정한
`gpt-oss:20b` 인데, `build_providers` 가 랩탑 프로필에서 `single_model_on_laptop` 때문에
`coder = general` 로 덮어쓰고 있었다. 설정은 파일에도 설정 패널에도 그대로 남아 있고
아무 일도 하지 않았다.

**그리고 어느 화면에도 안 나왔다.** `/status` 는 모델을 아예 안 찍었고, `/gpu` 는
`id(provider) in seen` 으로 같은 객체를 건너뛰어 coder 줄 자체를 생략했다. 공유 상태가
단일 모델 기계와 구분되지 않았다. 그 결과 **ADR 이 정한 모델이 안 돌고 있는데
테스트는 438개가 전부 통과했다.**

**조치:** `/status` 가 실효 모델 두 개를 이름으로 찍고 공유일 때 그렇다고 표시한다.
`/gpu` 는 coder 줄을 생략하는 대신 왜 없는지 말한다. `tests/test_status_models.py` 4개.

> **테스트 통과는 "코드가 의도대로 동작한다" 는 증거지 "설정이 의도대로 적용돼 있다" 는
> 증거가 아니다.** 조용히 이기는 설정이, ADR 을 아무도 안 고쳤는데 ADR 이 거짓이 되는
> 경로다.

**한계:** 지금 고친 것은 두 명령뿐이다. 웹 설정 패널은 여전히 무시되는 값을 그냥
보여준다. 같은 규칙을 적용해야 하지만 UI 변경이라 따로 다룬다.

**2026-09-25 같은 종류 하나 더 — 런처가 옛 코드 서버를 조용히 재사용했다.** 런처는 같은
프로젝트의 서버가 떠 있으면 그것을 쓴다(두 개 띄우지 않으려고). 그런데 그 뒤에 코드가
바뀌었는지는 보지 않았다. 웹 읽기(ADR-051)를 넣고 런처를 다시 켰더니 07:56 부터 떠 있던
서버가 응답해서 `/status` 에 새 기능이 없었다 — claude 가 `python.exe` 서버를 두고
`pythonw.exe` 만 종료했기 때문이다. 사용자는 업데이트 후 더블클릭하면 똑같이 옛 버전을 쓰게 된다.
**조치:** 서버가 불러온 코드의 **내용** 지문을 `/health` 에 싣고(`web.LOADED_CODE`), 런처가 디스크
코드와 비교해 다르면 "앱 종료 후 다시 실행" 을 안내하고 멈춘다. 지문이 없는 서버는 이 검사
이전 버전이므로 역시 옛 코드로 본다. 수정 시각이 아니라 내용으로 잰다 — 압축 해제는 시각만 바꾼다.
`tests/test_launcher_stale.py` 9개, 실제 앱으로 거부 → 앱 종료 → 재실행 → 재사용까지 확인.


## ADR-050 — 작업 중에 받은 정정을 그 자리에서 경험으로 남긴다

**결정:** `experiences.py add --file <json>` 으로 누구든(사용자·claude·코덱스) 측정이나
사람이 자기 판단을 뒤집은 순간을 바로 경험으로 기록한다. dry-run 이 기본이고,
`source` 로 중복을 막고, `split` 은 받지 않는다.

**문제:** 경험 저장소를 채우는 문이 `/good` `/bad`, `/apply-edit` `/discard-edit`,
학습 시도뿐이었다 — 전부 사용자가 ALEX 와 직접 주고받은 것이다.
`import-corrections` 는 문서에 이미 적힌 정정을 수확하지만 목록이 스크립트에
하드코딩돼 있어 새 정정마다 스크립트를 고쳐야 했다.

**그래서 claude 와 코덱스가 하는 작업은 저장소에 하나도 안 들어갔다.** 그런데
`GPT_NEXT.md` 2절이 지적했듯, **더 큰 모델이 근거보다 확신 있게 말했다가 측정에 의해
교정되는 기록**이야말로 ALEX 가 못 배우는 바로 그 행동(I-2)의 교재다.

**설계 제약 (ADR-037 을 그대로 따른다):**
- `split` 을 받지 않는다. 사람이 고르면 흥미로운 사례가 학습으로 몰려 평가셋이
  분포를 대표하지 못한다. id 해시로만 정해진다.
- 본문 5필드는 `content_hash` 로 고정돼 나중에 못 고친다. 그래서 기본이 dry-run 이다.
- `stage='raw'` 로만 들어간다. **쓴 쪽이 승인하면 자기참조다** — 승인은 사용자 몫이다.

**첫 적용:** 2026-09-21 하루의 정정 6건을 이 문으로 넣었다. 확률적 픽스처를 결정적이라고
단언한 것, 낡은 메모를 믿고 끝난 일을 넘긴 것, `--peek` 없이 남의 큐를 소비한 것,
테스트 통과를 설정 적용의 증거로 읽은 것, 돌고 있지 않은 조건으로 벤치마크한 것,
한 모델의 비용을 설정 일반의 비용으로 일반화한 것. 경험 12 → 18건.

**한계:** 무엇을 기록할지는 여전히 판단이다. 자동으로 "정정처럼 보이는 것" 을 고르면
그것이 곧 모델의 자기 채점이 된다. 이 문은 기록을 **쉽게** 만들 뿐 **자동화하지 않는다.**
그리고 `training_pool` 은 아직 0이다 — 승인이 없으면 문만 생긴 것이다.


## ADR-051 — 웹 읽기(`/search`, `/web`)를 넣는다. 로컬 전용은 인터넷 금지가 아니다

**결정:** 사용자가 명령으로 부를 때만 웹을 **읽어서** 로컬 모델에게 준다. 표준 라이브러리만
쓴다. 결제·주문·로그인은 하지 않는다. 기본값은 켜짐(`web_enabled`).

**계기 (2026-09-24~25):** 사용자가 ALEX 에게 쇼핑 정보를 찾아달라고 했더니 "외부 API 나 인터넷에
접근할 수 있는 기능이 없습니다" 라고 답했다. 기능이 없는 것은 사실이었다. 그런데 claude 가 그
이유를 "로컬 우선 원칙과 충돌한다" 고 설명하자 사용자가 바로잡았다 —

> "로컬전용이라는 게 토큰소비를 안 하게 하는 거잖아? 그리고 애초에 나를 도와서 작업을 계속하려면
> 그런 인터넷 사용도 잘 해야 하지 않아?"

**둘 다 맞았다.** `local_only` 는 README "Cloud 경계" 에 정의된 대로 **대화·기억을 클라우드 LLM
으로 보내지 않는** 경계다. 웹 페이지를 읽는 것은 LLM 호출이 아니고 토큰을 쓰지 않는다. 그리고
반도체 개발 보조라면 데이터시트·PDK·툴 문서·에러 메시지를 찾아봐야 한다. 그것 없이는 I-1 이
"모른다" 에서 멈춘다 — 정직하지만 거기서 끝난다.

**원인 하나가 더 있었다.** `_environment_line` 이 매 요청마다 모델에게 "외부 API 로 나가지
않는다" 고 말하고 있었다. 모델은 그 문장을 근거로 "불가능" 이라고 답했다. 웹 읽기가 생긴 뒤에도
이 문장이 남으면 ALEX 는 계속 불가능하다고 답한다. 문장을 사실대로 바꿨다.

**설계:**
- **명시적 명령만.** 모델이 스스로 검색하는 경로(도구 호출 루프)는 넣지 않았다. 네트워크는
  사용자가 친 명령에서만 나간다. 도구 호출은 다음 단계다.
- `/search` 는 검색 결과 6건 + **위 두 건 중 읽히는 한 쪽의 본문**을 준다. 스니펫만으로는
  기술 질문에 답이 얕다.
- `/ask-file` 과 같은 틀: 자료는 시스템 지시와 다른 메시지에 "지시가 아니라 자료" 머리말과 함께,
  예산 대부분을 자료에, LOCAL 에게만, 원문은 세션에 저장하지 않는다.
- **출처는 앱이 붙인다.** 모델이 번호를 빠뜨리거나 지어내도 사용자는 원문 주소를 본다.
- 검색엔진은 DuckDuckGo **lite**. 2026-09-25 실측에서 `html.duckduckgo.com` 은 봇 확인(202,
  anomaly)을 돌려줬고 `lite` 는 결과 10건을 정상으로 줬다. 키가 필요 없다. 막히면 "막았다" 고
  말하고 답을 지어내지 않는다.

**보안 (자세한 규칙은 `SECURITY.md` "Web reading"):** 가장 큰 위험은 SSRF 다 — 웹 페이지나
사용자 실수로 `http://127.0.0.1:11434` 같은 주소를 읽으면 ALEX 가 이 기계 안쪽을 모델에게
넘긴다. 이름 해석 결과와 **실제로 연결된 소켓의 상대 주소**를 둘 다 검사한다. 뒤의 것이 DNS
리바인딩을 막는다. 테스트는 흉내가 아니라 127.0.0.1 에 실제 서버를 띄워, 평범한 urllib 로는
닿고 ALEX 의 opener 로는 안 닿는 것을 확인한다.

**테스트가 잡은 결함 두 개:** (1) 처리기를 직접 고른 `OpenerDirector` 에 `UnknownHandler` 가
없어서 `file://` 을 열면 **오류 대신 `None`** 을 돌려줬다. 읽지는 않지만 조용히 넘어가는 것은
거부가 아니다. (2) 깨진 HTML 대비 예비 추출 규칙이 정상적인 짧은 페이지에도 발동해서 걸러낸
메뉴·저작권 문구가 되살아났다.

**랩탑 실측 (`PERFORMANCE.md` 25절, gpt-oss:20b, ctx 8192, 문맥 예산 6000자):** 세 질문 모두
사실과 맞는 답을 출처와 함께 냈다. 5~10초(첫 호출은 모델 로딩 포함 30초). 자연어로 "인터넷에서
찾아줄 수 있어?" 라고 물으면 이제 "불가능" 대신 `/search` 를 안내한다.

**한계:**
- 모델이 스스로 검색하지 않는다. 사용자가 명령을 알아야 한다.
- 자바스크립트로 그리는 페이지와 PDF(데이터시트에 흔하다)는 읽지 못한다.
- 랩탑 문맥 예산 6000자라 긴 문서는 앞뒤만 들어간다. 데스크톱은 24000자다.
- 페이지 내용 속 지시를 막는 것은 머리말뿐이다. 모델이 넘어갈 수 있다. 그래서 웹 답에는
  파일 수정·명령 실행이 연결돼 있지 않다 — 편집은 여전히 diff + 명시 승인 + 백업을 거친다.
- 쇼핑의 **결제·주문은 이 결정 밖이다.** 읽기 전용이 이 ADR 의 경계다.

**같은 날 추가 1 — 명령 없이 채팅으로 (사용자 요청).** "명령어를 입력해야 한다는데.. 그냥 채팅에서
검색해달라고 하면 바로 되도록". 라우터에 `TOOL_WEB_SEARCH` / `TOOL_WEB_READ` 를 넣었다. 네트워크가
나가는 조건은 여전히 **사용자의 요청**이다 — 알아보는 방법이 `/search` 에서 자연어로 넓어졌을 뿐이고,
모델이 스스로 나가기로 정하는 도구 호출 루프는 여전히 없다. 규칙:
- 공개 주소가 붙어 있으면 읽기. localhost·사설 IP 는 웹으로 보내지 않는다(막힐 게 뻔하고 "이 주소
  왜 안 열려?" 에는 모델의 답이 필요하다).
- "검색해/검색 좀/구글링" 은 그것만으로 검색. "검색해줄 수 있어?" 는 한국어에서 대개 정중한 부탁이다.
- "찾아봐/찾아줘" 는 코드·디버깅 단어가 없으면 검색. "인터넷에서/웹에서" + 찾아/알아봐, "최신/가격/
  뉴스" + 찾아/알아봐 도 검색.
- 빼는 것: 이 기계 안의 일("코드에서", "프로젝트에서", "내 컴퓨터", "아까 말한"), 능력 질문("찾아줄
  수 있어?", "가능해?"), 디버깅("에러 로그 찾아줘"), 파일 검색(기존 규칙이 먼저).
- 검색엔진에는 부탁 말투를 걷어낸 말을, 모델에게는 사용자가 한 말 그대로를 준다.
- 골든 코퍼스 29 -> 50건. **실측이 한 건을 잡았다:** "RTX 5080 VRAM 용량 좀 찾아봐" 가 기존
  시스템 정보 규칙의 `vram` 한 단어에 걸려 이 노트북의 RTX 4070 8GB 를 보여줬다. 내가 고른 예문
  22개와 코퍼스 44건은 전부 맞았는데 기존 규칙과 새 규칙이 겹치는 곳을 담지 않았다. 찾아보라는
  말이 있고 이 기계를 가리키지 않으면 웹으로 가게 고쳤다.

**같은 날 추가 2 — ALEX 가 자기 클라우드 기능을 지어냈다.** "클라우드 AI 에게 요청보내는건?" 에
"연결 기능이 없다, 인터넷이 금지돼 있다, 방화벽을 풀고 requests 로 코드를 짜야 한다" 고 답했고,
"다시 확인해봐" 에는 "제가 현재 환경에서 확인한 바로는" 이라며 같은 말을 반복했다. 연결 코드는
있고(OpenAI·Anthropic·Gemini), 인터넷은 금지가 아니며, 방화벽은 없고, 확인할 수단은 없다.
원인은 09-21 의 "GPT-4 기반입니다" 와 같다 — 모델은 우리가 준 글만 본다. `_environment_line` 을
세 번 고치며 매번 실제 모델로 다시 쟀다.

| | 수정 전 | 1차 | 2차 | 3차 |
|---|---|---|---|---|
| 정확한 답 | 0/2 | 0/4 | 3/6 | **7/8** |
| 새로 지어낸 것 | 방화벽·requests | 없는 메뉴, /search→클라우드 | `/enable-cloud`, 키를 설정 파일에 | "재시작하면 된다" |

1차는 "설정에서 정한다" 를 메뉴가 있다는 뜻으로 읽혔다. 그래서 헷갈린 지점마다 **"무엇이 아니다"**
를 같이 적었다(로컬 전용은 인터넷 금지가 아니다, 화면에 켜는 메뉴는 없다, /search 는 클라우드가
아니다, 명령은 /help 에 있는 것만). 남은 1건은 프롬프트로 더 조이지 않았다 — 문장이 길어질수록
웹 자료 예산이 줄고, 이것이 바로 ALEX-M1 의 학습 목표(I-2)이기 때문이다. 오답 5건을 모델 출력
그대로 경험으로 기록했다.

**같은 날 추가 3 — 위 7/8 은 새 대화창에서만이었다.** 사용자의 실제 대화(앞선 오답이 쌓인 세션)에서는
새 코드로도 같은 오답이 나왔다. 복사본 재현으로 프롬프트 수정 네 가지를 쟀지만 어느 것도 확실히
되돌리지 못했다(`PERFORMANCE.md` 25-3). 결정적인 원인은 기록 속 모델 자신의 오답이고, 세션 요약은
과거 오답을 system 자리로 되돌려 보내는 두 번째 경로다. **프롬프트를 더 늘리지 않는다.** 구조 수정
후보 두 가지(`/bad` 한 답을 문맥·요약에서 빼기, 요약에서 ALEX 자신에 관한 주장 빼기)는 다음 ADR 의
몫이다 — 사용자 대화 데이터를 문맥에서 빼는 결정이라 사용자 확인이 필요하다.


## ADR-052 — 노트북에서 데스크톱 ALEX 로: Tailscale 위의 SSH 터널. ALEX 는 열지 않는다

**결정:** 노트북은 SSH 로컬 포워딩(`127.0.0.1:8766` → 데스크톱 `127.0.0.1:8765`)으로 데스크톱 ALEX 에
붙는다. 두 기기는 Tailscale 로 잇는다. **ALEX 는 여전히 127.0.0.1 에서만 듣고, 로그인 기능을 새로
넣지 않는다.** 로그인·암호화는 SSH 가, 집 밖 연결은 Tailscale 이 맡는다.

**계기 (2026-09-25):** 사용자가 데스크톱을 주 작업기로 쓰고, 노트북을 들고 나가서도 이어 쓰고,
나중에 상시 서버용 미니 PC 를 두려 한다. "노트북을 들고 밖에 나갈 경우 어떻게 연결되는데?" →
"그렇게 할 수 있도록 지금 프로그램 수정해줘".

**데이터의 집은 한 곳이다.** 기기마다 따로 쓰면 대화와 경험(학습 데이터)이 흩어진다. 지금은 데스크톱,
미니 PC 가 생기면 미니 PC 가 집이다. 인터넷이 없을 때만 노트북 ALEX 를 쓰고 `/state-export` →
`/state-import` 로 합친다(ADR-041 — 살아 있는 SQLite 를 네트워크·클라우드 동기화 폴더에 두지 않는다).

**코드 수정 없이 되는 이유:** ALEX 의 Host 검사(`web._valid_host`)는 호스트 이름만 본다. 터널로 들어온
요청은 `127.0.0.1:8766` 이라 통과한다. 토큰·CSP·Host 방어가 그대로 유지된다.

**검토하고 버린 것:**
- ALEX 를 `0.0.0.0` 에 열기 — 로그인이 없다. 같은 망의 누구나 쓴다(SECURITY "Web": LAN 공개 금지).
- `tailscale serve` 로 8765 를 사설망에 내기 — Host 가 Tailscale 이름이 되어 Host 검사에 걸린다. 검사를
  넓히면 DNS 리바인딩 방어가 약해진다. 필요해지면 따로 결정한다.
- 데스크톱 Ollama 를 노트북에서 직접 쓰기 — Ollama 는 인증이 없다. 미니 PC → 데스크톱 위임 때 새 ADR 로.

**만든 것:**
- `jarvis/remote.py` + `connect_desktop.cmd` — 터널을 열고, 너머가 정말 ALEX(로컬 전용)인지 `/health`
  로 확인한 뒤에만 브라우저를 연다. 데스크톱 코드 지문이 다르면 알린다. 실패하면 ssh 의 영어 오류를
  할 일로 바꿔 말한다(`-E` 로 로그를 파일로 받아서 — 비밀번호 입력 창은 그대로 둔다).
  `setup` 은 암호 없는 ed25519 키를 만들고 연결 대상을 저장하고 데스크톱에 붙일 한 줄을 준다.
- `scripts/remote_setup_desktop.ps1`(관리자, UTF-8 BOM) — OpenSSH 서버, **방화벽을 Tailscale 범위
  (100.64.0.0/10, fd7a:115c:a1e0::/48)로 제한**, 키 등록(관리자·일반 계정 파일 둘 다, 권한은 SID 로),
  로그인 시 ALEX 자동 실행(`start_alex_background.vbs` → `launcher --background`).
- `launcher.run_background` — 창·브라우저 없이 띄우고 실패는 `logs/autostart.log` 에만.

**보안 규칙:**
- ssh 는 고정된 인자 목록으로 셸 없이 부른다(ADR-044). 설정의 host/user 는 '-' 시작·공백·특수문자를
  거부한다 — `-oProxyCommand=...` 같은 값이 옵션으로 읽혀 프로그램을 실행하는 길을 막는다.
- 노트북 쪽 포트를 `127.0.0.1` 로 명시한다 — 카페 와이파이의 다른 사람이 노트북 포트로 들어오지 못한다.
- `StrictHostKeyChecking=accept-new` — 처음 보는 데스크톱은 기억하고, 바뀐 신원은 거부한다.
- 데스크톱 스크립트는 ALEX 를 켜지 않는다 — 관리자 창에서 켜면 ALEX 가 관리자 권한으로 돈다.
- 키에 암호가 없다(한 번에 붙게). 해지는 Tailscale 관리 화면에서 노트북 제거 + 데스크톱 키 줄 삭제.

**검증:** `tests/test_remote.py` 34(가짜 ssh·가짜 데스크톱: 성공, 코드 불일치, 옛 데스크톱, ssh 실패,
터널은 열렸는데 ALEX 없음, ALEX 아닌 응답, 로컬 전용 아님, 기존 터널 재사용, 남이 쓰는 포트, 주입 거부,
설정 보존·백업, 스크립트 인코딩). 진짜 `ssh.exe` 로 실패 경로 둘("Connection refused", "Could not
resolve hostname")이 맞는 안내로 바뀌는 것, 진짜 `ssh-keygen` 키를 데스크톱 스크립트 검사식이 받는 것을
확인. PowerShell 5.1 파서로 스크립트 문법 확인(실행은 안 함 — 노트북에 SSH 서버를 설치하게 되므로).

**한계:**
- **실제 데스크톱에서 끝까지 해 보지 않았다.** 노트북에는 SSH 서버가 없고 데스크톱에 접근할 수 없다.
- 데스크톱이 켜져 있고 **로그인돼 있어야** 한다. 원격 켜기(WOL)는 같은 공유기 안에서만 되고, WOL 로
  부팅하면 로그인 화면에서 멈춰 ALEX 가 자동으로 켜지지 않는다. 항상 켜 두는 미니 PC 가 해법이다.
- 휴대폰은 안 된다(SSH 터널이 필요하다).
- 밖에서 한 대화는 노트북에 남는다 — 이동 번들은 기억·할 일·경험만 옮긴다.

## ADR-053 — 최신 외부 정보가 필요한 자연어 질문은 ALEX가 웹에서 찾는다

**계기 (2026-09-25):** 사용자가 "26년 현재기준 rtx5090 가격은?"과 "상하이 여행 호텔 추천해줘"를
물었을 때 ALEX는 오래된 지식으로 답하거나 `/search`를 쓰라고 했다. 사용자는 명령어 없이 알아서
검색하도록 요청했다.

**결정:** 라우터가 가격·시세·날씨·뉴스·출시 정보, 여행·숙소 등 현실 세계의 추천, 학습 시점 이후의
연도를 묻는 질문을 `tool/web_search`로 보낸다. 사용자가 검색 명령어를 적을 필요가 없다. 검색어만
외부 검색엔진에 나가며 대화 기록과 장기 메모는 전송하지 않는다. 가져온 자료는 로컬 모델에만 준다.
웹 읽기의 주소·연결 검사와 출처 목록은 ADR-051을 그대로 따른다. 모델이 스스로 임의의 도구를
고르는 경로는 없다.

**경계:** ALEX 자신에 관한 질문, 로컬 파일·코드·오류 조사, 이전 대화 지시, 검색 기능 자체에 대한
질문은 자동 검색에서 뺀다. 무엇이 검색되는지는 `jarvis/router.py`의 규칙과 라우터 코퍼스로 고정한다.

**검증:** Windows Python의 전체 검증 596 tests, 보안 smoke 13/13, 라우터 코퍼스 통과
(`logs/claude-autosearch-verify-20260925.txt`). 실제 모델과 인터넷으로 위의 두 질문을 재현해 각각
`tool/web_search`와 출처 URL이 붙는 것을 확인했다. "그 명령어 없어도..."는 `local_general`로 남았다
(`logs/claude-autosearch-live-20260925.json`). UI의 출처 제목 링크는 ADR-051의 렌더러가 처리한다.

**남은 문제:** 실제 가격 답변은 출처가 붙어도 정확하지 않았다. 가격 질문에서 권장가격과 판매가격을
구분하라는 지시를 두 차례 시험했지만 잘못된 수치가 계속 나와 그 지시는 채택하지 않았다
(`logs/codex-autosearch-price-recheck*-20260925.json`). 검색 결과 품질과 답의 사실성은 별도 개선이
필요하다. 출처 링크가 있다는 사실을 답의 검증으로 간주하지 않는다.

## ADR-054 — 데스크톱에서는 Ollama 플래시 어텐션을 끈다

**계기 (2026-09-25 17:27):** 사용자가 설정 화면에서 데스크톱 모델을 `qwen3.5:122b` 로 바꾸고 말을
걸었더니 1분 뒤 오류가 났다. `logs/ollama-server.log`: 모델을 올리던 `llama-server` 가
`CUDA error: shared object initialization failed` — `ggml_cuda_flash_attn_ext_mma_f16_case` 의
`cudaFuncSetAttribute(... MaxDynamicSharedMemorySize ...)` — 로 멈추고 `exit status 0xc0000409`,
`Load failed`, HTTP 500(17:28:40). **죽은 곳은 플래시 어텐션(FA) 커널이다.**

**크래시는 매번이 아니었다.** 같은 FA on 서버에서 바로 이어진 두 번째 로드는 43.95초 만에 올라가
답했다(17:29:55, HTTP 200). FA on 2회 중 1회 크래시. 코덱스가 같은 포터블 Ollama v0.34.4 를 별도
포트에서 FA off 로 띄워 1회 로드·응답을 확인했다(49.0초 로드, `logs/codex-qwen122b-flashoff-*`).
Ollama 저장소 이슈에 같은 Blackwell 계열 오류와 `OLLAMA_FLASH_ATTENTION=0` 우회가 있다.
**근거는 표본 수가 아니라 위치다** — FA 를 끄면 죽은 커널을 아예 타지 않는다. 표본으로는 아직 약하다.

**결정:** 런처가 Ollama 를 띄울 때 **DESKTOP 프로필에서만** `OLLAMA_FLASH_ATTENTION=0`
(`launcher.ollama_environment`). 노트북·UNKNOWN 은 Ollama 기본값(FA on) 그대로다.
- 코덱스가 처음 넣은 것은 모든 프로필에 적용이었다. 노트북은 FA off 로 잰 적이 없고, 지금까지의
  노트북 측정(PERFORMANCE 12~25)은 전부 FA on 조건이다. 재지 않은 기계의 조건을 바꾸지 않으려고 좁혔다.
- 모델별로 켜고 끄지 않는다. 설정 화면에서 모델을 바꿔도 Ollama 는 다시 뜨지 않으므로, 모델을
  기준으로 하면 17:27 처럼 바꾼 직후에 다시 죽는다.
- 프로필은 이제 Ollama 를 띄우기 **전에** 정한다. `force_profile` 도 그 전에 적용된다. 순서가
  뒤바뀌면 데스크톱에서 FA on 으로 뜨므로 연결 테스트로 고정했다(`tests/test_launcher_ollama_env.py`, 6).

**대가:** 데스크톱 20b 생성 속도 173.6 → 155.6 tok/s(약 -10%), 120b 23.6 → 23.2 tok/s(같음).
짝 비교가 아니다 — FA on 값은 phase0(512토큰), off 값은 코덱스 비교(256토큰)다
(`logs/codex-flashoff-compare-20260925.json`).

**측정에 미치는 영향:** PERFORMANCE 26 의 FA on 수치(phase0 의 20b·120b, stage1 의 앞 7건)는 이제
앱의 실제 조건이 아니다. `desktop_num_ctx` 판정(20b 가 그 ctx 에서 100% GPU 인가)은 FA 를 끄면 바뀔 수
있다 — FA 없는 어텐션은 계산 버퍼가 ctx 에 비례해 커진다. 배치·속도는 FA off 로 다시 잰다. FA on 파일은
지우지 않고 그대로 둔다(증거).

**Ollama 가 이미 떠 있으면** 런처는 그 서버를 쓴다 — 이 환경은 Ollama 를 새로 띄울 때만 적용된다.
실제 값은 `logs/ollama-server.log` 의 마지막 `server config` 줄(`OLLAMA_FLASH_ATTENTION:false`)이다.
측정 스크립트는 이 줄을 읽고 시작한다.

**재검토 조건:**
1. **FA off 에서 한 번이라도 같은 크래시가 나면** 이 결정의 가설이 틀린 것이다. stage1 이 FA off 로
   `qwen3.5:122b` 를 다섯 번 넘게 새로 올리므로 그것이 첫 확인이다.
2. 포터블 Ollama 를 올리면 FA on 으로 `qwen3.5:122b` 를 여러 번 올려 본다. 안 죽으면 되돌린다.
3. 데스크톱 모델 결정(PERFORMANCE 26)이 FA 가 필요 없는 모델로 끝나면, FA on/off 를 같은 조건으로
   짝 비교해서 다시 정한다(20b 의 10%).

**2026-09-26 확인 (claude, PERFORMANCE 26-8):** 조건 1 — FA off 로 `qwen3.5:122b` 를 GPU 에 13번 새로 올렸고(09-25 18:17 ~ 09-26 08:34, Ollama 로그)
FA 커널 충돌은 없었다. 08:13 에 `0xc0000409` 가 한 번 났지만 워밍업의 FA 커널이 아니라 생성 도중의 `CUDA error: unknown error` 였다 —
그 뒤 GPU 가 사라지고 Ollama 가 81GB 모델을 RAM 에만 다시 올리다 PC 가 비정상 재시작했다(Kernel-Power 41). 09-25 18:14 의
비정상 재시작은 17:28 충돌·SQLite `disk I/O error` 46분 뒤였고 그 사이 이 모델을 올린 기록은 없다 — 관련은 모른다. 조건 3 — `qwen3.5:122b` 는 replay 0/3 으로
후보에서 떨어졌다. FA off 를 둘 이유가 사라졌으므로 결선 뒤 FA 를 되돌릴지 사용자와 정한다.

**같은 시각의 SQLite `disk I/O error` (한 번):** 17:27:40 ~ 17:28:40 사이 화면 새로고침(`list_tasks`)이
`disk I/O error` 로 실패했다. 122b(약 75GiB, Windows+CUDA 에서는 mmap 을 끄고 RAM 에 전부 올린다)를
올리던 중이었다. 원인은 확인하지 못했다. 이후 재발 없음, 18:14 재부팅 뒤 `PRAGMA integrity_check` = ok.

## ADR-055 — 승인된 경험을 다음 비슷한 질문에 다시 쓴다 (모델을 바꾸지 않는 성장의 첫 층)

**계기 (2026-09-25):** 사용자: "모델을 교체 안 하더라도 자체적으로 성장하는 AI 를 만들고 싶었다."
GPT 와 나눈 설계(기억·Skill·판단·모델의 네 층, 경험 → 결과 → 교훈 → 검색 → 학습 후보 → LoRA →
회귀 검사 → 사람 승인)를 참고해 보강을 요청했다. 같은 날 ALEX 는 앱에 있는 자동 검색(ADR-053)을
"자연어로는 안 된다, /search 를 써야 한다" 고 두 모델(qwen3.5:122b, qwen3.8:27b)에서 되풀이했다 —
같은 대화의 앞선 오답을 따르는 PERFORMANCE 25-3 의 패턴이다.

**대조:** PROJECT_ALEX 9절의 루프 가운데 기록·결과·학습 후보 풀(ADR-037 `training_pool`)·학습 이력·
사람 승인(CLI)은 있었다. **없던 것은 승인된 경험이 다음 대화에 쓰이는 길이다.** 경험은 기록만 되고
읽히지 않았다. 사람이 오답을 바로잡아도 가중치를 다시 학습하기 전에는 ALEX 의 행동이 바뀌지 않았다.

**결정:**
1. 승인된 **train** 분할의 `answer` 경험 가운데 이번 질문과 관련된 것을 최대 3개(`lesson_context_limit`,
   0 = 끔) system 메시지로 넣는다(`jarvis/lessons.py`). 요약 뒤에 둔다 — 문맥이 빠듯하면 `_fit_context` 가
   뒤쪽 system 부터 남기므로, 사람이 확인한 교정이 모델이 쓴 요약보다 먼저 살아남는다.
2. 넣지 않는 것: **eval**(성장을 재는 자다 — 답에 흘리면 잰 값이 부풀어도 모른다), raw·candidate·rejected
   (사람이 보지 않은 자기 오답의 되먹임), route·edit·codegen, 그리고 **identity** — 대부분 에이전트의
   작업 판단 기록이라 대화에 붙이면 엉뚱했다. 성향은 가중치 학습 후보로 남긴다(PROJECT_ALEX 10절, 기억할
   것과 학습할 것은 다르다). 선택 함수 자체가 stage·split·kind 를 다시 검사한다.
3. 쓰였으면 답 끝에 `— 참고한 승인 경험: id (/growth)` 를 붙인다(ADR-049). 답을 못 했거나 클라우드로
   갔으면 붙이지 않는다. 클라우드에는 장기 메모처럼 보내지 않는다.
4. 사람이 앱 안에서 검토한다: `/review`(대기 목록), `/review ID`(전체 보기), `/review ID 승인|거절`.
   `/growth` 는 루프의 단계별 수. `/status` 에 `lessons:` 줄 — 교훈은 프롬프트를 바꾸므로 측정 전에 읽는
   줄에 있어야 한다.
5. **승인은 사용자만 한다.** 에이전트(클로드·코덱스)는 `/review` 도 `experiences.py review` 도 쓰지 않는다
   (ADR-037·050 — 쓴 쪽이 승인하면 자기참조).

**고르는 법과 그 한계 (재서 정했다):** 한글은 두 글자 조각으로 비교하고, 질문의 뜻 있는 단어가 경험에
나오는지 단어 단위로 센다 — 어간(첫 조각)이 있어야 하고 같은 어간은 한 번. 질문 단어의 절반(최소 2)이
나와야 한다. 사용자의 실제 질문 60개에 기존 경험 전부를 **승인됐다고 가정**(메모리에서만, DB 는 그대로)
하고 점검했다. 처음 방식(조각 2개 겹침)은 27/60 에 붙었고 엉뚱한 것이 많았다("= 버튼이 두개야" → 테스트
픽스처 기록, "날짜시간 다 알려줘" → 리스트 뒤집기). 지금 방식은 20/60, 명백한 오탐 1건("지금 설정한
모델로도 충분히…" → 클라우드 교정). 말을 크게 바꾼 질문("너 학습은 언제까지 된거야")은 놓친다
(`logs/claude-lesson-dryrun-20260925.txt`, 재현 `logs/probe/lesson_dryrun*.py`).

**효과 — 첫 측정은 절반 (PERFORMANCE 27):** 사실 줄을 앞·뒤에 두는 등 프롬프트 수정 네 가지는 대화 속
오답에 모두 졌다(PERFORMANCE 25-3). 교훈은 "앞선 답이 틀렸다" 고 구체적으로 짚는다. `replay` 로 같은 대화를
복사본에서 되돌리고 교정 경험을 **복사본에서만** 승인한 A/B(20b, n=3): 앞선 오답의 되풀이 2/3 → **0/3**,
확인할 수 없는 것의 단정 1/3 → 0/3. 그러나 교훈이 시킨 "앞서 틀렸다고 정정하기" 는 0/3 → 0/3 — 해로운 말은
멈췄지만 스스로 바로잡지는 않았다. 바로 그 상황의 교정이 들어간 경우이고 n=3 이다. 120b 는 측정 중.
사용자가 아무것도 승인하지 않은 지금은 동작에 영향이 없다.
**재측정 (PERFORMANCE 27-2, 격리 확인 뒤):** 되풀이 20b·120b 모두 3/3 → 0/3. 120b 는 교훈이 있을 때 처음으로
"앞서 제가 인터넷이 차단돼 있다고 말씀드린 부분은 잘못된 안내였습니다" 라고 스스로 정정했다(1/3, 20b 0/3).

**다음 층 (이 ADR 밖):** Skill(검증된 코드·절차 재사용), 판단(경로·도구 선택 — `/wrong` 과 라우터 코퍼스가
이미 일부), 모델(LoRA). LoRA 는 학습 가능 0건이라 아직 이르다. 사용자의 실제 작업에서 승인된 train 경험이
쌓이고, eval 분할과 PERFORMANCE 26 의 결선 채점기(파이썬·RTL·정체성·재생)로 "나빠지지 않는지" 를 가르는
관문이 선 다음이다.

## ADR-056 — 모델이 모르는 것을 알아차리면 스스로 검색한다 (web_search 도구)

**계기 (2026-09-25, 사용자 두 번째 요청):** 사용자가 "모르겠으면 너가 직접 확인해서 답해줘야지" 라고 하자 ALEX 는
"'○○ 검색해줘' 라고 치시면 앱이 찾아 준다, 나는 호출 못 한다" 고 답했다. 사용자: "꼭 내가 명령어를 넣어야지만
인터넷 검색이 가능하다는데..." ADR-051·053 은 "모델이 스스로 나가는 경로는 없다" 로 끝났다 — 자동 검색은 라우터가
질문 모양(가격·뉴스·"검색해줘")을 보고 할 뿐, "이건 내가 모른다" 는 판단은 모델만 할 수 있는데 모델에게 길이 없었다.
이 결정은 그 줄을 바꾼다. CLAUDE_NEXT 5절 9번에 사용자 요청으로 남아 있던 항목이다.

**확인 먼저 (구현 전, `logs/claude-tool-call-probe-20260925.txt`):** 이 Ollama(v0.34.4)에 `tools` 를 주면
qwen3.8:27b 와 gpt-oss:20b 모두 "모르면 직접 확인해서 답해줘" 에는 `web_search` 를 불렀고, "리스트 뒤집는 법" 에는
부르지 않았다. 호출은 스트림 끝에 한 덩어리(`message.tool_calls`)로 온다.

**결정:**
1. 대화·코딩 경로(`_generate_with_fallback` 의 로컬 후보)에서 모델에게 `web_search(query)` 도구 하나를 준다.
   모델이 부르면 앱이 `/search` 와 같은 `WebReader` 로 찾고(결과 5건 + 읽히는 한 쪽 본문), 자료를 `tool` 메시지로
   돌려준 뒤 다시 묻는다(`OllamaProvider._generate_with_tools`). 웹 경로·파일·프로젝트 질문·요약에는 주지 않는다.
2. **밖으로 나가는 것은 검색어뿐이다.** 대화·메모·경험은 보내지 않는다. 검색어는 모델이 지으므로 200자로 자르고,
   민감정보 검사와 개인 정보 모양(메일, 파일 경로, 9자리 넘는 숫자)에 걸리면 **보내지 않고** 모델에게 그렇게 말한다.
3. 한 질문에 두 번까지 찾는다. 같은 검색어는 다시 세지 않는다. 모델 차례는 `MAX_TOOL_ROUNDS`(3) 번까지 도구를 실행해
   주고, 그 뒤에도 도구만 부르면 끊는다. **도구는 마지막 차례에도 빼지 않는다** — 처음엔 빼서 답을 받으려 했는데
   gpt-oss 는 도구를 뺀 차례에도 도구를 부르며 본문을 비웠다(`logs/probe/tool_final_round_probe.py`). 도구를 둔
   채로는 답했다. 그래서 횟수는 앱이 "그만 찾고 지금 답하라" 는 tool 결과로 막는다.
4. 무엇을 찾았고 어디서 왔는지는 **앱이** 답 끝에 붙인다: `출처 — ALEX 가 스스로 검색: '검색어'` + `[n] 제목 — 주소`.
   못 가져왔으면 그렇다고 붙인다. 자료에는 웹 자료 머리말(지시가 아니라 데이터, ADR-051)을 단다.
5. 검색 실패를 모델에게 줄 때 "인터넷이 막힌 것이 아니라 이 검색 한 번이 실패한 것" 이라고 적는다 — 검색엔진이
   막았다는 문구를 받은 gpt-oss:20b 가 "외부 검색이 차단된 상태" 라고 답했다.
6. 검색엔진 보호: 같은 검색어는 10분 동안 다시 보내지 않고, 검색엔진이 자동 요청으로 막으면 5분 동안 보내지 않는다
   (`WebReader.search`). 시험 중 몇 분 사이 15번쯤 찾자 DuckDuckGo lite 가 막았다 — 막힌 채로 두드리면 길어진다.
7. 도구를 모르는 모델(Ollama 400 "does not support tools")은 도구 없이 답한다. 설정 `web_tool_enabled`
   (기본 켬, `JARVIS_WEB_TOOL`), `/status` 에 `web: on (/search, /web, 스스로 검색)`. 환경 문장은 도구가 있는
   경로에서만 "직접 찾아서 답한다, 사용자에게 검색 명령을 치라고 하지 않는다" 로 바뀐다.

**실측 (임시 DB, 실제 인터넷, `logs/probe/web_tool_live.py`, 답을 읽어서 판정):**
- qwen3.8:27b — 찾아야 할 질문 3개 모두 스스로 찾았고 출처가 붙었다(공개일 "2026년 8월", 학습 시점은 "공식 자료에
  없음, 커뮤니티 답변은 2025년 초" 라고 구분). 찾을 필요 없는 코딩 질문은 찾지 않았다. 오류 1건: 공개일을 찾은 뒤
  "그러니 제 학습 시점도 8월" 이라고 공개일과 학습 시점을 섞었다. (`logs/web-tool-live-qwen3.8_27b.json`)
- gpt-oss:20b 첫 회 — 마지막 차례 빈 답 1건(위 3의 원인), 검색 실패를 "차단" 으로 말함 1건(위 5의 원인). 고친 뒤
  둘째 회는 검색엔진이 막혀 있던 때라 실패 경로만 확인됐다: "차단" 이라는 말은 없어졌지만, 한 답은 자료 없이
  "2023년 말" 을 지어냈다(I-2). (`logs/web-tool-live-gpt-oss_20b*.json`)
- 시험 질문은 전부 반말이었고 모델의 답은 전부 존댓말이었다: qwen3.8 6/6, gpt-oss:20b 두 회 11/11(앱 오류 문구 1건
  제외). 같은 날 시스템 프롬프트에 넣은 존댓말 줄 때문으로 본다 — 넣기 전의 같은 조건은 재지 않았다.

**대가와 한계:** 도구 차례마다 모델을 한 번 더 부르므로 느린 모델(122b)은 답이 길어진다. 검색엔진은 키 없는 무료
DuckDuckGo lite 하나라 자주 찾으면 막힌다 — 다른 엔진을 더할지는 막히는 빈도를 보고 정한다. 모델이 자료 없이
지어내는 것(I-2)은 도구로 고쳐지지 않는다. 사용자가 시키지 않았는데 네트워크가 나가는 첫 경로이므로, 끄려면
`web_tool_enabled: false`.

## ADR-057 — 틀린 것·몰랐던 것은 자동으로 배운다 (사용자 결정): 바로잡힌 것은 승인, 찾은 사실은 후보

**계기 (2026-09-25):** `/review ID 승인` 을 꼭 입력해야 하느냐는 물음에 이어 사용자가 물었다: "잘못된 것, 몰랐던 것은
자동으로 학습하게 하는 건 문제가 되나? 잘못 학습할까 봐 그런 거야?" 문제는 누가 맞다고 판단하느냐라는 설명(ALEX 가
스스로 판단하면 자기 오답이 굳는다 — 같은 날 gpt-oss:20b 는 검색 실패 뒤 학습 시점을 지어냈고 qwen3.8 은 공개일을
학습 시점으로 착각했다)을 듣고, 세 선택지 가운데 **"ALEX 가 찾은 것까지 자동"** 을 골랐다. 추천은 "내가 바로잡은
것만" 이었다. 틀린 웹 자료가 교훈으로 굳을 위험은 사용자가 알고 고른 것이다.

**결정:**
1. **사용자가 대화에서 바로잡으면 그 말이 곧 승인이다.** 직전 답 다음에 오는 확실한 교정 표시("틀렸…", "잘못 알…",
   "그게 아니라", "아니야, …"로 시작, "…잖아"로 끝, "…텐데?")가 있으면 `situation`=직전 질문, `judgment`=직전 답,
   `correction`=사용자의 말 **그대로**로 기록하고 `approved`(reviewed_by `user(chat)`). 이유를 적은 `/bad` 도 같다
   (`user(/bad)`). 이유 없는 `/bad`·`/good` 은 무엇이 맞는지 모르므로 검토 목록에 남는다.
   **표시 없는 반박은 잡지 않는다**("26년 2월 기준 모델이야…", "…설정해뒀는데?") — "…는데?" 는 "어떻게 하는데?" 같은
   물음에도 붙어서 뺐다. "아니 괜찮아", "아니 그냥 궁금해서" 도 뺐다.
2. **ALEX 가 출처 있는 웹 자료로 답하면 그 답을 배운다** — 라우터 검색(/search·자동 검색)과 스스로 검색(ADR-056) 모두.
   날짜(`YYYY-MM-DD 웹에서 찾아 답했다`)와 출처 주소를 붙인다. 뺀다: 금방 바뀌는 것(가격·시세·날씨·뉴스·오늘·현재·
   최신…), 못 찾은 답("자료에 없", "찾지 못", "확인하지 못"…), 같은 질문(질문 해시로 한 번만). **90일이 지나면 쓰지
   않는다.** 이것은 `candidate`(reviewed_by `auto(found)`)로 둔다 — 교훈으로는 바로 쓰지만 가중치 학습 후보
   (`training_pool` = approved)가 아니다. 사람이 `/review` 로 승인해야 학습 후보가 된다.
3. 배우면 답 끝에 알린다: `— 바로잡아 주신 내용을 배웠습니다(id). 잘못 배웠으면: /review id 거절`,
   `— 찾은 내용을 배웠습니다(id, 90일 동안 참고). 틀렸으면: /review id 거절`. 거절하면 그 뒤로 쓰이지 않는다.
4. **eval 분할도 교훈으로 쓴다**(ADR-055 를 고친다). 빼면 사용자가 바로잡은 것 다섯 중 하나가 말없이 적용되지 않는다.
   eval 은 가중치 학습에서 빠지는 몫이고, **모델을 재는 측정은 교훈과 자동 학습을 끈다**
   (`JARVIS_LESSON_CONTEXT_LIMIT=0`, `JARVIS_AUTO_LESSONS=0` — stage1·2 스크립트). 교훈 효과를 재는 A/B 만 켠다.
5. 설정 `auto_lessons_enabled`(기본 켬, `JARVIS_AUTO_LESSONS`). `/status` 에 `자동 학습 on/off`, `/growth` 에
   바로잡은 것·찾은 것·검토 승인한 것의 수. 클로드·코덱스가 쓴 기록은 여전히 사람이 `/review` 로 승인한다.

**모델 가중치 학습(LoRA)은 이 결정 밖이다.** 교훈은 프롬프트에 참고로 넣는 것이라 언제든 빼면 되돌아가고 답 끝에
보인다. 가중치는 틀린 것이 모델 안에 녹아 찾기도 되돌리기도 어렵다 — 학습 후보는 사람의 승인, 학습 결과는 회귀·정체성
관문과 사용자 채택(PROJECT_ALEX 9절)을 그대로 거친다.

**남은 위험:** 사용자의 교정 자체가 틀릴 수 있다(같은 날 사용자는 "qwen 3.8이면 26년 2월 기준" 이라고 했는데 3.5 가
2월, 3.8 은 8월이다 — 이 문장은 표시가 없어 잡히지 않았다). 찾은 사실은 출처가 틀리면 틀린다(ADR-053 의 가격 오답).
교훈이 대화 속 오답을 이기는 힘은 절반이다 — 되풀이는 멈추지만 스스로 정정하지는 않았다(PERFORMANCE 27).
테스트: `tests/test_auto_lessons.py`(14), `tests/test_lessons.py` 개정. 670 tests PASS.

## ADR-058 — 자연스러운 교정 인식과 자동 학습 취소

**계기 (2026-09-25):** 사용자는 정해진 표현으로만 바로잡을 수 있고, 배운 것을 취소하려면
`/review ID 거절`을 입력해야 하는 흐름이 불편하다고 했다. ADR-057의 표시 규칙은
“2+2는 4입니다. 다시 계산해 주세요” 같은 분명한 반박도 놓친다.

**결정:**
1. 직전 답이 있는 로컬 대화·코딩 차례에 `remember_correction` 도구를 모델에게 준다. 사용자가
   직전 답을 바로잡았을 때만 호출하도록 설명한다. 기존의 명시적 표현 감지는 즉시 기록하고,
   모델 도구가 중복 호출해도 같은 출처의 경험은 하나만 남긴다. 기록의 교정 문장은 **모델 요약이
   아닌 사용자 문장**이다(기존 민감정보 가림·500자 제한 적용). 모델이 만든 `right` 인수는 저장하지 않는다. 사용자 교정의 승인 규칙은
   ADR-057과 같다(`user(chat)`). 도구 결과는 앞선 오류를 인정한 뒤 답하도록 안내한다.
2. “방금 배운 거 취소”, “그거 배우지 마” 같은 말은 모델 호출 전에 처리한다. **같은 대화의
   바로 직전 답에서 새로 배운 항목만** 거절한다. 다른 대화의 항목이나 이후 사람이 `/review`로
   승인한 항목은 바꾸지 않는다. 취소 요청 자체를 새 교정으로 배우지 않는다.
3. 답 끝의 학습 알림 옆에 `배운 것 취소` 버튼을 표시한다. 버튼은 `/undo-learning ID`를 호출하며,
   이 명령도 `user(chat)`·`user(/bad)`·`auto(found)`가 남긴 활성 항목만 취소한다.
   `/review ID 거절`은 기존의 명시적 검토 명령으로 유지한다.
4. 교정 도구는 로컬 모델에게만 준다. 웹 검색 도구와 같이 쓰지만, 교정 도구는 인터넷을
   호출하지 않는다. 기존 경험 저장소의 본문·split·학습 후보 경계는 그대로 유지한다.

**검증:** Windows Python 전체 릴리스 검증 676 tests(2 skip), router 65/65, security 13/13 통과.
실제 `qwen3.5:122b`를 사용자 DB와 분리한 임시 DB에서 시험했다
(`logs/codex-adr058-live-qwen122b-20260925.json`). 표시 없는 산술 교정은 도구를 1회 호출하고
“앞서 제가 틀렸습니다”라고 답했다. 감사·새 개인 사실·새 산술 질문은 도구 호출 0/3이었다.
표본이 작아 다른 모델이나 모든 문장에 대한 정확도를 보장하지는 않는다.

## ADR-059 — 긴 대화 요약은 같은 로컬 모델의 짧은 비사고 요청으로 생성한다

**계기 (2026-09-25):** 긴 대화에서 최근 기록 밖의 여섯 메시지가 쌓일 때마다 요약을 답변 전에 동기 생성한다.
기존에는 답변용 provider를 그대로 재사용해 `think on`과 최대 6144 출력 토큰을 적용했다. 요약 대기시간은
그대로 사용자의 답변 대기시간에 더해진다. 예전 ALEX의 잘못된 환경 설명이 압축 요약에 남아 재사용된 사례도 있다.

**결정:**
1. 요약에는 **같은 로컬 모델·같은 문맥 창**의 별도 Ollama provider를 쓴다. 답변용 설정이 `think on`이면
   요약만 `think off`로 보내고, 출력 상한을 `min(답변 상한, max(2048, summary_max_chars))`로 낮춘다.
   모델을 새로 내려받거나 클라우드에 보내지 않는다. 답변용 provider가 이미 `think off`거나 Mock이면
   기존 객체를 그대로 쓴다.
2. 요약 지시문은 오래된 assistant의 환경·설정 주장을 검증되지 않은 것으로 취급하고, 사용자 교정이
   충돌하면 그 교정을 우선한다. 다음 대화에 주입되는 요약에도 확정 사실이 아니며 현재 환경 정보·사용자
   교정이 우선한다고 표시한다. 저장된 과거 대화나 이미 있는 요약 본문은 수정하지 않는다.
3. 요약 생성 시간을 내용 없이 로그에 남긴다. provider 실패 시 기존의 추출식 요약으로 돌아간다.
   화면에서 모델이나 생각 설정을 바꾸면 요약 provider도 함께 다시 조립한다. 그러지 않으면 답변은
   새 모델인데 요약은 예전 모델을 계속 불러와 로딩 비용과 결과 불일치가 생긴다.

**검증:** Windows 릴리스 검증 679 tests(2 skip) PASS. 같은 합성 대화·같은 `qwen3.8:27b`/16K에서
기존 요청은 49.19초(로딩 9.85초, 생성 38.12초, 531토큰), 새 요청은 21.36초(로딩 0초,
생성 20.00초, 216토큰)였다. 둘 다 앞선 “인터넷 차단” 설명을 오류로 정리했다.
임시 DB를 쓰는 실제 `build_core` 경로에서 새 요약 provider가 `think off`/상한 3000으로 조립되고
8.72초에 올바른 짧은 요약을 저장했다. 시험은 한 쌍이라 일반적인 가속률이나 장기 요약 품질을
보장하지 않는다. 원자료는 `logs/summary-perf-ab-20260925.json`이다.

## ADR-060 — 측정용 환경변수가 로컬 설정을 확실히 이긴다

**계기 (2026-09-25):** FA off 모델 비교는 `JARVIS_WEB_TOOL=0`·`JARVIS_AUTO_LESSONS=0`을
Windows Python에 전달했지만, `load_settings()`가 로컬 설정을 병합할 때 필드 이름으로 만든
`JARVIS_WEB_TOOL_ENABLED`·`JARVIS_AUTO_LESSONS_ENABLED`만 확인했다. 환경에는 `0`이
있어도 로컬 파일의 `true`가 다시 적용됐다. 실제 replay에 웹 검색 알림이 나타나 발견했다.

**결정:** 두 필드의 짧은 이름과 전체 이름을 모두 환경변수 우선으로 인정한다. Stage 1·2는 시작 시
실효 설정이 `web_tool=False`, `auto_lessons=False`, `lesson_context_limit=0`인지 Windows Python으로
확인하고 다르면 측정을 중단한다. 잘못된 조건에서 재측정한 결과는
`logs/codex-backup-20260925-summary-perf/replay-adr059-env-bug/`에 보관하고 완료 표시를 지웠다.
변경 전 A/B와 모델 품질 비교도 이 격리 조건을 충족했다고 간주하지 않는다.

**검증:** 로컬 설정이 두 기능을 켠 상태에서도 `JARVIS_WEB_TOOL=0`·`JARVIS_AUTO_LESSONS=0`이
우선함을 Windows Python과 회귀 테스트로 확인했다. Stage 1·2 구문 검사와 릴리스 검증
681 tests(2 skip) PASS.

## ADR-061 — Model Catalog는 검증된 정확한 Ollama 태그와 추정 표시만 제공한다

**계기 (2026-09-25):** 사용자는 122B 초과·397B 미만의 모델을 찾고, 특히
`qwen3.8-flash-next:125b-a6b-q4_K_M`을 추가하되 현재 장비의 메모리 부족 예상과 MLX 제외를
요청했다. 초기 Catalog에는 실제 Ollama 태그·용량이 확인되지 않은 후보가 섞였고, 새 테스트는
기존 릴리스 검증에서 발견되지 않았다.

**결정:** Catalog는 웹 검색이나 scraping이 아닌 수동 검증 목록이다. 정확한 Ollama 태그와
출처·검증일·확인된 메타데이터만 다운로드 허용 목록에 넣는다. DeepSeek는 Catalog 후보에서
제외하되 이미 설치된 모델 목록은 그대로 보여준다. Windows/NVIDIA의 기본 후보에서는 MLX를
제외한다. Qwen 항목은 MoE 본체 125B, 활성 6B, 추가 n-gram embedding 51B를 별도 필드로
표시한다. 모델 상세정보의 약 177B는 추가 embedding 등을 포함하므로 125B와 같은 범위의
수치로 혼동하지 않는다. 추정은 OS·런타임·KV 캐시 여유를 고려해 `메모리 부족 예상`으로
표시하되 다운로드나 선택을 막지 않는다.

다운로드는 사용자가 확인한 정확한 Catalog 태그만 Ollama HTTP `/api/pull`에 전달한다. 진행
스트림의 `success`가 없으면 완료로 표시하지 않으며, 취소에는 서버가 준 요청 ID를 쓴다.
저장 경로는 런처가 현재 Ollama를 직접 띄워 확인한 경우에만 여유 공간을 표시한다. 이미
실행 중인 Ollama의 저장 위치는 이 API로 확정할 수 없으므로 모르면 `확인 불가`로 표시한다.

**검증:** 가짜 로컬 Ollama에 대한 다운로드 정상·거절·취소 HTTP 시험을 포함한 7개 새
`unittest`가 Windows와 WSL에서 통과했다. Windows 전체 릴리스 검증 688 tests(2 skip) PASS.
실제 대형 모델 다운로드·실행은 하지 않았다. 로그는
`logs/codex-model-catalog-qwen-verify-20260925.txt`다.

## ADR-062 — 모델 없는 지름길은 짧은 직접 요청일 때만 탄다

**계기 (2026-09-26 아침):** 사용자가 새로 받은 모델을 시험하려고 실습 노트북 문제(1,274자, 42줄)를
붙여 넣었다. 코드 속 예시 문자열 `'현재 GPU 알려줘'` 에 라우터가 `gpu 알려` 로 걸려 시스템 정보 표만
두 번 돌려줬고 모델은 한 번도 불리지 않았다 — "답변이 제대로 안나오는데". 시스템 정보·파일 검색·세션
이어가기 세 경로는 모델 없이 답을 대신하므로, 긴 글 속 낱말 하나로 타면 답이 통째로 사라진다.

**결정:** 세 지름길은 공백을 합친 글이 `SHORTCUT_MAX_CHARS`(120자) 이하일 때만 탄다. 긴 글은 원래 순서의
나머지 규칙(웹·코드·일반)으로 간다. 모델도 환경 문장으로 이 기계의 사양을 알기 때문에, 긴 글 속 사양
질문을 모델이 받아도 답할 수 있다. 골든 코퍼스의 지름길 문장은 19자 이하라 여유가 크다.
따옴표 속 낱말을 빼는 방식은 쓰지 않았다 — 붙여 넣은 문서·로그는 따옴표 없이도 같은 낱말을 담는다.

**남긴 것:** 웹 경로(주소 읽기, 명시 검색, 알아서 검색)는 그대로다. 긴 글에 주소나 "최신" 같은 낱말이
있으면 여전히 웹으로 가고, 검색어가 글 전체가 될 수 있다(붙여 넣은 내용이 검색엔진으로 나간다). 모델이 답을
하므로 이번 증상과는 다르지만, 알아서 검색에도 같은 길이 제한을 둘지는 사용자와 정한다 — "명령 없이 검색"
(ADR-053)을 긴 질문에서 줄이는 교환이다. 짧은 "이 모델 돌리려면 VRAM 얼마나 필요해?" 도 `vram` 한 낱말로
시스템 정보로 가는 오래된 오판이다(이번에 고치지 않음).

**검증:** 실패를 먼저 재현한 `tests/test_router.py::PastedTextTests` 2건(사용자가 붙인 노트북 모양, 세 지름길
낱말을 담은 긴 글 — 같은 낱말의 짧은 요청은 여전히 지름길). 사용자의 실제 메시지는 이제 `local_coder`.
라우터 골든 코퍼스 65/65, Windows 전체 릴리스 검증 705 tests(2 skip) PASS
(`logs/claude-router-paste-verify-20260926.txt`). 앱 재시작, 앱 = 디스크 지문 `693040a4437b9897`.

**결정 2 (2026-09-26 16:55, 사용자: "그냥 너가 진행해"):** 알아서 검색(ADR-053)에도 같은 120자 제한을 둔다. 부탁 말
(검색해줘·찾아봐)·주소 읽기는 길이와 상관없이 그대로다 — 사용자가 밖으로 보내라고 한 것이다. 긴 글의 최신 정보는 모델이
`web_search` 도구로 짧은 검색어를 만들어 찾는다(ADR-056, qwen3.8:27b 실측 3/3). 검증: `PastedTextTests` 에 긴 회의록
(‘요즘’·‘가격’·‘최신’ 포함)이 검색으로 가지 않고, 같은 글에 "검색해줘" 를 붙이면 검색하는 경우 — 고치기 전 라우터에서 실패 확인.
골든 코퍼스 65/65.

## ADR-063 — Coding Mode는 승인된 한 파일·한 테스트의 감독형 루프부터 시작한다

**계기 (2026-09-26):** 사용자는 ALEX가 Codex/Claude Code처럼 프로젝트를 이해하고 파일 수정·테스트·오류 분석·재수정을 이어가길 원한다. 기존 ALEX에는 안전한 프로젝트 검색/읽기와 diff가 있는 `SafeWorkspaceEditor`가 있으나, 테스트 실행·작업 단계 저장·재시험 연결은 없었다.

**결정:** 첫 Coding Mode는 승인된 프로젝트 안의 기존 파일 하나와 `tests/test_*.py` 하나를 대상으로 한다. 기준 테스트를 고정 인자 `python -m unittest discover`로 실행하고, 실패 시 로컬 코딩 모델에 진단 자료를 주어 기존 편집기의 수정안을 생성한다. 기준 테스트가 통과한 기능 작업은 `/dev-propose`로 수정안을 요청한다. 사용자가 diff를 확인해 `/apply-edit ID --yes`를 실행하기 전에는 파일을 바꾸지 않는다. 적용 후 같은 테스트를 다시 실행한다. 단계·목표·제한된 테스트 결과는 SQLite `app_state`에 저장하며, 미적용 수정안은 재시작 때 소멸한다. 테스트는 120초, 출력 2MB, 시도 3회로 제한하고 사용자의 중단 신호를 테스트 프로세스에 전달한다.

**경계:** 테스트 파일은 프로젝트 코드이므로 실행 자체의 부작용은 격리하지 못한다. 임의 터미널, 다중 파일의 원자적 패치, Git 자동 작업, 자동 승인/무한 재시도는 넣지 않는다. 기존 `/tree`, `/grep`, `/ask-project`, `SafeWorkspaceEditor`와 백업/undo를 재사용한다. 전체 설계·남은 과제는 `SYSTEM_AUDIT_20260926.md`에 있다.

**검증:** Windows 전체 release gate 722 tests(2 skip) PASS, 신규 개발·lifecycle·스트림 상태 테스트 포함. 앱 재시작 후 Coding Mode 화면과 명령을 HTTP로 확인했다. 실제 125B 모델의 수정안 생성은 수행하지 않았다.

## ADR-064 — 데스크톱 기본 모델은 qwen3.8:27b, 플래시 어텐션은 Ollama 기본값으로 되돌린다

**계기 (2026-09-26 16:55):** 결선(PERFORMANCE 26-9)이 끝나고 사용자가 남은 결정을 맡겼다 — "그냥 너가 진행해. 그리고 fan이
너무 돌더라고". 사용자는 그 사이 119GB `qwen3.8-flash-next:125b` 를 기본으로 골라 두었다(재지 않음, RAM 69GB 고정 + 33.6GB
디스크 매핑, 26-8 의 멈춤 위험이 가장 큰 모델).

**결정 1 — 모델:** `desktop_general_model`·`desktop_coder_model` = `qwen3.8:27b`(26 규칙 1~4 통과: 오답이 쌓인 대화 3/3,
fresh 8/8, 결선에서 20b 와 같거나 낫고, 대화형 중앙값 35.8초). 노트북 설정(`general_model` 20b)은 그대로. 125B 는 지우지 않았다
(설치된 채로 두며, 쓰려면 PERFORMANCE 26 의 자로 먼저 잴 것). 앱이 꺼져 있을 때 `jarvis.local.json` 의 두 줄만 바꿨다(ADR-049).

**결정 2 — FA:** ADR-054 는 데스크톱 전체에서 FA 를 껐다. 원인 qwen3.5:122b 가 결선에서 떨어졌고(replay 0/3), 끄면 qwen3.8:27b
27%·20b 22% 느려진다(26-2). 이제 **Ollama 기본값**(환경 변수 없음 → llama-server `--flash-attn auto`, 이 GPU 에서
"Flash Attention enabled" — 16:58 실측)으로 띄우고, 데스크톱 기본 모델이 FA 에서 죽은 계열(`FA_CRASH_FAMILIES = ("qwen3.5:",)`)일
때만 `OLLAMA_FLASH_ATTENTION=0` 이다. 빠를수록 같은 답에 GPU·CPU 가 덜 오래 돈다 — 팬 소음에도 이 방향이다.
주의: Ollama 서버 로그의 `OLLAMA_FLASH_ATTENTION:false` 는 "변수 없음" 표시다 — 실제 여부는 `--flash-attn` 과 `flash_attn` 줄을 볼 것.
런처는 떠 있는 Ollama 를 재사용하므로, 설정 화면에서 qwen3.5 로 바꿨다면 Ollama 까지 새로 떠야 FA 가 꺼진다.
FA off 조건으로 만든 측정 스크립트(`scripts/desktop_stage*.sh`)는 이제 시작할 때 ABORT 한다 — 의도된 동작이다.

**재검토 조건:** qwen3.8:27b 로 FA on 에서 로딩 실패·`0xc0000409` 가 나면 ADR-054 로 돌아간다. 125B 를 기본으로 쓰려면 먼저
기본 클럭에서 결선 자로 잰다(사용자는 오버클럭 상태, 26-8 덧붙임).

**검증:** `tests/test_launcher_ollama_env.py` — 데스크톱 + qwen3.8 은 기본값, 데스크톱 + qwen3.5 는 FA off(연결 테스트로 launch() 가
Ollama 를 띄우기 전에 모델을 정하는지까지), 노트북은 언제나 기본값. 고치기 전 런처에서 핵심 연결 테스트 실패 확인. Windows 전체
release gate 724 tests(2 skip) PASS(`logs/claude-decisions-verify-20260926.txt`). 백그라운드 실행(`start_alex_background.vbs`)으로
켜서 `/health` = 디스크 지문 `5e28c51ee93abf6d`, `/status` general=coder=`qwen3.8:27b`, qwen3.8 적재 로그 `warmup: flash attention is enabled`.

## ADR-065 — 여러 파일 변경은 '찾을 부분 → 바꿀 내용' 묶음으로, 모두 적용하거나 하나도 적용하지 않는다

**계기 (2026-09-26 밤):** 코덱스가 Coding Mode 4단계의 다음 순서로 "기존 파일의 일부 패치·새 파일 생성·여러 파일 변경 묶음,
원본 해시·묶음 승인·실패 시 rollback" 을 남겼다(`CODING_AGENT_STAGE4_20260926.md` 2번). 사용자: "이어서 작업해줘". 지금까지 편집기는
기존 파일 하나를 통째로 다시 쓰게 했다 — 큰 파일은 문맥 예산을 넘고, 여러 파일에 걸친 변경은 한 번에 승인할 수 없었다.

**결정:**
1. **모델 출력 형식:** 줄 번호 diff 가 아니라 `FILE: 경로` + `<<<<<<< SEARCH / ======= / >>>>>>> REPLACE`(찾을 부분이 파일에 **정확히 한 번**
   있어야 한다), 새 파일은 `NEW FILE: 경로` + `<<<<<<< CONTENT … >>>>>>> END`. 찾을 부분이 비면 파일 끝에 덧붙인다. 틀린 수정안은 적용 전에
   거부된다(0곳·2곳 이상·문법 오류·빈 파일). 모델 출력은 텍스트로만 해석하고 실행하지 않는다.
2. **관대한 해석(실측 근거):** gpt-oss:120b 는 꺾쇠를 6개 쓰고 파일 이름 다음에 `---` 를 넣고, 경로 앞에 `/` 를 붙이고, 덧붙일 때
   찾을 부분을 비웠다. 표시는 5~9개를 같은 것으로, `---` 는 건너뛰고, 앞의 `/`·`./` 는 프로젝트 기준으로 본다. 프로젝트 밖·숨김
   경로·없는 폴더·허용 밖 확장자·이미 있는 파일 만들기는 편집기가 그대로 막는다.
3. **적용:** 모든 파일의 원본 해시·새 파일 부재·문법을 다시 확인한 뒤 원본을 한 백업 폴더에(되돌리기 목록 `.alex-bundle.json` 과 함께)
   남기고, 임시 파일을 다 써 둔 다음 차례로 교체한다. 중간에 실패하면 이미 바꾼 파일을 되돌리고 새 파일을 지운다 — **전부 또는 전무**.
4. **되돌리기:** `/undo-bundle ID --yes` 는 묶음을 통째로 되돌린다. 적용 뒤 바뀐 파일이 하나라도 있으면 아무것도 하지 않는다
   (사용자의 이후 수정을 덮지 않는다). 되돌리기 직전 상태도 백업한다.
5. **저장·화면:** 수정안은 기존 `edit_proposals` 표에 `kind: bundle` 로 저장해 재시작 뒤에도 승인·폐기된다(스키마 변경 없음).
   `/edit-files 경로1, 경로2 :: 지시` 응답도 `편집 제안 ID:` 줄로 시작해 기존 적용·폐기 버튼과 `/apply-edit`·`/discard-edit` 를 그대로 쓴다.
   한 묶음은 파일 8개까지.

**아직 아닌 것:** `/dev-*` 개발 작업은 여전히 파일 하나·테스트 하나다 — 묶음을 개발 루프에 넣는 것이 다음(격리된 작업 공간과 함께).
새 폴더는 만들지 않는다. 파일 교체들은 OS 수준의 한 트랜잭션이 아니다(되돌리기로 보상한다).

**검증:** `tests/test_workspace_bundle.py` 14건(파싱·변형 허용, 부분 수정·새 파일·거부 조건 8종, 전부 적용과 통째 되돌리기, 중간 실패 시
전부 복구 — 되돌리기를 뺀 변이에서 실패 확인, 적용 뒤 바뀐 파일이 있으면 되돌리기 거부, 재시작 뒤 복구, CRLF 유지, 채팅 명령 흐름).
Windows 전체 release gate 752 tests(3 skip) PASS(`logs/claude-bundle-verify-20260926.txt`). 실제 모델, 임시 프로젝트의 "버그 수정 + 새 함수 +
새 테스트 파일" 과제(`logs/probe/bundle_live.py`): 관대한 해석 전 gpt-oss:120b 2/3·3/4 → 뒤 **4/4**, qwen3.8:27b **3/3** — 매번 적용 뒤
그 프로젝트의 테스트 통과. 실제 크롬 `logs/probe/bundle_ui_smoke.py` 4/4(버튼·확인 창·세 파일 적용·CSP 오류 없음). 앱 = 디스크 `a5cc60fc22bf291f`.

**ADR-065 결정 6 (2026-09-26 22:25, claude — 사용자: "계속 이어서해."):** `/dev-*` 개발 작업도 여러 파일을 받는다 —
`/dev-start a.py, b.py :: tests/test_x.py :: 목표`(최대 8개). 파일이 둘 이상이면 수정안은 묶음(`propose_bundle_local`)이고, 하나면
지금까지처럼 파일 전체 교체다(옛 동작·옛 기록 그대로 — `DeveloperRun.extra_sources` 기본값 빈 목록). 묶음은 **작업에 지정한 파일만 고치고
`tests/` 는 고치지도 만들지도 못한다** — 어기면 적용 전에 폐기한다(테스트가 합격 기준). 테스트 파일은 수정 대상으로 받지 않는다.
변경 감지는 지정한 모든 파일의 해시로 한다(파일 하나면 옛 해시와 같다). 묶음을 `/apply-edit` 로 적용하면 작업은 재검증 대기로 간다.
검증: `tests/test_developer.py` +4(두 파일 묶음으로 통과까지·테스트를 건드린 묶음 폐기 — 범위 검사를 뺀 변이에서 실패 확인·시작 거부 3종·옛 기록),
전체 756 PASS(`logs/claude-devbundle-verify-20260926.txt`). 실제 모델로 끝까지(`logs/probe/dev_bundle_live.py`, 두 파일에 걸친 단위 버그):
gpt-oss:120b 2/2, qwen3.8:27b 2/2 — 모두 수정안 한 번으로 통과. 크롬 Coding Mode 7/7·묶음 4/4. 앱 = 디스크 `22d931a6663a3156`.

## ADR-066 — 자동 재수정 루프는 격리된 복사본에서만 돌고, 실제 파일은 최종 묶음을 사용자가 승인할 때만 바뀐다

**계기 (2026-09-26 22:38):** 코덱스 4단계 문서의 다음 순서 4번 "격리된 작업 공간에서 계획 → 수정안 → 테스트 → 실패 분석 → 재수정의
제한된 자동 루프, 실제 파일 반영은 최종 diff 와 테스트 결과를 보고 사용자가 승인". 사용자: "계속해 지금 단계에서 할수잇는대로 다해".

**결정:** `/dev-auto`(개발 모드 화면의 "자동 수정(최대 3회)" 버튼).
1. 프로젝트를 임시 폴더로 복사한다 — `runtime`·`logs`·`data`·`backups`·가상환경·`node_modules`·숨김 폴더·심볼릭 링크·5MB 넘는 파일은 빼고,
   전체 300MB 를 넘으면 거부(ALEX 의 `runtime/` 은 100GB 가 넘는다).
2. 복사본에서 선택한 테스트 → 실패면 로컬 모델의 묶음 수정안(ADR-065 형식, 작업 파일만·`tests/` 금지) → 복사본에 적용 → 재시험을 **최대 3번**.
   거부된 수정안(형식·범위 위반)은 그 이유를 다음 회차 지시에 붙인다. 테스트 실행 문제(시간 초과·중단 등)면 멈춘다. 중단 버튼이 먹는다.
3. 복사본에서 통과하면 작업 파일의 최종 내용(+새로 만든 파일)을 **실제 프로젝트의 묶음 수정안 하나**로 만든다 — 시작 때 읽은 원본과
   지금 실제 파일이 다르면(그 사이 사용자가 고침) 만들지 않는다. 적용은 버튼/`/apply-edit`, 되돌리기는 `/undo-bundle`. 통과하지 못하면
   수정안을 내지 않는다. 실제 파일은 루프 동안 한 번도 쓰지 않는다.

**한계:** 테스트는 여전히 사용자 권한으로 실행된다(복사본이라 파일은 분리되지만 OS 수준 격리는 아니다 — SANDBOX_DESIGN.md). 복사본에서 빠진 폴더를
테스트가 읽으면 복사본에서만 실패할 수 있다.

**검증:** `tests/test_developer.py::AutoLoopTests` 5건(틀림 → 맞음 두 회차로 통과하고 실제 파일은 승인 때만 바뀜, 끝내 못 고치면 수정안 없음,
범위 위반 회차 거부와 되먹임, 그 사이 실제 파일이 바뀌면 옮기지 않음, 복사 제외 폴더) — 루프가 실제 편집기를 쓰도록 바꾼 변이에서 3건 실패 확인.
전체 761 tests(3 skip) PASS(`logs/claude-devauto-verify-20260926.txt`). 실제 모델(`logs/probe/dev_bundle_live.py --auto`): 두 파일 단위 버그 —
gpt-oss:120b 2/2·qwen3.8:27b 2/2, 예외 경우 7개짜리 `slugify`(`--task slug`) — 2/2·2/2, 모두 1회차 실패 → 복사본 수정 → 2회차 통과 → 승인 뒤 실제
프로젝트에서도 통과. 크롬 `bundle_ui_smoke.py` 5/5(자동 수정 버튼 포함)·`coding_mode_ui_smoke.py` 7/7. 앱 = 디스크 `9d5e392a5d2624c1`.

**ADR-065 결정 7·8 (2026-09-26 22:55, claude):**
7. **새 폴더:** 묶음의 새 파일은 없는 폴더도 만든다 — 프로젝트 안, 숨김 아님, 한 번에 새 폴더 3단계까지, 중간에 파일이 있으면 거부.
   적용 중 실패하거나 `/undo-bundle` 로 되돌리면 새로 만든 폴더도 지운다(비어 있지 않으면 — 사용자가 뭔가 넣었으면 — 남긴다). 되돌리기 목록에 `created_dirs`.
   **새 파일은 비어 있어도 된다**(`__init__.py` — 실측에서 두 모델 모두 새 패키지의 빈 `__init__.py` 를 만들다 거부당했다). 기존 파일을 비우는 수정은 여전히 막는다.
8. **합격 기준 테스트 여러 개:** `/dev-start 파일들 :: tests/test_a.py, tests/test_b.py :: 목표`(최대 5개). 테스트 파일마다 따로 돌리고 하나라도 실패하면
   실패다(실행 문제면 거기서 멈춤). 모델은 테스트별 결과를 머리글(`=== tests/…: 종료 코드 N ===`)과 함께 받는다. `/dev-next`·`/dev-auto` 모두 같다.
검증: `tests/test_workspace_bundle.py` 17건(새 폴더 생성·되돌리기·중간 실패 복구·사용자 파일이 있는 폴더는 남김·빈 새 파일 허용),
`tests/test_developer.py` 30건(여러 테스트 모두 통과해야 통과·하나만 실패해도 실패·중복/6개 거부). 전체 766 PASS(`logs/claude-devtests-verify-20260926.txt`).
실제 모델 "새 패키지 mathx/ + 수정 + 새 테스트"(`logs/probe/bundle_live.py --task newdir`): 빈 파일 허용 전 0/4 → 뒤 gpt-oss:120b 2/2·qwen3.8:27b 2/2.
크롬 7/7·5/5. 앱 = 디스크 `92c9ec5bcb1f591c`.

**ADR-066 보충 (2026-09-26 23:40, claude) — 복사에서 건너뛰는 파일:** 실제 ALEX 폴더로 `/dev-auto` 를 돌리다 루트의 `NUL` 파일(셸 리다이렉트
흔적)에서 복사가 `SameFileError` 로 멈췄다. Windows 장치 이름(`CON`·`PRN`·`AUX`·`NUL`·`COM1-9`·`LPT1-9`, 확장자와 무관)인 파일과 복사 중
`OSError` 가 나는 파일은 건너뛰고, 루프 기록에 "복사하지 못해 건너뜀 N개: …" 로 남긴다. 테스트가 그 파일을 읽는다면 복사본에서만 실패할 수 있다 —
위 한계 문단과 같은 종류. 검증: `tests/test_developer.py` 복사 테스트 1건 추가, 전체 767 PASS(`logs/claude-nulfix-verify-20260926.txt`).
크롬 7/7·5/5. 앱 = 디스크 `853098208b75e83a`.

## ADR-067 — 문맥 예산보다 큰 파일은 발췌로 보이고 '찾을 부분' 묶음으로만 고친다

**계기 (2026-09-26 23:40):** core·app·storage·web·providers·developer·workspace 가 데스크톱 문맥 예산(약 24,000자)을 넘어 `/dev-*` 대상이 못 됐다.
실제 평가에서는 모델에게 가는 실패 출력이 `unittest -v` 통과 줄로 채워져 1,600자 지시 한도에서 트레이스백이 잘릴 수 있음도 보였다.

**결정:** 1. 묶음 수정안(ADR-065)에서 파일이 예산을 넘으면 거부 대신 발췌(`jarvis/excerpt.py`): 트레이스백 줄이 든 함수, 지시·실패 출력에 이름이 나온
함수·클래스·상수(두 곳 이하), import, 단서 낱말(식별자 조각·한국어 낱말, 드문 낱말일수록 무겁게)을 많이 공유하는 함수 본문, 남는 자리에 나머지 정의의
첫 줄. 빠진 곳은 `⋯ 줄 a–b 생략 ⋯` 로 표시하고 규칙 한 줄을 붙인다. 작은 파일은 통째로. 2. 안전: 찾을 부분은 원본 전체에서 정확히 한 곳과 맞아야
적용되므로(`create_bundle`) 발췌 밖을 지어내면 적용 전에 거부된다 — 새 위험 경로가 없다. 3. 단일 파일 개발 작업도 파일이 전체 다시 쓰기 예산을
넘으면 묶음(발췌) 수정안(`core.fits_whole_file_edit`). 4. 모델에게는 통과·건너뜀 줄을 뺀 실패 출력(`developer._failure_digest`), 묶음 지시 한도
`min(3000, budget // 5)`(노트북 8,000 예산은 1,600 그대로).

**한계:** 단서가 테스트 파일 안에서만 끝나면(고칠 함수 이름이 출력에 없음) 낱말 겹침에 기댄다. 모델은 테스트 파일 자체를 보지 못한다.
`/dev-auto` 는 실패한 회차의 변경도 최종 수정안에 쌓인다(launcher_fa 실측) — 다음 후보: 통과 뒤 불필요한 변경 덜어내기.

**검증:** `tests/test_excerpt.py` 11건(실패 함수 통째·나머지 생략 표시, 트레이스백 줄의 함수, 여러 줄 상수, 개요, 드문 낱말 순위, 비파이썬 파일,
작은 파일 통째·자리 부족 거부, 예산 두 배 넘는 파일의 묶음 적용, 숨은 부분 지어내기 거부, 큰 단일 파일 → 묶음·실패 부분만). 전체 778 PASS
(`logs/claude-excerpt-verify-20260927.txt`). 앱 = 디스크 `71088ffb999ce9d1`. 실제 모델 큰 파일 평가(`coding_agent_eval.py --set large`)는 아직.
실제 ALEX 코드 평가(`logs/probe/coding_agent_eval.py`, qwen3.8:27b, 발췌 모드 전): 오늘 고친 버그 4개를 복사본에 되살려 `/dev-auto` 로 고치게 함 — **2/4 회귀 없이 해결**. router_shortcut 2회차 통과(원래 줄 복원, 4분), launcher_fa 3회차 통과(`all→any` 정답이지만 앞 회차의 불필요한 `report` 감싸기도 최종 수정안에 남음, 13분), lessons_stem 3회 수정 후에도 실패(36분), webread_ipv4_mapped 는 평가 스크립트의 테스트는 실패했는데 `/dev-auto` 복사본에서는 1회차에 통과 → 수정안 없음(원인 미조사: 복사본 차이 또는 평가 스크립트 문제).

**ADR-066 보충 2 (2026-09-29 22:00, claude) — 통과 뒤 덜어내기:** 복사본 루프는 실패한 회차의 변경도 쌓아 둔다(실측 launcher_fa: `all→any` 정답에
불필요한 `report` 감싸기 함수가 함께 남음). 통과하면 변경 덩어리(difflib 기준)를 하나씩 되돌려 합격 기준 테스트를 다시 돌리고, 여전히 통과하는
덩어리는 뺀다. 덩어리마다 테스트 1번, 후보가 8곳을 넘으면 건너뛴다(기록에 남김). 주석·빈 줄만 바꾼 덩어리는 설명이라 시험하지 않고, 새 파일도
건드리지 않는다. 다 덜어낸 뒤 한 번 더 돌려 실패하면 통과했던 원래 수정안을 쓴다. 테스트가 덮지 않는 '필요한' 변경도 빠질 수 있다 — 테스트가
합격 기준이라는 ADR-066 원칙 그대로다. 검증: `tests/test_developer.py` 1건(군더더기 1곳 제거·주석 유지·승인 뒤 통과), 전체 779 PASS.

**ADR-067 실측 (2026-09-29 23:05, claude):** 큰 파일 실측(`coding_agent_eval.py --set large`, qwen3.8:27b, 발췌 모드·실패 부분만·덜어내기 적용, 22:01~23:01): **3/4 회귀 없이 해결** — core.py(112KB) 2회차·원본과 같음·2분 40초, workspace.py 2회차·원본과 같음·2분 30초, web.py 4회차·원본과 다른 한 줄(현재 대화를 지울 때만 `last_exchange` 를 비움 — 테스트는 통과, 원본보다 좁다)·16분, developer.py(`_run_tests` 의 '하나라도 실패하면 실패' 두 줄 삭제) 3번 고쳐도 실패·37분. 통과한 과제는 모두 전체 unittest 종료 코드 0. 작은 파일 평가(2/4)와 합쳐 실제 ALEX 코드 8과제 중 5개. 발췌 모드 전에는 이 네 파일 모두 `/dev-*` 대상이 아니었다.
실패한 developer 과제는 트레이스백이 테스트 파일에서 끝나고(IndexError) 고칠 함수 이름이 출력에 없는 경우 — 한계 문단 그대로다.

**ADR-066 보충 3 (2026-09-29 23:35, claude) — 실패한 테스트 코드를 읽기 전용으로:** 수정 지시에 실패 출력이 가리키는 합격 기준 테스트의 코드를 붙인다
(작으면 통째, 크면 `excerpt` 로 실패한 테스트 함수·import 중심 1,500자). 테스트를 고치는 수정안은 여전히 폐기된다(`_check_bundle_scope`) — 보는 것만
허용한다. 실측: 트레이스백이 테스트 파일 안에서 끝나던 lessons_stem 이 3회 실패·36분 → 2회차 통과·원본과 같음·6분. developer_any_test_fails 는 여전히
실패 — 회차별 수정안을 `logs/coding-agent-eval-rerun2-developer.json` 에 남겨 원인을 본다. 데스크톱 묶음 지시 한도 3,000자 안에 실패 출력(통과 줄 제외)과
테스트 코드가 함께 들어간다. 노트북(1,600자)에서는 뒤쪽이 잘릴 수 있다.

**ADR-065 결정 9 (2026-09-30 07:00, claude) — 편집안 받기의 운영 실패 두 가지:** ① 편집안은 늘 스트리밍으로 받는다(`on_chunk` 를 주어
Ollama 스트리밍 경로). 한 번에 받으면 소켓 시간 제한(데스크톱 360초)이 전체 생성 시간에 걸려, 27b 가 생각을 길게 하면 끊긴다. 화면에서는 중단 버튼용
`cancel_event` 때문에 이미 스트리밍이었고 화면 없는 실행(평가·CLI)에서만 났다. ② 변경 블록 해석이 실패하면 형식 안내(`BUNDLE_FORMAT_RETRY`)를 붙여
그 자리에서 한 번만 다시 묻는다 — 모델 호출은 한 번 늘지만 `/dev-auto` 의 3회차 한도를 형식 실수로 쓰지 않는다. 적용 안전성은 그대로다(찾을 부분은
여전히 원본 전체에서 한 곳과 맞아야 한다). 검증: 3건(스트리밍 경로·한 번 다시 묻기·두 번 틀리면 거부), 전체 784 PASS.

## ADR-068 — 빌드·검사 명령은 사용자가 파일로 정한 이름 붙은 argv 만 실행한다 (/dev-check)

**계기 (2026-09-30 07:15, claude):** Coding Mode 4단계 문서 다음 순서 3번 "승인된 프로젝트에서 빌드 등 필요한 명령을 정해진 인자와 실행 정책으로
제공한다 … 무제한 터미널과 모델 출력의 직접 실행은 도입하지 않는다". 사용자 목표 "스스로 성장하는 AI" — 테스트 말고도 스스로 확인할 수단.

**결정:** 1. 명령 목록은 프로젝트 루트 `alex-checks.json` 하나. 이름(영문·숫자·_·-, 40자) → 문자열 인자 목록(최대 50개, 빈 값·NUL 금지). 셸 문자열은
거부한다. 최대 20개. 2. 실행은 사용자의 채팅 명령 `/dev-check 이름` 으로만 — 모델 출력에서 이름이나 명령을 받지 않는다. 3. 모델이 목록을 바꿀 수 없게
편집기가 이 파일(루트)의 생성·수정을 거부한다(`PROTECTED_FILES`). 4. 실행 한도는 unittest 와 같다(120초·2MB 로그·중단·Windows `taskkill /T`), 첫 인자
`python`/`python3`/`py` 는 앱의 `sys.executable`.

**한계·다음:** `/dev-auto` 가 검사를 합격 기준에 넣는 것은 아직 아니다(테스트만). 사용자가 목록에 넣은 실행 파일 자체는 믿는다 — 목록을 쓰는 것이 승인이다.
**검증:** DevCheckTests 5건(파일 없음 안내, 목록·통과·실패·없는 이름, 셸 문자열·이상한 이름 거부, 모델 묶음이 만들기·고치기 거부, 채팅 명령 연결).
전체 792 PASS.

**ADR-065 결정 10 (2026-09-30 07:40, claude) — 생각 한도와 중복 호출:** 편집안 요청에서 모델이 출력 한도를 생각에 다 써 `ContextExhausted` 가
나면, 생각이 켜진 제공자에 한해 그 자리에서 `think=False` 로 한 번 더 묻고 원래대로 되돌린다(대화용 설정 보존). 코더·일반이 같은 호스트·모델이면
한 번만 묻는다 — 같은 지시를 같은 모델에 다시 보내 같은 실패를 기다리지 않는다(가짜 모델처럼 모델 이름이 없으면 객체 단위). 실측 근거: 27b
developer 과제 1·2회차가 6,144토큰을 생각에만 씀(회차당 약 17분 낭비). 검증 +2건, 전체 794 PASS.

## ADR-069 — 개발 작업의 결과는 종료 코드와 사용자 판단으로만 기록한다 (dev-outcomes.jsonl)

**계기 (2026-09-30 08:17, claude):** Codex 조사 `docs/ALEX_LEARNING_RESEARCH_20260930.md` 1순위 — 교훈 추출·검색·Skill·자동 개선·LoRA 모두 '무엇이 실제로
성공했는가' 라는 증거가 먼저다. DB 에는 training_runs 0, 경험 38(승인 3·코딩 2)뿐이었다. 사용자 목표 "스스로 성장하는 AI" 의 자.

**결정:** 1. DB 옆 JSONL 한 파일(스키마 변경 없음, 사람이 읽을 수 있음, 나중에 DB 로 옮길 수 있음). 앱 상태(10K 제한)의 작업 기록과 별개.
2. 사건 종류: `tests`(선택 테스트, 목표·파일·테스트·종료 코드·실패 요약), `proposal`, `auto`(회차별 증거·덜어내기·결과·시간·모델), `applied`, `discarded`, `suite`.
3. 상태는 계산한다 — 미검증 / 실패 / 선택 검사 통과 / 전체 검사 통과 / 사용자 채택(적용 뒤 전체 검사 통과). 모델의 자기평가는 넣지 않는다.
4. 기록 실패는 작업을 멈추지 않는다. 20MB 에서 한 번 돌린다.

**한계·다음:** '테스트는 통과했지만 의미상 틀림' 은 아직 표시할 수단이 없다(사용자 표시 명령 후보). 다음은 변이(mutation) 과제 생성기로 평가 과제를
늘려 교훈 on/off 를 학습·평가 과제로 나눠 잴 수 있게 하는 것(Codex 조사 2·5순위의 전제).
**검증:** 2건(자동 3회차 증거·상태 미검증 → 선택 검사 통과 → 사용자 채택·/dev-outcomes, 기록 불가여도 계속), 전체 796 PASS.

**ADR-069 보충 (2026-09-30 08:34, claude) — 변이 과제로 자를 늘린다:** 성장했는지 재려면 교훈을 만든 과제와 잰 과제가 달라야 한다(Codex 조사 2·5순위).
손으로 고른 과제 7개로는 부족해 SWE-smith 방식의 절차적 변형으로 51개를 만들고(학습 35·평가 16, (파일, 함수) 해시 분할), 기존 테스트가 잡는 것만 남겼다.
여러 줄 변형(if/else 뒤집기)·LM 재작성·버그 합치기·이슈 글 생성은 아직 하지 않는다 — 한 줄 변이는 쉬운 편이라 점수가 실제 버그보다 높게 나올 수 있다.
참고: SWE-smith(https://arxiv.org/abs/2504.21798)의 절차적 변형·'기존 통과 테스트를 하나 이상 깨는 것만'·2분 제한, mutmut 류 변이 테스트의 killed mutant(https://opensource.com/article/20/7/mutmut-python).

## ADR-070 — 코딩 에이전트는 결과 기록에서 절차 교훈을 뽑아 쓰되, 효과를 재기 전에는 켜지 않는다

**계기 (2026-09-30 08:49, claude):** Codex 조사 2순위(성공·실패에서 짧은 교훈을 뽑고 다시 검증). ADR-069 의 결과 기록과 변이 과제(학습 35·평가 16)가 생겼다.
실측으로 효과가 있던 것은 코드 사실이 아니라 절차였다(테스트 코드 참고로 lessons_stem 이 풀림 — ADR-066 보충 3).

**결정:** 1. ExpeL(https://arxiv.org/abs/2308.10144, 구현 https://github.com/LeapLabTHU/ExpeL) 의 두 비교(같은 과제의 실패↔성공, 여러 과제의 성공)와 목록 연산(ADD 2·EDIT/UPVOTE +1·DOWNVOTE -1·0 이면 삭제)을 따른다.
2. 교훈은 **절차**만(다른 버그에 옮겨 쓸 수 있는 방법) — 추출 지시에 명시. 최대 12개·200자. 3. 뽑기는 로컬 모델만, 사용자가 `/dev-learn` 으로 부른다.
4. 수정안 요청에 붙이기는 `coding_insights_enabled` — 기본 꺼짐. 켜는 것은 평가 과제(학습 과제로 뽑은 교훈 → 겹치지 않는 평가 과제)에서
켜고 끈 비교를 본 뒤 사용자가 정한다. 5. 사람이 보고 지울 수 있다(`/dev-insights remove`). 원본 결과 기록은 지우지 않는다.

**하지 않은 것:** 성공 사례를 예시(few-shot)로 검색해 붙이기(ExpeL 의 다른 절반) — 데스크톱 문맥 예산 24,000자에서 발췌 파일과 경쟁한다. 교훈의 자동 승격.
**검증:** 6건(연산·삭제·상한·중복, 예산 순서, 추출용 글, 켜짐일 때만 붙임, /dev-learn 한 번만·목록·지우기·사용법). 전체 802 PASS.

## ADR-071 — 앞선 답이 있는 대화에서는 핵심 사실을 마지막 질문 바로 앞에 한 번 더 준다(측정 뒤 켠다)

**계기 (2026-09-30 08:55, claude):** 사용자 "14b모델이 바로잡기가 안되는이유는?" — 새 질문에는 맞히는데(fresh 7/8) 자기 오답이 쌓인 대화에서는 맨 앞
안내보다 기록을 따른다(replay 0/3, 기록의 모델 이름까지 따라 말함). gpt-oss:120b 도 0/3, qwen3.8:27b 는 3/3.

**결정:** `fact_reminder_enabled`(기본 꺼짐). 켜면 일반·코딩 경로에서, 대화 기록에 assistant 답이 있고 마지막이 사용자 질문일 때만, 그 바로 앞에 짧은
system 한 줄(자주 틀린 사실만, 약 250자)을 넣는다. 새 대화에는 넣지 않는다(토큰 절약·효과 없음). 맨 앞 환경 안내는 바꾸지 않는다.
근거: Lost in the Middle(https://arxiv.org/abs/2307.03172) — 모델은 긴 문맥의 처음과 끝을 가장 잘 쓴다.
**검증 계획:** 같은 고정 대화(replay n=3)를 켠 채 14b·27b·120b 로 — 끈 기록(0/3·3/3·0/3)과 비교해 나아지고 27b 를 해치지 않으면 켜는 것을 사용자에게 제안.
단위 검증 3건(기본 꺼짐, 마지막 질문 바로 앞, 새 질문에는 없음), 전체 805 PASS.

**ADR-069 보충 2 (2026-09-30 14:50, claude) — Codex 리뷰 3차까지 반영:** 결과 기록은 사건마다 소스 지문(digest)을 남기고 지금 버전의 증거만
센다(R01), 테스트 중 소스가 바뀐 결과는 valid=False(R02), 지문 없는 옛 기록은 지금 지문을 알면 세지 않고 파일을 못 읽으면 미검증(2차 R2-02),
`/dev-check` 는 같은 프로젝트 작업에만 붙는다. 평가는 **고정 스냅샷**(처음 한 번 복사, 행마다 `source_hash`)과 **manifest**(과제·교훈·harness 지문,
요청값과 실제 provider 값 effective 분리 — 3차 R3-03)를 쓴다. 변이 생성은 UTF-8 열 위치를 문자 위치로 바꾸고(R03), 소속 붙은 함수 이름·hard ID 전체·
남은 시간 제한(R04·E2-02·R3-01)·합친 뒤 문법/가져오기 오류 거르기(E2-03). 문서: `docs/CODEX_REVIEW{,2,3}_20260930.md`. 전체 830 PASS.

**ADR-070 보충 (2026-09-30 14:50, claude) — 실측과 보완:** 교훈 켬/끔이 쉬운 세트 15/16·15/16, 어려운 세트 9/11·9/11(PERFORMANCE 31) — 성공 수 변화 없음,
기본 꺼짐 유지. 보완: 사후 확인(적용·선택 테스트·전체 검사)을 **그 수정안의 적용 지문**으로만 묶는다(R05, 2차 R2-01), 사후 확인이 바뀌면 한 번 더
검토(3차 R3-02 — 학습 키 = 사건 + 사후 확인 지문), 쓸 수 없는 연산(내용 없는 ADD·없는 번호·ADDITION)은 소모하지 않음(R2-04), 재실행 재투표 방지(R2-05),
형식이 틀린 교훈 파일은 저장 전 원본 보존(R06·R2-03). 교훈 #3·#6 은 다음 교훈 버전에서 고친다(Codex `docs/CODEX_TRAIN_INSIGHTS_ANALYSIS_20260930.md`).

**ADR-071 실측 (2026-09-30 14:50, claude):** 켠 채 replay — 14b 0/3·120b 0/3·27b 3/3, 끈 것과 같음. 재확인 문장 위치는 확인됨(마지막 질문 바로 앞, 줄이기 없음).
system 한 줄로는 쌓인 자기 오답을 못 이긴다 — **꺼 둔다.** 문맥 줄이기에서도 제자리를 지키게 고친 것(Codex 리뷰)은 남긴다.

## ADR-072 — 코딩 수정안 요청에 '이 파일이 쓰는 객체의 API'(시그니처만)를 붙일 수 있게 한다(기본 꺼짐)

**계기 (2026-09-30 14:50, claude):** 쉬운 평가의 유일한 실패(core `ask_file_local`)에서 27b 가 지워진 `self.file_read_tool.run(path=…)` 대신 없는 `read()`·`read_file()` 을
돌려 썼다(교훈 켬/끔 모두, Codex 비교 문서). 사용자 "진행해줘" — 다음 계단으로 실제 시그니처를 보여 주기.
**결정:** `jarvis/apimap.py` — 고칠 파일에서 `self.<속성> = 클래스(...)` 를 찾아 그 클래스가 정의된 프로젝트 파일의 공개 메서드 **시그니처 줄만**(본문 없음) 보인다.
`**kwargs` 는 본문이 읽는 키(`# kwargs: path`)와 짧은 설명 첫 줄을 붙인다. 실패 출력·지시와 겹치는 낱말이 많은 속성부터, 약 1,200자. 참고: Aider repo map
(https://aider.chat/docs/repomap.html — 저장소 전체 시그니처를 그래프 순위로 약 1k 토큰; ALEX 는 지금 파일의 객체만). `coding_api_map_enabled`(기본 꺼짐),
평가 `--api-map`. **검증:** `tests/test_apimap.py` 4건, 전체 830 PASS. **진단(15:51, 이미 본 실패 5개 — 일반 성능 근거 아님):** 2/5. 겨냥한 `ask_file_local` 이 2회 만에 엄격 통과, remote 통과는 API 블록이 비어 있어
재시도의 우연 → 분산이 크다. 기본 꺼짐 유지, 다음은 보류 과제로 켬/끔 반복(PERFORMANCE 31 ⑦).

## ADR-073 — 자연어로 그림 그리기: 이 PC 의 ComfyUI + Z-Image Turbo, 규칙 라우팅, 그래픽카드 번갈아 쓰기

(초기 공개본에서는 제외했으나 2026-10-11부터 연결 코드를 복원했습니다. 현재 사용·검증 내용은 docs/LTX_VIDEO.md를 참고하세요.)

## ADR-075 — 영상 만들기: Wan 2.2(글로만 만들면 14B, 그림에서 시작하면 5B), FHD 는 SeedVR2, 영상 동안 대화 모델 내림

(초기 공개본에서는 제외했으나 2026-10-11부터 연결 코드를 복원했습니다. 현재 사용·검증 내용은 docs/LTX_VIDEO.md를 참고하세요.)

## ADR-076 — 코딩 묶음 수정안만 긴 문맥으로(설정만, 기본 꺼짐 — 실측 뒤 켠다)

**배경:** 사용자 "코딩에이전트 능력을 … sonnet 5.0 수준으로"(2026-10-03) → 계획 [docs/CODING_AGENT_SONNET_PLAN_20261003.md] 중 사용자가 고른 1번(모델 교체 말고, 문맥 늘리기).
"평가는 하지 말고 코드만 수정해줘"(팬). 27b 가 못 푼 과제는 큰 파일(core.py 11만 7천 자)이고, 16K 문맥이라 발췌만 보고 발췌 밖 API 를 지어냈다(PERFORMANCE 31 ⑦).
27b 는 65층 중 16층만 전체 어텐션이고 KV 헤드가 4개라 64K 의 KV 캐시가 약 4GB(계산, 16K 의 4배)다 — 문맥을 늘릴 여지가 있다.

**결정:** ①설정 `coding_num_ctx`(0 = 대화와 같음, 기본)·`coding_num_gpu_layers`(0 = Ollama 자동 배치)·`coding_context_char_budget`(0 = 대화 예산 × 문맥 비율).
②`JarvisCore.coding_runtime` 이 **묶음 수정안(propose_bundle_local, 찾을 부분 → 바꿀 내용)을 만드는 동안만** 코더·일반 공급자의 num_ctx·num_gpu 와 글자 예산을 바꾸고,
끝나면(실패·취소 포함) 되돌린다. ③파일 전체 다시 쓰기(propose_file_edit_local)와 그 판정(fits_whole_file_edit)은 대화 예산 그대로 — 키우면 core.py 도 전체 다시 쓰기로
넘어가 11만 자를 출력해야 하고 출력 한도(6,144토큰)에서 잘린다. 그래서 큰 파일은 묶음으로 가고, 거기서 통째로 보인다. ④`/status` 에 `coding_runtime` 줄.
⑤평가 전용 `coding_agent_eval.py --coding-num-ctx/--coding-num-gpu/--coding-context-chars`(manifest 에 기록).

**켜기 전에 잴 것(GPU, 사용자 허락 뒤):** 64K 에서 27b 의 GPU 층 수·입력 처리/생성 속도(54층은 16K 기준, 32K 이상에서 넘칠 수 있다 — PERFORMANCE 29),
core.py 의 실제 토큰 수(→ 글자 예산: 통째로 보이려면 약 12만 자 이상), 27b 가 못 푼 과제 5개 + 어려운 11 을 16K 결과와 짝 비교. 문맥이 바뀌면 Ollama 가 모델을
다시 올린다(대화 ↔ 코딩 전환마다 한 번). `tests/test_coding_context.py` 7건. 전체 904 PASS, 앱 `9cea2756e8f21ad9`.

## ADR-077 — /dev-auto 독립 풀이: 한 풀이가 실패하면 처음 상태에서 새로 푼다(설정만, 기본 1 = 예전과 같음)

**배경:** 계획([docs/CODING_AGENT_SONNET_PLAN_20261003.md]) 2번, 사용자 "이어서 작업해"(코드만 — 평가·GPU 없음 유지). 근거: 같은 계산량이면 한 답을 고쳐 나가는 것보다
새로 여러 번 푸는 편이 대개 낫고 고치기는 자기 오류를 알아보는 능력에 막힌다(Olausson et al., ICLR 2024, arXiv 2306.09896). 코딩은 테스트라는 검증기가 있어 표본을
늘린 만큼 오른다(Large Language Monkeys, arXiv 2407.21787: SWE-bench Lite 15.9% → 56%, 250개). 27b·Ollama 기본 온도가 1.0 이라 풀이마다 답이 다르다.

**결정:** ①설정 `coding_auto_attempts`(1~5, 기본 1). ②`/dev-auto` 가 한 풀이(최대 3회차) 안에 통과하지 못하면 복사본의 수정 대상 파일을 원본으로 되돌리고 앞 풀이가
만든 새 파일을 지운 뒤 새로 푼다 — 앞 풀이의 실패·피드백은 주지 않는다(독립). ③첫 통과에서 멈춘다(합격 기준 테스트가 검증기). 이후 덜어내기·최종 수정안·사용자 승인은
그대로. 실행 문제(종료 코드 124~130)면 더 풀지 않는다. ④결과 기록에 `attempts`, 회차마다 `attempt`. `/status` 에 `dev_auto` 줄(2 이상일 때), `/help` 안내.
⑤평가 전용 `coding_agent_eval.py --auto-attempts N`.

**켜기 전에 잴 것:** 실패 과제에서 시간이 최대 N배다. 27b 가 못 푼 과제 5개 + 어려운 11 을 `--auto-attempts 2~3` 으로(가능하면 ADR-076 긴 문맥과 따로·같이) 재서
성공 수와 시간을 본다. 회차(3)와 풀이 수의 나눔(예: 2회차 × 2풀이)도 그때 정한다. 테스트 `AutoLoopTests` 3건 추가. 전체 907 PASS, 앱 `1d7a83519e2415fe`.

## ADR-078 — /dev-auto 회귀 검사: 합격 테스트 통과 뒤 전체 테스트로 '다른 테스트를 깨뜨렸는지' 본다(설정만, 기본 꺼짐)

**배경:** 계획 7절 실패 유형 D. 27b 가 합격 테스트는 통과시키고 다른 테스트를 깨뜨린 채 '통과'했다 — 9/30 hard app 345+421(test_local_beta), 10/4 역사 과제
summary-perf_web(같은 테스트). 루프가 합격 테스트만 돌리기 때문이다. 평가의 엄격 성공(전체 테스트 새 실패 없음)과 루프의 합격 기준이 어긋나 있었다.

**결정:** ①설정 `coding_regression_check`(기본 꺼짐). 켜면 합격 테스트가 통과한 복사본에서 전체 테스트를 돌려, 실패가 있으면 복사본을 수정 전으로 잠시 되돌려 전체를 한 번 더
돌리고(한 작업에 한 번) 원래부터 실패하던 것은 뺀다. 새 실패가 있으면 그 출력과 "합격 테스트를 지키면서 이 실패도 없애라" 를 주고 다음 회차로(회차 수 안에서).
②덜어내기는 합격 테스트만 보므로, 켜져 있으면 덜어낸 뒤 다시 보고 새 실패가 나오면 덜어내기를 취소한다. ③전체 검사가 돌지 못하면(시간 초과 등) 판단하지 않고 그 사실을
기록에 남긴다. ④곁가지 고침: 전체 테스트 실행 상한 120초 → 900초(`SUITE_LIMIT_SECONDS`) — ALEX 전체 검사가 131~179초라 /dev-verify 도 시간 초과였다.
⑤`/status` 줄, `/help`, 평가 옵션 `--regression-check`.

**대가·측정:** 통과할 때마다 전체 검사 약 2~3분(CPU). 켜기 전에 역사 과제·어려운 11 에서 성공 수와 시간을 잴 것. `tests/test_developer.py` 4건, `test_coding_context` 2건.
전체 920 PASS(`logs/claude-regression-verify-20261004.txt`), 앱 `bf731267e8d69399`.

## ADR-079 — 코딩 에이전트 실패 원인 재점검(평가 177회차)과 고침: 너그러운 찾기, 출력 한도, 넓힌 API 요약, 거부 원문 기록

**배경:** 사용자 "코딩에이전트로서 개선 가능한 부분 전체적으로 다시 파악해서 수정 … 오답 원인 … 속도"(2026-10-04). 9/30~10/4 평가 18개 파일(과제 120, 엄격 성공 92)의
모델 수정안 177회차를 다시 셌다: 적용 147, **거부 30(17% — 회차를 통째로 날림)**: 찾을 부분 없음 9·생각 소진 7·블록 없음 6·출력 잘림 3·찾을 부분 여러 곳 2·문법 2·시간 초과 1.
'찾을 부분 없음'의 기록된 첫 줄은 7건 중 6건이 파일에 그대로 있었다 — 뒤 줄의 공백·들여쓰기·빈 줄이 어긋난 것. 적용됐는데 틀린 것은 대부분 '다른 파일의 API 를 모름'
(역사 과제 실패 3/3, 48K 실패 1)이었고, 그때 API 요약(ADR-072)은 app.py·web.py 에서 **빈 블록**이었다(그 모양을 못 담음).

**결정:** ①`workspace._loose_replace`: 글자 그대로 없을 때만, 줄마다 앞뒤 공백을 빼고 같고 들여쓰기 차이가 모든 줄에서 같으면(다음은 빈 줄 차이까지 무시) **정확히 한 곳**일 때
받고 바꿀 내용을 그 차이만큼 옮긴다. 두 곳 이상이면 거부하고 "앞뒤 줄을 더 넣어라" 힌트(Aider 의 너그러운 찾기와 같은 생각). ②긴 문맥 코딩(ADR-076)에서는 남은 문맥만큼
출력 한도를 늘린다(프롬프트 3자 = 1토큰으로 넉넉히 어림, 최대 `CODING_MAX_OUTPUT` 16,384, 끝나면 원래 값). ③API 요약 넓힘(ADR-072 보충): 다른 파일에서 가져와 쓰는
함수·클래스, 타입 표시된 매개변수를 거친 속성(`core: JarvisCore` → `core.storage` → Storage), 생성자 매개변수로 받은 속성(`self.storage = storage`). 메서드 없는 클래스는 빼고,
예산이 모자라는 큰 클래스는 메서드 이름만. 예산은 글자 예산의 1/20(최소 1,200·최대 8,000 — 48K 에서 6,750). ④거부된 수정안의 모델 원문 앞 2,000자를 오류에 붙여
/dev-auto 회차 기록(`rejected_output`)과 평가 결과(`round_diffs` 의 REJECTED)에 남긴다 — 다음 원인 분석용.
API 요약·긴 문맥은 여전히 기본 꺼짐(설정). ①④는 늘 켜진다(동작은 '거부되던 것을 받는 것'뿐, 한 곳 조건).

**측정:** 아직 GPU 평가 전(대기열 4 는 고치기 전 얼린 코드). 새 얼린 복사본으로 역사 과제를 다시 잴 것. 테스트: workspace_bundle 4·apimap 3(+1 고침)·coding_context 3.
**ADR-079 보충 (2026-10-04 09:0x, claude) — 긴 문맥에서 지시·테스트 코드 상한:** 48K 에서도 묶음 수정안의 지시(목표 + 실패 출력 + 실패한 테스트 코드)는 3,000자로,
실패한 테스트 코드는 1,500자 발췌로 잘렸다 — 파일은 통째로 보이는데 '테스트가 무엇을 기대하는지'는 잘린 채 갔다. remote 과제(48K·48K + API 요약 둘 다 실패)는 단서인
테스트 준비 함수 `alex_health()`(app='jarvis' 를 준다)가 실패한 테스트에서 100줄 떨어져 있었다. 긴 문맥이 켜져 있으면 지시 상한 = 글자 예산의 1/10(최대 16,000, 대화 예산
24,000 에서는 3,000 그대로), 테스트 코드 = 글자 예산의 1/15(48K 에서 9,000). 테스트 2건(test_coding_context).

**ADR-078 보충 (2026-10-04 09:4x, claude — Codex 가이드 `docs/CODING_AGENT_PRIORITY_GUIDE_20261004.md` 우선순위 1) — 회귀 검사 판정 네 가지:**
처음 판의 `_regressions` 는 전체 검사가 124·125·126·130 으로 끝나도 '새 실패 없음'과 같은 None 을 돌려줘 미검증·취소 수정안을 통과로 올릴 수 있었고, 기준이 실패 중일 때
후보의 실패 이름을 못 읽으면(출력 끝 3,500자만 남는다) 빈 집합 차이로 통과가 될 수 있었다(Codex 재현 7건 — 지적이 맞다). 결정: `RegressionResult(status=verified |
new_failures | unverified | cancelled)`. 전체 출력은 마지막 완료 요약(Ran N / OK / FAILED (failures, errors))으로 읽고, 실패 비교는 FAIL:/ERROR: 머리글 수가 요약과
맞을 때만 — 아니면 unverified(정상 기준 + 실패한 후보면 new_failures 일반 설명). unverified 는 더 고치거나 새로 풀지 않고 `needs_test` 로 끝낸다(통과로 올리지 않음),
cancelled 는 즉시 중단. 끝나지 못한 기준은 기억하지 않는다. 덜어내기 재검사는 문구 대신 실제 내용 비교로, verified 가 아니면 검증된 직전 후보 그대로 되돌린다. 덜어내기
뒤·최종 수정안 직전·직후에도 취소를 본다(직후면 만든 수정안을 버림). 결과 기록에 `verification_status/scope/reason`(scope = full_suite | selected_tests), `result` 의 뜻은
그대로. 같은 ID 의 실패 원인 변화까지는 보지 않는다(이름 비교의 한계, 문구에 그렇게 적음). 테스트 RegressionVerdictTests 7건(+기존 3건 문구), 전체 961 PASS
(`logs/claude-verdict-verify-20261004.txt`), 앱 `1bd2f0754d9f8773`, diff `logs/claude-regression-verdict-20261004.diff`. 성공률 개선 주장은 아니다(판정의 정직성).

**ADR-078 보충 2 (2026-10-04 11:20, Codex 구현 + claude 마무리 — Codex 재검토 `docs/CODEX_PRIORITY1_REVIEW_20261004.md` R1~R3):**
R1 subTest 인자·[설명]까지 실패 ID 에 넣는다(줄 전체, 모르는 형식은 미검증). R2 `UNEXPECTED SUCCESS` 도 실패로 비교하고 요약 항목·종료 코드·유형별 머리글 수가 모두 맞아야
읽는다(모르는/중복 항목, 0개 실행, 요약 뒤 딴 글 → 미검증). R3 모든 취소를 `_cancel_auto` 하나로 — 수정 없는 완료·수정안 생성 전후·합격 테스트 130 포함, 기록은
`verification_status=cancelled`(앞 회차의 통과 기록은 회차에 남음). 여기까지 Codex 가 별도 작업 폴더에서 구현·검사(개발자 80·기존 11·추가 6 PASS)하고 반영 전에 멈췄다.
**claude 가 실제 ALEX 전체 검사로 확인하다 찾은 것:** 테스트의 print 는 버퍼에 있다가 프로세스 끝에 요약 **뒤**에 붙고(약 3천 자), 앱은 끝 3,500자만 남겨 앞쪽 실패
머리글이 잘렸다 — 엄격해진 판독기로는 **정상 통과도 '읽을 수 없음'**, 예전 판독기도 실패가 있으면 이름이 모자랐다(회귀 검사를 켜면 늘 미검증으로 끝났을 것; 가짜 실행기
테스트로는 안 보임). 고침: 기본 전체 검사 실행기만 전체 로그(종료 코드 0·1)에서 `_suite_digest` 로 실패 목록(머리글 전부·트레이스백은 끝부분)과 Ran·요약을 다시 짠다.
트레이스백 안의 머리글 모양 줄은 들여 써서 세지 않고, 구분선까지 갖춘 중첩 보고는 미검증. 같은 ID 가 여러 번이면 '#2' 를 붙여 횟수 증가도 새 실패로.
실측: 실제 ALEX 전체 검사(973) 정상 → ok, 일부러 넣은 subTest·오류·unexpected success → 셋 다 정확히(고치기 전 둘 다 미검증). 평가 도구의 엄격 성공은 출력 전체를 받고
종료 코드 0 을 요구해 이 문제와 무관 — 지금까지 점수는 그대로. 테스트 `SuiteDigestTests` 6건(실제 하위 프로세스 1건) + Codex `SuiteReportTests`·`RegressionBoundaryTests`.
전체 **992 tests OK, RELEASE PASS**(`logs/claude-r123-verify-release-20261004.txt`), 앱 `e6413740ce60f17c`, diff `logs/claude-r123-final-20261004.diff`.

## ADR-080 — 수정 전 읽기 전용 진단 근거: 실패 문구의 조건·실패한 테스트의 준비 코드(설정만, 기본 꺼짐)

**배경:** Codex 가이드(`docs/CODING_AGENT_PRIORITY_GUIDE_20261004.md`) 우선순위 2, 사용자 "코덱스 아스트라가 남긴 거 … 진행해". remote 과제는 48K·48K + API 요약 둘 다
버그 줄을 못 찾았다 — 실패 출력은 `AssertionError: 1 != 0` 뿐이고, 단서(테스트 준비 함수 `alex_health` 가 app='jarvis' 를 준다)는 실패한 테스트에서 100줄 떨어져 있었다.

**결정:** 새 `jarvis/diagnostics.py`(ast·문자열만, 모델 호출·코드 실행 없음): 지금 후보(격리 복사본)에서 ①실패 출력의 오류 문구가 나오는 곳과 그것을 감싼 if·함수
②트레이스백이 가리키는 수정 대상 줄 ③실패한 테스트가 부르는 같은 모듈의 준비 함수·setUp(한 단계) ④①을 못 찾았을 때만 승인 루트의 다른 .py 제한 검색. 상한 파일 80·
파일당 200KB·합계 4MB·구간 12·2초, 루트 밖·심볼릭 링크·숨김/SKIP_DIRS·민감 이름 제외, 트레이스백 경로는 수정 대상·테스트와 끝이 맞을 때만, 경로처럼 생긴 글은 문구로 쓰지 않음.
설정 `coding_diagnostic_context`(기본 꺼짐): /dev-auto 가 원래 실패 출력·합격 테스트를 `propose_bundle_local` 에 넘기고, 고칠 파일을 다 넣은 **뒤 남는 자리에만**
(min(8000, 예산/10, 남은 자리), 600자 미만이면 넣지 않고 까닭 기록) "읽기 전용 진단 근거" 메시지로 붙인다. 모델 호출 수 그대로. 회차 기록에 근거의 경로·줄·해시·이유·예산
(`diagnostics`). 기존 발췌·API 요약·범위 검사·테스트 지문은 그대로. API 본문(`run(**kwargs)` 의 path) 은 API 요약의 `# kwargs: path` 가 이미 보여 줘 되풀이하지 않는다.
실제 remote 실패 출력으로 시험: 준비 함수 4개(setUp·alex_health·popen_opening_tunnel·run_connect)를 1개 파일만 읽고 골랐다. 테스트 `tests/test_diagnostics.py` 12건(심볼릭 링크 1건은 권한으로 건너뜀).
**측정:** 같은 얼린 복사본(v2 `320c4f61027159b3`)·같은 설정에서 근거 끔/켬 한 변수 비교(대기열 6). 효과 확인 전에는 켜지 않는다.

## ADR-081 — 만들기 루프: 새 프로그램은 쓰고·실행하고·고친다(기본 켬) + 코딩 후속은 코딩 모델로 + 생각 넘침 재시도

**문제(2026-10-05 테트리스 대화):** 한 번에 써서 넘긴 코드는 회전이 틀렸고, 고쳐 달라는 말에 실행 없이 추측했다(멀쩡한 계산 회전을 원인으로 지목).
코딩 단어 없는 후속이 대화용 모델(Q3)로 갔고, 코딩 모델(Q4)은 그 잘못된 조언을 따라 맞던 코드를 틀리게 바꿨다. 대화 8.6K + 생각 7.7K 로
16K 가 차서 답 없이 "/new" 안내가 두 번 나갔다.

**원인 실측(`docs/ALEX_CODING_ROOTCAUSE_20261005.md`):** 같은 모델·옵션에서 ALEX 메시지 그대로 / ADR-046 '짧게 생각' 블록만 뺌 / 질문만 —
같은 비율로 틀렸다(Q3 각 3회 중 2회 시작하자마자 실제 버그로 죽음). **프롬프트는 원인이 아니다.** ADR-046 블록은 27B 에서 이득도 해도 보이지 않아 그대로 둔다.
한 번에 제대로 도는 비율은 Q3 3/9, Q4 4/4 — 모델 차이는 크다. 어느 모델이든 한 번에 쓴 코드를 실행 없이 넘기는 구조가 공통 원인이다.

**결정:**
1. `jarvis/build_loop.py`: 만들기 말+대상("테트리스 게임 만들어줘")은 코딩 모델이 `data/builds/<시각>-<세션>` 에 도구(write_file·edit_file·read_file·run_python·finish)로
   만든다. **검사 파일(test_*.py) 실행이 종료 코드 0 이어야 끝낼 수 있다**(통과 전 finish 거절, 통과 뒤 고치면 다시). 후속 고침은 같은 폴더에서 이어서.
   공개 근거: Harness Engineering(arXiv 2609.00006) — 공개 코딩 에이전트 11개 모두 직접 짠 단순 반복 루프, 적고 굵은 도구, 출력 자르기, 끝내기 전 검증, 찾아-바꾸기 편집.
   OpenCode 는 쓰지 않는다(Ollama `/v1` 경로의 qwen3.8 멈춤·지연 보고, 셸 권한이 넓음).
2. 안전: 작업 폴더 밖·허용 확장자 밖 거부, 실행 전 AST 정적 차단(subprocess·socket·shutil·ctypes·winreg·urllib/requests·os.remove/system/exec* 등, 폴더 밖 open),
   셸 없이 20초·출력 3,000자(`developer._run_bounded` 재사용 + `extra_env`). **OS 수준 격리는 아니다** — developer 수정안 테스트와 같은 위험 등급.
3. `core.handle`: 같은 세션 바로 앞 요청이 코딩이었거나 코드 블록을 답했으면 코딩 단어 없는 후속도 LOCAL_CODER.
4. `providers`: 스트림에서 생각이 (출력 한도 2/3, 문맥 남은 자리) 상한을 넘으면 `ThinkingTooLong`(ContextExhausted 하위)로 끊고, 대화 경로는 한 번 생각 없이 다시 부른다.

**검증:** 새 검사 28개, 전체 1041개 RELEASE PASS. 실모델: Q3 루프 2분 43초 통과, Q4 루프는 15분 안에 못 끝냄(스스로 쓴 검사의 실수) —
실패한 실행에 실제 API 목록 동봉으로 보강. 표본 작음, 루프 통과율·시간은 다음에 과제 여러 개로 잰다.
**되돌리기:** `coding_build_loop_enabled=false`(만들기 루프만 끔). 원본 `logs/claude-fixes-20261005/baseline/`.

## ADR-082 — 채팅 코딩은 코드를 답으로 주고 실행 확인은 뒤에서(ADR-081 만들기 루프는 /build 로만)

**문제:** ADR-081 이 채팅의 "~ 만들어줘" 를 코딩 에이전트처럼 처리해 사용자 PC 의 `data/builds/` 에 파일을 만들고 요약만 답했다. 사용자는 코드를 답으로
받기를 원했다("그냥 코드를 짜서 달라고 한 건데"). 07:59 에는 후속 문장만 맥락 없이 새 작업으로 넘어가 무엇을 만들지 모른 채 빈 파일만 만들었다.
클라우드 채팅(ChatGPT·Claude.ai)도 코드는 답으로 주고 확인은 자기 격리 공간에서만 한다.

**결정:** 채팅의 만들기 부탁·코딩 후속은 평소처럼 대화 맥락으로 코드를 답한 뒤, 답의 파이썬 블록을 임시 폴더(삭제)에서 6초 실행해 본다(`code_check.py`).
시작 직후 오류면 그 오류만 주고 고친 전체 코드를 받아 최대 2번 다시 확인하고, 최종 코드에 확인 결과를 붙인다. 실행할 수 없는 경우(창·입력·미설치 패키지·위험 코드)는
실행하지 않고 이유를 적는다. 파일을 직접 만드는 루프는 `/build` 로만(바로 다음 "고쳐줘" 는 같은 폴더). 실행은 아스트라의 `build_runner.py` 감시(작업 폴더 밖 쓰기·
subprocess·socket 등 차단)를 쓴다 — OS 격리는 아니다.

**근거:** 한 번에 쓴 테트리스 Q3 9번 중 6번이 시작하자마자 실제 버그로 죽음(시작 직후 실행으로 잡힘). 실모델: 소수 프로그램 확인 통과(5초), 테트리스는 pygame 미설치로
확인 못 함 표시. 새 검사 12개, 전체 1071개 RELEASE PASS. **남은 것:** ALEX 파이썬에 pygame 이 없어 게임은 문법만 확인(설치는 사용자 결정).

## ADR-083 — /dev-auto 의 수정은 오픈코드가 하고 판정·적용은 ALEX 가 한다(설정 coding_worker, 기본 opencode)

**근거(공개 자료 먼저):** Qwen3.8-27B 로컬 코딩은 에이전트를 새로 만들지 말고 검증된 것(Claude Code via `ollama launch claude`, OpenCode, Pi)을 붙이는 게
표준이다(NVIDIA Local Coding Agent 안내, 16GB·UD-Q3_K_XL·MTP 로 OpenCode 2시간 자율 개발 사례). 사용자: "기존 자료 찾아보면 최적의 조합이 있을텐데 왜 이런
테스트를 하냐". 오픈코드를 고른 이유: 우리와 같은 조건의 성공 사례, 이 PC 확인(같은 과제 46초, `/v1` 지연 버그 없음), 평가 도구에 이미 연결, 오픈소스·로컬 설정.
Claude Code 는 기본 지시문이 커서 16~32K 문맥에 빠듯해 2순위로 둔다.

**결정:** /dev-auto 회차마다 '모델 수정안' 대신 오픈코드가 격리 복사본의 사본에서 고친다(`jarvis/opencode_worker.py`). ALEX 는 바뀐 파일을 읽어 묶음 수정안으로
만들고 작업 범위 밖·tests/·삭제는 버린 뒤(기록) 기존 루프 그대로 판정한다 — 합격 테스트, 전체 회귀 검사, 덜어내기(ADR-081 보충 수정), 사용자 승인. 오픈코드 권한은
셸 `python -m unittest`·`py_compile` 만, 인터넷·폴더 밖 금지, 공유·자동 업데이트 끔(OS 격리 아님). 모델은 문맥을 박은 Ollama 별칭(`qwen3.8-q3-32k`).
실행 파일·별칭이 없거나 실행이 안 되면 그 회차는 예전 모델 수정안 방식으로 돌아간다. 설정: `coding_worker`(alex|opencode), `opencode_dir`, `opencode_model`,
`opencode_context`, `opencode_timeout`.

**검증:** 가짜 오픈코드로 11개 검사(사본에서만 일함·범위 밖/테스트 변경 버림·시간 초과·중단·권한·없을 때 예전 방식·/build 재확인).
전체 1086개 RELEASE PASS. 실모델(Q3·32K): v3 과제 gpumodel_config **엄격 통과**, 오픈코드 42초·전체 444초. /build 도 오픈코드가 만들고 ALEX 가 검사를 다시 돌린다.


## ADR-084 — LoRA 학습 파이프라인: 승인된 '질문 → 좋은 답' → 작은 모델(4B) LoRA → 합쳐 GGUF → Ollama, 채택은 사용자

**계기:** 사용자 "낮은 모델로 LoRA학습은 되는거 같은데? 파이토치같은거. 그거 파이프라인 검토하고, 가능하면 구현해줘"(2026-10-05).
ALEX 에는 학습 데이터 내보내기(ADR-037)까지만 있었다.

**공개 자료 먼저:** 본 모델 Qwen3.8-27B 는 4비트 QLoRA 도 24GB 이상(Unsloth Qwen3.8 안내) — 16GB 인 이 PC 에서는 불가.
4B 는 LoRA 약 8~10GB(Unsloth 요구 사양 표). Ollama 0.34.1 부터 Modelfile `ADAPTER` 가 없어졌다(ollama#14969) — 이 PC 는 0.34.4.

**결정:** ①`scripts/lora.py`(ALEX 환경, 표준 라이브러리만) prepare → train → export → eval, 상태는 `data/lora/<ID>/run.json`.
②학습은 별도 환경 `C:\ALEX\tools\lora\venv`(torch 2.14.1+cu130·transformers 5.18·PEFT 0.21)의 `scripts/lora_worker.py` —
transformers + PEFT LoRA(r16·α16·전 선형층·답 토큰에만 손실). Unsloth 대신 이것: Windows 에서 컴파일 없이 설치되고 같은 코드를 CPU 로 시험할 수 있다.
③바탕 모델 Qwen/Qwen3-4B-Instruct-2507(일반 구조·생각 없이 답함·Apache-2.0). Qwen3.5-4B 는 4비트 학습 비권장·혼합 주의 커널·비전 모델이라 첫 판에서 뺐다.
④내보내기: 어댑터를 CPU 에서 합친 뒤 llama.cpp(b11404) 로 q8_0 GGUF → `ollama create alex-lora-<ID>`. 비교용 바탕 모델도 같은 변환으로 한 번.
⑤데이터는 `training_pool`(승인 + train) 중 **'질문 → 사용자가 좋다고 한 답'(answer·good·교정 없음)만**(`sft_ready`). 채팅 교정의 correction 은
사용자가 한 말이고 개발 교훈은 원칙이라, 학습하면 그 문장을 따라 말한다(연결 확인에서 발견). 50건 미만이면 시작 안 함(`--allow-small` 은 연결 확인용).
⑥GPU 는 `--yes` + 그래픽카드 사용 2GB 이하 + Ollama 모델 없음을 모두 통과해야 시작, 프로세스 VRAM 상한 11GB. 합치기·변환·CPU 평가는 GPU 를 숨긴다.
⑦`training_runs` 는 학습이 끝났을 때만 "LoRA 학습 완료: …" 로 남기고 `/growth` 는 실제 학습을 따로 센다. 스크립트는 ALEX 설정을 바꾸지 않는다 — 채택은 사용자.
⑧의존성 검사: `lora_worker.py` 에만 torch·transformers·peft·huggingface_hub 허용(`SEPARATE_ENV`), 같은 scripts/ 의 다른 스크립트는 로컬로 본다.

**검증:** 가짜 일꾼·변환기·ollama 로 22개 검사. 전체 1133개 RELEASE PASS. 이 PC CPU 만으로 처음부터 끝까지(연결 확인, 필터 전 교정 2건):
학습 3단계 16초·평가 손실 1.98→1.67, 합치기·GGUF·Ollama 등록 1분 45초, CPU 비교 생성 6분 — 일반 질문 답 변화 없음. 그래픽카드 470MiB·0% 그대로.
`logs/claude-lora-20261005/`, 상세 `docs/ALEX_LORA_PIPELINE_20261005.md`.

**한계:** 지금 '질문 → 좋은 답' 은 train 0건·eval 1건 — 효과 있는 학습은 아직 불가. GPU 학습과 Ollama 실제 생성은 아직 안 돌렸다(사용자 동의·다른 GPU 작업 없을 때).
4B 학습 모델이 27B 를 대신하지 않는다. 본 모델 학습은 맥 스튜디오(MLX)나 24GB 이상 GPU 에서 같은 데이터·평가로.


## ADR-085 — LoRA 학습 데이터: ALEX 문서·인계 교훈 → 27B 가 문답 생성·채점(7점 이상) → 후보, 사람이 묶음 승인

**계기:** 사용자 "학습데이터는 가능한방법중 추천방법으로 진행해줘"·"지금까지 인수인계했던부분들도 학습시킬수 있나?"(2026-10-05).
**결정:** `scripts/lora_data.py` — Meta synthetic-data-kit 과 같은 순서(생성 → 같은 모델 채점 → 기준 7점)를 표준 라이브러리로.
근거는 잘 안 바뀌는 문서(IDENTITY·PROJECT_ALEX·명령 도움말·SECURITY)와 인계에서 뽑아 둔 판단 교훈(kind=identity, `--set lessons`).
만든 문답은 candidate 로만 저장, 사람이 `approve --batch`. 출처 `synthetic:` 은 대화 교훈으로 쓰지 않는다(lessons.usable).
grade_identity 평가 질문과 같은 주제·비슷한 표현, 근거 문장이 새어 나온 답('자료에 따르면')은 버린다. `lora.py eval` 에 정체성 7문항 채점.
**상태:** 묶음 20261005-b(185건, 7건 제외 권장)·20261005-c(62건) 사용자 승인 대기. 첫 묶음 -a 는 방향이 틀려 거절(auto(regenerated)).

## ADR-086 — 로컬 모델 서버로 Strata(Qwen3.8-Flash-Next 125B MoE)를 쓸 수 있게 한다(설정 local_backend, 기본은 그대로 ollama)

**계기:** 사용자 "이 내용을 토대로 strata를 사용해서 alex를 업그레이드해보고 싶은데"(2026-10-10, 유튜브 소개 영상) → "전부실행해".

**공개 자료 먼저:** Strata(github.com/Niko1221/Strata, MIT, 별 1.95만, 0.1.41)는 MoE 전문가를 RAM 에 두고 자주 쓰는 것만 그래픽카드에
올려 12GB 이상 카드에서 Qwen3.8-Flash-Next(전체 125B·활성 6B, 공식 카드)를 돌린다. 공식 수치: DeepSWE 1.1 58.7(27B 42.2),
SWE-bench Pro 62.5(61.7), SWE-bench Multilingual 81.0(73.8). RAM 96GB 면 IQ3_S(공개 시험에서 원본과 같은 점수, 전문가 50GB) 권장.
RTX 5070 12GB 실측 IQ3_S 53 tok/s·프롬프트 1,620 tok/s. API 는 OpenAI 호환 `/v1`(127.0.0.1:8080), `/unload`·`/load`·`/health`.
주의(Strata 문서): 첫 적재 때 1~3분 PC 가 느림, NVIDIA 드라이버 리셋·블루스크린 사례(#961, 전력 제한 낮추기 권장).

**결정:** ①`providers.StrataProvider`(OllamaProvider 상속 — 도구 루프·생각 상한·중단·빈 답 판정은 그대로, 통신만 OpenAI SSE).
생각은 `reasoning_effort`(think 켜면 strata_reasoning_effort, 끄면 none). 도구 결과는 호출 id 로 돌려준다.
②설정 `local_backend`(ollama|strata, 기본 ollama), `strata_host`(이 PC 주소만), `strata_model`, `strata_num_ctx`(설치 때 고른 64K),
`strata_reasoning_effort`. strata 면 대화·코딩·요약이 한 Strata 모델, 오픈코드 일꾼도 Strata `/v1`.
③그래픽카드 번갈아 쓰기: 그림·`/unload` 는 서버 종류에 맞게 내린다(`unload_local_models`: Ollama keep_alive 0 / Strata `POST /unload`).
④런처: strata 모드면 Strata 서버를 띄우고(`strata-<모델>.json`, 로그 `logs/strata-server.log`) 모델이 올라올 때까지 기다린다.
⑤설치: `C:\ALEX\tools\strata`(git, 0.1.41), 모델 `C:\ALEX\tools\Strata-data`(IQ3_S 84GB). 거절을 줄이는 실험 옵션(speed projection)은 끔.

**검증(코드):** 가짜 Strata 서버로 16개 검사(SSE·생각 숨김·생각 상한·조각난 도구 호출·문맥 초과·503·끊김·/unload JSON·409·
설정 loopback·strata 모드 조립·오픈코드 연결·런처). 전체 1164개 RELEASE PASS. 실모델 측정은 아래 보충에 적는다.

**ADR-086 보충 (2026-10-10 11:05, claude) — 이 PC 실측과 전환:** 설치 0.1.41, IQ3_S 84GB(두 번째 파일은 setup 단일 연결이 2~7MB/s 라 huggingface_hub(hf_xet)로 40MB/s
받아 넣고 SHA-256 을 Hugging Face 값과 대조 — 일치). 설정 `strata-iq3_s.json` 에 `--vram-reserve-mib 2048` 추가(설치 안내: 이 카드가 화면도 맡음, #779;
원본 `.orig-20261010`). 첫 적재 약 30초, GPU 15.0~15.2GB. 짧은 답 59~76 tok/s(첫 글자 0.3~0.6초), 16,706토큰 입력 5.2초(3,192 tok/s),
최고 294W·57도(전력 제한은 사용자 결정으로 기본 360W). ALEX 를 `local_backend: strata` 로 전환(`jarvis.local.json`, 백업 `.bak-20261010-strata`),
웹 /api/state 에서 대화·코딩 모두 `strata:qwen3.8-flash-next`(num_ctx 65536) 확인, 실제 대화 4건 정상(자기소개 8.2초, 코드 3.7초).
증거 `logs/claude-strata-20261010/`. v3 11과제 /dev-auto(회귀 검사 켬) 측정은 진행 중(`devauto-v3-strata.log`).

## ADR-092 — 여러 사람이 쓰기: 계정·사람별 데이터·대기열, 프로젝트, 흑백 채팅 화면

**계기:** 2026-10-11 사용자 — "여러 명 동시 접속을 가정하고 미리 만들려는 건데… 사용자마다 화면이 따로 떠야", "UI 변경도 없이", "일단 데스크탑에서의 사용을 제대로", UI 는 '채팅형 유지 + 정리', 색은 '흑백 미니멀', "프로젝트 기능도 — Gemini 의 Gems 같은 것". Claude·ChatGPT 앱과 같은 형태.
**결정:** (1) 계정: users(scrypt 비밀번호)·auth_sessions(쿠키 토큰은 SHA-256 만 저장, HttpOnly·SameSite=Strict, 밖에서는 Secure, 30일). 이 PC(127.0.0.1, 프록시 머리글 없음)는 주인(관리자, id 'owner')으로 바로 들어오고 '사용자 전환'(alex_manual 쿠키) 뒤에는 로그인. 밖(Tailscale Serve)은 로그인 또는 관리자가 연결한 Tailscale 계정. X-Forwarded-*·Tailscale-User-Login 이 붙은 요청은 Host 가 127.0.0.1 이어도 이 PC 로 보지 않는다. 비밀번호 5회 틀리면 1분 잠금. (2) 사람별: sessions·tasks·memories 에 owner(스키마 v7, 예전 것은 모두 주인 것, 바꾸기 전 자동 백업), 만든 그림·영상·문서는 artifacts 표로 주인을 기록(기록 없는 예전 파일은 주인 것). 다른 사람의 대화·첨부·파일은 없는 것과 같다. (3) 권한: 관리자만 설정·엔진·종료·사용자 관리·작업 폴더·개발 모드·/build·/rtl·파일 명령·자료 경로 저장·모두의 학습 자료 추가. 일반 사용자의 요청은 core.member_mode(파일 찾기·시스템 정보·만들기 차단) + 자동 학습·코드 실행 확인 끔으로 처리하고 끝나면 되돌린다. 명령은 허용 목록(MEMBER_COMMANDS)만. (4) 대기열: 모델은 한 번에 한 요청(state.lock). 요청은 FIFO 표(사람당 하나)에서 차례를 기다리며 'queue' 사건으로 앞 사람 수를 받는다. 기다리는 요청은 본인(또는 관리자)만 취소. 대화 고르기·새 대화·기억·할 일·첨부는 모델을 쓰지 않으므로 기다리지 않는다. 직전 문답(교정·그림 고치기)은 사람마다 따로 보관해 바꿔 끼운다. (5) 프로젝트: projects(이름·지침 8,000자)와 sessions.project_id. 프로젝트 대화에는 지침을 system 으로(자료 뒤 — 문맥이 빠듯할 때 남도록) 넣고, 프로젝트 자료는 지식 DB scopes('project:ID')로 그 프로젝트 대화에서만 찾는다(모두의 자료 ''와 같이). 같은 파일은 본문을 한 번만 저장하고 쓰이는 곳이 없어지면 지운다. (6) 화면: 왼쪽 칸(새 대화·검색·프로젝트·날짜별 대화·계정 메뉴), 가운데 768px 대화(내 말 오른쪽 말풍선, ALEX 답은 말풍선 없이), 둥근 입력창, 흑백 토큰, 768px 아래는 왼쪽 칸이 서랍. 아이콘은 index.html 의 SVG(innerHTML 없이).
**검증:** tests/test_multiuser.py 15개(사람별 분리·관리자 경로 403·명령 허용 목록·member_mode 되돌림·로그인 잠금·사용 중지·밖 요청 로그인·프록시 위장 차단·파일 주인·대기열 순번·대기 취소·프로젝트 지침/자료 범위·프로젝트 삭제·빈 대화 재사용·남의 프로젝트), 전체 1313개 중 실패는 사용자가 만들고 있는 jarvis/2dsoul.py(pygame) 의존성 감사 1개뿐. 실제 Edge 로 관리자·로그인·두 사람 대기열('앞에 1명', 대기 5.2초)·휴대폰 너비 확인(logs/claude-desktop-ui-20261011/, JS 오류 0). 실제 DB 사본 이전: 대화 11·메시지 833·기억 2 그대로, 지식 자료 11개 그대로.
**한계:** 처리량은 그대로(이 PC 60~67토큰/초) — 대기 순서만 공정해진다. 교훈(experiences)·캐릭터 그림·모두의 학습 자료는 여전히 공용. Tailscale Serve 의 X-Forwarded-For 부착은 실제 재시작 뒤 확인 필요.

## ADR-093 — 생성 코드 실행 보호: 실행마다 주는 임시 폴더 안에서만 지우기 허용

**계기:** 2026-10-12 로컬 모델 코딩 비교 실측에서 csv_summary 가 답과 무관하게 실패했고, 같은 보호를 쓰는 ALEX 의 코드 실행 확인(만들기·코드 답 확인)도 `tempfile.mkdtemp()`(쓸 수 있는지 보려고 시험 파일을 만들고 지움)와 `TemporaryDirectory()`(끝날 때 지움)를 막아 임시 파일을 쓰는 올바른 프로그램을 실패로 판정함을 확인(작업 폴더에 시험 파일 200개가 쌓임). 사용자 승인: "그렇게 진행해".
**결정:** 실행마다 부모가 작업 폴더 안에 빈 `.alex-tmp` 를 만들어 TEMP·TMP·TMPDIR 로 주고, 실행이 끝나면 치운다. 실행 중 감시(build_runner)는 os.remove·os.rmdir·os.chmod·os.truncate·os.rename 을 **경로가 모두 그 폴더 안일 때만** 허용한다(rename 은 양쪽, 파일 번호·dir_fd 상대 경로는 거절, 심볼릭 링크는 따라가 판정). 작업 파일·폴더와 그 밖의 지우기·이름 바꾸기는 지금처럼 금지하고, 생성 코드의 명시적 지우기(os.remove·shutil·.unlink 등)는 정적 검사가 계속 먼저 거절한다. 임시 폴더는 작업 파일 목록·정적 검사·API 요약에서 뺀다. 비교 도구(scripts/compare_python.py)도 같은 규칙을 쓰고, 과제의 준비 코드는 신뢰된 검사 코드라 보호 전에 실행한다.
**검증:** test_build_loop 2개(임시 파일을 쓰는 프로그램이 통과하고 임시 폴더가 남지 않음 / getattr 로 정적 검사를 피한 작업 파일 지우기·임시 파일로 덮어쓰기·폴더 지우기는 실행 중 감시가 막고 파일 그대로) + test_model_compare 1개. 전체 1,350 중 실패 1(사용자 게임 파일 2dsoul 의존성 감사). code_check 로 TemporaryDirectory·csv 를 쓰는 프로그램이 ok_finished.
**한계:** 운영체제 격리가 아니다(기존과 같음). 임시 폴더 안의 지우기만 늘었다.

## ADR-112 — ALEX 자체 코딩 하네스 (2026-10-22 공개 기록)

OpenCode의 코딩 절차·도구·권한·문맥 압축 방식을 ALEX 파이썬 코드로 옮겨 모델 서버와 직접 연결한다. 기존 외부 OpenCode 경로도 유지한다. MIT 출처와 라이선스를 보존하며 모델 학습으로 표현하지 않는다.

목표 테스트 하나만 통과하고 관련 종료 계약을 놓친 사례에 따라 HarnessWorker에서 관련 검사를 독립 실행하고 실제 실패 출력으로 한 번 재수정한다. 검사 불명·전체 skip·단계 상한을 성공으로 처리하지 않는다. 직접 import 지도는 전체 회귀를 대체하지 않는다. 별도 `/code` 검증 공유와 호출 중 강제 시간 한도는 후속이다.

개발 PC는 로컬 coding_worker=harness, 공개 기본 opencode 유지. 과거 비교와 오늘 단회 재검증은 조건이 달라 범용 우위를 주장하지 않는다. 상세 [하네스 검토](docs/CODEX_HARNESS_REVIEW_20261022.md)·[공개 인수인계](docs/HANDOFF_20261022.md).

## ADR-113 — 코딩 경험으로 배우기·OpenCode 무료 모델·웹 다시 켜기 (2026-10-23 공개 기록)

코딩 세션 결과를 자동 수정 기록과 같은 형식으로 남겨 절차 교훈 추출(ADR-070)에 쓰고, 쉬는 중 자동 실행은 설정으로 켠다. 교훈을 스킬로 올리는 것은 사람이 고를 때만이다. OpenCode 의 무료 모델은 OpenCode 밖에서 직접 부를 수 없으므로(403 FreeTierError) 우회하지 않고 opencode.exe 를 통해 쓰며, 사용자가 고를 때만 코드를 보내고 개인 설정·기록은 빼고 보낸다. 실행기는 켜진 옛 코드 웹을 쉬는 중일 때 끄고 새로 켜며, 화면의 '다시 켜기' 는 웹이 실행기를 따로 띄우고 스스로 꺼진다. 상세 [공개 인수인계](docs/HANDOFF_20261023.md).

## ADR-114 — 자기 기능 알기·스스로 점검(준비)·말로 코딩 세션 (2026-10-23 공개 기록)

대화 모델이 명령·화면 기능을 몰라 '웹 검색뿐' 처럼 답했던 일에 따라, 기능 줄을 손으로 쓰지 않고 /help 와 화면 설명에서 뽑아 기능 질문일 때만 붙인다. 권한 단계와 스스로 하는 검색은 따로 적는다. 스스로 점검은 정답을 코드에서 얻고 사람이 부를 때만 돈다. 프로젝트에 있는 파일을 집어 고치라는 말은 코딩 세션으로 보내고 허락은 채팅의 짧은 답으로 받는다. 파일을 집지 않은 코드 요청은 코드를 답으로 준다.

## ADR-116 — 딥서치와 자동 선택 (2026-10-23 공개 기록, Codex)

여러 검색·본문·추가 확인·근거 보고를 로컬 모델로 하고, 복합 비교·근거 조사는 명령 없이 자동으로 고른다. 검색 금지·짧은 답 요청·로컬 파일·코딩 질문은 자동 조사에서 뺀다. 대화·기억·파일을 검색 계획에 넣지 않고 클라우드로 보내지 않는다. 상세 [CODEX_DEEPSEARCH_20261023.md](docs/CODEX_DEEPSEARCH_20261023.md). (ADR-115 는 문서 고치기 연결용으로 비워 둠.)

## ADR-115 — Office 첨부 사본과 프로젝트 문서 편집 (2026-10-23, Codex)

기존 officeedit 엔진을 office.OfficeEdits로 연결한다. 대화 소유 범위의 첨부는 원본 대신 마지막 편집 사본을 이어 편집한다. PC 문서는 관리자·승인된 프로젝트로 한정하고 read/ask/auto, 수정안 승인, 원본 해시 대조, 백업·원자적 교체·되돌리기를 적용한다. 다른 작업 뒤의 짧은 긍정 답을 이전 문서 승인으로 사용하지 않으며 기존 코딩 세션과 최신 작업 우선순위를 구분한다. 원문을 교훈으로 자동 승격하지 않고 모델은 로컬만 사용한다. 원본 패키지 보존과 작은 교정의 run 보존을 지원하되 모든 혼합 서식·Office 구조 편집을 보장하지 않는다. 합성 파일을 실제 Microsoft Office 3종에서 열고 PDF·PNG로 비교했다. 상세 docs/CODEX_IMPROVEMENTS_20261023.md.

## ADR-117 — 검사 통과 재사용에는 실제 실행과 현재 내용의 증거가 필요 (2026-10-23, Codex)

stdout 합계와 discover 경로만으로 파일 통과를 추정하지 않는다. 별도 unittest 어댑터가 파일별 실행 ID·성공·skip을 기록한다. 부모·자식 수정 revision을 공유하고 검사 전후/현재 프로젝트 입력 SHA가 일치할 때만 캐시한다. 증거가 없거나 범위를 넘으면 독립 재검사한다. 독립 검사도 목표 파일의 성공을 확인하고 전체 회귀는 유지한다. 긴 외부 일꾼 요청은 Windows argv 대신 실제 CLI로 검증한 stdin을 사용한다. 새 기본 모델/일꾼을 선택하거나 사용자 설정을 바꾸는 결정은 아니다. 조사 발췌 보강도 한 주제 검증으로만 보고하고 범용 정확도를 주장하지 않는다.

## ADR-118 — 밖으로 보내는 복사본의 키·인증 파일 목록을 하나로 (2026-10-26, Claude 클라우드)

OpenCode 무료 모델 경로(코딩 세션 `_copy_for_free`, /dev-auto 일꾼 `OpenCodeWorker.run`)는 `.env`·`jarvis.local.json`·`.pem`/`.key` 만 빼고 보내 `id_rsa`·`credentials.json`·`secrets.*`·`.pfx`·`.netrc` 등이 opencode.ai 로 나갈 수 있었다. 클라우드 넘기기(ADR-102)가 이미 쓰던 이름 목록을 `opencode_worker.SECRET_NAMES`·`SECRET_SUFFIXES`·`secret_file()` 로 한 곳에 두고 세 경로가 함께 쓴다. 이름만 보는 거름이며 내용 속 비밀값 검사(클라우드 넘기기의 secret_patterns)는 무료 모델 경로에 아직 없다. 상세 [CLAUDE_CLOUD_20261026.md](docs/CLAUDE_CLOUD_20261026.md).

## ADR-119 — 프롬프트 캐시를 살리는 순서(설정, 기본 꺼짐) (2026-10-27, Claude 클라우드)

대화 요청의 맨 앞 지시에 분 단위 시각이 있고, 그 뒤에 질문마다 다시 고르는 메모·교훈·자기 기능·자료가 와서 차례마다 요청이 거의 처음부터 달라졌다. 로컬 모델 서버는 앞 요청과 같은 앞부분만 다시 읽지 않으므로 매 차례 대화 전체를 다시 읽었다(이 PC 27b 입력 처리 초당 약 900~1,100토큰). `stable_prompt_prefix` 를 켜면 지시·요약·프로젝트 지침·대화 기록을 앞에 두고, 시각·메모·교훈·자기 기능·자료는 마지막 질문 바로 앞(시각이 맨 끝)에 둔다. 문맥이 넘쳐도 이 부분은 그 자리를 지키며 기존 system 예산을 나눠 쓴다. 꺼져 있으면 순서와 자르기는 예전과 같다. 답의 질이 달라질 수 있어 PC 측정(`scripts/measure_prompt_cache.py --live`) 뒤 사용자가 켠다. Hermes Agent 의 고정 시스템 프롬프트를 참고했다. 상세 [HERMES_REFERENCE_20261027.md](docs/HERMES_REFERENCE_20261027.md).

## ADR-120 — 배우지 말 것·남의 글 속 지시·교훈 보관 (2026-10-27, Claude 클라우드)

Hermes Agent(MIT)를 참고했다. ① `/dev-learn` 은 환경 탓 실패·'도구가 안 된다' 는 부정 주장·못 푼 시도·일시 오류를 교훈으로 쓰지 않는다 — 지시문에 적고, 어겨도 코드가 거른다(ALEX 의 '인터넷이 막혀 있다' 오답과 같은 꼴). ② `jarvis/threats.py`(Hermes 패턴 + 한국어)로 웹 페이지·검색 요약·저장 자료 조각에 지시 모양이 보이면 경고 줄을 붙이고 보이지 않는 글자를 뺀다. 교훈은 '따를 방식' 으로 붙으므로 지시 모양이 있으면 아예 뺀다. ③ 점수 0·사람이 뺀 코딩 교훈은 지우지 않고 보관함에 두며 되살릴 수 있다. 모델이 스스로 장기 메모를 쓰는 기능은 들이지 않는다(사람 승인 원칙). 상세 [HERMES_REFERENCE_20261027.md](docs/HERMES_REFERENCE_20261027.md).

