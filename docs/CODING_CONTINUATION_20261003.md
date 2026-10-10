# 코딩 에이전트·학습 안정화 — 2026-10-03, Codex

## 이번 수정

사용자는 인계 후 평가 준비뿐 아니라 현재 코드에서 가능한 오류 수정을 요청했다. `GPT_NEXT.md`의 10/03 동시 작업 안내에 따라 125B 모델 평가와 ComfyUI 작업은 겹치지 않게 두고, CPU로 확인할 수 있는 코딩 에이전트·학습 로직을 고쳤다.

1. `jarvis/developer.py`: `unittest` 종료 코드가 0이어도 실제 실행한 테스트가 0개이거나 모두 skipped면 실행 문제 125로 처리한다. 선택한 테스트 파일이 격리 복사본에 실제로 있는지 매 회차 검사한다. 복사본의 수정 대상 파일이 없거나 복사 시작 전 읽은 원본과 다르면 모델을 부르기 전에 멈춘다.
2. `jarvis/insights.py`: `after_evidence`가 다음 `auto`의 결과를 이전 기록에 연결하지 않게 경계를 바꿨다. 내용이 같은 두 기록도 객체 동일성으로 구별한다. 긴 상위 교훈이 예산을 초과해도 뒤의 짧은 교훈은 살핀다.
3. 위 동작의 회귀 사례를 `tests/test_developer.py`와 `tests/test_insights.py`에 추가했다.

## 근거와 검증

- 기존 운영 코드의 거짓 통과를 임시 프로젝트에서 실제 재현했다: [재현 결과](../logs/codex-empty-test-result-20261003.json). 원본 테스트는 `exit=1`인데 복사본에서 빠지자 `/dev-auto`가 `passed`로 기록했으며, 모델 호출은 0회였다.
- 운영 파일 수정 전 별도 후보 코드에서 9개 사례를 통과시켰다: [후보 검증](../logs/codex-test-guard-candidate-20261003.txt), [변경 diff](../logs/probe/codex-test-guard-20261003.diff). 실제 `jarvis/developer.py`의 수정 후 SHA-256은 후보와 같은 `b80fda79adf42c5cebce1209439fde2617721a2b439827b5e227a5e2052eecb9`다.
- Windows Python 3.11.9의 표적 테스트: [코딩 모드 49개 PASS](../logs/codex-testguard-targeted-20261003.txt), [학습 16개 PASS](../logs/codex-insights-targeted-20261003.txt).
- 첫 전체 release 검증은 858 tests 중 2개가 실패했다: `logs/codex-release-verify-20261003.txt`. 검사 중 Claude가 `jarvis/core.py`를 11:24에 수정했다. 먼저 import한 `web.LOADED_CODE`와 나중에 읽은 디스크 지문이 달랐고, `inspect.getsource`도 이전 함수의 소스 줄을 읽었다. 수정 후 새 프로세스에서 [launcher 검사](../logs/codex-launcher-fresh-20261003.txt)와 [출처 링크 검사](../logs/codex-static-links-fresh-20261003.txt)는 모두 통과했다. 두 번째 [전체 검사](../logs/codex-release-verify-second-20261003.txt)는 **858 tests OK, 3 skipped, RELEASE VERIFICATION: PASS**. [소스 지문 기록](../logs/codex-release-stable-20261003.txt)에서 시작·종료 `jarvis/`·`tests/`·`scripts/`의 Python 파일 지문이 `c7e5378423eac89b3053cd1c3ef4b7d83e1b14941bcee9aea3e46ddd05c9b8e1`로 같음을 확인했다.
- 운영 앱 `/health`의 코드 `2b3e486119c39dfa`가 11:30에는 디스크와 일치했다. **11:31 이후 Claude가 `jarvis/imagegen.py`를 다시 수정했다.** 11:33 관측: `/health` 응답 `2b3e486119c39dfa`, 디스크 `121637b04cdd117f`로 달랐다. Claude 이미지 작업이 끝나 재시작한 뒤 `/health`와 디스크 지문을 재확인해야 한다. 브라우저 탭은 재시작 뒤 F5.
- 변경 전 네 소스·테스트 파일과 인계 원본 백업: `logs/codex-backup-20261003-testguard/`.

## 평가·교훈 준비

- 신규 [정적 사전 검사](../logs/probe/preflight_coding_eval.py)가 저장된 과제의 고유 ID, 대상 경로, 변이의 정확한 적용 횟수, 변형한 Python 구문, 실행 중 파일 변경을 확인한다. 도구 자체의 9개 CPU 테스트 PASS. 코드 수정 후 다시 검사해 일반 [16/16](../logs/codex-preflight-test-postfix-20261003.json), 어려운 [11/11](../logs/codex-preflight-hard-postfix-20261003.json) 확인. **테스트 실행 여부와 모델 해결 능력을 입증한 결과는 아니다.** 평가 시작 직전 최신 소스에서 다시 검사해야 한다.
- 위험한 과잉 일반화가 확인된 교훈 #3·#6의 문장을 [v2 후보](../logs/coding-insights-v2-candidate-20261003.json)에 수정했다. 기존 학습 파일의 SHA-256 `05f496faae2e11f9f8d1be7634d05d91a19d287811f9d82354b0fd67a0c39669`와 내용은 그대로 유지했다. 기존 8개 교훈을 ALEX 파서가 모두 읽고 #3·#6도 프롬프트에 포함함을 CPU에서 확인했다. 후보는 효과 시험·승인·실행 설정 반영 전이며 기존 성적과 섞으면 안 된다.

## 인계 시 확인할 것

1. 클로드의 이미지 생성 작업 종료 및 GPU 해제 확인. 그 전에는 125B 평가를 시작하거나 Ollama를 재설정하지 않는다. `GPT_NEXT.md` 10/03 11:12 동시 작업 항목이 기준이다.
2. Codex 수정과 당시 이미지 코드를 포함한 release gate는 위 지문에서 통과했다. Claude가 이후 `imagegen.py`를 바꿨으므로 이미지 작업 완료 때 해당 변경에 맞는 검사와 `/health` 비교가 필요하다. 11:33 관측에서는 서버가 떠 있었지만 디스크와 코드 지문이 달랐다. 재시작은 Claude 이미지 작업에 맞춰 조정한다.
3. 125B 평가를 택하면 27B와 같은 과제의 hard 11개·test 16개를 별도 결과 파일에 기록한다. 안전 온도·CUDA 중단·FA off·기본 GPU 클럭을 확인하고 정적 사전 검사를 다시 실행한다. 정적 사전 검사는 기존 테스트가 변이를 잡는지까지 확인하지 못한다.
4. 교훈 v2 후보는 #3·#6의 문장만 변경했다. 점수·근거 문자열·학습 키는 기존 값을 보존했으며 점수가 성공률은 아니다. 다른 모델/코드 조건이 섞인 기존 off/on 점수와 인과 비교하지 않는다.
