# ALEX LoRA 학습 파이프라인 — 검토와 구현 (2026-10-05, claude, ADR-084)

사용자: "낮은 모델로 LoRA학습은 되는거 같은데? 파이토치같은거. 그거 파이프라인 검토하고, 가능하면 구현해줘"

## 1. 결론

| 질문 | 답 | 근거 |
|---|---|---|
| 본 모델(Qwen3.8-27B)을 이 PC 에서 LoRA 학습? | **안 된다.** 4비트 QLoRA 도 24GB 이상 필요, LoRA 는 36GB 초과. 이 PC 는 16GB | Unsloth Qwen3.8 안내(공개) |
| 작은 모델은? | **된다.** 4B 는 LoRA 약 8~10GB(QLoRA 3.5~5GB). 이 PC 에서 VRAM 여유를 남긴다 | Unsloth 요구 사양 표(공개) |
| 학습한 모델을 Ollama 에? | Ollama 0.34.1 부터 Modelfile `ADAPTER` 가 없어졌다(이 PC 0.34.4). **어댑터를 바탕 모델에 합친 뒤 GGUF 로 바꿔 넣는다** | ollama#14969, mellea#1651(공개) |
| 지금 데이터로 효과? | **없다.** 대화 학습에 쓸 '질문 → 좋은 답' 쌍이 train 0건·eval 1건. 승인된 3건은 교정·교훈 형식이라 학습하면 해롭다 | 이 PC 실제 DB(읽기 전용 조회) |

구현은 끝났고 CPU 만으로 처음부터 끝까지 돌려 확인했다(4절). GPU 학습은 사용자 동의와 다른 GPU 작업이 없을 때만 한다.

## 2. 구성 — 표준 조합 그대로

- 학습: Hugging Face **transformers + PEFT LoRA**(PyTorch). r=16, alpha=16, dropout 0, 모든 선형층(q/k/v/o/gate/up/down),
  lr 2e-4 선형 예열·감소, 그래디언트 체크포인팅. 손실은 마지막 assistant 답 토큰에만.
  Unsloth(2배 빠름) 대신 이것을 고른 이유: Windows 에서 Triton 컴파일 없이 설치되고, **GPU 없이 같은 코드를 CPU 로 시험할 수 있다**.
  데이터가 수백 건 수준이면 속도 차이는 몇 분이다. 데이터가 커지면 Unsloth 로 바꿀 수 있다(같은 PEFT 어댑터).
- 바탕 모델: **Qwen/Qwen3-4B-Instruct-2507**(Apache-2.0). 일반 트랜스포머 구조라 llama.cpp 변환·Ollama 가져오기가 검증된 길이고,
  생각 없이 바로 답하는 모델이라 ALEX 의 '질문 → 답' 데이터와 맞는다. Qwen3.5-4B 는 더 새롭지만 4비트 학습 비권장(16비트 10GB),
  혼합 주의(Gated DeltaNet) 커널이 Windows 에서 불확실, 비전 모델이라 첫 판에서 뺐다(`scripts/lora.py` `BASES` 에 추가하면 된다).
- 변환: llama.cpp `convert_hf_to_gguf.py`(b11404 고정) → **q8_0** GGUF(4B 약 4.3GB, 거의 무손실, 별도 빌드 불필요).
- 등록: `ollama create alex-lora-<실행 ID>`(Qwen chatml 틀·모델 카드 권장 샘플링). 비교용 바탕 모델도 같은 변환을 거쳐
  `alex-lora-base-qwen3-4b-instruct-2507` 로 한 번 만든다 — 차이가 학습 때문인지 양자화 때문인지 섞이지 않게.

## 3. 안전장치

- **데이터:** 학습은 `training_pool` 뷰(승인 + train)만, 평가는 승인 + eval 만 — 둘은 구조적으로 섞이지 않는다(ADR-037).
  그중 **'질문 → 사용자가 좋다고 한 답'(/good, 교정 없음)만** 쓴다(`sft_ready`). 채팅 교정의 correction 은 고친 답이 아니라
  사용자가 한 말("사용자가 바로잡았다: 그게 아니야")이라, 그대로 학습하면 모델이 그 문장을 따라 말한다. 50건 미만이면 시작하지 않는다
  (`--allow-small` 은 연결 확인용, 1건 이상일 때만).
- **GPU:** `--yes`(사용자 동의) + 그래픽카드 사용 2GB 이하 + Ollama 에 올라간 모델 없음, 셋 다 통과해야 시작.
  학습 프로세스 VRAM 상한 11GB(`set_per_process_memory_fraction`) — 넘으면 이 프로세스만 메모리 부족으로 멈추고 카드를 꽉 채우지 않는다.
  합치기·변환·CPU 평가는 `CUDA_VISIBLE_DEVICES=""` 로 GPU 를 아예 안 본다. 시간 초과(학습 3시간) 시 프로세스 트리를 끊는다.
- **기록:** `training_runs` 는 학습이 실제로 끝났을 때만 "LoRA 학습 완료: …" 로 남는다. `/growth` 는 내보내기 기록과 실제 학습을 나눠 센다.
- **채택:** 스크립트는 ALEX 설정을 바꾸지 않는다. 평가 보고서(`eval-report.md`)를 보고 사용자가 정한다.
  학습 환경 쪽 바탕 모델은 학습 직전에만 받고(온라인), 학습·합치기·평가는 오프라인 — 몰래 새 판을 받지 않는다. 어느 판으로 학습했는지 `run.json` 에 남는다.

## 4. 이 PC 확인 (CPU 만, 그래픽카드 470MiB·0% 그대로)

연결 확인 `smoke-20261005` — 필터를 넣기 전, 승인 경험 2건(교정 형식)으로:

| 단계 | 결과 |
|---|---|
| 학습(3단계, bf16 CPU) | 16.2초. 학습 손실 5.42 → 3.75, 평가 손실(2건) 1.98 → 1.67 |
| 합치기 → q8_0 GGUF → Ollama 등록(학습 + 비교용 바탕) | 1분 45초 |
| CPU 비교 생성(6문항 × 2모델) | 약 6분. 점검 표시 0. 일반 질문 답은 바탕과 거의 같다(2건으로는 바뀌지 않는 게 정상) |

보고서에서 교정 형식 데이터 문제를 찾아 `sft_ready` 를 넣었다. 그 모델은 쓰지 않으므로 Ollama 모델·GGUF 를 지웠다(기록은 `data/lora/smoke-20261005/`).
Ollama 로 실제 생성(GPU)은 아직 안 했다 — `ollama create` 가 틀을 받아들인 것까지 확인. 첫 GPU 실행 때 `eval --engine ollama --yes` 로 확인한다.

## 5. 사용법

```
python scripts/lora.py status                      # 환경·학습 가능한 쌍·실행 목록
python scripts/lora.py prepare --run-id m1         # '질문 → 좋은 답' 50건 이상일 때
python scripts/lora.py train --run-id m1 --yes     # GPU(사전 점검 통과 시). --device cpu 는 GPU 를 안 쓴다
python scripts/lora.py export --run-id m1          # 합치기 → GGUF → Ollama alex-lora-m1 (GPU 안 씀)
python scripts/lora.py eval --run-id m1            # CPU 비교 보고서. --engine ollama --yes 는 Ollama(GPU)로
```

설치(이미 됨, 다시 만들 때):

```
C:\ALEX\tools\lora\venv            Python 3.11.9
  pip install torch --index-url https://download.pytorch.org/whl/cu130        # 2.14.1+cu130 (sm_120)
  pip install "transformers>=4.56" peft accelerate safetensors sentencepiece protobuf numpy   # 5.18.0 / 0.21.2
C:\ALEX\tools\lora\llama.cpp       git clone --depth 1 --branch b11404 https://github.com/ggml-org/llama.cpp
C:\ALEX\tools\lora\hf-cache        Qwen3-4B-Instruct-2507 (7.6GB)
C:\ALEX\tools\lora\base\…          비교용 바탕 GGUF(4.0GB) — Ollama alex-lora-base-qwen3-4b-instruct-2507
```

## 6. 다음 — 막힌 곳은 학습기가 아니라 데이터

1. **좋은 답에 /good → /review 승인**: 가장 확실하지만 느리다.
2. **실행 확인을 통과한 채팅 코딩 답을 후보로**(ADR-082 의 뒤 실행 확인 ok): 실행으로 검증된 '요청 → 코드' 쌍. 사용자가 묶어서 승인.
3. **ALEX 자기 지식 문답**(정체성·명령·설정): 문서에서 27B 가 문답을 만들고 사용자가 검토 → 효과를 바로 잴 수 있는 첫 학습
   (예: "너는 누구야" → 지금 바탕 모델은 "알리바바 Tongyi 실험실에서…" 라고 답한다).
4. **본 모델 27B**: 이 PC 로는 불가. 맥 스튜디오(MLX LoRA)나 24GB 이상 클라우드 GPU 에서 같은 데이터·평가로.

2·3 은 무엇을 기록·생성할지 사용자 결정이 필요해 하지 않았다.

## 출처

- Unsloth Qwen3.8 학습 안내 — https://unsloth.ai/docs/models/qwen3.8/train
- Unsloth 요구 사양(VRAM 표) — https://unsloth.ai/docs/get-started/fine-tuning-for-beginners/unsloth-requirements
- Unsloth Qwen3.5 학습 안내 — https://unsloth.ai/docs/models/qwen3.5/fine-tune
- Unsloth Windows 설치 — https://unsloth.ai/docs/get-started/install/windows-installation
- Ollama ADAPTER 제거(0.34.1) — https://github.com/generative-computing/mellea/issues/1651
- Ollama 모델 가져오기 — https://docs.ollama.com/import
- Hugging Face PEFT — https://huggingface.co/docs/peft · LoRA 논문 — https://arxiv.org/abs/2106.09685
- Qwen3-4B-Instruct-2507 — https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507
