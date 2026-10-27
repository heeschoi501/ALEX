# TODO — 지금 남은 일 (2026-10-26 기준)

예전 v0.2.0 계획은 [docs/archive/TODO-v0.2.0.md](docs/archive/TODO-v0.2.0.md) 로 옮겼다. 이 목록은 최근 인수인계 문서에서 아직 끝나지 않은 것만 모은 것이다. 끝나면 지우고, 근거는 CHANGELOG·인수인계 문서에 남긴다.

## PC 에서만 할 수 있는 것 (모델·그래픽카드·Windows)

1. 2026-10-26 클라우드 작업 확인 — 순서는 [CLAUDE_CLOUD_20261026.md](docs/CLAUDE_CLOUD_20261026.md#pc-에서-확인할-것-순서대로).
2. 성능 평가 — `python -X utf8 scripts/run_pc_benchmarks.py` (자동 수정 v4 11과제·LiveCodeBench hard·GPQA). 결과 폴더를 넘기면 10-10·10-22 기록과 비교.
3. 같은 조건 여러 회차 비교 — 소스·문맥·출력 상한을 고정하고 기존 OpenCode 경로와 ALEX 하네스를 여러 번 잰다([HANDOFF_20261022](docs/HANDOFF_20261022.md) 다음 순서 2). 한 번 잰 값으로 우열을 일반화하지 않는다.
4. 프롬프트 순서(ADR-119) 측정 — `python -X utf8 scripts/measure_prompt_cache.py --live`. 빨라지고 답이 그대로면 `stable_prompt_prefix` 를 켠다([HERMES_REFERENCE_20261027](docs/HERMES_REFERENCE_20261027.md)).
5. 딥서치 실제 주제 1건 — 출처 충실도·빠진 질문·걸린 시간 확인 후 개선([CODEX_DEEPSEARCH_20261023](docs/CODEX_DEEPSEARCH_20261023.md)).

## 2026-11-30 까지 결정(ADR-121, `docs/upstreams.json`)

- 실험 7개: 측정해 기본으로 만들고 옛 길을 지우거나, 그 기능을 지운다. 측정 방법은 목록의 `measure`.
- 코드 고치기 4갈래 중 진 일꾼 내보내기(같은 v4 과제로 `coding_worker` 별 비교).
- 매달 `python -X utf8 scripts/check_upstreams.py` — 원본이 바뀐 곳을 판정해 `history` 에 남기고 기준 판을 옮긴다.

## 클라우드(모델 없이)에서도 할 수 있는 것

- 관련 시험 고르기는 직접 import·변경 함수 사용에 기반한 어림이다. 동적 import·간접 의존은 놓칠 수 있어 바깥 전체 회귀 검사를 계속 유지한다(지울 항목 아님 — 바꾸기 전에 기억할 제약).
- 무료 모델로 보내지 않는 파일: 가짜 비밀값을 담은 시험 파일(test_core 등)도 빠진다. 그 시험이 합격 기준인 과제는 무료 모델이 시험 내용을 못 본다. 필요하면 시험 파일만 예외로 할지 정한다.
- 휴대폰 입력창 아래 도구 줄은 가로로 밀어야 '추론'·'오픈코드 API' 가 보인다(스크롤바 숨김). 더 있다는 표시가 필요한지 정한다.
- 브라우저 JS 로 그리는 문서·웹 PDF 는 딥서치가 읽지 못한다(WebReader 범위 밖).

## 지금 넣지 않을 것 (v0.2.0 때부터 유지)

- 무승인 파일 쓰기·임의 셸 자동 실행·무승인 클라우드 전송
- LAN 에 웹 화면을 그대로 공개
