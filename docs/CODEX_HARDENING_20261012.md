# ALEX 대기열·설치 패키지 보완 — 2026-10-12

GitHub 공개본 `4a39bf4aeb77e07bd87ead5dd5af32de834ede0f`를 확인해 두 가지 문제를 수정했다. 사용자 PC를 원격으로 조작하거나 모델·대화 DB·개인 설정을 옮기지 않았다.

## 수정 내용

### 대기 요청이 실행 중 요청의 중단 신호를 지우던 문제

이전 `web._stream`은 모델 잠금을 얻기 전에 콜백·명령 취소 상태·모델 목록을 읽고, 요청 종료 시 모든 모델의 `cancel_event`를 `None`으로 바꿨다.

- A가 응답 중 B가 대기 취소·연결 종료·헤더 전송 오류로 끝나면 A 모델의 중단 신호가 해제됐다.
- A가 끝나고 B가 처리된 뒤에는 B가 대기 시작 때 읽은 A의 콜백이 다시 연결됐다.
- 대기 중 엔진·모델이 바뀌면 B는 현재 모델 대신 대기 시작 당시 모델에 중단 신호를 연결했다.

이제 모델 잠금을 얻은 뒤 현재 콜백·명령 취소 신호·모델 목록·모델별 기존 취소 신호를 저장한다. 잠금을 얻지 못한 요청은 공유 실행 상태를 바꾸지 않는다. 자신이 변경한 상태만 잠금 해제 전에 원래 값으로 복원한다.

`tests/test_web_stream_lifecycle.py`에 회귀 테스트 5개를 추가했다. 취소된 대기 요청, 헤더 실패, 연결 종료, 대기 중 모델 교체·콜백 복원, 실행 오류·생성 취소의 기존 상태 복원을 검사한다. 새 검사는 수정 전 실패했고, 수정 후 기존 검사와 함께 7개 모두 통과했다.

### wheel에 PWA 파일이 없던 문제

기존 `pyproject.toml`은 HTML·CSS·JS만 package-data로 지정해 `manifest.webmanifest`, `icon-192.png`, `icon-512.png`가 wheel에서 빠졌다. 해당 확장자를 포함하도록 수정했다.

격리한 소스 사본에서 wheel을 빌드하고 별도 경로에 설치했다. 설치 패키지의 정적 파일 8개가 소스와 바이트 단위로 같으며, 설치한 패키지의 서버가 아래 경로를 HTTP 200과 올바른 Content-Type으로 제공하는지 확인했다.

| 경로 | MIME |
|---|---|
| `/manifest.webmanifest` | `application/manifest+json` |
| `/sw.js` | `text/javascript; charset=utf-8` |
| `/icon-192.png` | `image/png` |
| `/icon-512.png` | `image/png` |

README·TEST_RESULTS에 최신 검사 수를 반영하고, 기존 TODO는 과거 계획이라고 표시했다.

## 검증

환경: Linux 컨테이너, Python 3.12.14. 전체 검사는 파일 단위 8 worker, 임시 DB와 가짜 모델·도구 서버 사용.

```text
python3 scripts/verify_release.py --test-workers 8

Ran 1323 tests in 51.928s
OK (skipped=5)
Python 3.10 syntax compatibility audit PASS (160 files parsed)
Dependency audit PASS
Router golden corpus: 83/83
Security smoke PASS (13/13)
compileall / alpha / beta: PASS
RELEASE VERIFICATION: PASS
```

- 실제로 통과한 테스트 1,318개, 건너뜀 5개, 실패 0개.
- 스트리밍 회귀 7/7, 사용자 분리·프로젝트 HTTP 15/15 별도 통과.
- 수정 전 회귀: 7개 중 기존 2개는 통과, 새 5개는 실패(오류·생성 취소 subTest를 포함해 실패 항목 6개).
- 설치 wheel: 정적 파일 8개 바이트 일치, PWA 경로 4개 HTTP 200·MIME 확인.
- `git diff --check` 통과.

이 결과는 공개본 기준이다. 개발 PC의 별도 pygame 게임을 포함한 검사와 구별한다. Python 3.10 문법 감사는 실제 Python 3.10 실행 검증과 다르다. 실GPU/실LLM·Windows·Tailscale 접속·사용자 PC의 실제 데이터는 이번에 재검증하지 않았다.

## 백업·PC 업데이트·롤백

수정 전 커밋은 [GitHub 백업 브랜치](https://github.com/heeschoi501/ALEX/tree/backup/pre-hardening-20261012-4a39bf4)에 보존했다. 동일 시점의 전체 Git 이력을 담은 `ALEX_before_fix_20261012_4a39bf4.bundle`도 만들고 `git bundle verify`로 확인했다.

PC의 ALEX 폴더가 이 Git 저장소에 연결돼 있고, 현재 `main` 브랜치일 때 다음 명령으로 코드를 내려받은 뒤 ALEX를 재시작한다.

```bash
git pull --ff-only origin main
```

코드를 수정 전 버전으로 돌려 확인하려면 ALEX를 종료하고 다음을 실행한 뒤 재시작한다.

```bash
git fetch origin
git switch --detach origin/backup/pre-hardening-20261012-4a39bf4
```

수정본으로 돌아가려면 다음을 실행하고 재시작한다.

```bash
git switch main
```

위 절차는 코드 버전을 전환한다. 무시된 `data/`, 모델, `jarvis.local.json`을 삭제하지 않는다. Git이 로컬 수정과 충돌한다고 알리면 강제 전환하지 않고 그 수정부터 보존한다. 이번 수정은 DB 스키마를 바꾸지 않는다.

## 남은 실사용 확인

1. 실제 PC에서 A가 생성 중일 때 B 대기 취소·연결 종료가 A의 생성 중단 기능에 영향을 주지 않는지 확인.
2. Windows에서 업데이트 후 정상 시작·프로젝트 자료 범위·자동 제목 확인.
3. Tailscale Serve의 실제 전달 헤더·계정 연결·모바일/PWA 설치 확인. 계정 관리 메뉴는 기존 설정대로 숨겨져 있다.
