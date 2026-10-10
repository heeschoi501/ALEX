# Project JARVIS v0.2.0 — Real Hardware Validation Sheet

이 문서는 Claude Code가 사용자 PC에서 실제 수치를 기록하기 위한 체크시트다. **추정값을 쓰지 않는다.**

## A. RTX 4070 Laptop

### Environment
```text
Date:            2026-09-13 (실측), 2026-09-19 문서 반영
OS:              Windows-10-10.0.26200-SP0
Execution:       Windows native
Python:          3.11.8 (C:\Users\<user>\AppData\Local\Programs\Python\Python311)
CPU:             AMD64 Family 25 Model 97 Stepping 2 (AuthenticAMD)
GPU detected:    NVIDIA GeForce RTX 4070 Laptop GPU (driver 572.70, CUDA 12.8)
VRAM detected:   8.0 GB (가용 약 6.9 GB)
RAM detected:    31.2 GB
Auto profile:    LAPTOP
Ollama version:  0.34.0 (번들, runtime/ollama/ollama.exe)
Ollama host:     127.0.0.1:11434
```

출처: `logs/real-validation-qwen3_8b-q4_K_M.json`, `logs/real-validation-qwen3.5_9b-q4_K_M.json`.
**이 표의 값은 전부 실측이며 추정치가 없다.**

### 모델별 E2E 결과

| 모델 | 통과 | 비고 |
|---|---|---|
| `qwen3:8b-q4_K_M` | **11/11** | — |
| `qwen3.5:9b-q4_K_M` | 10/11 | `file_analysis` 1회 FAIL |

> **주의 — 9b 의 FAIL 을 그대로 읽지 마십시오.** 같은 검사를 5회 반복하니 5/5 였습니다.
> 디스크의 그 JSON 은 표본 1개의 편차이며 모델의 결함이 아닙니다.
> `PERFORMANCE.md` 의 "표본 1개로 모델을 판단하지 말 것" 과 같은 사례입니다.

### VRAM 실측 (2026-09-13 · 2026-09-18 재확인)

| 모델 | num_ctx | GPU 분할 |
|---|---|---|
| `qwen3:8b-q4_K_M` | 8192 | **100% GPU** |
| `qwen3.5:9b-q4_K_M` | 8192 | 12% CPU / 88% GPU |
| `qwen3.5:9b-q4_K_M` | 4096 | 100% GPU |

2026-09-18 측정: 12% 오프로딩의 처리량 저하는 **약 18%** (46.6 → 38.0 tok/s).
`PERFORMANCE.md` 초기본의 "6.6배" 는 재현되지 않았습니다 — 같은 문서의 정정 항 참조.

Commands:

```bash
python scripts/verify_release.py
python scripts/check_environment.py
python scripts/setup_models.py
```

### Model preparation

Dry-run에서 표시된 exact model tag를 기록한 뒤 사용자 의사에 따라:

```bash
python scripts/setup_models.py --pull
```

### Benchmark

```bash
python scripts/benchmark_local.py --repeat 3
```

```text
General exact model:
Run 1: first-content= / total= / eval tok/s= / load= / VRAM before/after=
Run 2:
Run 3:
Warm/cold note:
```

### Functional Alpha
- [ ] Korean general chat
- [ ] Python list/dict function
- [ ] Regex
- [ ] Pandas explanation/code
- [ ] SQL explanation/code
- [ ] `/profile`
- [ ] `/doctor`
- [ ] `/remember` -> restart -> `/recall`
- [ ] `/ask-file`
- [ ] `/ask-project`
- [ ] 30+ turns -> `/summary`
- [ ] session resume
- [ ] `/backup`
- [ ] `python -m jarvis.web` local browser chat

### Laptop decision after measurement
```text
Single model sufficient? Y/N + evidence:
Need separate coder? Y/N + evidence:
Thinking ON worth it? Y/N + evidence:
Context/output tuning needed? + evidence:
```

## B. RTX 5080 Desktop — 미측정

**이 절의 값은 하나도 재지 않았다. 아래는 채우는 순서다.**

> **먼저 알아야 할 것:** `jarvis.local.json` 의 `num_ctx` / `local_max_output_tokens` /
> `request_timeout` 은 **A절 랩탑에서 실측해 정한 값**이다(8GB VRAM / 8B 기준).
> 그대로 Desktop 에 가져가면 16GB 장비를 8GB 기준으로 묶는다 — 특히 `num_ctx` 는
> 설정되어 있으면 `NUM_CTX_BY_PROFILE[DESKTOP]`(16384) 을 덮어쓴다.
>
> 그래서 **Desktop 전용 설정**이 따로 있다. 비어 있으면 앱이 시작할 때 알려준다.
>
> | 설정 | 환경변수 |
> |---|---|
> | `desktop_num_ctx` | `JARVIS_DESKTOP_NUM_CTX` |
> | `desktop_max_output_tokens` | `JARVIS_DESKTOP_MAX_OUTPUT_TOKENS` |
> | `desktop_request_timeout` | `JARVIS_DESKTOP_REQUEST_TIMEOUT` |
>
> **재기 전에 값을 적지 마라.** 랩탑 숫자를 옮겨 적는 것이 이 문서가 막으려는 바로 그 일이다.

### B-1. 환경 기록

```text
Date:            2026-09-25 (claude, 데스크톱에서 직접)
OS:              Windows 11 Pro 10.0.26200. 시스템 로캘(유니코드 미지원 프로그램용) en-US = 코드페이지 1252,
                 표시 문화권 ko-KR. <- 이 조합이 clock_line 의 strftime('%Y년') 을 깨뜨렸다(CHANGELOG 2026-09-25)
Python:          3.11.9 사용자별 설치 %LOCALAPPDATA%\Programs\Python\Python311 (tkinter 8.6)
CPU:             AMD Ryzen 7 9800X3D 8-Core (16 스레드)
GPU detected:    NVIDIA GeForce RTX 5080, 드라이버 591.86 / CUDA 13.1. Ollama: CUDA compute 12.0, cuda_v13
VRAM detected:   15.9 GiB (Ollama 시작 시 available 14.6 GiB - 나머지는 Windows 화면이 쓴다)
RAM detected:    93.6 GiB
Auto profile:    DESKTOP (강제 없음)
Ollama version:  v0.34.4 포터블(runtime/ollama, SHA-256 확인). 모델은 runtime/models
```

원자료: `logs/claude-desktop-environment-20260925.txt`(`check_environment.py`),
`logs/claude-desktop-firstrun-20260925.txt`(첫 실행), `logs/ollama-server.log`(GPU 인식 줄).

```
python scripts/check_environment.py
```

### B-2. `num_ctx` 상한 — VRAM 이 먼저 정한다

A절에서 8B/8192, 9B/4096 이 GPU 100% 였다. Desktop 은 모델도 크므로 반드시 다시 잰다.

```
ollama run qwen3:14b-q4_K_M --verbose "hi"
ollama ps
```

`ollama ps` 의 PROCESSOR 열이 **100% GPU** 인 가장 큰 `num_ctx` 를 찾는다.
`num_ctx` 를 바꿔가며(8192 → 16384 → 32768) 반복한다.

| num_ctx | SIZE | PROCESSOR | 100% GPU? |
|---|---|---|---|
| 8192 | | | |
| 16384 | | | |
| 32768 | | | |

> **CPU 로 넘어가는 순간을 기록하라.** 랩탑에서는 층의 12% 만 CPU 로 가도
> 첫 토큰이 7.0초에서 44.2초가 됐다. 자동 감지가 없다.

### B-3. 출력 예산 — 토큰 분포로 정한다. 평균으로 정하지 마라

```
python scripts/grade_codegen.py --execute -n 10 --think --model qwen3:14b-q4_K_M ^
  --num-ctx <B-2 에서 찾은 값> --num-predict 7000 --timeout 300
```

결과 JSON 에서 **완전통과 회차의 `eval_count` 최댓값**을 본다.

| | 값 |
|---|---|
| 완전통과 회차 `eval_count` 범위 | |
| **완전통과 최댓값** | |
| `done_reason=length` 발생 횟수 | |
| 성공 회차 최장 시간(초) | |

- `desktop_max_output_tokens` = **완전통과 최댓값 + 약 10%**
- `desktop_request_timeout` = **성공 회차 최장 시간 × 약 1.7**

> **왜 최댓값인가:** 랩탑에서 평균만 보고 5120 으로 정했다가, 프롬프트를 바꾼 조건에서
> 5,715 토큰짜리 만점 회차가 나와 그 설정을 망가뜨릴 뻔했다(`PERFORMANCE.md` 13절).
> 그리고 벤치마크 프롬프트는 327 토큰뿐이다 — 실사용 프롬프트는 훨씬 길다.

### B-4. 14B 가 실제로 나은지 확인 — 파라미터 수는 근거가 아니다

랩탑에서 9B 가 8B 보다 **느리고 나빴다**(`PERFORMANCE.md` 5절). 같은 일이 14B 에서
일어나지 않는다는 보장이 없다. 같은 채점기로 나란히 잰다.

| 모델 | 평균 | 완전통과 | 회당 시간 | `done=length` |
|---|---|---|---|---|
| qwen3:14b-q4_K_M | | | | |
| qwen2.5-coder:14b | | | | |
| qwen3:8b-q4_K_M (대조군) | | | | |

> **대조군을 빼먹지 마라.** 8B 가 Desktop 에서도 이기면 `auto_profile_models` 를
> 끄는 것이 답이고, 그것도 유효한 결과다.

### B-5. 정체성이 유지되는지

모델을 바꾸면 **"더 똑똑한가" 와 "여전히 ALEX 다운가" 를 함께 묻는다**(`PROJECT_ALEX.md` 8절).

```
python scripts/grade_identity.py --model qwen3:14b-q4_K_M
```

ALEX-M0 기준선은 **6.0/7** 이다(I-2 만 0/3 실패). 이보다 낮으면 코드 점수가 올랐어도
그대로 채택하지 마라.

### B-6. 전환 비용

```text
General -> Coder switch latency:
VRAM behavior (두 모델 동시 상주 가능한가):
single_model_on_laptop 을 Desktop 에서도 켜야 하는가:
```

## C. Failures

For every failure record:

```text
Command/request:
Expected:
Actual:
Exact error:
Reproducible? Y/N
Native/WSL only?:
Fix/next hypothesis:
```

Do not hide failed runs. They are the data used to decide v0.1.4/v0.2.
