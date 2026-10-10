# Strata 후속 검토 및 적용 대기 수정안

작성: 2026-10-10 11:22 KST, Codex. 사용자 요청: Claude Code의 현재 작업을 확인하고 충돌 없이 이어서 진행.

## 사용자 요청에 따른 후속 인수

**12:30 KST 완료:** 기존 평가 종료 후 수정·검사·설정을 운영에 반영했다. 전체 **1,176개(5 SKIP) RELEASE PASS**, ALEX 웹 재시작 후 실행/디스크 코드 `a0f1e305065ef27a` 일치를 확인했다. Strata 서버 PID3448·모델 적재는 유지했다. 적용/백업/검증 기록은 `logs/codex-strata-review-20261010/application.json` 및 `operational-release-final.txt`를 확인한다.

Claude 사용량 소진을 사용자가 직접 보고해 Codex가 결과 수집·검토·후속 수정을 맡았다. 최신 평가 결과는 `docs/ALEX_STRATA_EVALUATION_20261010.md`와 두 NEXT의 가장 최근 절을 우선한다. 아래 본문은 11:22 첫 검토 시점의 기록이다.

- R03도 별도 사본에서 수정했다. 명시적 설정 파일을 먼저 선택하고, 상대 경로는 Strata 설치 폴더 기준으로 해석하며, 존재하지 않는 선택 파일은 거절한다.
- R01/R02/R03 및 새 Strata 계약 검사 6개를 포함한 사본은 전체 1,170개(5 SKIP) 릴리스 검사 통과했다.
- 이후 모델의 개발 상태 복구·미디어 종료·영상 라우팅 후보에서 기존 검사가 놓친 보호/동작 누락을 실제 재현했다. 기존 정상 코드는 새 계약 검사 6개를 통과했고 후보는 각각 4개 실패, 1개 실패+4개 오류, 5개 실패를 보였다. 운영 developer.py/imagegen.py/router.py는 유지하고 계약 검사만 추가했다.
- 최종 반영은 launcher.py/providers.py, 새 검사 파일4개, 기존 Ollama 검사 설정 분리, jarvis.local.json의 회귀 검사 활성화(총8개 파일)다. 설정은 회귀 검사 하나만 바꾸고 모델·문맥·VRAM·전력 값은 유지했다.
- 첫 운영 릴리스 검사에서 기존 Ollama 검사4개가 사용자 Strata 설정을 읽는 문제를 발견해 명시적 Ollama Settings로 분리했다. 기존 기대값을 유지한 대상9개 검사와 최종 전체 검사가 통과했다. 초기 실패 기록도 보존했다.
- 후보 전체 파일과 원본 대비 diff, 모의 재현 결과는 `logs/codex-strata-review-20261010/candidate-evidence/`, `candidate-*-contracts.txt`, `original-*-contracts.txt`에 보존했다. 기존 평가 원본은 고치지 않았다.

## 현재 진행 상황

- Claude의 11:10 인계와 실제 프로세스·평가 로그를 확인했다. ALEX(8765), Strata(8080)를 유지했다. 모델 생성 요청, 서버 재시작, GPU 설정 변경은 하지 않았다.
- 최초 평가 `logs/claude-strata-20261010/devauto-v3-strata.log`는 첫 과제 성공 후 두 번째 과제의 변이 문자열이 현재 코드에 없어 `AssertionError`로 종료했다. 이는 모델의 문제 해결 실패와 구별해야 한다.
- 첫 과제 `hist_claude-backup-20260926-devbundle_app_test_developer`: 엄격 성공, 원본과 같은 복구, 회귀 실패 0, 3회 수정, 809.0초. 완료 결과는 **1개**뿐이며 11개 전체 성공률로 해석하지 않는다.
- Claude는 결과를 `devauto-v3-strata-part1.json`으로 보존하고 `tasks-v3-remaining.json`으로 나머지를 재개했다. 11:19 확인 기준 평가 PID 7416, 로그 `devauto-v3-strata-part2.log`, 시작 11:16:56. 따라서 최초 PID가 사라졌다는 이유로 새 평가를 시작하면 안 된다.
- 기존 사전 검사 `logs/probe/preflight_coding_eval.py`를 재사용했다. 최초 11개 중 6개만 문자열·문법 조건 통과, 5개는 오래된 변이 문자열이 없어 평가 불가. Claude의 나머지 과제는 5/5 통과. 이는 정적 확인이며 테스트 통과나 모델 성능을 보장하지 않는다.
- 최초 manifest의 요청 문맥 값은 16,384이지만 기록된 실제 provider 문맥은 65,536이다. 이번 결과를 단순히 '16K 평가'로 분류하지 않는다. 이전 27B 결과와 모델뿐 아니라 실행 경로·과제·설정이 달라 직접적인 우위 주장도 보류한다.

## 발견 및 별도 사본에서 수정한 문제

### R01 [P1] Strata 전용 시작도 Ollama 설치·모델을 요구함

위치: `jarvis/launcher.py`, `launch()`의 백엔드 준비 단계.

현재 런처는 Ollama의 설치·실행·모델 목록 확인·필요 모델 다운로드를 먼저 수행하고 이후 `ensure_strata()`를 호출한다. Strata만 설치된 PC에서는 이미 Strata가 정상이어도 Ollama 다운로드 안내에 막힐 수 있다. 별개 모델 서버를 불필요하게 시작할 수도 있다.

수정안은 `local_backend == "strata"`이면 Strata 준비만 수행하고, 기존 하드웨어 프로필·Ollama 모델 준비는 `else`로 옮긴다. 기존 ALEX 서버 재사용·포트/코드 지문 확인, 웹 서버 시작 과정은 유지했다.

검증: 실제 프로세스와 통신을 모의 처리한 시작 검사. Ollama 모델 선택·설치·API 접근이 한 번이라도 발생하면 실패한다. 원본에서는 실패, 수정안에서는 Strata 준비와 웹 시작만 수행하여 통과했다. **실제 모델을 내려서 하는 콜드 스타트 검사는 아직 수행하지 않았다.**

### R02 [P1] 불명확한 Strata 상태를 GPU 반환 완료로 오판

위치: `jarvis/providers.py`, `StrataProvider.loaded_models()`.

현재 구현은 `health.get("loaded")`의 참/거짓만 보고 판정한다. `loaded`가 누락되거나 `None`·숫자 0이면 빈 모델 목록을 반환하여 미디어 실행 전 반환 확인에서 '비어 있음'으로 오판할 수 있다.

수정안은 오류 응답과 `loaded`의 비 boolean 값을 `ProviderError`로 거절한다. 명시적인 boolean `False`에서만 빈 목록을 반환하며, `True`이면 모델 목록을 반환한다. 새 VRAM 여유량을 추가하는 변경은 없다.

검증: 누락·None·0·문자열·오류가 포함된 False 응답을 거절하고, 명시적인 True/False를 구분하는 검사. 원본의 5개 거절 사례가 실패했고 수정안에서 통과했다. 실제 `/unload`는 호출하지 않았다.

### R03 [P2] 여러 Strata 설정 파일에서 선택 환경변수가 효력을 못 냄

위치: `jarvis/launcher.py`, `strata_command()`.

설정 파일이 둘 이상이면 `JARVIS_STRATA_CONFIG`를 읽기 전에 예외를 발생시킨다. 안내문에 따라 환경변수로 하나를 지정해도 해결되지 않는다. 현재 설치는 한 설정 파일이므로 이번 두 수정안에 포함하지 않았다. 다음 보완에서는 명시적 설정을 먼저 선택하고 존재·경로를 확인하는 검사까지 추가한다.

## 수정안 및 검증 증거

운영 `jarvis/`, `tests/`, `scripts/`, `jarvis.local.json`, 실행 중 평가 파일은 변경하지 않았다. 작업 시작부터 `logs/codex-strata-review-20261010/worklog.md`에 기록했다.

- 수정 전 사본: `logs/codex-strata-review-20261010/baseline/`
- 수정 후 사본: `logs/codex-strata-review-20261010/staged/`
- 적용할 diff: `logs/codex-strata-review-20261010/changes.diff`
- 원본 39개 SHA-256: `snapshot-manifest.json`
- 적용할 3개 파일의 원본/수정 SHA-256: `patch-manifest.json`
- 추가 검사: `staged/tests/test_strata_handoff.py` (3개 검사 메서드)

| 확인 | 결과 | 로그 |
| --- | --- | --- |
| 원본에 새 계약 검사 실행 | 실패 6건: 런처 1건 + 상태 판독 5개 하위 사례 | `before-tests.txt` |
| 수정 사본에 새 검사 | 3개 통과 | `after-tests.txt` |
| 기존 Strata 검사 | 16개 통과, ResourceWarning 오류 처리 | `strata-existing-tests.txt` |
| 기존 Ollama 런처 검사 | 9개 통과 | `ollama-existing-tests.txt` |
| 원본 과제 정적 확인 | 6/11 통과 | `preflight-original.json` |
| 재개 과제 정적 확인 | 5/5 통과 | `preflight-remaining.json` |

수정 사본의 관련 검사 합계는 **28개 통과**다. 모의 서버·모의 프로세스 검사이며 실제 GPU를 사용하지 않았다. 전체 릴리스 검사는 아직 수행하지 않았다. Claude의 기존 1164개 릴리스 통과 기록은 이 수정안의 전체 검증을 대신하지 않는다.

첫 과제 비교표는 기존 읽기 전용 도구로 생성한 `evaluation-first-task.md`에 있다. 원본 평가 결과를 중복 합산하지 않는다(`devauto-v3-strata.json`과 `part1.json`은 같은 첫 결과).

## Claude / 다음 Codex 적용 순서

1. `devauto-v3-strata-part2.log`와 관련 평가 프로세스를 다시 확인한다. 진행 중이면 운영 파일 변경·추가 모델 실행·서버 재시작을 보류한다. 이 문서의 PID는 시점 기록이지 현재 상태 보장이 아니다.
2. 평가 종료 후 `patch-manifest.json`의 `baseline_sha256`와 운영 `jarvis/launcher.py`, `jarvis/providers.py`를 대조한다. 다르면 staged 파일을 통째로 덮어쓰지 말고 R01/R02 부분만 최신 코드에 병합한다. 새 `tests/test_strata_handoff.py`가 이미 생겼어도 먼저 비교한다.
3. 쓰기 직전에 원본을 다시 읽고 날짜가 붙은 별도 백업을 남긴다. `changes.diff`의 두 변경과 새 검사 파일만 적용한다. baseline/staged 폴더 전체를 운영 폴더로 복사하지 않는다.
4. 관련 검사 3개 파일을 실행한다. 이후 `scripts/verify_release.py`의 기존 절차로 전체 릴리스 검사를 수행한다. GPU 평가 결과는 적용 전후의 소스와 구별해 기록한다.
5. 검사 통과 후 실제 ALEX 재시작이 필요하면 대기·GPU 작업 종료를 확인하고 정상 lifecycle 절차를 사용한다. 가능하면 사용자 입회 시 Strata 단독 콜드 스타트도 확인한다. 검증 전에는 실제 시작 문제 해결 완료로 표시하지 않는다.
6. 적용·전체 검증·재시작 여부를 두 NEXT와 CHANGELOG에 갱신한다. 현재는 **수정안 준비 완료 / 운영 적용 대기**다.

GPU 전력 360W, Claude가 설정한 Strata `--vram-reserve-mib 2048`, 모델·서버 구성은 이번 검토에서 바꾸지 않았다. 공개 GitHub 사본도 변경하거나 push하지 않았다.
