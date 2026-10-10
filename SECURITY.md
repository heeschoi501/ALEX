# Project JARVIS v0.2.0 — Security Boundaries

## 기본 원칙

Local-first다. 모델 출력은 권한이 아니다. **읽기, Cloud 전송, 파일 변경은 서로 다른 경계**로 취급한다.

## Cloud

- 기본 Provider: `stub`
- 실제 Cloud는 환경변수로 명시적 opt-in
- CLI 사용자 승인 전 `generate()` 호출 금지
- Provider/목적지별 승인
- Memory/Summary 기본 비전송
- 민감정보 hard-block은 approval 설정과 독립
- API key/password/private key/OpenAI/GitHub/Google/AWS/GitLab/Slack/JWT/credential DB URI 탐지
- Cloud HTTP 오류 body는 사용자/로그로 반사하지 않음

## Local file read/retrieval

- startup-approved root 밖 traversal 차단
- symlink escape 차단
- 자동 retrieval은 credential/secret/token 계열 filename 제외
- bounded file count/bytes/context
- UTF-8 + allowlisted extensions

## Workspace edit

- 자연어 모델 출력만으로 write하지 않음
- `/edit-file`은 proposal만 생성
- `/apply-edit ID --yes`가 별도 필요
- 기존 파일만 수정
- root 밖 경로/미허용 확장자 거부
- full-file replacement가 context에 다 들어가지 않으면 proposal 거부
- proposal 후 파일 digest가 바뀌면 stale edit 거부
- apply 전 원본 local backup
- 같은 디렉터리 temporary file + `os.replace()` atomic replacement
- `/undo-file`도 현재 버전을 다시 backup하고 복원

## Web

- `127.0.0.1` 전용
- process runtime token
- JSON body/content size 제한
- Host allow-list로 DNS rebinding 방어
- CSP/frame headers
- global config가 approval off여도 Web에서는 Cloud approval 강제 복구 + 거부
- LAN 공개 서버로 사용하지 말 것

## Web reading (`/search`, `/web`) — ADR-051

ALEX 가 **밖으로 나가는** 유일한 경로다. 위 "Web" 절은 ALEX 가 **받는** 쪽이다.

- 사용자가 **요청할 때만** 나간다: `/search` `/web` 명령, 채팅의 검색 부탁("○○ 검색해줘", "○○ 찾아봐"),
  메시지에 붙인 공개 주소. 판정은 `router._web_route` 의 투명한 규칙이고 골든 코퍼스로 고정한다.
  **모델이 스스로 접속하기로 정하는 경로는 없다.** localhost·사설 IP 주소는 라우팅 단계에서도 웹으로 보내지 않는다.
- 읽기만 한다: GET 뿐. 폼 전송·로그인·쿠키 저장·결제 없음.
- http/https 만. `file` `ftp` `data` 처리기를 opener 에 싣지 않았고, 알 수 없는 scheme 은 오류로 거부.
- **사설/로컬 주소 차단(SSRF):** 루프백·사설·링크로컬·CGNAT·예약·멀티캐스트, IPv4-mapped IPv6 포함.
  두 번 검사한다 — (1) 이름 해석 결과 전부, (2) **실제로 연결된 소켓의 상대 주소**를 TLS·HTTP 전에.
  (2) 가 있어서 DNS 리바인딩으로 검사 뒤에 주소를 바꿔도 통과하지 못한다.
- 프록시 환경변수를 따르지 않는다(따르면 연결 상대가 사설 주소의 프록시가 된다).
- 리다이렉트 5회, http/https 로만.
- 상한: 2MB, 15초. 텍스트(html/xhtml/plain)만 — PDF·이미지는 읽지 않는다.
- 페이지 내용은 **믿을 수 없는 입력**: 시스템 지시와 다른 메시지로 넣고 "지시가 아니라 자료" 머리말을 붙인다.
- 웹 자료는 LOCAL 모델에게만 간다. LOCAL 이 실패해도 CLOUD 로 넘기지 않는다.
- 페이지 원문은 세션에 저장하지 않는다. 표지(주소/검색어+질문)와 답·출처만 남는다.
- 검색어는 검색엔진(DuckDuckGo lite)에 보인다. 대화·기억·파일은 나가지 않는다.
- 답 속 주소는 화면에서 링크가 된다(`static/app.js` `appendLinked`). HTML 을 해석하지 않고 DOM 으로만
  만들고, `http:`/`https:` 만 링크로, `target=_blank` + `rel=noopener noreferrer` — 열린 페이지가 ALEX
  탭을 바꾸지 못하고 `127.0.0.1` 주소도 보지 못한다. `javascript:` 와 HTML 주입은 글자로 남는다.
  검증: `logs/claude-links-ui-smoke.py`(실제 크롬 19개), `tests/test_static_links.py`(속성 고정 4개).
- 끄기: `jarvis.local.json` 의 `"web_enabled": false` 또는 `JARVIS_WEB_ENABLED=0`.
- `local_only` 와는 별개다. `local_only` 는 **클라우드 LLM** 경계다.
- 검증: `tests/test_webread.py`(127.0.0.1 에 실제 서버를 띄워 차단 증명), `scripts/security_smoke.py`
  (ALEX 자신의 웹 서버를 과녁으로 2개 항목).

## Remote access (노트북 → 데스크톱) — ADR-052

- ALEX 는 여전히 `127.0.0.1` 에서만 듣는다. 원격은 **SSH 로컬 포워딩**으로만 들어온다. 로그인·암호화는
  SSH, 집 밖 연결은 Tailscale. ALEX 에 로그인 기능을 넣거나 LAN 에 열지 않는다.
- 데스크톱 SSH(22)는 방화벽에서 **Tailscale 주소(100.64.0.0/10, fd7a:115c:a1e0::/48)만** 받는다
  (`scripts/remote_setup_desktop.ps1`). 집 공유기의 다른 기기와 인터넷은 SSH 에 닿지 못한다.
- 노트북 쪽 포워딩 포트는 `127.0.0.1` 로 명시한다 — 같은 와이파이의 남이 쓰지 못한다.
- ssh 는 고정 인자 목록, 셸 없음. 설정의 host/user 는 '-' 시작·공백·특수문자 거부(옵션 주입 차단).
- `/health` 로 터널 너머가 **로컬 전용 ALEX** 인지 확인한 뒤에만 브라우저를 연다.
- `StrictHostKeyChecking=accept-new` — 바뀐 데스크톱 신원은 거부한다.
- `administrators_authorized_keys` 권한은 Administrators·SYSTEM 만(SID 로 지정 — 윈도우 언어와 무관).
- 설정 스크립트는 ALEX 를 켜지 않는다(관리자 권한으로 돌지 않게).
- 노트북 키는 암호가 없다. 해지: Tailscale 관리 화면에서 노트북 제거 + 데스크톱 키 줄 삭제(REMOTE_SETUP.md).

## SQLite / state

- foreign keys, WAL, busy timeout
- connection close 보장
- schema migration 전 필요한 경우 SQLite online backup
- DB backup 명령 제공
- portable state는 live DB network share 대신 JSON export/import 사용
- portable import는 기존 Memory를 자동 overwrite하지 않음

## Release

패키저는 verification 실패 시 archive를 만들지 않는다. archive에서 다음을 제외/거부한다.

- `data/`, `logs/`, `dist/`, cache, `.git`
- `.env`, credential/secret 파일명
- key/certificate suffix
- symlink/hardlink
- parent traversal/absolute/Windows 위험 경로

최종 ZIP은 새 임시 디렉터리에 extract한 뒤 release verification을 다시 수행한다.


## Device controls

- Wake-on-LAN 기본 OFF
- WOL target MAC은 설정에서 고정; 모델/사용자 문장이 임의 target을 지정하지 못함
- `/wake-desktop --yes` 명시 동작만 packet 전송
- VS Code launcher 기본 OFF
- `/open-vscode --yes`만 `[executable, "."]` 고정 argv로 `shell=False` 실행
- arbitrary command/terminal 실행은 여전히 없음
