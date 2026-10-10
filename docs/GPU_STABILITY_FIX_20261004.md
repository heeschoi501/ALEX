# ALEX GPU 장애 조사와 보호 로직 수정

## 2026-10-04 23:40 KST Codex — 사용자 요청으로 1.5GiB 예약 제거, 반환 확인 유지

- 최신 지시: "1.5GB 여유는 빼고 반환 확인만 유지". 아래 이전 인계의 1.5GiB 예약 적용/유지 지시보다 이 항목이 우선한다.
- `jarvis/launcher.py`의 최소 `OLLAMA_GPU_OVERHEAD` 강제 지정과 `jarvis/imagegen.py`의 `--reserve-vram 1.5` 인자를 제거했다. 엔진 기본 동작 및 사용자가 별도로 설정한 환경변수는 유지한다. 새 16GB 강제 상한을 구현한 것은 아니다.
- Ollama 언로드 실패·잔류 모델 차단, ComfyUI 대기열·예약 메모리 반환 확인, 잘못된 응답 차단, 일반/스트리밍/모델 변경 후 보호는 모두 유지한다.
- 관련 검사 **92개 PASS** (`logs/codex-gpu-stability-20261004/remove-reserve-tests.txt`). 이번에는 전체 검사를 재실행하지 않았고 실GPU 기동·추론·서버 재시작도 하지 않았다. 16GB 이하 사용만으로 장애가 예방된다는 것은 미입증이다.
- 백업 `logs/codex-gpu-stability-20261004/remove-reserve-baseline/`, 변경 `remove-reserve.diff`. GPU 평가 보류 및 기존 전환 보호 유지.

아래는 23:37 최초 수정 기록이며, VRAM 예약 부분은 위 최신 지시로 철회됐다.

작성: 2026-10-04 23:32 KST, Codex. 사용자 요청: ALEX 실행 후 팬 과속·화면 꺼짐 재발 원인 조사와 해결.

## 확인한 것과 미확정인 것

- 이전 실행에서 `nvidia-smi`가 `GPU is lost`를 반환했다. CPU 사용량만의 문제가 아니라 GPU/드라이버가 정상 응답하지 못한 상태였다.
- Windows 원본 이벤트에서 21:21 nvlddmkm의 Resetting/Reset/Restarting TDR 기록을 확인했다. Windows 이벤트 ID 153을 NVIDIA Xid 153으로 해석하지 않는다.
- Claude 인계에는 21:20 전후 VRAM 한계 평가와 이미지 생성이 겹쳤고, 이후 이미지 단독 실행과 모델 적재 전 GPU 탐색 단계에서도 갑작스런 종료가 있었다고 기록돼 있다. ComfyUI 로그에 `Fatal Python error: Aborted`가 있다. 이것만으로 드라이버·CUDA·장비 중 근본 원인을 특정할 수 없다.
- VRAM 한계 사용과 동시 적재는 실제 위험 요인이지만, 모든 장애를 설명하는 확정 원인은 아니다. 짧은 게임 실행 성공, 낮은 대기 온도, 전원 재연결 뒤 정상 조회만으로 원인 해결이나 하드웨어 정상 여부를 확정하지 않는다. 이전 인계의 단정적인 결론은 가설로 읽어야 한다.
- 조사 시작 시 ALEX/Ollama/ComfyUI 포트 8765/11434/8188 리스너 없음. GPU는 43°C·1%·약22W·1368/16303MiB로 조회됐다. 이는 대기 상태 확인이며 부하 안정성 검증이 아니다.

## 수정한 보호 결함

1. `jarvis/core.py`: 이미지/영상 실행 전 Ollama 언로드 실패를 기록만 하고 계속하던 경로를 차단했다. 요청 성공 후에도 `/api/ps`에 모델이 남으면 생성하지 않는다. 실패는 사용자에게 설명하고 실패 상태로 기록한다.
2. `jarvis/providers.py`: 잘못된 모델 상태 응답을 빈 목록으로 처리하지 않는다. 일반/스트리밍 대화 요청 전에 미디어 엔진 반환 확인을 호출한다. `core.py`와 `web.py`에서 초기화 및 런타임 모델 변경 시 일반·코딩·요약 Provider 모두에 연결한다.
3. `jarvis/imagegen.py`: ComfyUI `/free`는 비동기 요청 접수이므로 HTTP 성공만으로 반환 완료라고 보지 않는다. 대기/실행 중 미디어 작업이 있으면 LLM 요청을 막고, 작업이 없을 때 PyTorch 예약 메모리를 확인·반환 요청한다. 큰 예약이 남거나 응답이 잘못되거나 시간 초과면 다음 LLM을 실행하지 않는다. 연결 거절만 엔진 종료 상태로 취급한다. 검사 기준은 예약 메모리 64MiB 이하이며 CUDA 컨텍스트 자체가 없어졌다는 보장은 아니다.
4. `jarvis/launcher.py`: 데스크톱에서 런처가 시작하는 Ollama에 최소 1.5GiB `OLLAMA_GPU_OVERHEAD`를 지정한다. 더 큰 기존 예약은 유지한다. ALEX가 시작하는 ComfyUI에는 `--reserve-vram 1.5`를 전달한다.

## 적용 범위와 한계

- 예약값은 엔진의 메모리 배치 여유이며 실제 VRAM 사용량의 강제 상한이 아니다. 오프로딩 증가로 CPU 사용량·지연이 늘 수 있다. 이미 실행 중인 엔진에는 시작 인자가 소급 적용되지 않는다.
- ALEX의 전환 경로를 보호한다. 외부 평가 스크립트나 다른 앱이 직접 Ollama/ComfyUI를 호출하는 것을 잠그지는 못한다. 상태 확인 직후 외부 작업이 시작되는 경쟁도 남는다. 평가와 일반 사용을 동시에 하지 않는 기존 제약을 유지한다.
- 모델, `jarvis.local.json`, 평가 스크립트, GPU 드라이버·전력·클럭·BIOS 설정은 변경하지 않았다. Q3 평가 및 OOM 한계 탐색 재개 없음.
- 이번에는 ALEX/Ollama/ComfyUI를 시작하지 않았고 실제 GPU 추론도 하지 않았다. 따라서 화면 꺼짐과 팬 과속의 완전 해결은 미검증이다.

## 검증과 보관 위치

- 신규 보호 검사: 실행 중 미디어 차단, 비동기 반환 확인, 잔여 메모리·잘못된 상태·시간 초과 차단, 일반/스트리밍 HTTP 호출 전 차단, Ollama 언로드 실패/잔류 시 이미지·영상 제출 차단, VRAM 예약 인자 확인.
- 런타임 설정 검사 8개 통과. GPU 전환 검사 최종 7개 통과(실GPU 미사용).
- 첫 묶음 검사에서 84개 통과, `test_runtime_settings`는 기존 절대 import 방식 때문에 모듈 직접 실행이 실패했다. discovery 방식으로 다시 실행해 위 8개 통과를 확인했다. 이 최초 실패 로그도 보존했다.
- 최종 전체 release 검사: 1010개 실행, 1005 PASS·5 SKIP, RELEASE PASS (`release-final.txt`). 첫 전체 검사는 실행 중 마지막 코드 보완으로 지문 검사 1개가 실패했으며, 코드 동결 후 전체 재검사로 해소했다.
- `logs/codex-gpu-stability-20261004/`: 변경 전 원본 `baseline/`, 엔진 로그 사본, `windows-events.json`, `changes.diff`, 검사 로그, `worklog.md`.

## 다음 작업자가 이어갈 순서

1. NEXT 최상단의 최종 검사 결과와 이 문서를 읽고, Q3 평가 보류 및 한계 탐색 금지를 유지한다.
2. 실제 GPU 재검증은 별도로 조율한다. 재시작이 필요하면 기존 lifecycle/공식 런처를 사용하고 다른 평가·이미지 작업과 겹치지 않도록 한다. 웹/상태 확인과 작은 단일 작업부터 구분해서 기록하며 장시간 시험을 자동 시작하지 않는다.
3. 재발하면 시각·작업 종류·Windows TDR/종료 이벤트·엔진 로그를 보존한다. 수정으로 보호되는 전환 문제인지, 모델 적재 전 또는 단독 CUDA 실행에서도 나는 장애인지 구분한다. 후자의 근본 원인은 이 코드 수정으로 확정 해결되지 않았다.

## 참고 근거

- 설치된 ComfyUI의 `server.py` `/free`, `/system_stats`, `main.py`의 worker 메모리 반환 처리 및 `comfy/cli_args.py`의 `--reserve-vram` 정의를 읽었다.
- [Ollama 공식 환경 설정 코드](https://github.com/ollama/ollama/blob/main/envconfig/config.go): `OLLAMA_GPU_OVERHEAD`는 GPU별 예약 바이트다.
- [NVIDIA 오류 진단 문서](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html): GPU 오류는 로그와 실행 조건을 함께 확인해야 한다. 이 문서는 해당 PC의 특정 하드웨어 고장을 입증하는 근거가 아니다.
