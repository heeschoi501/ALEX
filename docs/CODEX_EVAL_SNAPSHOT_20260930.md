# ALEX 평가 결과 읽기 전용 스냅샷 — 2026-09-30

## 진행 중 train 결과의 사전 분석

11:26 관측은 35개 계획 중 29행이며 strict 25개 성공, 선택 검사 미해결 3개, 선택 검사 통과 후 새 전체 검사 실패 기록 1개다. 아래는 기록된 실패 유형이고 최종 결과나 원인 확정이 아니다.

| 과제 | 관측 | 해석/후속 확인 |
|---|---|---|
| mut_app_change_operator_683_d326 | 선택 통과, suite_exit=1, test_mutations_reject_missing_token 신규 실패 | 전체 회귀 결과를 교훈과 결합해야 함. 수정 유발인지 환경/간헐 실패인지는 재현 전 미확정 |
| mut_core_swap_operands_1679_cdc7 | 4회차, 502.7초, 후반 두 후보의 변경 블록 없음 | 형식 실패가 해결 예산을 소모. CodeT 후보 수만 늘리기보다 형식·호출 비용을 별도 기록 |
| mut_core_change_operator_535_7b4f | 4회차, 1,276.6초, 찾을 코드 불일치·문법 오류 | 편집 위치/문맥과 형식 문제를 구분. 모델 크기만 원인으로 단정하지 않음 |
| mut_remote_change_operator_151_ebe8 | 4회차, 1,094.1초, 두 수정 후 여전히 실패·마지막 변경 없음 | 잘못된 수정을 반복하는 양상. 독립 후보 비교의 개발용 사례 후보 |

app 과제의 outcomes JSONL에는 auto(copy_passed), applied, tests(exit=0)만 있고 suite 이벤트는 없다. 실제 row의 전체 검사 실패가 교훈 입력에 누락될 수 있다. **실험 중인 결과/교훈 파일을 수정하지 않았으며** 후속 분석 때 이 사례의 최신 상태를 확인해야 한다.

## 도구 사용과 검증

- 기본은 중복 task ID를 오류로 거부한다. 과거 기준선만 의도적으로 `--allow-duplicate-observations`를 지정했다. 행을 제거/합치거나 동일 이름을 일대일 대응시키지 않는다.
- 종료 코드 0은 파일 분석 완료, 2는 읽기/자료형/중복/불안정 입력 오류다. 모델 평가가 실패했다는 뜻으로 도구 종료 코드를 사용하지 않는다.
- 입력 JSON 목록만 지원한다. 결과 파일/모델 호출/앱 import/하위 프로세스를 만들지 않는다. stdout 저장은 호출자가 필요할 때 수행한다.
- `--expected`는 각 파일의 계획 개수다. 계획이 다른 train/test 파일을 한 번에 넣고 동일 expected를 쓰지 않는다. 지정하지 않으면 분모를 `?`로 표시한다.
- 합성 JSON으로 7가지 상태, 자료형/중복 기본 거부, 중복 허용 경고, 부분/누락/빈 파일, 계획 개수 초과, Markdown escaping, SHA-256, 입력 불변을 검증했다. 기존 baseline/train JSON도 읽었다.
- 검증 기록: [codex-compare-validation-20260930.txt](../logs/probe/codex-compare-validation-20260930.txt). ALEX 전체 테스트·모델 검증은 수행하지 않았다.

## 원본 JSON 집계

Codex가 기존 JSON을 읽어 생성한 표다. 모델·ALEX 테스트를 실행하지 않았다. train과 test는 다른 과제이므로 두 성공률로 학습 효과를 비교하지 않는다. train은 진행 중 일부 기록이며 이후 갱신될 수 있다. rows/planned는 관측 행 수이고 정상 종료 확인이 아니다.

엄격 성공 = bug_detected=true + acceptance_passed=true + 정수 suite_exit=0 + 명시적 new_failures=[]. Not solved는 검사 실패와 운영상 중단을 포함할 수 있다. Unverified는 전체 검사/결과 증거 부족이다. Exact solved는 엄격 성공 중 원본 동일 복구 수다. 초 합계는 JSON 과제별 시간으로 전체 실행 벽시계와 다르다.

**기준선 ID 주의:** 과거 결과 JSON의 mut_device_change_constant_25는 두 행에서 반복된다. 현재 mutation_tasks.json에는 그 baseline_id에 대응하는 mut_device_change_constant_25_eaad, mut_device_change_constant_25_ac3d가 있다. 15/16은 중복을 제거하지 않은 결과 행 기준이며 고유 ID는 15개다. 과제별 전후 비교에는 각 행의 실제 변이 대응을 추가 확인해야 한다. 이 보고서에서는 --allow-duplicate-observations를 명시하여 모든 행을 보존했다. 도구의 기본 동작은 중복 ID 거부다.

11:17 Claude 수정이 있었고 harness가 과제마다 live ROOT를 복사하므로 동일 파일 전체가 동일 소스 조건이라는 보장은 없다. 소스/설정 지문이 없는 결과에서 기능 효과나 공정한 모델 간 승패를 확정하지 않는다.

도구: logs/probe/compare_eval_results.py (표준 라이브러리, 입력 읽기·stdout 출력만).
재생성: python -B -X utf8 logs/probe/compare_eval_results.py --expected 16 --allow-duplicate-observations logs/coding-agent-eval-mut-test-qwen3.8_27b.json

생성 시각: 2026-09-30 11:26:33 +09:00

# Coding evaluation snapshots

Read at: 2026-09-30T02:26:32+00:00

Rows are observations, not proof of run completion. Denominator = bug-detected rows.
Missing evidence is not a pass. Not-solved can include operational stops.
Input files do not establish identical source/settings/test versions; causal comparison is unverified.
Seconds are recorded per-task time, not whole-run elapsed time; timing coverage is shown.

| File | Model | Split | Rows/planned | Unique IDs | Strict solved/detected rows | Not solved | Regression | Unverified | Skipped | Exact solved | Sum s | Median s | Timed rows |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coding-agent-eval-mut-test-qwen3.8_27b.json | qwen3.8:27b | test | 16/16 | 15 | 15/16 (93.8%) | 1 | 0 | 0 | 0 | 11 | 2128.2 | 119.5 | 16/16 |

WARNING: coding-agent-eval-mut-test-qwen3.8_27b.json: duplicate IDs retained as observations {'mut_device_change_constant_25': 2}; task identity is ambiguous, no paired comparison.

Input SHA-256 (content identity only, not experimental comparability):
- C:\ALEX\jarvis\logs\coding-agent-eval-mut-test-qwen3.8_27b.json: `1d01a1d12cb66df5430e027c7871f781673dc36c01223421bf1813efbb8b0588`

# Coding evaluation snapshots

Read at: 2026-09-30T02:26:33+00:00

Rows are observations, not proof of run completion. Denominator = bug-detected rows.
Missing evidence is not a pass. Not-solved can include operational stops.
Input files do not establish identical source/settings/test versions; causal comparison is unverified.
Seconds are recorded per-task time, not whole-run elapsed time; timing coverage is shown.

| File | Model | Split | Rows/planned | Unique IDs | Strict solved/detected rows | Not solved | Regression | Unverified | Skipped | Exact solved | Sum s | Median s | Timed rows |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coding-agent-eval-mut-train-qwen3.8_27b.json | qwen3.8:27b | train | 29/35 | 29 | 25/29 (86.2%) | 3 | 1 | 0 | 0 | 19 | 7805.7 | 157.0 | 29/29 |

Input SHA-256 (content identity only, not experimental comparability):
- C:\ALEX\jarvis\logs\coding-agent-eval-mut-train-qwen3.8_27b.json: `37658fdf1d753bd544c4566760eb947c5f354a04108b96c7ebe24ccad016c522`
