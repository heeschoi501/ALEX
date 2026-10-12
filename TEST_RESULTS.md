# TEST RESULTS — Project ALEX (구 JARVIS)

## 2026-10-12 — 공개본 대기열·PWA 보완

기준: `main`의 `4a39bf4aeb77e07bd87ead5dd5af32de834ede0f`에서 수정. Linux / Python 3.12.14, 모델·GPU 호출 없이 임시 DB와 테스트 서버 사용.

| 검사 | 결과 |
|---|---|
| 전체 단위·통합, ResourceWarning 포함 | 1,323개: 1,318 PASS·5 SKIP·0 FAIL; 51.928초 |
| 스트리밍 상태 회귀 | 7/7 PASS; 새 테스트 5개는 수정 전 실패 확인(오류·생성 취소 subTest 포함 6개 실패) |
| 사용자 분리·프로젝트 HTTP 검사 | 15/15 PASS |
| Python 3.10 문법 감사 | PASS, 160개 파일; 실제 3.10 실행과는 별개 |
| 의존성 / compileall / alpha / beta | 모두 PASS |
| 라우터 / 보안 스모크 | 83/83 / 13/13 PASS |
| wheel 설치 검증 | 정적 파일 8개 바이트 일치; manifest·서비스 워커·아이콘 2개 HTTP 200·MIME 확인 |
| 릴리스 종합 | PASS |

이번 공개본 통과는 개발 PC의 별도 pygame 게임을 포함한 원본 검사 결과와 구별한다. 실제 GPU/LLM의 중단 동작·Windows 런타임·Tailscale 원격은 이번에 재검증하지 않았다. 상세 및 롤백은 [보완 기록](docs/CODEX_HARDENING_20261012.md).

아래는 이전 버전의 검증 기록이다.

## 2026-09-19 — 경험 저장소

```text
RELEASE VERIFICATION: PASS   315/315 (skipped=2)
신규 테스트 16개 — tests/test_experiences.py
```

네 가지 불변식을 테스트로 고정했다.

1. **본문 불변** — 검토해도 `content_hash` 가 바뀌지 않는다. DB 를 직접 위조하면 잡힌다.
2. **결정적 분할** — 같은 ID 는 항상 같은 쪽. `record_experience` 에 `split` 파라미터가 없다.
3. **검토 없이는 학습 불가** — `raw`/`rejected` 는 학습 풀에 안 들어간다.
4. **평가셋 오염 차단** — 60건을 **전부 승인해도** 학습 풀에 `eval` 이 하나도 없다.

실물 확인: 앱에서 `/good` → 경험 1건 기록 → 승인 → 내보내기. 그 경험이 해시상 `eval` 로
배정되어 **`train.jsonl` 이 비어 있고 `eval.jsonl` 에만 들어갔다.** 구조적 차단이 실제로 동작한다.


## 2026-09-18 — 되먹임 루프 측정과 정체성 기준선

```text
ADR-036 되먹임 루프:  8회 중 7회가 두 번 고쳐도 점수 불변
                      최초 2.5/7 → 되먹임 2회 후 3.0/7, 완전통과 0/8
                      → 앱에 도입하지 않기로 닫음

정체성 평가 기준선:    6.0/7 (3회 모두 6/7, 편차 없음)
                      I-2(사실과 추정 구분)만 0/3 으로 일관 실패

채점기 회귀:          첫 측정에서 정답 3개를 거짓 실패로 판정 (6/7 을 3/7 로 읽음)
                      판정 순서를 want 우선으로 바꾸고 회귀 테스트 6개 추가
```


## 2026-09-14 — 간헐 실패 제거

어제 넘긴 기준선은 **3회 중 1회 FAIL** 이었다(`test_workspace` 의 undo 백업 테스트).
원인은 Windows 시계 해상도로 백업 폴더 이름이 충돌해 undo 가 직전 백업을 덮어쓴 것이다.
`_next_backup_stamp()` 로 고치고 시계를 고정한 회귀 테스트 2개를 넣었다.

```text
수정 전: 단위 테스트 3회 중 1회 FAILED (failures=1)
수정 후: 단위 테스트 5회 연속 OK (293 tests, skipped=2)
RELEASE VERIFICATION: PASS
```

회귀 테스트가 실제로 버그를 잡는지 확인했다. 수정을 임시로 되돌리면
`test_undo_keeps_both_backups_when_clock_does_not_move` 가 `1 != 2` 로 실패하고,
두 백업이 같은 폴더(`20260101T120000000000Z`)를 가리킨다.


Date: 2026-09-13
Environment: 사용자 RTX 4070 Laptop / Windows, Python 3.11.8
(`C:\Users\<user>\AppData\Local\Programs\Python\Python311\python.exe`)

## Release verification

```text
Unit tests: 281/281 PASS (skipped=2, symlink 권한 없는 환경)
Python 3.10 syntax compatibility: PASS (51 files parsed)
Dependency audit: PASS
Router golden corpus: 29/29 PASS
Security smoke: 11/11 PASS
Alpha E2E smoke: PASS
Beta E2E smoke: PASS
compileall: PASS
RELEASE VERIFICATION: PASS
```

이전 기록(257 tests, container Python 3.13)은 v0.2.0 패키지 시점 값이다. 위 수치는
실제 사용자 장비에서 다시 측정한 값이다.

## 이번에 고친 회귀 (claude)

`storage.remember()` 가 저장할 때마다 `datetime.now()` 를 그대로 썼다. Windows 시계
해상도는 약 15ms 라서 연속 저장이 **같은 `updated_at`** 을 받고, 그러면
`list_memories()` 의 `ORDER BY updated_at DESC` 가 동률이 되어 "가장 최근 메모" 순서가
조용히 삽입 순서로 뒤집혔다.

- 증상: `test_core.MemoryRelevanceTests.test_no_relevance_falls_back_to_recent_memory` 가
  간헐적으로 실패(`['old'] != ['new']`). 타이밍에 따라 통과하기도 해서 잘 드러나지 않았다.
- 영향: 테스트만의 문제가 아니라, 관련 메모가 없을 때 Cloud/Local prompt 에 **가장 오래된**
  메모가 실려 나가는 실제 동작 버그였다.
- 수정: `Storage._next_memory_timestamp()` 를 추가해 동률일 때만 1마이크로초 밀어
  timestamp 를 엄격히 단조 증가시킨다. schema 변경 없음(schema version 4 유지).
- 회귀 테스트: `tests/test_storage.py::MemoryRecencyOrderingTests` 3개 추가.
  고치기 전 해당 테스트는 재현 가능하게 실패했고, 고친 뒤 8회 반복 실행 모두 통과했다.

Unit tests are run with:

```bash
python -W error::ResourceWarning -m unittest discover -s tests -v
```

따라서 SQLite connection leak 같은 ResourceWarning도 회귀 시 실패한다.

## Beta smoke가 추가로 확인하는 것

- Task persistence
- app state persistence
- Workspace edit proposal이 비파괴적임
- 승인된 edit 적용
- edit undo
- portable Memory import
- portable Task import

## 아직 검증하지 못한 것

이 환경에는 사용자 RTX GPU/Ollama가 없으므로 다음은 미검증이다.

- 실제 Ollama generation 품질/속도
- RTX 4070 8GB VRAM peak
- RTX 5080 16GB VRAM peak
- Windows/WSL 환경별 latency
- 실제 OpenAI/Anthropic/Gemini API key를 사용한 live 호출

이 값은 `HARDWARE_VALIDATION.md`에서 사용자가/Claude가 실제 장비로 기록한다.
