"""`STUDY_GUIDE.md` 의 미니 과제를 바탕화면의 학습 폴더로 펼친다.

한 파일에 900 줄이 넘게 쌓이면 "오늘 뭘 풀지" 를 고르는 것부터 일이 된다. 문제 하나당
파일 하나로 펼치고, 답을 쓸 자리와 진행 체크리스트를 같이 만든다.

**원본은 `STUDY_GUIDE.md` 하나뿐이다.** 문제를 새로 만들 때는 거기에 쓰고 이 스크립트를
다시 돌린다. 바탕화면 쪽을 직접 고치면 다음 실행에서 덮인다 — 단, `내_답안/` 은
건드리지 않는다.

사용:
  python scripts/build_study_pack.py              # 어디에 무엇을 쓸지만 보여준다
  python scripts/build_study_pack.py --write
  python scripts/build_study_pack.py --write --dest "D:/study"
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass

# ---------------------------------------------------------------------------
# 문제별 파이썬 뼈대. 별도 모듈로 두었더니 dependency_audit 가 외부 import 로 잡았다.
# 그 규칙이 맞다 — 이 저장소는 표준 라이브러리만 쓴다. 그래서 한 파일에 둔다.
# ---------------------------------------------------------------------------
HEADER = '''"""문제 {n} — {title}

{body}
"""
import sys
from pathlib import Path

PROBLEM_NUMBER = {n}
PROBLEM_TITLE = {title!r}
PROBLEM_STATEMENT = """{body}"""

# 저장소 코드를 불러올 수 있게 경로를 잡는다.
REPO = Path(r"{repo}")
if REPO.is_dir():
    sys.path.insert(0, str(REPO))


def need_repo(module: str):
    """저장소 모듈을 불러온다. 실패하면 traceback 대신 무엇을 하면 되는지 알려준다."""
    try:
        return __import__(module, fromlist=["*"])
    except ImportError:
        print("저장소 코드를 불러오지 못했습니다.")
        print(f"  찾은 경로: {{REPO}}  (존재: {{REPO.is_dir()}})")
        print("  이 문제는 Windows 파이썬으로 돌려야 합니다:")
        print("  Windows 파이썬으로 돌리십시오 (WSL 파이썬은 저장소 경로를 못 봅니다).")
        sys.exit(0)

'''

CHECKABLE: dict[int, str] = {
    1: '''
route_request = need_repo("jarvis.router").route_request


def count_destinations(sentences):
    """문장 리스트 -> {목적지 이름: 개수} dict.

    힌트: route_request(문장) 이 RouteDecision 을 돌려주고 .destination 에 목적지가 있다.
    """
    # TODO: 여기를 채운다
    raise NotImplementedError


def check():
    sentences = ["파이썬 리스트 정렬하는 코드 짜줘", "오늘 기분 어때?", "이 함수 버그 고쳐줘"]
    got = count_destinations(sentences)
    assert isinstance(got, dict), f"dict 를 돌려줘야 한다. 받은 것: {type(got).__name__}"
    assert sum(got.values()) == len(sentences), f"합이 {len(sentences)} 여야 한다: {got}"
    assert all(isinstance(v, int) for v in got.values()), f"값은 정수여야 한다: {got}"
    print("PASS", got)
''',
    2: '''
def memory_pass(expected: dict, actual: dict) -> bool:
    """expected 의 모든 key/value 가 actual 에 그대로 있으면 True.

    actual 에 값이 더 있는 것은 상관없다. 빠지거나 다르면 False 다.
    """
    # TODO
    raise NotImplementedError


def check():
    assert memory_pass({"a": "1"}, {"a": "1", "b": "2"}) is True
    assert memory_pass({"a": "1"}, {"a": "2"}) is False
    assert memory_pass({"a": "1"}, {}) is False
    assert memory_pass({}, {"a": "1"}) is True, "빈 기대값은 항상 통과다"
    print("PASS")
''',
    5: '''
def choose_profile(ram_gb: float, vram_gb: float, gpu_name: str = "") -> str:
    """RAM/VRAM 으로 LAPTOP / DESKTOP / UNKNOWN 중 하나를 돌려주는 순수 함수.

    기준은 jarvis/hardware.py 의 choose_profile 을 읽고 정한다.
    **먼저 읽지 말고 예상부터 해보라.** 그다음 읽고 맞춰라.
    """
    # TODO
    raise NotImplementedError


def check():
    assert choose_profile(96, 16, "RTX 5080") == "DESKTOP"
    assert choose_profile(32, 8, "RTX 4070 Laptop GPU") == "LAPTOP"
    assert choose_profile(8, 0, "") == "UNKNOWN", "GPU 가 없으면 UNKNOWN 이다"
    assert choose_profile(64, 4, "") == "UNKNOWN", "RAM 만 커도 VRAM 이 작으면 안 된다"
    print("PASS")
''',
    23: '''
from math import comb   # noqa: E402


def one_sided_p(successes_b: int, n_b: int, successes_a: int, n_a: int) -> float:
    """B 가 A 보다 나을 확률의 단측 p 값(초기하 분포).

    2026-09-19 측정: A 12/20, B 18/20 -> p = 0.032
                     A  7/10, B 10/10 -> p = 0.105
    """
    # TODO: 전체 N, 전체 성공 K 를 구하고 P(X >= successes_b) 를 더한다
    raise NotImplementedError


def check():
    p20 = one_sided_p(18, 20, 12, 20)
    p10 = one_sided_p(10, 10, 7, 10)
    assert abs(p20 - 0.032) < 0.002, f"n=20 에서 0.032 가 나와야 한다: {p20}"
    assert abs(p10 - 0.105) < 0.002, f"n=10 에서 0.105 가 나와야 한다: {p10}"
    assert p10 > p20, "표본이 작을수록 p 가 크다"
    print(f"PASS  n=20 -> {p20:.4f},  n=10 -> {p10:.4f}")
    print("생각할 것: n=10 의 0.105 는 '같다' 는 뜻인가?")
''',
}

OBSERVE: dict[int, str] = {
    3: '''
from collections import Counter   # noqa: E402


def count_routes(log_path: Path) -> Counter:
    """로그에서 route= 값을 세어 Counter 로 돌려준다."""
    # TODO
    raise NotImplementedError


def check():
    candidates = [REPO / "logs" / "misroute.jsonl", REPO / "logs"]
    print("로그 위치 후보:", [str(c) for c in candidates])
    print("실제 파일을 찾아 경로를 넣고 돌려보라. 파일이 없으면 그것도 답이다 —")
    print("왜 없는지, 무엇이 로그를 쓰는지 찾아보라.")
''',
    9: '''
from datetime import datetime   # noqa: E402


def distinct_now_values(times: int = 1000) -> int:
    """datetime.now() 를 times 번 불러 서로 다른 값이 몇 개인지 센다."""
    # TODO
    raise NotImplementedError


def check():
    n = distinct_now_values(1000)
    print(f"1000번 중 서로 다른 값: {n}개")
    print()
    print("이 숫자가 1000 보다 한참 작다면, datetime.now() 를 정렬 키나 파일 이름으로")
    print("쓰는 코드는 동점 때문에 순서가 뒤집힐 수 있다는 뜻이다.")
    print("이 저장소에서 같은 뿌리의 버그가 세 번 났다:")
    print("  - storage 메모 정렬  - workspace 백업 파일명  - 세션 정렬(2026-09-21)")
    print("WSL 파이썬에서도 돌려서 숫자를 비교해 보라.")
''',
}

# 번호를 손으로 관리하면 새 문제를 넣을 때마다 빠뜨린다 — 실제로 27 을 빠뜨렸다.
# 제목이 "(코드 없음)" 이라고 말하면 그 말을 따른다.
NO_CODE = {17, 20}


def _is_discussion(number: int, title: str) -> bool:
    return number in NO_CODE or "코드 없음" in title

DEFAULT = '''
def explore():
    """이 문제는 정해진 정답이 없다. 직접 돌려보고 관찰한 것을 적는다."""
    # TODO: 문제의 지시대로 확인해 보라
    raise NotImplementedError


def check():
    explore()
'''

FOOTER = '''

def _my_code() -> str:
    """이 파일에서 내가 쓴 부분만 뽑는다. 문제 설명과 채점 코드는 뺀다."""
    text = Path(__file__).read_text(encoding="utf-8")
    body = text.split('"""', 2)[-1]
    return body.split("def check(")[0].strip()


def _record(passed: bool, message: str) -> None:
    """시도를 경험 저장소에 남긴다. 판정은 파이썬이 실제로 실행해서 정한다.
    끄려면 JARVIS_STUDY_RECORD=0."""
    try:
        from jarvis.study import record_attempt
    except ImportError:
        return
    saved = record_attempt(number=PROBLEM_NUMBER, title=PROBLEM_TITLE,
                           statement=PROBLEM_STATEMENT, code=_my_code(),
                           passed=passed, message=message)
    if saved:
        print(f"  (이 시도를 경험으로 남겼습니다: {saved[:12]})")


if __name__ == "__main__":
    try:
        check()
        _record(True, "")
    except NotImplementedError:
        print("아직 TODO 가 비어 있다. 함수를 채우고 다시 돌려라.")
    except AssertionError as exc:
        print("FAIL:", exc)
        _record(False, str(exc))
    except Exception as exc:                      # noqa: BLE001
        print(f"오류: {type(exc).__name__}: {exc}")
        _record(False, f"{type(exc).__name__}: {exc}")
'''


def stub_for(number: int, title: str, body: str, repo: str) -> str | None:
    """문제 번호에 맞는 .py 내용. 코드 없는 문제는 None."""
    if _is_discussion(number, title):
        return None
    head = HEADER.format(n=number, title=title, body=body.strip(), repo=repo)
    middle = CHECKABLE.get(number) or OBSERVE.get(number) or DEFAULT
    return head + middle + FOOTER


GUIDE = ROOT / "STUDY_GUIDE.md"
HEADING = re.compile(r"^### 문제\s*(\d+)\s*[—\-–]\s*(.+?)\s*$", re.MULTILINE)
UNSAFE = re.compile(r'[<>:"/\\|?*]')


def desktop_dir() -> Path:
    """OneDrive 로 옮겨진 바탕화면도 찾는다. 못 찾으면 홈으로 떨어진다."""
    home = Path(os.path.expanduser("~"))
    for candidate in (home / "OneDrive" / "바탕 화면", home / "OneDrive" / "Desktop",
                      home / "바탕 화면", home / "Desktop"):
        if candidate.is_dir():
            return candidate
    return home


def slug(title: str) -> str:
    cleaned = UNSAFE.sub("", title).strip().replace(" ", "_")
    return cleaned[:60] or "문제"


def parse_problems(text: str) -> list[dict]:
    """`### 문제 N — 제목` 부터 다음 `###`/`##` 직전까지를 한 문제로 본다."""
    problems: list[dict] = []
    matches = list(HEADING.finditer(text))
    for index, match in enumerate(matches):
        start = match.end()
        end = len(text)
        for boundary in re.finditer(r"^#{2,3} ", text[start:], re.MULTILINE):
            end = start + boundary.start()
            break
        if index + 1 < len(matches):
            end = min(end, matches[index + 1].start())
        problems.append({"number": int(match.group(1)), "title": match.group(2).strip(),
                         "body": text[start:end].strip()})
    return problems


def build(dest: Path, problems: list[dict], write: bool) -> None:
    problem_dir = dest / "문제"
    answer_dir = dest / "내_답안"
    reference_dir = dest / "참고"

    lines = ["# STUDY AI", "",
             f"`STUDY_GUIDE.md` 에서 뽑은 미니 과제 {len(problems)}개다.", "",
             "## 쓰는 법", "",
             "1. `문제/` 에서 `.md` 로 문제를 읽는다.",
             "2. 같은 이름의 `.py` 가 있으면 **그 파일의 TODO 를 채우고 직접 돌린다.**",
             "   ```",
             "   cd 문제",
             "   python 01_Router_카운트.py",
             "   ```",
             "   통과하면 `PASS`, 틀리면 `FAIL: ...` 로 무엇이 틀렸는지 알려준다.",
             "3. `.py` 가 없는 문제는 글로 답한다. `내_답안/` 에 쓴다.",
             "   **이 폴더는 다시 만들어도 지워지지 않는다.**",
             "4. 막히면 `참고/STUDY_GUIDE.md` 의 해당 단계 설명을 본다.", "",
             "> 문제는 전부 **이 저장소에서 실제로 난 버그와 실제로 내린 판단**이다.",
             "> 정답을 맞히는 것보다, 왜 그때 그것을 놓쳤는지를 설명할 수 있는 쪽이 중요하다.", "",
             "## 진행", "",
             "| | 번호 | 제목 | 푸는 법 |", "|---|---|---|---|"]
    for item in problems:
        stem = f"{item['number']:02d}_{slug(item['title'])}"
        has_code = stub_for(item["number"], item["title"], item["body"], str(ROOT)) is not None
        done = "O" if (answer_dir / f"{item['number']:02d}_답.md").exists() else " "
        how = f"`python {stem}.py`" if has_code else "글로 답한다"
        lines.append(f"| [{done}] | {item['number']:02d} | [{item['title']}](문제/{stem}.md) | {how} |")
    lines += ["", "---", "", "이 폴더는 `scripts/build_study_pack.py` 가 만든다.",
              "문제를 새로 만들 때는 저장소의 `STUDY_GUIDE.md` 에 쓰고 그 스크립트를 다시 돌린다."]
    index_text = "\n".join(lines) + "\n"

    print(f"대상 폴더: {dest}")
    print(f"문제 {len(problems)}개 -> {problem_dir}")
    if not write:
        print("\n--write 없이는 아무것도 쓰지 않는다. 미리보기:")
        for item in problems[:5]:
            print(f"  {item['number']:02d}_{slug(item['title'])}.md")
        if len(problems) > 5:
            print(f"  ... 외 {len(problems) - 5}개")
        return

    for directory in (problem_dir, answer_dir, reference_dir):
        directory.mkdir(parents=True, exist_ok=True)

    # 지난 실행이 남긴 문제 파일만 지운다. 답안은 절대 건드리지 않는다.
    for pattern in ("*.md", "*.py"):
        for stale in problem_dir.glob(pattern):
            stale.unlink()

    code_count = 0
    for item in problems:
        stem = f"{item['number']:02d}_{slug(item['title'])}"
        stub = stub_for(item["number"], item["title"], item["body"], str(ROOT))
        where = (f"`{stem}.py` 를 열어 TODO 를 채우고 `python {stem}.py` 로 돌린다."
                 if stub else f"코드 없는 문제다. 답은 `내_답안/{item['number']:02d}_답.md` 에 쓴다.")
        body = (f"# 문제 {item['number']} — {item['title']}\n\n{item['body']}\n\n---\n\n{where}\n")
        (problem_dir / f"{stem}.md").write_text(body, encoding="utf-8")
        if stub:
            (problem_dir / f"{stem}.py").write_text(stub, encoding="utf-8")
            code_count += 1

    (dest / "00_시작하기.md").write_text(index_text, encoding="utf-8")
    guide_note = ("여기 있는 `STUDY_GUIDE.md` 는 저장소의 사본이다. 고쳐도 반영되지 않는다.\n"
                  "원본은 `local ai project/jarvis/STUDY_GUIDE.md` 다.\n")
    (reference_dir / "README.md").write_text(guide_note, encoding="utf-8")
    (reference_dir / "STUDY_GUIDE.md").write_text(GUIDE.read_text(encoding="utf-8"), encoding="utf-8")
    answer_readme = answer_dir / "README.md"
    if not answer_readme.exists():
        answer_readme.write_text(
            "여기에 답을 쓴다. 파일 이름은 `01_답.md` 처럼 문제 번호를 앞에 둔다.\n\n"
            "**이 폴더는 스크립트가 다시 돌아도 지워지지 않는다.**\n", encoding="utf-8")
    print(f"완료: 문제 {len(problems)}개 (그중 파이썬 뼈대 {code_count}개), 시작 문서, 참고 사본")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--write", action="store_true", help="실제로 파일을 쓴다. 없으면 미리보기만 한다.")
    p.add_argument("--dest", default="", help="기본값은 바탕화면의 'STUDY AI'")
    args = p.parse_args(argv)

    if not GUIDE.exists():
        print(f"STUDY_GUIDE.md 를 찾지 못했습니다: {GUIDE}")
        return 2
    problems = parse_problems(GUIDE.read_text(encoding="utf-8"))
    if not problems:
        print("문제를 하나도 찾지 못했습니다. '### 문제 N — 제목' 형식인지 확인하세요.")
        return 2
    numbers = [item["number"] for item in problems]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    if duplicates:
        print(f"주의: 번호가 겹칩니다 {duplicates} — 뒤의 것이 앞의 파일을 덮습니다.")
    dest = Path(args.dest) if args.dest else desktop_dir() / "STUDY AI"
    build(dest, problems, args.write)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
