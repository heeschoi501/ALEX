"""여러 종류의 파이썬 과제로 모델을 채점한다. `grade_codegen.py` 의 일반화다.

`grade_codegen.py` 는 계산기 **한 문제**만 잰다. 그런데 `CODING_SYSTEM_ADDENDUM` 이
바로 그 문제에서 관찰된 실패를 겨냥해 만들어졌다 — 같은 문제로 채점하면 과적합을
성능으로 읽는다. 2026-09-21 까지 "파이썬 코딩 18/20" 이라는 숫자가 그것이었다.

그래서 종류를 섞었다. 파싱 · 자료구조 · 파일 · 수치 · 경계조건. 각 과제는
**숨은 테스트로 채점**한다. 모델에게 테스트를 보여주지 않으므로 답을 외워 맞출 수 없다.

채점은 사람이 하지 않는다. 생성된 코드를 별도 프로세스에서 실행해 assert 를 돌린다.

SECURITY: `grade_codegen.py` 와 같다. 모델이 쓴 코드를 실행하므로 `--execute` 가 있어야
채점한다. 앱은 여전히 생성 코드를 실행하지 않는다.

  python scripts/grade_python.py --dry-run
  python scripts/grade_python.py --execute
  python scripts/grade_python.py --execute --model gpt-oss:20b --think -n 2
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass

from jarvis.app import NUM_CTX_BY_PROFILE, apply_profile_override, effective_local_models
from jarvis.config import load_settings
from jarvis.core import CODING_SYSTEM_ADDENDUM
from jarvis.hardware import detect_hardware

# 과제마다 (이름, 요구사항, 숨은 테스트). 테스트는 모델에게 보여주지 않는다.
TASKS: list[dict] = [
    {
        "name": "parse_log",
        "prompt": (
            "`parse_durations(text)` 함수를 작성하십시오.\n"
            "여러 줄 문자열에서 `elapsed=<숫자>ms` 패턴을 모두 찾아 **밀리초 정수 리스트**로 "
            "나타난 순서대로 돌려줍니다. 숫자는 소수일 수 있고 그때는 반올림합니다.\n"
            "패턴이 없으면 빈 리스트를 돌려줍니다."
        ),
        "tests": '''
assert parse_durations("a elapsed=12ms b") == [12]
assert parse_durations("elapsed=1ms\\nelapsed=2ms") == [1, 2]
assert parse_durations("elapsed=2.6ms") == [3]
assert parse_durations("아무것도 없음") == []
assert parse_durations("") == []
assert parse_durations("elapsed=10ms elapsed=3ms elapsed=7ms") == [10, 3, 7]
''',
    },
    {
        "name": "merge_ranges",
        "prompt": (
            "`merge_ranges(ranges)` 함수를 작성하십시오.\n"
            "`(start, end)` 튜플 리스트를 받아 겹치거나 맞닿는 구간을 합쳐 "
            "**시작값 오름차순 리스트**로 돌려줍니다. 입력은 정렬돼 있지 않을 수 있습니다.\n"
            "`(1,3)` 과 `(3,5)` 처럼 끝이 맞닿으면 합칩니다."
        ),
        "tests": '''
assert merge_ranges([]) == []
assert merge_ranges([(1, 3)]) == [(1, 3)]
assert merge_ranges([(1, 3), (2, 6)]) == [(1, 6)]
assert merge_ranges([(1, 3), (3, 5)]) == [(1, 5)]
assert merge_ranges([(5, 7), (1, 2)]) == [(1, 2), (5, 7)]
assert merge_ranges([(1, 10), (2, 3)]) == [(1, 10)]
assert merge_ranges([(1, 2), (3, 4)]) == [(1, 2), (3, 4)]
''',
    },
    {
        "name": "moving_average",
        "prompt": (
            "`moving_average(values, window)` 함수를 작성하십시오.\n"
            "숫자 리스트의 이동평균을 float 리스트로 돌려줍니다. 길이는 "
            "`len(values) - window + 1` 입니다.\n"
            "`window` 가 1 미만이거나 데이터보다 길면 `ValueError` 를 일으킵니다."
        ),
        "tests": '''
assert moving_average([1, 2, 3], 1) == [1.0, 2.0, 3.0]
assert moving_average([1, 2, 3, 4], 2) == [1.5, 2.5, 3.5]
assert moving_average([1, 1, 1, 1], 4) == [1.0]
try:
    moving_average([1, 2], 3); raise SystemExit("ValueError 가 나야 한다")
except ValueError: pass
try:
    moving_average([1, 2], 0); raise SystemExit("ValueError 가 나야 한다")
except ValueError: pass
''',
    },
    {
        "name": "csv_summary",
        "prompt": (
            "`summarize_csv(path)` 함수를 작성하십시오.\n"
            "헤더가 있는 CSV 파일을 읽어 `{열이름: 평균}` dict 를 돌려줍니다.\n"
            "**숫자로 바꿀 수 있는 열만** 포함합니다. 빈 값은 건너뜁니다.\n"
            "표준 라이브러리만 씁니다(pandas 금지)."
        ),
        "setup": '''
import tempfile, os
_p = os.path.join(tempfile.mkdtemp(), "t.csv")
open(_p, "w", encoding="utf-8").write("name,x,y\\na,1,2.5\\nb,3,3.5\\nc,,4.5\\n")
''',
        "tests": '''
_r = summarize_csv(_p)
assert "name" not in _r, f"문자열 열은 빼야 한다: {_r}"
assert abs(_r["x"] - 2.0) < 1e-9, _r
assert abs(_r["y"] - 3.5) < 1e-9, _r
''',
    },
    {
        "name": "binary_search_bounds",
        "prompt": (
            "`first_ge(sorted_values, target)` 함수를 작성하십시오.\n"
            "오름차순 정렬된 리스트에서 `target` **이상인 첫 원소의 인덱스**를 "
            "이진 탐색으로 찾아 돌려줍니다. 없으면 `len(sorted_values)` 를 돌려줍니다.\n"
            "`bisect` 모듈을 쓰지 말고 직접 구현하십시오."
        ),
        "tests": '''
assert first_ge([], 5) == 0
assert first_ge([1, 3, 5], 0) == 0
assert first_ge([1, 3, 5], 3) == 1
assert first_ge([1, 3, 5], 4) == 2
assert first_ge([1, 3, 5], 6) == 3
assert first_ge([2, 2, 2], 2) == 0
assert first_ge([1] * 1000 + [9], 9) == 1000
''',
    },
    {
        "name": "safe_int_stats",
        "prompt": (
            "`stats(values)` 함수를 작성하십시오.\n"
            "숫자 리스트를 받아 `{'min':..,'max':..,'mean':..,'median':..}` 를 돌려줍니다.\n"
            "median 은 원소가 짝수개면 가운데 두 값의 평균입니다.\n"
            "빈 리스트면 `ValueError` 를 일으킵니다. `statistics` 모듈을 쓰지 마십시오."
        ),
        "tests": '''
_s = stats([3, 1, 2])
assert _s["min"] == 1 and _s["max"] == 3
assert abs(_s["mean"] - 2.0) < 1e-9
assert abs(_s["median"] - 2.0) < 1e-9
_s = stats([1, 2, 3, 4])
assert abs(_s["median"] - 2.5) < 1e-9, _s
try:
    stats([]); raise SystemExit("ValueError 가 나야 한다")
except ValueError: pass
''',
    },
]


# ---------------------------------------------------------------------------
# 어려운 과제. 2026-09-21 에 위 6과제로 qwen3:8b 와 gpt-oss:20b 를 재니 **둘 다 12/12**
# 였다. 변별력이 없는 벤치마크는 아무것도 알려주지 않는다 — 어느 쪽이 나은지 모른 채
# "둘 다 잘한다" 는 결론만 남는다. 그래서 상태·재귀·부동소수·데코레이터처럼 작은 모델이
# 실제로 무너지는 자리를 넣었다.
# ---------------------------------------------------------------------------
HARD_TASKS: list[dict] = [
    {
        "name": "topological_sort",
        "prompt": (
            "`topo_sort(edges, nodes)` 함수를 작성하십시오.\n"
            "`edges` 는 `(a, b)` 리스트로 'a 가 b 보다 먼저' 를 뜻합니다. `nodes` 는 전체 "
            "노드 리스트입니다.\n"
            "위상 정렬 결과를 리스트로 돌려줍니다. **같은 순위면 `nodes` 에 나온 순서를 "
            "유지**하십시오(결정적이어야 합니다).\n"
            "순환이 있으면 `ValueError` 를 일으킵니다."
        ),
        "tests": '''
assert topo_sort([], ["a", "b"]) == ["a", "b"]
assert topo_sort([("a", "b")], ["b", "a"]) == ["a", "b"]
assert topo_sort([("a", "c"), ("b", "c")], ["a", "b", "c"]) == ["a", "b", "c"]
assert topo_sort([("b", "c")], ["a", "b", "c"]) == ["a", "b", "c"]
try:
    topo_sort([("a", "b"), ("b", "a")], ["a", "b"]); raise SystemExit("순환인데 ValueError 가 안 났다")
except ValueError: pass
''',
    },
    {
        "name": "bracket_error_position",
        "prompt": (
            "`check_brackets(text)` 함수를 작성하십시오.\n"
            "`()`, `[]`, `{}` 의 짝을 검사합니다. 올바르면 `None` 을 돌려줍니다.\n"
            "틀리면 **처음 잘못된 위치의 0-기반 인덱스**를 돌려줍니다.\n"
            "닫는 괄호가 남으면 그 괄호의 인덱스, 여는 괄호가 안 닫히면 그 여는 괄호의 인덱스입니다."
        ),
        "tests": '''
assert check_brackets("") is None
assert check_brackets("([]{})") is None
assert check_brackets(")") == 0
assert check_brackets("()]") == 2
assert check_brackets("(") == 0
assert check_brackets("([)]") == 2, check_brackets("([)]")
assert check_brackets("ab(cd") == 2
assert check_brackets("a[b]c") is None
''',
    },
    {
        "name": "lru_cache_class",
        "prompt": (
            "`LRU` 클래스를 작성하십시오.\n"
            "`LRU(capacity)` 로 만들고 `get(key)` 와 `put(key, value)` 를 가집니다.\n"
            "`get` 은 없으면 `None` 을 돌려주고, **있으면 그 항목을 가장 최근 사용으로** 올립니다.\n"
            "`put` 으로 용량을 넘기면 **가장 오래 쓰지 않은 항목**을 버립니다.\n"
            "이미 있는 키에 `put` 하면 값을 갱신하고 최근 사용으로 올립니다."
        ),
        "tests": '''
c = LRU(2)
c.put("a", 1); c.put("b", 2)
assert c.get("a") == 1
c.put("c", 3)                      # a 를 최근에 썼으므로 b 가 밀려나야 한다
assert c.get("b") is None, "가장 오래 쓰지 않은 것을 버려야 한다"
assert c.get("a") == 1 and c.get("c") == 3
d = LRU(2)
d.put("x", 1); d.put("x", 2)
assert d.get("x") == 2 and len([k for k in ("x",) if d.get(k) is not None]) == 1
e = LRU(1)
e.put("p", 1); e.put("q", 2)
assert e.get("p") is None and e.get("q") == 2
''',
    },
    {
        "name": "histogram_edges",
        "prompt": (
            "`histogram(values, edges)` 함수를 작성하십시오.\n"
            "`edges` 는 오름차순 경계입니다. 각 구간은 **[왼쪽 포함, 오른쪽 미포함)** 이며, "
            "**마지막 구간만 오른쪽도 포함**합니다.\n"
            "구간별 개수를 리스트로 돌려줍니다. 길이는 `len(edges) - 1` 입니다.\n"
            "범위 밖 값은 세지 않습니다. `edges` 가 2개 미만이면 `ValueError` 입니다."
        ),
        "tests": '''
assert histogram([1, 2, 3], [0, 2, 4]) == [1, 2]
assert histogram([0, 2, 4], [0, 2, 4]) == [1, 2], "마지막 구간은 오른쪽 포함"
assert histogram([-1, 5], [0, 2, 4]) == [0, 0], "범위 밖은 세지 않는다"
assert histogram([], [0, 1]) == [0]
assert histogram([1.9999999, 2.0], [0, 2, 4]) == [1, 1]
try:
    histogram([1], [0]); raise SystemExit("ValueError 가 나야 한다")
except ValueError: pass
''',
    },
    {
        "name": "retry_decorator",
        "prompt": (
            "`retry(times, exceptions)` 데코레이터를 작성하십시오.\n"
            "감싼 함수가 `exceptions` 튜플에 든 예외를 내면 **최대 `times` 번까지 다시** 부릅니다"
            "(최초 호출 포함해서 총 `times` 번).\n"
            "마지막까지 실패하면 **그 예외를 그대로** 올립니다. 목록에 없는 예외는 재시도하지 "
            "않고 즉시 올립니다.\n"
            "성공하면 그 반환값을 돌려줍니다. `time.sleep` 을 쓰지 마십시오."
        ),
        "tests": '''
calls = []
@retry(3, (ValueError,))
def flaky():
    calls.append(1)
    if len(calls) < 3: raise ValueError("아직")
    return "ok"
assert flaky() == "ok" and len(calls) == 3, calls

calls2 = []
@retry(2, (ValueError,))
def always():
    calls2.append(1); raise ValueError("계속 실패")
try:
    always(); raise SystemExit("마지막 예외를 올려야 한다")
except ValueError: pass
assert len(calls2) == 2, calls2

calls3 = []
@retry(3, (ValueError,))
def other():
    calls3.append(1); raise KeyError("목록에 없음")
try:
    other(); raise SystemExit("KeyError 가 그대로 나와야 한다")
except KeyError: pass
assert len(calls3) == 1, "목록에 없는 예외는 재시도하지 않는다"
''',
    },
    {
        "name": "chunked_stream",
        "prompt": (
            "`chunked(iterable, size)` **제너레이터**를 작성하십시오.\n"
            "입력을 `size` 개씩 묶은 리스트로 차례로 내놓습니다. 마지막 묶음은 짧을 수 있습니다.\n"
            "**입력 전체를 메모리에 올리지 마십시오** — 제너레이터를 받아도 동작해야 합니다.\n"
            "`size` 가 1 미만이면 `ValueError` 입니다."
        ),
        "tests": '''
assert list(chunked([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]
assert list(chunked([], 3)) == []
assert list(chunked(iter([1, 2, 3]), 3)) == [[1, 2, 3]]
import itertools
_g = chunked(itertools.count(), 2)          # 무한 입력이어도 앞부분을 낼 수 있어야 한다
assert next(_g) == [0, 1] and next(_g) == [2, 3]
try:
    list(chunked([1], 0)); raise SystemExit("ValueError 가 나야 한다")
except ValueError: pass
''',
    },
]


# ---------------------------------------------------------------------------
# 더 어려운 과제. `gpt-oss:20b` 가 HARD_TASKS 를 6/6 으로 통과해 **한계가 보이지 않는다.**
# 한계를 모르면 학습 목표를 정할 수 없다. 그래서 파서 우선순위, 부동소수 누적 오차,
# 계산량 제약, 바이트 경계, 제너레이터 정리 의미론을 넣었다. 전부 기계로 채점된다.
# ---------------------------------------------------------------------------
EXPERT_TASKS: list[dict] = [
    {
        "name": "expr_parser",
        "prompt": (
            "`evaluate(expr)` 함수를 작성하십시오.\n"
            "정수 사칙연산 문자열을 계산합니다. `+ - * /` 와 괄호, 단항 마이너스를 지원합니다.\n"
            "`/` 는 **실수 나눗셈**이고 0 으로 나누면 `ZeroDivisionError` 입니다.\n"
            "우선순위와 왼쪽 결합을 지켜야 합니다. `eval` 과 `ast` 모듈을 쓰지 마십시오."
        ),
        "tests": '''
assert evaluate("1+2*3") == 7
assert evaluate("(1+2)*3") == 9
assert evaluate("-3+5") == 2
assert evaluate("2*-3") == -6
assert evaluate("10-2-3") == 5, "왼쪽 결합"
assert evaluate("100/4/5") == 5.0, "왼쪽 결합"
assert abs(evaluate("7/2") - 3.5) < 1e-9
assert evaluate("-(2+3)") == -5
try:
    evaluate("1/0"); raise SystemExit("ZeroDivisionError 가 나야 한다")
except ZeroDivisionError: pass
''',
    },
    {
        "name": "stable_sum",
        "prompt": (
            "`stable_sum(values)` 함수를 작성하십시오.\n"
            "float 리스트의 합을 **누적 오차를 보정해서** 돌려줍니다.\n"
            "단순히 `sum()` 이나 반복 덧셈을 쓰면 통과하지 못합니다.\n"
            "Kahan 또는 Neumaier 보상 합산을 직접 구현하십시오. `math.fsum` 을 쓰지 마십시오."
        ),
        "tests": '''
_v = [1.0, 1e100, 1.0, -1e100]
assert abs(stable_sum(_v) - 2.0) < 1e-9, f"보상 합산이 아니다: {stable_sum(_v)}"
assert stable_sum([]) == 0.0
assert abs(stable_sum([0.1] * 10) - 1.0) < 1e-12
_big = [1e16] + [1.0] * 100
assert abs(stable_sum(_big) - (1e16 + 100.0)) < 1.0, stable_sum(_big)
''',
    },
    {
        "name": "utf8_truncate",
        "prompt": (
            "`truncate_bytes(text, limit)` 함수를 작성하십시오.\n"
            "문자열을 UTF-8 로 인코딩했을 때 `limit` 바이트를 넘지 않도록 자릅니다.\n"
            "**문자를 쪼개면 안 됩니다** — 한글 한 글자는 3바이트이므로 중간에서 자르면 "
            "깨집니다.\n"
            "잘린 문자열을 돌려줍니다. `limit` 이 음수면 `ValueError` 입니다."
        ),
        "tests": '''
assert truncate_bytes("abc", 10) == "abc"
assert truncate_bytes("한글", 3) == "한"
assert truncate_bytes("한글", 5) == "한", "5바이트로는 두 글자를 못 담는다"
assert truncate_bytes("한글", 6) == "한글"
assert truncate_bytes("a한", 2) == "a"
assert truncate_bytes("", 0) == ""
assert len(truncate_bytes("가나다라", 7).encode("utf-8")) <= 7
try:
    truncate_bytes("a", -1); raise SystemExit("ValueError 가 나야 한다")
except ValueError: pass
''',
    },
    {
        "name": "topk_stream",
        "prompt": (
            "`top_k(stream, k)` 함수를 작성하십시오.\n"
            "`(점수, 이름)` 튜플을 내놓는 이터러블에서 **점수가 큰 순으로 상위 k개**를 "
            "리스트로 돌려줍니다.\n"
            "점수가 같으면 **먼저 나온 것이 앞**입니다.\n"
            "**전체를 리스트로 만들지 마십시오** — 메모리는 k 에 비례해야 합니다. "
            "`k` 가 1 미만이면 `ValueError` 입니다."
        ),
        "tests": '''
assert top_k([(3, "a"), (1, "b"), (2, "c")], 2) == [(3, "a"), (2, "c")]
assert top_k([(1, "x"), (1, "y")], 2) == [(1, "x"), (1, "y")], "동점은 먼저 나온 것이 앞"
assert top_k([(1, "a")], 5) == [(1, "a")]
assert top_k([], 3) == []
import itertools
_s = ((i % 7, f"n{i}") for i in range(200000))
_r = top_k(_s, 3)
assert len(_r) == 3 and _r[0][0] == 6, _r
try:
    top_k([(1, "a")], 0); raise SystemExit("ValueError 가 나야 한다")
except ValueError: pass
''',
    },
    {
        "name": "generator_cleanup",
        "prompt": (
            "`counted_lines(lines, counter)` **제너레이터**를 작성하십시오.\n"
            "각 줄을 그대로 내놓되, **끝나거나 중간에 닫히더라도** `counter` 리스트에 "
            "내놓은 줄 수를 append 해야 합니다.\n"
            "즉 소비자가 `close()` 하거나 break 로 빠져나가도 정리 코드가 반드시 돕니다.\n"
            "(힌트: `try/finally`)"
        ),
        "tests": '''
c = []
assert list(counted_lines(["a", "b"], c)) == ["a", "b"]
assert c == [2], c

c2 = []
g = counted_lines(["a", "b", "c"], c2)
assert next(g) == "a"
g.close()
assert c2 == [1], f"중간에 닫아도 정리가 돌아야 한다: {c2}"

c3 = []
for _ in counted_lines(["x", "y", "z"], c3):
    break
import gc; gc.collect()
assert c3 == [1], f"break 로 빠져나가도 정리가 돌아야 한다: {c3}"
''',
    },
    {
        "name": "merge_sorted_streams",
        "prompt": (
            "`merge_streams(streams)` **제너레이터**를 작성하십시오.\n"
            "각각 오름차순인 여러 이터러블을 받아 전체 오름차순으로 하나씩 내놓습니다.\n"
            "**전체를 모아서 정렬하지 마십시오** — 각 스트림은 매우 길 수 있습니다.\n"
            "`heapq` 를 써도 됩니다. 같은 값이면 앞쪽 스트림이 먼저입니다."
        ),
        "tests": '''
assert list(merge_streams([[1, 3, 5], [2, 4, 6]])) == [1, 2, 3, 4, 5, 6]
assert list(merge_streams([])) == []
assert list(merge_streams([[], [1]])) == [1]
assert list(merge_streams([[1], [1]])) == [1, 1]
import itertools
_a = (i for i in range(0, 1000000, 2))
_b = (i for i in range(1, 1000000, 2))
_g = merge_streams([_a, _b])
assert [next(_g) for _ in range(6)] == [0, 1, 2, 3, 4, 5], "무한/거대 입력에서 앞부분을 내야 한다"
''',
    },
]

RUNNER = '''
import sys
{setup}
{code}
try:
{tests}
    sys.stdout.write("@@OK@@")
except SystemExit as _e:
    sys.stdout.write("@@BAD@@" + str(_e))
except Exception as _e:
    sys.stdout.write("@@BAD@@" + type(_e).__name__ + ": " + str(_e))
'''

_FENCE_OPEN = re.compile(r"```(?:python|py)?[^\S\n]*\n", re.IGNORECASE)


def extract_code(text: str) -> tuple[str, str]:
    """`grade_codegen.extract_code` 와 같은 규칙. 잘린 응답의 여는 펜스를 남기지 않는다."""
    closed = re.findall(r"```(?:python|py)?[^\S\n]*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if closed:
        return closed[0], "fenced"
    opens = list(_FENCE_OPEN.finditer(text))
    if opens:
        return text[opens[-1].end():], "unclosed-fence"
    return text, "bare" if "def " in text else "no-code"


def generate(host, model, num_ctx, num_predict, think, prompt, timeout, coding_prompt):
    messages = []
    if coding_prompt:
        messages.append({"role": "system", "content": CODING_SYSTEM_ADDENDUM})
    messages.append({"role": "user", "content": prompt + "\n코드 블록 하나만 출력하십시오."})
    payload = {"model": model, "messages": messages, "stream": False, "think": think,
               "keep_alive": "10m", "options": {"num_ctx": num_ctx, "num_predict": num_predict}}
    request = urllib.request.Request(f"{host.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        item = json.loads(response.read().decode("utf-8"))
    message = item.get("message") or {}
    return str(message.get("content") or ""), {
        "seconds": round(time.perf_counter() - started, 1),
        "done_reason": str(item.get("done_reason") or ""),
        "eval_count": item.get("eval_count"),
        "thinking_chars": len(str(message.get("thinking") or "")),
    }


def run_task(code: str, task: dict, exec_timeout: int) -> tuple[bool, str]:
    tests = "\n".join("    " + line for line in task["tests"].strip().splitlines())
    script = RUNNER.format(setup=task.get("setup", ""), code=code, tests=tests)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "candidate.py"
        path.write_text(script, encoding="utf-8")
        try:
            proc = subprocess.run([sys.executable, str(path)], capture_output=True,
                                  text=True, timeout=exec_timeout, cwd=tmp)
        except subprocess.TimeoutExpired:
            return False, "timeout"
    if "@@OK@@" in proc.stdout:
        return True, ""
    if "@@BAD@@" in proc.stdout:
        return False, proc.stdout.split("@@BAD@@", 1)[1].strip()[:120]
    last = (proc.stderr.strip().splitlines() or ["no output"])[-1]
    return False, last[:120]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("-n", "--runs", type=int, default=1)
    p.add_argument("--model", default="")
    p.add_argument("--num-ctx", type=int, default=0)
    p.add_argument("--num-predict", type=int, default=0)
    p.add_argument("--think", action="store_true")
    p.add_argument("--no-coding-prompt", action="store_true")
    p.add_argument("--timeout", type=int, default=0)
    p.add_argument("--hard", action="store_true", help="어려운 과제 6개를 함께 낸다")
    p.add_argument("--only-hard", action="store_true")
    p.add_argument("--only-expert", action="store_true", help="가장 어려운 6과제만")
    p.add_argument("--exec-timeout", type=int, default=15)
    p.add_argument("--host", default="")
    p.add_argument("--out", default="")
    args = p.parse_args(argv)

    settings = load_settings()
    hardware = detect_hardware()
    apply_profile_override(hardware, settings.force_profile)
    _general, coder = effective_local_models(settings, hardware.profile)
    model = args.model or coder
    num_ctx = args.num_ctx or settings.num_ctx or NUM_CTX_BY_PROFILE[hardware.profile]
    num_predict = args.num_predict or settings.local_max_output_tokens
    host = args.host or settings.ollama_host
    timeout = args.timeout or settings.request_timeout
    coding_prompt = not args.no_coding_prompt

    print(f"model={model} num_ctx={num_ctx} num_predict={num_predict} "
          f"think={'on' if args.think else 'off'} coding_prompt={'on' if coding_prompt else 'off'}")
    if args.only_expert:
        tasks = list(EXPERT_TASKS)
    elif args.only_hard:
        tasks = list(HARD_TASKS)
    elif args.hard:
        tasks = TASKS + HARD_TASKS + EXPERT_TASKS
    else:
        tasks = list(TASKS)
    print(f"{len(tasks)} tasks: " + ", ".join(t["name"] for t in tasks))

    if args.dry_run:
        for t in tasks:
            print(f"\n--- {t['name']} ---\n{t['prompt']}")
        return 0
    if not args.execute:
        print("\n--execute 없이는 채점하지 않습니다. 모델이 쓴 코드를 실행하기 때문입니다.")
        return 2

    runs: list[dict] = []
    for i in range(1, args.runs + 1):
        results = []
        for task in tasks:
            try:
                text, diag = generate(host, model, num_ctx, num_predict, args.think,
                                      task["prompt"], timeout, coding_prompt)
            except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
                results.append({"task": task["name"], "ok": False,
                                "why": f"generation failed: {exc}", "diag": {}})
                print(f"  run {i} {task['name']:<20} 생성 실패")
                continue
            code, how = extract_code(text)
            ok, why = run_task(code, task, args.exec_timeout)
            diag["extraction"] = how
            results.append({"task": task["name"], "ok": ok, "why": why, "diag": diag,
                            "code": code[:3000]})
            mark = "PASS" if ok else "FAIL"
            detail = "" if ok else f"  <- {why}"
            print(f"  run {i} {task['name']:<20} {mark} ({diag['seconds']:>5.1f}s){detail}")
        score = sum(1 for r in results if r["ok"])
        runs.append({"run": i, "score": score, "results": results})
        print(f"  run {i} 합계: {score}/{len(tasks)}\n")

    total = sum(r["score"] for r in runs)
    possible = len(tasks) * len(runs)
    print(f"전체 {total}/{possible}  ({100*total/possible:.0f}%)")
    per_task: dict[str, int] = {}
    for r in runs:
        for item in r["results"]:
            per_task[item["task"]] = per_task.get(item["task"], 0) + (1 if item["ok"] else 0)
    print("과제별:")
    for name, hits in per_task.items():
        print(f"  {name:<22} {hits}/{len(runs)}")

    out = Path(args.out) if args.out else ROOT / "logs" / f"python-grade-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": model, "num_ctx": num_ctx, "num_predict": num_predict,
                               "think": args.think, "coding_prompt": coding_prompt,
                               "total": total, "possible": possible, "runs": runs},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
