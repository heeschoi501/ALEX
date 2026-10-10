# ALEX CodeT 기반 후보 선택 설계 — 2026-09-30

상태: 설계만. GPU 실험 중 구현·모델 호출 없음. 기본 사용자 모델 gpt-oss:120b와 기존 승인 흐름을 유지한다.

## 문제와 실측 근거

[PERFORMANCE 29·30](../PERFORMANCE.md)의 27B f16/16K/54층 실측은 짧은·코드·긴 입력 생성 19.4/34.8/17.3 tok/s, CPU 평균 58%다. 쉬운 변이 16개에서 회귀 없이 15개 해결, 11개 원본 동일 복구, 전체 약 37분이다. JSON seconds 합은 2,128.2초이며 문서의 전체 시간과 측정 범위가 다르다.

쉬운 과제에는 성공률 상한이 가까워 여러 후보의 비용이 더 눈에 띌 수 있다. 목적은 어려운 과제의 최종 선택 정확도 향상이지 후보 수 증가 자체가 아니다. 16개 결과만으로 효과나 예상 속도를 약속하지 않는다.

기존 baseline은 16행 중 ID 하나가 중복되어 고유 ID가 15개다. 15/16은 결과 행 집계다. 후속 비교는 현재 변이 ID와 과거 baseline_id의 실제 대응을 검증한 manifest를 사용하며, 이름만으로 일대일 join하지 않는다. 상세는 [스냅샷](CODEX_EVAL_SNAPSHOT_20260930.md)을 따른다.

[CodeT 원 논문](https://arxiv.org/abs/2207.10397)은 코드 후보와 생성 테스트를 실행하고 테스트·후보의 일치성을 이용해 선택한다. 아래는 ALEX의 수정안·회귀 검사·승인 구조에 맞춘 제한된 변형이며 원 논문의 완전 재현은 아니다.

## 진입점과 인터페이스 제안

- 기존 `DeveloperMode.auto/_auto`와 별도의 후보 선택 경로를 둔다. 기존 동작은 그대로 기본값이다. UI/명령명은 아직 확정하지 않는다.
- 제안 설정: candidate_mode=off, candidate_count=2, max_candidate_count=3, generated_test_limit=8. 시간/토큰 총예산은 기존 단일 실행 예산과 비교해 개발셋에서 정하고 최종 평가 전에 고정한다. 이 값들은 측정된 최적값이 아니다.
- `CandidateRequest`: run_id, baseline_digest, source_paths, acceptance_tests, goal, model_id, budget, cancellation.
- `CandidateEvidence`: candidate_id, parent_digest, patch_digest, generation_attempts, actual_model, test_matrix, acceptance_result, suite_result, timings, rejection_reason.
- `SelectionResult`: selected_id 또는 none, score_components, trusted_checks, selection_reason, proposal_id.
- `core.propose_bundle_local(..., editor=..., record=False)`, `DeveloperMode._check_bundle_scope`, `SafeWorkspaceEditor`, 기존 bounded runner와 취소 처리를 재사용한다. 앱 공용 provider의 model 값을 잠시 바꾸는 방식은 피한다.

## 처리 흐름

1. 대상 소스·합격 기준·관련 설정을 고정하고 baseline 지문을 만든다. 실행 중 다른 작업의 상태를 읽어 와 후보에 섞지 않는다.
2. 동일 baseline에서 독립 복사본 A/B를 만든다. 각 후보는 원래 실패·목표를 받고 다른 진단 관점으로 생성한다. B가 A의 변경을 이어받지 않는다. 같은 patch hash는 한 후보로 처리한다.
3. 생성된 bundle을 기존 경로·범위·보호 파일 검사로 검증한다. 테스트 자체나 alex-checks.json을 후보가 고치지 못하게 한다. 형식 실패·생각 한도 재시도도 후보별 호출 수에 포함한다.
4. 각 후보의 기존 합격 기준을 먼저 실행한다. 원본 테스트 통과 여부를 생성 테스트로 덮어쓰지 않는다.
5. 목표·API 계약·허용된 테스트 예시로 생성한 추가 검사 집합을 통과 후보 모두에게 동일하게 적용한다. 후보별로 자기에게 유리한 검사만 생성하지 않는다.
6. 후보×테스트 결과 행렬로 우선순위를 정한다. 첫 순위에 전체 회귀를 실행하고 실패하면 남은 예산에서 다음 후보를 확인한다. 통과 후보가 없거나 실행 미완료면 제안하지 않는다.
7. 선택 후보에 기존 `_trim`을 적용한다면 선택·생성·전체 검사를 다시 통과해야 한다. 검증 후 diff가 바뀌면 이전 증거를 승계하지 않는다. 초기 실험에서는 후보 비교 뒤 추가 trim을 생략하는 것도 허용하되 비교군 설정을 기록한다.
8. 실제 소스가 baseline과 여전히 일치할 때만 `create_bundle(expected_originals=...)`로 최종 제안 하나를 만든다. 실제 적용은 기존 사용자 승인 단계다. 적용 뒤에는 새 revision의 검증이 필요하다.

## 생성 테스트의 신뢰 경계

- 초기 범위는 순수 함수의 입력/기대값·경계값·속성 검사로 제한한다. 임의 Python/셸 코드를 무제한 실행시키지 않는다. 허용된 테스트 자료 형식을 고정된 harness가 해석한다.
- 별도 프로세스 timeout·출력 제한·복사본은 자원/원본 보호 수단이다. OS 보안 sandbox와 같다고 주장하지 않는다. 파일·네트워크·프로세스 부작용이 필요한 테스트 생성은 초기 범위 밖이다.
- 생성기는 benchmark 정답 원본, 원본 동일 복구 여부, hidden 평가 결과를 보지 않는다. 깨진 baseline의 출력으로 기대값을 만들지도 않는다.
- 검사 유효성 필터: 파싱/스키마 정상, 허용된 대상, 중복 제거, 명세와 충돌하지 않는 기대값, 짧고 결정적인 실행. 실행 오류는 false가 아니라 invalid/incomplete로 분리한다.
- 비교 점수는 유효 검사 통과 비율과 동일 출력/통과 패턴의 후보 간 지지도를 함께 기록한다. 정확한 가중치는 개발셋에서 정하고 고정한다. 공통 버그가 있는 후보의 다수결은 정답 증거가 아니다.
- 유효 검사가 없거나 변별력이 없으면 기존 검사·최소 변경량 기반 선택으로 명시적으로 축소한다. 생성 검사가 유일한 이유로 후보를 성공 처리하지 않는다.

## 실행 예산과 실패 처리

- 단일 GPU에서 후보 생성은 순차 실행한다. 2배 후보가 정확히 2배 시간이라는 가정은 하지 않는다. 입력 처리·모델 적재·재시도·전체 검사 시간도 기록한다.
- 총 deadline과 모델 호출/토큰 한도를 함께 적용한다. `_generate_local_edit`의 provider fallback, no-think 재시도, 변경 블록 재요청을 모두 실제 호출로 센다.
- 남은 예산보다 긴 새 단계를 시작하지 않는다. 취소는 모든 후속 후보/검사를 중지하고 실제 프로젝트는 변경하지 않는다.
- 상태 제안: generated → scope_valid → acceptance_passed → ranked → suite_passed → proposed → applied → reverified. source_changed/cancelled/timeout은 성공과 분리한다.
- 기록은 run_id/candidate_id/proposal_id/source/test/config 지문을 연결한다. R01/R02를 먼저 해결해야 이벤트만 보고 올바른 학습·선택을 할 수 있다.

## 실험 설계와 채택 기준

| 비교군 | 목적 |
|---|---|
| 기존 순차 자동 수정 | 현재 기준선 |
| 독립 후보 2개 + 기존 검사 | 후보 증가 효과 분리 |
| 독립 후보 2개 + 생성 검사 + 전체 회귀 | 테스트 기반 선택의 추가 효과 |

- 동일 소스·데이터셋·모델 태그/지문·KV·문맥·GPU 층·think·총예산으로 비교한다. 여러 모델을 섞는 실험은 이후 별도 수행한다.
- 쉬운 16개는 회귀 점검용. hard/실패 과제를 함수 단위로 개발·최종 평가 분리하고, 조정에 사용한 hard 과제를 최종 미사용 평가라고 부르지 않는다.
- hidden 검사는 최종 선택 이후 평가만 한다. 그 결과로 선택 규칙이나 후보를 바꿔 같은 평가를 반복하지 않는다.
- 지표: 최종 선택 strict 성공률, oracle-any-candidate 성공률(별도), 새 회귀, 형식 실패, total/model/test 시간, 호출/토큰, 성공당 시간, 승인 후 재검증 결과.
- 최종 평가는 pass@k와 선택된 pass@1을 구분한다. 생성 검사만 추가해 늘어난 성공 주장과 실제 전체 검사 성공을 구분한다.
- 먼저 작은 개발 실험으로 비용과 실패 유형을 확인한다. 품질 이득·허용 가능한 추가 지연·신규 회귀 없음이 독립 평가에서 확인되기 전 기본값을 켜지 않는다. 수치 채택 문턱은 사용자의 시간 선호와 함께 사전 확정한다.

## 이후 구현 순서

검증 이벤트 revision 정리 → 고정 snapshot/manifest → 모델 없는 가짜 후보·검사로 상태/취소 검증 → 후보 2개 실험 → 생성 검사 추가 → 독립 평가 → opt-in 기능 판단. 이 문서는 현재 구현이나 실험 시작 지시가 아니다.
