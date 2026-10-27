## 2026-10-27 (4차) — 성장 검토 누락·스킬 묶음 읽기·기존 세션 갱신 보완

- 유휴 성장 검토는 마지막 검토 지점 다음의 기록을 오래된 순서로 20개씩 읽고, 실제로 읽은 묶음 끝까지만 진행한다. 긴 기록의 마지막 묶음과 명령만 있는 묶음도 지나가며 발언을 건너뛰지 않는다.
- 승인된 라이브러리 스킬 묶음은 하네스의 read 도구로 보조 문서를 읽을 수 있다. 프로젝트 밖 쓰기와 다른 사용자·프로필·제안 기록 읽기는 허용하지 않는다.
- 기존 코딩 세션과 이어 쓰는 하위 에이전트의 스킬 목록·지시문을 다음 사용자 요청에 갱신한다. 승인·수정·보관이 새 세션 없이 반영된다.
- 승인 문서의 링크·읽기 실패는 그 파일만 제외하고 알린다. 빈 보조 파일은 그대로 가져오되 SKILL.md·프로필·메모 본문 검사는 유지한다.
- Office 경로 거절 시험은 나중에 새로 만든 ZIP 대신 실제 수정 전 바이트와 비교해 생성 시각에 따른 우발 실패를 없앴다.

## 2026-10-27 (3차) — 승인형 스킬·프로필(Codex)과 교차 검토, 자동 검사(CI)

- `/growth review·pending·show·approve·reject·profile·skills·import·archive·restore·auto` — 대화에서 스킬·USER.md·MEMORY.md 변경안을 만들고 사람이 승인해야 반영(Codex, ADR-122). 자동 검토는 기본 꺼짐.
- 교차 검토로 고침: 크기 상한(USER 1,375·MEMORY 2,200), 검사를 통과 못 하는 승인 파일이 대화를 멈추던 것, ```json 으로 감싼 답 거절, 형식 오류 시 같은 대화를 10분마다 다시 검토, '제안 0개' 알림이 대화에 쌓이던 것.
- 기억을 지우던 200개 한도 두 곳을 없앰(Hermes 에서 온 숫자가 아님): 배움 제안 기록(넘으면 새 제안이 영영 막혔다 — 대기 중인 제안 20개만 제한)과 코딩 교훈 보관함(넘으면 오래된 것부터 버렸다).
- 웹 화면: 새 대화를 불러오는 동안 친 글이 가끔 사라지던 것 수정(검사가 그 구간을 매번 재현).
- GitHub Actions: 모든 push·PR 에서 `verify_release`(Ubuntu 3.10·3.11·3.13, Windows 3.11)·웹 화면(Chromium)·비밀값(gitleaks). Windows 짧은 경로에서 시험 파일 쓰기를 거부하던 버그 수정.
- 흡수 목록: Hermes 기준 085f562 → 69f73ec(흡수한 8곳 그대로). OpenCode v2 배포 사실을 바로잡고 이동 시점은 보류.

## 2026-10-27 (2차) — 흡수 목록·원본 변경 확인·덩치 보고, 종합 보고서

- `docs/upstreams.json`: OpenCode 11곳·Hermes Agent 5곳·ExpeL·Aider 아이디어, 결론 안 난 실험 7개, 코드 고치기 4갈래(모두 2026-11-30 까지 결정).
- `scripts/check_upstreams.py`: 흡수한 원본 파일만 판 사이 비교 + 덩치 + 지난 결정일. 첫 실행 OpenCode v1.18.34 → v1.19.4 에서 2곳 바뀜, 둘 다 반영할 것 없음(ADR-121).
- 다른 AI 검토용 종합 보고서: docs/GROWTH_ABSORPTION_REPORT_20261027.md.

## 2026-10-27 — Hermes Agent 참고: 배우지 말 것·남의 글 속 지시·프롬프트 순서(설정)·교훈 보관·지난 대화 검색

- `/dev-learn` 이 환경 탓 실패·'도구가 안 된다' 같은 부정 주장을 교훈으로 들이지 않음(지시문 + 코드 거름, ADR-120).
- 웹 페이지·검색 요약·저장 자료에 AI 에게 지시하려는 문장 모양이 보이면 경고를 붙이고, 그런 교훈은 쓰지 않음(`jarvis/threats.py`, ADR-120).
- 설정 `stable_prompt_prefix`(기본 꺼짐): 차례마다 바뀌는 것을 질문 바로 앞으로 — 모델 없이 잰 같은 앞부분 6.5% → 79%. PC 측정 `scripts/measure_prompt_cache.py --live` (ADR-119).
- 점수 0·뺀 코딩 교훈은 보관함으로(`/dev-insights archived`·`restore 번호`). 지난 대화 본문 검색(`/chat-search`, 옆 목록 '대화 검색').
- 상세: docs/HERMES_REFERENCE_20261027.md.

## 2026-10-26 (2차) — 내용 속 비밀값·새 대화 입력 유지·평가 11/11·시험 보강·문서 정리

- 무료 모델로 보내는 복사본에서 내용에 실제 키 모양이 보이는 파일도 빼고, 그 자리(와 `.env` 같은 이름)에 OpenCode 가 쓴 파일은 들이지 않음.
- '새 대화' 를 누른 직후 친 글이 옛 대화 초안으로 가던 문제 수정(브라우저 검사에서 발견). 허락 대기 중 '노트…'·'no-op…' 을 거절로 읽던 것 수정.
- 평가 과제 v4 11/11(wan14b 포함). diagnostics·runtime 설치기 시험 추가. 9월 CHANGELOG·옛 TODO 는 docs/archive 로.
- Python 3.11·3.13 릴리스 검사와 브라우저 검사 PASS. 상세: docs/CLAUDE_CLOUD_20261026.md.

## 2026-10-26 — 클라우드 작업(PC 없이): 받아쓰기 파이프·무료 모델 키 파일·평가 과제 v4·PC 평가 스크립트

- 받아쓰기 일꾼을 멈추거나 다시 띄울 때 파이프를 닫고, 늦게 끝난 옛 일꾼이 새 일꾼을 '멈춤'으로 보이게 하던 경합을 막음.
- OpenCode 무료 모델로 보내는 복사본에서 `id_rsa`·`credentials.json`·`secrets.*`·`.pfx` 등도 뺌(클라우드 넘기기와 같은 목록, ADR-118).
- 자동 수정 평가 과제를 지금 소스에 다시 심은 v4(10/11, 과제마다 고치기 전 통과·변이 뒤 실패 확인). `scripts/run_pc_benchmarks.py` 로 PC 평가를 한 번에.
- 실제 모델 성능은 재지 않음(클라우드에 모델 없음). 상세·PC 확인 순서: docs/CLAUDE_CLOUD_20261026.md.

## 2026-10-23 — 검사 캐시 정확성·긴 요청·Office 편집 연결

- 실행된 unittest 파일별 증거와 검사 전후/현재 입력 해시로 통과 캐시 보강. 부모·하위 Workspace가 revision 공유. 독립 검사도 대상 파일 실행 확인.
- 긴 OpenCode 요청은 표준입력(실제 CLI 105,627자 원문 전달 확인). 실행 환경 오류 별도 기록·무료 세션 스트림 정리·모델/도구/독립 검사 시간 기록.
- 첨부 Office 편집 사본 다운로드와 관리자 프로젝트 문서의 승인·백업·되돌리기를 연결(ADR-115). 작은 교정의 run 서식·긴 문단·셀/ZIP 경계 보강. 실제 Office 6파일 열기·총8쪽 렌더링 확인.
- 공개 주제 실제 딥서치 점검 후 질문별 발췌·버전/식별자 추출·후속 검색 예산·요청 길이 지시 보강. 코딩 화면 재진입 검사 보고·미검증/시간 초과 표시.
- 상세: docs/CODEX_IMPROVEMENTS_20261023.md. 원본/근거/최종 검사·실행 결과는 logs/codex-improvements-20261023/ 및 두 NEXT 맨 위. 새 범용 성능 성공률로 해석하지 않음.

## 2026-10-23 오후 — 딥서치·말로 파일 고치기·자기 기능 알기

- 복합 비교·근거 조사 질문은 자동 딥서치(여러 검색·본문·출처와 한계 표시), `/research` 로 직접도.
- 채팅에서 '프로젝트 파일 이름 + 고쳐줘' 로 코딩 세션이 그 파일을 고침. 허락 '응'·'항상'·'아니', '되돌려줘'.
- 기능을 묻는 질문에 실제 명령·화면·권한 안내로 답함. `/self-check` 로 스스로 점검(부를 때만).
- 입력창 '오픈코드 API' 고르기. 워드·엑셀·PPT 서식 유지 고치기 엔진(채팅 연결은 다음).
- [공개 인수인계](docs/HANDOFF_20261023.md).

## 2026-10-23 — 하네스 검사 보강·코딩 경험으로 배우기·OpenCode 무료 모델·다시 켜기

- 독립 관련 검사를 좁히고(상수는 그 이름과 읽는 함수만, 에이전트가 마지막 수정 뒤 통과시킨 시험은 실행 기록으로 인정) 나란히 실행, 셸은 UTF-8 모드.
- 코딩 세션 결과도 배울 기록으로, 쉬는 중 자동 /dev-learn(설정), 고른 교훈을 하네스 스킬로. 교훈을 하네스·코딩 세션 지시문에.
- OpenCode 무료 모델(OpenCode 와 같은 목록에서 무료만, opencode.exe 를 통해)을 /dev-auto·/build 와 코딩 세션에서 선택. 개인 설정·기록은 보내지 않음.
- 설정 '코딩 에이전트 · API' 칸, 입력창 '코딩 세션' 버튼, 웹 '다시 켜기'와 실행기의 옛 버전 자동 재시작.
- [공개 인수인계](docs/HANDOFF_20261023.md).

## 2026-10-22 — ALEX 자체 코딩 하네스와 최신 연결 기능

- OpenCode MIT 출처와 라이선스를 보존한 ALEX 자체 하네스, `/code` 화면·API, 관련 테스트 독립 검사·한 번 재수정·테스트 보호·분할 읽기·설정 저장 수정.
- 최신 앱·웹·provider 의존 코드, 문서·검색·뒤 작업·승인 기반 클라우드 위임·RTL/EDA·미디어 연결·실시간 통역 코드와 회귀 검사를 함께 동기화했다. 도구와 모델·개인 설정은 별도다.
- MIT 라이선스를 Python 패키지에도 포함한다. HWP/HWPX 공개 시험 자료는 출처·Apache-2.0 라이선스를 함께 보존했다.
- 개발 PC만 기본 일꾼을 harness로 전환했다. 공개 기본 opencode는 유지하고 설정으로 선택한다. 평가 우위는 미입증이며 호출 중 시간 한도는 후속이다.
- 검사·한계·다음 순서: [공개 인수인계](docs/HANDOFF_20261022.md). 개인 데이터·설정·원문 인계·로그·모델·개인 게임은 제외한다.

## 2026-10-13 — GPQA Diamond 실측: ALEX 로컬 모델 171/198 = 86.4%

- 박사 수준 과학 객관식 198문제를 ALEX 와 같은 조건(생각 medium·답 최대 32,768토큰)으로 한 번씩: 물리 83/86, 화학 73/93, 생물 15/19. 발표 점수 Qwen 원본 91.7·GPT-5.6 Luna 약 92·Claude Sonnet 5 88.9 와 비교(docs/EVAL_GPQA_20261012.md).
- 한도(32,768토큰)에 걸려 답을 못 쓴 화학 문제 147번을 모델 서버 문맥 96K·128K 로 다시 풀기: 96K: 답 못 씀(96,000토큰·890초·그래픽카드 최고 13.9GB·메모리 최고 67.7GB); 128K: 오답(61,683토큰·606초·그래픽카드 최고 13.9GB·메모리 최고 68.2GB). 시험 뒤 원래 64K 로 복원.
- 공개 평가 스크립트 추가: scripts/eval_gpqa.py(데이터는 넣지 않음), scripts/eval_lcb.py(LiveCodeBench, 공식과 같은 정확 비교·안전한 시험 읽기, 다른 모델 답 채점 가능), tests/test_eval_scripts.py.

## 2026-10-12 — 생성 코드 실행 보호: 실행용 임시 폴더 안에서만 지우기 허용 (ADR-093)

- ALEX 의 코드 실행 확인(만들기·코드 답 확인)이 tempfile.mkdtemp·TemporaryDirectory 를 막아 임시 파일을 쓰는 올바른 프로그램을 실패로 보던 문제를 고침. 실행마다 작업 폴더 안 .alex-tmp 를 TEMP 로 주고 그 안에서만 지우기·이름 바꾸기 허용, 끝나면 치움. 작업 파일·폴더 밖은 계속 금지, 명시적 지우기 코드는 정적 검사가 계속 거절.
- 회귀 시험 2개 추가(임시 파일 프로그램 통과·임시 폴더 안 남음 / 정적 검사를 피한 작업 파일 지우기·덮어쓰기·폴더 지우기는 계속 차단). 전체 1,350개.

## 2026-10-12 — 로컬 모델 코딩 비교 첫 실측·평가 도구 수정

- 18과제(기본·어려움·전문가) × 3회를 사용자 PC 의 Strata(Qwen3.8-Flash-Next 125B)로 처음 실측: 54/54 통과, 과제당 중앙값 5.6초·최대 24.5초. 이 과제 묶음은 이 모델에게 천장이라 정답률로는 모델을 가르지 못한다.
- 평가 도구 수정: csv_summary 의 준비 코드(tempfile.mkdtemp)가 실행 보호의 지우기 금지에 걸려 모든 답이 실패하던 문제 — 준비 코드를 보호 전에 실행. 회귀 시험 추가.
- ALEX 코드 실행 확인도 같은 보호라 tempfile 을 쓰는 올바른 프로그램이 실패로 보일 수 있음을 기록(설계 결정 필요, 미수정). GPT-5.6 Luna 는 API 키가 없어 미측정 — 답안 가져오기 방법을 docs/MODEL_EVAL_STRATA_20261012.md 에 정리.

## 2026-10-12 — PC의 방 이동·코드 복사(Codex)와 클라우드 보완 합치기

- PC 에만 있던 방을 오가며 대화·코드 블록 전체 복사(2026-10-11 Codex)를 공개본에 올리고, 같은 날 클라우드 보완(인증·업로드·검증)과 합쳤다.
- 두 쪽이 각각 고친 '대기 요청 취소가 실행 중인 요청의 콜백·취소 신호를 지우던' 문제는 차례가 온 뒤에만 공유 상태를 잡고 끝나면 원래 값으로 되돌리는 한 가지 방식으로 정리.
- 프로젝트 첫 메시지 두 번 보내기 방지는 방 이동 기능의 화면 이동 잠금(navigating)으로 처리(클라우드의 lock(true) 방식은 방별 요청 흐름과 맞지 않아 쓰지 않음).
- 스트림에 'running'(차례가 와서 답을 만들기 시작) 사건이 생겨 test_titles·test_local_beta 의 사건 순서 기대값을 맞춤.
- 검증: 전체 1,347개 통과(6 SKIP), 릴리스 검사 PASS, Edge 브라우저 방 이동·코드 복사 27항목 + 관리자·다른 사용자·대기열·휴대폰 너비 흐름 통과(JS 오류 0).

## 2026-10-12 — 실행 검증 확대·인증·업로드·프로젝트·호환성·배포 보완

- 실제 쿠키로 로그인 만료·계정 중지 뒤 로컬 관리자 전환을 막는다. 동시 실패 횟수를 로그인 전용 잠금으로 보호하고, 임의 이름이 기존 계정 잠금을 지우지 못하게 한다.
- 지식 자료 저장 뒤 모델 잠금을 풀고 응답해, 바로 뒤의 요청이 불필요하게 409가 되던 경쟁을 수정. 프로젝트 첫 대화 준비도 입력 잠금 범위에 포함한다.
- Python 3.10의 파일 경로 별칭 때문에 unittest 검사를 0개로 읽던 집계를 code object 식별로 수정. 격리 코드 검사 일꾼의 Linux pygame 글꼴 초기화를 지원하고 생성 코드의 기존 I/O 차단은 유지한다.
- 공개 배포가 비공개 인계 파일을 필수로 요구하던 문제를 수정. 개인 설정·runtime·Node 의존성·브라우저 결과물을 배포에서 제외한다.
- 최종 1,342개: Python 3.10·3.11·3.12 모두 릴리스 PASS(Windows 전용 3개, pygame 미설치 버전은 1개 추가 SKIP). 실제 HTTP 취소·HTTPS 브라우저·모바일·PWA·합성 DB 이전·복구도 확인했다.
- 18과제 모델 비교 도구와 채점기 회귀 7개 추가. GPT-5.6 Luna·ALEX 실모델은 연결되지 않아 점수·시간 비교는 미실측. 상세 [전체 검증](docs/FULL_VALIDATION_20261012.md), [비교 실행](docs/MODEL_COMPARISON_GPT56_LUNA_20261012.md).
- 직전 커밋 404b25d를 GitHub backup/pre-full-validation-20261012-404b25d와 검증된 Git bundle로 보존했다.

## 2026-10-12 — 대기열 공유 상태 복원·PWA 설치 패키지 보완 (Codex)

- 대기 요청이 취소되거나 연결·헤더 전송에 실패하면 실행 중 요청의 콜백·모델 중단 신호를 건드리지 않는다. 모델 잠금을 얻은 뒤 현재 콜백·모델 목록·중단 신호를 저장하고, 자신이 변경한 상태만 잠금 안에서 원래 값으로 복원한다.
- 대기 중 모델이 교체된 경우도 현재 모델에 중단 신호를 연결한다. 오류·생성 취소 후에도 기존 상태를 복원한다. 회귀 테스트 5개 추가(수정 전 실패 확인).
- wheel에 `manifest.webmanifest`·192/512 PNG 아이콘을 포함한다. 격리한 wheel 설치에서 정적 파일 8개 바이트 일치, PWA 경로 4개 HTTP 200·MIME 확인.
- 공개본 전체 1,323개: 1,318 PASS·5 SKIP·0 FAIL, 51.928초. 릴리스 종합 PASS. 실제 GPU·모델·사용자 PC DB는 이번에 실행하지 않았다.
- 수정 전 `4a39bf4`는 GitHub `backup/pre-hardening-20261012-4a39bf4` 브랜치와 검증한 Git bundle에 보존. 상세 [검증·롤백 안내](docs/CODEX_HARDENING_20261012.md).

<!-- CLAUDE-MULTIUSER-UI-20261011 -->
## 2026-10-11 23:10 KST — 새 화면(Claude·ChatGPT 같은 채팅형)·프로젝트·사람별 구분·대화 제목

- 화면 다시 그림: 왼쪽 칸(새 대화·검색·프로젝트·날짜별 대화·메뉴), 가운데 대화, 둥근 입력창. 밝은 테마 기본 + 설정에서 어둡게/시스템 따라. 휴대폰 너비에서는 왼쪽 칸이 서랍.
- 프로젝트(Gemini Gems 처럼): 이름 + 지침 + 자료 + 그 안의 대화. 지침과 자료는 그 프로젝트 대화에서만 쓰인다.
- 여러 사람이 쓰기 준비(ADR-092): 계정·사람별 대화/기억/할 일/만든 파일·관리자 권한·모델 대기열(순번 표시). 로그인 화면은 정식 버전에서 열기로 하고 지금은 숨김 — 이 PC 는 주인으로 바로 들어온다. DB 스키마 v7(처음 켤 때 자동 백업 후 이전).
- 대화 제목: 첫 답 뒤 모델이 주제로 짓고(약 1초), 제목을 눌러 직접 바꿀 수 있다.
- 테스트: test_multiuser 15·test_titles 5 추가, 전체 1318 중 실패는 별도 게임 파일(pygame) 의존성 감사 1개.

## 2026-10-11 — 로컬 문서 지식 기능 + 검색 품질·안전 수정

- 로컬 문서 지식: `/learn-file 경로`, `/knowledge`, `/knowledge-search`, `/ask-knowledge`, `/forget-knowledge`, 웹 파일 올리기. PDF(별도 venv의 pypdf 일꾼)·UTF-8 텍스트·Verilog 지원, 원문 사본·SHA256·쪽수 보존, 출처 표시. 가중치 학습이 아니다.
- 검색 품질(K01): 디지털 설계·반도체 한영 용어 사전(`jarvis/knowledge_terms.py`)으로 '래치'↔latch 처럼 교차 언어 검색, 질문을 핵심 개념·일반 낱말로 나눠 관련 없는 자료를 붙이지 않는다. 합성 평가 1위 정답 1/11→10/11, 무관 질문 오검출 1/6→0/6.
- 안전·정합성: 자료를 인용한 대화는 Cloud 로 갈 때 지난 대화·요약을 보내지 않음(K03), 등록·삭제 실패 시 사본 정리(K04), 실제로 문맥에 온전히 들어간 조각만 출처로 표시(K05).
- 의존성 감사에 PDF 일꾼(별도 환경) 추가, 학습 패키지 시험의 의도(학습 패키지는 학습 일꾼에만) 유지.

## 2026-10-11 — LTX 소리 포함 영상 공개본 연결

- 기존 공개본의 이미지 엔진 껍데기를 실제 ComfyUI 연결로 복원. LTX 영상/소리 생성, 직접 Wan/LTX 선택, 자연어·참고 그림, 두 단계 확대와 오디오 결합을 지원한다.
- 긴 소리 후행 지시의 오분류와 LTX 설명600자 잘림을 수정했다. 영상 생성 안내는 실측 기반 약2분으로 바꿨다.
- 최신 실사용 core/app과 함께 필요한 config·provider·runtime·web 등 연결 코드와 회귀 검사를 동기화했다. 개인 설정·모델·영상·대화 DB·작업 인계 원문은 공개하지 않는다.
- 개발 PC 원본 전체1264=1258PASS+5SKIP+별도게임pygame감사1FAIL. 공개본은 그 별도 게임을 포함하지 않는 별도 사본이며, 사본 전체검사 결과는 게시 기록에 따로 남긴다. 상세 docs/LTX_VIDEO.md 및 CODEX_LTX_LIVE_TEST_20261011.md.

# Changelog

## 2026-10-10 12:34 KST — Strata 후속 보완·전체 회귀 검사 활성화 및 평가 의미 검토 (Codex)

- Strata 시작에서 Ollama설치/모델준비를 분리하고, loaded의 불명확한 상태를 반환 성공으로 인정하지 않으며, 명시적 Strata 설정 선택을 우선 처리.
- 전체 회귀검사를 실사용에 활성화. 모델·문맥·GPU예약/전력 유지. Strata 계약6개 및 개발 중단/미디어 종료/영상 라우팅 계약각2개 추가. Ollama 시작 검사가 실제 사용자 설정에 의존하던 부분은 명시적 Ollama Settings로 분리.
- Claude 평가 인수: 유효6개 기존엄격통과, 그중3개 후보는 추가검사로 보호/동작 누락을 확인해 의미상 거절. 원본평가 유지 및 전체후보/재현로그 보존; 운영 developer/imagegen/router 유지.
- 전체1176개(5SKIP) RELEASE PASS, 정상재시작 후 실행/디스크코드a0f1e305065ef27a 일치. 기존 Strata서버/모델적재 유지. 콜드시작·새GPU평가는 미실시.
- docs/ALEX_STRATA_EVALUATION_20261010.md 및 docs/CODEX_STRATA_REVIEW_20261010.md, 증거/백업 logs/codex-strata-review-20261010/. 실제운영반영8파일, 두NEXT최상단 최종인계.

## 2026-10-10 11:22 KST — Strata 후속 검토 및 적용 대기 수정안 (Codex)

- Claude GPU 평가에 개입하지 않고 별도 사본에서 Strata 시작의 Ollama 의존과 불명확한 loaded 상태의 반환 오판을 수정. **운영 적용은 아직 하지 않음.**
- 원본/수정 사본·SHA-256·diff·검사 로그 보존. 관련 검사 28개 통과, 전체 릴리스/실제 콜드 스타트는 미검증.
- 기존 평가 사전 검사 재사용: 원래 과제6/11 유효, Claude 재개 과제5/5 유효. 첫 과제1개 엄격 성공809초를 전체 성능과 구별해 기록.
- 상세 `docs/CODEX_STRATA_REVIEW_20261010.md`, 증거 `logs/codex-strata-review-20261010/`, 두 NEXT 맨 위에 평가 종료 후 적용 절차 인계.

## 2026-10-10 — Strata 로 Qwen3.8-Flash-Next(125B MoE) 연결 준비 (ADR-086, claude)

- `StrataProvider`(OpenAI 호환 SSE, 생각 상한·도구 호출·중단·문맥 초과 처리) + 설정 `local_backend`(기본 ollama)·`strata_*`(이 PC 주소만).
- strata 모드: 대화·코딩·요약·오픈코드 일꾼이 Strata 하나를 쓰고, 그림·`/unload` 는 Strata `/unload` 로 그래픽카드를 비우며, 런처가 Strata 서버를 띄운다.
- Strata 0.1.41 설치(`C:\ALEX\tools\strata`), IQ3_S 모델 다운로드(`C:\ALEX\tools\Strata-data`). 실모델 측정은 사용자 입회 후.
- LoRA 학습 데이터 생성기 기록(ADR-085). 새 검사 16개, 전체 1164개 RELEASE PASS.

## 2026-10-05 15:20 — LoRA 학습 파이프라인: 작은 모델(4B) 학습 → GGUF → Ollama (ADR-084, claude)

- `scripts/lora.py` prepare·train·export·eval·status + 학습 환경 일꾼 `scripts/lora_worker.py`(transformers + PEFT). 학습 환경 `C:\ALEX\tools\lora`.
- 27B 는 16GB 로 학습 불가(공개 자료), 4B(Qwen3-4B-Instruct-2507) 는 가능. Ollama 0.34.1+ 는 ADAPTER 가 없어 합친 뒤 q8_0 GGUF 로 등록.
- 학습 데이터는 승인된 '질문 → 좋은 답'(/good)만 — 채팅 교정·교훈은 답이 아니라서 뺀다. 지금 train 0건·eval 1건이라 실제 학습은 데이터가 쌓인 뒤.
- GPU 는 --yes·빈 그래픽카드·Ollama 모델 없음 확인 후만, VRAM 상한 11GB. CPU 만으로 전체 흐름 확인(학습 16초, 등록 1분 45초).
- `/growth`·모델 자기 설명에 실제 기능 반영(내보내기 기록과 실제 LoRA 학습을 나눠 셈). 의존성 검사에 학습 일꾼 예외 명시.
- 22개 새 검사, 전체 1133개 RELEASE PASS, ALEX 재시작 `8762cda3707c7077`. 증거 `logs/claude-lora-20261005/`.

## 2026-10-05 13:14 — OpenCode 반환 보호·검증 보강·릴리스 병렬 연결 (Codex)

- OpenCode /dev-auto·/build에 기존 GPU 반환 확인 연결(추가 VRAM 예약 없음), 실패·취소 시 실행 차단 및 상태 보존.
- Worker 비정상 종료, /build의 시간 초과/누락·작성 거절/검사 중 소스 변경을 완료 처리하지 않음. 실패 학습 상태 보완.
- 병렬 discovery의 load_tests 누락·0개/모두 skip 오판·전체 시간 제한 수정. 자식 로그 UTF-8 및 extra_env 병합 보완.
- 릴리스 검사도 같은 suite runner 사용, --test-workers 1로 직렬 선택. 최종 1110개(5 SKIP) RELEASE PASS, 72.069초.
- 대기 중 웹 서버 재시작 완료, 코드 06e25b8d8d014df6 일치. 실제 모델 실행 없음. 상세 `docs/ALEX_CONTINUATION_REVIEW_20261005.md`, 원본/증거 `logs/codex-continue-20261005/`.

## 2026-10-05 10:50 — 전체 검사 동시 실행: 197초 → 61초 (claude)

- /dev-auto 회귀 검사의 전체 테스트를 tests/ 파일 단위로 최대 8개 동시에 돌리고 하나의 unittest 결과로 합친다(판정 기준 그대로).
- 실제 ALEX 전체 3회 모두 OK(1090개·5 건너뜀), 61·61·60초. 새 검사 4개(`tests/test_parallel_suite.py`).

## 2026-10-05 09:55 — /dev-auto 일꾼을 오픈코드로(판정·승인은 ALEX) (claude, ADR-083)

- 공개 자료의 표준 조합(검증된 에이전트 + 로컬 Qwen3.8-27B)을 따른다. 새 `jarvis/opencode_worker.py`, `developer._worker_bundle`.
- 오픈코드는 격리 복사본의 사본에서 일하고, ALEX 가 범위 밖·tests/ 변경을 버린 뒤 기존 합격·전체 검사·덜어내기·승인으로 판정. 셸은 unittest 만 허용.
- 설정 `coding_worker`(기본 opencode, 없으면 예전 방식), `opencode_*`. 새 검사 9개, 전체 1084개 RELEASE PASS.
- /build 도 오픈코드가 만들고 ALEX 가 검사를 다시 돌려 확인. 실측: v3 과제 엄격 통과(오픈코드 42초, 전체 444초). 전체 1086개 RELEASE PASS.

## 2026-10-05 09:10 — /dev-auto 덜어내기가 맞는 수정을 망가뜨리던 버그 + OpenCode 1과제 비교 (claude)

- 덜어내기 전 후보가 고쳐 둔 '수정 전부터 실패하던 테스트'가 덜어낸 뒤 다시 실패하면 덜어내지 않은 후보로 되돌린다(`developer.py`).
  실측: 역사 gpumodel_config(Q3) — 모델 수정은 전체 통과였는데 덜어내기가 환경변수 줄을 빼서 config 테스트 2개가 다시 깨졌다. 새 검사 1개(수정 없이 실패 확인).
- OpenCode(사용자 지시) vs ALEX 같은 과제: 둘 다 합격 통과·config 2개 깨짐, 시간 46초 vs 765초. `/v1` 지연 버그는 이 PC 에서 없음. 전체 1075개 RELEASE PASS.

## 2026-10-05 08:20 — 코드는 채팅 답으로, 확인은 뒤에서 (claude, 아스트라 보강 이어받음)

- 사용자: "코드를 짜서 나한테 주는 게 아니라 그냥 직접 만들어버리면 어쩌자는 거야". 채팅의 "~ 만들어줘"·코딩 후속은 다시 코드를 답으로 준다.
- 새 `jarvis/code_check.py`: 답의 코드를 보이지 않는 임시 폴더에서 6초 실행(아스트라의 `build_runner.py` 감시 실행기), 시작하자마자 오류면
  실제 오류로 최대 2번 고쳐 최종 코드를 답으로, 답 끝에 실행 확인 결과. 창·입력·미설치 패키지·위험 코드는 실행하지 않고 그렇게 적는다. 설정 `coding_code_check_enabled`.
- 파일을 직접 만드는 만들기 루프는 `/build 요청` 명령으로만. 바로 다음 "고쳐줘"는 같은 폴더에서 이어서. 겹친 작업 폴더 경로 정리.
- 아스트라 보강(실행 중 감시·실제 검사 수 집계·취소·경로·환경변수) 유지. 새 검사 12개, 전체 1071개 RELEASE PASS.
- 08:35: 코드 조각은 '실행 확인 안 함'으로 표시, pygame 2.6.1 설치(사용자 승인), 실행 감시가 레지스트리 읽기·글꼴 폴더 읽기 허용(쓰기·삭제는 차단).
  예전 pygame 테트리스 5개가 실제로 실행 확인됨. 전체 1074개 RELEASE PASS.

## 2026-10-05 02:00 — 코딩 원인 파악·수정: 만들기 루프, 코딩 후속 경로, 생각 넘침 재시도 (claude)

- 원인 실측: ALEX 프롬프트 A/B(그대로/짧게 생각 뺌/질문만)는 같은 비율로 틀림 → 프롬프트는 원인 아님. 한 번에 제대로 도는 테트리스 Q3 3/9 vs Q4 4/4.
- 새 `jarvis/build_loop.py`: 새 프로그램 부탁은 코딩 모델이 작업 폴더에 쓰고 검사 파일을 실행해 통과할 때까지 고침(verify-on-stop, 공개 하네스 연구 반영).
  후속 고침은 같은 폴더에서 이어서. 정적 위험 코드 차단·폴더 밖 금지·20초 제한. 설정 `coding_build_loop_enabled`(기본 켬).
- 코딩 대화의 코딩 단어 없는 후속 질문도 코딩 모델로(`storage.last_route_destination`).
- 생각이 답 자리까지 먹으면 스트림에서 끊고(`ThinkingTooLong`) 생각 없이 한 번 다시 답함. 답 끝에 표시.
- 실모델 확인(테트리스): Q3 루프 2분 43초 검사 통과(실행 정상·회전 7/7). Q4 루프는 15분 안에 못 끝냄(프로그램은 정상, 스스로 쓴 검사의 기대값 실수).
  실패한 실행에 프로그램의 실제 함수 목록을 함께 돌려주게 고침, 호출 30·시간 15분 상한.
- 검사: 새 28개(test_build_loop 18, test_coding_followup 10). 전체 1041개(1036 PASS·5 SKIP) RELEASE PASS. 상세 `docs/ALEX_CODING_ROOTCAUSE_20261005.md`.

## 2026-10-04 23:40 — 사용자 요청: 추가 VRAM 예약 제거 (Codex)

- Ollama 최소 1.5GiB 예약 및 ComfyUI 명시적 예약 인자 제거. 메모리 반환 확인과 실패 시 실행 차단 유지.
- 관련 92개 검사 PASS, GPU 실행 없음. 백업/차이: `logs/codex-gpu-stability-20261004/remove-reserve-baseline/`, `remove-reserve.diff`.

## 2026-10-04 23:37 — GPU 모델 전환 보호 및 VRAM 예약 (Codex)



- Ollama 언로드 실패/잔류 모델 시 이미지·영상 생성 차단. 잘못된 상태 응답을 성공으로 처리하지 않음.

- 미디어 대기열과 PyTorch 메모리 반환을 확인한 뒤 일반/스트리밍 대화 실행. 설정 변경 후에도 보호 재연결.

- 자체 시작 엔진에 1.5GiB VRAM 예약(강제 사용 상한 아님). 모델/드라이버/하드웨어 설정 유지.

- CPU 모의 검사 포함 전체 1010개(1005 PASS, 5 SKIP), RELEASE PASS. 실제 GPU 장애 해결은 미검증, GPU 실행 보류 유지.

- 상세 `docs/GPU_STABILITY_FIX_20261004.md`, 원본/증거/검사 `logs/codex-gpu-stability-20261004/`.



## 2026-10-04 22:23 — GPU 소실·팬 과속·모니터 신호 끊김 인수인계 (Codex)



- `nvidia-smi`가 GPU 소실을 보고해 ALEX 웹 서버를 lifecycle로 종료하고 ComfyUI GPU 프로세스를 정리한 뒤 Windows를 재부팅했다.

- 재부팅 후 RTX 5080이 35°C·GPU 0%·약 33.62W로 복구됨을 확인했다. 현재 8765·11434 리스너는 없으며 ALEX/Ollama는 다음 실행 때 시작해야 한다.

- 코드·모델·설정은 변경하지 않았다. 상세 상태와 백업은 `logs/codex-handoff-20261004-reboot/`에 남겼다.



## 2026-10-04 21:05 — 코딩 평가 오판 방지·목표별 검사 보강 (Codex)



- 기존 합격 검사 실패를 변이 감지로 오인하지 않도록 원본 기준선 확인. 완료 요약·subTest·unexpected success를 기존 엄격 판독기로 확인.

- OpenCode 조사 인계 반영: 한 과제만 허용, 시간 초과/비정상 종료/범위 밖 최종 변경 성공 제외, 전체 이벤트 로그 보존. 실제 도구·모델 실행 없음.

- 다중 합격 테스트 및 preflight 지원. 새 history-contracts-v3 정의는 outcomes의 insights 기능, GPU 모델 설정의 환경변수 계약까지 포함. 과거 원본 및 결과 보존.

- 계약 검사4개 추가, 과거 우회/일부 복원 오류2개 검출. 별도 도구 검사24개 PASS. 전체999개(5 SKIP), RELEASE PASS.

- 상세 `docs/ALEX_IMPROVEMENTS_AFTER_REVIEW_20261004.md`, 백업/증거 `logs/codex-improvements-20261004/`. 런타임/모델/하드웨어 설정 미변경.



## 2026-10-04 20:04 — 남은 기존 실패 기록(Codex Luna 제안 A)·생성 중 취소 기록·OpenCode 비교 준비 (claude)



- 회귀 검사가 '수정 전부터 실패하던 것 중 남은 것'을 이름으로 기록·표시(`verification_completion` = suite_passed | residual_failures). 정책은 그대로(새 실패만 막음).

- 모델이 수정안을 만드는 도중의 취소(GenerationCancelled)도 `verification_status=cancelled` 로 끝냄(R3 와 같은 계약). 테스트 3건 추가, 관련 114 OK, 전체 **995 OK, RELEASE PASS**.

- 평가 도구 `--solver opencode`(공개 에이전트 비교), OpenCode v1.18.34 를 `C:\ALEX\tools\opencode` 에 로컬 전용 설정으로 설치(실행 시험 전). 보고서 `docs/ALEX_CODING_STATUS_REPORT_20261004.md`.



## 2026-10-04 11:20 — 회귀 검사 판정 R1~R3(Codex) + 실제 전체 검사 출력 읽기 고침(claude) (ADR-078 보충 2)



- Codex 재검토 R1~R3 를 Codex 가 별도 폴더에서 구현·검사 후 반영 전에 멈춤 → 사용자 "받아서 이어서 마무리" → claude 가 원본 해시 확인 뒤 그대로 반영.

- 실제 ALEX 전체 검사로 확인하다 발견: print 잡음이 요약 뒤에 붙고 끝 3,500자만 남아 정상 통과도 '읽을 수 없음'. 전체 로그에서 실패 목록·요약을 추리는

  `_suite_digest`(기본 전체 검사 실행기만), 같은 ID 반복은 '#2'. 테스트 6건 추가, 전체 **992 OK, RELEASE PASS**, 앱 `e6413740ce60f17c`.

- 얼린 복사본 2 를 이 코드로 다시 얼림(앞의 것 `.pre-r123`). 평가는 대기열 7 로 이어 감.



## 2026-10-04 09:55 — 수정 전 읽기 전용 진단 근거(기본 꺼짐) — Codex 가이드 우선순위 2 (ADR-080, claude)



- 새 `jarvis/diagnostics.py`: 실패 문구가 나오는 조건, 트레이스백 줄, 실패한 테스트가 쓰는 준비 코드(한 단계)를 지금 후보에서 찾아 남는 자리에만 붙인다. 모델 호출 없음, 읽기만.

  설정 `coding_diagnostic_context`, 평가 옵션 `--diagnostics`, `/status` 줄. 실제 remote 실패 출력에서 단서(`alex_health` 가 app='jarvis')를 골랐다.

- 테스트 12건, 전체 **973 tests OK, RELEASE PASS**(`logs/claude-diagnostics-verify-20261004.txt`). 앱 재시작 `8cec5b20c63eefa4`. 얼린 복사본 2 를 이 코드로 다시 얼림

  (`320c4f61027159b3`; 앞의 것들은 `.pre-verdict`·`.pre-diag`). 사용자 "왜 이렇게 오래 걸려" → 평가를 핵심 비교만(대기열 6: 역사 16K → v2 근거 끔 → v2 근거 켬).



## 2026-10-04 09:45 — 회귀 검사 판정 보완(미검증·취소를 통과로 올리지 않음) — Codex 가이드 우선순위 1 (ADR-078 보충, claude)



- Codex(gpt-6-astra) 가이드 `docs/CODING_AGENT_PRIORITY_GUIDE_20261004.md` 의 지적 확인 — 맞다. 판정을 verified·new_failures·unverified·cancelled 로 나누고, 불완전한

  실패 목록으로 빈 집합 통과를 막고, 덜어내기·최종 수정안의 취소를 처리했다. 결과 기록에 verification_status/scope/reason.

- 테스트 7건 추가(+3건 문구), 전체 **961 tests OK, RELEASE PASS**. 앱 재시작 `1bd2f0754d9f8773`. 얼린 복사본 2 를 이 코드로 다시 얼림(`a8464ed2b3b413b1`, 옛 것은

  `C:\ALEX\eval-frozen-20261004.pre-verdict`). 백업 `logs/claude-backup-20261004-verdict/`, 작업 기록 `logs/claude-regression-verdict-20261004.md`.

- 우선순위 2(실패 근거 수집)는 다음 작업. 성공률 개선이 아니라 판정의 정직성 고침이다.



## 2026-10-04 08:45 — XLSX·PDF·PPTX·DOCX 생성·문서 다운로드 (Codex)



- `/export 형식 [last|all] [제목]`, `/document 형식 제목 | 내용`: 최신 답변·전체 대화·직접 입력을 문서로 저장. 기존 `/export` Markdown 유지. Web UI 네 형식 버튼과 토큰 인증 다운로드 추가.

- 새 표준 라이브러리 기반 `jarvis/documents.py`: 한글·Markdown 표·코드, XLSX 별도 표 시트/문자열 안전 저장/행 높이, DOCX 제목/실제 표, PPTX 편집 텍스트/긴 본문 슬라이드 분할. PDF는 설치 브라우저의 GPU 꺼진 별도 headless 프로필로 A4 인쇄. 외부 런타임 패키지 없음.

- `scripts/document_export.py` CPU 전용 CLI. 이름/경로/크기 검증, 파일 덮어쓰기 방지, HTML 이스케이프, PDF 타임아웃 처리.

- 관련 **107 tests PASS·1 SKIP(총108)**; security13/13·beta·dependency·3.10 syntax·JS syntax PASS. 네 실파일 생성·독립 Office 판독·PDF 한글 추출/렌더 확인 PASS. `logs/codex-document-integration-20261004.txt`, `logs/document-export-smoke-20261004/receipt.json`.

- Claude GPU 평가 보호: 설정/평가/모델 미변경, 앱 미재시작, 전체 회귀 미실행. 평가 뒤 정상 재시작/웹 F5로 활성화하고 전체 release 검증할 것. 안내 `docs/DOCUMENT_EXPORT_20261004.md`, 백업 `logs/codex-backup-document-export-20261004/`.



## 2026-10-04 08:50 — 코딩 에이전트 실패 원인 재점검·고침: 너그러운 찾기, 출력 한도, 넓힌 API 요약, 거부 원문 기록 (ADR-079, claude)



- 평가 177회차 중 17% 가 형식 문제로 버려졌다(PERFORMANCE 38). 찾을 부분이 공백·들여쓰기·빈 줄만 다르면 한 곳일 때 받는다. 긴 문맥에서는 남은 자리만큼 출력 한도(최대 16K).

  API 요약이 가져온 함수·클래스, `core.storage` 같은 매개변수 경유 속성, 생성자 매개변수 속성까지 담는다(역사 과제 실패 모양). 거부된 수정안 원문을 기록에 남긴다.

- 09:0x 덧붙임: 긴 문맥에서는 지시(실패 출력·테스트 코드) 상한도 키운다 — 48K 에서도 3,000자로 잘려 테스트가 기대하는 것이 빠졌다(ADR-079 보충).

- 관련 테스트 173 OK. 08:3x 검증은 그 사이 Codex 가 app.py·web.py·documents.py 를 고쳐 지문 테스트 1건만 흔들렸고(코드 문제 아님), Codex 문서 작업이 끝난 뒤

  **Codex 변경까지 합쳐 954 tests OK, RELEASE PASS**(`logs/claude-combined-verify-20261004.txt`), 앱 재시작 `59282b5a652e08c8`(Ollama 유지).

  고친 코드를 얼린 복사본 2(`C:\ALEX\eval-frozen-20261004`, 지문 `73fa55022011784d`)로 역사 과제 11개를 다시 잴 예정(대기열 5).

  백업 `logs/claude-backup-20261004-regression/`.



## 2026-10-04 08:25 — /dev-auto 회귀 검사(기본 꺼짐), 전체 검사 시간 상한, 모델 4개 정리 (ADR-078, claude)



- 설정 `coding_regression_check`: 합격 테스트 통과 뒤 복사본 전체 테스트로 이 수정이 새로 깨뜨린 테스트를 찾아(수정 전에도 실패하던 것은 빼고) 다시 고치게 한다.

  덜어내기가 다른 테스트를 깨면 취소. 평가 옵션 `--regression-check`. 실패 유형 D(9/30 hard app, 10/4 summary-perf_web) 대책.

- 고침: 전체 테스트 실행 상한 120초 → 900초 — ALEX 전체 검사(131~179초)가 /dev-verify 에서 시간 초과였다.

- 테스트 6건, 전체 **920 tests OK, RELEASE PASS**(`logs/claude-regression-verify-20261004.txt`). 앱 재시작 `bf731267e8d69399`. 백업 `logs/claude-backup-20261004-regression/`.

- 사용자 결정으로 Ollama 모델 4개 삭제(mistral-large:123b·qwen3.5:122b·gemma4:31b·muse-glimmer:30b, 약 193GB).

- 평가 대기열 4(GPU) 진행 중 — 02:06 GPU 소실로 멈춘 평가를 우선순위대로(48K + API 요약부터).



## 2026-10-04 02:10 — 밤샘 코딩 평가 결과·GPU 소실로 중단 (claude)



- 실패 5개 48K = 3/5(9/30 16K 0/5, API 요약 켜고 2/5) — 큰 파일에서 긴 문맥(ADR-076) 효과 확인, 대가 시간 2배. ALEX 역사 과제 16K = 4개째까지 1/4, 실패는 모두 '다른 파일의 API 모름'.

- 02:06 GPU 소실("GPU is lost") — 안전 감시가 멈추고 ALEX 정상 종료, 02:36 PC 자동 종료(사용자 요청). 같은 계열 세 번째 → 다음 GPU 작업 전에 장비 안정성 확인. PERFORMANCE 37.



## 2026-10-03 23:55 — 코딩 평가 기반: 긴 문맥 측정, ALEX 역사 과제, 안전 감시 대기열(claude, 평가는 밤새 진행 중)



- 측정(PERFORMANCE 36): 27b 의 core.py = 33,524토큰, 48K·48층이 최적(입력 1,128·생성 10.9 tok/s). 52층부터 VRAM 넘침.

- **ALEX 역사 과제**(계획 0번) `logs/probe/history_tasks.py`: Claude·Codex 가 실제로 고친 변경(logs/*backup*)의 소스 덩어리를 지금 코드에서 되돌리고, 그 변경이 더하거나

  바꾼 테스트가 실패하면 과제로(SWE-bench 방식). `--validate` 로 두 번씩 재확인, `history_subset.py` 로 12개. 생성 27개 이상.

- 평가 도구: `claude_eval_guard_20261003.sh`(83°C·새 CUDA 오류면 멈춤), 대기열 `claude_eval_queue2_20261003.sh` — Codex 가 밤새 코드를 고치므로 **얼린 복사본**

  `C:\ALEX\eval-frozen-20261003`(지문 `1229422665c9a964`)에서 돈다. 비교표 `claude_compare_20261003.py`. 끝나면 30분 뒤 PC 종료(사용자 요청).

- 고침: claude 가 넣은 독립 풀이 테스트가 실패 출력의 실행 시간까지 비교해 CPU 부하에 흔들렸다 — 시간 줄을 빼고 비교(`tests/test_developer.py`).

- 9/30 실패 유형 분석(계획 7절): 생각 소진·형식 오류·엉뚱한 곳 고치기·다른 테스트 깨뜨림. 마지막은 새 후보 ⑩ 회귀 검사.



## 2026-10-03 23:25 — 상단 종료 UI와 이미지 생성 로딩 (Codex)

- (공개판에서 제외: 이미지·영상 생성 관련 변경)

## 2026-10-03 23:15 — /dev-auto 독립 풀이: 설정 추가(기본 1 = 예전과 같음) (ADR-077, claude)



- 계획 2번. 사용자 "이어서 작업해" — 코드만, GPU 평가 없음. 설정 `coding_auto_attempts`(1~5): 한 풀이(최대 3회차)가 실패하면 복사본을 처음 상태로 되돌려

  새로 푼다(앞 풀이의 실패는 주지 않음, 첫 통과에서 멈춤). 결과 기록 `attempts`, `/status` `dev_auto` 줄, 평가 옵션 `--auto-attempts`.

- 테스트 3건(`tests/test_developer.py` AutoLoopTests: 원본에서 다시 시작, 실패한 풀이의 새 파일 제외, 설정 횟수만큼), 전체 **907 tests OK, RELEASE PASS**

  (`logs/claude-attempts-verify-20261003.txt`). 앱 재시작 `1d7a83519e2415fe`. 백업 `logs/claude-backup-20261003-attempts/`.



## 2026-10-03 18:40 — 코딩 묶음 수정안만 긴 문맥으로: 설정 추가(기본 꺼짐) (ADR-076, claude)



- 계획([docs/CODING_AGENT_SONNET_PLAN_20261003.md](docs/CODING_AGENT_SONNET_PLAN_20261003.md)) 1번. 사용자 "평가는 하지 말고 코드만" — GPU 측정·평가 없음.

- 설정 `coding_num_ctx`·`coding_num_gpu_layers`·`coding_context_char_budget`(jarvis.local.json·환경변수 `JARVIS_CODING_*`). 켜면 묶음 수정안을 만드는 동안만

  모델 문맥·GPU 층 수·글자 예산을 바꿔 core.py 같은 큰 파일을 발췌 없이 보이고, 끝나면 대화 값으로 되돌린다. 전체 다시 쓰기 판정은 그대로(출력 한도 때문).

- 평가 전용 `coding_agent_eval.py --coding-num-ctx 65536 [--coding-num-gpu N] [--coding-context-chars N]`. `/status` 에 `coding_runtime`.

- 테스트 `tests/test_coding_context.py` 7건, 전체 **904 tests OK, RELEASE PASS**(`logs/claude-codingctx-verify-20261003.txt`). 앱 재시작 `9cea2756e8f21ad9`.

  원본 백업 `logs/claude-backup-20261003-codingctx/`. 켜기 전 측정 항목은 ADR-076.



## 2026-10-03 17:30 — 글로 만드는 영상을 Wan 2.2 14B 로 (ADR-075, claude)

- (공개판에서 제외: 이미지·영상 생성 관련 변경)

## 2026-10-03 16:51 — 이미지·영상 직접 프롬프트 입력 (Codex)

- (공개판에서 제외: 이미지·영상 생성 관련 변경)

## 2026-10-03 16:34 — 코딩 작업 증거와 합격 기준 고정 (Codex)



- `developer.py`: 승인된 수정안과 실제 검증 버전의 지문을 연결해 수동 변경분이 `사용자 채택`으로 보이지 않게 했다. `/dev-auto`는 합격 기준 테스트의 복사·실행·최종 제안 사이 변경을 탐지해 거짓 통과 수정안을 제안하지 않는다.

- 회귀 검사 4개 추가. 코딩 관련 111 OK(1 skipped). 전체 888 tests 중 기존 이미지 경계 검사 2건 실패, 나머지 7 gate PASS; `logs/codex-release-verify-third-20261003.txt`. 앱은 재시작하지 않았다. 상세는 `docs/CODING_CONTINUATION_20261003_2.md`.



## 2026-10-03 13:04 — 캐릭터 유지·그림 올리기(코드), 판타지 안내문, 영상 시험 (claude)

- (공개판에서 제외: 이미지·영상 생성 관련 변경)

## 2026-10-03 11:59 — 그림의 경계(ADR-073 보충, claude)

- (공개판에서 제외: 이미지·영상 생성 관련 변경)

## 2026-10-03 11:35 — 자연어로 그림 그리기: ComfyUI + Z-Image Turbo (ADR-073, claude)

- (공개판에서 제외: 이미지·영상 생성 관련 변경)

## 2026-10-03 — 코딩 에이전트 테스트 증거·교훈 연결 안정화 (Codex)



- `developer.py`: 0개/전부 건너뛴 unittest를 PASS로 기록하지 않음. `/dev-auto` 복사본에서 선택 테스트 누락·수정 대상 누락/변경을 모델 호출 전에 거부. 기존 코드의 거짓 PASS를 임시 프로젝트에서 재현했다.

- `insights.py`: 다음 자동 수정 기록의 검사 결과가 이전 기록의 학습 근거로 붙지 않게 하고, 예산을 넘는 긴 교훈 뒤의 짧은 교훈도 검색함.

- Windows 전체 검증 858 tests OK(3 skipped), release gate PASS. 검사 시작/종료의 Python 소스 지문 동일. 상세 기록 [docs/CODING_CONTINUATION_20261003.md](docs/CODING_CONTINUATION_20261003.md). Claude 이미지 생성 작업은 별도로 진행 중이므로 후속 소스 변경의 검사·서버 지문은 해당 작업 종료 때 재확인.


## 2026-09 이전

2026-09-30 이전(v0.1 ~ 2026-09-30) 기록은 [docs/archive/CHANGELOG-2026-09.md](docs/archive/CHANGELOG-2026-09.md) 로 옮겼다.
