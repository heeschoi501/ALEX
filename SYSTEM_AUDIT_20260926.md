# ALEX 안정화 감사 및 Coding Mode — 2026-09-26

## 범위와 실행 조건

현재 `jarvis/`, `scripts/`, `tests/`, 시작 스크립트, 설정, 인수인계와 최근 Model Catalog 변경을 조사했다. 작업 초기에는 Claude Code의 Stage 2 측정이 WSL에서 실행 중이어서 측정 스크립트·실제 Ollama 모델·성능 결과 파일을 건드리지 않았다. 측정 종료 후 전체 검증과 웹 앱 재시작을 진행했다. 실제 대형 모델 생성·다운로드는 수행하지 않았다. 이 폴더에는 `.git` 저장소가 없다.

## 현재 구조

| 책임 | 코드 | 확인한 동작 |
| --- | --- | --- |
| 시작·종료 | `start_alex.vbs`, `jarvis/launcher.py`, `jarvis/web.py`, `jarvis/lifecycle.py` | VBS가 `pythonw` 런처를 열고, 런처가 필요할 때 Ollama와 웹 서버를 각각 실행한다. 웹의 `/api/shutdown`은 작업 중 409로 거절하며 웹 서버만 종료한다. |
| 설정·구성 | `jarvis/config.py`, `jarvis/app.py` | 설정 검증, 프로필별 General/Coding 모델 구성, 로컬·클라우드 provider 조립. |
| 추론·도구 | `jarvis/core.py`, `jarvis/router.py`, `jarvis/providers.py`, `jarvis/tools.py` | 라우팅, 대화와 요약, 로컬 Ollama, 독립적인 OpenAI/Anthropic/Gemini 어댑터, 안전한 파일 읽기·검색. |
| 모델 목록 | `jarvis/model_registry.py`, `jarvis/web.py`, `jarvis/static/app.js` | 정적 검증 카탈로그, Ollama `/api/tags`의 설치 목록, `/api/ps`의 로드 목록, General/Coding 선택, 명시적 `/api/pull`. 다운로드와 실행 가능 여부는 별개다. |
| 저장·학습 | `jarvis/storage.py`, `jarvis/lessons.py` | SQLite 대화·경험·교훈·학습 후보. 검증된 경험의 검색/재사용은 있으나 모델 가중치를 자동 업데이트하지 않는다. |
| 개발 작업 | `jarvis/workspace.py`, `jarvis/developer.py` | 기존 승인형 파일 수정과 이번 감독형 테스트·수정안 반복. |

## 우선순위별 발견 및 처리

| 등급 | 발견 | 처리 |
| --- | --- | --- |
| Critical | 이 감사에서 재현된 항목 없음. 전체 회귀 검증은 통과했지만 대형 모델 실제 적재는 시험하지 않았다. | 모델 실행 확인은 별도 작업 |
| High | 채팅 스트림 헤더 송신 중 실패하면 `active_id`가 남아 서버가 계속 바쁜 것으로 보일 수 있었다. | `_stream` 전체 경로에 정리를 적용하고 테스트 추가 |
| High | 독립 요약 Ollama provider에는 중단 신호가 전달되지 않아 긴 요약을 중단하지 못했다. | 채팅 중단 신호를 요약 provider에도 연결하고 테스트 추가 |
| High | 개발 모드 테스트 실행은 출력이 무한히 늘거나 중단 버튼 뒤에도 최대 120초 계속될 수 있었다. | 출력 로그 2MB 제한, 시간 제한, 사용자 중단 시 프로세스 종료, 결과 상태 분리 |
| High | 모델 설정은 `/api/tags`의 설치만 확인한다. 메모리 부족, CUDA 오류, 실제 `generate` 실패를 사전 보장하지 못한다. | 현 구조를 명시. 측정 중 강제 적재 검사는 하지 않음. 선택 후 첫 실패 진단과 선택적 실행 확인 기능이 후속 과제 |
| Medium | ALEX 화면 종료는 웹만 끄고 Ollama 프로세스는 남긴다. `runtime/ollama.pid`만으로 오래된 PID의 소유권을 안전하게 확인할 수 없다. | 웹 `status/start/stop/restart` 명령 추가. Ollama 자동 종료는 보류 |
| Medium | `/api/tags`, `/api/ps` 조회가 provider 내부와 웹 모델 상태에 걸쳐 있고, `/api/pull`은 런처·설정 스크립트·웹에서 각 용도로 사용된다. | 중복은 확인했으나 동작 중인 경로를 합치지 않음 |
| Medium | Ollama 접속 실패 시 모델 목록 조회는 빈 집합을 돌려준다. UI가 '서버 꺼짐'과 '설치 모델 없음'을 명확히 구별하지 못한다. | 데이터 손실은 없으나 상태 표시 개선 필요 |
| Medium | 웹 UI 일부 작업은 별도 JSON API 대신 `/dev-*`, `/apply-edit` 같은 채팅 명령을 보낸다. 모바일/PWA 클라이언트도 명령 문자열을 알아야 한다. | 현재 UI 보존. 장기적으로 작업 API와 인증 흐름 분리 필요 |
| Medium | 개발 모드는 기존 전체 파일 교체 편집기와 한 파일·한 테스트에 한정된다. 미적용 수정안은 앱 재시작 후 사라진다. Git 작업 트리도 없다. | 제한을 UI와 문서에 표시. 작업 상태/테스트 이력은 SQLite에 저장 |
| Medium | 선택한 프로젝트의 테스트 파일은 Python 코드로 실행된다. 승인된 프로젝트라도 테스트의 자체 부작용까지 격리하는 샌드박스는 없다. | 명시적인 `/dev-next`에서만 실행하고, 테스트 경로·시간·출력을 제한. 무신뢰 저장소 실행은 후속 설계 필요 |
| Medium | `/growth`의 경험·교훈 재사용과 학습 후보 저장은 모델 가중치 학습이 아니다. LoRA 학습·독립 평가·승격/rollback 파이프라인은 없다. | 현재 UI/문서에서 단계 구분 유지. 자율 학습으로 과장하지 않음 |
| Low | `Jarvis*`, `PROJECT_JARVIS.md` 등 과거 명칭이 남아 있다. | 광범위한 이름 변경을 하지 않음 |
| Low | 최상위 Python 함수의 정적 참조 수를 확인했지만 테스트·CLI·다른 모듈에서 호출되는 함수가 많아 안전하게 지울 수 있는 dead code는 확정하지 못했다. `jarvis/*.bak-*` 같은 과거 백업 파일도 남아 있다. | 참조 증거 없이 삭제하지 않음 |

Ollama가 꺼진 경우 provider의 health check가 실패하고 core는 다른 후보를 시험하거나 사용자에게 실패를 설명한다. 서버 자체가 모델 적재 실패 하나로 종료되는 구조는 아니다. 기존 테스트는 provider 연결 실패·중단·fallback을 검증한다. OpenAI, Anthropic, Gemini는 각각 별도 HTTP 어댑터지만 현재 설정은 한 클라우드 회사만 coder/reasoning 두 역할에 함께 적용하며, 일상 웹 UI의 승인 콜백은 클라우드 호출을 거절한다. 역할별 GPT·Claude 동시 사용은 아직 없다.

## 이번에 구현한 감독형 Coding Mode

1. 승인된 프로젝트에서 기존 파일 하나와 `tests/test_*.py` 하나, 목표를 등록한다. 목표·단계·시도·테스트 결과는 기존 SQLite `app_state`에 저장한다.
2. `/dev-next`는 해당 테스트 파일만 고정된 인자 목록으로 실행한다. 실패하면 로컬 코딩 모델의 수정안을 생성하고 diff를 보여 준다. 파일은 그대로 둔다.
3. 기준 테스트가 통과했지만 새 기능을 만들려면 `/dev-propose`로 목표에 따른 수정안을 요청한다.
4. 사용자가 `/apply-edit ID --yes`로 적용하거나 `/discard-edit ID`로 폐기한다. 적용 시 기존 `SafeWorkspaceEditor`의 원본 백업·원자적 교체·stale 파일 검사·`/undo-file`을 재사용한다.
5. 적용 뒤 `/dev-next`로 다시 시험한다. 테스트는 최대 3번 실행한다. 통과 뒤 소스가 바뀌면 통과 상태를 취소한다.

UI의 ‘개발 모드’ 패널에서도 같은 명령을 실행한다. 이 모드는 임의 터미널, 여러 파일의 패치, Git 브랜치/커밋, 자동 승인, 완전 자율 반복을 제공하지 않는다. 기존 `/tree`, `/grep`, `/ask-project`, `/ask-file`은 저장소 이해에 재사용할 수 있다.

## 변경 파일

- 신규 코드: `jarvis/developer.py`, `jarvis/lifecycle.py`.
- 기존 코드: `jarvis/app.py`, `jarvis/web.py`, `jarvis/static/index.html`, `jarvis/static/app.js`.
- 신규 테스트: `tests/test_developer.py`, `tests/test_lifecycle.py`, `tests/test_web_stream_lifecycle.py`.
- 기록: 이 보고서, `DECISIONS.md` ADR-063, `CHANGELOG.md`, `GPT_NEXT.md`, `CLAUDE_NEXT.md`. 기존 파일의 작업 전 사본은 `logs/codex-backup-20260926-developer-mode/`에 있다.

## 시작·종료 명령

프로젝트 폴더에서 Windows Python으로 `python -m jarvis.lifecycle status`, `start`, `stop`, `restart`를 실행한다. `stop`은 같은 ALEX 인스턴스인지 확인하고 웹의 인증된 `/api/shutdown`을 사용한다. 생성·다운로드 중이면 서버가 409로 거절한다. `restart`는 종료 확인 뒤 기존 런처를 다시 실행한다. 이 명령은 Ollama를 종료하지 않는다. Stage 2 종료 후 실제 재시작과 지문 일치까지 확인했다.

## 책임과 변경 위험

- `config.py`와 `app.py`는 값 검증과 provider 조립, `providers.py`는 HTTP 추론, `model_registry.py`는 수동 검증한 후보, `storage.py`는 영속화, `web.py`는 로컬 HTTP·화면 연결을 맡는다. `web.py`의 설정 저장/모델 조회/다운로드/채팅 처리에는 책임이 다소 집중돼 있다.
- 현재 작동 중인 `SafeWorkspaceEditor`의 파일 격리·백업, 웹의 loopback/토큰/Host 검사와 cloud 승인 차단, storage의 마이그레이션, router golden corpus, Stage 2 측정 스크립트는 근거 없이 다시 작성하지 않는다.
- 다음 확장에서는 패치 단위 변경과 여러 파일의 원자적 제안, Git 작업 트리 감지와 diff, 작업별 상태/API를 먼저 설계한다. 웹 화면만 바꾸면 모바일 확장 문제가 해결되지 않는다.

## 다음 우선 개발 과제 3개

1. **모델 실행 가능성 확인:** Claude Code의 Stage 2 판정은 `PERFORMANCE.md` 26-9절에 기록됐다. 사용자 모델/FA 결정 뒤 설치됐지만 실행에 실패하는 모델의 실제 오류·메모리 상태를 진단하고 첫 생성 실패 안내를 검증한다. 125B 모델을 무작정 적재하지 않는다.
2. **Coding Mode 확장:** 작은 패치·여러 파일의 승인 가능한 변경 묶음, 작업별 로그/취소/rollback, 저장소가 Git일 때만 상태·diff·브랜치 확인. 임의 명령 실행은 샌드박스와 권한 모델을 먼저 설계.
3. **모델·provider 경험 개선:** 설치와 실행 가능 상태를 분명히 구분하고 첫 생성 실패 원인을 표시. 이후 역할별 클라우드 provider 선택·키 관리·요청별 승인 UI를 별도 작업으로 구현.

## 검증

- Stage 2는 09:43:57에 종료됐다. Windows Python 3.11로 `scripts/verify_release.py` 실행: **722 tests, 2 skipped, 전체 PASS**. compileall, Python 3.10 문법 검사, 의존성 감사, 라우터 golden corpus, 보안/alpha/beta smoke도 모두 PASS. 로그는 `logs/codex-system-audit-verify-20260926.txt`.
- 측정 종료 후 웹 앱을 `python -m jarvis.lifecycle restart`로 안전 재시작했다. `/health`의 서버 지문과 디스크 지문은 모두 `54b31a180f9c35be`. Coding Mode 화면과 `/dev-status`를 실제 HTTP로 확인했다. `/api/models`는 카탈로그 3·설치 8·로드 0, 선택된 125B 모델은 설치됐지만 언로드로 응답했다. 기존 브라우저 탭에는 F5가 필요하다.
- 재시작 뒤 Windows 프로세스 조회에서 `pythonw.exe`는 0개, `python.exe` 웹 서버는 1개(PID 34696, 127.0.0.1:8765 LISTENING), `ollama.exe`는 1개였다. 종료 범위가 웹과 Ollama로 분리된 것을 확인했다.
- 실제 Ollama 모델 다운로드·적재와 모델 생성 품질은 시험하지 않았다. 특히 현재 선택된 125B 모델은 설치 확인만으로 안정적인 생성이 보장되지 않는다.
