# ALEX 채팅방 이동 중 대화·코드 복사 — 구현·복원·인수인계

작성: Codex, 2026-10-11 (KST). 적용 전 기준: GPT_NEXT.md 23:15, ADR-092, 공개본 4a39bf4.
사용자 요청: 답을 기다리는 동안 다른 방으로 이동·전송, 방별 초안·중단·완료 표시, 롤백과 인계,
GPT처럼 코드 블록 전체를 바로 복사.

## 변경 파일

- `jarvis/web.py`: Ticket.session_id, 방별 중복 거부, 사람별 최대 4개 요청, FIFO 순차 실행, 선택한 방 유지,
  사람+방별 직전 문답/라우팅 상태, 요청 실행 직전 계정 상태/권한 재조회, 대기 요청의 취소 정리,
  실행/대기 방 삭제 보호, 대기 중인 빈 방을 새 대화로 재사용하지 않음.
- `jarvis/static/app.js`: roomRequests/roomDrafts/unreadRooms, 화면 선택과 작업 분리, 진행 노드 보존,
  초안·첨부 보존, 방별 중단, 완료 후 화면 유지, 목록 상태 표시, 늦게 돌아온 화면 응답 무시,
  다른 탭의 처리 상태 확인, 프로젝트 초안/이동, 첨부 이미지 URL의 원래 방 고정,
  최대 20개 방의 화면 노드 캐시(명령 결과·diff 승인 버튼·완료 시간·코드 복사 버튼 유지).
- `jarvis/static/markdown.js`: 코드 블록마다 언어 표시와 ‘코드 전체 복사’, 성공 표시,
  Clipboard API 실패 시 브라우저 복사 fallback, 모두 거부되면 코드 선택/수동 복사 안내.
- `jarvis/static/style.css`: 생성 중/대기 중/새 답변 표시, 코드 블록 도구줄·복사 버튼.
- `tests/test_room_chat.py`: 회귀 검사 5개.

## 동작과 책임

1. `/api/chat/stream` 접수 시 소유권을 확인하고 방 ID를 고정한다. 같은 방의 실행/대기 요청이 있으면 409.
   같은 사람도 서로 다른 방에서는 요청 가능하다. 모델·GPU 작업은 기존 state.lock/FIFO로 한 번에 하나만 실행한다.
2. 사용자당 진행/대기 스트림은 최대 4개. HTTP/1.1 브라우저 연결이 긴 스트림으로 모두 차서
   화면 이동과 취소 요청까지 막히는 상황을 피하기 위한 제한이다. 병렬 모델 생성 설정은 변경하지 않았다.
3. 상태 응답의 `queue.requests`는 해당 사용자 요청만 포함한다:
   `{request_id, session_id, status: running|queued, position}`.
   `request_id`와 `queue.position`은 현재 표시 중인 방 기준이다. `busy`는 서버 전체 작업 유무를 계속 나타낸다.
4. 스트림 `start`에 방 ID, 새 `running` 사건을 추가했다. `done.last_message_id`는 저장된 마지막 메시지 ID다.
   화면 캐시가 저장된 이력과 같은지 판단할 때 사용한다. 기존 `queue/chunk/done/title` 필드는 유지한다.
   기존 UI가 모르는 `running` 사건을 무시해도 기본 생성은 가능하다. 새 UI 기능은 서버 재시작 후 사용한다.
5. 스트림 콜백과 provider.cancel_event는 실행 차례를 얻은 요청만 바꾸고 정리한다.
   대기 중인 요청 취소/연결 끊김이 실행 중인 다른 요청의 콜백을 지우던 문제를 수정했다.
6. 직전 답변(/bad·교정·그림 수정)은 사람+방 단위로 분리한다. 같은 사람이 여러 방을 오갈 때
   마지막으로 처리한 다른 방의 답을 평가하거나 참조하지 않게 한다.
7. 종료·모델 설정·작업 폴더 변경·모델 다운로드는 다른 방의 요청도 끝나야 진행한다.
   파일 수정의 기존 사용자 확인을 유지한다. 프로젝트 삭제는 이 사람의 요청이 끝난 뒤 가능하다.
8. DB 스키마, 저장된 대화/자료, jarvis.local.json, 모델 파일, Strata 설정, 실제 프롬프트 생성 경로는 변경하지 않았다.
9. 코드 복사는 해당 `<code>`의 텍스트만 복사한다. 설명·언어 표시·Markdown fence는 포함하지 않는다.
   들여쓰기·빈 줄·한글·후행 공백을 유지한다(기존 렌더러의 CRLF→LF 정규화 유지).
   줄 안의 코드 표시는 블록 복사 버튼 대상이 아니다. 기존 답변 전체 ‘복사’ 버튼도 유지한다.
10. 이번 검증에서 완료 이벤트의 메시지 번호를 `StoredMessage.id`로 읽던 오류를 발견했다.
    `StoredMessage`에는 ID가 없으므로 기존 `Storage.message_page(limit=1)`를 재사용해 수정했다.
    회귀 검사에서 `done.last_message_id`가 실제 저장된 마지막 ID인지 확인한다.

## 검증

- `PYTHONPATH=tests:. python3 -m unittest test_room_chat test_multiuser test_web_stream_lifecycle test_web -q`:
  관련 36개 통과. 임시 DB와 mock provider, loopback HTTP만 사용. 전체 release suite는 이번에 실행하지 않았다.
- 별도 headless Edge 프로필·임시 DB·느린 mock provider로 27항목 통과:
  초안 복원, 생성 중 이동, 다른 방 전송/대기, 진행 답변 복원, 방별 취소, 다른 방 작업 유지,
  완료 시 화면 유지, 답변 섞이지 않음, 새 답변 표시/해제, 생성 중 적은 다음 질문 보존,
  빠른 연속 이동, 프로젝트 초안, 프로젝트/현재 방 삭제 후 유효한 방 선택, 새 방 생성,
  스트림 정상 완료와 완료 정보 유지, 명령 결과 노드 보존, 블록별 복사·줄바꿈·공백 보존,
  Clipboard API 미지원/거부 fallback 및 수동 선택, 방 복귀 후 코드 복사 버튼 유지.
- Python AST/JavaScript 컴파일로 문법 확인. 실제 모델 추가 추론·학습·다운로드 없음.
- 근거: `logs/codex-room-chat-20261011/backend-check.txt`, `browser-result.json`, `rooms-queued.png`,
  `rooms-completed.png`, `verify_browser.py`. 시험 도중 실사용 DB 변경 0건.
- `rollback.py` 기본 실행으로 원본·수정본 SHA 검증 통과(`rollback-preview.txt`). 실제 롤백은 실행하지 않았다.

## 실제 적용

적용 완료: 2026-10-11 23:57 KST. 정상 종료·런처 재시작 후 live/disk 코드 `2ff916394e224922` 일치, idle 확인.
Strata PID 24948·20680 유지. 종료 응답에서 Ollama preserved 확인. 근거: `deployment.json`, `restart-final.txt`.
재시작 전 `live_status.py`로 idle(생성/대기 0) 확인. `logs/probe/restart_alex.py`의 정식 종료/런처 경로만 사용한다.
사용자 화면은 적용 후 F5가 필요하다(프로세스별 토큰). Strata/Ollama는 별도 서비스로 유지한다.

## 롤백

폴더: `logs/codex-room-chat-20261011/`. 원본 `before/`, 전후 SHA `manifest.json`.

Windows 터미널에서 프로젝트 루트로 이동한 뒤, 현재 프로젝트용 Windows Python으로:

```text
python logs\codex-room-chat-20261011\rollback.py
python logs\codex-room-chat-20261011\rollback.py --apply
```

- 기본은 파일 SHA 검증과 복원 계획만 출력한다. `--apply`만 실제 복원한다.
- 다른 후속 코드 변경이 있으면 자동 복원을 거절한다. 해당 변경을 백업하고 수동 병합해야 한다.
- 진행/대기 요청이 있으면 종료/복원을 거절한다. 정상 종료 확인 후 현재 파일을 `after-rollback-시간/`에 보관한다.
- 제품 파일 4개를 복원하고 이번 새 회귀 검사 파일을 제거한다. DB·설정·모델·인계 문서는 보존한다.
- 복원 후 START_ALEX.vbs로 켜고 F5. DB v7은 그대로이며 이번 변경은 데이터 마이그레이션이 없다.

## 한계와 다음 작업자 주의

- 방 이동은 스트림을 끊지 않는다. F5·탭 종료·네트워크 끊김·앱 재시작 뒤의 생성 지속/재연결은 보장하지 않는다.
  기존 연결 끊김 시 취소 동작을 유지했다. 생성 지속이 필요하면 서버 작업 저장/재연결을 별도 설계할 것.
- 입력 초안·새 답변 표시와 임시 진행 노드는 현재 브라우저 탭 메모리다. 새로고침 후 초안/읽음 상태는 사라진다.
  완료된 일반 대화는 기존 DB에 저장된다. 명령 결과는 최근 20개 방의 화면 캐시에 남지만,
  기존 DB 영속화 범위는 넓히지 않았다. 다른 클라이언트가 새 메시지를 저장하면 캐시 대신 DB 이력을 읽는다.
- 직전 문답은 방별로 나눴지만 교훈 경험 저장소의 개인/공용 분리, 지식 포함 전체 백업,
  프로젝트 자료 명령 범위, 자동 제목의 수동 변경 덮어쓰기, PWA 패키지 데이터 누락은 앞선 검토의 남은 과제다.
- Strata의 생각만 하고 content 없이 종료한 문제는 이번 변경으로 해결됐다고 주장하지 말 것.
- 실모델 여러 방 연속 사용/휴대폰/노트북은 이번에 추가 실측하지 않았다.
- 공개 사본/GitHub는 이번 변경을 게시하지 않았다. 사용자 요청이 있을 때 제품 파일 4개+새 검사를 동기화할 것.
- 기존 Claude 화면 작업의 롤백(ADR-092)과 이번 롤백은 별개다. 후속 파일 변경 여부를 확인하고 쓸 것.
