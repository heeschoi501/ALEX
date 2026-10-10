# Project JARVIS (구 명칭)
## Personal Local AI — Project Definition

> **2026-09-14: 정식 명칭이 정해졌습니다 — Project ALEX.**
> 비전·정체성·성장 철학은 `PROJECT_ALEX.md` 를 보십시오.
> 이 문서는 아래의 기술적 목적·구조 정의로서 계속 유효합니다.
> 코드와 설정의 `jarvis` 식별자는 아직 바꾸지 않았습니다(이유는 `PROJECT_ALEX.md` 와 `GPT_NEXT.md` 참조).

> 개발 코드네임: **Project JARVIS**

---

## 1. 프로젝트 목적

Project JARVIS는 단순한 로컬 챗봇이 아니다.

최종 목표는 영화 《아이언맨》의 JARVIS처럼 사용자의 여러 기기에서 동일한 기억과 맥락을 유지하면서, 평상시에는 로컬 AI로 동작하고 필요할 때만 강력한 클라우드 AI를 호출하는 **개인용 AI 시스템**을 만드는 것이다.

핵심 원칙:

> **일상적인 작업은 로컬에서 처리하고, 고난도 작업만 외부 AI에 맡긴다.**

목표 효과:

- 클라우드 AI 토큰 소비 및 비용 감소
- 빠른 응답
- 개인 데이터의 로컬 우선 보관
- 특정 AI 회사/모델에 대한 종속성 감소
- 노트북/데스크톱/향후 서버에서 동일한 AI 사용
- 새로운 로컬 모델 등장 시 자유로운 교체

---

## 2. 핵심 철학: JARVIS와 LLM을 분리한다

### JARVIS Core

JARVIS 자체는 다음 요소의 집합이다.

- Memory
- User Context
- RAG
- Tool 사용
- Agent System
- Hardware Manager
- Model Router
- Voice Interface
- Device Control
- Session / Project Management

### LLM

LLM은 JARVIS가 사용하는 **교체 가능한 두뇌**다.

후보 예:

- Qwen
- Gemma
- Llama 계열
- 기타 오픈웨이트 모델
- GPT
- Claude
- Codex
- Gemini

특정 모델에 JARVIS 전체가 종속되어서는 안 된다.

---

## 3. 기본 운영 구조

```text
사용자 요청
   ↓
JARVIS Core
   ↓
요청 분석
   ↓
Memory / RAG / Tools 확인
   ↓
Model Router
   ├─ 단순 명령 → Tool / Local 처리
   ├─ 일반 대화 → Local General LLM
   ├─ 간단한 코딩 → Local Coding LLM
   ├─ 중간 난이도 → 장비에 따라 더 큰 Local LLM
   └─ 고난도 → GPT / Claude / Codex / Gemini 등 Cloud AI
```

---

## 4. 로컬 AI가 담당할 범위

로컬 AI가 최고급 GPT/Claude를 완전히 대체하는 것은 목표가 아니다.

### 일반 대화
- 자연스러운 질답
- 일반 지식 질의
- 설명
- 간단한 추론

### 개인 비서
- 일정 확인
- 메모
- 프로젝트 상태 확인
- 파일 검색
- 이전 작업 검색
- 사용자 명령 분류
- 앱 실행
- PC 상태 확인

### 문서
- 요약
- 정리
- 간단한 번역
- RAG 기반 질답

### 간단한 코딩
최소한 다음 정도는 로컬에서 가능해야 한다.

- 기본 Python
- Pandas
- 간단한 SQL
- Regex
- 짧은 함수 작성
- 간단한 오류 수정
- 파일 입출력
- 간단한 자동화
- 짧은 Streamlit/FastAPI 기능 추가

### Cloud로 넘길 작업
- 대형 코드베이스 분석
- 복잡한 리팩터링
- 어려운 디버깅
- 고난도 추론
- 장시간 에이전트 작업
- 고품질 설계 검토

---

## 5. 최소 모델 기준

최소 실행 환경은 **노트북**으로 잡는다.

### 기본 목표
**7~9B급 quantized Local LLM**

이유:

- 3~4B: 라우팅/명령 분류는 가능하지만 일반 대화와 코딩에서 부족할 수 있음
- 7~9B: 일반 대화 + 간단한 코딩의 현실적인 최소선
- 14B+: 8GB VRAM 노트북의 상시 기본 모델로는 부담

초기 전략:

```text
General Local AI : 7~9B
Coding Local AI  : 7~9B
```

또는 충분한 범용 8B 모델 하나로 시작한다.

초기에는 여러 모델을 동시에 VRAM에 상주시킬 필요가 없다.

---

## 6. 현재 하드웨어 프로필

### LAPTOP PROFILE — Minimum Spec

MSI Alpha 17

- GPU: RTX 4070 Laptop
- VRAM: 8GB
- RAM: 32GB

담당:

- 7~9B Local LLM
- 일반 대화
- 간단한 코딩
- RAG
- Memory
- 음성
- Tool 실행
- PC 제어
- Cloud Routing

---

### DESKTOP PROFILE — Expanded Spec

- CPU: Ryzen 7 9800X3D
- GPU: RTX 5080
- VRAM: 16GB
- RAM: 96GB

Laptop 기능 전체에 추가:

- 8~14B급 Local LLM
- 필요 시 20~32B급 모델 실험
- 더 긴 Context
- 더 큰 RAG
- Vision
- 복수 Agent
- 코드베이스 분석
- 고성능 Local Worker

---

## 7. Performance Profile

노트북용/데스크톱용 프로그램을 따로 만들지 않는다.

같은 JARVIS 프로그램이 시작 시 하드웨어를 탐지해 Profile을 선택한다.

```text
RTX 4070 Laptop
VRAM 8GB
RAM 32GB
→ LAPTOP PROFILE
```

```text
RTX 5080
VRAM 16GB
RAM 96GB
→ DESKTOP PROFILE
```

향후 확장:

```text
고성능 GPU / 대용량 Unified Memory
→ ULTRA PROFILE
```

Configuration은 코드에 하드코딩하지 않고 별도의 Profile/Config로 관리한다.

---

## 8. Model Router

JARVIS의 핵심 기능이다.

예시:

```text
"VS Code 켜줘."
→ Tool 실행
→ LLM 사용 최소화

"이 문서 요약해줘."
→ Local General LLM

"이 Python 함수 고쳐줘."
→ Local Coding LLM

"프로젝트 전체 구조를 분석하고 리팩터링해줘."
→ Cloud Coding/Reasoning Model
```

모든 요청을 최고급 모델로 보내지 않는다.

---

## 9. Cloud AI의 역할

Cloud AI는 JARVIS 자체가 아니라 **필요할 때 호출하는 외부 전문가**다.

후보:

- GPT
- Claude
- Codex
- Gemini
- 향후 다른 모델

목표:

```text
잘못된 방식:
전체 대화 + 전체 코드 + 전체 로그 + 전체 프로젝트
→ Cloud AI

목표 방식:
Local Search
→ 관련 파일/메모리 추출
→ 필요한 Context만 정리
→ Cloud AI
```

토큰 소비를 줄이는 중요한 구조다.

---

## 10. Memory

Memory는 LLM과 분리한다.

Local LLM을 교체해도 Memory는 유지되어야 한다.

저장 대상 예:

- 사용자 Preferences
- 프로젝트 상태
- 이전 결정
- 작업 로그
- 일정
- 문서
- 코드 프로젝트 정보

최종적으로 Laptop과 Desktop은 같은 Memory를 공유해야 한다.

예:

```text
Laptop:
"여기까지 작업했어."

Desktop:
"아까 하던 거 계속해."

→ 동일한 JARVIS가 Context를 복원
```

---

## 11. Device 구조

장기적으로 JARVIS는 특정 컴퓨터에 종속되지 않는다.

```text
Phone
  ↓
Tablet
  ↓
Laptop
  ↓
Desktop
  ↓
Always-on Core (향후)
```

각 Device는 같은 JARVIS에 접근하되 성능과 역할만 다르다.

---

## 12. Always-on Core — 향후

초기에는 별도 구매하지 않는다.

프로젝트의 실용성이 확인되면:

- Mac mini
- 저전력 Mini PC
- 기타 Local AI Server

등을 검토한다.

역할:

- 24시간 Memory Server
- Agent Core
- Scheduler
- RAG
- STT/TTS
- Device 상태 관리
- Wake-on-LAN
- Router

무거운 연산이 필요하면 Desktop을 WOL로 깨우고 Worker로 사용한다.

---

## 13. 목표 아키텍처

```text
User
 ↓
Phone / Tablet / Laptop
 ↓
JARVIS Core
 ├─ Memory
 ├─ RAG
 ├─ Voice
 ├─ Session Manager
 ├─ Tool Manager
 ├─ Agent Manager
 ├─ Hardware Manager
 └─ Model Router
       ├─ Local Small Model
       ├─ Desktop High-Performance Local Model
       └─ Cloud AI
```

---

## 14. 개발 로드맵

### v0.1 — Local Personal AI Skeleton
목표: 노트북에서 실제 동작하는 최소 JARVIS

포함:

1. Local LLM 연결 구조
2. Chat Interface
3. Conversation 저장
4. Session 재개
5. Simple Memory
6. Hardware 자동 탐지
7. Performance Profile
8. Model Router 기본 구조
9. Cloud Provider Stub
10. Tool Interface
11. Logging
12. Config
13. 기본 Test

첫 구현은 실제 Local LLM 전에 **Mock LLM**으로 전체 흐름을 검증한다.

### v0.2 — Memory 강화
- SQLite
- Project Memory
- Conversation Summarization
- Semantic Search
- Vector DB 필요성 검토
- Long-term Memory

### v0.3 — Tools
- 파일 검색
- 프로그램 실행
- VS Code 실행
- Terminal
- Python 실행
- 시스템 상태
- Web/API

### v0.4 — Voice
```text
STT → JARVIS → TTS
```

### v0.5 — Multi-device
- Laptop ↔ Desktop
- Memory 공유
- Remote Worker
- Wake-on-LAN

### v1.0
평상시에 실제로 사용할 수 있는 Personal Local AI.

예:

- "어제 하던 프로젝트 이어서 해."
- "지난번에 왜 이 방법을 선택했지?"
- "VS Code 열고 프로젝트 실행해."
- "이 Python 코드 오류 좀 봐."
- "이건 어려우니까 Claude한테 넘겨."
- "이 내용 기억해."
- "집 컴퓨터 깨워서 이거 돌려."

---

## 15. 개발 원칙

1. Laptop을 Minimum Spec으로 한다.
2. Desktop에서 기능이 자동 확장되게 한다.
3. Model과 JARVIS Core를 분리한다.
4. Memory는 Local 우선이다.
5. Cloud AI는 필요할 때만 사용한다.
6. 특정 AI Provider에 종속되지 않는다.
7. 처음부터 거대한 시스템을 만들지 않는다.
8. 각 단계에서 실제 실행 가능한 제품을 만든다.
9. 사용자가 코드를 이해하고 수정할 수 있을 정도의 복잡도를 유지한다.
10. 과도한 추상화와 Enterprise-style 설계를 피한다.

---

## 16. 현재 첫 번째 목표

> **RTX 4070 Laptop 8GB / RAM 32GB에서 7~9B Local AI 기반 JARVIS가 쾌적하게 동작하고, 동일한 코드가 RTX 5080 Desktop에서는 자동으로 확장되도록 만든다.**

이 단계가 성공하면 이후 Memory, Voice, Multi-device, Agent 기능의 기반으로 사용한다.

---

## 개발 역할 분담

Project JARVIS의 개발은 다음 역할로 진행한다.

### GPT + Claude — 공동 구현 담당
두 모델 모두 설계와 구현에 참여한다.

담당:
- Architecture 제안 및 개선
- Python 코드 작성
- 테스트 코드 작성
- 리팩터링
- 디버깅
- 성능 최적화
- Local LLM / Tool / Memory / Router 구현
- 구현 결과 상호 교차 검토

한 모델이 작성한 코드를 다른 모델이 수정하거나 개선할 수 있다.

중요한 설계 결정은 모델 고유의 대화 맥락에만 남기지 않고
`DECISIONS.md` 등 프로젝트 파일에 기록한다.

### Gemini — 독립 검토 담당
Gemini는 기본적으로 구현자가 아니라 Reviewer 역할을 맡는다.

담당:
- Architecture 비판적 검토
- 과설계 여부
- 누락 기능
- 보안
- 성능 병목
- Laptop Minimum Spec 현실성
- Provider 종속성
- 테스트 누락
- GPT/Claude 구현 결과에 대한 수정 지시

기본 개발 흐름:

```text
Project Definition
      ↓
GPT + Claude 공동 설계/구현
      ↓
실행 / Simulation / Test
      ↓
Gemini 독립 검토
      ↓
수정 지시
      ↓
GPT + Claude 수정
      ↓
재검증
```
