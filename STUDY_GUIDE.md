# Project JARVIS — Python Study Guide

이 프로젝트는 단순히 완성 코드를 받는 용도가 아니라, 직접 고치면서 Python을 익히기 위한 교재 역할도 한다.

## 추천 학습 순서

### 1단계 — 함수와 조건문: `jarvis/router.py`
먼저 여기부터 본다.

배울 것:
- 함수
- 문자열 처리
- tuple
- `any()`
- `Enum`
- `dataclass`
- if/elif 흐름

직접 해볼 것:
1. "엑셀 읽어줘"를 `local_coder`가 아니라 별도 `tool/file` 후보로 분류해 보기.
2. "코드 설명해줘"와 "3만 줄 코드 리팩터링"이 다른 곳으로 가는 이유 설명하기.
3. `CODE_WORDS`에 `numpy`를 추가하고 테스트 작성하기.

### 2단계 — 클래스: `jarvis/providers.py`
배울 것:
- class
- 상속
- ABC / abstractmethod
- 생성자 `__init__`
- method override

직접 해볼 것:
1. `EchoProvider`를 새로 만든다.
2. 입력 문장을 그대로 반환하되 글자 수를 함께 표시한다.
3. `health_check()`가 False인 Provider를 만들어 Core에서 어떻게 처리할지 생각한다.

### 3단계 — 데이터 저장: `jarvis/storage.py`
배울 것:
- SQLite
- context manager (`with`)
- SQL INSERT / SELECT / UPDATE
- parameter binding `?`
- list comprehension

직접 해볼 것:
1. `delete_memory(key)` 추가.
2. 최근 메모 5개를 보여주는 함수 추가.
3. `messages`에서 user 메시지만 조회하는 함수 추가.

### 4단계 — 시스템 정보: `jarvis/hardware.py`
배울 것:
- 예외 처리
- subprocess
- OS 차이
- fallback 설계

직접 해볼 것:
1. GPU가 없을 때 프로그램이 죽지 않는 이유 찾기.
2. RAM 16GB / VRAM 8GB 시스템은 왜 UNKNOWN인지 설명하기.
3. profile 기준을 config로 빼는 설계를 해보기.

### 5단계 — 객체 연결: `jarvis/core.py`
배울 것:
- composition
- dependency injection 기초
- dictionary로 객체 선택
- orchestration

직접 해볼 것:
1. `provider.health_check()` 실패 시 읽기 쉬운 메시지 반환.
2. local provider 실패 시 cloud로 자동 보낼지, 사용자 확인을 받을지 정책 설계.
3. Router 결과를 화면에 표시하지 않고 로그에만 남기는 옵션 만들기.

### 6단계 — 실제 프로그램 흐름: `jarvis/app.py`
배울 것:
- while loop
- 사용자 입력
- 명령 파싱
- 프로그램 entry point

직접 해볼 것:
1. `/memories` 명령 추가.
2. `/history 5` 명령 추가.
3. 잘못된 명령에 help를 보여주게 수정.

## 시험/연습 스타일 미니 과제

### 문제 1 — Router 카운트
문장 리스트를 받아 각 Destination별 개수를 dict로 반환한다.

### 문제 2 — Memory PASS 판정
메모 key가 3개 이상 저장되어 있으면 `PASS`, 아니면 `FAIL`을 출력한다.

### 문제 3 — 로그 파싱
`jarvis.log`에서 `route=` 값을 읽어 routing 횟수를 집계한다.

### 문제 4 — Session 통계
SQLite에서 세션별 user message 개수를 구한다.

### 문제 5 — Profile 판정 함수
RAM/VRAM 숫자를 입력받아 LAPTOP/DESKTOP/UNKNOWN을 반환하는 순수 함수를 별도로 작성한다.

## 학습 원칙

- 먼저 정답 코드를 보지 말고 현재 코드를 읽고 예상한다.
- 수정 전 테스트를 하나 만든다.
- 수정 후 테스트가 통과하는지 확인한다.
- 한 번에 한 기능만 바꾼다.
- Claude/GPT에게 처음부터 정답을 달라고 하기보다 "힌트만" 요청해도 좋다.

## 이 프로젝트에서 당장 공부할 우선순위

1. `router.py`
2. `storage.py`
3. `app.py`
4. `providers.py`
5. `core.py`
6. `hardware.py`

이 순서가 Python 기초 → 실제 프로그램 구조로 자연스럽게 올라간다.

---

# v0.1.1 추가 학습 포인트

## 7단계 — 예외 설계: `jarvis/config.py`, `jarvis/storage.py`

`ConfigError`, `StorageError`, `ProviderError`, `ToolError` 네 개만 정의했다.

볼 것:
- 왜 `ValueError` 를 그냥 쓰지 않고 `ConfigError(ValueError)` 로 감쌌는가
- `raise ... from exc` 의 의미 (원래 예외를 잃지 않는다)
- 예외 메시지에 "다음에 뭘 하면 되는지"를 넣는 습관

연습:
- `validate()` 에 `data_dir` 이 쓰기 가능한지 확인하는 규칙을 추가하고 테스트를 쓴다.

## 8단계 — 생성자(generator): `jarvis/tools.py` 의 `_walk`

`return` 대신 `yield` 를 쓴다. 전체 파일 목록을 메모리에 올리지 않는다.

볼 것:
- `stack = [root]` 를 쓰는 반복 방식(재귀 대신)
- `try/except` 로 권한 오류를 건너뛰는 이유
- `for ... : yield` 를 소비하는 쪽에서 `break` 하면 탐색이 멈춘다는 점

연습:
- `_walk` 가 몇 개의 파일을 훑었는지 세는 카운터를 붙여 본다.

## 9단계 — 실패를 흡수하는 흐름: `jarvis/core.py` 의 `_generate_with_fallback`

볼 것:
- `for candidate in (destination, *FALLBACK_CHAIN.get(destination, ()))` 의 언패킹
- `continue` 로 다음 후보로 넘어가는 패턴
- 모든 후보가 실패했을 때 "무엇을 시도했는지"를 사용자에게 돌려주는 이유

연습:
- 성공한 provider 이름을 `RouteDecision` 에 담아 CLI 에 표시해 본다.

## 10단계 — 네트워크 없이 네트워크 코드 테스트하기: `tests/test_providers.py`

`self.provider._post_json = fake_post` 한 줄로 HTTP 호출을 바꿔치기한다.

볼 것:
- 왜 `unittest.mock` 없이도 되는가 (Python 에서 메서드는 그냥 속성이다)
- 테스트가 실제 서버에 의존하면 안 되는 이유

연습:
- Ollama 가 `{"error": "model not found"}` 를 돌려줄 때를 테스트로 추가한다.

## 추가 미니 과제

### 문제 6 — Fallback 통계
`logs/jarvis.log` 를 읽어 `fallback used:` 줄을 세고, 어떤 경로가 가장 자주
실패했는지 출력하는 함수를 작성하라.

### 문제 7 — Router 커버리지
`router.py` 의 모든 키워드 튜플을 순회하며, 각 키워드가 실제로 의도한
`Destination` 을 만들어내는지 확인하는 테스트를 작성하라.

### 문제 8 — 설정 덤프
현재 `Settings` 를 표 형태로 출력하는 `/config` CLI 명령을 추가하라.
단, 나중에 API 키가 들어가면 가려야 한다는 점을 고려해 설계하라.

---

# v0.1.2 추가 학습 포인트

## 11단계 — Context Manager를 제대로 이해하기: `storage.py`

이번 버전에서 실제 버그를 하나 고쳤다.

```python
with sqlite3.connect(...) as conn:
    ...
```

이 코드는 transaction을 commit/rollback 하지만 connection 자체를 반드시 close하는 뜻은 아니다.
`Storage._connection()`은 `@contextmanager` + `finally`를 사용한다.

볼 것:
- `yield` 앞/뒤 코드가 언제 실행되는가
- `try/finally`가 resource 정리에 적합한 이유
- `python -W error::ResourceWarning ...`으로 숨은 resource leak을 찾는 방법

연습:
- 임시 파일을 열고 close를 빼먹은 테스트를 만든 다음 `ResourceWarning`을 관찰한다.

## 12단계 — Callback과 Dependency Injection: Cloud 승인

Core 안에서 `input()`을 직접 호출하지 않는다.

```text
Core -> CloudApprovalRequest -> approval_callback -> True/False
```

볼 것:
- `Callable[[CloudApprovalRequest], bool]`
- Core와 UI를 분리하는 이유
- callback이 예외를 내도 `False`로 처리하는 fail-closed 설계

연습:
- 테스트에서 `lambda request: False`와 `lambda request: True`를 각각 넣고 Cloud provider 호출 횟수를 비교한다.

## 13단계 — 작은 DB Migration

기존 사용자의 DB를 지우지 않고 새 컬럼을 추가한다.

볼 것:
- `PRAGMA table_info(sessions)`
- `ALTER TABLE ... ADD COLUMN`
- migration 테스트가 왜 새 DB 테스트보다 중요한가

연습:
- 직접 v0.1.1 모양의 SQLite DB를 만든 뒤 `Storage(old.db)` 한 줄로 migration되는 과정을 확인한다.

## 14단계 — 대화 압축 알고리즘

전체 대화를 계속 LLM에 넣지 않는다.

```text
오래된 대화 -> rolling summary
최근 대화   -> 그대로 유지
장기 메모   -> 별도 system context
```

볼 것:
- `summary_through_id`가 중복 요약을 막는 방법
- Local 요약 실패 시 extractive fallback
- 왜 Cloud 모델을 자동 요약기로 쓰지 않는가

연습:
- `summary_trigger_messages=6`, `history_limit=4`로 낮춰 10개의 메시지를 넣고 DB의 summary 변화를 출력한다.

## 15단계 — 보안 경계가 있는 File Tool

`SafeFileSearchTool`과 `SafeFileReadTool`을 비교한다.

볼 것:
- `.resolve()`와 `Path.parents`
- 상대경로/절대경로
- path traversal (`../`)
- symlink escape
- 파일 크기와 확장자 allow-list

연습:
- 프로젝트 밖 파일을 가리키는 symlink를 만들고 `/read`가 막는지 테스트한다.

## 16단계 — 실사용 데이터를 이용한 Router 개선

`/wrong`을 누르면 다음 두 곳에 남는다.

```text
SQLite route_events
logs/misroute.jsonl
```

볼 것:
- JSON Lines 포맷
- `Counter`
- 먼저 측정하고 나중에 규칙을 바꾸는 개발 방식

실행:

```bash
python scripts/misroute_stats.py
```

## 17단계 — 실제 Local LLM 성능 측정

사용자 Laptop/Desktop에서:

```bash
python scripts/check_environment.py
python scripts/benchmark_local.py
```

배울 것:
- `time.perf_counter()`
- HTTP streaming line 처리
- JSON 결과 저장
- `subprocess.run()`으로 `nvidia-smi` 읽기

### 학습 과제 9 — `/history N`
현재 세션 최근 N개 메시지를 보여주는 CLI 명령을 직접 추가한다.

### 학습 과제 10 — Memory 관련성 점수
지금은 최근 장기 메모를 넣는다. 사용자 질문 단어와 key/value가 겹치면 점수를 높여 상위 N개만 선택하도록 **Vector DB 없이** 구현해 본다.

### 학습 과제 11 — Router 분석 CSV
`misroute.jsonl`을 읽어 `destination, correction, matched, count` CSV를 만드는 스크립트를 작성한다.

### 학습 과제 12 — Backup 보존 정책
`data/backups`에 10개보다 많이 있으면 가장 오래된 것을 삭제하는 함수를 작성하되, 실제 삭제 전 `dry_run=True`를 기본으로 만든다.

---

# v0.1.3 추가 학습 포인트

## 18단계 — Profile에 따라 설정을 바꾸는 순수 함수: `app.effective_local_models`

같은 프로그램이 Laptop/Desktop에서 다른 모델을 고르는 핵심은 거대한 클래스가 아니라 작은 순수 함수다.

볼 것:
- `Enum`
- `(general, coder)` tuple 반환
- Settings를 직접 mutate하지 않는 이유
- 입력이 같으면 결과가 같은 함수가 테스트하기 쉬운 이유

연습: `PerformanceProfile`별로 기대 모델을 dict로 만든 뒤 table-driven test를 작성한다.

## 19단계 — Bounded scan과 Cache: `tools.py`

프로젝트 전체를 무제한 읽으면 느리고 위험하다.

볼 것:
- 최대 파일 수 / 총 byte / 최종 context chars가 서로 다른 제한인 이유
- 파일 `mtime/size`를 이용한 작은 cache invalidation
- binary/hidden/symlink를 건너뛰는 순서
- 민감한 filename을 automatic context에서 제외하는 이유

연습: 같은 파일을 두 번 검색했을 때 두 번째는 cache를 쓰고, 내용을 변경하면 cache가 무효화되는 테스트를 직접 다시 작성한다.

## 20단계 — Fail-closed 보안 로직: Cloud gate

보안 코드에서 중요한 질문은 “성공하면 무엇을 하나?”보다 **“판단에 실패하면 허용하나, 막나?”**다.

현재 JARVIS는 callback 부재/예외/거부를 모두 Cloud 불허로 처리한다.

연습:
1. approval callback이 `True`
2. `False`
3. exception
4. callback 자체가 없음

네 경우의 provider 호출 횟수를 표로 만든다.

## 21단계 — 작은 HTTP Provider 직접 읽기: `providers.py`

외부 SDK 없이 `urllib.request`와 JSON만으로 API adapter를 만든다.

볼 것:
- Base Provider interface
- API key를 error/log에 넣지 않는 이유
- OpenAI/Anthropic/Gemini의 request/response shape 차이를 Core 밖에 가두는 방식
- HTTP error와 JSON parse error를 `ProviderError` 하나로 정규화하는 이유

연습: network를 쓰지 말고 `_request_json`을 fake 함수로 교체해 request payload를 검사하는 unit test를 작성한다.

## 22단계 — Web 최소 보안: `web.py`

Local Web UI라고 해서 모든 요청을 신뢰하면 안 된다.

볼 것:
- loopback bind
- process runtime token
- `Content-Type: application/json`
- body size bound
- CSP / frame header

연습: token 누락, 잘못된 content type, 과대 body가 각각 거부되는 테스트를 따라 작성한다.

## 23단계 — 릴리스 엔지니어링: `verify_release.py` + `package_release.py`

코딩이 끝난 것과 “다른 모델에게 안전하게 넘길 수 있는 산출물”은 다르다.

흐름:

```text
compile
 -> tests
 -> dependency audit
 -> router eval
 -> security smoke
 -> E2E smoke
 -> ZIP/TAR
 -> archive content audit
 -> SHA256
```

볼 것:
- `subprocess.run()`의 return code
- `zipfile`, `tarfile`
- SHA-256 streaming hash
- 왜 `data/`, `logs/`, `__pycache__`를 handoff에서 빼는가

## 24단계 — Benchmark를 “성능 개선”보다 먼저 만드는 이유

`benchmark_local.py`는 모델을 빠르게 만드는 코드가 아니라 **측정 기준을 만드는 코드**다.

배울 것:
- cold/warm run 차이
- first-content latency vs total latency
- eval tokens/sec
- model load duration
- VRAM before/after

숫자를 먼저 재고 그 다음 설정을 바꾸는 습관을 연습한다.

## 추천 실습 순서

1. `study/practice.py`의 기초 5문제
2. `study/practice_v013.py`의 v0.1.3 문제
3. `tests/test_router.py` 한 테스트 직접 추가
4. `/history N` 직접 구현
5. `verify_release.py` 통과
6. 본인 변경을 `CHANGELOG.md`에 한 줄 기록

코드를 바꿀 때는 정답을 바로 AI에게 받기보다 먼저 실패하는 테스트 또는 작은 함수부터 직접 작성한다.

# v0.2.0 추가 학습 트랙

## 1. `jarvis/workspace.py`

배울 것:

- dataclass로 편집 제안 표현
- SHA-256으로 stale state 확인
- `difflib.unified_diff`
- `tempfile.mkstemp` + `os.replace` 원자적 파일 교체
- 백업과 undo
- “AI 출력”과 “실제 권한” 분리

직접 설명해볼 질문:

1. 왜 `/edit-file`과 `/apply-edit`을 한 명령으로 합치지 않았는가?
2. 왜 원본 파일이 context budget보다 크면 편집을 거부하는가?
3. 왜 proposal 생성 시 SHA-256을 저장하는가?
4. `os.replace()`가 단순 `write_text()`보다 어떤 실패를 줄이는가?

## 2. SQLite Task / App state

`jarvis/storage.py`의 `add_task`, `list_tasks`, `set_task_status`, `set_state`, `get_state`를 읽는다.

학습 포인트:

- 구조화된 데이터와 key/value Memory의 차이
- SQL UPDATE/DELETE
- parameter binding
- status/priority validation

## 3. `jarvis/portable.py`

학습 포인트:

- JSON serialization
- schema/version validation
- conservative merge
- 중복 제거
- 기존 데이터 overwrite 정책

연습:

- Task priority가 잘못된 bundle을 import할 때 skip되는 이유 설명
- 기존 Memory key가 있을 때 imported value로 덮지 않는 정책의 장단점 적기

## 4. 실전 과제

v0.2.0을 실제 Laptop에서 켠 뒤 다음을 직접 해본다.

1. Task 3개 추가 후 하나 완료
2. 작은 연습용 `.py` 파일을 `/edit-file`로 수정 제안
3. diff에서 바뀐 줄을 직접 읽기
4. apply 후 Python 실행/테스트는 JARVIS가 자동 수행하지 않는 이유 설명
5. `/undo-file`로 원복
6. portable state export 내용을 텍스트 에디터로 열어 구조 확인

---

# v0.2.x 추가 학습 포인트 — 실제로 겪은 것들

여기부터는 2026-09-13 ~ 09-18 사이에 이 저장소에서 **실제로 일어난 일**을 교재로 삼는다.
남의 예제가 아니라 내 코드에서 난 버그라 기억에 남는다.

---

## 11단계 — 시간은 생각보다 촘촘하지 않다: `storage.py`, `workspace.py`

이 저장소에서 **같은 원인으로 버그가 두 번** 났다. 한 번은 메모 정렬, 한 번은 파일 백업이다.

### 무슨 일이 있었나

```python
# 둘 다 이렇게 쓰고 있었다
timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
```

`%f` 는 마이크로초다. 백만 분의 1초까지 찍히니 겹칠 리 없어 보인다.
**그런데 Windows의 시계 해상도는 약 15.6밀리초다.** 즉 `datetime.now()` 를 아무리 빨리
두 번 불러도 15.6ms 안에서는 **완전히 같은 값**이 나온다. 마이크로초 자리는 그냥 0으로 채워진다.

결과:

| 위치 | 증상 |
|---|---|
| `storage.remember()` | 연속 저장한 메모가 같은 `updated_at` → "가장 최근 메모" 순서가 뒤집힘 |
| `workspace.apply()` + `restore_latest()` | 백업 폴더 이름이 같아짐 → **undo가 직전 백업을 덮어써서 되돌리기 이력 소실** |

두 번째가 특히 고약하다. 테스트가 "3회 중 1회" 만 실패해서 한동안 그냥 불안정한 테스트로
보였다. 실제로는 사용자 데이터가 사라지는 버그였다.

### 배울 것

- 시계 해상도(clock resolution)와 값의 자릿수는 다르다
- 단조 증가(monotonic) 보장이 필요한 자리
- 간헐적으로 실패하는 테스트를 "불안정하니까 무시" 로 처리하면 안 되는 이유

### 직접 해볼 것

1. 이걸 직접 확인해 보라. Windows에서:
   ```python
   from datetime import datetime
   xs = [datetime.now().strftime("%Y%m%dT%H%M%S%fZ") for _ in range(1000)]
   print(len(xs), "번 호출 →", len(set(xs)), "개의 서로 다른 값")
   ```
   1000이 아니라 훨씬 적은 수가 나온다. 몇 개나 나오는지 보고, WSL(리눅스)에서도 돌려 비교하라.
2. `jarvis/workspace.py` 의 `_next_backup_stamp()` 를 읽고, `storage.py` 의
   `_next_memory_timestamp()` 와 **왜 구현이 다른지** 설명하라.
   (힌트: 하나는 DB의 MAX 값을 보고, 하나는 인스턴스 변수를 본다. 각각 무엇을 가정하는가?)
3. `_next_backup_stamp()` 는 인스턴스 변수를 쓴다. **프로세스가 두 개면 어떻게 되는가?**
   이 한계가 실제 문제가 되는 상황을 하나 적어 보라.

---

## 12단계 — 남이 쓴 코드를 안전하게 실행하기: `scripts/grade_codegen.py`

모델이 만든 코드가 맞는지 확인하려면 **실행해 봐야** 한다. 그런데 그 코드는 신뢰할 수 없다.

### 읽을 곳

`grade()` 함수. 다음을 왜 그렇게 했는지 생각하며 읽는다.

```python
subprocess.run([sys.executable, str(path)],
               capture_output=True, text=True,
               timeout=exec_timeout, cwd=tmp)
```

- **왜 `exec()` 이 아니라 별도 프로세스인가?** — 같은 프로세스에서 돌리면 `sys.exit()`
  하나로 채점기가 같이 죽는다. 무한 루프면 영원히 멈춘다.
- **왜 `timeout` 인가?** — 실제로 겪었다. 측정 중 한 번은 생성된 코드가 7케이스 전부
  타임아웃을 냈다. 무한 루프거나 `input()` 대기였다. 타임아웃이 없었으면 측정이 멈췄다.
- **왜 `cwd=tmp` 인가?** — 상대 경로로 파일을 쓰면 임시 폴더 안에만 떨어진다.
- **왜 `@@GRADE@@` 마커를 쓰는가?** — 생성 코드가 stdout에 아무거나 찍을 수 있으니,
  채점 결과를 식별할 표식이 필요하다.

### 배울 것

- `subprocess` 의 `capture_output`, `timeout`, `cwd`
- 프로세스 격리가 왜 예외 처리보다 강한 보호인가
- `tempfile.TemporaryDirectory()` 의 컨텍스트 매니저 정리

### 직접 해볼 것

1. `grade()` 에서 `timeout=` 을 지우고 무한 루프를 만드는 코드를 넣어 보라. 무슨 일이 나는가?
2. 생성 코드가 `sys.exit(1)` 을 부르면 지금 코드는 어떻게 되는가? 직접 확인하라.
3. `RUNNER` 문자열을 읽고, 케이스마다 `try/except` 로 감싼 이유를 설명하라.
   전체를 하나의 `try` 로 감싸면 무엇을 잃는가?

---

## 13단계 — 측정으로 결정을 닫기: `DECISIONS.md` ADR-036

이건 코드가 아니라 **판단**에 대한 공부다. 엔지니어링에서 더 중요할 때가 많다.

### 이야기

계산기 코드 품질이 3.0/7에서 멈췄다. 남은 수단은 "생성 → 실행 → 오류 되먹임 → 재생성"
루프였다. 그런데 이걸 앱에 넣으려면 **모델이 쓴 코드를 자동 실행**해야 하고,
그건 `SECURITY.md` 가 막아 둔 경계다.

흔한 선택은 둘 중 하나다.

1. 일단 넣고 본다 → 경계가 뚫린다
2. 위험하니 안 한다 → 개선 기회를 버린다

셋째 길을 골랐다. **"효과가 있는지 먼저 싸게 재보고, 있으면 그때 경계를 논의한다."**
측정은 앱이 아니라 개발자 도구에서 했다(`--refine`). 결과:

```text
8회 중 7회가 두 번 고쳐도 점수가 전혀 안 움직였다
최초 평균 2.5/7 → 되먹임 2회 후 3.0/7 (완전통과 0/8)
```

모델은 `'(' was never closed` 라는 구체적인 오류를 받고도 같은 코드를 다시 썼다.
**효과가 없으니 경계를 열 이유도 없어졌다.** ADR은 "보류" 가 아니라 "측정 결과 도입 안 함" 으로 닫혔다.

### 배울 것

- 위험한 변경 앞에서 **싼 실험을 먼저 설계하는 법**
- "안 하기로 했다" 도 근거와 함께 기록해야 하는 이유 (안 그러면 6개월 뒤 같은 논쟁을 반복한다)
- 부정적 결과(negative result)도 결과다

### 직접 해볼 것

1. `DECISIONS.md` 의 ADR-034(thinking 끄기)와 ADR-036을 나란히 읽어라.
   둘 다 "측정 후 안 쓰기로 함" 인데 **기각 근거의 성격이 어떻게 다른가?**
2. ADR-036의 "대안과 기각 사유" 에서 "한 번 승인으로 N회 반복" 이 왜 기각됐는지
   자기 말로 설명하라.
3. 만약 RTX 5080에서 14B 모델로 같은 측정을 했더니 되먹임이 3.0 → 6.5로 올랐다면,
   그때는 어떤 순서로 무엇을 다시 논의해야 하는가?

---

## 14단계 — 채점기를 못 믿을 때: `scripts/grade_identity.py`

가장 실전적인 교훈이다. **측정 도구 자체가 틀릴 수 있다.**

### 무슨 일이 있었나

정체성 평가를 처음 돌렸더니 **3/7** 이 나왔다. 절망적인 점수다.
그런데 모델의 실제 답변을 읽어 보니:

| 케이스 | 모델 답변 | 채점 | 진실 |
|---|---|---|---|
| `wrong_premise` | "아니요." | FAIL | **정답이다.** 기대 표현 목록에 "아니요" 가 없었다 |
| `partial_failure` | "완료했다고 보고하기는 어렵습니다" | FAIL | **정답이다.** 패턴이 "완료라고" 여서 "완료했다고" 를 못 잡았다 |
| `unknown_fact` | "확인할 수 있는 방법은..." | FAIL | **정답이다.** 금지 표현이 잘못 발동했다 |

7개 중 **3개가 거짓 실패**였다. 진짜 점수는 6/7이었다.

### 무엇을 고쳤나

판정 순서를 뒤집었다.

```python
# 전: 금지 표현을 먼저 보고, 걸리면 무조건 실패
# 후: 기대 행동이 보이면 통과. 금지 표현은 기대 행동이 없을 때만 본다
```

### 배울 것

- **점수가 이상하면 원본을 봐라.** 6/7을 3/7로 읽으면 엉뚱한 곳을 고치게 된다
- 키워드 매칭의 취약함. 한국어는 어미 변화("완료라고" / "완료했다고") 때문에 특히 그렇다
- 그래서 `grade_identity.py` 에 `--show` 옵션이 있다. 답변 원문을 같이 보라고 만든 것이다
- `IDENTITY.md` 6절이 "이 점수는 회귀 감시용 신호이지 정체성의 증명이 아니다" 라고
  적어 둔 이유

### 직접 해볼 것

1. `--show` 로 돌려 답변 원문과 판정을 나란히 읽어라. 아직도 미심쩍은 케이스가 있는가?
2. `measured_vs_guess` 는 지금 **일관되게 실패**한다(3회 중 3회). 답변 원문을 보고
   이것이 거짓 실패인지 진짜 실패인지 판단하라. 근거를 적어라.
3. 새 케이스를 하나 설계해 `CASES` 에 추가하라. `IDENTITY.md` 의 어느 항목(I-1~I-5)에
   대응하는지 밝히고, **거짓 실패가 나지 않도록** 기대 표현을 충분히 넓게 잡아라.
4. 어려운 문제: 키워드 대신 더 나은 판정 방법이 있을까? 모델에게 채점을 시키면
   안 되는 이유는 `PERFORMANCE.md` 8절에 있다. 그것 말고 무엇이 가능한가?

---

## 추가 미니 과제

### 문제 9 — 시계 해상도 재기
`datetime.now()` 를 1000번 불러 서로 다른 값이 몇 개인지 세는 스크립트를 쓰고,
Windows Python과 WSL Python에서 각각 돌려 결과를 비교하라.

### 문제 10 — 되먹임 로그 읽기
`logs/codegen-grade-*.json` 에서 `trajectory` 필드만 뽑아, 되먹임으로 점수가 **오른 실행**과
**안 오른 실행**의 수를 세는 스크립트를 쓰라.

### 문제 11 — 정체성 회귀 감시
`logs/identity-*.json` 두 개를 받아 **어느 항목(I-1~I-5)이 나빠졌는지** 비교 출력하는
스크립트를 쓰라. 이것이 `PROJECT_ALEX.md` 8절이 말하는 "여전히 ALEX다운가" 의 실제 구현이다.

### 문제 12 — 채점기 검증
`grade_identity.py` 의 `score_case()` 만 따로 테스트하는 단위 테스트를 쓰라.
14단계에서 발견한 3개의 거짓 실패가 **다시 생기면 잡히도록** 케이스를 넣어라.

---

# v0.2.x 추가 — 15~17단계: 데이터 설계

11~14단계가 "코드의 버그" 였다면, 여기부터는 **"설계의 버그"** 다. 코드는 테스트로 잡히지만
설계는 6개월 뒤에야 드러난다. 그래서 더 비싸다.

---

## 15단계 — 왜 기억과 경험을 다른 테이블에 두는가: `storage.py`

### 먼저 직접 보라

```
python scripts/experiences.py status
```

그리고 `jarvis/storage.py` 에서 `memories` 와 `experiences` 두 테이블 정의를 나란히 읽어라.

```sql
memories    (key, value, updated_at)
experiences (id, created_at, kind, situation, judgment, rationale, choice, outcome,
             verdict, correction, source, model, stage, split, ..., content_hash)
```

### 왜 이렇게 갈랐나

`memories` 는 **사용자가 고치고 지우는** 것이다. "내 장비는 4070" 같은 것. 프롬프트에 실린다.
`experiences` 는 **학습 코퍼스**다. 한 번 쓰면 본문이 바뀌지 않는다.

둘을 한 테이블에 넣으면 이런 일이 난다.

```
1월:  경험 #42 를 ALEX-M1 학습에 사용
3월:  사용자가 #42 의 내용을 수정
5월:  M1 이 이상하게 동작 → "어떤 데이터로 학습했지?" → #42 를 열어봄
      → 3월에 바뀐 내용이 보임. 1월에 무엇으로 학습했는지 알 길이 없다
```

**출처 추적이 거짓이 된다.** 롤백이 의미를 잃는다.

### 배울 것

- 같은 "저장" 이라도 **수명 주기가 다르면 다른 테이블**이다
- 불변성(immutability)이 왜 감사(audit)의 전제인가
- `content_hash` 로 불변을 강제하는 방법

### 직접 해볼 것

1. `_experience_hash()` 를 읽어라. **왜 본문 다섯 필드만 해시하고 `stage`, `verdict` 는 빼는가?**
   둘 다 넣으면 무슨 일이 나는지 설명하라.
2. 위조 실험:
   ```python
   import sqlite3
   from contextlib import closing
   with closing(sqlite3.connect("data/jarvis.db")) as c:
       c.execute("UPDATE experiences SET judgment='위조' WHERE id=(SELECT id FROM experiences LIMIT 1)")
       c.commit()
   ```
   그다음 `/experiences` 를 실행하라. 무엇이 나오는가?
   **(끝나면 그 경험을 `rejected` 로 바꿔 두어라.)**
3. `sqlite3.connect()` 를 `with` 로만 쓰면 Windows 에서 파일이 잠긴다. 왜인가?
   `tests/test_experiences.py` 의 주석에 답이 있지만, 먼저 스스로 생각해 보라.

---

## 16단계 — 사람이 고르면 안 되는 것: 결정적 분할

### 문제

학습셋과 평가셋을 나눠야 한다. 가장 자연스러운 방법은 "기록할 때 고르는 것" 이다.

```python
store.record_experience(..., split="train")   # ← 이렇게 하고 싶어진다
```

**그런데 `record_experience` 에는 `split` 파라미터가 없다.** 일부러 막았다.

### 왜

사람이 고르면 **흥미로운 사례가 학습으로 몰린다.** 어려운 질문, 모델이 멋지게 답한 것,
공들여 고친 것 — 전부 "학습에 넣고 싶은" 것들이다. 그러면 평가셋에는 시시한 것만 남는다.

평가셋이 실제 분포를 대표하지 못하면 **점수가 거짓으로 좋아진다.** 그리고 그걸 눈치채지
못한다. 평가가 쉬워졌을 뿐인데 모델이 좋아졌다고 착각한다.

### 해법

```python
digest = hashlib.sha256(experience_id.encode()).digest()
bucket = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
return "eval" if bucket < 0.2 else "train"
```

ID 만으로 결정된다. 사람의 취향이 못 끼어든다. 언제 어디서 계산해도 같은 답이다.

### 배울 것

- 해시를 **난수 대신** 쓰는 이유 (재현 가능성)
- 선택 편향(selection bias)이 평가를 어떻게 망가뜨리는가
- **"할 수 없게 만드는 것" 이 "하지 말자고 약속하는 것" 보다 강하다**

### 직접 해볼 것

1. `_split_for()` 를 100개의 가짜 ID 로 돌려 train/eval 비율을 세어 보라. 0.2 에 가까운가?
2. `random.random() < 0.2` 를 쓰면 무엇이 깨지는가? **구체적인 시나리오**를 하나 써라.
   (힌트: DB 를 복구하거나 경험을 다른 기기로 옮길 때)
3. 20% 가 적절한가? 경험이 100건일 때와 10,000건일 때 각각 생각해 보라.

---

## 17단계 — 약속이 아니라 구조로 막기: `training_pool` 뷰

### 가장 중요한 한 줄

```sql
CREATE VIEW training_pool AS
    SELECT * FROM experiences WHERE stage='approved' AND split='train';
```

### 왜 뷰인가

"평가용은 학습에 쓰지 맙시다" 는 **약속**이다. 약속은 깨진다. 6개월 뒤 급할 때,
새로 합류한 사람이, 혹은 다른 AI 에이전트가 `SELECT * FROM experiences` 를 쓴다.

뷰는 안 깨진다. `training_pool` 만 쓰면 **평가셋이 물리적으로 들어올 수 없다.**

### 실물로 확인된 것

경험 1건을 `/good` 으로 기록하고 승인한 뒤 내보냈더니:

```
train 0 -> datasets/.../train.jsonl     ← 비어 있음
eval  1 -> datasets/.../eval.jsonl
```

그 경험이 해시상 `eval` 로 배정됐기 때문이다. **승인했는데도 학습에 안 들어간다.**
버그처럼 보이지만 설계대로다.

### 배울 것

- 데이터베이스 뷰의 실제 쓸모 (단순 편의가 아니다)
- 불변식(invariant)을 코드가 아니라 **스키마에 새기는** 법
- 테스트로 불변식을 고정하는 법 — `test_training_pool_never_contains_eval`

### 직접 해볼 것

1. `tests/test_experiences.py` 의 네 불변식 테스트를 읽어라. 각각 **어떤 실수를 막는가?**
2. `test_train_and_eval_sets_are_disjoint` 는 60건을 **전부 승인**한다. 왜 굳이 전부인가?
   10건만 승인하면 이 테스트가 무엇을 놓치는가?
3. `scripts/experiences.py` 의 `cmd_export()` 에 이런 코드가 있다.
   ```python
   overlap = {e.id for e in train} & {e.id for e in evals}
   if overlap:
       print("FATAL: ...")
       return 1
   ```
   **뷰가 이미 막는데 왜 또 검사하는가?** 이게 방어적 프로그래밍인지 중복인지 판단하고
   근거를 써라. (정답이 하나가 아니다)

---

## 추가 미니 과제

### 문제 13 — 경험 흐름 추적
`/good` 을 누른 순간부터 `train.jsonl` 에 들어가기까지, 데이터가 지나는 **모든 함수**를
순서대로 나열하라. `core.record_answer_feedback` 에서 시작한다.

### 문제 14 — 자동 경험 수집
`grade_identity.py` 가 실패한 케이스를 자동으로 경험으로 남기도록 고쳐라.
`kind='identity'`, `verdict='bad'`, `source='grade_identity:<case_id>'` 로 남긴다.
**주의:** 이건 GPT_NEXT.md 의 1순위 작업이다. 먼저 손대고 싶으면 보드에 알려라.

### 문제 15 — 검토 큐
`stage='raw'` 인 경험을 오래된 것부터 하나씩 보여주고 승인/거부/건너뛰기를 받는
대화형 스크립트를 써라. 경험이 100건 넘어가면 `review <id>` 를 일일이 치는 건 불가능하다.

### 문제 16 — 계보 조회
`ALEX-M1` 과 `ALEX-M2` 를 받아 **M2 에만 들어간 경험**을 출력하는 스크립트를 써라.
성능이 떨어졌을 때 범인을 좁히는 도구다. `experiences_in_run()` 을 쓴다.

### 문제 17 — 설계 비평 (코드 없음)
지금 설계의 약점을 하나 찾아 근거와 함께 써라. 힌트가 필요하면 아래를 보라.
- 경험이 10만 건이 되면 `verify_experience_integrity()` 는 어떻게 되는가?
- `EVAL_RATIO` 를 나중에 바꾸면 무슨 일이 나는가?
- 한 경험이 두 모델 버전에 들어갔다면 `training_members` 는 그걸 표현할 수 있는가?

### 문제 18 — 측정기가 거짓말한 사례
2026-09-19 에 실제로 있었던 일이다. 코드 채점기의 실패 로그가 이렇게 남아 있었다.

```
run 1: score=0  92.9s   SyntaxError: invalid syntax
run 3: score=0  92.91s  SyntaxError: unterminated string literal
```

읽으면 "모델이 문법을 틀렸다" 로 보인다. 그래서 한동안 그렇게 믿었다. 실제 원인은 달랐다.

1. `logs/codegen-grade-20260918-233512.json` 을 열어 run 1 의 `code` 앞 300자를 출력하라.
   무엇이 1행에 있는가? 그게 왜 `SyntaxError` 를 만드는가?
2. 같은 `code` 에서 ` ``` ` 이 **몇 번** 나오는가? 짝이 맞는가? 안 맞는다면 왜인가?
3. 비어 있지 않은 줄 가운데 중복 비율을 구하라. `repetition_ratio()` 를 쓰지 말고 직접 세라.
4. 이제 진짜 원인을 한 문장으로 쓰라. 그리고 **예산(`num_predict`)을 두 배로 올리면
   이 실패가 해결되는가?** 답과 이유를 쓰라.

**여기서 배울 것:** 측정 도구가 틀리면 잘못된 곳을 고치게 된다. 이 버그가 없었다면
"예산이 부족하다" 가 아니라 "반복 루프를 끊어야 한다" 가 먼저 보였을 것이다.
채점기는 판정자가 아니다 — `tests/test_identity_scoring.py` 와 같은 교훈이 반복된 것이다.

### 문제 19 — 재현 불가능한 벤치마크
`scripts/grade_codegen.py` 의 `generate()` 는 `seed` 를 보내지 않는다.

1. 같은 설정으로 10회를 돌리면 0/7 부터 7/7 까지 나온다. `seed` 를 고정하면 어떻게 되는가?
2. 그렇다면 **왜 처음부터 seed 를 고정해 두지 않았는가?** seed 를 고정한 벤치마크로
   "설정 A 가 B 보다 낫다" 를 주장할 때 생기는 문제를 하나 쓰라.
3. `--seed 1` 로 n=5 를 돌려 보라. 5회가 모두 같은 점수인가? 아니라면 seed 외에
   무엇이 더 달라지고 있는가? (힌트: `keep_alive`, GPU 오프로딩)

### 문제 20 — cmd 파일의 한글
`.cmd` 안에 한글 주석 한 줄을 넣었더니 `'/d' 은(는) 명령이 아닙니다` 가 나면서
스크립트 전체가 깨졌다. 한글은 5번째 줄에 있었는데 2번째 줄이 깨졌다.

1. 원인을 설명하라. (힌트: 파일은 UTF-8 인데 cmd.exe 는 무엇으로 읽는가?)
2. `tools/launch.ps1` 에는 왜 UTF-8 **BOM** 이 필요했는가? `.cmd` 와 무엇이 다른가?
3. 이 부류의 버그를 막는 규칙을 한 줄로 쓰라.

### 문제 21 — 오염된 측정 찾기
2026-09-19 에 같은 모델을 같은 `auto` 설정으로 두 번 쟀는데 결과가 달랐다.

```
1차:  VRAM 2.30G / RAM 10.79G / 18% GPU / 17.1 tok/s
2차:  VRAM 5.65G / RAM  7.42G / 43% GPU / 23.4 tok/s
```

1. 설정이 같은데 왜 다른가? (힌트: 잴 때 **다른 모델**이 무엇을 하고 있었나)
2. `OLLAMA_MAX_LOADED_MODELS=1` 인데도 이런 일이 생긴다. 왜인가? (힌트: `keep_alive`)
3. 모델 A 와 B 를 공정하게 비교하려면 사이에 무엇을 해야 하는가?
4. **이 두 값 중 어느 쪽도 "틀린" 것은 아니다.** 그런데 하나는 쓸 수 없다. 왜인가?

### 문제 22 — 같은 손잡이, 반대 방향
`num_gpu`(GPU 에 올릴 층 수)를 두 모델에서 훑은 결과다.

```
qwen3:14b (밀집)      auto 10.3 → 32 로 15.2 tok/s   (+48%)
gpt-oss:20b (MoE)     auto 23.4 → 24 로  8.7 tok/s   (-63%)
```

1. MoE 는 매 토큰 전체 파라미터 중 일부만 쓴다. 이 사실로 −63% 를 설명하라.
2. 밀집 모델에서 층을 GPU 로 옮기면 왜 이득인가? MoE 에서는 왜 아닌가?
3. 두 모델 모두 어떤 지점을 넘으면 급격히 느려진다. 그 지점의 **공통 원인**은 무엇인가?
   (힌트: `nvidia-smi` 의 `memory.total` 과 표의 VRAM 값을 비교하라)
4. "GPU 를 최대한 쓰면 빠르다" 는 문장을 **틀리지 않게** 다시 쓰라.

### 문제 23 — 표본을 두 배로 늘렸더니 설명이 바뀌었다
같은 비교를 n=10 과 n=20 으로 했다.

| | n=10 | n=20 |
|---|---|---|
| thinking 최댓값 감소 | −28% | −5% |
| 예산 소진 감소 | 2 → 0 | 4 → 1 |
| 완전통과 | 7/10 → 10/10 | 12/20 → 18/20 |

n=10 때 claude 는 **"점수 차이는 유의하지 않으니 메커니즘(−28%)이 근거"** 라고 적었다.

1. 세 지표 중 n=20 에서 살아남은 것은 무엇인가?
2. `P(B ≥ 18) = 0.032` 와 `P(B ≤ 1) = 0.17` 을 각각 해석하라. **"유의하지 않다" 가
   "같다" 를 뜻하는가?**
3. 결론(도입한다)은 바뀌지 않았는데 **이유는 바뀌었다.** 이것이 왜 중요한가?
   나중에 누군가 이 결정을 인용할 때 무슨 일이 생기는가?
4. n=10 에서 저 설명을 쓰지 않으려면 그때 무엇을 했어야 하는가?

### 문제 24 — 목록에 없는 값
`jarvis.local.json` 의 `local_max_output_tokens` 를 실측으로 6144 로 올렸다.
웹 설정창의 출력 토큰 `<select>` 에는 512/1024/2048/4096 만 있었다.

1. `select.value = 6144` 를 하면 무슨 일이 생기는가? 직접 브라우저 콘솔에서 확인하라.
2. 그 상태로 저장 버튼을 누르면 `Number($('output-limit').value)` 는 무엇을 돌려주는가?
3. 그래서 설정창을 **열고 저장만 해도** 무슨 일이 일어나는가?
4. `jarvis/static/app.js` 의 `setOutputLimit()` 이 이 문제를 어떻게 막는지 읽고 설명하라.
5. **교훈을 한 줄로 쓰라.** 코드에서 설정값을 바꿀 때 무엇을 같이 확인해야 하는가?

### 문제 25 — 학습 데이터의 판정은 누가 하는가 (코드 없음)
ALEX 의 경험 저장소는 `verdict`(good/bad)를 가진다. 이것이 학습 신호다.

1. 모델이 자기 답변을 스스로 `good`/`bad` 로 판정하면 무슨 문제가 생기는가?
2. 2026-09-19 에 claude 가 다섯 번 틀렸고 다섯 번 다 교정됐다. **교정한 주체는 누구/무엇인가?**
   그것이 왜 중요한가?
3. `scripts/experiences.py import-corrections` 는 문서에 **명시적으로 적힌 정정만** 수확한다.
   "정정처럼 보이는 문장" 을 LLM 에게 찾게 하면 안 되는 이유를 쓰라.
   (힌트: `tests/test_identity_scoring.py` 와 `tests/test_codegen_extract.py`)
4. `/good` 을 누르는 사람이 틀렸다면? 이 설계는 그것을 막을 수 있는가? 막아야 하는가?

### 문제 26 — 튜닝한 문제로 채점하기
2026-09-21 까지 이 저장소는 "파이썬 코딩 18/20" 이라는 숫자를 갖고 있었다.
그 18/20 은 계산기 한 문제(`grade_codegen.py`)의 점수다.

1. `jarvis/core.py` 의 `CODING_SYSTEM_ADDENDUM` 을 읽어라. 그 지시들은 **어느 과제에서
   관찰된 실패**를 겨냥하는가? `PERFORMANCE.md` 8절과 대조하라.
2. 그 지시를 붙이고 **같은 과제로** 채점하면 무엇을 재는 것인가?
3. `scripts/grade_python.py` 를 열어 과제 6개를 보라. `grade_codegen.py` 와 무엇이
   다르게 설계돼 있는가? **테스트를 모델에게 보여주지 않는 이유**를 쓰라.
4. 직접 새 과제를 하나 추가하고 정답 코드와 엉터리 코드로 각각 돌려 보라.
   **하네스가 엉터리를 잡지 못하면 그 과제는 쓸모가 없다.**

### 문제 27 — 학습으로 풀려던 문제가 교체로 풀렸다 (코드 없음)
`IDENTITY.md` 의 I-2(사실과 추정 구분)는 `qwen3:8b` 에서 **0/3** 이었다.
`DEFAULT_SYSTEM_PROMPT` 에 "추측을 사실처럼 말하지 않는다" 가 **이미 있는데도** 그랬다.

그래서 `PROJECT_ALEX.md` 4절은 이것을 "프롬프트로 닿지 않는 행동" 으로 규정하고
**ALEX-M1 의 첫 학습 목표**로 삼았다. 2026-09-21 에 `gpt-oss:20b` 로 재니 **7.0/7** 이었다.

1. 무엇이 틀렸던 것인가 — 진단인가, 처방인가, 둘 다인가?
2. 이 사실이 ADR-037(경험 저장소) 전체를 무의미하게 만드는가? 왜 그런가/아닌가?
3. **"학습으로 고친다" 는 계획을 세우기 전에 무엇을 먼저 해봐야 하는가?**
   비용을 비교해서 답하라.
4. 반대로, **모델 교체로 풀리지 않을 종류**의 문제는 무엇인가? 예를 하나 들어라.

### 문제 28 — 측정 도구의 예산이 모자랐을 뿐
`"내 컴퓨터 사양이 어떻게 돼?"` 에 모델이 **빈 답**을 돌려줬다. 나(claude)는 그것을
"앱이 하드웨어를 안 알려줘서" 로 진단했다.

실제 응답은 이랬다.

```
done_reason=length   eval_count=250   content=67자   thinking=776자
```

1. `done_reason=length` 는 무슨 뜻인가? 무엇이 모자랐는가?
2. `think: False` 인데 `thinking` 이 776자다. 이것이 예산에 어떤 영향을 주는가?
3. 같은 질문을 `num_predict` 를 2000 으로 올려 직접 돌려 보라. 답이 나오는가?
4. **빈 답을 보고 대상을 탓하기 전에 무엇을 확인했어야 하는가?** 한 줄로 쓰라.
   (힌트: 문제 18 과 같은 교훈이다)

### 문제 29 — 빈 문자열은 모든 문자열의 부분문자열이다
표현식 파서의 참조 정답을 쓰다가 **여덟 케이스 전부** 이 오류로 실패했다.

```
ValueError: invalid literal for int() with base 10: ''
```

문제가 된 줄은 이것이다. 입력 끝에서 `peek()` 는 `""` 를 돌려준다.

```python
def peek(): return s[pos[0]] if pos[0] < len(s) else ""
...
while peek() in "*/":      # ← 여기
```

1. 파이썬에서 `"" in "*/"` 를 직접 쳐 보라. 결과가 왜 그런가?
2. 그래서 입력 끝에 도달하면 무슨 일이 생기는가? 한 줄로 설명하라.
3. 고치는 방법을 **두 가지** 쓰라. 하나는 조건을 더하는 것이고, 다른 하나는 `peek()` 가
   돌려주는 값을 바꾸는 것이다. 어느 쪽이 더 안전한가?
4. 이 함정은 `in` 연산자가 **문자열에서는 부분문자열 검사**이기 때문에 생긴다.
   리스트나 집합을 썼다면 어땠겠는가? `while peek() in ("*", "/")` 를 쳐서 확인하라.

### 문제 30 — 잘린 답은 완성된 답처럼 보인다
`OllamaProvider` 는 `done_reason` 을 `last_metrics` 에 담고 있었는데 **아무도 읽지 않았다.**
그래서 예산이 소진돼 끊긴 답이 화면에 그냥 답으로 나왔다.

1. `jarvis/providers.py` 에서 `last_metrics` 에 `done_reason` 이 담기는 곳을 **두 군데**
   찾아라. 스트리밍 경로와 비스트리밍 경로다.
2. `jarvis/core.py` 의 `_truncation_notice()` 를 읽어라. 왜 `destination` 을 받는가?
   일반 모델 지표를 보면 안 되는 이유를 쓰라.
3. `tests/test_truncation_notice.py` 의 `test_missing_metrics_say_nothing` 이 없으면
   무슨 일이 생기는가?
4. **사용자 입장에서** 왜 이 한 줄이 중요한가? 끝이 어색한 답을 보면 사람은 무엇을
   의심하는가? (힌트: 모델을 의심하지 예산을 의심하지 않는다)
