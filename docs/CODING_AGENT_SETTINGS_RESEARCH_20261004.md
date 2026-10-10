# 코딩 에이전트 설정 조사 — 공개 자료 vs 지금 설정 (2026-10-04 claude)

사용자 "깃허브나 이런 것들 뒤지면 최적의 셋팅 다 나오는 거 아니야?" → "응 해봐". 결론: 공개 자료는 **후보**를 주고, 이 장비(RTX 5080 16GB·RAM 93GB)·
이 모델(qwen3.8:27b Q4_K_M, family qwen35)·이 코드(core.py 3.3만 토큰)의 최적값은 실측으로 고른다. 조사 결과 대부분은 **이미 적용 중**이었다.

| 항목 | 공개 자료 | 지금 | 판단 |
|---|---|---|---|
| 샘플링 | Qwen3.8-27B 모델 카드: 사고 모드 temperature 1.0·top_p 0.95·top_k 20·presence 0, 비사고 0.7·0.8·20·presence 1.5. 일반 글은 "코드는 0~0.3" 이지만 Qwen 은 greedy 금지(반복·성능 저하) | Ollama 기본값 = 카드의 사고 모드 값 그대로 | 이미 제작사 권장. 낮은 온도는 사고 모델 근거가 약하고 독립 풀이의 다양성과 충돌 — 우선순위 낮음 |
| 반복 억제 | 카드: 끝없는 반복이면 presence_penalty 0~2(높으면 언어 섞임) | 0 | 실패 177회차 중 '사고 소진' 7회가 반복일 수 있음 → 후보(품질 실험 필요) |
| 추측 디코딩(MTP) | Qwen3.8 은 MTP 로 학습, Ollama 가 MTP 초안을 기본으로 켬(draft_num_predict 4); 값에 따라 빨라지거나(1~3) 오히려 느려짐(6) 보고 | **이미 켜짐** — 서버 로그 채택률 0.67~0.83, 평균 3.7토큰/회 | 초안 길이 2·3·4·6 속도만 재 볼 가치(요청 옵션이라 재시작 없음) |
| KV 캐시 8비트 | Ollama 문서: q8_0 은 품질 손실 거의 없음(퍼플렉시티 +0.002~0.05), FA 필요, 같은 메모리에 문맥 약 2배. q4_0 은 긴 문맥에서 손해 | FA 켜짐(ADR-064: qwen3.5 계열만 끔), KV 는 f16 | 후보 1순위(속도): 48K 에서 상태 ~3GB → 절반이면 GPU 층을 48 → 52~56 로 올릴 여지. 서버 환경 변수라 Ollama 재시작 필요 |
| 큰 파일·다른 파일 API | Aider 저장소 요약, Agentless/SWE-agent 위치 찾기 | ADR-072·079(API 요약), ADR-076(48K), ADR-080(근거) | 적용함 — 오늘 평가로 효과 확인 중 |
| 여러 번 풀기 | Olausson et al. ICLR 2024 | ADR-077(독립 풀이, 기본 1) | 적용함 — 측정은 사용자 결정 |

## 다음 실측(평가 대기열 7 이 끝난 뒤, GPU)
1. 속도만(고정 4만 토큰 코딩 프롬프트, 품질 아님): ①draft_num_predict 2·3·4·6 ②KV q8_0 + GPU 층 48·52·56. 약 30~40분.
2. 확실히 빠른 조합이 있으면 품질 확인(역사 11, 같은 얼린 복사본) — 사용자 결정. presence_penalty 는 품질 실험이라 따로.
설정 파일(jarvis.local.json)·런처의 Ollama 환경은 사용자 승인 없이 바꾸지 않는다(실험은 임시로 띄우고 원래대로 되돌린다).

## 출처
- Qwen3.8-27B 모델 카드: https://huggingface.co/Qwen/Qwen3.8-27B
- Qwen3 샘플링 권장(사고 0.6/비사고 0.7, greedy 금지): https://huggingface.co/Qwen/Qwen3-8B
- Ollama MTP 기본 켬(draft_num_predict 4): https://x.com/SaiyamPathak/status/2088333707174445548 , https://github.com/open-webui/open-webui/discussions/27479
- KV 캐시 양자화: https://www.ssdnodes.com/learn/ollama-kv-cache-quantization , https://modelpiper.com/blog/ollama-kv-cache-quantization
- 추측 디코딩 일반: https://www.glukhov.org/llm-performance/optimization/speculative-decoding/
- 코드 생성 온도 일반론: https://medium.com/@glanzz/stop-using-temperature-1-0-385cb51ac863

## 추가 조사 (2026-10-04 20:4x, 사용자 "실측치가 다른 데서 다 있는데 왜 한 거야?", "오픈코드를 굳이 비교할 필요가 있나?")

**원칙(사용자 지적 반영):** 긴 GPU 측정 전에 공개 실측부터 찾는다. ALEX 에만 해당하는 것(ALEX 과제 성공률·ALEX 기능·ALEX 실제 출력에서만 보이는 버그)만 잰다.

| 항목 | 공개 자료 | 결정 |
|---|---|---|
| OpenCode + Qwen3.8-27B 능력 | 16GB RTX 5060 Ti·UD-Q3_K_XL·MTP 로 약 46 tok/s, 2시간·100만 토큰 자율 개발을 프롬프트 3번으로 성공(현장 보고) | 능력은 검증된 것으로 본다 → **3~5시간 전체 비교(역사 11) 취소** |
| OpenCode + Ollama 속도 | 이슈 #46506(열림): Qwen3.8 직접 호출 약 38초 vs OpenCode 경유 약 4분 41초(약 7배), OpenCode 1.18.25·macOS, Ollama 는 놀고 OpenCode 가 CPU 35% | 이 PC(이미 과제당 10~25분)에서 치명적일 수 있음 → **과제 1개 시험만**(속도·윈도우 동작), 느리면 접는다 |
| 27B 를 16GB 에 통째로 | 위 보고는 Q3(약 13GB)로 VRAM 에 다 넣어 약 46 tok/s. ALEX 는 Q4(17.7GB)라 48층만 GPU → 생성 12 tok/s, 생성 중 GPU 35%·76W(10/4 실측) | **우선 후보:** Q3 로 ALEX 역사 11 품질만 잰다(지금 Q4 9/11). 속도 향상은 공개 자료로 충분, 남은 질문은 품질뿐 — 사용자 결정 대기 |

준비된 것(실행 전): OpenCode v1.18.34 `C:\ALEX\tools\opencode`(로컬 전용 설정), 평가 도구 `--solver opencode`.

출처:
- https://github.com/anomalyco/opencode/issues/46506
- https://codersera.com/blog/qwen-3-8-27b-local-claude-code-replacement-2026/
- https://computingforgeeks.com/run-qwen3-8-27b-locally/

## Q3 속도 실측과 125B 경로 (2026-10-04 21:1x~21:3x)

**Q3 속도(같은 2.7만 토큰 입력·1,200토큰 생성·48K):** Q4 48층 13.7 tok/s(116초) → Q3 자동 층 6.8(Ollama 가 8.8GB 만 올림) → Q3 60층 20.3 → Q3 62층 12.2(메모리 넘침, 읽기 89 tok/s)
→ **Q3 + Ollama KV q8_0 + 66층 전부 GPU 65.1 tok/s(47초), VRAM 13.0GiB**. 공개 사례(16GB 카드·Q3·KV 압축 49K 약 40 tok/s)와 같은 방향.
Q3 모델 `qwen3.8:27b-q3` = unsloth UD-Q3_K_XL + 공식과 같은 RENDERER/PARSER/샘플링/MTP(`C:\ALEX\tools\q3\Modelfile`). Ollama 재시작 도구 `logs/probe/claude_ollama_restart_20261004.py [q8_0]`.
품질(역사 11, 같은 얼린 복사본·설정)은 대기열 8 에서 측정 중.

**125B(Flash-Next) — 사용자 "그렇게 해": Q3 결과 먼저, 125B 는 전원 대책 뒤 후보.** 공개 자료: 가장 작은 GGUF UD-IQ1_S 67.56GiB, UD-IQ4_XS 87.25GiB →
16GB 에 통째로는 불가. MoE(활성 6B)라 전문가 층을 RAM 에(`--n-cpu-moe`), N-gram 표를 SSD 에 두면 RAM 64GB 부터 돌린 사례. 이 PC 예상 10~15 tok/s(추정),
llama.cpp 직접·70~90GB 새로 받기·CPU 상시 최대(소음·꺼짐 위험). 출처: https://atomic.chat/blog/guides/how-to-run-qwen-3-8-flash-next-locally ,
https://openclawdc.com/blog/can-i-run-qwen-3-8-flash-next-128gb-ram/ , https://github.com/QwenLM/Qwen3.8-Flash-Next

## 도구 루프 조사 (2026-10-04 22:1x, 평가 대기 중 — 방향 '장점만 모아 ALEX 완성'의 2단계)

- **업계 하네스는 단순하다:** 공개 코딩 에이전트 11개(Claude Code·Codex CLI·Gemini CLI·Mistral Vibe·OpenHands·Aider·Mini-SWE-Agent·Hermes·Pi·OpenCode·OpenClaw)의
  소스 분석 — 모두 직접 짠 비동기 루프, 일반 에이전트 프레임워크 미사용, 코드 검색은 임베딩이 아니라 결정적 검색(grep·파일 읽기). 18가지 설계 권고와
  **90줄 최소 하네스 뼈대** 제시. → ALEX 안에 작은 루프를 만드는 방향과 일치. 다음 단계에서 논문 본문(권고·뼈대)을 읽고 반영할 것.
  https://arxiv.org/abs/2609.00006
- **Ollama 경로 주의:** qwen3.8:27b 를 OpenAI 호환 `/v1/chat/completions` 로 부르면 응답이 오지 않는 보고(Ollama 0.32.13, macOS 는 PR #17901 로 고침, ROCm #17790 미해결),
  고유 `/api/chat` 는 정상. OpenCode 는 `/v1` 을 쓴다 → OpenCode 느림·멈춤 위험의 한 원인일 수 있음. **ALEX 루프는 고유 `/api/chat` + tools 로.**
  https://www.betterclaw.io/blog/qwen-3-8-27b-tool-calling-fails-agents-fix , https://github.com/ollama/ollama/issues/14493 (이전 Qwen 3.5 렌더러 불일치 사례)
- 정리: OpenCode 를 일꾼으로 붙이기보다 **ALEX 안전장치 안의 작은 `/api/chat` 도구 루프**(읽기·검색·SEARCH/REPLACE 수정·합격 테스트·끝내기)가 이 PC 에서 더 안전.
  아직 모르는 것: Qwen3.8 의 ALEX 도구 형식 준수율, Q3 에서의 도구 호출 정확도, 걸음 수·문맥 예산 — 구현 후 역사 11·v3 로 측정.

