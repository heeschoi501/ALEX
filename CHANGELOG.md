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



## 2026-09-30 14:50 — Codex 리뷰 1~3차 반영분 적용, API 요약(ADR-072), 실험 결과 (claude)



- 별도 복사본(`C:\Users\<user>\AppData\Local\Temp\claude-fixes-20260930`)에서 만든 14개 파일을 실험이 끝난 뒤 옮겼다(복사 시점과 같은지 해시 확인, 원본

  `logs/claude-backup-20260930-applyfixes/`, diff `logs/claude-fixes-20260930-v3.diff` + apimap). 결과 기록의 버전 검증·교훈 사후 확인 연결·교훈 파일 보존·

  `/unload` 사후 확인·`/dev-check` 프로젝트 확인·평가 고정 스냅샷과 manifest·변이 생성기 UTF-8/시간 제한/문법 거르기(ADR-069 보충 2, ADR-070 보충).

- 새 `jarvis/apimap.py`(ADR-072): 수정안 요청에 쓰는 객체의 API 시그니처(기본 꺼짐, 평가 `--api-map`).

- 전체 830 PASS(`logs/claude-final-verify-20260930.txt`). 앱 재시작 `998d6577aed58a90`. `/unload` 실제 확인: qwen3.8:27b 가 올라간 상태에서 VRAM 15,823 → 1,082 MiB.

- 실험(PERFORMANCE 31): 교훈 켬/끔 쉬운 15/16·15/16, 어려운 9/11·9/11 → 효과 없음(기본 꺼짐). 사실 재확인 14b·120b 0/3 그대로 → 꺼 둠.

  API 요약 진단(이미 본 실패 5개) 2/5 — `ask_file_local` 통과, remote 통과는 우연(기본 꺼짐 유지). 전력 08:34~15:51 약 1.09 kWh(GPU+CPU 패키지).

- 새 문서 `docs/MODEL_CHOICE_20260930.md`: 맥 스튜디오 판단과 같은 크기 최상급 공개 모델 비교(출처 포함) → 먼저 이 PC 로 Flash Next 125b 코딩 평가.



## 2026-09-30 11:17 — Codex 코드 리뷰(GPT_NEXT 11:13) 중 실험에 걸리는 3건 반영 (claude)



- **사실 재확인 위치(P2 #3, 확인됨)**: `_fit_context` 가 예산을 넘는 긴 대화에서 system 을 모두 기록 앞으로 모아 재확인이 제자리를 잃었다 — 마지막 질문

  바로 앞의 재확인은 따로 빼 두었다가 제자리에 되돌린다. 13시 replay 측정 전에 고쳤다. 테스트 +1.

- **교훈 추출 글(P2 #6)**: `trajectory_text` 가 앞 회차부터 자르면 통과한 마지막 수정이 빠졌다 — 뒤 회차를 먼저 자세히, 앞 회차는 한 줄로. 테스트 +1.

  12시쯤 교훈 추출 단계부터 적용.

- **평가 성공 집계(P2 #9)**: `coding_agent_eval.py` 는 이제 bug_detected·acceptance_passed·suite_exit==0·new_failures==[] 가 모두 맞을 때만 센다.

  Codex 가 기준선 16개를 이 조건으로 다시 세어도 15/16.

- 전체 811 PASS(`logs/claude-codexfix-verify-20260930.txt`). 나머지(P1 결과 기록 검증 버전 무효화·검사 중 변경, 변이 생성의 UTF-8 열 위치, hard 루프 시간 제한,

  learn 과 전체 검사 결합, insights 자료형 검증, /dev-check 작업 폴더 확인, /unload 문구, 중첩 함수 이름)는 GPU 실험이 끝난 뒤 고친다.



## 2026-09-30 09:08 — 모델 내리기: /unload·화면 버튼 (claude)



- 사용자: "로컬ai를 돌리면서 게임하거나 그런건 안된다는거네" — 앱은 마지막 질문 10분 뒤에야 모델을 내린다. `/unload`·화면 '모델 내리기(그래픽카드

  비우기)' 버튼: 앱의 Ollama 에 올라간 모델을 모두 `keep_alive: 0` 으로 바로 내린다(`providers.unload_ollama_models`, `ollama stop` 과 같은 동작,

  https://docs.ollama.com/faq). 다음 질문 때 다시 올라온다(수십 초).

- 테스트 +4(`tests/test_unload.py`, 가짜 Ollama 서버), 전체 809 PASS(`logs/claude-unload-verify-20260930.txt`), 크롬 7/7·5/5·7/7.

  실제 Ollama 로 누르기는 평가(27b 사용 중)가 끝난 뒤. 원본 `logs/claude-backup-20260930-unload/`.



## 2026-09-30 08:55 — 사실 재확인: 앞선 답이 있는 대화에서 마지막 질문 바로 앞에 핵심 사실 한 번 더 (claude, ADR-071, 기본 꺼짐)



- `fact_reminder_enabled`(설정·`JARVIS_FACT_REMINDER_ENABLED`): 켜면 대화에 앞선 답이 있을 때만 마지막 사용자 질문 바로 앞에 system 한 줄 —

  '앞선 답과 다르면 이것이 맞다, 틀렸으면 먼저 인정' + 모델 이름·검색은 앱이 대신·클라우드 연결 꺼짐·지어내지 않기. 맨 앞 환경 안내는 그대로.

- 계기: 09-29 14b·gpt-oss:120b 가 자기 오답이 쌓인 대화에서 맨 앞 안내 대신 기록을 따랐다(바로잡기 0/3, 새 질문은 7/8 정답). 참고:

  Lost in the Middle(https://arxiv.org/abs/2307.03172) — 긴 문맥의 가운데보다 처음·끝을 잘 쓴다.

- 테스트 +3(`tests/test_fact_reminder.py`), 전체 805 PASS(`logs/claude-reminder-verify-20260930.txt`). 효과 측정은 교훈 실험 뒤 자동

  (`logs/probe/claude_reminder_replay_20260930.sh` → `logs/replay-reminder-*.json`, 14b·27b·120b 각 3회). 원본 `logs/claude-backup-20260930-reminder/`.



## 2026-09-30 08:49 — 코딩 절차 교훈: /dev-learn·/dev-insights, 수정안에 붙이기(기본 꺼짐) (claude, ADR-070, Codex 조사 2순위)



- `jarvis/insights.py`: ExpeL(https://arxiv.org/abs/2308.10144, 구현 https://github.com/LeapLabTHU/ExpeL) 방식 — 결과 기록의 실패 회차와 통과한 수정을 비교해 **절차 교훈**을 뽑고, 목록은 ADD(점수 2)·EDIT(+1)·

  UPVOTE(+1)·DOWNVOTE(-1) 연산으로만 바꾼다(0 이면 삭제, 최대 12개, 200자). DB 옆 `coding-insights.json`(쓴 기록 키 포함).

- `/dev-learn`(아직 안 쓴 자동 수정 기록 4건씩, 로컬 모델만) · `/dev-insights`(목록) · `/dev-insights remove N`.

- `coding_insights_enabled`(설정·`JARVIS_CODING_INSIGHTS_ENABLED`, **기본 꺼짐**): 켜면 묶음 수정안 요청에 점수 높은 교훈부터 1,600자까지 붙인다.

- 실험 도구: `coding_agent_eval.py --outcomes-out F`(과제별 결과 기록 모으기)·`--insights 교훈파일`(넣고 켜기), `logs/probe/learn_insights.py`.

  변이 과제 ID 에 변경 해시를 붙였다(같은 줄의 두 상수 변이가 ID 를 나눠 가짐 — `baseline_id` 에 옛 ID).

- 테스트 +6(`tests/test_insights.py`), 전체 802 PASS(`logs/claude-insights-verify-20260930.txt`). 원본 `logs/claude-backup-20260930-insights/`.



## 2026-09-30 08:34 — 변이 과제 생성기: 평가 과제 7개 → 51개(학습 35·평가 16) (claude, ADR-069 보충)



- `logs/probe/mutation_tasks.py`: ALEX 모듈(대응 `tests/test_모듈.py` 가 있는 18개)의 함수 안 한 곳을 AST 로 골라 바꾼다 — 연산자 바꾸기(비교·any/all),

  피연산자 교환, and/or, 상수 ±1, 조건문(return/raise 한 줄짜리) 지우기, 대입 지우기. 격리 복사본에서 그 모듈의 테스트를 돌려 **실패(종료 코드 1)한 것만**

  과제로 남긴다(시간 초과·실행 문제는 버림). 찾을 줄은 파일에서 한 곳만 맞게 앞줄을 붙인다. 학습/평가는 (파일, 함수) 해시로 나눠 같은 함수가 양쪽에 없다.

- 결과 `logs/probe/mutation_tasks.json`: 51개(연산자 17·대입 지우기 14·피연산자 9·상수 4·and/or 4·조건문 3), 9분. 참고: SWE-smith(https://arxiv.org/abs/2504.21798)의 절차적 변형·'기존 통과 테스트를 하나 이상 깨는 것만'·2분 제한, mutmut 류 변이 테스트의 killed mutant(https://opensource.com/article/20/7/mutmut-python).

- `coding_agent_eval.py --tasks-file logs/probe/mutation_tasks.json --split test [--limit N]` 로 푼다. 평가 행에 앱의 결과 기록 상태(`outcome_status`)도 싣는다.



## 2026-09-30 08:17 — 개발 작업 결과 기록 `dev-outcomes.jsonl` 과 `/dev-outcomes` (claude, ADR-069, Codex 조사 1순위)



- DB 옆 `dev-outcomes.jsonl` 에 한 줄씩: `/dev-auto` 회차별(종료 코드·실패 요약·적용한 파일·diff 앞부분·거부 사유)·덜어내기·결과(copy_passed/not_passed/

  already_passing/stopped)·걸린 시간·모델, `/dev-next` 시험 결과, 수정안, 사용자 적용·폐기, 전체 검사. 20MB 넘으면 `.1` 로 돌린다. 기록 실패는 작업을 막지 않는다.

- 상태는 종료 코드와 사용자 판단으로만: 미검증 / 실패 / 선택 검사 통과 / 전체 검사 통과 / 사용자 채택(적용 뒤 전체 검사 통과). `/dev-outcomes`·화면 '결과 기록' 버튼.

- 테스트 +2, 전체 796 PASS(`logs/claude-outcomes-verify-20260930.txt`), 크롬 7/7·5/5·코딩 모드 전부. 앱 재시작 `98966314c49410ff`. 원본 `logs/claude-backup-20260930-outcomes/`.

- 같은 날 developer_any_test_fails 재평가 4회째(`logs/coding-agent-eval-rerun4-developer.json`): 시간 초과·생각 한도 문제는 사라졌지만 실패 —

  형식 없음(재요청 뒤에도), 통과 메시지를 꾸미는 엉뚱한 수정, 없는 코드를 찾는 수정. 이 과제는 27b 에게 어렵다 — 한 과제에 맞춰 더 고치지 않는다.



## 2026-09-30 07:40 — 생각이 출력 한도를 다 쓰면 생각 없이 한 번 더, 같은 모델은 두 번 묻지 않는다 (claude, ADR-065 결정 10)



- 스트리밍 뒤 재평가(`logs/coding-agent-eval-rerun3-developer.json`): 시간 초과는 사라졌지만 1·2회차가 **출력 한도 6,144토큰을 생각에만 쓰고** 답을

  못 냈다(코더·일반 두 번씩, 회차당 약 17분). 3회차는 테스트가 기대하는 `=== tests/…` 머리글을 보고 테스트 코드 참고의 머리글을 바꾸는 엉뚱한 수정.

- `_generate_local_edit`: `ContextExhausted` 이고 생각이 켜져 있으면 그 자리에서 생각을 끄고 한 번 더(끝나면 되돌림). 코더와 일반이 같은 Ollama

  모델(호스트·이름)이면 한 번만 묻는다.

- 테스트 +2, 전체 794 PASS(`logs/claude-thinkfallback-verify-20260930.txt`). 앱 재시작 `a99146248b8fca89`. 원본 `logs/claude-backup-20260930-thinkfallback/`.



## 2026-09-30 07:15 — /dev-check: 사용자가 정한 검사 명령만 이름으로 실행 (claude, ADR-068, 4단계 3번)



- 프로젝트 루트의 `alex-checks.json`(`{"checks": {"이름": ["실행 파일", "인자", ...]}}`, 최대 20개)에 **사용자가 직접** 적은 명령을

  `/dev-check`(목록)·`/dev-check 이름`(실행)으로 돌린다. 셸 없이 argv, 테스트와 같은 120초·2MB·중단 한도(`developer._run_bounded` 로 공용화),

  `python` 은 앱의 파이썬으로 바꾼다. 개발 작업이 있으면 기록에 남긴다. 화면 개발 모드에 '검사 명령' 버튼.

- `alex-checks.json` 은 편집기의 보호 파일이다(`workspace.PROTECTED_FILES`) — 모델의 수정안·묶음이 만들거나 고치면 거부한다.

- 테스트 +5(`tests/test_developer.py::DevCheckTests`), 전체 792 PASS(`logs/claude-devcheck-verify-20260930.txt`). 원본 `logs/claude-backup-20260930-devcheck/`.



## 2026-09-30 07:05 — GPU 층 수를 한 모델에만 적용하는 설정 (claude)



- `num_gpu_layers_model`(jarvis.local.json·`JARVIS_NUM_GPU_LAYERS_MODEL`): 적으면 `num_gpu_layers` 를 그 모델에만 주고 다른 모델은 자동 배치.

  비우면 지금처럼 모든 모델. 계기: 27b 최적 54층(PERFORMANCE 29)을 gpt-oss:120b(37층·MoE)에도 주면 안 된다. 설정 화면 저장이 지우지 않게 LOCAL_FIELDS 에 넣었다.

- **설정은 바꾸지 않았다**(사용자 결정). 27b 로 바꿀 때 예: `desktop_general_model`·`desktop_coder_model` = `qwen3.8:27b`, `num_gpu_layers` = 54,

  `num_gpu_layers_model` = `qwen3.8:27b` — 앱을 끈 뒤 고치고 켠다(restart_alex.py 는 설정을 끈 상태에서 쓴다).

- 테스트 +3(`tests/test_ram_offload.py::NumGpuPerModelTests`), 전체 787 PASS(`logs/claude-gpumodel-verify-20260930.txt`). 원본 `logs/claude-backup-20260930-gpumodel/`.



## 2026-09-30 07:00 — 편집안은 늘 스트리밍으로, 변경 블록이 없으면 한 번 더 묻는다 (claude, ADR-065 결정 9)



- 09-29 밤 developer 과제 회차 기록: 3번 중 2번이 추론이 아니라 운영 실패 — ②27b 수정안이 두 모델 모두 **시간 초과**(한 번에 받는 경로는 생각+출력이

  360초를 넘으면 끊긴다; 화면에서는 중단 신호 때문에 이미 스트리밍이라 화면 없는 실행에서만 났다) ③**변경 블록 없이 설명만**.

- `core._generate_local_edit` 는 늘 스트리밍으로 받는다(토큰이 오는 한 기다림). `propose_bundle_local` 은 블록 해석이 실패하면 형식 안내를 붙여

  **그 자리에서 한 번만** 다시 묻는다 — `/dev-auto` 회차를 쓰지 않는다. 두 번째도 틀리면 지금처럼 거부.

- 테스트 +3(`tests/test_workspace_bundle.py::LocalEditRobustnessTests`), 전체 784 PASS(`logs/claude-stream-verify-20260930.txt`).

  앱 재시작 `721ae2aeefd30191`. 원본 `logs/claude-backup-20260930-stream/`.



## 2026-09-29 23:35 — 모델이 실패한 테스트 코드를 읽기 전용으로 본다 → lessons 과제가 풀렸다 (claude, ADR-066 보충 3)



- `/dev-next`·`/dev-auto` 의 수정 지시에 실패 출력이 가리키는 테스트 파일을 붙인다 — 작으면 통째로, 크면 실패한 테스트 함수 중심 발췌(1,500자,

  `developer._test_context`). 테스트는 여전히 고칠 수 없다(범위 검사). 계기: 트레이스백이 테스트 파일 안에서 끝나면 모델이 기대값을 몰랐다.

- 재평가(qwen3.8:27b): **lessons_stem 통과**(2회차·원본과 같음·회귀 0·6분 — 전엔 3회 실패·36분). developer_any_test_fails 는 다시 실패(3회·12분).

  실제 ALEX 코드 과제 8개 중 6개(테스트가 못 잡는 webread 과제를 빼면 7개 중 6개). 결과 `logs/coding-agent-eval-rerun-*.json`.

- 평가 스크립트: 회차별 수정안(`round_diffs`)과 회차 기록(`journal`)을 남긴다.

- 테스트: 기존 2건에 확인 추가, 전체 779 PASS(`logs/claude-testctx-verify-20260929.txt`). 앱 재시작 `bd2ef4dfba70bf13`. 원본 `logs/claude-backup-20260929-testctx/`.



## 2026-09-29 23:05 — 큰 파일 코딩 평가 3/4, 평가 스크립트의 버그 감지 수정 (claude)



- 큰 파일 실측(`coding_agent_eval.py --set large`, qwen3.8:27b, 발췌 모드·실패 부분만·덜어내기 적용, 22:01~23:01): **3/4 회귀 없이 해결** — core.py(112KB) 2회차·원본과 같음·2분 40초, workspace.py 2회차·원본과 같음·2분 30초, web.py 4회차·원본과 다른 한 줄(현재 대화를 지울 때만 `last_exchange` 를 비움 — 테스트는 통과, 원본보다 좁다)·16분, developer.py(`_run_tests` 의 '하나라도 실패하면 실패' 두 줄 삭제) 3번 고쳐도 실패·37분. 통과한 과제는 모두 전체 unittest 종료 코드 0. 작은 파일 평가(2/4)와 합쳐 실제 ALEX 코드 8과제 중 5개. 결과 `logs/coding-agent-eval-large-qwen3.8_27b.json`.

- 평가 스크립트 버그: 테스트 파일을 지정할 때 인자 순서가 틀려(`-s -p 패턴 tests`) unittest 가 사용법 오류(종료 코드 2)로 끝났고, 그래서 '버그가

  테스트에 잡힘' 확인이 늘 참이었다. 고침(`logs/probe/coding_agent_eval.py`). 통과·회귀 판정은 ALEX 의 테스트 실행기와 전체 suite 로 했으므로 결과는 유효하다.

  webread_ipv4_mapped 과제만 '잡히지 않는 버그'로 분류가 바뀐다(Python 3.11.9 표준 라이브러리가 이미 막음).



## 2026-09-29 22:00 — /dev-auto 가 통과 뒤 필요 없는 변경을 덜어낸다 (claude, ADR-066 보충 2)



- 복사본에서 통과하면 변경 덩어리를 하나씩 되돌려 보고, 되돌려도 합격 기준 테스트가 통과하는 덩어리는 최종 수정안에서 뺀다

  (`jarvis/developer.py` `_trim`, 덩어리마다 테스트 1번, 최대 8곳, 끝에 한 번 더 확인). 주석·빈 줄만 바꾼 덩어리와 새 파일은 그대로 둔다.

  계기: 실제 ALEX 코드 평가 launcher_fa 에서 실패한 회차의 군더더기 함수가 최종 수정안에 남았다(2026-09-27).

- 평가 스크립트: 처음 실패한 테스트 이름을 남기고(`detected_failures`), 큰 파일 과제 4개(`--set large`)의 core 과제 찾을 줄을 유일하게 고쳤다.

  webread_ipv4_mapped 과제는 Python 3.11.9·3.10.12 표준 라이브러리가 `::ffff:127.0.0.1` 을 이미 전역 주소가 아니라고 봐서 테스트가 잡지 못한다(과제 문제).

- 테스트 +1, 전체 779 PASS(`logs/claude-trim-verify-20260929.txt`). 원본 `logs/claude-backup-20260929-trim/`. 앱 재시작은 평가가 끝난 뒤.



## 2026-09-27 00:40 — 큰 파일 발췌 모드, 모델에게 실패 부분만 (claude, ADR-067)



- 문맥 예산보다 큰 파일도 `/edit-files`·`/dev-*`·`/dev-auto` 대상이 된다: 트레이스백 줄의 함수 → 이름이 나온 함수·상수 → import → 단서 낱말을 많이

  공유하는 함수 본문 → 나머지 정의 첫 줄 순으로 예산까지 보인다(`jarvis/excerpt.py`). 찾을 부분은 파일 전체에서 한 곳과 맞아야 적용되므로 보이지 않은 곳을

  지어낸 수정안은 거부된다. 단일 파일 작업도 전체 다시 쓰기에 크면 묶음(발췌) 수정안. 묶음 지시 한도 1,600 → 3,000자(데스크톱).

- 모델에게 주는 테스트 출력에서 `unittest -v` 통과 줄을 뺀다 — 지시 예산을 먹어 트레이스백이 잘렸다.

- 테스트 +11(`tests/test_excerpt.py`), 전체 778 PASS(`logs/claude-excerpt-verify-20260927.txt`). 앱 재시작 `71088ffb999ce9d1`.

  원본 `logs/claude-backup-20260927-excerpt/`. 큰 파일 과제 4개 모의(`logs/claude-draft-excerpt-20260926/sim_large.py`): 버그 자리가 모두 발췌에 들어감.

- 실제 ALEX 코드 평가(`logs/probe/coding_agent_eval.py`, qwen3.8:27b, 발췌 모드 전): 오늘 고친 버그 4개를 복사본에 되살려 `/dev-auto` 로 고치게 함 — **2/4 회귀 없이 해결**. router_shortcut 2회차 통과(원래 줄 복원, 4분), launcher_fa 3회차 통과(`all→any` 정답이지만 앞 회차의 불필요한 `report` 감싸기도 최종 수정안에 남음, 13분), lessons_stem 3회 수정 후에도 실패(36분), webread_ipv4_mapped 는 평가 스크립트의 테스트는 실패했는데 `/dev-auto` 복사본에서는 1회차에 통과 → 수정안 없음(원인 미조사: 복사본 차이 또는 평가 스크립트 문제).



## 2026-09-26 23:40 — /dev-auto 복사가 `NUL` 같은 Windows 장치 이름 파일에서 멈추던 문제 (claude, ADR-066 보충)



- ALEX 폴더에 셸 리다이렉트 흔적 `NUL`(54바이트, 09-25 23:07, "INFO: Could not find files…")이 있어 `_isolated_copy` 가 `SameFileError` 로 멈췄다.

  장치 이름(`CON`·`PRN`·`AUX`·`NUL`·`COM1-9`·`LPT1-9`, 확장자 무관)과 복사할 수 없는 파일은 건너뛰고, `/dev-auto` 기록에 건너뛴 개수·이름을 남긴다.

  `NUL` 파일 자체는 지우지 않았다(사용자 폴더 — 지워도 되는 찌꺼기라고 안내함).

- 테스트 +1, 전체 767 PASS(`logs/claude-nulfix-verify-20260926.txt`), 크롬 7/7·5/5. 앱 재시작 `853098208b75e83a`.

- 실제 ALEX 코드 평가(`logs/probe/coding_agent_eval.py`)는 과제마다 결과를 저장하도록 바꿨다 — 첫 실행이 40분 제한에 끊겨 결과를 잃었다.



## 2026-09-26 22:55 — 묶음에서 새 폴더·빈 새 파일, 개발 작업에 테스트 여러 개 (claude, ADR-065 결정 7·8)



- 새 파일을 위한 새 폴더(3단계까지)를 만들고, 실패·되돌리기 때 함께 지운다(사용자 파일이 있으면 남김). 새 파일은 비어 있어도 된다(`__init__.py`).

  실측: 두 모델이 새 패키지를 만들다 빈 `__init__.py` 에서 막혀 0/4 → 허용 뒤 4/4.

- `/dev-start 파일들 :: tests/test_a.py, tests/test_b.py :: 목표` — 테스트는 모두 통과해야 통과다. 화면 입력칸 안내도 바꿨다.

- 테스트 +6, 전체 766 PASS, 크롬 7/7·5/5. 앱 재시작 `92c9ec5bcb1f591c`. 원본 `logs/claude-backup-20260926-newdirs/`.



## 2026-09-26 22:38 — 자동 재수정 루프 /dev-auto: 격리 복사본에서 최대 3회, 실제 파일은 승인 때만 (claude, ADR-066)



- `/dev-auto`(개발 모드 "자동 수정(최대 3회)" 버튼): 무거운 폴더를 뺀 임시 복사본에서 테스트 → 묶음 수정안 → 적용 → 재시험을 최대 3번.

  통과하면 실제 프로젝트에 최종 묶음 수정안 하나만 내놓고, 적용은 사용자가 한다(`jarvis/developer.py` `auto`·`_isolated_copy`).

  `propose_bundle_local` 은 다른 편집기(복사본)에 수정안을 만들 수 있고(`editor=`·`record=`), 묶음은 파일 전체 교체(`content`)도 받는다.

- 실제 모델: 두 파일 버그·7가지 예외 slugify 모두 gpt-oss:120b·qwen3.8:27b 각 2/2. 테스트 +5, 전체 761 PASS, 크롬 5/5·7/7.

  앱 재시작 `9d5e392a5d2624c1`. 원본 `logs/claude-backup-20260926-devauto/`.



## 2026-09-26 22:25 — 개발 작업(/dev-*)도 여러 파일: 묶음 수정안, 테스트는 손대지 못함 (claude, ADR-065 결정 6)



- `/dev-start a.py, b.py :: tests/test_x.py :: 목표` — 파일이 둘 이상이면 `/dev-next`·`/dev-propose` 가 묶음 수정안을 만든다. 작업에 지정한

  파일만 고칠 수 있고 `tests/` 를 건드린 묶음은 적용 전에 폐기한다. 파일 하나짜리 작업과 저장된 옛 작업은 그대로 동작한다(`jarvis/developer.py`).

- 실제 모델로 끝까지: 두 파일에 걸친 버그를 gpt-oss:120b 2/2·qwen3.8:27b 2/2 가 수정안 한 번에 고쳐 테스트 통과(`logs/probe/dev_bundle_live.py`).

- 테스트 +4, 전체 756 PASS, 크롬 7/7·4/4. 앱 재시작 `22d931a6663a3156`. 원본 `logs/claude-backup-20260926-devbundle/`.



## 2026-09-26 22:10 — 여러 파일 변경 묶음: 부분 수정·새 파일·전부 또는 전무·통째 되돌리기 (claude, ADR-065)



- `/edit-files 경로1, 경로2 :: 지시`: 로컬 모델이 '찾을 부분 → 바꿀 내용' 블록과 새 파일 블록으로 묶음 수정안을 만든다(`jarvis/workspace.py`

  `parse_change_blocks`·`create_bundle`·`apply_bundle`·`undo_bundle`, `jarvis/core.py` `propose_bundle_local`). `/apply-edit` 은 묶음을 전부

  적용하거나(중간 실패 시 모두 되돌림) 하나도 적용하지 않는다. `/undo-bundle ID --yes` 로 통째로 되돌린다. 화면 버튼은 그대로 쓴다.

- 실제 모델 실측으로 형식 해석을 넓혔다(꺾쇠 개수, `---`, 앞의 `/`, 빈 찾을 부분 = 끝에 덧붙이기): gpt-oss:120b 2/3 → 4/4, qwen3.8:27b 3/3.

- 테스트 +14(`tests/test_workspace_bundle.py`), 전체 752 PASS, 크롬 4/4(`logs/probe/bundle_ui_smoke.py`). 코덱스 21:06 작업(738 PASS,

  DB 스키마 6)도 확인: 검증 뒤 코드 변경 없음, DB 무결성 ok. 앱 재시작 `a5cc60fc22bf291f`. 원본 `logs/claude-backup-20260926-bundle/`.

- 사용자가 20:34 에 데스크톱 모델을 gpt-oss:120b 로 바꿔 두었다 — 그대로 두었다.



## 2026-09-26 21:06 — Coding Mode 작업 이력·수정안 영속 저장 (Codex)



- 작업 ID별 이력, `/dev-list`, `/dev-resume ID`, 화면 목록 버튼을 추가했다. 중단된 실행은 자동 재실행 없이 재검증 대기로 복구한다.

- 프로젝트별 수정안을 SQLite에 저장하여 재시작 후 승인·폐기가 가능하다. schema 6 전환 전 DB 백업, 기존 기록 이전, 원본·경로 재검사를 적용했다.

- Windows release gate 738 tests(3 skipped) PASS, Chrome 7/7 PASS, 새 symlink 경계 테스트 WSL PASS. 안전 재시작 후 서버/디스크 `a5716ef187695240` 일치.

- 상세: `CODING_AGENT_STATE_20260926.md`. 사용자 요청으로 여기서 마무리했다.



## 2026-09-26 20:45 — Coding Mode 4단계 기반: 전체 테스트·Git 변경 조회 (Codex)



- `/dev-verify`: 선택한 테스트가 통과한 뒤 승인된 프로젝트의 `tests/` 전체 unittest를 실행한다. 결과와 시각을 작업 상태에 기록한다. 테스트 실패는 파일을 자동 변경하지 않는다.

- `/dev-changes`: Git 프로젝트의 변경 파일과 스테이징 전·후 diff 요약을 읽기 전용으로 표시한다. 이 ALEX 프로젝트는 Git 저장소가 아니므로 안내를 표시한다. 두 기능의 버튼을 개발 모드 화면에 추가했다.

- 테스트 실행 중단·시간 초과·출력 초과 시 Windows `taskkill /T`, POSIX 프로세스 그룹 종료로 자식 프로세스도 정리한다. 테스트가 대상 소스를 수정·삭제하면 통과 판정을 무효화한다. Windows 자식 프로세스 실측 테스트 포함.

- Windows 릴리스 검증 **730 tests (2 skip) PASS**, 임시 Chrome 화면 검사 **6/6 PASS**. 앱 안전 재시작 후 서버/디스크 지문 `1e1f4fe25f526cd0` 일치. 설계·남은 범위는 `CODING_AGENT_STAGE4_20260926.md`, 로그 `logs/codex-stage4-verify-final-20260926.txt`, 원본 `logs/codex-backup-20260926-stage4-verify/`.



## 2026-09-26 18:45 — 125B 측정: 빠르지만 바로잡기 1/3, CUDA 오류는 안전장치가 막음 (claude)



- 새 `scripts/desktop_screen.sh MODEL screen|finals`: 한 모델을 26 의 자로 잰다(쉼 대기·대화 양보·완료 표시). **안전장치:** GPU 83°C 이상

  또는 `ollama-server.log` 에 새 CUDA 오류가 찍히면 멈추고 Ollama 를 끈다 — 26-8 의 "GPU 를 잃은 Ollama 가 큰 모델을 RAM 에만 다시 올려

  PC 가 멈춤" 을 막는다.

- `qwen3.8-flash-next:125b`(PERFORMANCE 26-10): 22.6 tok/s(가장 빠름), replay 1/3. fresh 첫 질문에서 `CUDA error: an illegal memory

  access`(18:38) — 안전장치가 5초 안에 멈추고 Ollama 를 껐고 PC 는 멈추지 않았다. 18:40 ALEX 재시작, 기본 qwen3.8:27b 정상.

  데스크톱 기본은 qwen3.8:27b 그대로.



## 2026-09-26 18:18 — Coding Mode 수정안 검토 버튼 복구 (Codex)



- `/dev-next` 실패 수정안과 `/dev-propose` 기능 수정안의 ID를 `편집 제안 ID:` 독립 줄로 통일하고, 화면의 적용·폐기 버튼을 연결했다. 사용자 승인 전 원본은 바뀌지 않는다.

- 같은 웹 서버에서 새로고침한 뒤 `/dev-next`로 대기 중인 diff를 다시 볼 수 있다. 재시작하면 미적용 수정안이 사라지는 기존 제한은 유지된다.

- Windows release gate **724 tests (2 skip) PASS**, Chrome 임시 앱 실제 버튼 흐름 **4/4 PASS**, 기존 UI smoke **7/7 PASS**. `logs/codex-coding-ui-verify-final-20260926.txt`, `logs/probe/coding_mode_ui_smoke.py`. 안전 재시작 후 코드 지문 `89e9ee2b10144ff9` 일치. 백업 `logs/codex-backup-20260926-coding-ui/`.



## 2026-09-26 17:05 — 데스크톱 기본 qwen3.8:27b, FA 되돌림, 알아서 검색 길이 제한 (claude, ADR-064·ADR-062 결정 2)



- 사용자가 남은 결정을 맡겼다("그냥 너가 진행해. 그리고 fan이 너무 돌더라고"). 결선(PERFORMANCE 26-9) 대로

  **데스크톱 기본 모델 `qwen3.8:27b`**(`jarvis.local.json` 두 줄, 앱이 꺼진 동안). 125B 는 지우지 않았다.

- **FA:** 데스크톱에서도 Ollama 기본값(→ `--flash-attn auto`, 이 GPU 에서 켜짐). FA 에서 죽은 qwen3.5 계열이 기본일 때만 끈다

  (`jarvis/launcher.py` `FA_CRASH_FAMILIES`, 런처가 Ollama 를 띄우기 전에 모델을 정한다). qwen3.8 27%·20b 22% 빨라진다(26-2).

- **알아서 검색도 120자 이하 질문만**(`jarvis/router.py`): 긴 글이 통째로 검색어가 되어 밖으로 나가지 않는다. 부탁 말·주소는 그대로,

  긴 글의 최신 정보는 모델의 검색 도구가 맡는다.

- 테스트 +2(FA 연결·긴 글 검색) — 고치기 전 코드에서 실패 확인. 전체 **724 tests (2 skip) PASS**. 백그라운드로 켜서

  `/status` = qwen3.8:27b, 지문 `5e28c51ee93abf6d`, qwen3.8 적재 `flash attention is enabled`. 원본 `logs/claude-backup-20260926-decisions/`.



## 2026-09-26 10:15 — 결선 판정, 코덱스 작업 이어받기 확인 (claude)



- PERFORMANCE 26-9: 결선에서 gpt-oss:20b 와 qwen3.8:27b 는 어려운 파이썬 6/6·RTL 5/6(각자 다른 과제를 재측정에서도 틀림)으로

  같고, 웹은 13/14 대 14/14, qwen3.8 대화형 중앙값 35.8초 — 26 규칙상 데스크톱 기본을 qwen3.8:27b 로 권했다(사용자 결정 대기).

  정체성 채점기는 숫자를 거절하는 답을 기대 표현으로 못 잡는다(두 모델 모두 5.x/7, 읽으면 핵심 충족).

- 10:13 코덱스가 20분 넘게 쉬어 이어받았다: 09:45 검증(722 PASS) 뒤 코드 변경 없음, 앱 = 디스크 `54b31a180f9c35be`.

  코덱스의 Coding Mode 패널이 들어간 화면으로 `logs/probe/session_ui_smoke.py` 7/7(대화 삭제·맨 아래·CSP 오류 없음).



## 2026-09-26 09:47 — 시스템 감사와 감독형 Coding Mode (Codex)



- `SYSTEM_AUDIT_20260926.md`: 실행·종료, provider/설정, Model Catalog의 설치/선택/로드 상태, 파일/보안, 학습과 Coding Agent의 현재 경계를 점검하고 우선순위를 정리했다.

- `jarvis/developer.py`, `jarvis/app.py`, 화면 패널: 한 파일/한 테스트의 기준 시험 → 실패 시 로컬 수정안·diff → 명시적 적용/폐기 → 재시험. 통과한 기준 시험 뒤에는 `/dev-propose`로 기능 변경안을 요청한다. 작업 단계/시험 이력을 SQLite에 저장하고, 재시작 뒤 유실된 미적용 제안과 통과 뒤 바뀐 소스를 감지한다. 테스트 프로세스는 120초·출력 2MB·사용자 중단·최대 3회로 제한한다.

- `jarvis/web.py`: 채팅 스트림 초기화 실패에도 busy 상태를 정리하고, 독립 요약 provider 및 Coding Mode 테스트 프로세스에 중단 신호를 전한다. `jarvis/lifecycle.py`: 같은 인스턴스의 웹 서버에 대해 `status/start/stop/restart` 명령을 추가했다(Ollama는 계속 실행).

- Windows 릴리스 검증 **722 tests (2 skip) PASS**, Python 3.10 문법·라우터·보안/alpha/beta smoke 포함 (`logs/codex-system-audit-verify-20260926.txt`). Stage 2 측정 종료 뒤 앱을 안전 재시작해 서버/디스크 지문 `54b31a180f9c35be` 일치, Coding Mode 화면·상태 명령을 HTTP로 확인했다. 실제 대형 모델 생성은 시험하지 않았다. `GPT_NEXT.md`와 `CLAUDE_NEXT.md`에 인수인계. 백업 `logs/codex-backup-20260926-developer-mode/`.



## 2026-09-26 09:30 — 대화 목록에서 바로 삭제, 대화를 열면 맨 아래에서 시작 (claude)



- **대화 삭제 버튼**(사용자: "대화목록 제거 기능도 필요할거 같은데"): 목록의 각 대화에 `×`(마우스를 올리거나 보고 있는 대화면 보임,

  터치 화면은 늘 보임). 확인 창("메시지 N개가 지워지고 되돌릴 수 없습니다") 뒤 새 `POST /api/session/delete`

  (`confirmed: true` 필수, 토큰·다른 탭·답변 중 잠금은 다른 변경 요청과 같다)가 기존 `storage.delete_session` 으로 지운다 —

  메시지·라우팅 기록까지, 배운 교훈은 남는다. 보던 대화를 지우면 새 대화가 열리고, 지운 대화의 마지막 질문·답은 교정 학습용

  메모리(`last_exchange`)에서도 비운다. 채팅 명령(`/delete-session ID --yes`)을 쓰지 않으므로 대화창에 명령문이 찍히지 않는다.

- **대화를 열면 맨 아래**(사용자: "새로고침을 하거나 다른 대화내용을 클릭하면 … 스크롤을 해서 내려오는데"): 대화창 CSS 의

  `scroll-behavior:smooth` 때문에 긴 대화를 맨 위에서부터 애니메이션으로 내려왔다. 지웠다 — 고치기 전 크롬에서 그린 직후

  맨 아래까지 8611px(새로고침)·5603px(대화 전환)이 남았고, 고친 뒤 0px.

- **카탈로그 창 CSP 위반**(함께 발견): `index.html` 의 `style="…"` 두 곳이 `style-src 'self'` 에 막혀 저장 공간 줄·목록 배치가

  빠져 있었다 → 같은 값을 `style.css` 로 옮겼다.

- 테스트: `tests/test_local_beta.py` +3(다른 대화 삭제·보던 대화 삭제·확인/없는 대화/답변 중 거절) 과 토큰 검사·정적 자원 검사

  보강 — 고치기 전 코드에서 5건 실패 확인. 실제 크롬(창 없음) 검사 `logs/probe/session_ui_smoke.py` 7/7(고치기 전 코드는 스크롤

  두 건과 버튼에서 실패). 측정과 겹친 첫 실행에서 한 번 오류 2건이 났고(내용은 남지 않음, 요청 제한 3초 초과로 추정) 뒤 9번 연속 통과.

  원본 `logs/claude-backup-20260926-session-delete/`.



## 2026-09-26 08:55 — 결선이 RTL 차단에 멈추지 않게 (claude)



- 결선 첫 시작(08:46)에서 gpt-oss:20b 어려운 파이썬은 6/6(42초)이었지만 RTL 에서 Windows **Smart App Control** 이 서명 없는

  `C:\oss-cad-suite\bin\vvp.exe` 를 막아(WinError 4551, CodeIntegrity 3077/3118) `set -e` 로 결선 전체가 멈췄다.

  `iverilog.exe` 는 통과한다.

- `scripts/desktop_stage2.sh`: 시작할 때 `vvp.exe -V` 를 한 번 띄워 보고 막혀 있으면 RTL 단계만 건너뛴다(완료 표시를 남기지

  않으므로 풀린 뒤 다시 돌리면 RTL 만 잰다). 08:53 재시작.

- 08:58 사용자가 Smart App Control 을 껐다(레지스트리 `VerifiedAndReputablePolicyState` = 0). 사전 확인이 `C:\oss-cad-suite\lib`

  없이 `vvp.exe` 를 띄워 풀린 뒤에도 DLL 을 못 찾아(0xC0000135) 막힌 것처럼 보였다 → RTL 단계와 같은 PATH 로 띄우게 고쳤다.

  지금 결선이 끝나면 연결 작업이 결선을 한 번 더 돌려 RTL(20b·qwen3.8)만 잰다.

- 09:12 모델 없이 RTL 시뮬레이션 경로만 다시 돌려 봤다(`grade_rtl.simulate`, 같은 PATH): 맞는 8비트 가산기 PASS,

  cin 을 뺀 가산기 FAIL(`a=24 b=81 cin=1 -> 0a5 exp=0a6`) — iverilog 컴파일과 vvp 실행이 모두 된다.



## 2026-09-26 09:05 — Stage 1 끝, 결선 시작, 08:13 비정상 재시작의 경위 (claude)



- Stage 1 끝(08:45). qwen3.5:122b: replay 0/3·fresh 7/8·지식 2024-10 → 결선은 qwen3.8:27b 하나(PERFORMANCE 26-8).

  결선(Stage 2)이 08:46 에 자동으로 시작했다.

- 08:15 의 비정상 재시작은 측정 중 GPU 오류(`CUDA error: unknown error`) 뒤 Ollama 가 81GB 모델을 RAM 에만 다시 올리다

  멈춘 것으로 보인다(Kernel-Power 41). FA 커널 충돌은 아니다 — ADR-054 에 덧붙였다.



## 2026-09-26 08:15 — 붙여 넣은 긴 글이 시스템 정보 표로 새지 않게 (ADR-062, claude)



- 사용자가 새 모델을 시험하려고 실습 노트북 문제를 붙여 넣었는데, 코드 속 예시 문자열 `'현재 GPU 알려줘'` 때문에

  시스템 정보 표만 두 번 나왔다. 시스템 정보·파일 검색·세션 이어가기 지름길은 이제 120자 이하의 짧은 요청일 때만

  탄다(`jarvis/router.py` `SHORTCUT_MAX_CHARS`). 긴 글은 모델이 읽는다.

- `tests/test_router.py::PastedTextTests` 2건(먼저 실패 확인). 골든 코퍼스 65/65, 전체 705 tests(2 skip) PASS.

  앱 재시작, 지문 `693040a4437b9897`. 원본 `logs/claude-backup-20260926-router/`.



## 2026-09-26 07:45 — 결선 스크립트가 데스크톱에서 바로 멈추지 않게, 전체 검증 (claude)



- `scripts/desktop_stage2.sh`: FA 확인이 `rg` 를 썼는데 데스크톱 WSL 에는 `rg` 가 없다. 빈 값이 되어 곧바로 ABORT 했을

  것이다 — stage1 과 같은 `grep` 으로 바꿨다. 결선은 아직 한 번도 돌지 않았다(완료 표시·결과 파일 없음).

- 전체 검증(측정을 멈춘 상태): **703 tests (2 skip) PASS**, 라우터 65/65, 보안 13/13.

- PERFORMANCE 26-8: qwen3.5:122b replay 0/3 — 오답을 되풀이하지도 바로잡지도 않고 `/status` 로 떠넘겼다.



## 2026-09-26 00:35 — 종료 중 요청 거절 테스트, qwen3.8:27b 품질 결과 (claude)



- `tests/test_local_beta.py` 에 1건: 답변 중에는 `/api/shutdown` 도 409(`restart_alex.py` 가 이 거절을 믿는다),

  종료를 받은 뒤 서버가 멈추기 전의 틈에 온 대화 요청은 409 이고 저장되지 않으며 `/api/state` 가 `shutting_down` 을

  알린다. `shutting_down` 이 켜지지 않게 바꾼 변이에서 실패하는 것을 확인했다. 다운로드 중단은 이미

  `tests/test_model_catalog.py` 에 있었다. 이 파일 22건 PASS(Windows). 전체 검증은 측정이 끝난 뒤(측정 중 CPU 를

  쓰지 않으려고) — 예상 703 (2 skip).

- PERFORMANCE 26-5: qwen3.8:27b 가 교훈 없이 오답이 쌓인 대화를 3/3 바로잡았다(gpt-oss 둘은 0/3). 결선 조건 1 을

  넘은 첫 모델.

- 26-6 gemma4:31b: replay 3/3·fresh 8/8 이지만 중앙값 129.7초 — 결선에 넣지 않기를 권함. 26-7 muse-glimmer:30b:

  replay 1/3(없는 설정 파일을 지어냄) — 탈락. qwen3.5:122b 는 01:45 예약 종료로 끊겨 다음에 잰다.



## 2026-09-25 23:50 — 중단 버튼이 멈춘 응답을 실제로 끊게 (코덱스 system-audit 마무리, claude)



- 코덱스가 23:39 에 멈춘 작업을 이어받았다: 응답 본문이 오지 않는 동안에도 [중단] 이 먹게 하는

  `jarvis/http_control.interrupt_on_cancel`, 끊긴 연결을 오류가 아니라 '중단됨' 으로 바꾸는 provider 처리,

  웹의 종료 중 요청 거절(`shutting_down`)·다운로드 중단 문구. 기록·테스트 없이 멈춰 있었다.

- **Windows 에서는 효과가 없었다.** 실측: 다른 스레드의 `shutdown(SHUT_RDWR)` 은 막힌 recv 를 풀지 못해 5초

  그대로 기다렸다. urllib 은 소켓을 makefile 로 감싸 `close()` 도 미뤄진다. 실제로 닫으면(`_real_close`) 0.3초 만에

  풀린다. Windows 에서만 강제로 닫게 했다(이 응답 전용 소켓).

- `tests/test_http_cancel.py`(5): makefile 로 감싼 소켓의 멈춘 읽기가 중단 0.2초 뒤 풀리는지(고치기 전 5초 실패

  확인), 끊긴 연결·조용히 닫힌 스트림이 중단 뒤에는 GenerationCancelled, 중단이 없으면 여전히 오류.

  Windows 릴리스 검증 **702 tests (2 skip) PASS** (`logs/claude-cancel-fix-verify-20260925.txt`).

- 웹 쪽 변경(종료 중 거절·다운로드 중단 표시)은 전용 테스트가 아직 없다 — 다음 작업자 몫.

  백업: `logs/codex-backup-20260925-system-audit/`(코덱스 작업 전), `logs/claude-backup-20260925-after-codex/`(내 수정 전).



## 2026-09-25 — Ollama 비추론 모델 호환성



- Mistral Large 설치 후 think=True HTTP 400 재현.

- unsupported thinking 응답에만 옵션을 제거해 1회 재시도하고 모델 인스턴스별로 기억. 스트림·일반·도구 경로 보존.

- 실효 think 상태 표시 및 회귀 테스트 5개 추가.



## 2026-09-25 — 성장 기능 설명과 보고 정정



- 경험 DB 영속성, 교훈 재사용, 모델 가중치 학습의 구분과 현재 스위치를 일반 대화에 제공.

- `/growth`의 export 기록을 실제 학습 실행으로 표시하던 문구 정정.

- 재개방/새 세션/취소 및 설정 회귀 검증 추가. Windows 692 tests (2 skip) PASS.

- 상세 범위·증거·남은 과제: `LEARNING_AUDIT.md`.



## Unreleased - 2026-09-25 (desktop) - Codex, ADR-061



- Added the verified Ollama tag `qwen3.8-flash-next:125b-a6b-q4_K_M` to the static Model Catalog: 125B MoE body, 6B active, 51B additional n-gram embedding parameters, Q4_K_M, about 120GB download. The current desktop shows **estimated memory shortage**, but download remains a manual choice.

- Default Windows/NVIDIA catalog filtering excludes MLX tags. Removed unverified downloadable candidates and corrected the Grok tag/size. Installed local models remain visible regardless of catalog filtering.

- Repaired the catalog pull flow: explicit confirmation is checked server-side, the stream supplies a request ID for cancellation, and success is required before the UI says download complete. Storage free space is shown only when ALEX knows the running Ollama store path.

- Replaced the previously failing pytest-only catalog tests with seven discovered `unittest` cases using a fake local Ollama. Windows release verification: **688 tests (2 skipped), PASS** (`logs/codex-model-catalog-qwen-verify-20260925.txt`). No model was downloaded. ALEX was safely restarted; server/disk code fingerprint `68605612a133c350`.



## Unreleased - 2026-09-25 (late night, desktop) - Codex, ADR-060



- Fixed local-config precedence for `JARVIS_WEB_TOOL=0` and `JARVIS_AUTO_LESSONS=0`. The benchmark had

  silently re-enabled both from `jarvis.local.json`; a replay answer revealed the web tool was still active.

- Stage 1 and Stage 2 now abort unless Windows Python confirms web tool, auto lessons, and lesson injection

  are off. Contaminated replay results were backed up and their clean markers reset for a fair rerun.

- Windows release verification: 681 tests (2 skipped), PASS. The app restart and benchmark resume wait for

  the current user answer to finish.



## Unreleased - 2026-09-25 (late night, desktop) - Codex, ADR-059



- Periodic long-conversation summaries now use the same local Ollama model with `think` off and a shorter

  output cap. This removes unnecessary reasoning/output from a synchronous step before the user's answer.

- Summary prompts label old assistant claims as unverified, prioritize user corrections, and mark saved

  summaries as lower trust than current environment facts. Existing conversation data is not rewritten.

- Changing the model in the web UI now rebuilds the summary provider too, so a later summary does not load

  the previous model or use stale reasoning settings.

- Synthetic A/B on `qwen3.8:27b`/16K: old 49.19s including 9.85s load; new 21.36s warm. Both summaries

  correctly rejected the old “internet blocked” claim. A temporary-DB `build_core` live probe passed.

- Windows release verification: 679 tests (2 skipped), PASS. The app was restarted after its active answer

  completed; disk/server code fingerprints match (`0d090daeed69f038`). The FA-off comparison resumed

  from clean markers, with old 20b/120b replay results backed up for a fair rerun.



## Unreleased - 2026-09-25 (night, desktop) - Codex, ADR-058



- Natural corrections can now be noticed by the local model's `remember_correction` tool, with the user's words

  stored after the existing redaction and length limit, rather than the model's paraphrase. The model is asked to acknowledge its previous error. Explicit correction

  markers still work and duplicate tool calls do not create duplicate experiences.

- “방금 배운 거 취소” reverses only automatic lessons from the immediately preceding answer in the same session.

  A `배운 것 취소` button appears beside learning notices, including loaded chat history. Its guarded

  `/undo-learning ID` command preserves anything the user later approved manually.

- Temporary-DB live probe on the configured `qwen3.5:122b`: unmarked correction invoked the tool and the answer

  acknowledged the error; three ordinary followups invoked no correction tool. No web search was used.

- Windows release verification: 676 tests (2 skipped), router 65/65, security smoke 13/13, PASS.

- App restarted with disk/server fingerprint `ba101c63d59a2090`. The paused FA-off desktop comparison resumed

  in the background at 20:18; completed steps are skipped and user chat interrupts an overlapping attempt.

- FA-off clean placement results now have a preliminary table in PERFORMANCE 26-2. Stage 2's runner now checks

  the FA setting, stops an attempt when ALEX receives user chat, and resumes from clean-step markers.



## Unreleased - 2026-09-25 (evening, desktop) - claude, continuing where Codex hit its usage limit



- **ALEX now learns what it got wrong and what it did not know, automatically (ADR-057, user's decision).**

  The user asked whether auto-learning is a problem ("잘못 학습할까봐 그런거야?"), heard that the issue is who judges

  correctness, and chose "ALEX 가 찾은 것까지 자동" over the recommended "only what I correct". Now: a correction in

  chat ("틀렸어…", "아니야, …", "…잖아", "…텐데?", `/bad 이유`) is recorded with the user's words verbatim and approved

  on the spot (`user(chat)` / `user(/bad)`); an answer built from sourced web material (router search or self-search)

  is recorded with its date and sources as a **candidate** (`auto(found)`) - used as a lesson at once, never training

  data unless the user approves it. Skipped: fast-changing topics (price, weather, news, today, latest…), failed

  answers, repeats; found facts expire after 90 days. Every new lesson is announced at the end of the answer with

  the one-line undo (`/review id 거절`). `auto_lessons_enabled` (default on).

- Lessons now also use the eval split (ADR-055 had excluded it, which would silently drop one in five of the user's

  corrections). To keep measurements honest, model-evaluation steps in the stage scripts run with lessons and

  auto-learning off; only the lesson A/B turns lessons on.

- Unmarked pushback ("26년 2월 기준 모델이야…", "…설정해뒀는데?") is not caught - "…는데?" also ends ordinary

  questions ("어떻게 하는데?"). 14 new tests; 670 tests PASS; app restarted on `5fe5cc7d3b28f2fa`.

- **Lesson A/B on 120b (PERFORMANCE 27):** without lessons 120b repeated the chat's earlier wrong claims 3/3 (20b:

  2/3) - size does not cure history contamination; with lessons 0/3. Neither model said "I was wrong earlier".



- **ALEX can now decide to search by itself (ADR-056).** The user: "if you don't know, check it yourself" -

  and ALEX replied "type '... 검색해줘' and the app will search; I can't call it". Only the router could start a

  search (by the question's shape); only the model can tell that it does not know. Conversational and coding routes

  now give the model one tool, `web_search(query)`; the app runs it through the same guarded `WebReader` as

  `/search`, hands the material back as a `tool` message (data, not instructions), and appends

  `출처 — ALEX 가 스스로 검색: '...'` with the sources itself. Only the query leaves the machine; queries that look

  private (e-mail, file path, 9+ digit number, secrets) are not sent; two searches per question; models without

  tool support answer without it. `web_tool_enabled` (default on), `/status` says `스스로 검색`.

- Verified before building (Ollama 0.34.4): qwen3.8:27b and gpt-oss:20b both call the tool for "모르면 직접 확인해서

  답해줘" and not for a coding question. Live after building: qwen3.8 searched in 3/3 cases that needed it, 0/1 that

  did not, with sources; one answer then equated release date with training cutoff. gpt-oss:20b exposed two faults,

  both fixed: it answers with an empty body when the tools are withdrawn on the last turn (so tools now stay offered

  and the app refuses further searches instead), and it turned one failed search into "external search is blocked"

  (the tool result now says one search failed, the internet is not blocked).

- **The testing tripped DuckDuckGo's bot block** (about 15 searches in a few minutes). `WebReader.search` now reuses

  results for the same query for 10 minutes and stops sending for 5 minutes after a block, instead of knocking

  again. The block was still on at 19:34; it lifts by itself. Three tests.

- **Polite Korean even when the user writes casually.** qwen3.8:27b answered "맞아, 나는 호출 못 해" after the user

  wrote in 반말 - the system prompt only said "answer in the user's language". It now says to keep 존댓말 even when

  the user does not. Every test question in the live runs above was 반말; every model answer was polite (6/6, 11/11).

- **The full name under "ALEX"** in the launcher window and in the web sidebar: "Adaptive Learning & Evolving

  eXperience" (PROJECT_ALEX 1). The sidebar previously said "YOUR LOCAL INTELLIGENCE". Checked with a headless Chrome

  screenshot (`logs/claude-ui-brand-20260925.png`) and by measuring the Tk layout; the UI smoke scripts named in the

  handoff are not on the desktop.

- 654 tests (2 skip), router 65/65, security 13/13, RELEASE VERIFICATION PASS

  (`logs/claude-webtool-verify2-20260925.txt`). App restarted on the new code (`d8afdbe21d302e44`).



- **Approved experiences now change the next answer - growth without swapping the model (ADR-055).**

  The user asked for an ALEX that grows without replacing its model. The experience store (ADR-037) had

  capture, human review and a training pool, but nothing ever read an approved experience back: a

  correction only mattered after a future weight update. `jarvis/lessons.py` puts up to three approved

  **train**-split `answer` experiences that match the question into the prompt, after the summary so they

  outlive it when the budget is tight, and the answer ends with `— 참고한 승인 경험: id (/growth)`.

  Never used: eval (the ruler for growth), anything a human has not approved, route/edit/codegen, and

  identity (mostly agents' own work notes - they attached to unrelated questions). Not sent to cloud.

- In-app review: `/review` lists waiting experiences, `/review ID` shows one in full, `/review ID 승인|거절`

  records the user's decision (`reviewed_by = user(app)`). `/growth` shows the loop stage by stage. `/status`

  gains a `lessons:` line. `lesson_context_limit` (default 3, 0 = off) in settings and

  `JARVIS_LESSON_CONTEXT_LIMIT`. **Agents do not approve** - same rule as ADR-037/050.

- Matching was measured, not guessed: all existing experiences *assumed* approved in memory only, run

  against the user's last 60 real questions. Bigram counting attached lessons to 27/60, many absurd ("= 버튼이

  두개야" got a test-fixture note). Word-level matching with the stem required and one count per stem:

  20/60, one clear false positive; heavy paraphrases are missed (`logs/claude-lesson-dryrun-20260925.txt`).

  **First A/B (PERFORMANCE 27, 20b, n=3, lessons approved in a DB copy only):** repeating the chat's earlier

  wrong claim ("internet is blocked") 2/3 -> 0/3, asserting what it cannot check 1/3 -> 0/3, but explicitly

  saying "what I said earlier was wrong" 0/3 -> 0/3. The harmful part stopped; self-correction did not come.

  120b is being measured. Until the user approves something, nothing changes.

- **Empty answers from a full context window now say so.** The user switched the desktop to `qwen3.8:27b`

  and asked in a long session: prompt 7830 tokens in an 8192 window, 361 tokens left, all spent thinking,

  zero content - twice, because the fallback retried the same model through the coder slot (35.6 s more).

  The app said "no model can answer, check the Ollama server and model tag" and appended "the answer was cut

  off". New `ContextExhausted` (with the prompt and window sizes), no retry on the same model and window,

  a message that says what happened and what to do (/new, desktop_num_ctx), and no cut-off/lesson notice on

  an answer that never started. Six tests.

- **`desktop_num_ctx` 0 -> 16384** (PERFORMANCE 26-1): 7830 + 6144 = 13974 needs more than 8192; 16384 is the

  smallest value that holds it, and phase 0 showed 120b no slower at 16384. Those numbers were FA on, so the

  FA-off stage-1 re-run checks it. Set while the app was stopped (a running settings panel rewrites the whole

  file). Previous value kept in `logs/claude-backup-20260925-growth/jarvis.local.json`.

- Two of today's wrong answers recorded as **raw** experiences (ADR-050): "natural-language questions do not

  search, use /search" (qwen3.5:122b, #198) and "qwen3.5:122b was trained up to 2024-06" (#196, copied from

  gpt-oss earlier in the chat). Both landed in train; they become lessons only if the user approves them.

  `logs/experience-add-20260925-desktop-3.json`. Store 37, trainable 0, integrity ok.



- **Flash attention is off on the desktop only (ADR-054).** The user's pick, `qwen3.5:122b`, died while

  loading at 17:28: `CUDA error: shared object initialization failed` inside

  `ggml_cuda_flash_attn_ext_mma_f16_case` (`cudaFuncSetAttribute`), `llama-server` exit 0xc0000409,

  HTTP 500. Not every time - the very next load on the same FA-on server came up in 43.95 s and answered.

  Codex confirmed one load with FA off on a separate port, measured the cost (desktop 20b about -10%,

  120b unchanged; not a paired comparison), set `OLLAMA_FLASH_ATTENTION=0` for **every** profile in the

  launcher, and ran out of usage before writing the ADR the comment pointed to.

- Narrowed it to the DESKTOP profile: the laptop was never measured with FA off, and every laptop number in

  PERFORMANCE 12-25 was taken with it on. `launcher.ollama_environment(profile)`; hardware detection now

  runs **before** Ollama starts (it used to run after, so the profile could not choose the environment),

  and `force_profile` applies before it too. Six tests, three of them driving `launch()` itself with a fake

  `Popen` so moving detection back below the start fails a test. 596 -> 602 tests, router 65/65, security

  smoke 13/13, RELEASE VERIFICATION PASS (`logs/claude-fa-verify-20260925.txt`).

- The FA-on offload numbers already in PERFORMANCE 26 (phase 0 and the first seven stage-1 runs) are no

  longer the app's conditions. They stay on disk; the desktop is re-measured with FA off.

- The same minute also produced one SQLite `disk I/O error` on a page refresh while about 75 GiB was being

  loaded into RAM. Cause not established; not seen again; `PRAGMA integrity_check` = ok after the 18:14

  reboot. The reboot also killed the stage-1 run (it lived in WSL).

- Backups: `logs/claude-backup-20260925-fa/`.

- Measurement scripts: `export ALEX_QUIET_SECONDS=300` never reached `alex_idle.py` - WSL passes only variables named

  in `WSLENV` to Windows programs, so the idle wait ran on its 60 s default ("idle after waiting 80s") and measurements

  started a minute after the user's last chat. Added to `WSLENV` in both stage scripts; Windows Python now sees 300.

  `scripts/desktop_stage1_fa0.sh` (FA off, ctx 16384) replaces the FA-on stage-1 run, which stays as evidence.



## Unreleased - 2026-09-25 (desktop first run) - claude on the desktop



- **Natural-language automatic web search (ADR-053):** current prices, weather, news, releases,

  travel and hotel recommendations now route to local-model web answers without `/search`.

  Windows release verification passed (596 tests, security smoke 13/13). A live replay of the user's

  two failed questions took the web route and included source URLs; the follow-up question about

  ALEX's own search ability stayed local. The price answer still repeated an incorrect MSRP from a

  third-party result. Two extra prompt variants did not fix the factual error, so neither was kept.

  Evidence: `logs/claude-autosearch-verify-20260925.txt`,

  `logs/claude-autosearch-live-20260925.json`, and

  `logs/codex-autosearch-price-recheck*-20260925.json`.

- **The desktop ZIP failed release verification: every chat request crashed.** First run on the

  RTX 5080 desktop with the documented Windows Python 3.11.9: 587 tests -> 83 errors + 1 failure,

  security smoke and alpha smoke FAIL. All 84 were one line - `clock_line()` put Korean inside

  strftime format specs (`{moment:%Y년 %m월 %d일}`). Windows strftime encodes the format with the

  C locale's code page, and this desktop's system locale (for non-Unicode programs) is en-US, code

  page 1252, while its display culture is ko-KR. `'locale' codec can't encode character '년'`.

  This was not a test-only failure: `clock_line()` builds the system message of every chat request,

  so ALEX could not answer anything on this machine. The laptop's locale encodes Korean, so the

  laptop's PASS (and the ZIP's own PASS) could not see it.

- Fix: format specs are ASCII only (`{moment:%Y}년 {moment:%m}월 ...`); the output is byte-for-byte

  the same. Changing the Windows locale was not an option - it needs a reboot and affects every

  other program. Regression test switches the C locale to `English_United States.1252` itself so it

  fails on any Windows machine regardless of that machine's locale (checked: the old format passes

  under `Korean_Korea.949` and fails under 1252). Only these three lines used non-ASCII formats.

- Verification after the fix: 588 tests (2 skip), router 51/51, security smoke 13/13,

  RELEASE VERIFICATION PASS (`logs/claude-desktop-verify-20260925-after-fix.txt`; the failing run

  is `logs/claude-desktop-verify-20260925.txt`). Backups: `logs/claude-backup-20260925-locale/`.

- Desktop setup done headless with the launcher's own `launch(True)` (same as the "모델 다운로드

  시작" button): Python 3.11.9 per-user (PSF signature valid, no PATH change, no admin), Ollama

  v0.34.4 portable (SHA-256 verified), `gpt-oss:20b` into ALEX's `runtime/models`. Ollama sees the

  RTX 5080 (CUDA compute 12.0, cuda_v13, 15.9 GiB). Log: `logs/claude-desktop-firstrun-20260925.txt`.

  First run took 4 min 53 s end to end (1.46 GB runtime + 13.8 GB model at about 55-65 MB/s).

- **ALEX told the user it runs on the laptop.** On the desktop, "속도는 어때?" got "RTX 4070 Laptop

  8GB VRAM, RAM 31GB" although `_environment_line` gives the RTX 5080 facts on every request. The

  long-term memory still said `장비=RTX 4070 Laptop 8GB VRAM, RAM 31GB`, and the same session already

  held ALEX's own RTX 4070 answer (msg 162) - the same "earlier wrong answer wins" pattern as

  PERFORMANCE 25-3. When the user pushed back ALEX corrected the specs but invented that the memory was

  "another user's preference". **User decision:** keep the memory, list both machines. Changed through

  the app's own `/remember` (commands add no chat messages); old value recorded here:

  `RTX 4070 Laptop 8GB VRAM, RAM 31GB` -> `데스크톱 RTX 5080 16GB VRAM, RAM 96GB (주 작업기) / 노트북

  RTX 4070 Laptop 8GB VRAM, RAM 31GB`. Both wrong answers recorded as raw experiences (ADR-050,

  `logs/experience-add-20260925-desktop.json`); not approved, no split. Store: 34, trainable 0, integrity ok.

- **User switched the desktop model to `gpt-oss:120b` (settings panel, 15:59:48)** before the comparison

  finished - `desktop_general_model`/`desktop_coder_model` = `gpt-oss:120b`, `desktop_max_output_tokens`

  = 6144 (same as the global). Kept as the user's decision; the laptop profile still uses 20b. The

  measurement scripts name their model explicitly, so they are not affected. First two 120b chats asked

  for its knowledge cutoff; it answered 2024-06 (the same family as 20b).

- **User asked for other families ("왜 GPT 계열뿐이냐 - 사용 가능한 것 중 최고 모델만").** Re-queried the

  Ollama library (the laptop's list was newest-first only and missed `gemma4`). Downloaded through ALEX's

  runtime: `qwen3.8:27b`, `gemma4:31b`, `muse-glimmer:30b` (all local, general, thinking+tools). Left out:

  cloud-only models, code-only models, and models whose RAM share would leave only a few GB free

  (`qwen3.5:122b` 81GB, `nemotron-3-super:120b` 87GB, dense `mistral-medium-3.5:128b` 80GB). Reasons and

  the two-stage screening plan are in PERFORMANCE 26, written before any result.

- Measurement guard (`logs/probe/alex_idle.py`): the user's chat evicted 120b mid-measurement (Ollama

  keeps one model), costing one turn a 99.6 s reload. Each step now waits until ALEX is idle and is

  re-run once if a chat arrived during it.

- RTL grading tools for the desktop: YosysHQ OSS CAD Suite 2026-09-24 Windows build (SHA-256 from the

  GitHub release checked) extracted to `C:\oss-cad-suite` - Icarus Verilog 14.0 (devel), Verilator 5.053.

  No system PATH change: measurement commands prepend it per process. `C:\oss-cad-suite\shim\verilator.cmd`

  exists because Windows `shutil.which` cannot find the extensionless `verilator` Perl wrapper; it sets

  `VERILATOR_ROOT` (without it lint fails on the missing waiver file).



## Unreleased - 2026-09-25 (next: bigger model on the desktop) - claude



- **User decision: continue on the desktop with a bigger model.** The handoffs said "download 120b but

  do not make it the default - every graded task is already a perfect score for 20b, so there is

  nothing to tell them apart". That no longer holds. Today produced a task 20b reliably fails:

  correcting its own earlier wrong answers inside the same conversation - 0/3 on the user's real

  session even after Codex's fix (one run invented a `/modify` command). Handoffs now say: take the

  bigger model, but earn the switch by measurement, starting with that task.

- `scripts/replay_session.py` - the replay probe promoted from `logs/probe/` (which the desktop ZIP

  does not carry). `replay` rewinds a real session on a copy of the DB and re-asks; `fresh` asks the

  cloud/internet questions in an empty session; `--model` puts the same model in every profile slot.

  No automatic grading on purpose. Three tests; a SQLite connection left open by `with connect()`

  (which commits but does not close) was caught by Windows refusing to delete the temp file.



## Unreleased - 2026-09-25 (remote) - claude



- **Use the desktop's ALEX from the laptop, at home or outside (ADR-052).** SSH local forwarding over

  Tailscale: laptop `127.0.0.1:8766` -> desktop `127.0.0.1:8765`. ALEX still listens on loopback only

  and gains no login of its own; SSH authenticates, Tailscale connects the two machines anywhere. Works

  without touching ALEX's Host check - it only looks at the host name, and a tunnelled request is

  `127.0.0.1`.

- `connect_desktop.cmd` (laptop, double-click): opens the tunnel, confirms that what answers is a

  local-only ALEX before opening the browser, warns when the desktop runs different code, and turns

  ssh's English errors into what to do - name not found, desktop off or Tailscale down, SSH server not

  installed, login failed, host identity changed, port busy. `setup` makes a passwordless ed25519 key,

  saves the target (backup kept), and prints the one line to paste on the desktop. `check` reports

  what is ready, including whether the desktop's port 22 is reachable.

- `scripts/remote_setup_desktop.ps1` (desktop, admin, UTF-8 BOM): OpenSSH server on auto-start,

  firewall limited to Tailscale ranges, laptop key registered in both the admin and user key files

  with ACLs set by SID, ALEX started hidden at login via `start_alex_background.vbs` ->

  `launcher --background`. It deliberately does not start ALEX itself - from an elevated window ALEX

  would run as administrator.

- Guarded ssh argv: host/user refuse a leading '-', spaces and specials, so a value like

  `-oProxyCommand=...` can never become an option.

- 34 tests with a fake ssh and a fake desktop; the real `ssh.exe` failure paths ("Connection refused",

  "Could not resolve hostname") map to the right advice; a real `ssh-keygen` key passes the desktop

  script's check; the script parses under PowerShell 5.1. **Not yet run end to end on the real desktop.**

- `REMOTE_SETUP.md`: step by step, outside use, troubleshooting table, offline fallback, what to do if

  the laptop is lost, and what does not work yet (phone, waking the desktop from outside).





## Unreleased - 2026-09-25 - claude



- **ALEX can read the web: `/search <query>` and `/web <url> :: <question>` (ADR-051).** Read-only,

  user-invoked, standard library only. Search returns six results plus the body of the first page

  that loads; the answer comes from the local model and the **app** appends the sources.

- **Local-only was never "no internet".** `local_only` keeps conversation and memory away from

  cloud LLMs (README, "Cloud 경계"). Reading a web page is not an LLM call and costs no tokens.

  I had called web access a conflict with the local-first principle; the user pointed out that

  the principle is about tokens, and that a work assistant has to be able to look things up.

- ALEX had been telling users it had no internet because `_environment_line` told the model so on

  every request ("외부 API 로 나가지 않는다"). The line now describes `/search` and `/web`. Asked

  in plain language, ALEX now points to `/search` instead of saying it is impossible.

- **SSRF is the real risk and it is checked twice:** every resolved address, and the address of

  the socket actually connected - before TLS, before HTTP - so DNS rebinding cannot slip past.

  Loopback, private, link-local, CGNAT, reserved, IPv4-mapped IPv6 all refused. No proxy env, no

  file/ftp/data handlers, http/https redirects only, 2MB / 15s caps, text types only.

- Tests caught two defects in my first version: the hand-built `OpenerDirector` lacked

  `UnknownHandler`, so `file://` returned `None` instead of raising; and the fallback extractor

  for broken HTML fired on ordinary short pages and brought the nav/footer back.

- **Measured on the laptop, not assumed** (`PERFORMANCE.md` section 25): gpt-oss:20b answered all

  three lookups correctly with sources, 5-10s once the model is loaded. That was the user's

  condition for shipping it.

- `tests/test_webread.py` (38, including a real 127.0.0.1 server reachable by plain urllib and

  refused by ALEX's opener), `tests/test_web_commands.py` (21), two new `security_smoke` checks

  that aim the reader at ALEX's own web server. 462 -> 521 tests, RELEASE VERIFICATION PASS.

- Not done: the model cannot search on its own (no tool-call loop), PDFs and JS-rendered pages are

  unreadable, and buying anything is out of scope by design.

- **The launcher silently reused a server running old code.** After adding web reading I relaunched

  ALEX and `/status` had no `web:` line: the port was held by a `python.exe` server Codex had

  started at 07:56, I had only killed `pythonw.exe`, and the launcher reuses any running server of

  the same project without asking whether the code changed. A user double-clicking after an update

  would get the old version with no hint. The server now reports a **content** fingerprint of the

  code it loaded (`/health` -> `code`), and the launcher refuses to reuse a server whose fingerprint

  differs from disk, telling the user to press 앱 종료 and relaunch. Content, not mtime - unzipping

  changes mtimes. Nine tests; verified on the real app (refused -> quit -> relaunch -> reused).

  Same class as ADR-049, recorded there. 521 -> 530 tests.

- **No command needed: asking in chat searches** (user request). The router now sends "○○ 검색해줘",

  "○○ 찾아봐", and messages with a public URL to the web. Still only on the user's request - the

  model never decides to go online by itself. Kept out: things on this machine ("코드에서",

  "내 컴퓨터", "아까 말한"), capability questions ("찾아줄 수 있어?"), debugging ("에러 로그 찾아줘"),

  localhost/private URLs. Golden corpus 29 -> 50. The search engine gets the request with the

  politeness stripped; the model gets exactly what the user said.

- **A live run caught a misroute my own cases missed:** "RTX 5080 VRAM 용량 좀 찾아봐" hit the old

  system-info rule on the single word `vram` and showed this laptop's RTX 4070 8GB. My 22 probe

  sentences and 44 corpus cases all passed; none sat where the old rule and the new one overlap.

- **ALEX invented its own cloud story.** Asked about cloud AI it said there was no cloud code, that

  internet was banned, and that one must open a firewall and write `requests` code - then, told to

  "check again", said "as I checked in the current environment". Three providers exist, internet is

  not banned, there is no firewall, and it has no way to check. It had one line of facts to go on.

  The environment line was rewritten three times, each followed by a live re-run: 0/2 -> 0/4 ->

  3/6 -> **7/8** correct, judged by reading the answers. A keyword check had called the first retry

  clean; all four answers in it were wrong in new ways. Five of ALEX's wrong answers were stored as

  experiences with the verified correction - this is I-2, the ALEX-M1 training target.

- **The 7/8 held only in fresh sessions.** In the user's actual conversation - which already held ALEX's

  two wrong answers - the new code still said "internet is blocked, open the firewall". Replayed on a

  copy of the DB, four prompt variants (facts first, facts last, override reminder, summary removed)

  failed to reliably pull it back: 0-1/2 each. The model sides with what it said earlier in the chat.

  The rolling summary is a second path: it is written by the model from its own answers and re-enters

  as a system message; this session's summary still asserted a fixed date and "20B needs an 80GB GPU".

  No more prompt growth. Structural options (drop `/bad`-marked answers from context and summary; keep

  claims about ALEX itself out of summaries) are left for an ADR the user should approve.

- **Links are clickable** (user: "링크주소만 알려주고 하이퍼링크는 안되네"). The chat renderer put

  everything outside code fences in as plain text. URLs now become links; source lines

  (`[n] 제목 — 주소`) link the **title** and show the host beside it; markdown `[text](url)` works.

  Built with DOM calls only, http/https only, new tab with `noopener noreferrer`. Checked in real

  headless Chrome (19: `javascript:` stays text, HTML injection stays text, URLs inside code blocks

  untouched, trailing punctuation and Korean after a URL excluded); Codex's settings UI smoke still

  19/19; four static tests pin the safety attributes.

- **Given web material, ALEX still said it had no internet - in English.** The user put two searches

  and one URL in a single message; the URL routed it to a page read, the page came back, and the model

  answered in English that it had no real-time access. The material header never said the material

  had just come from the internet, what language to answer in, or what to do when it covers only part

  of the request. Replaying that exact message: before 0/2 (one empty answer, one "please wait"),

  after 3/3 answering V13.0 in Korean and saying the RTX 5080 figure was not in the material.

- Experiences 21 -> 31. 542 -> 546 tests.





## Unreleased - 2026-09-21 (6) - claude



- **A guard test was failing 1 run in 6, and the guard it replaced was the point.**

  `verify_release.py` came back FAIL on

  `test_portable_experiences.RoundTripTests.test_the_fixture_actually_contains_both_splits`.

  Run alone it passed; in the suite it failed. Not isolation - the fixture is probabilistic.

  `record_experience` derives the id from `sha256(content_hash + timestamp)` and the split

  from `sha256(id)`, so eight fixed records land wherever the clock puts them. With

  `EVAL_RATIO = 0.2`, all eight land in `train` with probability `0.8**8 = 16.8%`.

- The damage was not the red run. `test_train_eval_split_is_preserved` is the reason this

  file exists, and on those 16.8% of runs it compared train-only sets on both sides and

  **passed without ever exercising `eval`** - the exact hole the guard was added to catch.

- Fixed by recording until both splits are present (minimum 8, hard stop at 200) instead of

  hoping. `test_importing_twice_adds_nothing` now asserts against `self.count` rather than a

  hardcoded 8. 15 consecutive runs OK; 438 tests, RELEASE VERIFICATION PASS.

- **Lesson for any fixture built on the deterministic split:** an id that contains a timestamp

  is a random variable. Asserting a distribution over 8 draws is not a test, it is a coin flip.



- **A setting was being ignored in silence, and that is why nobody noticed the model was wrong.**

  `jarvis.local.json` has `coder_model: gpt-oss:20b` - the model ADR-047 decided on - but on a

  laptop profile `build_providers` overwrites `coder = general` when `single_model_on_laptop`

  is on. The setting stays in the file, stays in the settings panel, and does nothing.

- Nothing on screen said so. `/status` never printed a model at all. `/gpu` skipped the coder

  line entirely via `id(provider) in seen`, so the shared case looked identical to a

  one-model machine. The result: ALEX answered every route with `qwen3.5:9b-q4_K_M`, a model

  never scored on the hard Python set or the identity set, while the documents said 20b.

- `/status` now names both effective models and marks the shared case; `/gpu` says why the

  coder line is missing instead of dropping it. Four tests in `tests/test_status_models.py`,

  one of which reconstructs the exact 2026-09-21 state. 442 tests, RELEASE VERIFICATION PASS.

- **The rule this is an instance of:** a setting that is overridden must say so where the user

  looks. Silently winning is how a decision written in an ADR stops being true without anyone

  editing the ADR.

- Corrected item C in `GPT_NEXT.md`: `HARDWARE_VALIDATION.md` was already filled in on 09-19.

  I handed Codex finished work because I trusted a stale note instead of opening the section.



- **The work being done on this repo can now become training data.** Until today the experience

  store had four doors and all of them were the user talking to ALEX: `/good`, `/bad`,

  `/apply-edit`, `/discard-edit`, plus study attempts. `import-corrections` harvested nine

  corrections from the documents, but its list is hardcoded in the script - a new correction

  meant editing the script. So nothing Claude or Codex did ever entered the store.

- `experiences.py add --file <json>` is that door. One object or a list; dry-run by default;

  `source` blocks duplicates; `split` is refused outright because `record_experience` derives

  it from the id hash (ADR-037 decision 5). Thirteen tests in `tests/test_experience_add.py`.

- Recorded today's four through it - the coin-flip fixture, the stale TODO handed to Codex, the

  board queue consumed by a read without `--peek`, and green tests standing in for a claim about

  which model was running. Each carries the judgment, the evidence that refuted it, and the

  correction. Experiences 12 -> 16, `raw/train` 7 -> 11, `training_pool` still 0.

- **Approval stays with the user.** These are records of my own errors; approving them myself is

  the self-reference the review stage exists to prevent.



- **Item A closed: the code-specialised model lost, and it lost at hardware code.**

  `qwen2.5-coder:14b` scored 5/6 on the hard Python set but **2/6 on RTL** against the general

  20b's 6/6 - an 8-bit adder that computes 0+76+1 = 184, a priority encoder that reports

  `valid=0` on a live request, a Gray round-trip that does not round-trip, a FIFO that calls

  itself empty while holding data. Identity came in at 5.0/7 over three runs, under the 6.0 bar.

- The identity number is the one that settles it. First run was 4.0/7; the rule is not to

  disqualify a model on n=1, so two more runs went in (6/7, 5/7). The mean rose, but

  `measured_vs_guess` failed **all three times** - I-2 0/3. That is the trait ADR-047 turned on,

  and the trait the whole experience store exists to train. Not variance, a property.

- **The comparison table had been unfair and I built it that way.** Only 20b was measured with

  `think off` while the app runs `think on`; every other row was the app's real setting. Re-run

  at `think on`, 20b scores 6/6 in **147.6s** against 9b's 5/6 in **545.8s**.

- That result also kills a claim I had just written. I had recorded "think on costs 3-4x, two to

  three minutes a task" - true of 9b, not of `think`. 20b averages 24.6s a task with thinking on.

  9b spends its thinking budget: three tasks at 164s, 146s, 140s against 20b's worst of 63s.

- **Throughput is not latency.** 20b is the slower model by `tok/s` (23.4) and the faster one by

  wall clock. Choosing a model on tokens per second would have picked the wrong one.

- `gpt-oss:20b` set as the default for **both** profiles - laptop and desktop - in

  `jarvis.local.json` (backup `jarvis.local.json.bak-20260921-2010`). Restart required.

  `PERFORMANCE.md` section 24 carries the tables, the per-task timings, and what stays unmeasured.

- Two more experiences recorded through the new door: benchmarking a condition the running system

  did not use, and generalising one model's cost to a setting. Experiences 16 -> 18.





## Unreleased — 2026-09-21 (5) — claude



- **`training_pool` finally has a path off zero.** Built `experiences.py import-corrections`

  from the spec written for Codex, which never got to it. Nine corrections the documents state

  explicitly - ADR-034's contaminated comparison, ADR-046's refuted mechanism, the 6.6x offload

  misread, the MoE reversal, the 5120 budget, the contaminated 20b reading, "max GPU is best",

  the overfitted Python score, and the probe-budget misdiagnosis - are now experiences with

  `verdict='bad'` and the correction attached. Experiences 3 -> 12 (7 in `train`).

- The list is **hand-picked on purpose**. Letting a regex or a model decide what "looks like a

  correction" would be the model grading itself, which is the one thing this data must not be.

  What the script verifies instead is an **anchor**: the sentence must still exist in its

  document, or the entry is reported and skipped rather than recorded against a source that

  has moved. Dry-run by default; `--write` to store.

- **Approving them is left to the user.** I wrote these corrections; approving my own would be

  the same self-reference the design exists to prevent.

- **The expert tier exists because 20b showed no ceiling.** It scored 6/6 on the hard set, so

  there was no visible weakness to aim training at. Added six more: an expression parser with

  precedence and unary minus, compensated summation, UTF-8-safe byte truncation, bounded-memory

  top-k with stable ties, a generator whose cleanup must run on `close()`, and a k-way merge

  over huge streams.

- Writing the reference parser caught a genuine Python trap: **`"" in "*/"` is `True`**, so

  `while peek() in "*/"` never terminates at end of input. My first reference solution failed

  all eight cases. A task that traps the reference implementation is a task worth keeping.

- **Truncated answers now say so.** `done_reason` was already preserved in

  `OllamaProvider.last_metrics` on both the streaming and non-streaming paths - nothing read it.

  A budget-exhausted answer therefore looked like a finished one, and an odd ending reads as

  the model being strange rather than the budget running out. `_truncation_notice()` appends a

  line the same way `GenerationCancelled` does. This matters more now: `gpt-oss:20b` spends

  budget on `thinking` even with `think: False`.

- **The expert tier found no ceiling: `gpt-oss:20b` scored 6/6 there too.** Across three

  difficulty tiers it is 12/12, 6/6, 6/6 while `qwen3:8b` drops to 3/6 at tier two.

  **So "raise coding ability by fine-tuning" currently has no evidence behind it** - nothing

  has shown there is headroom to raise. Where training is worth doing is the user's domain

  (RTL, lithography) and this repository's conventions, not general Python.

- Added study problems 29 and 30 from today's two real traps.

- **Added `scripts/grade_rtl.py` - the domain harness ADR-048 calls for.** Six Verilog-2001

  tasks graded by simulation, not by reading: an 8-bit adder (100k random vectors plus the

  max-carry case), an MSB-priority encoder (all 256 inputs), a synchronous counter whose reset

  must beat its enable, a Gray-code pair checked for round-trip **and** single-bit adjacency,

  a rising-edge detector that must pulse exactly once while the input stays high, and a depth-4

  synchronous FIFO exercised past full, past empty, and on simultaneous read/write.

  `verilator --lint-only -Wall` runs on anything that passes.

- RTL suits this project better than Python does: **the verdict comes from the simulator.**

  A testbench with a reference model knows the answer, so no human and no model has to judge -

  which is the property the experience store needs (STUDY_GUIDE problem 25).

- Harness validated the same way as the others: reference implementations 6/6 PASS, stub

  implementations 6/6 FAIL. The FIFO reference had to model simultaneous read/write to pass,

  which is exactly the case a careless design gets wrong.

- **`gpt-oss:20b` scored 6/6 on RTL too**, FIFO included, with no lint warnings. Across

  Python (18 tasks), RTL (6) and identity (7) **nothing has found its ceiling.**

- **That undercuts ADR-048's own premise.** It moved the training target to the domain because

  general ability showed no headroom - but the domain tasks are full marks as well. The reason

  is that these are textbook parts: an 8-bit adder, a priority encoder, Gray code, a depth-4

  FIFO. A base model has seen thousands of each. **The next tasks have to come from the user's

  real work**, because there is only something to learn where the base model could not already

  know it. Recorded on ADR-048's re-open conditions rather than left to be rediscovered.

- Tests: 426 -> 438.





## Unreleased — 2026-09-21 (4) — claude



- **The Python benchmark could not tell the models apart.** `qwen3:8b`, `qwen3:14b` and

  `gpt-oss:20b` all scored full marks on the six tasks from (3), so the 12/12 recorded for

  20b proved nothing about it being the better model - 8b matched it at twice the speed.

  A benchmark that everything passes answers no question.

- Added six harder tasks (`--only-hard`) aimed where small models actually break: a

  topological sort that must be deterministic among equals, the index of the *first* bracket

  error, an LRU whose `get` must update recency, histogram bins where only the last is

  right-inclusive, a retry decorator that must re-raise the **original** exception and not

  retry unlisted ones, and a generator that must work on `itertools.count()`.

  Validated the same way: correct code 6/6 PASS, stub code 6/6 FAIL.

- **The hard set discriminates: `qwen3:8b` 3/6, `gpt-oss:20b` 6/6.** All three of 8b's

  failures are one kind of error - the spec's exception clause was ignored. It missed

  "only the last bin is right-inclusive", raised a fresh `Exception` instead of re-raising

  the original (so a caller's `except ValueError` would not catch it), and treated a `list`

  as an iterator. **That is a measurable, fixable defect, not "bad at Python".**

- ADR-047 holds. Its re-open condition said "if 8b scores 12/12" - which happened - but the

  condition never said *on which ruler*, and the easy set was the wrong one. Conditions now

  name the benchmark as well as the number.

- **Experiences now travel between devices with their identity intact.** `portable.py`

  carried only memories and tasks. Re-recording an experience on the other machine would

  mint a new id, and `split` is derived from the id hash - so the same content could be

  `train` on the laptop and `eval` on the desktop, and **one device would train on what the

  other uses to evaluate.** Added `Storage.import_experience()`, which preserves `id`,

  `created_at` and `split`, recomputes `content_hash` from the five body fields and refuses

  the record if it no longer matches. Bundle format v2; v1 files still load.

- A single corrupt record is rejected on its own and the rest of the move continues. A

  transfer that aborts wholesale on one bad row makes device migration impossible.

- Tests: 415 -> 426.





## Unreleased — 2026-09-21 (3) — claude



- **"ALEX is only good for simple coding" was wrong, and the evidence for it was one task.**

  The 18/20 figure came from the calculator benchmark - the same task `CODING_SYSTEM_ADDENDUM`

  was written against. Added `scripts/grade_python.py`: six varied tasks (parsing, data

  structures, numerics with exceptions, file I/O, search boundaries, statistics), graded by

  hidden asserts the model never sees, with boundary cases chosen where small models usually

  break. Validated the harness first - correct code 6/6 PASS, stub code 6/6 FAIL.

  **`gpt-oss:20b` scored 12/12 across two runs.** It also produced a Verilog ripple-carry adder

  that passed 101,024 vectors under `iverilog` with zero `verilator -Wall` warnings.

- **Identity for `gpt-oss:20b` is 7.0/7 - including I-2.** That trait failed 0/3 on `qwen3:8b`

  and `PROJECT_ALEX.md` section 4 named it the first learning target precisely because the

  system prompt already forbids the behaviour and the model did it anyway. **A model swap

  solved what was scoped as a fine-tuning problem.**

- One general model therefore covers conversation, identity, Python and Verilog. No weight

  merging, no general/coder dual routing, no code-specialised model. The cost is half the

  throughput (23.4 vs 47.4 tok/s).

- Added `JarvisCore._environment_line()`: the model is now told which model it is, the

  hardware, the project root, and whether local-only is on - appended **after** budget

  truncation like the clock. Asked "너는 무슨 모델이야?" it had answered **"GPT-4 기반

  모델입니다"**, and "프로젝트 폴더는?" had returned **"저"**. The app knew both and was not

  passing them.

- **One of the three failures I diagnosed was my own measurement.** "내 컴퓨터 사양은?"

  returned an empty answer because my probe set `num_predict` to 250 and `gpt-oss:20b`

  writes 200-800 chars of `thinking` **even with `think: False`** - `done_reason` was

  `length`. At 2000 it answers correctly. I read a truncated probe as the model not knowing.

- `_environment_line()` falls back to a provider's `.name` when it has no `.model`, so mock and

  cloud providers identify themselves rather than reporting "(미상)".

- Tests: 408 -> 415.





## Unreleased — 2026-09-21 (2) — claude



- **The model had no way to know today's date, and said 2023.** Asked "오늘 날짜가?", the local

  model answered **"2023년 10월 10일입니다"** - a date from its training data, stated as fact.

  Nothing in the app ever told it: not `DEFAULT_SYSTEM_PROMPT`, not `SystemInfoTool` (which

  reports CPU/RAM/GPU but had no clock), not the router. The machine knows the time; the model

  was never shown it. This is the most ordinary form of the one identity trait that fails,

  `IDENTITY.md` I-2 (measured vs guessed, 0/3).

- `clock_line()` now appends the current date/time to the system message **per request**, not at

  startup - the app stays running for days and a startup-baked date would go stale. It is

  appended **after** the context-budget truncation, so a tight budget cannot cut the date off

  and send the model back to 2023.

- **The weekday is computed in Python, not by the model.** Given the English `(Monday)`, the 8B

  answered **수요일**, and to "내일은?" answered **금요일** (actually Monday/Tuesday). Handing it

  the Korean weekday and tomorrow's date directly made all four probe questions correct. A

  conversion the code can do must not be delegated to a small model.

- `SystemInfoTool` now reports `now:` first. It is the tool that answers "what do you know about

  this machine", and it had no clock.

- **A restarted app silently broke every open tab.** The CSRF token is per-process and baked

  into the page (`web.py:50`), so after a restart the browser keeps sending the old one and the

  server answers 403 - which looked like "no response" and nothing else. The page now says the

  app restarted, offers a Reload button, and disables the composer; `lock(false)` no longer

  re-enables it. This is how the user actually hit it today, after I restarted the app twice.

- `request_timeout` 180 -> **360**, because the model was switched to `gpt-oss:20b`: 5120 tokens

  at the measured 23.4 tok/s is 219s, plus a 30s cold load. The old value was measured for

  `qwen3:8b` at 47.4 tok/s.

- **Fixed session ordering - the third instance of one root cause.** The full suite failed

  intermittently on `test_list_sessions` (8 straight re-runs passed). `ORDER BY updated_at DESC`

  is ambiguous for sessions created inside the same ~15.6ms Windows clock tick. `latest_session_id()`

  uses that ordering to resume a conversation at startup, so **the app could reopen the wrong

  conversation.** Both queries now break the tie with `rowid DESC`, which also repairs rows

  already stored. Memo ordering and workspace backup filenames were the first two instances;

  the board note asking someone to sweep for more was right.

- Proved the new test catches it by reverting the fix (fails) and restoring it (passes).

  The fixture's frozen timestamp had to be moved into the **past** - a future one made the

  following `add_message` write an earlier real time and read correct behaviour as a failure.

- Tests: 396 -> 400.





## Unreleased — 2026-09-21 — claude



- Startup banner is now an acrostic: A/L/E/X down the left, the expansion across. The X line

  carries **both** meanings from `PROJECT_ALEX.md` section 1 - `eXperience / eXistence` - since

  A, L and E are shared and only X differs. Written as `eXperience`, not `Xperience`: the X

  comes from the middle of the word and trimming the front changes what the acronym says.

  ASCII only, because a Korean comment in a `.cmd` file was silently mangled by cp949 two days

  ago and the banner prints straight to the same console. `tests/test_banner.py` pins the

  vertical axis - reordering the lines would otherwise break it invisibly.

- **Web UI reshaped from a three-column workspace into a messenger.** The user's messages are

  now right-aligned bubbles, ALEX's are left-aligned, each with a timestamp; the sidebar and

  context panel are hidden at every width and open as overlays from ☰ and ⋯. The collapsing

  machinery already existed for narrow screens - this makes it the default rather than adding

  a second mechanism. Dropped the "PERSONAL WORKSPACE" eyebrow, which was most of what made the

  page read as a dashboard.

- Timestamps needed **no server change**: `storage.message_page()` already returned `created_at`

  and `web.py` already passed it through; the client was discarding it.

- An assistant bubble containing a `pre` block widens to 96%. A code block squeezed into a 78%

  chat bubble is unreadable, which is the usual way messenger layouts fail for a coding

  assistant.

- The answer's timestamp is stamped when generation **finishes**, not when the placeholder

  appears - a clock sitting above "생각하고 있습니다…" reads wrong.

- `clockLabel()` returns an empty string for a missing or unparseable `created_at`. A clock that

  cannot be read must not take the message with it.

- Tests: 385 -> 390.





## Unreleased — 2026-09-19 (3) — claude



- **The codegen grader was misreporting why runs failed.** `extract_code()` had no case for an

  unclosed code fence, which is exactly what a budget-exhausted response looks like. It returned

  the whole response, so the leading ```` ```python ```` line produced `SyntaxError` on line 1 and

  the log blamed the model's syntax. Replaying the stored failures shows the real cause: the

  model had fallen into a repetition loop (one `re.sub` line repeated **208** and **134** times,

  95% duplicate lines) and been cut off. Raising the budget cannot fix that - it only buys more

  repetitions. `extract_code()` now returns `(code, how)` with `fenced` / `unclosed-fence` /

  `bare` / `no-code`.

- An empty response is no longer graded as seven `NameError`s. Three think-ON failures had

  `content_chars=0` - the model spent the entire budget thinking and emitted no code at all -

  but the log made it look like broken code. `grade()` now reports `no code emitted` /

  `no def calc() in output` before executing anything.

- `generate()` records Ollama's `done_reason`, `eval_count`, `prompt_eval_count` and the length

  of the thinking channel. `done_reason == "length"` states truncation as a fact instead of it

  being inferred from an error string.

- Added `repetition_ratio()` and a `failures by cause:` summary line, so a run reports

  *budget exhausted* vs *repetition loop* vs *wrong code* - these have different fixes.

- Exposed `--repeat-penalty`, `--seed`, `--temperature`. Neither the grader nor

  `providers.py` set any sampling option, so `repeat_penalty` sits at Ollama's default 1.1 and

  has never been measured, and without a seed no two runs are comparable. **The app is

  deliberately unchanged until the measurement justifies it** (ADR-036 is the precedent).

- `grade()` dedents the extracted block, so an indented code block still runs.

- Tests: 321 -> 332 (`tests/test_codegen_extract.py`).



- Measured the output budget with the fixed grader (n=10, think ON, `num_ctx` 8192):

  mean 4.2 -> **5.5/7**, perfect 6/10 -> **7/10**. But the mean is not the finding - the token

  distribution is. **Every one of the 7 perfect runs finished within 2,650-4,712 tokens; both

  failures consumed exactly 7,000 and emitted no code at all.** Nothing finished in between.

  Above roughly 4,700 tokens this model does not stop, so a larger budget buys failures more

  time, not more successes - both runaway runs took an identical 164.8s, the time it takes to

  fill the cap. Recorded in `PERFORMANCE.md` section 12 and ADR-045.

- Applied the measurement to `jarvis.local.json`:

  `local_max_output_tokens` 4096 -> **5120** (two successful runs needed 4,170 and 4,712, so

  4096 was truncating work that would have passed) and `request_timeout` 120 -> **180**.

  The timeout was the more dangerous of the two: successful runs reached **107.4s** against a

  120s limit, and real prompts are far longer than this benchmark's 327 tokens, so correct

  answers were likely being cut off in daily use. Backup kept as `jarvis.local.json.bak-*`.

  **A running app keeps the old values - restart to pick these up.**

- Added `--brief-thinking` to `grade_codegen.py`. The runaway failures are not repetition

  (`repetition 0%`); they think roughly twice as long as a successful run (29,772 / 32,046 chars

  vs 14,662) and never emit code. `CODING_SYSTEM_ADDENDUM` tells the model to check the examples

  *before* producing code, which pushes in that direction, so the new addendum moves

  verification after the code. **Effect not yet measured** - the flag exists to measure it.

- Added study problems 18-20 to `STUDY_GUIDE.md`, all from today's real bugs: a measuring tool

  that lied about its own failures, a benchmark with no seed, and a `.cmd` file broken by one

  Korean comment (cp949).

- Tests: 332 -> 336 (brief-thinking prompt assembly).



- Measured `--brief-thinking` (prompt only; every other setting identical, n=10):

  perfect runs 7/10 -> **10/10**, `done_reason=length` 2 -> **0**. The finding is in the tail,

  not the average: median thinking barely moved (-9%) while the **maximum** dropped 28%

  (32,046 -> 23,150 chars). The instruction did not make the model terser in general - it

  stopped the runaway, which is exactly the failure it targeted.

  **Recorded as ADR-046 in "measuring, not adopted" state**, and deliberately not applied to

  the app: 3/10 vs 0/10 is Fisher one-tailed p ~ 0.105, below the conventional bar. The reason

  to believe the result is that the *mechanism* moved, not the score - confirmation rounds are

  running to fill n=20 per arm.

- **Corrected the budget set an hour earlier: 5120 -> 6144.** 5120 came from the A run's

  maximum successful output (4,712 tokens), but the B run produced a **perfect** result at

  5,715 tokens - 5120 would have truncated the best-performing configuration. Changing the

  prompt changes the distribution, so a ceiling measured under one condition does not transfer

  to another.

- Added ADR-046 and `PERFORMANCE.md` section 13. ADR count 46, no duplicate numbers.



- **Desktop-specific runtime limits.** Models already switched by profile

  (`desktop_general_model` / `desktop_coder_model`), but `num_ctx`,

  `local_max_output_tokens` and `request_timeout` were global - and those three were measured

  on the 8GB laptop. Carrying them to the 16GB desktop caps it at laptop limits; `num_ctx` in

  particular **overrides** `NUM_CTX_BY_PROFILE[DESKTOP]` (16384) whenever it is set, so the

  desktop would have been pinned to 8192. Added `desktop_num_ctx`,

  `desktop_max_output_tokens`, `desktop_request_timeout` (`JARVIS_DESKTOP_*`), resolved by

  `effective_runtime_limits()`. **0 means inherit, so existing behaviour is unchanged**, and

  the app says so at startup when running on DESKTOP with laptop-tuned globals and no

  overrides. No desktop numbers were invented - they are unmeasured and left at 0.

- Rewrote `HARDWARE_VALIDATION.md` section B from a blank template into a measurement

  procedure: find the `num_ctx` that still reports 100% GPU, then set the budget from the

  **maximum** successful `eval_count` rather than the average (the 5120 mistake), then check

  14B actually beats 8B **with 8B as a control** (9B lost to 8B on the laptop), then re-run

  `grade_identity.py` - a new model must still score 6.0/7, because a model swap asks both

  "is it smarter" and "is it still ALEX".

- Tests: 336 -> 347 (`tests/test_desktop_limits.py`).



- **The output budget is now checked against the context window.** Validation looked at

  `local_max_output_tokens` and `num_ctx` separately and never compared them, so a machine

  that falls to the UNKNOWN profile (`num_ctx` 2048) ran with the laptop-measured budget of

  6144 - **an output budget larger than the entire context window** - and nothing said so.

  Two guards: `validate()` now rejects an explicitly configured pair where the budget is not

  smaller than the context, and `clamp_output_budget()` reserves prompt space

  (`max(1024, num_ctx // 4)`) when the context comes from the profile. The app reports the

  clamp at startup and says to turn thinking off at that tier, because the smallest successful

  code run measured still needed 2,650 output tokens.

  **The measured laptop config (ctx 8192 / budget 6144) is unchanged - it lands exactly on the

  limit**, which is what the reserve was sized to preserve.

- Tests: 347 -> 357.



- **RAM offload is now visible and budgeted.** Nothing in the code ever prevented offloading -

  Ollama places layers automatically and the app never set `num_gpu` - so the only reason this

  machine ran fully on GPU was that 6.19GB happened to fit in 8GB. When it did overflow once

  (a model switch to 9b with `num_ctx` 8192 left in place), **the wall clock went 6.6x and

  nothing said a word**; you had to run `ollama ps` yourself to find out.

  Added `ram_offload_budget_gb` (`JARVIS_RAM_OFFLOAD_BUDGET_GB`, default 0 = not allowed),

  `OllamaProvider.placement()` which reads the real split from `/api/ps`, `offload_report()`

  which turns it into a verdict, and a `/gpu` command that prints it.

  Set to **16.0** for this laptop: 31.2GB RAM, leaving ~15GB for the OS and applications, which

  puts roughly a 33B q4_K_M model within reach.

- The placement is **read, never estimated**. Computing expected VRAM from parameter count and

  quantisation is wrong often enough that a warning built on it would get ignored, and every

  message quotes the measured slowdown for that offload ratio (6.6x at 12%, 12.3x at 48%)

  rather than a generic "this may be slower".

- **Note what this does not do:** the budget does not force or enable offloading - Ollama still

  decides placement. It declares what is acceptable and makes the result visible. Using a

  larger model still means choosing one.

- Tests: 357 -> 372 (`tests/test_ram_offload.py`).



- **Corrected the repository's headline claim about RAM offload, and the tuning rule built on

  it.** `PERFORMANCE.md` said "12% offloaded and the wall clock goes 6.6x", and concluded the

  tuning principle was "keep 100% on GPU". Both are wrong for conversation. That column was a

  **cold start including model load**; the same table's tok/s had only dropped 43.6 -> 38.3,

  a contradiction sitting in plain sight that nobody (me included) followed up.

- Added `scripts/measure_offload.py`, which splits Ollama's `load_duration` /

  `prompt_eval_duration` / `eval_duration` instead of timing the whole request, and runs a

  6-turn conversation so warm turns can be measured separately. Same model

  (`qwen3.5:9b-q4_K_M`), `num_ctx` varied to produce the offload, warm turns only:



  | ctx | GPU% | RAM | first token | tok/s |

  |---|---|---|---|---|

  | 4096 | 100% | 0 | 0.21s | **43.1** |

  | 8192 | 88% | 0.72G | 0.19s | **38.4** (-11%) |

  | 32768 | 76% | 1.61G | 0.36s | **23.8** (-45%) |



  The 6.6x was the **first turn's model load (9.88s), paid once** - `keep_alive` keeps it

  resident and turns 2-6 all showed `load_duration` 0.00s. First-token latency stayed under

  0.5s at every offload level.

- **The tuning rule is now per-route.** Chat turns are 100-500 tokens and stream, and human

  reading is roughly 6 tok/s, so even 24% offloaded (23.8 tok/s) is four times reading speed -

  chat should be allowed to use RAM. Code generation writes 2,650-5,715 tokens in one go and

  is unusable until finished, so 43.1 -> 23.8 tok/s is 116s -> 210s - the coder route should

  stay on GPU. The app already has separate `LOCAL_GENERAL` / `LOCAL_CODER` providers.

- `offload_report()` now quotes measured **throughput loss interpolated from the measured

  curve** instead of the cold-start multiplier it was written with an hour earlier.

- Tests: 372 -> 374.



- **ADR-046 adopted.** The pre-write verification line is gone from `CODING_SYSTEM_ADDENDUM`

  and three lines telling the model to verify *after* writing code are appended. Confirmation

  rounds brought each arm to n=20: perfect runs 12/20 -> **18/20**, P(B >= 18) = **0.032**.

- **The confirmation rounds refuted my explanation of the result, not the result.** At n=10 the

  maximum thinking length had dropped 28% and I wrote, repeatedly, that the score difference was

  too weak to rely on and the *mechanism* was the real evidence. At n=20 that 28% collapsed to

  5% - the second round produced a 30,338-char runaway under the brief prompt too - while the

  score became significant. Budget exhaustion alone (4 -> 1) is p ~ 0.17. What actually improved

  alongside it was **wrong code, 4 -> 1**: moving verification after the code makes the model

  write more correct code, which is the hypothesis's direction reached by a different path.

  ADR-046 says so explicitly, so nobody cites it as "this is how you stop runaway thinking".

- Identity re-scored after the prompt change: **6.0/7, unchanged from the ALEX-M0 baseline**,

  with I-2 still the only failure (`logs/identity-20260919-082055.json`). Changing a prompt can

  move identity, and that scorer exists to catch it.

- `grade_codegen.py --brief-thinking` no longer double-appends now that the app carries the

  instruction; it still applies under `--no-coding-prompt`.

- Tests: 374 -> 376.



- **Fixed a settings-dialog bug that could zero the output budget.** The web panel's

  "maximum output tokens" `<select>` was hardcoded 512/1024/2048/4096. Once the measured value

  6144 was written to `jarvis.local.json`, `select.value = 6144` matched no option and the

  control silently went blank - and saving from that state sent `Number('')` = **0**.

  So merely opening the settings dialog and pressing save would have destroyed the value

  measured today. Added 5120/6144/8192, made the loader **inject an option when the configured

  value is not in the list** so any future value displays, and made the save fall back to the

  current setting rather than 0.

- `index.html` is read once at import (`web.py:43`) while `app.js` is read per request, so a

  page change needs an app restart, not just a browser refresh. Noted here because "just

  refresh" was wrong advice.



- **Ollama's automatic GPU layer placement was leaving throughput on the table, and the code

  had no way to override it.** Prompted by the user asking why the GPU was not filled first:

  `nvidia-smi` showed **1.77GB of VRAM free while 4.22GB of the model sat in system RAM**.

  Measured `qwen3:14b` at ctx 8192 across forced layer counts:



  | num_gpu | VRAM | RAM | GPU% | tok/s |

  |---|---|---|---|---|

  | auto | 5.86G | 4.22G | 58% | 10.3 |

  | **32** | **7.61G** | **2.47G** | **76%** | **15.2** |

  | 36 | 8.48G | 1.60G | 84% | 14.4 |

  | 40 | 9.39G | 0.67G | 93% | 10.7 |



  Forcing 32 layers is **48% faster for free**. But the maximum is not the optimum: past 32 the

  VRAM figure exceeds the physical 8.0GB and the Windows driver spills the excess to system RAM,

  which is *slower* than letting Ollama place explicit CPU layers - 93% on GPU performs the same

  as 58%. The rule is "up to just under physical VRAM", not "as much as possible".

- Added `num_gpu_layers` (`JARVIS_NUM_GPU_LAYERS`, default 0 = automatic), threaded through to

  the Ollama options. **Left at 0.** The optimum is per-model: `qwen3:8b` has 36 layers and

  already fits entirely on the GPU, so setting 32 there would push four layers off it and make

  things worse. The app has one global slot and it does not follow the model - a design gap,

  recorded rather than papered over.

- Tests: 376 -> 385.



- **Measured which model this laptop can actually run** (`PERFORMANCE.md` 16-17절):



  | 모델 | 전체 | GPU% | tok/s |

  |---|---|---|---|

  | qwen3:8b-q4_K_M | 5.76G | 100% | **47.4** |

  | qwen3:14b | 10.08G | 58% | 10.3 |

  | **gpt-oss:20b (MoE)** | 13.09G | **43%** | **23.4** |

  | qwen3:32b | ~20G | - | **won't load** |



  **`gpt-oss:20b` runs 57% out of system RAM and is still more than twice as fast as the 14B**

  at 42% offload. It is MoE: only a fraction of the 20.9B is read per token, and since token

  generation is memory-bandwidth-bound, weights that are never read cost nothing by sitting in

  RAM. **This reverses what I wrote this morning** - I had dismissed MoE as "needs all the VRAM

  for 3B-class speed", which is the dense-model intuition applied where it does not hold. Under

  RAM offload, MoE is the *most* favourable architecture.

- `qwen3:32b` cannot load, and **not because of VRAM**: 31.2GB of RAM with 21.6GB in use leaves

  9.6GB free against a ~20GB requirement. The OS, WSL, browser and editor hold 10GB+ at rest,

  which is exactly why `ram_offload_budget_gb` was set to 16 rather than higher.

- **The `num_gpu` strategy is opposite for dense and MoE models.** Forcing layers onto the GPU

  gained 48% on the dense 14B but **lost 63%** on the MoE 20B (23.4 -> 8.7 tok/s at 88% GPU):

  inactive expert weights occupy VRAM without ever being read, pushing the KV cache and compute

  buffers past the physical 8.0GB. A value tuned on one model must not be carried to another.

- Recorded a contaminated measurement rather than deleting it: the first `gpt-oss:20b` reading

  (18% GPU, 17.1 tok/s) was taken while `qwen3:14b` still held VRAM. Re-measured clean it is

  43% GPU and 23.4 tok/s. **Placement depends on what else is resident at the time** -

  `OLLAMA_MAX_LOADED_MODELS=1` does not help while the previous model is still within its

  `keep_alive` window.



- **Measured KV cache quantisation - the item that had sat "unverified" in five places since

  2026-09-13.** `OLLAMA_FLASH_ATTENTION=1` was already set by the launcher; only

  `OLLAMA_KV_CACHE_TYPE` was left at its f16 default. Added `scripts/measure_kv_cache.py`

  (`qwen3:8b-q4_K_M`, think off, identical prompt; the cache type is a **server** environment

  variable, so Ollama has to be restarted to change it):



  | num_ctx | f16 VRAM / GPU% / tok/s | q8_0 VRAM / GPU% / tok/s |

  |---|---|---|

  | 8192 | 5.76G / 100% / 47.4 | 5.23G / 100% / 46.8 |

  | 12288 | 6.71G / 87% / 36.0 | 5.54G / **100%** / 35.3 |

  | **16384** | 7.28G / **80%** / **29.0** | **5.84G** / **100%** / **35.4** |

  | 32768 | - | 7.56G / 76% / 26.1 |



  **`num_ctx` 16384 now fits entirely on the GPU** - the question section 1 had left open.

  VRAM drops 7.28G -> 5.84G and, with the offload gone, that context runs **22% faster**

  (29.0 -> 35.4 tok/s). At the current ctx 8192 it is essentially free: 0.53G saved for a 1.3%

  throughput change, which is measurement noise.

- **Did not adopt q8_0 either.** Quality at the same settings came out 3/5 perfect against the

  f16 baseline's 18/20. P(q8_0 <= 3) = 0.17, which is **not significant - but "not significant"

  at n=5 means "we cannot tell", not "the same"**; both failures were `done=stop` wrong code,

  not truncation. Since the speed gain at the current ctx 8192 is zero (47.4 -> 46.8, noise) and

  the 0.53G of VRAM saved has nowhere to go on a model already fully resident, turning it on

  would mean accepting an unquantified quality risk for no measured benefit - the same call that

  ADR-036 exists to prevent. **KV quantisation only pays together with `num_ctx` 16384**, and

  that pairing needs its own n=20 measurement of both speed and quality. Ollama was restored to

  the default f16.

- **Did not raise `num_ctx` to 16384.** Every code-quality number confirmed today (18/20 perfect,

  section 13) was measured at ctx 8192 with f16. Doubling the context can move the output-token

  distribution and nothing has measured whether this model gets more scattered over a longer

  context - and at 12288 the q8_0 run was *not* faster than f16 despite having no offload at

  all, which suggests dequantisation cost can cancel the offload gain in some range. One sample

  each. Raising the context is a separate decision that needs its own measurement.



## Unreleased — 2026-09-19 (2) — claude



- Fixed **seven duplicated ADR numbers**. `ADR-020`~`ADR-026` each pointed at two different

  decisions: the Korean v0.1.2/v0.1.3 set and the English v0.2.0 set, which restarted numbering

  from 020. The v0.2.0 block moved to `ADR-038`~`ADR-044`; nothing in the repo referenced the

  old numbers, so no references changed. Added a number index at the top of `DECISIONS.md` -

  ADR numbers are permanent identifiers, so position in the file no longer implies order.

- `/apply-edit` and `/discard-edit` now record an `edit` experience (ADR-037). These are a

  human's real verdict on model-written code, which is the most valuable training signal the

  app produces, and it was being thrown away. The instruction text from `/edit-file` is kept

  until the verdict arrives so the experience carries what was actually asked for.

- `grade_codegen.py` gained `--num-predict`, so the output budget can be varied without

  editing the user's `jarvis.local.json`. Needed because thinking consumes the budget before

  the code is emitted.

- Documented what the repo had but `README.md` never mentioned: the windowed launcher,

  the verified portable-runtime install (`runtime/`), `validate_local.py`, and the grading and

  experience scripts, each with the safety detail that matters (`setup_models.py` is dry-run by

  default; `grade_codegen.py` refuses to grade without `--execute`). Added a **운영 도구** section.

- Documented **local-only mode** in the cloud-boundary section. `JARVIS_LOCAL_ONLY` defaults to

  true and reverts cloud routing decisions *before* the approval gate, so while it is on,

  approving a cloud send still sends nothing. This boundary was implemented but undocumented.

- Documented cancellation: `/api/cancel` sets `cancel_event`, the provider raises

  `GenerationCancelled`, and the partial answer is **kept** in the session rather than discarded.

- Replaced the stale "이 개발 환경에는 ... Ollama가 없다" section. The laptop numbers have been

  measured since 2026-09-13; what is actually unmeasured is the desktop, KV cache quantization,

  30+ turn behaviour, and Ollama over WSL.

- Tests: 315 -> 321.



## Unreleased — 2026-09-19 — claude



- Added the **experience store** (ADR-037, schema v5): the training/eval corpus, deliberately

  separate from `memories`. Five model-independent body fields (situation / judgment /

  rationale / choice / outcome) so accumulated experience survives a base-model change; the

  chat template is applied only at export time.

- Body fields are immutable, enforced by `content_hash`; only review metadata changes.

  `verify_experience_integrity()` reports tampering.

- Train/eval split is derived from `sha256(id)`, not chosen by a human - hand-tagging biases

  the eval set toward whatever felt uninteresting. There is no `split` parameter to pass.

- Eval contamination is blocked structurally by the `training_pool` view

  (`stage='approved' AND split='train'`), not by convention.

- Added `training_runs` / `training_members` so it is possible to answer "which experiences

  went into ALEX-M1" when a later version regresses.

- Added `/good`, `/bad [reason]`, `/experiences`. Until now `/wrong` corrected **routing only**;

  there was no signal about answer quality, which is the signal training actually needs.

- Added `scripts/experiences.py` (status / import-routes / list / review / export).

  `import-routes` absorbs the `/wrong` corrections that were being collected and never used.

- Tests: 299 -> 315.



## Unreleased — 2026-09-18 — claude



- Renamed the project to **Project ALEX** (`PROJECT_ALEX.md`). Code identifiers still say

  `jarvis`; only the name changed, pending a decision on rename scope.

- Measured the generate-run-refine loop (`grade_codegen.py --refine N`) and **decided against

  it**: 7 of 8 runs showed no improvement across two refinement rounds (2.5/7 -> 3.0/7,

  0/8 perfect). ADR-036 is closed as "not adopted", so the app's code-execution boundary

  stays shut. `SANDBOX_DESIGN.md` records the design that was evaluated.

- Added `IDENTITY.md` - ALEX's invariant traits, written as observable behaviours so they

  can be scored, plus the ALEX-M0 baseline.

- Added `scripts/grade_identity.py`, the other half of `PROJECT_ALEX.md` section 8

  ("is it still ALEX?"). Baseline 6.0/7, stable across runs; I-2 fails consistently.

- Fixed the identity scorer: it read 6/7 as 3/7 by checking forbidden phrases before

  expected ones and by patterns that missed Korean verb-ending variation.

  `tests/test_identity_scoring.py` holds the real answers that were misjudged.

- Extended `STUDY_GUIDE.md` with stages 11-14 drawn from this repo's real bugs and decisions.

- Renamed every user-facing surface to ALEX: app banner and `[ALEX]` prompt, `/help` text,

  the default system prompt, launcher window, web UI title and brand, `START_ALEX.vbs`.

  Internal identifiers are deliberately unchanged (`JarvisCore`, `JARVIS_*` env vars,

  `X-Jarvis-Token`, the `jarvis/` package, `jarvis.local.json`, `data/jarvis.db`) - moving

  those needs a data migration and is still the user's call.

- `grade_identity.py` now measures ALEX *with* its system prompt by default (`--bare` for the

  raw model). Measured both: identical 6.0/7. I-2 fails 0/3 in both even though the system

  prompt explicitly forbids it - prompting does not reach that behaviour.

- Tests: 293 -> 299.



## Unreleased — 2026-09-14 — claude



- Fixed workspace backup overwrite: `apply()` and `restore_latest()` both built the backup

  folder name from `datetime.now().strftime(...%f)`, and Windows' ~15ms clock granularity gave

  them the same name, so an undo silently overwrote the backup it was undoing. Undo history

  could be lost. Backup stamps are now strictly monotonic (`_next_backup_stamp()`).

  This is the same root cause as the memory-ordering fix below, in a second place.

- Added `scripts/grade_codegen.py`: the code-generation grading harness behind

  `PERFORMANCE.md` section 8. It previously existed only as prose, so the benchmark could not

  be reproduced or extended without rewriting it. Requires an explicit `--execute`.

- Tests: 291 -> 293.



## Unreleased — 2026-09-13 — claude



- Fixed memory recency ordering: `remember()` gave rapid writes identical `updated_at` on

  Windows (~15ms clock granularity), so `ORDER BY updated_at DESC` silently returned the

  oldest memory first. Timestamps are now strictly monotonic. No schema change.

- Added configurable `num_ctx` (`JARVIS_NUM_CTX` / `jarvis.local.json` / Web settings).

  `0` keeps the existing per-profile automatic value.

- Turned `ollama_think` off on the RTX 4070 Laptop after measurement (3.4x faster, higher score).

- Added `PERFORMANCE.md` with measured VRAM limits, RAM-offload cost and thinking-mode comparison.

- Added `CODING_SYSTEM_ADDENDUM`, applied only on coder routes (`LOCAL_CODER`/`CLOUD_CODER`)

  and on `/edit-file` proposals. Measured: generated-calculator score 1.7/7 -> 3.7/7 on qwen3:8b.

- Tuned this laptop to qwen3:8b + num_ctx 8192 + num_predict 3072 after measurement.

- Tests: 281 -> 291.



## v0.2.0 — 2026-09-13 — GPT



- Added approval-based Workspace Edit proposal/diff/apply/backup/undo.

- Added SQLite Task state and persisted active project sub-root.

- Added portable Memory+Task JSON export/import for device transfer.

- Added `/status` and Beta smoke verification.

- Added opt-in fixed-target Wake-on-LAN and fixed-argv VS Code launcher.

- Added AST validation for Python edit proposals.

- Schema version advanced to 4.

- Preserved Local-first, Cloud approval/DLP, localhost Web, stdlib runtime boundaries.



# CHANGELOG



## v0.1.3 — 2026-09-13 — GPT



### Local usability

- `python -m jarvis` entry point and `pyproject.toml` console scripts.

- Local Web chat Alpha on loopback only with per-process POST token, local Host allow-list (DNS-rebinding defense), and security headers.

- CLI streaming callback support.

- `/project`, `/tree`, `/grep`, `/ask-file`, `/ask-project`, `/ask-local`, `/ask-cloud`, `/route`.

- Session rename/delete and expanded `/doctor` database stats.

- Rotating file logs.



### Local model/profile policy

- Laptop/UNKNOWN default General -> `qwen3:8b-q4_K_M`; single-model routing by default.

- Desktop auto profile -> `qwen3:14b-q4_K_M` + `qwen2.5-coder:14b`.

- Ollama `think`, `num_predict`, `keep_alive` controls.

- WSL `nvidia-smi` path support and largest-VRAM GPU selection.

- Model setup dry-run/pull script and richer local benchmark metrics.



### Safe project context

- Bounded project tree and lexical multi-file retrieval.

- Project-content cache with invalidation on file metadata change.

- Automatic context skips credential/secret/token/key-like filenames.

- `/ask-file` and `/ask-project` are Local-only; source context never takes Cloud fallback.



### Cloud/privacy

- Optional OpenAI Responses, Anthropic Messages and Gemini generateContent stdlib adapters.

- Default Cloud remains Stub.

- Provider/destination-specific approval; denial blocks remaining Cloud candidates for that request.

- Persistent Memory and Rolling Summary excluded from Cloud payload by default.

- Sensitive token/key patterns hard-block Cloud by default and redact feedback/log previews.

- Cloud HTTP error response bodies are not reflected into user-visible/logged ProviderError messages.

- Remote Ollama blocked unless explicitly enabled.



### Storage/recovery

- SQLite WAL, busy timeout and schema version metadata.

- Existing DB migration creates an online pre-migration backup before altering schema.

- Session titles and safe cascading session deletion.

- Memory key/value bounds.



### Verification/release

- Router Golden Corpus regression suite.

- Security smoke and offline Alpha E2E smoke.

- Dependency audit (stdlib/local imports only).

- `verify_release.py` reproducible gate.

- Clean package builder now runs verification before packaging, skips symlinks/sensitive env-key files, audits ZIP/TAR contents, re-verifies the extracted clean ZIP, and emits SHA-256 checksums.

- `.gitignore` now excludes all runtime `data/`, `logs/`, `dist/` plus common local secret/key files.



## v0.1.2 — 2026-09-13 — GPT



- Fixed SQLite connection close leak discovered after Claude v0.1.1.

- Exact Ollama tag health check + TTL cache.

- Cloud explicit approval gate.

- Local-only rolling conversation summary + migration.

- Router event persistence and `/wrong` feedback.

- Safe explicit file reader.

- `/doctor`, `/backup`, `/summary`, `/use`, `/export`.

- Local backup/export and real-machine environment/benchmark scripts.

- Context char budget, lexical Memory relevance, Laptop single-model default and remote Ollama block.



## v0.1.1 — 2026-09-13 — Claude



- Ollama Local Provider.

- Provider fallback chain.

- Safe file-name search.

- Config validation and failure messages.

- 88-test baseline.



## v0.1 — 2026-09-13 — GPT



- Mock Local Personal AI Skeleton with Router, Hardware Profile, Session/Memory, CLI and initial tests.

