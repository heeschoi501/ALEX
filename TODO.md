# TODO — after Project JARVIS v0.2.0 Local Beta

## P0 — 실제 사용자 장비 검증

1. RTX 4070 Laptop / Windows + WSL에서 Python 3.10+ runtime 검증
2. Ollama 설치/연결
3. Qwen3 8B Q4 benchmark 3회
4. first-token / total / tokens-per-sec / load / VRAM 기록
5. 30+ turn 대화 + rolling summary + restart/resume
6. `/ask-file`, `/ask-project`, `/edit-file` 실사용
7. Web localhost 사용
8. RTX 5080 Desktop에서 동일 검증
9. 14B General/Coder 모델 전환 지연 측정

## P1 — 실제 사용 데이터 기반 개선

- Router `/wrong` 데이터 수집 후 golden corpus 확장
- Context budget/num_ctx/num_predict 실측 튜닝
- Workspace edit prompt 품질 실측
- portable state 충돌 정책 UX 개선
- Task에 due date/tag가 실제로 필요한지 사용 후 결정

## P2 — Gemini 독립 리뷰

`REVIEW_PACKET.md` 기준으로 architecture/security/performance/over-engineering review.

## P3 — 다음 확장 후보

실사용 검증 뒤 우선순위를 고른다.

- STT/TTS/Voice adapter
- 인증된 remote worker + Wake-on-LAN
- real multi-device state sync
- Vision
- 승인형 restricted process runner
- semantic/vector retrieval이 lexical보다 실제로 필요한지 검증

## 지금 넣지 않을 것

- 임의 shell 자동 실행
- 무승인 file write
- 무승인 Cloud
- LAN에 Web UI 바로 공개
- LangChain/LangGraph를 이유 없이 도입
- 복잡한 Multi-Agent
