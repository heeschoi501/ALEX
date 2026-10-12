# ALEX 로컬 모델 코딩 비교 실측 (2026-10-12)

클라우드가 만든 18과제 비교 도구(`scripts/compare_python.py`, 기본·어려움·전문가 각 6)를 사용자 PC 에서 처음 실제로 돌렸다.
GPT-5.6 Luna 쪽은 PC 에 OpenAI API 키가 없어 **아직 측정하지 않았다**(아래 '남은 일').

## 조건

- 모델: Strata `qwen3.8-flash-next`(Qwen3.8-Flash-Next 125B MoE, IQ3_S, RTX 5080 16GB + RAM), 생각 켬, 답 최대 8,192토큰, 생성 180초 제한.
- 과제마다 대화 기록 없이 한 번에 생성, 코드는 격리 프로세스·실행 보호 안에서 실제 검사로 채점. 과제 묶음 해시 `8eaa2dae77ca…`.
- 반복 3회(과제당 3번, 모두 54회 생성).

## 결과

| 묶음 | 통과 | 시간(중앙값) | 최대 |
|---|---:|---:|---:|
| 기본 6 × 3 | 18/18 | 3.4초 | 11.5초 |
| 어려움 6 × 3 | 18/18 | 6.5초 | 14.3초 |
| 전문가 6 × 3 | 18/18 | 7.1초 | 24.5초 |
| **합계** | **54/54** | 5.6초 | 24.5초 (54개 합 391초) |

- 모든 답이 코드 블록으로 나왔다(추출 실패 0, 생성 오류 0).
- **이 18과제는 이 모델에게 너무 쉽다(천장).** 정답률로는 모델을 가를 수 없다 — GPT-5.6 Luna 와 비교해도 주로 속도 차이만 보일 가능성이 크다. 의미 있는 비교에는 더 어려운 과제(ALEX 의 RTL 과제·/build 과제·여러 파일 수정)가 필요하다.

## 평가 도구에서 찾아 고친 것

- 첫 실측에서 `csv_summary` 가 **모델과 상관없이 항상 실패**했다. 과제의 준비 코드가 `tempfile.mkdtemp()` 로 입력 CSV 를 만드는데,
  이 함수는 쓸 수 있는 폴더인지 보려고 시험 파일을 만들고 지운다(os.remove). 실행 보호는 지우기를 모두 막으므로 준비 단계에서 실패했다.
  클라우드 검증은 다른 과제로만 확인해 놓쳤다.
- 수정: 준비 코드(신뢰된 검사 코드)를 실행 보호를 켜기 전에 돌리고, 모델 답과 검사는 그대로 보호 안에서 돌린다.
  회귀 시험 추가(`tests/test_model_compare.py`: 올바른 답 통과·빈 답 실패·답안이 파일을 지우면 실패). 첫 실행의 답을 고친 채점기로 다시 매기면 18/18.

## 함께 드러난 ALEX 문제 (결정 필요, 고치지 않음)

ALEX 의 코드 실행 확인(만들기 /build·코드 답 확인)도 같은 보호를 쓴다. 보호 안에서 `tempfile.mkdtemp()` 는 실패하고(작업 폴더에 시험 파일이 쌓임),
`TemporaryDirectory()` 는 정리할 때 실패한다. 임시 파일을 쓰는 올바른 프로그램이 실패로 보일 수 있다 — ALEX 가 맞는 코드를 '고치려' 들 수 있다.
지우기 금지는 의도된 설계(시험 코드가 작업 파일을 지우지 못하게)라서 바로 풀지 않았다. 제안: 실행마다 `작업폴더/.alex-tmp` 를 TEMP 로 주고,
**그 안에서만** 지우기·이름 바꾸기를 허용(작업 파일·폴더 밖은 지금처럼 금지, 생성 코드의 명시적 os.remove 는 정적 검사로 계속 거절).

## 남은 일: GPT-5.6 Luna

- API 키로: PC 환경변수 `OPENAI_API_KEY` 를 둔 뒤
  `python scripts/compare_python.py --provider openai --model gpt-5.6-luna --execute --runs 3 --out logs/model-compare/luna56.json`
- 키 없이: `logs/model-compare/answers-template-gpt56luna.json`(과제 18개·해시 포함)의 `reply` 를 GPT-5.6 Luna 의 실제 답으로 채워
  `python scripts/compare_python.py --answers 그파일 --execute --out logs/model-compare/luna56-imported.json` 로 같은 채점기에 넣는다.
  이때 모델 이름은 제공자가 적은 값이며 도구가 확인한 값이 아니다.
- 결과 파일: `logs/model-compare/alex-strata-runs3.json`(3회), `alex-strata-run1.json`(첫 실행)·`alex-strata-run1-rescored.json`(고친 채점기로 다시 매김).
