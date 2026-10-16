# 인계 — Strata Qwen3.6 엔진 개선 작업 중 (2026-10-17 00:50, 클로드)

받는 쪽: GPT(Codex) 또는 다음 클로드. **진행 중인 작업**의 인계다. 먼저 읽을 것:
Codex 인계 [HANDOFF_20261017_ALEX_STRATA_LAPTOP.md](HANDOFF_20261017_ALEX_STRATA_LAPTOP.md)(그때까지의 상태와 규칙).

## 0. 이번에 정해진 것(사용자)

- **깃허브:** 노트북 작업은 main 이 아니라 **`laptop` 브랜치**에 올린다. 사용자가 "노트북용으로 따로 만들어 끼워 넣기"를 원했다.
  첫 커밋 `23b3060`(클로드 + Codex 변경 전부, 1,372개 검증 PASS)을 이미 push 했다. main 은 건드리지 않았다.
- **실제 ALEX 대화 속도 측정:** 사용자가 나중에 집에서 직접 한다. 지금은 재지 않는다.
- **엔진 개선(Qwen3.6 경로에 Flash-Next 식 장치 옮기기):** "당장 해봐". 이 문서는 그 작업 도중의 상태다.

## 1. 하는 일 — 왜

노트북(8GB/31GB)에서 Qwen3.6 엔진(`src/qwen35/q36_engine.cu`)은 켤 때마다 전문가 18.75GiB 를 GGUF 에서 다시 묶어
**새 메모리에 복사**한다. 이 복사본이 RAM 을 다 먹고(여유 0.05~0.9GB 관측), 켜는 시간 60~90초도 여기서 나온다.
GGUF 는 이미 파일 매핑으로 열려 있는데, 엔진 커널이 전문가마다 [gate|up|down] 이 붙은 모양(blob)을 원해서 복사한다.

Strata 소스에 넣은 두 가지(**둘 다 환경변수로 켤 때만** 동작하고, 기본 동작은 원래 Strata 와 같다):

1. **`STRATA_Q36_EXPERT_FILE=1`(또는 경로)** — 묶은 blob 을 `<gguf>.q36experts` 파일에 **한 번** 쓴다.
   쓸 때는 18GiB 복사본 없이 흘려 쓰고(`.tmp` 에 쓴 뒤 이름 변경), 이후에는 그 파일을 **읽기 전용으로 매핑**한다.
   바이트가 복사본과 같으므로 출력도 같아야 한다. 머리말(4KB)에 배치 키(GGUF 크기·시각, 층별 stride·bytes·off·type)가 있어,
   맞지 않으면 다시 쓴다. 매핑에 실패하면 경고 후 예전처럼 복사한다.
   기대 효과: 켤 때 묶는 시간 제거, RAM 이 모자라면 OS 가 page file 에 쓰지 않고 버렸다가 파일에서 다시 읽음, 커밋 부담 없음.
2. **`STRATA_Q36_PROFILE=1`(또는 경로)** — 요청이 끝날 때마다(serve 의 DONE 뒤) 엔진의 라우팅 점수를
   `<gguf>.q36profile`(수 KB)에 저장하고, 다음 시작 때 **점수 높은 전문가부터 VRAM 칸을 채운다**(지금은 0번부터).
   GPU 가 계산하는 전문가가 바뀌면 반올림이 달라져 답이 조금 달라질 수 있다(실행 중 교체도 이미 같은 성질).

## 2. 지금 상태 (00:50)

| 항목 | 상태 |
|---|---|
| Strata 소스 수정 | **완료, 아직 컴파일 안 함.** `tools\strata\src\qwen35\q36_engine.cu` +301줄. 패치: `docs/patches/strata-296b59d-q36-expert-file-profile.patch`. 수정 전 원본: `ALEX\logs\q36_engine.cu.orig-20261017` |
| VS 2022 Build Tools(C++) | **설치 성공**(winget, `Microsoft.VisualStudio.2022.BuildTools`, VCTools 워크로드) |
| CUDA Toolkit 12.9 | **설치 중**(00:47 시작). 드라이버를 바꾸지 않도록 구성 요소만: `-s nvcc_12.9 cudart_12.9 cublas_12.9 cublas_dev_12.9 thrust_12.9 nvtx_12.9 cuobjdump_12.9 nvdisasm_12.9 nvprune_12.9 -n`. 기록: `tools\buildtools-install-20261017.log`. 드라이버는 572.70 그대로여야 한다 |
| 빌드 스크립트 | `tools\strata_build_q36.py` — setup.py 의 `build_engine(gpu, "none", True, llama, toolkit=12)` 를 직접 부른다(`--setup` 을 안 써서 모델 설정 파일을 다시 쓰지 않는다). 먼저 `engine-cuda12` 를 `engine-cuda12.release-0.1.44.1` 로 백업한다 |
| ALEX / Strata | ALEX 8767(Strata 백엔드), Strata 8080 Qwen3.6(Codex 설정: `STRATA_Q36_NO_MMQ=1`, 16K, prefill 512) 실행 중 |
| 깃 | ALEX `laptop` 브랜치 = origin/laptop `23b3060` + 이 문서와 패치(아래 커밋). Strata 쪽은 Niko1221/Strata 클론이라 push 하지 않는다 — 패치 파일로 보관 |

## 3. 다음 순서

1. CUDA 설치 끝 확인: `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.9\bin\nvcc.exe`, `nvidia-smi` 드라이버 **572.70 그대로**.
2. **Strata 끄기**(실행 중인 `engine-cuda12\strata.exe` 는 잠겨 있어 덮어쓸 수 없다). 사용자에게 먼저 알린다 — 그동안 ALEX 의 Strata 대화가 안 된다.
   `strata.exe` 와 `server.py ... --engine strata` python 만 끈다(모든 python 을 끄지 말 것).
3. 빌드(10~20분): Strata 폴더에서 `.venv\Scripts\python.exe "C:\Users\karse\local ai project\tools\strata_build_q36.py"`.
   컴파일 오류가 나면 패치 쪽부터 의심한다(새 코드는 `namespace q36file` 와 place_experts 의 두 곳, serve 의 DONE 뒤 한 줄).
4. **같은 출력인지 검증**(PATH 에 `.venv\Lib\site-packages\nvidia\{cublas,cuda_runtime}\bin` 필요):
   `strata.exe --native <GGUF> --max-context 16384 --prefill 512 --tokens "<id 목록>" --n 48` 의 `gen:` 줄을
   ① 백업 엔진 ② 새 엔진(환경변수 없음) ③ 새 엔진 + `STRATA_Q36_EXPERT_FILE=1`(첫 실행은 파일을 씀, 두 번째 실행은 매핑) 으로 비교.
   ①=②=③ 이어야 한다(같은 바이트, 같은 슬롯). `STRATA_Q36_NO_MMQ=1` 도 실제 설정과 같게 준다. 측정은 겹치지 않는다.
   `STRATA_Q36_PROFILE` 은 슬롯이 바뀌므로 같음을 요구하지 않는다 — 켜지는지와 로그 줄만 본다.
5. 통과하면 `tools\strata\strata-qwen36-ud-q4_k_xl.json` 의 `env` 에 `"STRATA_Q36_EXPERT_FILE": "1", "STRATA_Q36_PROFILE": "1"` 추가
   (백업 먼저), ALEX `jarvis.lifecycle restart` → 엔진 로그에서 `experts mapped from`, `file-mapped host memory`,
   `VRAM slots start from the saved routing profile`(두 번째 시작부터)와 켜는 시간·여유 RAM 을 기록. 속도 측정은 하지 않는다(사용자가 집에서).
6. 기록: CHANGELOG, `docs/STRATA_QWEN36_LAPTOP_20261017.md` 에 절 추가, 이 문서 갱신, `laptop` 브랜치에 커밋·push.

## 4. 되돌리기

- 엔진: `engine-cuda12` 를 지우고 `engine-cuda12.release-0.1.44.1` 을 `engine-cuda12` 로 되돌린다(BUILD.json 포함).
- 소스: `git -C tools\strata checkout -- src/qwen35/q36_engine.cu`(Codex 의 setup.py 수정은 남는다), 또는 원본 파일 복사.
- 설정: Strata json 의 env 에서 두 키를 **삭제**한다(이 두 변수는 "0"이나 빈 값이면 꺼지지만 삭제가 가장 확실하다).
- 생긴 파일: `Strata-data\models\qwen36-UD-Q4_K_XL\Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf.q36experts`(약 18.8GiB),
  `.q36profile` — 지워도 된다(다음 시작 때 다시 만든다).

## 5. 지킬 것

노트북에서는 실행 여부·속도·메모리만(품질 시험은 데스크톱). 측정 전 사용자에게 알린다. Windows Python 만으로 검증.
`.cmd` 에 한글 금지. 보드는 `--peek`. 경험 승인·split 지정 금지. 사용자 대화 DB 를 건드리지 않는다.
Strata 원 저장소(Niko1221/Strata)에 올리는 일은 사용자 결정 뒤에.
