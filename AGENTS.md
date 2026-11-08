# ALEX 저장소 — 여기서 일하는 AI(Codex·Claude·ALEX 자신)를 위한 규칙

ALEX 는 사용자 PC 에서 도는 로컬 우선 개인 AI 다. 이 파일은 Codex·Claude Code·OpenCode 와 ALEX 의 코딩 하네스가 저절로 읽는다(ALEX 는 8,000자까지 읽으므로 짧게 둔다). 지금 상태와 남은 일은 **최신 인수인계 [docs/HANDOFF_20261108.md](docs/HANDOFF_20261108.md)** 에 있다 — 새 인수인계를 쓰면 이 줄을 고친다.

## 어디서 시작하나

- 새 작업은 **`main` 최신**에서 가지를 따서 한다. 합쳐진 작업 가지에서 이어 쓰지 않는다.
- 가지·작업 이름은 특정 AI 이름 없이 짓는다(예: `fix/…`, `ci/…`). 공개 저장소이자 포트폴리오다.

## 원칙

사용자가 정했다. 바꾸려면 먼저 사용자에게 묻는다. (ALEX 의 자기 지식이 이 절의 `- **…**` 줄을 읽는다.)

- **흡수:** 바깥 에이전트를 도구로 부르지 않고 필요한 능력을 흡수한다. 흡수한 것은 `docs/upstreams.json` 에 적고 원본 변경을 확인해 필요한 것은 올리고, 아닌 것은 두고, 더 좋으면 변형한다.
- **덩치:** 쓸데없이 덩치만 커지는 것을 경계한다. 다만 '측정 전에는 기능을 넣지 않는다' 는 사용자의 기준이 아니다. 기본 꺼짐 실험과 같은 일의 여러 길은 정한 날까지 재서 정리한다.
- **기억은 지우지 않는다:** 배운 것·보관함·제안 기록에 개수 한도를 두고 오래된 것부터 버리지 않는다. 제한해도 되는 것은 대기 중인 제안 수처럼 사람의 검토량뿐이다.
- **승인:** 모델은 사용자의 프로필·메모·스킬을 바꿀 수 없다. 사람이 `/growth approve` 해야 반영된다.
- **로컬 먼저, 클라우드는 피드백:** ALEX 가 먼저 최대한 스스로 고친다. `/dev-auto`·`/rtl` 은 기본 5회차까지 로컬로 하고, 그래도 못 하면 ALEX 가 클라우드(Claude Code·Codex)에 피드백을 청한다. 클라우드는 읽기만 하고, 고치는 것은 ALEX 가 그 피드백으로 다시 한다(ADR-129).
- **여러 AI 가 함께 쓴다:** 한 AI 의 작업을 다른 AI 가 교차 검토한다. 인수인계와 이 파일을 최신으로 둔다.

## 검사

- 전체: `python scripts/verify_release.py` (단위 시험·ResourceWarning·3.10 문법·의존성·라우터·보안·알파·베타). 웹 화면: `node scripts/verify_web_ui.cjs python` (Playwright 1.63).
- 시험 파일 하나: `python -m unittest discover -s tests -p "test_이름.py"` — 시험끼리 서로 가져다 써서 `python -m unittest tests.test_이름` 은 import 오류가 난다.
- push 하면 GitHub Actions 가 Ubuntu 3.10·3.11·3.13, Windows 3.11, Chromium, gitleaks 로 다시 돌린다. 빨간 CI 는 다음 일보다 먼저 고친다. 시험을 건너뛰거나 끄지 않는다.
- 고칠 때마다 새 시험이 고치기 전에는 실패하고 고친 뒤에는 통과하는지 본다. 거절 시험은 이유까지 확인한다(`assertRaisesRegex`).

## 코드 규칙

- 실행 코드는 표준 라이브러리만 쓴다(`scripts/dependency_audit.py` 가 막는다). 문법은 Python 3.10 까지.
- 줄바꿈은 LF(`.gitattributes`). Windows 에서 고친 파일이 CRLF 로 올라가지 않게 한다. `.cmd`·`.bat`·`.vbs`·`.ps1` 은 그대로 둔다.
- 경로를 비교할 때 한쪽만 `resolve()` 하지 않는다. Windows 짧은 경로(`RUNNER~1`)·링크로 두 번 깨졌다. 포함 여부는 양쪽 다 푼 경로로, 링크 검사는 넘겨받은 루트 아래에서만. Linux 재현: `TMPDIR=<링크 폴더> python scripts/run_suite.py`.
- 시간 한도가 있는 시험은 '멈춤 한도' 로 넉넉히 둔다. 바쁜 Windows 실행기는 평소보다 100배 느릴 때가 있다.
- 주석·문서는 한국어로, 왜 그렇게 했는지와 날짜를 남긴다. 사용자 말은 따옴표로 그대로 옮긴다.
- 바꾼 것은 `CHANGELOG.md`, 설계 결정은 `DECISIONS.md`(ADR 번호), 남은 일은 `TODO.md` 에 적는다.

## 안전 경계

- 모델 주소는 127.0.0.1 만 쓴다. 다른 기기 모델은 SSH 터널(모델 노드, ADR-124), 다른 기기 작업은 SSH 작업 노드(ADR-125)로 — SSH 인자는 고정하고 노드 값은 옵션 주입을 검사한다.
- 밖(무료 모델·클라우드)으로 보내는 복사본은 `jarvis/egress.py` 를 거친다. 비밀 파일·링크·개인 저장소(`data`·`growth` 등)는 빠진다.
- 시험용 가짜 비밀값은 `tests/` 와 `scripts/security_smoke.py` 에만 둔다. 그 밖에서 gitleaks 가 잡으면 진짜 키로 보고 새로 발급받는다.
- 파일 쓰기·폴더 만들기는 시작할 때 승인된 루트(`JARVIS_FILE_SEARCH_ROOT`) 밖으로 나가지 않는다.

## 흡수 원본 점검

- `python -X utf8 scripts/check_upstreams.py`(한 달에 한 번). 종료 코드 1 이면 점검 실패부터 해결한다.
- 바뀐 원본 파일을 읽고 반영 여부와 이유를 `docs/upstreams.json` 의 `history` 에 남긴 뒤 `ref`·`commit`·`checked` 를 옮긴다.
