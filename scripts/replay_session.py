"""모델을 바꿔 가며 같은 조건으로 잰다 — 20b 가 틀리는 과제로 더 큰 모델을 가르기 위해.

**왜 있나.** 교과서 채점 과제(파이썬·RTL·정체성)는 gpt-oss:20b 가 이미 만점이라 더 큰 모델과 차이가
안 난다(ADR-048). 2026-09-25 에 20b 가 확실히 틀리는 과제가 생겼다 — **오답이 쌓인 대화에서 스스로
바로잡기.** 사용자 실제 대화(세션 333b12e29bc9)에서 ALEX 는 앞서 "인터넷이 금지돼 있다, 방화벽을
풀어야 한다" 고 틀리게 말했고, 새 사실을 줘도 같은 대화 안에서는 그 말을 되풀이했다
(PERFORMANCE.md 25-3, 코덱스 수정 뒤에도 0/2).

두 가지 방식:

  replay  사용자 세션을 **복사본에서** 특정 메시지 직전까지 되돌리고 그 메시지를 다시 넣는다.
          원본 DB 는 읽기만 한다.
  fresh   빈 세션에서 질문 목록을 묻는다(PERFORMANCE.md 25-2 의 클라우드·인터넷 질문).

    python scripts/replay_session.py replay --model gpt-oss:120b -n 2
    python scripts/replay_session.py replay --session 333b12e29bc9 --cut-at 143 --model gpt-oss:20b
    python scripts/replay_session.py fresh --model gpt-oss:120b -n 2

**판정은 사람이 답을 읽어서 한다.** 자동 채점을 붙이지 않았다 — 지난 오답의 단어 목록으로 판정했다가
새로 지어낸 말을 네 번 모두 놓친 적이 있다(PERFORMANCE.md 25-2). 이 스크립트는 답과 시간과 경로만 남긴다.

측정 규칙: 다른 측정과 겹쳐 돌리지 않는다. 모델을 바꿀 때는 앞 모델이 VRAM 에서 내려간 뒤에.
재기 전에 앱의 실효 설정(/status)을 읽는다.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import dataclasses
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass

from jarvis.app import build_core  # noqa: E402
from jarvis.config import load_settings  # noqa: E402

DEFAULT_SESSION = "333b12e29bc9"
DEFAULT_CUT = 143  # "코드 수정됐을텐데 다시 확인해봐" — 앞에 오답 140·142 가 있다
FRESH_QUESTIONS = (
    "클라우드 AI에게 요청보내는건?",
    "인터넷 접속이 금지돼 있는 거야?",
    "클라우드LLM을 사용할때 내 API를 사용하도록 해야하는건가?",
    "인터넷에서 찾아줄 수 있어?",
)
# 바른 답이 담아야 할 사실(판정 참고용 — 자동 채점이 아니다).
TRUTH = ("연결 코드는 있다(OpenAI·Anthropic·Gemini)지만 꺼져 있다 / 인터넷은 금지가 아니다(검색 부탁·주소와 "
         "최신 정보가 필요한 자연어 질문으로 웹을 읽는다) / 방화벽은 없다 / ALEX 는 설정·코드를 직접 확인할 수 없다 / "
         "앞서 틀렸다면 그렇다고 정정한다")


def with_model(settings, model: str | None, data_dir: Path):
    changes = {"data_dir": data_dir}
    if model:
        changes.update(general_model=model, coder_model=model,
                       desktop_general_model=model, desktop_coder_model=model)
    return dataclasses.replace(settings, **changes)


def copy_database(source: Path, target_dir: Path) -> Path:
    target = target_dir / source.name
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)  # 페이지 단위 복사라 rowid 가 그대로다
    finally:
        src.close()
        dst.close()
    return target


def replay_once(settings, model, session: str, cut_at: int, question: str | None) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="alex-replay-"))
    db = copy_database(settings.database_path, tmp)
    # 연결을 닫아야 한다 — `with sqlite3.connect()` 는 커밋만 하고 닫지 않아 윈도우가 파일을 붙잡는다.
    with closing(sqlite3.connect(db)) as conn, conn:
        row = conn.execute("SELECT role, content FROM messages WHERE rowid = ? AND session_id = ?",
                           (cut_at, session)).fetchone()
        if row is None:
            raise SystemExit(f"세션 {session} 에 rowid {cut_at} 메시지가 없습니다.")
        if question is None:
            if row[0] != "user":
                raise SystemExit(f"rowid {cut_at} 는 사용자 메시지가 아닙니다. --question 을 주십시오.")
            question = row[1]
        conn.execute("DELETE FROM messages WHERE session_id = ? AND rowid >= ?", (session, cut_at))
    core, _ = build_core(with_model(settings, model, tmp), approval_callback=lambda _r: False)
    started = time.time()
    answer, decision = core.handle(session, question)
    return {"question": question, "answer": answer, "route": decision.destination.value,
            "seconds": round(time.time() - started, 1)}


def fresh_once(settings, model, question: str) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="alex-fresh-"))
    core, _ = build_core(with_model(settings, model, tmp), approval_callback=lambda _r: False)
    session = core.get_or_create_session(False)
    started = time.time()
    answer, decision = core.handle(session, question)
    return {"question": question, "answer": answer, "route": decision.destination.value,
            "seconds": round(time.time() - started, 1)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("replay", "fresh"))
    parser.add_argument("--model", default=None, help="비우면 설정의 모델")
    parser.add_argument("-n", "--runs", type=int, default=2)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--cut-at", type=int, default=DEFAULT_CUT, help="이 rowid 부터 지우고 그 질문을 다시 넣는다")
    parser.add_argument("--question", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    settings = load_settings()
    model_label = args.model or "(설정값)"
    print(f"mode={args.mode} model={model_label} runs={args.runs}")
    print(f"바른 답의 사실: {TRUTH}\n")
    report = []
    for run in range(1, args.runs + 1):
        items = ([replay_once(settings, args.model, args.session, args.cut_at, args.question)]
                 if args.mode == "replay" else [fresh_once(settings, args.model, q) for q in FRESH_QUESTIONS])
        for item in items:
            item.update(run=run, model=model_label)
            report.append(item)
            print(f"=== #{run} ({item['seconds']}s, {item['route']}) {item['question'][:40]}")
            print(item["answer"].strip()[:900], "\n")
    out = Path(args.out) if args.out else ROOT / "logs" / (
        f"replay-{args.mode}-{(args.model or 'config').replace(':', '_')}-{time.strftime('%Y%m%d-%H%M%S')}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}\n판정은 답을 읽고 사람이 하십시오.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
