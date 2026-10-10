# 로컬 모델 고르기와 맥 스튜디오 판단 — 2026-09-30, claude

## 질문과 사용자 지적

- 사용자(15시께): 지금처럼 쓴다면 맥 스튜디오 M5 Ultra 256GB(약 1,700만 원)가 필요한가? 큰 모델을 쓰면 나아지나?
- claude 첫 답: 보류. 27b 로 충분하고 큰 모델이 더 정확하지 않았으며 개발은 클라우드가 한다는 이유였다.
- 사용자 지적 세 가지:
  1. `qwen3.8-flash-next` 는 (코딩으로) 안 써 봤다.
  2. 큰 모델은 같은 크기 중 최상급과 비교해야 한다.
  3. 지금 하는 일은 ALEX 가 **스스로** 개발하게 만드는 것이다.
- 세 지적 모두 맞다.
  - 코딩 평가(변이 과제)는 27b 로만 했다.
  - 큰 모델(gpt-oss:120b, qwen3.5:122b, qwen3.8-flash-next:125b)은 대화 바로잡기 replay(n=3)만 쟀다(PERFORMANCE 26-4~26-10).
  - 목표가 ALEX 스스로의 개발이면 로컬 모델 실력이 곧 ALEX 실력의 천장이다. 그래서 "개발은 클라우드가 하니 가끔 쓴다"는 이유는 버린다.

## 결론 (claude, 수정본)

**맥은 아직 보류한다.** 이유는 "필요 없어서"가 아니라 "지금 PC 로 먼저 확인할 수 있어서"다.

- 발표 수치에서 가장 크게 오르는 구간은 27B 에서 125B 로 가는 구간이다(DeepSWE 1.1 기준 +16.5).
- 125B 에서 맥에 들어가는 320B 급으로 가면 +4.7 이 더 오를 뿐이다.
- 125B 는 이미 이 PC 에 설치돼 있고, 9/26 측정에서 27b 보다 빨랐다(PERFORMANCE 26-10).

## 같은 크기 중 최상급 공개 모델 (발표 수치 — ALEX 과제에서는 아직 안 쟀다)

| 모델 | 전체 / 활성 | 어디서 도나 | DeepSWE 1.1 | SWE-bench Pro | AA 지수 |
|---|---|---|---|---|---|
| Qwen3.8 27B (지금 권고 모델) | 27B dense | 이 PC(`--num-gpu 54`) | 42.2 | 61.7 | 34(소형 1위) |
| **Qwen3.8 Flash Next** | 125B / 6B (+ n-gram 임베딩 51B, MTP 4B) | **이 PC — 설치됨**<br>(`qwen3.8-flash-next:125b-a6b-q4_K_M`, 112.5GB)<br>GPU 12GB + RAM 100.5GB, 22.6 tok/s | 58.7 | 62.5 | 목록에 없음 |
| GLM-5.3-Flash | 320B / 18B | 맥 256GB 는 4비트 급으로 줄여야<br>(FP8 원본 약 328GB), 이 PC 불가 | 63.4(검색 요약) | — | 42 |
| GLM-5.3 / MiMo-V2.6-Pro / Kimi K3 / DeepSeek V4.1 Flash | 753B / 1T / 2.8T / 552B | 256GB 에도 안 들어감 | — | — | 45 / 46 / 44 / 39 |
| (참고: 크다고 좋지 않다) MiniMax-M3 / Nemotron 3 Ultra | 428B / 550B | — | — | — | 29 / 23 (27B 의 34 보다 낮음) |

Flash Next 는 27B 보다 모든 코딩 수치가 높다. 벤치마크마다 폭은 다르다.

- DeepSWE +16.5(긴 과제)
- SWE-bench Multilingual +7.2
- Toolathlon +6.4
- LiveCodeBench +1.6
- SWE-bench Pro +0.8

## ALEX 실측과의 관계

**Flash Next 125b (9/26)**

- 대화 바로잡기 1/3(n=3). 두 번은 "파일에 접근할 수 없다"며 떠넘겼거나 반만 바로잡았다.
- 18:38 CUDA 오류(`illegal memory access`, FA 기본값)가 났고, 안전장치가 5초 안에 멈췄다.
- FA off 에서는 여러 번 답했고 오류가 없었다.
- 오버클럭도 원인 후보다(26-8).

**27b (오늘)**

- 쉬운 16개 15/16, 어려운 11개 9/11.
- API 요약 진단 2/5(이미 본 실패 5개, PERFORMANCE 31 ⑦).
- 못 푼 것은 core 큰 파일 과제 3개(hard 1개 포함)다.

## 다음 실험 — Flash Next 125b 코딩 평가 (사용자 결정 대기: 다음 세션 또는 오늘)

**조건**

- 기본 클럭을 권한다.
- Ollama 를 `OLLAMA_FLASH_ATTENTION=0` 으로 새로 띄운다(9/26 아침 조건).
- 안전장치: `scripts/desktop_screen.sh` 방식. GPU 83°C 이상이거나 새 CUDA 오류가 나면 멈추고 Ollama 를 끈다.

**과제:** `coding_agent_eval.py --model qwen3.8-flash-next:125b-a6b-q4_K_M` 로 두 묶음을 돈다. 둘 다 오늘 27b 결과와 과제별 짝 비교가 된다.

- 어려운 11(`--tasks-file logs/probe/mutation_tasks_hard.json`)
- 평가 16(`--tasks-file logs/probe/mutation_tasks.json --split test`)

**판정**

- 125b 가 27b 가 못 푼 과제(hard 2개, core 3개)를 회귀 없이 풀면 "큰 모델이 ALEX 를 키운다"로 본다.
  - 그때 맥(GLM-5.3-Flash 급, 조용함, 24시간 자기 개선)을 검토한다.
  - 더 싼 대안인 RAM 증설(96 → 192GB, 메인보드·BIOS 지원 확인 필요)도 함께 본다. 125b 가 디스크 매핑 없이 돈다.
- 비슷하면 병목은 모델이 아니라 도구다(테스트 보기·API 요약처럼 도구 개선의 효과가 컸다). 그러면 맥은 필요 없다.
- 125b 가 이 PC 에서 불안정(CUDA 오류)하면 그 자체가 전용 기기의 근거가 된다. 다만 기본 클럭에서 먼저 확인한다.

## 출처

- Qwen3.8-Flash-Next 모델 카드(27B 와 비교표): https://huggingface.co/Qwen/Qwen3.8-Flash-Next
- Artificial Analysis 공개 모델 지수: https://artificialanalysis.ai/models/open-source
- GLM-5.3-Flash:
  - https://huggingface.co/zai-org/GLM-5.3-Flash
  - https://www.marktechpost.com/2026/08/26/z-ai-releases-glm-5-3-flash-a-320b-a18b-natively-multimodal-moe-with-a-1m-token-context/
  - https://www.mindstudio.ai/blog/glm-5-3-flash-open-weight-model
  - DeepSWE 63.4·328GB 는 검색 요약에서 얻었다. 모델 카드 원문은 도구 오류로 못 읽었다 — 다시 확인할 것.
- OpenRouter 공개 모델 정리(6월): https://openrouter.ai/blog/insights/the-open-weight-models-that-matter-june-2026/
- gpt-oss 120b/20b SWE-bench Verified 62.4/60.7(OpenAI 모델 카드, 크기 약 6배에 1.7%p) — 사용자에게 든 예. 기억에 의존한 수치라 인용 전 다시 확인할 것.
