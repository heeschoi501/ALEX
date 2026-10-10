# ALEX Strata 평가 인수 및 결과 검토 — 2026-10-10

갱신: 2026-10-10T12:32:30 KST. 사용자 요청으로 Claude의 사용량 소진 뒤 Codex가 기존 평가와 후속 수정 작업을 인수했다.

## 결과 상태

**유효 과제 결과 저장 완료: 유효 6개 중 6개 저장, 엄격 통과 6/6.** 나머지 원래 과제 5개는 코드 변경으로 변이 정의가 맞지 않아 시험할 수 없다. 따라서 11개 전체 성공률로 보고하지 않는다.

**추가 계약 검사에서 거절한 후보: 3개.** 기존 엄격 통과 숫자는 원본 평가 기록이며 의미 검토의 최종 합격 숫자가 아니다. 개발 상태 복구·미디어 종료·영상 라우팅 후보의 보호 누락은 아래에 별도 기록했다.

엄격 통과 조건: 변이의 오류 감지, 합격 검사 통과, 전체 검사 종료 코드 0, 새 실패 목록이 명시적으로 비어 있음. 테스트 통과와 의미상 올바른 수정은 구별한다.

이 6개 결과는 추가 계약 검사와 구동 수정 **반영 전** 소스/테스트 스냅샷에서 측정한 기록이다. 강화된 새 시험 기준에서 모델을 재평가한 결과는 아니다.

| 과제 | 엄격 판정 | 검사 회차 | 과제 소요 초 | 원본과 동일 |
| --- | --- | ---: | ---: | --- |
| 묶음 적용 뒤 개발 상태 갱신 | 통과 | 3 | 809.0 | 같음 |
| 모델별 GPU 층 설정 | 통과 | 2 | 343.4 | 다름 |
| 작업 기록·교훈 명령 연결 | 통과 | 2 | 901.9 | 다름 |
| 개발 작업 상태 유지 | 통과 | 2 | 809.2 | 다름 |
| 미디어 엔진 종료 | 통과 | 2 | 494.1 | 다름 |
| 영상 요청 라우팅 | 통과 | 2 | 672.3 | 다름 |

기존 첫 실행은 두 번째 과제의 변이 문자열 불일치로 종료했다. 첫 결과는 part1에 보존됐고, Claude가 시작한 part2를 중복 실행 없이 계속 수집했다. `devauto-v3-strata.json`과 `part1.json`의 같은 행을 합산하지 않았다. 종료 확정과 적용 상태는 두 NEXT의 최신 절을 함께 본다.

## 원본과 다른 복구의 검토

- 묶음 적용: 승인 후 `DeveloperMode(core).approved(proposal_id)` 호출 복구. 저장된 diff를 얼린 원본의 변이에 메모리상 적용해 원본과 같은 Python AST/본문임을 확인했다.
- GPU 층 설정: `num_gpu_layers_model` 필드 및 환경변수 로딩 복구. 원본과 다른 것은 주석이며 Python AST는 같다. '원본과 다름'이 기능 실패를 뜻하지 않는 사례다.
- 작업 기록 명령: `/dev-outcomes`, `/dev-learn`, `/dev-insights [인자]`의 분기를 각각 복구한다. 정상 명령·인자 전달이 원래 의도와 맞고 ToolError 처리도 유지한다. 저장된 완전한 diff의 문법을 확인했으며 이번 기록에서 실패를 감추거나 테스트 판정을 우회하는 변경은 발견하지 않았다.
- 이후 과제의 diff 완전성·원본/AST 일치 여부는 `logs/codex-strata-review-20261010/patch-review.json`을 확인한다. 이 파일은 저장 diff만 검토하며 실제 모델을 호출하지 않는다. diff가 저장 상한에 잘렸으면 의미 검증 완료로 표시하지 않는다.

### 개발 상태 복구 후보에서 확인한 보호 누락

진행 중 평가의 승인된 임시 후보 `a0db6ea0b5d9` 전체 파일을 읽기 전용으로 보존했다(`candidate-evidence/`). 이 후보는 기존 검사를 통과했으나 중단 복구 때 suite_result를 INCOMPLETE로 바꾸는 부분과 전체 검사 직전 verifying 상태 저장을 복원하지 않았다. 이전 PASS가 중단 이후에도 남을 수 있다.

두 개의 독립적인 사용자 동작 계약 검사를 만들었다. 기존 정상 코드에서는 2개 통과, 해당 후보에서는 4개 실패 사례가 재현됐다. `candidate-recovery-contracts.txt` 및 `original-recovery-contracts.txt`. 따라서 이 후보가 기존 엄격 지표를 통과하더라도 **의미상 안전한 복구로 채택하지 않는다.** 운영 developer.py는 이미 해당 보호를 갖고 있어 모델 후보를 적용하지 않으며, 평가 종료 후 이 두 검사만 운영 테스트에 추가한다. 이 증거는 거대 모델에서도 기존 테스트의 빈틈을 이용한 불완전 복구가 가능함을 보여 준다.

### 미디어 종료 후보에서 확인한 보호 누락

임시 후보 `bf0be135c867`도 전체 파일을 보존했다. 자식 프로세스가 생기기 전 작업 잠금이 걸려 있어도 close가 False를 반환하며, poll/wait/kill/최종 wait 실패가 ImageGenError로 묶이지 않는 경로가 있다. 종료 실패를 정상적으로 알리고 재시도할 핸들을 유지하는 계약이 불완전하다.

추가 두 검사에서 후보는 1개 실패·4개 오류 사례를 보였고, 기존 정상 코드에서는 2개 통과했다(`candidate-shutdown-contracts.txt`, `original-shutdown-contracts.txt`). 이 후보도 운영에 적용하지 않는다. 기존 imagegen.py를 유지하고 계약 검사만 추가한다.

### 영상 라우팅 후보에서 확인한 기존 요청 회귀

임시 후보 `989a1565a455`는 이미지 라우팅을 영상보다 먼저 검사하고 영상 명령 표현도 줄였다. '이 그림으로 영상 만들어줘' 등 세 요청이 이미지 경로로 가고, 기존의 'render a short video of a dragon', '고양이가 뛰는 영상 뽑아줘'가 영상 생성으로 연결되지 않는다.

추가 두 검사에서 후보는 5개 실패 사례, 기존 정상 코드에서는 2개 통과했다(`candidate-routing-contracts.txt`, `original-routing-contracts.txt`). 이 후보도 채택하지 않고 기존 router.py를 유지하며 계약 검사만 추가한다.

## 과거 27B 결과와의 관계

| 과거 결과 파일 | 원래 전체 엄격 | 이번과 같은 ID 6개만 |
| --- | ---: | ---: |
| coding-agent-eval-history-ctx16k-combined-qwen3.8_27b.json | 5/11 | 3/6 |
| coding-agent-eval-history-v2-best-qwen3.8_27b.json | 9/11 | 5/6 |
| coding-agent-eval-history-v2-diag-qwen3.8_27b.json | 9/11 | 5/6 |

과제 ID를 맞춘 참고 기록이다. v2/v3 과제 정의·테스트·소스·에이전트 구현이 달라 **동일 시험의 모델 교체 비교가 아니다.** 이번은 125B Strata + ALEX의 OpenCode 일꾼 경로다. manifest의 실제 provider 문맥은 65,536이고 요청 설정에 남은 16,384와 다르다. 이번 결과만으로 125B의 순수 모델 개선 폭이나 Claude/Codex 대체율을 계산하지 않는다.

일부 평가 시간에는 Codex의 별도 CPU 릴리스 검사(2 workers)가 겹쳤다. 추가 GPU 작업은 없었으나 과제 전체 소요 시간을 엄밀한 단독 실행 속도 비교로 쓰지 않는다.

## 후속 코드 수정과 검증

**운영 반영: 2026-10-10T12:21:04. 전체 릴리스 통과, 웹 재시작 확인 완료.** 상세 적용 기록은 `logs/codex-strata-review-20261010/application.json`과 두 NEXT의 최신 절을 확인한다.

**운영 전체 1176개, 5 SKIP, RELEASE PASS. logs/codex-strata-review-20261010/operational-release-final.txt**

- R01: Strata 시작 시 Ollama 설치·모델 확인/다운로드를 요구하지 않도록 백엔드 준비 분리.
- R02: Strata 상태의 loaded 누락·비 boolean·오류 응답을 GPU 반환 완료로 처리하지 않음.
- R03: 여러 Strata 설정 파일이 있을 때 `JARVIS_STRATA_CONFIG`를 먼저 적용. 설치 폴더 기준 상대 경로도 지원하고 없는 선택 파일은 명시적으로 거절.
- 독립 사본 전체 릴리스 검사: 1,170개, 기존 5개 SKIP, **RELEASE PASS**. `logs/codex-strata-review-20261010/staged-release.txt`.
- 이후 기존 정상 코드에 개발 중단 복구·미디어 종료·영상 라우팅 계약 검사 각 2개를 추가했다. 이 추가분은 독립 실행 6개 통과하며 전체 적용 뒤 운영 릴리스 검사에 포함한다.
- 실사용 설정은 coding_regression_check가 꺼져 있었으므로 전체 회귀 검사를 켜는 변경을 준비했다. 그래야 추가한 보호 검사들이 실제 자동 수정의 채택 판정에 쓰인다. 모델·문맥·VRAM·전력 값은 바꾸지 않는다.
- 기존 Ollama 시작 검사가 실제 사용자 Strata 설정을 읽던 의존성을 제거했다. Ollama Settings를 검사에 명시하고 기존 검사 기대값을 유지했다. 첫 운영 검사 실패4건 및 수정 후 재검사 로그도 보존했다.
- 기존 평가 종료 확인 뒤 원본 SHA 대조·쓰기 직전 백업을 거쳐 반영한다. 전체 검증/실제 재시작 상태는 위의 상태와 두 NEXT의 최신 절을 우선한다.
- 실제 Strata 서버를 완전히 껐다가 올리는 콜드 스타트 및 새 기준의 GPU 재평가는 수행하지 않았다. 기존 서버 재사용을 통한 ALEX 웹 재시작과 코드 일치는 확인했다.

## 근거 파일

- 원본 평가: `logs/claude-strata-20261010/devauto-v3-strata-part1.json`, `devauto-v3-strata-part2.json`, 관련 로그/manifest.
- 중복 제거 합본: `logs/codex-strata-review-20261010/evaluation-combined.json`, 입력 SHA/완료 상태 `evaluation-progress.json`.
- 이 갱신 시점의 원본 사본: `logs/codex-strata-review-20261010/evaluation-snapshots/20261010-123230-365619/`.
- 검토·수정 절차: `docs/CODEX_STRATA_REVIEW_20261010.md`, 인수 시작 기록 `logs/codex-strata-review-20261010/takeover.md`.

GPU 전력·VRAM 예약·모델 구성과 문맥 설정은 유지했다. 실사용 설정은 전체 회귀 검사만 켰다. 새 학습·벤치마크·모델 다운로드·GitHub push는 수행하지 않았다.
