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
| 평가 과제 v4 | 자동 수정 평가 과제 11개 중 7개가 지금 소스와 안 맞아 건너뛰어지던 것(10-22 video_router 포함)을 지금 소스에 다시 심었다. `logs/probe/refresh_mutation_tasks.py`(3-way 병합 → 지운 구간이 자란 경우 줄 단위 이식 → 3과제는 손으로). `--check` 로 복사본에서 **고치기 전 통과·변이 뒤 실패**를 과제마다 확인해 통과한 것만 `history_tasks_contracts_v4_20261026.json` 에 넣었다. | 11/11 사용 가능(2차에서 wan14b 도 손으로 다시 심음 — 원래 실패하던 Wan14 시험 3개 + 새 14B I2V 시험이 실패) |
| PC 평가 한 번에 돌리기 | `scripts/run_pc_benchmarks.py` — 자동 수정(v4 과제)·LiveCodeBench hard·GPQA Diamond 를 기존 스크립트 그대로 부르고, 준비 안 된 것은 이유를 적고 건너뛴다. 결과 폴더에 로그·`summary.json`·`summary.md`. | 계획·건너뛰기·요약 시험 4개 |

## 2차 작업 (같은 날)

| 커밋 | 내용 | 확인 |
|---|---|---|
| 허락 답 오판 | 허락 대기 중 '노트에 …'·'no-op …' 으로 시작하는 말을 거절로 읽던 것 — 짧은 '노·no' 는 낱말일 때만 거절. | 새 시험 항목이 수정 전 실패·수정 후 통과 |
| 무료 모델: 내용 속 비밀값 | 이름이 평범해도 내용에 실제 키 모양(sk-·ghp_·AKIA·AIza·glpat-·xox·JWT·Bearer·비밀번호 든 DB 주소·본문 있는 개인 키)이 보이면 보내지 않는다. 대화 검사의 'api_key=·password:' 는 파일에 쓰지 않는다 — `password: str` 같은 코드에 걸려 ALEX 의 core·web·app 까지 빠졌다(실측). 지금은 ALEX 에서 가짜 비밀값을 담은 시험 파일 6개만 빠진다. 보내지 않은 자리나 `.env` 같은 이름에 OpenCode 가 쓴 파일은 들이지 않는다(새 파일이 원본을 덮지 않게). | 세션·일꾼 시험 각 1개가 수정 전 실패·수정 후 통과 |
| 새 대화 입력 유지 | '새 대화' 를 누르고 만들어지는 동안 친 글이 **옛 대화의 초안**으로 저장되고 새 입력창은 비던 문제(브라우저 검사에서 발견). 브라우저 검사가 앞 요청 기록을 읽던 것도 새 요청을 기다리게 고침. | `node scripts/verify_web_ui.cjs` 전부 PASS, 수정 전에는 'the new chat must reach the model' 로 실패 |
| 평가 과제 11/11 | 위 표 참고. | |
| 시험 보강 | 전체 커버리지 82%. 모델에 바로 영향을 주는 `harness/diagnostics.py`(ruff·yosys 결과 읽기, 49%→94%)와 Ollama 설치기 `runtime.py`(SHA-256 대조·압축 밖 경로 거절, 20%) 시험 추가. 별도 프로세스로 도는 `unittest_run`·`suite_runner`·`build_runner` 의 낮은 숫자는 측정 방식 탓이다. | 3.11·3.13 통과 |
| 문서 정리 | CHANGELOG 의 9월 기록(v0.1~09-30)을 `docs/archive/CHANGELOG-2026-09.md` 로 그대로 옮김(160KB→40KB). TODO.md 는 지금 남은 일만, 옛 v0.2.0 계획은 `docs/archive/TODO-v0.2.0.md`. DECISIONS.md 는 `scripts/experiences.py` 가 ADR 문장을 닻으로 찾아서 그대로 둠. | |

검사: Python 3.11(PC 와 같은 판)·3.13 모두 `scripts/verify_release.py` PASS, 브라우저 검사 PASS(데스크톱·휴대폰, JS 오류 0). 휴대폰 입력창 아래 도구 줄은 가로로 밀어야 '오픈코드 API' 가 보인다(스크롤바 숨김) — 고치지 않고 TODO 에 남김.

## 이미 되어 있던 것 (문서만 '후속'으로 남아 있었음)

`docs/HANDOFF_20261022.md` 의 다음 순서 1·3번은 10-23 커밋 `16f9d97` 에서 이미 구현돼 있었다. 그 문서와 CHANGELOG 10-22 항목의 '후속' 표기는 당시 기록이라 고치지 않는다.

- 1번 호출 중 시간 한도·취소: `OllamaProvider.chat_step_controlled` 가 요청 사본에 기한을 묶고 `open_interruptible` 이 헤더 대기·본문 스트림 중에도 그 연결의 소켓만 끊는다. 시험 `tests/test_harness_control.py`(두 프로토콜의 헤더·연속 본문 기한 끊기, 사용자 취소, 요약 호출 시작 전 기한, 재시도 대기).
- 3번 `/code` 세션 독립 검사: `CodeSessions._verify`(권한 단계에 맞춰 복사본에서 관련 시험 실행·한 번 재수정·학습 기록). 시험 `tests/test_code_verification.py`.

## 1차 리뷰에서 본 것

- (2차에서 고침) 무료 모델 경로의 내용 검사, 허락 답 오판.
- 검사 통과 재사용(`testevidence`·`unittest_run`·`already_passed`)과 Office 경로 검사(`office._pc`·`_stored`)는 문제를 찾지 못했다.

## PC 에서 확인할 것 (순서대로)

1. 브랜치 받기: `git fetch origin` → `git checkout ccr-75791893-0vhkrr`.
2. 전체 검사: `python scripts/verify_release.py` — 클라우드(Linux, Python 3.11·3.13)에서는 통과. Windows 에서 한 번 더. 브라우저 검사 `node scripts/verify_web_ui.cjs` 도(Node·playwright 가 있으면).
3. 받아쓰기: `/live` 에서 몇 마디 → 창을 닫았다 다시 열기 → 다시 말하기(일꾼 재시작이 정상인지).
4. 무료 모델 코딩 세션 한 번(선택): 프로젝트 폴더에 가짜 `id_rsa` 와, `TOKEN = "ghp_…"`(ghp_ 뒤 영숫자 20자 이상)를 담은 .py 를 두고 세션을 열어, OpenCode 쪽 복사본(`data/code_sessions/ses_*.oc/project`)에 둘 다 없고 '보내지 않은 파일' 안내가 나오는지.
4-1. 새 대화: '새 대화' 를 누르자마자 글을 쳐서 보내기 — 새 대화로 가는지.
5. 평가: `python -X utf8 scripts/run_pc_benchmarks.py --dry-run` 으로 준비 상태를 본 뒤 `python -X utf8 scripts/run_pc_benchmarks.py`. 자동 수정 11과제는 과제당 수십 분이 걸릴 수 있다(10-22 기록: 5과제 약 5,000초). 오래 걸리면 `--only devauto` 처럼 나눠서.
6. 결과 폴더(`logs/bench-…`)의 `summary.md`·`summary.json`(필요하면 `devauto.json`)을 클라우드 세션에 주면 10-10·10-22 기록과 비교 보고서를 쓴다.

## 해석 주의

- v4 과제의 옮긴 변이는 원래 버그와 '같은 변경'을 지금 코드에 한 것이다. 난이도가 원래와 같다는 보장은 없어서 v3 결과와 숫자를 그대로 비교하지 않는다.
- 클라우드 검사는 Linux 다. Windows 전용 동작(opencode.exe, taskkill, 소켓 강제 닫기)은 PC 에서만 확인된다.
