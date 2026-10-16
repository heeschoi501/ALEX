# ALEX 노트북 인수인계 — Strata / Qwen3.6 (2026-10-17)

## 1. 사용자 요청과 작업 범위

사용자는 Claude가 제안한 설정만 적용하는 데 그치지 않고, ALEX–Strata–Qwen3.6의 실제 구조를 확인해 현재 노트북에 맞게 개선하고 적용하도록 요청했다. 해당 수정·설정 적용·검증·재시작까지 완료했다.

**가장 최근 요청은 인수인계 문서만 작성하는 것이다.** 이 문서를 만드는 단계에서는 코드·설정을 추가로 변경하거나 서비스를 재시작하거나 측정을 더 진행하지 않았다.

- 확정 제품명은 **ALEX**다. JARVIS는 임시 이름이었고, `jarvis` 패키지·설정 파일·환경변수 이름은 기존 호환용으로 남아 있다.
- 작업을 이어갈 때 이 문서와 [상세 적용 기록](STRATA_QWEN36_LAPTOP_20261017.md)을 먼저 읽는다.
- 이전 Claude 인계: [HANDOFF_GPT_20261016_LAPTOP.md](HANDOFF_GPT_20261016_LAPTOP.md).
- 이전 보드 인계 ID: `20261016T145548Z-s8aj`. 보드를 읽는다면 `board read --as gpt --peek`처럼 커서를 소비하지 않는 방법을 쓴다.

## 2. 정확한 작업 위치

| 용도 | Windows 경로 |
|---|---|
| 현재 ALEX 작업본 | `C:\Users\karse\local ai project\ALEX` |
| Strata 소스·서버·엔진 | `C:\Users\karse\local ai project\tools\strata` |
| Strata 모델·토크나이저 | `C:\Users\karse\local ai project\tools\Strata-data` |
| 검증용 Windows Python | `C:\Users\karse\AppData\Local\Programs\Python\Python311\python.exe` |
| Strata 전용 Python | `C:\Users\karse\local ai project\tools\strata\.venv\Scripts\python.exe` |

WSL에서는 위 경로의 `C:\`를 `/mnt/c/`로 바꾸면 된다.

- 오래된 `project_jarvis_v0_2_0` 패키징본에서 이어서 작업하지 않는다.
- 옛 `local ai project\jarvis`는 옛 대화 DB와 Ollama 모델 저장소 보관용이다. 사용자는 옛 대화를 옮기지 않기로 했다.
- `ALEX\runtime`은 옛 `jarvis\runtime`을 가리키는 Windows junction이다. 삭제·복사·이동에 주의한다.
- ALEX 작업 시작 기준 커밋은 `35b755b`, Strata 소스 기준은 `296b59d`였다. **이번 작업은 커밋·푸시하지 않았다.**

## 3. 마지막으로 확인한 실행 상태

다음은 최적화 작업을 마무리하며 실제로 확인한 상태다. 이 문서 작성 시에는 서비스를 다시 조회하거나 변경하지 않았다.

| 항목 | 확인 결과 |
|---|---|
| ALEX | `http://127.0.0.1:8767`, 정상 실행 |
| ALEX 코드 지문 | `80f4369aefbfcab0` |
| Strata | `http://127.0.0.1:8080`, `loaded: true` |
| 모델 이름 | `qwen3.6-35b-a3b-ud-q4_k_xl` |
| 모델 파일 | `Strata-data\models\qwen36-UD-Q4_K_XL\Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf` |
| 모델 크기 | 약 22.9GB |
| 설치 엔진 | Strata 0.1.44.1, CUDA 12 경로 |
| 별도 STRATA 레이아웃 스튜디오 | 포트 8765를 쓰는 별도 프로그램. ALEX 포트와 혼동하지 않는다 |

노트북은 RTX 4070 Laptop 8GB, Ryzen 9 7945HX 16코어, 물리 RAM 총량 약 31.18GiB다. NVIDIA 드라이버는 인계 시 572.70이었다.

## 4. 최종 적용 설정

### Strata

파일: `tools\strata\strata-qwen36-ud-q4_k_xl.json`.

```json
"env": {
  "STRATA_Q36_NO_MMQ": "1"
}
```

기존 옵션 `--max-context 16384 --prefill 512 --mtp 2`를 유지했다. CPU 작업자는 자동 선택된 물리 코어 16개, GPU 전문가 슬롯은 자동 계산이다. 샘플링 값을 추가하지 않아 greedy/MTP 경로를 유지한다.

- 긴 입력의 GPU MMQ 작업 공간 1.10GiB를 없애고 그 자리를 전문가 캐시에 쓴다.
- 실제 엔진 로그에서 전문가 캐시가 **층당 4개 → 19개**로 바뀐 것을 확인했다.
- GPU 사용량 전체가 1.10GiB 줄었다는 뜻이 아니다. 확보한 공간을 전문가가 사용한다.
- 기존 CUDA 바이너리를 사용한다. **C++/CUDA 엔진을 수정하거나 다시 빌드하지 않았다.**
- CUDA Toolkit / Visual Studio Build Tools는 확인한 위치에 없었다. 새 드라이버·모델·엔진을 설치하지 않았다.

### ALEX

파일: `ALEX\jarvis.local.json`.

| 설정 | 적용값 |
|---|---|
| `local_backend` | `strata` |
| `strata_host` | `http://127.0.0.1:8080` |
| `strata_config` | `strata-qwen36-ud-q4_k_xl.json` |
| `strata_model` | `qwen3.6-35b-a3b-ud-q4_k_xl` |
| `strata_num_ctx` | 16384 |
| `strata_max_output_tokens` | 6144 |
| `ollama_think` | true |
| `strata_reasoning_effort` | medium |
| **`strata_reasoning_budget_tokens`** | **1024** |
| `web_port` | 8767 |

실효 입력 예산은 5529자, 최근 대화 한도는 24개, 요청 timeout은 360초였다.

생각 상한 1024는 **ALEX가 서버 요청에 전달하는 값**이다. Strata 설정 파일의 전역 기본값에 1024를 추가한 것은 아니다. 출력 한도 6144에는 생각과 답변이 함께 들어간다. 생각 상한을 줄이면 복잡한 문제의 답변에 영향을 줄 수 있으며, 품질 비교는 하지 않았다.

새 설정의 코드 기본값은 0이다. 0은 기존 클라이언트 차단 방식이며, 다른 컴퓨터에 1024를 강제하지 않는다. 설정 화면에서 조절할 수 있다.

## 5. 구조를 확인하고 수정한 내용

실행 경로:

```text
ALEX StrataProvider
  → HTTP /v1/chat/completions
  → Strata serve/server.py
  → GEN 파이프
  → src/qwen35/q36_engine.cu
  → GPU 공통 가중치·MTP·전문가 캐시 + CPU/RAM 전문가
```

Qwen3.6은 Flash-Next와 별도 경로다. Flash-Next의 전문가 pack·저RAM 모드·프로필 파일을 Qwen도 사용한다고 가정하면 안 된다. 전문가 **18.75GiB 전체를 호스트 RAM에 복사**하며, 이 노트북에서는 고정 메모리 확보에 실패해 pageable RAM으로 실행됐다.

| 파일 | 이번 변경 |
|---|---|
| Strata `setup.py` | `--families` 확인에도 CUDA 라이브러리 경로를 전달. 수동 PATH 우회 없이 Qwen 지원 인식 |
| Strata `tools/test_setup_qwen36.py` | DLL 경로 전달·부모 환경 보존·이전 엔진 fallback 검사 |
| ALEX `jarvis/strata_runtime.py` (신규) | 실제 서버의 모델 이름·문맥 용량을 검사하는 공통 함수 |
| `jarvis/launcher.py`, `model_switch.py` | 사용할 서버의 모델·문맥 검증. 비활성 서버는 GPU 반환 목적으로 모델 일치와 무관하게 추적 |
| `jarvis/providers.py` | 서버 생각 상한 전달, 실제 모델 목록 표시, 엔진 타이밍 기반 속도 표시, 잘못된 타이밍 값 방어 |
| `jarvis/opencode_worker.py` | OpenCode도 실제 Strata 모델·문맥 검사 |
| `jarvis/config.py`, `app.py`, `web.py` | 생각 상한 로딩·검증·저장·실행 전달·실효값 표시 |
| `jarvis/static/app.js`, `index.html` | 설정 화면에서 생각 토큰 상한 조절 |
| `scripts/measure_strata.py` (신규) | 상태 확인 후 합성 요청을 순차 실행하고 타이밍·메모리·출력 해시만 기록 |
| 관련 테스트 3개 파일 | `test_strata_provider.py`, `test_strata_handoff.py`, `test_model_switch.py` 보완 |
| 문서 | 상세 적용 기록, 경험 JSON, CHANGELOG, 이전 인계 문서의 최신 기록 안내 |

추가로 확인한 사실:

- 설치된 Qwen 템플릿을 low/medium/xhigh로 렌더링한 결과의 SHA-256이 모두 같았다. `enable_thinking=false`는 프롬프트를 바꾼다. 이 설치에서는 medium→low 변경만으로 생각이 줄어든다고 주장할 수 없다.
- Qwen 엔진의 샘플링/penalty 경로는 MTP를 사용하지 않는다. 권장 temperature를 무조건 추가하면 속도가 달라질 수 있다.
- 서버는 `reasoning_budget_tokens`에서 생각 종료를 넣고 같은 요청으로 답변을 이어 쓸 수 있다. 클라이언트가 요청을 중단하고 처음부터 재시도하는 비용을 피하도록 이 기능을 연결했다.
- 기존 속도 계산은 생각 토큰까지 포함한 수를 답변 표시 시간으로만 나눠 과대 표시할 수 있었다. 지금은 Strata의 `predicted_per_second`, `prompt_ms`, `predicted_ms` 등을 사용한다.

## 6. 검증 결과와 근거 파일

모든 실행 검증은 Windows Python 3.11로 했다.

| 검사 | 결과 | 기록 |
|---|---|---|
| 전체 릴리스 | **1372개 OK, 7 skipped, RELEASE VERIFICATION PASS** | `logs/verify-strata-20261017.txt` |
| 최종 연결 변경 영향 검사 | **48개 OK** | `logs/test-strata-final-20261017.txt` |
| Strata 설치 검사 | **14개 OK** | `logs/test-strata-setup-20261017.txt` |
| Strata 서버 생각/effort 경로 | **33개 OK** | `logs/test-strata-server-20261017.txt` |
| 실제 지원 모델 조회 | `qwen35moe`, `qwen4exp` | CUDA DLL 경로를 함수가 전달해 성공 |
| JS 문법·변경분 공백 | 통과 | Node `--check`, Git diff 검사 |
| ALEX 재시작 | 완료 | `logs/alex-final-start-20261017.txt` |
| 최종 응답 연결 | 정상 종료·빈 답 아님·엔진 타이밍 수신 | `logs/strata-alex-final-smoke-20261017.json` |

최종 연결 확인은 출력 128·생각 상한 32로 제한한 별도 요청이었다. 서버 로그에서 `thinking budget reached (32 tokens)`를 확인했다. 이 확인 요청을 사용자 대화 DB에 넣거나 답변 품질을 평가하지 않았다.

전체 검사의 첫 실행은 unloaded 상태·모델 이름 경계 처리에서 실패했고 보완 후 통과했다. 첫 실패 로그는 `logs/verify-strata-20261017-first.txt`에 남아 있다.

## 7. 성능 관측과 해석 한계

Thinking 끔, greedy, 출력 64토큰, 예열 후 혼합 합성 입력을 각 조합 1회 측정했다. 아래 요청의 prompt cache 재사용은 0이다.

| 입력 토큰 | MMQ 켬 첫 내용 / 전체 | MMQ 끔 첫 내용 / 전체 |
|---:|---:|---:|
| 123 | 0.782 / 2.172초 | 0.594 / 2.031초 |
| 739 | 4.656 / 5.938초 | 2.109 / 3.390초 |
| 2131 | 8.719 / 10.235초 | 6.000 / 7.359초 |

근거: `logs/strata-mmq512-mixed-warm-20261017.json`, `logs/strata-no-mmq512-mixed-warm-20261017.json`.

- RAM 여유가 기준선 0.18~0.62GiB, 후보 약 0.94GiB로 달랐다. 다른 프로그램과 Windows 메모리 압박을 완전히 통제한 실험이 아니다.
- 짧은 요청의 생성 속도가 모두 빨라진 것은 아니다. 일반 대화 전체의 속도 향상이나 품질 향상으로 일반화하지 않는다.
- 반복 숫자 출력은 MTP에 특히 유리하므로 그 수치를 일반 생성 속도로 부르면 안 된다.
- 최종 후보 설정의 재시작은 70.062초였으나 시작 시간도 RAM 상태에 크게 영향받는다.
- 노트북의 RAM 부족 문제는 남아 있다. 전문가 18.75GiB 복사를 없애지는 못했다.

## 8. 경험 기록 — 학습 완료로 표현하지 말 것

사용자가 개발 작업도 ALEX가 배울 수 있게 기록하도록 요청한 것을 반영했다.

- 입력: `docs/experiences/20261017_strata_laptop.json`.
- 기존 `scripts/experiences.py add --file ... --write` 경로로 **2건** 저장했다.
- ID: `4b24088fc8360ea429bdb03f`, `df43644e923b37c0cb9a4a5c`.
- 상태: **raw / unrated**. 내용 해시 무결성 확인.
- 기록: `logs/experience-strata-20261017.txt`.
- 승인·학습용 내보내기·모델 재학습을 하지 않았다. split을 수동 지정하지 않았다.

## 9. 미커밋 변경과 복구

**Claude의 기존 미커밋 변경과 이번 변경이 같은 작업 트리에 있다.** 전부 이번 작업에서 만든 것으로 취급하거나 `git reset --hard`로 정리하면 안 된다.

WSL Git은 CRLF 때문에 변경을 과대 표시할 수 있다. Windows Git 또는 다음처럼 확인한다.

```text
git -c core.autocrlf=true status --short
git -c core.autocrlf=true -c core.whitespace=cr-at-eol diff --check
```

- 작업 시작 시 백업: `ALEX\logs\before-strata-20261017\`.
- 이 백업에는 Claude가 이미 수정한 상태도 포함된다. 저장소 HEAD와 같다고 가정하지 않는다.
- Strata 파일 백업은 그 아래 `strata\`에 있다.
- 이전 백업: `strata-qwen36-ud-q4_k_xl.json.orig-20261016`, ALEX `logs\jarvis.local.json.bak-20261016-*`.
- `jarvis.local.json`, `logs\`, `runtime` junction은 커밋 대상으로 일괄 추가하지 않는다.
- **커밋·푸시 없음. 푸시는 사용자 확인 뒤에만 한다.**

가장 작은 설정 복구:

1. Strata 설정에서 `env.STRATA_Q36_NO_MMQ` 키를 **삭제**하고 작업이 없을 때 Strata 재시작.
2. 값을 `0`으로 바꾸면 안 된다. 엔진이 환경변수의 값이 아니라 존재 여부를 검사한다.
3. ALEX 생각 상한은 설정 화면에서 0으로 바꾸면 기존 동작으로 돌아간다.

## 10. 서비스 조작 시 지킬 것

ALEX 폴더에서 지정된 Windows Python으로 `-m jarvis.lifecycle status|start|stop|restart`를 사용한다. 실행 중인 작업이 있으면 종료를 강행하지 않는다.

Strata를 종료할 때는 작업·대기열이 없는지 확인하고 `/unload`로 모델을 내린 뒤 **이 설치의 서버 프로세스와 엔진만** 종료한다. 모든 `python.exe`를 일괄 종료하면 안 된다. `.venv` 래퍼와 실제 Python 서버가 별도 프로세스로 보일 수 있다.

Strata 폴더에서의 실행 명령 형태:

```bat
.venv\Scripts\python.exe serve\server.py --engine strata --config strata-qwen36-ud-q4_k_xl.json --port 8080
```

현재 서버가 떠 있는데 중복 실행하지 않는다. ALEX와 Strata는 loopback으로만 제공한다.

## 11. 다음 작업과 사용자 규칙

- **현재 요청은 문서 작성만이다. 추가 개발·측정은 새 요청을 받은 뒤 진행한다.**
- 노트북에서는 실행 여부·속도·메모리만 확인한다. 품질 시험(replay, 한국어 정답 평가, 모델 간 코딩 품질 비교)은 데스크톱에서 한다.
- 측정 전 사용자에게 알리고 실효 `/status` 또는 인증된 `/api/state`를 먼저 확인한다. GPU 측정을 겹치지 않는다.
- 검증은 지정된 Windows Python으로 한다. `.cmd`에는 한글을 넣지 않는다.
- 사용자 대화 데이터를 바꾸거나 경험을 승인하거나 split을 수동 지정하지 않는다.
- RAM 절감을 위한 Qwen 전용 디스크 pack·저RAM 실행·전문가 순위 저장은 별도 엔진 개발·빌드·검증이 필요한 후속 과제다. 이번에 구현한 것으로 표현하지 않는다.
- 생각 상한 1024의 답변 품질 영향, 샘플링 적용과 MTP 손실의 교환 관계, 더 긴 입력에서의 성능은 아직 평가하지 않았다.

상세 소스 근거·전체 측정 로그 목록·복구 설명은 [STRATA_QWEN36_LAPTOP_20261017.md](STRATA_QWEN36_LAPTOP_20261017.md)를 참고한다.
