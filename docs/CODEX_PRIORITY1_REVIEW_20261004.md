# 우선순위 1 구현 재검토 — Claude 회귀 검사 판정 보완

> 후속 확인 2026-10-04 13:12 KST: **아래 R1~R3는 수정 완료 및 재검증 통과.** [최종 반영본 리뷰](CODEX_R123_FINAL_REVIEW_20261004.md)를 먼저 볼 것. 아래 내용은 수정 전 발견 기록으로 보존한다.


검토: Codex, 2026-10-04 09:50 KST. 판정: **핵심 수정은 반영됐지만, 아래 3항목을 보완해야 완료로 볼 수 있다.** 이번 작업은 검토·별도 CPU 재현·문서 인계이며 실행 코드는 고치지 않았다.

## 검토 결과 요약

- 시간 초과·실행 불가·출력 제한을 `unverified`로 끝내고 추가 수정/독립 풀이를 막는 경로는 정상 확인했다.
- 전체 검사 취소 시 중단, 최종 제안을 만드는 사이 취소 시 폐기, 기존 실패 비교의 헤더 수 검사도 반영되어 있다.
- 덜어내기 변경을 실제 내용으로 판단하고 재검사가 미검증이면 직전 검증된 바이트로 되돌리는 동작도 확인했다.
- **기존 관련 11개 PASS. 추가 수용 검사 6개 중 2개 PASS·4개 FAIL.** 추가 4건은 아래 R1·R2 각 1건과 R3의 2건이다. 실제 GPU나 모델을 호출하지 않았다.
- Claude의 전체 검증 로그 `logs/claude-verdict-verify-20261004.txt`에는 961 tests, RELEASE PASS가 있다. Codex가 전체 검증을 다시 실행한 것은 아니다. 기존 검사에 없는 경계 상황이므로 그 PASS와 이번 발견은 모순되지 않는다.

## R1 — [P1] 서로 다른 subTest 실패가 같은 ID로 합쳐져 새 회귀를 놓침

위치: `jarvis/developer.py:57`의 `_FAILED_TEST`, 90~94행 `_suite_report`, 1106행 근처 `_regressions`의 집합 차이.

정규식이 첫 `)`까지만 잡으므로 아래 두 실패를 같은 것으로 취급한다.

```text
수정 전: FAIL: test_cases (test_guard.Guard.test_cases) (case=0)
수정 후: FAIL: test_cases (test_guard.Guard.test_cases) (case=1)
파싱 ID: test_cases (test_guard.Guard.test_cases)
```

임시 프로젝트에서 실제 unittest 실행기로 재현했다. 후보는 기존 실패 `(case=0)`을 고치는 대신 원래 통과하던 `(case=1)`을 깨뜨린다. 헤더 개수와 failures 개수는 맞으므로 완전한 목록으로 오인한다. `_regressions`는 새 실패 없음으로 반환하고, `/dev-auto`는 **`awaiting_approval`, `copy_passed`, `verification_status=verified`**로 끝났다.

이것은 “같은 테스트 ID의 실패 원인 변화는 비교하지 않음”이라는 알려진 한계와 다르다. unittest 하위 테스트는 인자까지 포함해 서로 다른 ID인데 판독 과정에서 그 부분을 버린 것이다.

보완 기준:

1. 비교 ID에 하위 테스트의 인자/설명까지 보존한다. 헤더 전체를 안전하게 식별하거나, 지원하지 않는 subtest 헤더이면 `unreadable → unverified`로 처리한다.
2. 본문에서 임의로 만든 문자열이 아닌 unittest 실패 헤더를 기준으로 하고, 기존 헤더 수/완료 요약 일치 검사는 유지한다.
3. `(case=0)`에서 `(case=1)`로 실패가 이동하는 합성 테스트에서 최종 승인 대기가 생기지 않아야 한다. 같은 하위 테스트가 계속 실패하는 경우와 구분한다.

재현 검사: `AdditionalAcceptanceTests.test_new_failing_subtest_cannot_alias_old_subtest`.

## R2 — [P2] unexpected success를 실패 비교에서 버림

위치: `jarvis/developer.py:90`의 `expected = failures + errors`, 91행의 FAIL/ERROR 전용 헤더 수집.

`@unittest.expectedFailure` 테스트가 예상과 달리 통과하면 unittest는 `UNEXPECTED SUCCESS:`와 `FAILED (..., unexpected successes=1)`을 출력하고 종료 코드 1을 반환한다. 현재 판독기는 이 항목을 비교 목록에서 제외한다.

기존부터 실패하던 `test_old`와 새 unexpected success가 같이 있는 임시 프로젝트로 재현했다. 실제 후보 suite는 **종료 코드 1, `FAILED (failures=1, unexpected successes=1)`**인데, `test_old`만 남아 새 실패 없음으로 분류되고 최종 제안이 나왔다. 검사의 기대값을 고칠 필요가 있는 상황일 수 있지만, 현재 gate가 검증 완료로 승인할 근거는 없다.

보완 기준:

- `unexpected successes`와 `UNEXPECTED SUCCESS` ID도 비교하거나, 해당 유형을 지원하기 전에는 명시적으로 미검증으로 처리한다.
- 알 수 없는 최종 실패 요약 항목을 무시해서 `failed + empty set`으로 만들지 않는다. 정상 `expected failures`/`skipped`와는 구분한다.
- 정상 원본과 실패 중인 원본 양쪽에서 검사하고, 새 unexpected success가 추가된 후보는 최종 통과시키지 않는다.

재현 검사: `AdditionalAcceptanceTests.test_new_unexpected_success_cannot_be_ignored`.

## R3 — [P2] 수정 없는 완료 분기의 취소 확인과 취소 결과 기록 누락

위치: `jarvis/developer.py:1022`의 `if not specs`가 1029행 취소 확인보다 앞에 있음. 취소 분기들의 `_verification` 갱신도 빠져 있음(920·997·1029·1041행 근처).

### R3a. 수정이 없을 때의 취소

선택 테스트가 처음부터 통과하는 작업에서, 덜어내기 뒤 최종 실제 테스트 fingerprint 검사 시점에 취소 event를 설정했다. 이는 외부 사용자 취소를 결정적인 타이밍에 주입한 대역이다. 이미 event가 설정되었는데도 `if not specs`가 먼저 반환해 **`phase=passed`, `result=already_passing`**으로 끝났다. 실제 파일이나 제안이 바뀌는 문제는 아니지만 요청한 취소가 성공으로 표시된다.

보완: 최종 완료 경계의 취소 확인을 `if not specs`보다 앞으로 옮기고, 수정이 있는 경우의 생성 전/후 확인은 유지한다. 취소면 `needs_test`, `stopped`로 끝내며 완료 메시지를 반환하지 않는다.

재현 검사: `AdditionalAcceptanceTests.test_cancel_before_no_change_completion_stops`.

### R3b. 제안 폐기는 되지만 기록은 verified

최종 제안 생성 직후 취소시키면 제안은 정상 폐기되고 `result=stopped`가 된다. 그러나 앞선 성공 값이 남아 **`verification_status=verified`**가 함께 저장된다. 전체 검사 중 취소는 반대로 이 필드가 아예 없을 수 있다. 취소를 네 가지 결과 상태 중 하나로 정한 계약이 기록까지 일관되게 적용되지 않았다.

보완: 공통 취소 처리에서 `verification_status='cancelled'`, 검사 범위와 취소 원인을 저장한다. 취소 전에 검사 통과가 있었다는 정보는 이전 회차 또는 별도 필드에 유지한다. 최종 작업 상태와 직전 검사 결과를 같은 필드에 섞지 않는다. 이 건에서 제안 폐기 자체는 제대로 작동했으므로 폐기 실패라고 보고하지 않는다.

재현 검사: `AdditionalAcceptanceTests.test_cancelled_final_proposal_records_cancelled_verification`.

## 재현 자료와 실행 방법

리뷰 폴더: `logs/codex-priority1-verification-20261004/`.

- `snapshot/`: 읽기용으로 복사한 Python 소스와 기존 developer 테스트. Claude의 우선순위 2 작업과 분리했다.
- `source-hashes.json`: 09:47:59 검토 스냅샷의 파일 해시.
- `check_review.py`: 기존 관련 검사 11개와 추가 수용 검사 6개. 합성 프로젝트와 모델 응답 대역을 사용한다. R1/R2의 unittest 실행은 실제 CPU 하위 프로세스다.
- `existing-tests.txt`, `additional-tests.txt`: 원문 검사 결과.
- `results.json`: 실패 헤더·전체 출력·파싱 ID·최종 phase·결과 기록.

```powershell
py -3.11 -B -X utf8 logs/codex-priority1-verification-20261004/check_review.py
```

현재 고정 사본에서는 의도한 수용 기준 4개가 실패하므로 종료 코드 1이 예상된다. 이 스크립트는 **snapshot을 import한다**. 다음 구현을 검증할 때는 원래 리뷰 사본을 덮어쓰지 말고, 새 검증 폴더/구현 복사본에서 같은 수용 검사를 실행해야 한다. 고정된 구버전 검사 결과를 새 수정의 실패로 오인하지 말 것.

이번 source SHA256:

- `jarvis/developer.py`: `ba38e037467ce4bd119d2889bd95d5bb37744c04d3be8f4f702f2918f64b9459`.
- `tests/test_developer.py`: `41f3fa0cac44e5d66ea2e1258de20e22496eeb65c36a5eee4bae67951bc4844d`.

09:50 재확인 때 두 파일은 실제 작업 폴더와 해시가 일치했다. 이후 Claude 작업이 진행되면 행 번호보다 함수 이름과 위 해시를 기준으로 대조한다.

## Claude Code / Sol에 전달할 보완 요청

```text
docs/CODEX_PRIORITY1_REVIEW_20261004.md를 읽고 우선순위 1의 남은 R1~R3를 보완해라.
기존 timeout/취소/미검증 중단과 trim 복원은 통과했으므로 되돌리지 마라.
subTest의 전체 ID와 unexpected success를 비교에 포함하거나 보수적으로 미검증 처리해라.
수정 없는 완료보다 먼저 취소를 확인하고, 모든 취소의 최종 verification 상태를 일관되게 기록해라.
logs/codex-priority1-verification-20261004/check_review.py의 추가 수용 기준을
새 구현 복사본의 테스트로 옮겨 검증해라. 원래 리뷰 snapshot과 증거는 보존해라.
현재 다른 구현/평가와 겹치는 소스는 최신 상태를 먼저 조율하고 쓰기 전 백업·재읽기해라.
GPU/Ollama/얼린 평가 복사본/실행 중 평가/실사용 설정은 건드리지 마라.
CPU 결과와 diff, 미검증 범위를 기록하고 GPT_NEXT.md·CLAUDE_NEXT.md에 인계해라.
```
