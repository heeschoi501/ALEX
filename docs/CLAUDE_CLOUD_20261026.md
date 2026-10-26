# 2026-10-26 클라우드 작업 인수인계 (Claude, PC 없이)

사용자가 PC 를 쓸 수 없는 동안 클라우드 컨테이너(그래픽카드·모델 없음, CPU 4개)에서 할 수 있는 일만 했다. 모든 확인은 가짜 모델·가짜 일꾼·실제 unittest 로 했고, **실제 모델 성능은 재지 않았다.** 브랜치 `ccr-75791893-0vhkrr` 에만 올렸고 `main` 은 그대로다.

## 되돌리기

- 이 작업 전 상태 = `main`(커밋 `ade8538`). PC 에서 `git checkout main` 이면 그대로 예전 상태다.
- 기능마다 커밋이 따로라 하나만 빼려면 `git revert <커밋>`.

## 바뀐 것

| 커밋 | 내용 | 확인 |
|---|---|---|
| live.py 파이프 정리 | 받아쓰기 일꾼을 멈추거나(고장·닫기·쉬어서 내리기) 다시 띄울 때 stdin/stdout 을 닫지 않아 ResourceWarning 이 났다(2026-10-22 기록의 후속). 읽는 스레드가 stdout 을 닫고 자기 일꾼의 큐에만 쓴다 — 늦게 끝난 옛 일꾼이 새 일꾼을 '멈춤'으로 보이게 하던 경합도 함께 막음. 끝난 일꾼에 쓰기는 다시 시도할 수 있는 LiveError. | 새 시험이 수정 전 실패·수정 후 통과. `python -X dev -m unittest tests.test_live` 경고 0 |
| 무료 모델로 보내는 복사본의 키 파일 제외(ADR-118) | OpenCode 무료 모델 경로가 `.env`·`jarvis.local.json`·`.pem`/`.key` 만 빼서 `id_rsa`·`credentials.json`·`secrets.*`·`.pfx`·`.netrc` 등이 opencode.ai 로 나갈 수 있었다. 클라우드 넘기기와 같은 이름 목록을 `opencode_worker.secret_file()` 하나로 쓰게 했다. | 새 시험 항목이 수정 전 실패·수정 후 통과 |
| 평가 과제 v4 | 자동 수정 평가 과제 11개 중 7개가 지금 소스와 안 맞아 건너뛰어지던 것(10-22 video_router 포함)을 지금 소스에 다시 심었다. `logs/probe/refresh_mutation_tasks.py`(3-way 병합 → 지운 구간이 자란 경우 줄 단위 이식 → 3과제는 손으로). `--check` 로 복사본에서 **고치기 전 통과·변이 뒤 실패**를 과제마다 확인해 통과한 것만 `history_tasks_contracts_v4_20261026.json` 에 넣었다. | 10/11 사용 가능, wan14b 과제는 영상 계획 구조가 바뀌어 제외 |
| PC 평가 한 번에 돌리기 | `scripts/run_pc_benchmarks.py` — 자동 수정(v4 과제)·LiveCodeBench hard·GPQA Diamond 를 기존 스크립트 그대로 부르고, 준비 안 된 것은 이유를 적고 건너뛴다. 결과 폴더에 로그·`summary.json`·`summary.md`. | 계획·건너뛰기·요약 시험 4개 |

## 이미 되어 있던 것 (문서만 '후속'으로 남아 있었음)

`docs/HANDOFF_20261022.md` 의 다음 순서 1·3번은 10-23 커밋 `16f9d97` 에서 이미 구현돼 있었다. 그 문서와 CHANGELOG 10-22 항목의 '후속' 표기는 당시 기록이라 고치지 않는다.

- 1번 호출 중 시간 한도·취소: `OllamaProvider.chat_step_controlled` 가 요청 사본에 기한을 묶고 `open_interruptible` 이 헤더 대기·본문 스트림 중에도 그 연결의 소켓만 끊는다. 시험 `tests/test_harness_control.py`(두 프로토콜의 헤더·연속 본문 기한 끊기, 사용자 취소, 요약 호출 시작 전 기한, 재시도 대기).
- 3번 `/code` 세션 독립 검사: `CodeSessions._verify`(권한 단계에 맞춰 복사본에서 관련 시험 실행·한 번 재수정·학습 기록). 시험 `tests/test_code_verification.py`.

## 리뷰에서 본 것 (고치지 않음)

- 무료 모델 경로는 **이름만** 보고 거른다. 클라우드 넘기기처럼 파일 내용 속 비밀값 모양(secret_patterns)까지 보지는 않는다 — 다음에 필요하면 같은 검사를 붙인다.
- `harness/intent.py` 의 허락 답 판정: '노트…'·'no-op…' 으로 시작하는 말은 거절로 읽힌다(안전한 쪽 오판). 허락 대기 중에만 쓰인다.
- 검사 통과 재사용(`testevidence`·`unittest_run`·`already_passed`)과 Office 경로 검사(`office._pc`·`_stored`)는 문제를 찾지 못했다.

## PC 에서 확인할 것 (순서대로)

1. 브랜치 받기: `git fetch origin` → `git checkout ccr-75791893-0vhkrr`.
2. 전체 검사: `python scripts/verify_release.py` — 클라우드(Linux, Python 3.13)에서는 통과. Windows 에서 한 번 더.
3. 받아쓰기: `/live` 에서 몇 마디 → 창을 닫았다 다시 열기 → 다시 말하기(일꾼 재시작이 정상인지).
4. 무료 모델 코딩 세션 한 번(선택): 프로젝트 폴더에 가짜 `id_rsa` 를 두고 세션을 열어, OpenCode 쪽 복사본(`data/code_sessions/ses_*.oc/project`)에 없는지.
5. 평가: `python -X utf8 scripts/run_pc_benchmarks.py --dry-run` 으로 준비 상태를 본 뒤 `python -X utf8 scripts/run_pc_benchmarks.py`. 자동 수정 10과제는 과제당 수십 분이 걸릴 수 있다(10-22 기록: 5과제 약 5,000초). 오래 걸리면 `--only devauto` 처럼 나눠서.
6. 결과 폴더(`logs/bench-…`)의 `summary.md`·`summary.json`(필요하면 `devauto.json`)을 클라우드 세션에 주면 10-10·10-22 기록과 비교 보고서를 쓴다.

## 해석 주의

- v4 과제의 옮긴 변이는 원래 버그와 '같은 변경'을 지금 코드에 한 것이다. 난이도가 원래와 같다는 보장은 없어서 v3 결과와 숫자를 그대로 비교하지 않는다.
- 클라우드 검사는 Linux 다. Windows 전용 동작(opencode.exe, taskkill, 소켓 강제 닫기)은 PC 에서만 확인된다.
