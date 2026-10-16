# GPT(Codex) 인계 — 노트북 ALEX 2026-10-16 (클로드)

> 후속 작업(2026-10-17): 사용자가 전체 구조를 확인해 노트북 최적화를 적용하도록 요청했다.
> 아래의 미결 상태는 10월 16일 기록이다. 최신 설정·측정·복구 방법은
> [STRATA_QWEN36_LAPTOP_20261017.md](STRATA_QWEN36_LAPTOP_20261017.md)를 먼저 확인할 것.

노트북: RTX 4070 Laptop 8GB · RAM 31.2GB · Ryzen 9 7945HX · 드라이버 572.70. 이 문서는 노트북 작업본 기준이다.

## 0. 먼저 알 것

- **노트북 작업본이 바뀌었다:** 깃허브 `heeschoi501/ALEX`(35b755b)를 받은 `C:\Users\karse\local ai project\ALEX`.
  옛 `local ai project\jarvis` 는 옛 대화 DB(`jarvis\data`)와 Ollama 모델 저장소만 보관한다. 사용자가 옛 대화는 옮기지 않기로 했다.
  `ALEX\runtime` 은 `jarvis\runtime` 으로 잇는 디렉터리 연결(junction)이다.
- **커밋·푸시 안 함.** 아래 변경은 모두 작업 트리에만 있다. 푸시 여부는 사용자가 아직 답하지 않았다.
- WSL 의 git 으로 보면 CRLF 때문에 263개 파일이 수정된 것처럼 보인다. Windows git 이나 `git -c core.autocrlf=true status` 로 볼 것.
  실제 변경은 아래 목록뿐이다.
- 기준 ALEX(대화·기억·경험 DB)는 데스크톱이다. 사용자는 **나중에 데스크톱 내용을 Mac Studio 로 옮길 계획**이다.
  지금 ALEX 실행부(.vbs/.cmd/.ps1, runtime\ollama.exe, Strata CUDA 엔진)는 Windows 전용이다.

## 1. 지금 켜져 있는 것

| 서비스 | 상태 |
|---|---|
| ALEX 웹 | 127.0.0.1:**8767**(코드 지문 a78989a78038599e), `local_backend: strata` |
| Strata | 127.0.0.1:8080, Qwen3.6-35B-A3B UD-Q4_K_XL, 문맥 16384, 창 없이 떠 있음 |
| Ollama | 켜져 있음, 올라간 모델 없음 |
| STRATA 레이아웃 스튜디오(node, 8765) | 재부팅 뒤 꺼져 있음. 다시 켜도 ALEX 는 8767 이라 충돌 없음 |

Strata 는 창이 없어 직접 꺼야 한다: `strata.exe` 와 `server.py ... --engine strata` 인 python 프로세스를 함께 종료.
Strata 가 떠 있으면 여유 RAM 이 1GB 밑으로 떨어진다.

## 2. 한 일(검증한 것만)

1. **웹 포트 설정 `web_port`**(jarvis.local.json·`JARVIS_WEB_PORT`, 기본 8765). 노트북 8765 를 STRATA 스튜디오가 써서 ALEX 가 안 켜졌다.
   런처·웹 서버·`jarvis.lifecycle` 이 이 값을 쓴다. 1024 미만과 `remote_local_port`(8766)는 거절. 노트북은 8767.
   `tests/test_web_port.py` 8개.
2. **노트북 20b 기준선**(PERFORMANCE 40절): 오염 replay 0/3, fresh 2/4(없는 `--cloud` 옵션을 지어냄), 대화 중앙값 40.8초.
   데스크톱 20b 와 같은 약점이다.
3. **Ollama 모델 정리**(사용자 결정): gpt-oss:20b·qwen3:8b 만 남기고 7개 삭제(약 73GB). 코드 기본값도 남은 모델로 맞췄다.
   노트북 general=coder=qwen3:8b, 데스크톱 기본 gpt-oss:20b. 실제 사용 모델은 jarvis.local.json 이 정한다.
   test_app·test_config·test_scripts·README·스크립트 사용 예시를 맞춤.
4. **Strata 설치**(`local ai project\tools\strata`, ALEX 기본 strata_dir; 모델은 `tools\Strata-data`).
   드라이버가 580 미만이라 `--cuda 12`(실험용 CUDA 12.9 엔진 0.1.44.1)를 썼다.
   - Flash-Next **Coder IQ1_M**: 31GB RAM 에 들어가는 유일한 Flash-Next 크기다(저RAM 모드). 30초에 켜지고 약 15 tok/s.
     **한국어가 깨진다**(n=1, "의의의…" 반복). 원인은 아래 6-③ greedy 일 가능성이 있다.
   - **Qwen3.6-35B-A3B UD-Q4_K_XL**(22.9GB, SHA-256 확인): 켜진다, 약 15 tok/s, 한국어 한 문장 정상(n=1).
   - Strata setup 버그: 지원 모델을 물을 때 `strata.exe --families` 를 CUDA 12 라이브러리 경로 없이 실행해서
     "이 엔진은 Qwen3.6 을 못 돌린다"로 멈춘다. PATH 에 `.venv\Lib\site-packages\nvidia\{cublas,cuda_runtime}\bin` 을 넣고 돌려 우회했다.
     Strata 코드는 고치지 않았다.
5. **설정 화면의 Strata 전환이 막히던 것을 고침:**
   - Strata 모델이 둘이면 `strata_command` 가 "여러 개" 오류를 냈고, 고를 곳이 환경변수뿐이었다.
     → 설정 `strata_config`(jarvis.local.json·`JARVIS_STRATA_CONFIG`)를 추가. 런처·`check_backend_install`·`start_backend` 가 모두 이 값을 쓴다.
   - 전환 때 Strata 를 기다리는 시간 240초 → 900초(런처와 같게). Qwen3.6 이 RAM 압박 속에서 275초 걸렸다.
   - 테스트 2개(test_strata_provider).
6. **Qwen3.6 이 느린 원인과 설정 조정**(`docs/STRATA_LAPTOP_QWEN36_20261016.md` 에 전부 정리):
   - ① 엔진 로그 `hybrid experts: 0 of 256 per layer in VRAM` — 버퍼를 잡고 나니 VRAM 이 0.67GiB 만 남았다.
     Qwen3.6 경로는 여유 768MiB 를 고정으로 빼고, 큰 입력 조각용 작업 공간 1.10GiB(한 층 전문가 × 2)를 잡는다.
     → Strata 설정 `strata-qwen36-ud-q4_k_xl.json` 을 `--max-context 16384 --prefill 512` 로(원본 `.orig-20261016`).
     ALEX `strata_num_ctx: 16384`(입력 예산 5,529자). 결과: 남은 VRAM 2.16GiB, **4/256 개/층**, 배치 66초 + 준비 12초.
   - ② 전문가 18.75GiB 가 고정(pinned) 메모리에 못 올라가 일반 메모리로 간다. RAM 이 모자라면 디스크로 밀린다.
   - ③ ALEX 의 `StrataProvider` 는 temperature 등 샘플링 값을 보내지 않는다. Strata 설정에도 `sampling` 블록이 없어 **greedy** 로 돈다
     (데스크톱 Flash-Next 도 같음). Qwen 공식 권장값: 생각 모드 1.0/0.95/top_k 20, 생각 끔 0.7/0.8/top_k 20 + presence_penalty 1.5.
     모델 카드에 따르면 presence_penalty 는 끝없는 반복을 줄인다.
7. 검증: Windows Python 3.11 `scripts/verify_release.py` **1,365개 OK(7 skip), RELEASE VERIFICATION PASS**(`logs/verify-20261016-strata-config.txt`).

## 3. 다음(사용자 결정이 먼저인 것 표시)

1. **[결정] 깃허브 푸시 여부.** 커밋 전에 아래 변경 목록을 Windows git 으로 다시 확인할 것.
2. **[결정] `STRATA_Q36_NO_MMQ=1`** 을 Strata 설정의 `"env"` 블록에 넣을지. 작업 공간 1.10GiB 를 돌려받아 약 19개/층이 된다.
   대신 긴 입력을 CPU 가 더 맡는다. 효과는 재지 않은 추정이다.
3. **[결정] 샘플링 권장값을 StrataProvider 에 넣을지.** 데스크톱 답에도 영향이 가므로 데스크톱 측정과 함께 정하는 게 맞다.
   넣으면 Coder 한국어 반복이 greedy 때문이었는지도 다시 볼 수 있다.
4. **[결정] 노트북 Strata 의 생각 정도**(`strata_reasoning_effort` medium → low).
5. Qwen3.6 엔진 개선은 Strata 쪽 일이다(엔진 재빌드 필요). 순위 목록 저장·적재가 가장 작은 일이고, 디스크 묶음과 저RAM 모드가 다음이다.
   위 4-의 setup 버그와 함께 Strata 에 이슈·PR 로 제안하는 편이 맞다.
6. 모델 품질 비교(Qwen3.6 vs Flash-Next vs 27b, replay·한국어)는 **데스크톱에서** 한다.

## 4. 주의

- **사용자 규칙:** 노트북에서는 실행 여부·속도·메모리만 잰다(품질 시험은 데스크톱). 사용자가 "속도 재지 말고 수정만"이라고 한 적이 있다.
  측정 전에 사용자에게 알린다(노트북을 쓰는 중일 수 있다).
- 사용자 대화 데이터를 바꾸는 결정은 사용자 승인 뒤에. 경험(experiences)을 승인하지 않는다. split 을 지정하지 않는다.
- 검증은 Windows Python(`C:\Users\karse\AppData\Local\Programs\Python\Python311\python.exe`)만 쓴다.
  `.cmd` 에는 한글을 넣지 않는다. 보드는 `--peek` 로 읽는다. 측정은 겹치지 않는다. 재기 전에 `/status`(실효 설정)를 읽는다.
- Strata 를 켜면 RAM 이 바닥난다(Qwen3.6: 다른 프로그램이 켜져 있으면 최저 0.1GB). 켜기 전에 사용자에게 다른 프로그램을 닫게 하면 좋다.
- 설정 백업: `logs\jarvis.local.json.bak-20261016-strata`, `logs\jarvis.local.json.bak-20261016-ctx16k`,
  `tools\strata\strata-qwen36-ud-q4_k_xl.json.orig-20261016`.

## 5. 변경 파일(커밋 전)

수정: CHANGELOG.md, PERFORMANCE.md(40·41절), README.md, jarvis/config.py, jarvis/launcher.py, jarvis/lifecycle.py,
jarvis/model_switch.py, jarvis/web.py, scripts/grade_codegen.py, scripts/measure_offload.py, scripts/tune_gpu_layers.py,
tests/test_app.py, tests/test_config.py, tests/test_launcher_stale.py, tests/test_scripts.py, tests/test_strata_provider.py.
새 파일: tests/test_web_port.py, docs/STRATA_LAPTOP_QWEN36_20261016.md, 이 문서.
깃에 넣지 않을 것: `runtime`(junction), `jarvis.local.json`, `logs/`.

## 6. 참조

docs/STRATA_LAPTOP_QWEN36_20261016.md · PERFORMANCE.md 40·41절 · CHANGELOG.md 2026-10-16 ·
logs/laptop-baseline-20261016.txt · logs/replay-laptop-*-gpt-oss_20b.json · logs/verify-20261016-strata-config.txt ·
tools/strata/strata-coder-iq1_m.log · tools/strata/strata-qwen36-ud-q4_k_xl.log · tools/strata/src/qwen35/q36_engine.cu(place_experts, rebalance)
