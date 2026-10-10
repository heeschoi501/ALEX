# ALEX 코드 리뷰 — 2026-09-30, Codex

## 범위와 확인 수준

사용자 요청: Claude GPU 실험에 영향을 주지 않는 정적 리뷰와 설계. 대상은 오늘 추가한 dev-outcomes, /dev-check, insights, fact reminder, /unload, mutation_tasks.py 및 관련 평가 집계다.

이 문서는 이전 대화의 리뷰를 파일로 옮기면서 **11:17 Claude 수정 이후 소스를 다시 읽은 상태**를 반영한다. 코드의 행 번호보다 함수 이름을 기준으로 찾을 것. ALEX 코드·설정·기존 테스트·실행 중 셸을 수정하지 않았으며, 모델·ALEX 테스트·서버 재시작은 실행하지 않았다. 후속 변경이 있을 수 있으므로 구현 전에 최신 소스를 다시 읽는다.

분류: P1은 검증 증거의 잘못된 승계, P2는 특정 조건에서의 오류/평가 신뢰성 문제다. 아래 재현 항목은 **앞으로 실행할 검사 명세**이지 이번에 실행한 테스트가 아니다.

## 아직 남은 발견 사항

### R01 · P1 · 새 수정안에 이전 검증 결과가 승계됨

- 근거: `jarvis/developer.py`의 `outcome_status`(약 170행), `approved`, `_refresh_passed_result`.
- `outcome_status`는 마지막 suite가 PASS인지와 과거 applied 이벤트 존재를 본다. 새 수정안 적용은 DeveloperRun.suite_result를 초기화하지만 이전 outcomes의 PASS를 무효화하지 않는다.
- 발생 순서: 같은 run의 선택 검사·전체 검사 통과 → 새 제안 생성·적용 → 재검사 전 outcomes 조회. 실제 상태는 재검증 대기인데 이전 PASS로 사용자 채택이 표시될 수 있다.
- 수정 방향: 이벤트에 source revision/digest와 proposal ID를 연결하고 apply·외부 변경·discard 등 이후 검증 증거의 유효 범위를 정의한다. 단순히 과거 applied가 있었다는 사실을 현재 버전의 검증과 결합하지 않는다.
- 후속 검사: 위 순서 및 외부 소스 변경 뒤 조회에서 미검증이어야 하며, 해당 버전의 선택·전체 검사를 다시 통과해야 상태가 올라가야 한다.

### R02 · P1 · 검사 중 소스 변경으로 무효화된 exit=0이 남음

- 근거: `developer.py`의 `next`, tests 이벤트 기록 약 635행과 그 뒤 `_source_changed`.
- tests 이벤트는 먼저 기록되고 그 뒤 소스 변경이 확인된다. DeveloperRun은 무효화되어도 결과 기록의 종료 코드 0은 남는다. `_refresh_passed_result`에서도 이벤트 무효화가 없다.
- 수정 방향: 검사 전후 지문을 기록하고 일치 시에만 검증 성공을 확정하거나 별도 invalidated 이벤트를 남긴다. 중단·실행 오류와 검증 성공을 다른 상태로 둔다.
- 후속 검사: 실행 중 대상 파일이 바뀌면서 runner가 0을 반환하는 경우 `outcome_status`가 통과를 반환하지 않아야 한다.

### R03 · P2 · 변이 생성에서 AST 바이트 위치를 문자 위치로 사용

- 근거: `logs/probe/mutation_tasks.py`의 `_segment`와 `candidates` 문자열 슬라이스(약 41, 71~98행).
- Python AST의 col_offset/end_col_offset은 UTF-8 바이트 위치다. Python str 슬라이스는 문자 위치여서 같은 줄의 한글 뒤에 있는 비교식·정수·any/all 치환 위치가 어긋날 수 있다.
- 영향: 후보 누락, 다른 위치 변형, 문법 오류로 후보 편향. 현재 51개 전부가 잘못됐다는 주장은 아니다.
- 수정 방향: UTF-8 바이트 단위 편집 후 디코딩하거나 명시적인 위치 변환. 소스 추출은 `ast.get_source_segment` 활용 가능하나 치환 위치도 함께 고쳐야 한다.
- 후속 검사 예: `return "한글" if x > 1 else ""`와 같은 줄의 비교식, 한글 식별자, ASCII 대조군에서 의도한 노드만 변하는지 확인.
- 근거 문서: [Python AST 위치](https://docs.python.org/3/library/ast.html#ast.AST.col_offset).

### R04 · P2 · max_seconds가 hard 조합·다음 모듈 검사에 적용되지 않음

- 근거: mutation_tasks.py main의 단일 후보 루프(약 163행), hard 조합 루프(187행), 모듈 baseline(152행).
- 단일 후보 루프 밖에서는 전체 deadline을 확인하지 않는다. hard 조합 검사가 계속되거나 시간이 지난 뒤 다음 모듈의 baseline 검사를 시작할 수 있다.
- 수정 방향: 모든 새 검사 시작 전 deadline 확인, 개별 timeout=min(기존 제한, 남은 시간), 시간 초과 시 중간 과제와 중단 이유 보존. 실행 중 스크립트에는 적용하지 않는다.
- 후속 검사: 가짜 시계와 runner로 deadline 이후 호출 0회, 실행 중 timeout 및 원본 복구 보장. 실제 장시간 실행은 필요 없다.

### R05 · P2 · 교훈에 최종 회귀 검증 결과가 결합되지 않음

- 근거: `developer.py:learn`, `logs/probe/learn_insights.py`는 auto(copy_passed/not_passed)를 추출한다. `coding_agent_eval.py`의 전체 suite 결과는 row에 저장되지만 자동 수정 이벤트와 결합되지 않는다.
- `copy_passed`는 선택한 테스트 통과다. 전체 회귀에서 실패하거나 적용되지 않은 후보를 검증된 최종 성공과 구분할 자료가 교훈 프롬프트에 없다.
- 수정 방향: 같은 run/소스 버전/후보에 해당하는 검사·승인 결과를 결합하고 `selected_tests_passed`, `full_suite_passed`, `regression`, `not_applied`, `unverified`를 구분한다. 실패도 학습 자료로 쓰되 성공 증거로 표현하지 않는다.
- 후속 검사: 선택 검사 통과·전체 검사 실패 궤적에 실패 표시가 들어가는지, 다른 revision의 suite 결과가 붙지 않는지.
- 관측 사례(11:26 스냅샷): train의 `mut_app_change_operator_683_d326`는 acceptance=true이나 suite_exit=1이고 새 실패 하나가 있다. 대응 `logs/coding-agent-outcomes-train.jsonl`에는 auto(copy_passed), applied, tests(exit=0)만 있으며 suite 이벤트는 없다. 전체 검사 실패의 원인/재현성은 미확인이나 교훈 입력이 그 결과를 보지 못한다는 사례다.

### R06 · P2 · insights JSON의 컨테이너·필드 자료형 검사 부족

- 근거: `jarvis/insights.py`의 `InsightStore.load`, `learned`, `prompt_block`.
- `insights:null` 또는 `learned:null`은 순회에서 예외가 난다. dataclass는 타입 힌트를 강제하지 않아 문자열 count 등이 들어오면 정렬·연산에서 실패할 수 있다.
- 수정 방향: list/dict 및 id/text/count/sources 자료형·범위 검증, 잘못된 항목의 진단, 읽기 실패와 빈 정상 저장소 구분. 손상 파일을 빈 결과로 자동 덮어쓰지 않는다.
- 후속 검사: null·문자열·배열 아닌 컨테이너·중복 ID·잘못된 count 및 정상 자료 혼합.

## Claude가 반영한 항목 — 중복 수정 금지

| 원래 발견 사항 | 11:17 이후 확인한 코드 | 확인 수준 |
|---|---|---|
| fact reminder가 문맥 축약 시 앞쪽으로 이동 | `core._fit_context`가 reminder 공간을 확보하고 마지막 user 앞에 재삽입 | 소스와 추가 테스트를 읽음. Codex 재실행 없음 |
| trajectory_text의 앞부분 절단으로 최종 회차 손실 | `insights.trajectory_text`가 뒤쪽 회차 우선·초기 회차 요약 방식으로 변경 | 소스 확인. 최종 diff 전체가 보존된다는 뜻은 아님(기존 700자 제한 존재) |
| 평가 성공 집계에서 suite_exit 누락 | coding_agent_eval의 solved 조건에 bug_detected·acceptance_passed·suite_exit==0·new_failures==[] 포함 | 소스 확인, 기존 baseline JSON 엄격 재집계 15/16 |

`CHANGELOG.md` 2026-09-30 11:17 절은 Claude의 811 PASS를 기록한다. 기존 `logs/claude-codexfix-verify-20260930.txt`의 RELEASE VERIFICATION: PASS도 읽었다. **이는 Claude의 검증 기록이며 Codex가 테스트를 실행했다는 뜻이 아니다.**

## 추가 위험·개선점

- `/dev-check`: `check`는 현재 workspace에서 실행한 결과를 저장된 DeveloperRun에 연결하면서 workspace 동일성을 확인하지 않는다. 프로젝트 전환 후 잘못된 작업에 붙는 상황을 확인할 것. project/check 설정 지문과 실행 결과를 함께 기록하면 추적이 쉽다. argv 실행·timeout·출력 제한·alex-checks.json 편집 보호는 이미 존재한다.
- `/unload`: keep_alive=0 요청 후 GPU 전체가 비었다고 안내한다. Ollama 모델 해제 요청, `/api/ps` 확인, 다른 프로그램의 GPU 점유는 서로 다르다. 부분 성공·재적재도 구분하는 문구가 필요하다. 실제 unload는 GPU 실험 중 실행하지 않았다.
- 변이 분리: ast.walk(function)이 중첩 함수의 본문을 바깥 함수 아래로 포함한다. 실제 소속 함수의 qualified name으로 분리하고 parent/child 경계를 정의해야 한다. split 누출이 현재 데이터에서 발생했다고 단정하지 않는다.
- hard ID는 두 번째 변이의 줄 번호만 사용한다. 다른 변이가 같은 줄에 있으면 충돌 가능하므로 두 변이 전체 ID/해시를 포함한다. 검토 당시 기본 51개·hard 11개에는 중복 ID가 없었다.
- **과거 평가 JSON의 실제 ID 충돌:** 현재 과제 파일과 달리 baseline 결과의 `mut_device_change_constant_25`는 두 행에서 반복된다. 결과는 16행/고유 ID 15개다. 현재 과제 파일은 `baseline_id`를 유지하며 `_eaad`/`_ac3d`로 구분한다. strict 15/16은 행 단위 집계이며 각 행의 실제 변이 대응 확인 전 ID 기반 전후 비교를 하면 안 된다. 비교 도구는 기본 거부, 명시적 `--allow-duplicate-observations`일 때만 경고하며 행을 모두 유지한다.
- 교훈 연산: 잘못된 출력으로 연산이 0개여도 learned로 표시한다. 의도적인 no-op과 파싱 실패를 구분해야 재시도가 가능하다. 반복 UP/DOWNVOTE의 회차별 제한도 검토한다.
- 결과 로그: 회전 이전 파일 및 run별 최신 활동 기준 조회를 검토한다. 기록 시각만으로 후보/revision을 대신하지 않는다.
- 평가 복사본: 현재 harness는 과제마다 live ROOT를 복사한다. Claude의 11:17 수정 전후 과제가 같은 결과 파일에 섞일 수 있으므로, 해당 파일 전체를 동일 소스 버전의 통제 실험이라고 단정하지 않는다. 이번 실행을 중단하거나 스크립트를 수정하지 않고, 다음 실험에서는 고정 snapshot과 해시를 사용한다.

## 다음 작업 순서

1. 현재 GPU 실험 종료·최신 코드 확인. 16시 예상 시각만으로 종료 판단하지 않음.
2. R01/R02의 검증 이벤트·revision 모델을 함께 정리하고 재현 검사부터 작성.
3. R05의 학습 증거 결합, R06의 자료형 검증.
4. 변이 생성 수정은 다음 데이터셋 버전부터 적용하고 기존 결과·과제 ID는 보존.
5. CodeT·모델 추천 구현 전 [CodeT 설계](CODET_DESIGN_20260930.md), [모델 추천 설계](ROUTELLM_DESIGN_20260930.md)의 평가 분리·예산을 확정.

현재 기준선 15/16은 strict 조건의 결과 행 집계에서도 유지된다. 고유 과제 ID가 16개라는 뜻은 아니다. 코드 위험 발견을 현재 성능 결과의 오류나 기능 개선 효과 확인으로 바꾸어 표현하지 않는다.
