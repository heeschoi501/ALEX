# 노트북 ALEX–Strata–Qwen3.6 적용 기록 (2026-10-17)

작업본: `C:\Users\karse\local ai project\ALEX`.
Strata: 같은 상위 폴더의 `tools\strata`, 소스 기준 `296b59d`, 설치된 CUDA 12 엔진 0.1.44.1.
RTX 4070 Laptop 8GB / Ryzen 9 7945HX 16코어 / 사용 가능한 물리 RAM 총량 31.18GiB.
Claude의 `HANDOFF_GPT_20261016_LAPTOP.md`와 보드 인계 `20261016T145548Z-s8aj`를 먼저 읽었다.

## 실제 구조와 추가로 확인한 제약

`ALEX StrataProvider → localhost:8080 /v1/chat/completions → serve/server.py → GEN 파이프 → src/qwen35/q36_engine.cu`.
Qwen3.6은 Flash-Next의 전문가 pack·저RAM 모드·프로필 파일을 사용하지 않는다.

| 부분 | 이 설치의 동작 | 이번 판단 |
|---|---|---|
| GPU | 공통 가중치 약 2.62GiB, 문맥·활성값, MTP, 전문가 캐시 | 모델 크기만으로 GPU 적재량을 판단하면 안 된다 |
| CPU/RAM | 전문가 18.75GiB 전부 복사, pin 실패 시 pageable RAM, 물리 코어별 작업자 | CPU 16개 유지. RAM 부족 자체는 해결되지 않았다 |
| 긴 입력 MMQ | GPU 작업 공간 1.10GiB, 전문가 캐시는 층당 4개 | 이 노트북에서는 작업 공간을 없애 캐시 19개로 늘리는 쪽이 측정에서 빨랐다 |
| MTP | greedy일 때 사용, 샘플링/penalty가 logits를 요구하면 사용하지 않음 | 권장 temperature를 무조건 추가하지 않았다. MTP 2 유지 |
| 생각 정도 | 설치된 Qwen 템플릿에는 `enable_thinking`만 있고 `reasoning_effort` 분기는 없음 | medium→low만으로 빨라진다고 말할 수 없다 |
| 생각 상한 | 서버가 `reasoning_budget_tokens`에서 생각 종료 토큰을 넣고 같은 요청을 이어 감 | ALEX에서 상한을 전달하도록 추가 |
| 대화 캐시 | Qwen 엔진은 이전 토큰의 정확한 연장만 재사용. ALEX는 시각·검색 문맥·생각 제거 등으로 prefix가 바뀔 수 있음 | 다른 계열의 prefix cache 기능을 Qwen에도 있다고 가정하지 않았다 |

근거: Strata의 `src/qwen35/q36_engine.cu`(place_experts, serve, Sampling),
`src/kernels/cpu/pool.cpp`, `serve/server.py`(reasoning_budget, request_timings),
`Strata-data/packs/qwen36-ud-q4_k_xl/tokenizer/chat_template.jinja`.
설치된 템플릿을 실제로 low/medium/xhigh로 렌더링해 SHA-256이 같고 Thinking 끄기는 다름을 확인했다:
`logs/strata-qwen-template-check-20261017.json` (GPU 사용 없음).

## 적용한 설정

Strata `strata-qwen36-ud-q4_k_xl.json`:

```json
"env": {"STRATA_Q36_NO_MMQ": "1"}
```

`--max-context 16384 --prefill 512 --mtp 2`는 유지한다. GPU 전문가 슬롯 자동 계산, CPU 작업자 자동 선택(16),
greedy 설정도 유지한다. 전체 모델·새 엔진·드라이버를 다운로드하지 않았다. 기존 CUDA 바이너리를 사용한다.
이 컴퓨터에는 CUDA Toolkit / Visual Studio Build Tools가 없어 C++/CUDA 변경을 적용했다고 주장하지 않는다.

ALEX 노트북 `jarvis.local.json`:

```json
"strata_reasoning_budget_tokens": 1024
```

문맥 16384, 출력 한도 6144, 입력 예산 5529자, Thinking 켜짐, effort medium은 유지한다.
1024는 생각에 쓰는 상한이며 답변 자체의 출력 한도를 1024로 줄이는 설정이 아니다.
복잡한 문제의 답변에 영향을 줄 수 있다. 품질 비교는 하지 않았으며 데스크톱에서 확인해야 한다.
앱의 설정 화면에서 조절할 수 있다. 0은 기존 동작이고, 새 설정의 코드 기본값도 0이므로 다른 컴퓨터에 강제하지 않는다.

## 코드 변경

- Strata `setup.py`: `--families` 확인에도 실제 실행과 같은 CUDA DLL 경로를 전달한다.
  수동 PATH 수정 없이 `qwen35moe`, `qwen4exp` 지원 응답을 확인했다. 이전 엔진 fallback 유지.
- ALEX `strata_runtime.py`: 실행 서버의 모델 이름과 문맥 용량을 검사한다.
  런처·백엔드 전환·대화·OpenCode가 다른 모델을 잘못 재사용하거나 작은 문맥에 큰 요청을 보내지 않는다.
  실행 중인 모델 목록도 설정값 대신 서버의 실제 모델을 표시한다.
- ALEX `providers.py`: Strata의 `timings`에서 입력 시간·생성 시간·생성 속도·캐시 토큰을 읽는다.
  생각 토큰까지 답변 표시 시간만으로 나누던 속도 과대 표시를 수정했다.
  서버 타이밍이 없는 경우 첫 생성 조각부터의 근사값만 쓰고, 0초/NaN/잘못된 수치를 걸러낸다.
- ALEX `config.py`, `app.py`, `web.py`, 설정 UI: 생각 상한의 저장·검증·실행 전달·실효값 표시.
  서버 상한은 생각 종료 후 답변을 이어 쓰게 하며, 기존 클라이언트의 과도한 생각 차단도 남아 있다.
- `scripts/measure_strata.py`: 상태 확인 후 합성 입력을 순서대로 보내는 재현 가능한 속도 측정 도구.
  ALEX/Strata가 작업 중이면 중단한다. 사용자 대화 DB에 요청을 넣지 않으며 출력 본문 대신 해시와 타이밍만 저장한다.

## 속도 측정 (품질 평가 아님)

실제 실행 상태를 먼저 읽었다: LAPTOP, Strata Qwen3.6, ctx16384, output6144, think=true, timeout360.
이후 ALEX가 종료된 상태에서 Strata에 직접 순차 측정했다. 테스트는 Thinking 끔, greedy, 출력 64토큰.
단순 숫자 출력과 코드·한국어·영어를 섞은 합성 입력을 사용했다. 답변 내용의 정답 여부는 평가하지 않았다.
매번 새 입력이며 아래 실행의 prompt cache 재사용은 모두 0이다.

예열 후 혼합 입력 측정, 각 조합 1회 (각 설정에서 별도 첫 실행도 기록):

| 입력 토큰 | MMQ 켬: 첫 내용 / 전체 | MMQ 끔: 첫 내용 / 전체 | 생성 속도 켬 → 끔 |
|---:|---:|---:|---:|
| 123 | 0.782 / 2.172초 | 0.594 / 2.031초 | 45.9 → 44.5 tok/s |
| 739 | 4.656 / 5.938초 | 2.109 / 3.390초 | 50.0 → 50.3 tok/s |
| 2131 | 8.719 / 10.235초 | 6.000 / 7.359초 | 42.2 → 47.1 tok/s |

반복 입력 2088토큰의 예열 후 입력 처리: MMQ 켬 7.887초 → 끔 3.806초.
반복 입력의 숫자 출력은 MTP에 특히 유리하다. 이 수치를 일반 대화 생성 속도라고 부르면 안 된다.
입력 처리가 개선됐지만 짧은 요청의 생성 속도가 모두 빨라진 것은 아니다.

예열 전후 차이가 크다. 처음 기준선에서 물리 RAM 여유가 0.049GiB까지 줄었다.
혼합 입력 예열 측정에서도 기준선 RAM 여유는 0.18~0.62GiB, 후보는 약 0.94GiB였다.
다른 프로그램과 Windows 메모리 압박을 완전히 통제한 벤치마크가 아니다. 수치는 이 환경에서 관측한 결과이며,
더 긴 입력·다른 출력·다른 RAM 상태의 성능이나 답변 품질을 보장하지 않는다.

기록: `logs/strata-mmq512-{baseline,warm,mixed-cold,mixed-warm}-20261017.json`,
`logs/strata-no-mmq512-{cold,warm,mixed,mixed-warm}-20261017.json`.
시작/배치 로그: `logs/strata-start-*-20261017*.log`, `logs/strata-ready-*-20261017*.json` 및 Strata 엔진 로그.
첫 후보 재시작은 74.359초, 재비교 기준선 재시작은 89.891초였다. 시작 속도도 RAM 상태의 영향을 받는다.
최종 후보 설정 재시작은 70.062초였다. 검사를 위해 이후 모델만 잠시 내렸으며 마무리 시 모델 적재를 재확인했다.

재현 예:

```bat
python scripts\measure_strata.py --label check --workload mixed --repeats 4 32 96 --output logs\strata-check.json
```

반드시 Windows Python으로 실행하고, 먼저 사용자에게 측정을 알린다. 다른 GPU 작업과 겹치지 않는다.
품질 테스트(replay, 한국어 정답/코딩 평가)는 노트북에서 하지 않는다.

## 완료 검증과 실행 상태

- Windows Python 3.11 `scripts/verify_release.py --test-workers 4`: **1372개 OK, 7 skipped, RELEASE VERIFICATION PASS**.
  `logs/verify-strata-20261017.txt` (첫 실행에서 발견한 unloaded 상태 처리/가짜 health 응답의 경계 사례를 보완).
- 최종 연결 상태 판정 변경 후 영향 범위 검사: **48개 OK** (`logs/test-strata-final-20261017.txt`).
  비활성 서버는 GPU 반환을 위해 실제 모델 이름과 무관하게 추적하지만, 사용할 서버는 모델·문맥을 검사한다.
- Strata 설치 검사 **14개 OK**, 서버의 생각 상한·effort 경로 검사 **33개 OK**.
  `logs/test-strata-{setup,server}-20261017.txt`. 실제 `--families`도 CUDA DLL 경로 우회 없이 정상.
- Node의 `--check`로 수정한 JS 문법 확인. 변경분 공백 검사 통과.
- ALEX 재시작 완료: `127.0.0.1:8767`, 코드 지문 `80f4369aefbfcab0`.
  Strata: `127.0.0.1:8080`, `loaded: true`, 모델 `qwen3.6-35b-a3b-ud-q4_k_xl`, context 16384.
- ALEX 실효 설정에서 생각 상한 1024, 출력 6144, 문맥 16384, 입력 5529자를 확인했다.
- 마지막 통신 확인은 별도 제한된 요청(출력 128, 생각 상한 32)으로 했다.
  서버 로그의 `thinking budget reached (32 tokens)`와 빈 답이 아닌 정상 종료, 엔진 타이밍 수신을 확인했다.
  사용자 대화 DB에는 이 확인 요청을 넣지 않았다. 결과: `logs/strata-alex-final-smoke-20261017.json`.

## 경험 기록

기존 `scripts/experiences.py add --file ... --write`로 검토용 경험 2건을 저장했다.
입력은 `docs/experiences/20261017_strata_laptop.json`, 근거와 한계를 포함하며 `raw/unrated` 상태다.
ID: `4b24088fc8360ea429bdb03f`, `df43644e923b37c0cb9a4a5c`.
내용 해시 무결성을 확인했다. 승인하거나 split을 수동 지정하거나 모델을 재학습하지 않았다.

## 복구와 다음 작업

변경 전 파일: `logs/before-strata-20261017/`. 이 백업은 Claude의 미커밋 변경까지 포함한 작업 시작 시점이다.
가장 작은 복구는 Strata 설정의 `env.STRATA_Q36_NO_MMQ` **키를 삭제**하고 Strata를 재시작하는 것이다.
값을 `0`으로 바꾸면 안 된다. 엔진은 값이 아니라 환경변수의 존재 여부를 검사한다.
ALEX 생각 상한은 설정 화면에서 0으로 되돌릴 수 있다.

Strata 재시작은 작업이 없는지 확인한 뒤 `/unload`로 모델을 내리고, 이 설치의 서버 프로세스만 종료한 후
같은 config로 다시 시작한다. 모든 python 프로세스를 일괄 종료하면 안 된다.
ALEX의 웹 포트는 8767, Strata는 8080이다.

이 변경으로 RAM의 전문가 18.75GiB 복사를 없애지는 못했다. 디스크 pack/저RAM 경로/전문가 순위 저장은
별도 CUDA 엔진 개발·빌드·검증이 필요하다. 샘플링을 적용하거나 모델을 바꾸는 품질 비교는 데스크톱에서 한다.
커밋·푸시는 하지 않았다.
