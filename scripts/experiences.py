"""Manage the experience store: import, review, export (ADR-037).

The experience store is the training/eval corpus. It is deliberately separate from
`memories`, which is user-editable context injected into prompts. Experiences are
append-only: the five body fields never change, only review metadata does.

Pipeline (PROJECT_ALEX.md section 9):

    raw  ->  candidate  ->  approved  ->  exported to a training set
                        \\-> rejected

Nothing reaches a training set without passing through a human. That is not politeness;
this model flags working code as buggy and rewrites the same broken code after being shown
the error (`PERFORMANCE.md` section 8). Training on unreviewed self-output amplifies error.

Examples:
  python scripts/experiences.py status
  python scripts/experiences.py import-routes        # absorb /wrong corrections
  python scripts/experiences.py list --stage raw
  python scripts/experiences.py review <id> approved
  python scripts/experiences.py export --out datasets/alex-m1
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass

from jarvis.config import load_settings
from jarvis.storage import Storage, StorageError


def open_storage() -> Storage:
    settings = load_settings()
    return Storage(settings.database_path)


def cmd_status(store: Storage, _args: argparse.Namespace) -> int:
    counts = store.experience_counts()
    if not counts:
        print("No experiences recorded yet. Use /good or /bad in the app.")
        return 0
    print("experience store (ADR-037)")
    for key in sorted(counts):
        print(f"  {key:<20} {counts[key]}")
    pool = store.training_pool()
    print(f"  {'trainable':<20} {len(pool)}   (approved AND train)")
    broken = store.verify_experience_integrity()
    print(f"  integrity            {'ok' if not broken else f'TAMPERED: {broken[:5]}'}")
    return 1 if broken else 0


def cmd_import_routes(store: Storage, args: argparse.Namespace) -> int:
    """`/wrong` corrections already in route_events become experiences.

    Until now that signal was collected and only ever shown as a statistic.
    """
    events = store.corrected_route_events(limit=args.limit)
    existing = {e.source for e in store.list_experiences(kind="route", limit=10_000)}
    added = 0
    for event in events:
        if not event.correction:
            continue
        source = f"route_events:{event.event_id}"
        if source in existing:
            continue
        store.record_experience(
            "route",
            situation=event.text,
            judgment=f"라우터가 {event.destination} 으로 보냈다 (근거: {event.reason})",
            rationale=f"matched={event.matched}",
            choice=event.destination,
            outcome=f"사용자 정정: {event.correction}",
            verdict="bad",
            correction=event.correction,
            source=source,
        )
        added += 1
    print(f"imported {added} corrected route event(s)")
    if added:
        print("They are stage='raw'. Review them before they can be exported.")
    return 0



# ---------------------------------------------------------------------------
# 문서에 **명시적으로 적힌 정정**만 수확한다 (GPT_NEXT.md 2026-09-21 명세).
#
# 왜 손으로 고른 목록인가: `verdict` 는 판단을 내린 모델 바깥에서 와야 한다. 문서를
# 훑어 "정정처럼 보이는 문장" 을 모델이나 정규식이 골라내면, 그것이 곧 모델이 스스로
# 채점하는 것이다. 아래 9건이 쓸 만한 이유는 **측정값이 반박했거나 사람이 지적했기**
# 때문이고, 그 사실은 사람이 확인해야 한다.
#
# `anchor` 는 그 정정이 아직 문서에 살아 있는지 확인하는 닻이다. 문서에서 사라졌으면
# 기록이 원본과 어긋난 것이므로 수확하지 않고 알린다.
# ---------------------------------------------------------------------------
CORRECTIONS: list[dict] = [
    {
        "source": "decisions:ADR-034",
        "file": "DECISIONS.md",
        "anchor": "위 비교는 오염되어 있었다",
        "situation": "thinking 을 켜는 것이 코드 품질에 이로운지 판단해야 했다. "
                     "채점 가능한 과제 3개로 think ON 과 OFF 를 비교했다.",
        "judgment": "thinking ON 은 3.4배 느리면서 점수가 더 낮았다(1/3 대 2/3). "
                    "이 장비의 ollama_think 를 false 로 둔다.",
        "rationale": "과제 3개 중 1개에서 점수가 갈렸고, 그것을 품질 차이로 읽었다.",
        "outcome": "비교가 오염되어 있었다. think ON 은 ctx 4096, OFF 는 ctx 8192 로 "
                   "컨텍스트가 같이 바뀌었다. think 만 바꿔 다시 재니 속도만 2.1배 차이였고 "
                   "품질 차이는 측정되지 않았다.",
        "correction": "한 번에 한 변수만 바꿔라. 두 가지가 같이 바뀐 비교로 인과를 주장하면 "
                      "안 된다. 그리고 과제 3개 중 1개 차이는 이 벤치마크의 편차(0/7~6/7)를 "
                      "이기지 못한다.",
    },
    {
        "source": "decisions:ADR-046",
        "file": "DECISIONS.md",
        "anchor": "내가 세웠던 메커니즘 가설은 기각한다",
        "situation": "코딩 지시에서 검산을 코드 뒤로 옮긴 변경이 효과가 있는지 "
                     "각 팔 n=10 으로 쟀다.",
        "judgment": "점수 차이(3/10 대 0/10)는 유의하지 않으니 근거가 아니다. "
                    "thinking 최댓값이 28% 줄어든 것이 진짜 근거다 — 이 지시는 폭주를 끊는다.",
        "rationale": "메커니즘 지표가 움직였으므로 점수는 그 결과일 뿐이라고 보았다.",
        "outcome": "n=20 에서 그 28% 는 5% 로 사라졌고(둘째 판에서 30,338자 폭주가 또 나왔다), "
                   "대신 점수 쪽이 유의해졌다(p=0.032). 함께 줄어든 것은 폭주가 아니라 "
                   "틀린 코드였다(4 → 1).",
        "correction": "표본 10개에서 만든 설명을 메커니즘이라고 부르지 마라. 결론이 살아남아도 "
                      "설명은 살아남지 못할 수 있다. 맞는 결론을 틀린 이유로 믿고 있었다.",
    },
    {
        "source": "performance:offload-6.6x",
        "file": "PERFORMANCE.md",
        "anchor": "12% 만 넘어가도 6.6배",
        "situation": "RAM 오프로딩이 대화 체감에 얼마나 비싼지 판단해야 했다.",
        "judgment": "층의 12% 만 RAM 으로 가도 벽시계가 6.6배 느려진다. "
                    "따라서 GPU 100% 를 유지하는 선에서만 써야 한다.",
        "rationale": "PERFORMANCE.md 2절 표의 '시간' 열을 그대로 인용했다.",
        "outcome": "그 열은 모델 로딩을 포함한 콜드 스타트였다. 로딩·프롬프트·생성을 갈라 "
                   "다시 재니, 모델이 상주한 둘째 턴부터는 12% 오프로딩에 tok/s 가 11% "
                   "떨어질 뿐이고 첫 글자까지는 0.5초 미만이었다.",
        "correction": "같은 표 안에서 tok/s 는 12% 밖에 안 떨어져 있었다. 두 숫자가 서로 "
                      "맞지 않으면 큰 쪽을 인용하지 말고 왜 다른지부터 보라.",
    },
    {
        "source": "performance:moe-offload",
        "file": "PERFORMANCE.md",
        "anchor": "MOE" if False else "MoE 가 RAM 오프로딩에서 유리하다",
        "situation": "MoE 모델을 RAM 오프로딩 환경에서 쓸 수 있는지 판단해야 했다.",
        "judgment": "MoE 는 총 파라미터만큼 VRAM 이 필요한데 속도는 활성 파라미터급이라 "
                    "오프로딩 환경에 부적합하다.",
        "rationale": "밀집 모델의 직관을 그대로 적용했다.",
        "outcome": "정반대였다. gpt-oss:20b 는 57% 가 RAM 에 있는데 42% 만 오프로딩된 "
                   "밀집 14b 보다 두 배 이상 빨랐다. 매 토큰 읽는 가중치가 적어서 "
                   "안 읽히는 가중치가 RAM 에 있어도 대가가 없다.",
        "correction": "구조가 다른 모델에 한 구조의 직관을 옮기지 마라. 토큰 생성은 "
                      "메모리 대역폭에 묶이고, MoE 는 매 토큰 읽는 양이 다르다.",
    },
    {
        "source": "performance:budget-5120",
        "file": "PERFORMANCE.md",
        "anchor": "예산 권장값 정정: 5120",
        "situation": "출력 예산(num_predict)의 적정값을 정해야 했다.",
        "judgment": "관측된 최대 성공이 4,712 토큰이므로 5120 으로 정한다.",
        "rationale": "한 조건(기본 프롬프트)에서 잰 성공 회차의 최댓값에 여유를 얹었다.",
        "outcome": "프롬프트를 바꾼 조건에서 5,715 토큰을 쓴 만점 회차가 나왔다. "
                   "5120 이었으면 최고 성능 설정이 잘렸다.",
        "correction": "한 조건에서 잰 상한을 다른 조건에 그대로 쓰지 마라. "
                      "프롬프트를 바꾸면 분포가 같이 바뀐다.",
    },
    {
        "source": "performance:contaminated-20b",
        "file": "PERFORMANCE.md",
        "anchor": "오염된 측정이다",
        "situation": "gpt-oss:20b 의 GPU/RAM 배치와 속도를 재야 했다.",
        "judgment": "VRAM 2.30GB / RAM 10.79GB, 18% GPU, 17.1 tok/s 다.",
        "rationale": "auto 배치에서 /api/ps 가 보고한 값을 그대로 적었다.",
        "outcome": "잴 때 qwen3:14b 가 아직 VRAM 에 상주해 있어 Ollama 가 쓸 수 있는 VRAM 이 "
                   "적었다. 비운 뒤 같은 설정으로 재니 43% GPU, 23.4 tok/s 였다.",
        "correction": "모델을 비교할 때는 앞 모델이 VRAM 에서 내려간 뒤에 재라. "
                      "OLLAMA_MAX_LOADED_MODELS=1 이어도 keep_alive 동안은 남아 있다.",
    },
    {
        "source": "performance:num-gpu-max",
        "file": "PERFORMANCE.md",
        "anchor": "최댓값이 최적이 아니다",
        "situation": "GPU 에 올릴 층 수(num_gpu)를 어떻게 정할지 판단해야 했다.",
        "judgment": "자동 배치가 VRAM 을 남기므로 최대한 많이 올릴수록 빠르다.",
        "rationale": "VRAM 1.7GB 가 놀고 있는데 4.2GB 가 RAM 에 있는 것을 보고 그렇게 읽었다.",
        "outcome": "num_gpu=32 까지는 48% 빨라졌지만 36·40 에서 다시 느려졌다. "
                   "VRAM 사용량이 물리 8.0GB 를 넘기면 드라이버가 초과분을 시스템 RAM 으로 "
                   "스필하는데, 그것이 명시적 CPU 층보다 느리다.",
        "correction": "GPU 에 최대한이 아니라 물리 VRAM 을 넘기 직전까지다. "
                      "그리고 MoE 에서는 올리는 것 자체가 해가 된다.",
    },
    {
        "source": "performance:python-overfit",
        "file": "PERFORMANCE.md",
        "anchor": "튜닝한 문제로 채점하고",
        "situation": "이 모델의 파이썬 코딩 능력을 평가해야 했다.",
        "judgment": "파이썬 코딩은 약하다. 계산기 벤치마크가 18/20 이지만 그것이 상한이다.",
        "rationale": "저장소에 있던 유일한 코딩 점수를 일반 능력으로 읽었다.",
        "outcome": "그 18/20 은 계산기 한 문제이고, CODING_SYSTEM_ADDENDUM 이 바로 그 문제에서 "
                   "관찰된 실패를 겨냥해 만들어진 것이다. 종류를 섞어 6과제로 재니 12/12 였고 "
                   "Verilog 도 101,024 벡터를 통과했다.",
        "correction": "튜닝한 과제로 채점하면 과적합을 성능으로 읽는다. 일반 능력을 말하려면 "
                      "그 지시가 겨냥하지 않은 과제로 재라.",
    },
    {
        "source": "performance:probe-budget",
        "file": "PERFORMANCE.md",
        "anchor": "측정 도구의 예산 부족을",
        "situation": "모델이 하드웨어 질문에 빈 답을 돌려준 이유를 찾아야 했다.",
        "judgment": "앱이 하드웨어 정보를 프롬프트에 안 넣어서 모델이 모르는 것이다.",
        "rationale": "빈 답을 지식 부재로 읽었다.",
        "outcome": "done_reason 이 length 였다. 프로브의 num_predict 가 250 이라 잘린 것이고, "
                   "gpt-oss:20b 는 think:False 여도 thinking 에 200~800자를 쓴다. "
                   "2000 으로 올리니 전부 정확히 답했다.",
        "correction": "빈 답을 보면 대상을 탓하기 전에 done_reason 과 예산부터 보라. "
                      "측정 도구의 한계를 대상의 성질로 읽는 실수가 이 저장소에서 반복됐다.",
    },
]


def cmd_import_corrections(store: Storage, args: argparse.Namespace) -> int:
    """문서에 명시된 정정을 경험으로 수확한다. 기본은 dry-run 이다."""
    root = Path(__file__).resolve().parent.parent
    texts = {name: (root / name).read_text(encoding="utf-8")
             for name in {c["file"] for c in CORRECTIONS}}
    existing = {e.source for e in store.list_experiences(kind="identity", limit=10_000)}

    planned, stale, already = [], [], []
    for item in CORRECTIONS:
        if item["anchor"] not in texts[item["file"]]:
            stale.append(item)
        elif item["source"] in existing:
            already.append(item)
        else:
            planned.append(item)

    for item in stale:
        print(f"  [닻 없음] {item['source']} — {item['file']} 에서 "
              f"{item['anchor']!r} 를 찾지 못했습니다. 문서가 바뀌었는지 확인하십시오.")
    for item in already:
        print(f"  [이미 있음] {item['source']}")
    for item in planned:
        print(f"  [넣을 것] {item['source']}: {item['judgment'][:60]}…")

    if not args.write:
        print(f"\n{len(planned)}건을 넣을 수 있습니다. 실제로 저장하려면 --write 를 붙이십시오.")
        return 0

    added = 0
    for item in planned:
        store.record_experience(
            "identity",
            situation=item["situation"],
            judgment=item["judgment"],
            rationale=item["rationale"],
            outcome=item["outcome"],
            verdict="bad",
            correction=item["correction"],
            source=item["source"],
        )
        added += 1
    print(f"\n{added}건을 넣었습니다. stage='raw' 이므로 검토를 거쳐야 내보낼 수 있습니다.")
    if stale:
        print(f"닻이 사라진 {len(stale)}건은 넣지 않았습니다.")
    return 0


REQUIRED_ADD_FIELDS = ("kind", "situation", "judgment", "source")


def cmd_add(store: Storage, args: argparse.Namespace) -> int:
    """작업 중에 받은 정정을 그 자리에서 경험으로 남긴다.

    `import-corrections` 는 문서에 이미 적힌 정정을 뒤늦게 수확한다. 목록이 이 파일 안에
    하드코딩돼 있어서 새 정정이 생길 때마다 스크립트를 고쳐야 한다. **그래서 claude 나
    코덱스가 하는 작업은 하나도 들어가지 않았다**(GPT_NEXT.md 2절).

    이 명령이 그 구멍을 메운다. 측정에 의해 판단이 교정되면 그 자리에서 넣는다.
    사람이 쓰든 claude 가 쓰든 코덱스가 쓰든 같은 문이다.

    입력은 JSON 파일이다. 명령줄 인자로 다섯 필드를 넘기면 따옴표와 줄바꿈에서
    반드시 깨진다 — 2026-09-21 에 실제로 겪었다.

    세 가지를 지킨다:
    - `split` 을 받지 않는다. `record_experience` 에 그 파라미터가 없다(ADR-037 결정 5).
    - `source` 가 이미 있으면 넣지 않는다. 같은 정정이 두 번 학습되면 안 된다.
    - 기본이 dry-run 이다. 본문 5필드는 `content_hash` 로 고정돼 나중에 못 고친다.
    """
    path = Path(args.file)
    if not path.exists():
        print(f"파일이 없습니다: {path}")
        return 2
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"JSON 을 읽지 못했습니다: {exc}")
        return 2
    records = payload if isinstance(payload, list) else [payload]

    existing = {e.source for e in store.list_experiences(limit=10_000) if e.source}
    planned: list[dict] = []
    rejected: list[tuple[int, str]] = []
    already: list[dict] = []
    seen: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            rejected.append((index, "객체가 아닙니다"))
            continue
        missing = [f for f in REQUIRED_ADD_FIELDS if not str(record.get(f, "")).strip()]
        if missing:
            rejected.append((index, f"빠진 필드: {', '.join(missing)}"))
        elif record["kind"] not in Storage.EXPERIENCE_KINDS:
            rejected.append((index, f"kind 가 목록에 없습니다: {record['kind']!r} "
                                    f"(가능: {', '.join(Storage.EXPERIENCE_KINDS)})"))
        elif "split" in record:
            rejected.append((index, "split 은 지정할 수 없습니다. id 해시로 정해집니다(ADR-037)"))
        elif record["source"] in existing or record["source"] in seen:
            already.append(record)
        else:
            seen.add(record["source"])
            planned.append(record)

    for index, why in rejected:
        print(f"  [거부] {index}번: {why}")
    for record in already:
        print(f"  [이미 있음] {record['source']}")
    for record in planned:
        print(f"  [넣을 것] {record['source']} ({record['kind']}): {record['judgment'][:60]}…")

    if not args.write:
        print(f"\n{len(planned)}건을 넣을 수 있습니다. 실제로 저장하려면 --write 를 붙이십시오.")
        return 0 if not rejected else 1

    added = 0
    for record in planned:
        store.record_experience(
            record["kind"],
            situation=record["situation"],
            judgment=record["judgment"],
            rationale=record.get("rationale", ""),
            choice=record.get("choice", ""),
            outcome=record.get("outcome", ""),
            verdict=record.get("verdict", "bad"),
            correction=record.get("correction", ""),
            source=record["source"],
            model=record.get("model", ""),
        )
        added += 1
    print(f"\n{added}건을 넣었습니다. stage='raw' 이므로 검토를 거쳐야 내보낼 수 있습니다.")
    print("승인은 이것을 쓴 쪽이 하면 자기참조입니다. 사용자가 하십시오:")
    print("  python scripts/experiences.py list --stage raw")
    return 0 if not rejected else 1


def cmd_list(store: Storage, args: argparse.Namespace) -> int:
    rows = store.list_experiences(stage=args.stage, split=args.split,
                                  kind=args.kind, limit=args.limit)
    if not rows:
        print("no matching experiences")
        return 0
    for e in rows:
        print(f"{e.id[:12]}  {e.kind:<9} {e.stage:<10} {e.split:<6} {e.verdict:<8} {e.created_at[:16]}")
        print(f"   상황: {e.situation[:90]}")
        print(f"   판단: {e.judgment[:90]}")
        if e.correction:
            print(f"   정정: {e.correction[:90]}")
    return 0


def cmd_review(store: Storage, args: argparse.Namespace) -> int:
    matches = [e for e in store.list_experiences(limit=10_000) if e.id.startswith(args.id)]
    if not matches:
        print(f"no experience starts with {args.id!r}")
        return 2
    if len(matches) > 1:
        print(f"{args.id!r} is ambiguous ({len(matches)} matches); use more characters")
        return 2
    store.review_experience(matches[0].id, args.stage, correction=args.correction)
    print(f"{matches[0].id[:12]} -> {args.stage}")
    return 0


# 모델 독립 본문을 학습 직전에만 대화 형식으로 바꾼다 (ADR-037 결정 2).
def to_chat(exp) -> dict:
    return {
        "messages": [
            {"role": "user", "content": exp.situation},
            {"role": "assistant", "content": exp.correction or exp.judgment},
        ],
        "meta": {"id": exp.id, "kind": exp.kind, "verdict": exp.verdict, "source": exp.source},
    }


def cmd_export(store: Storage, args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 학습은 반드시 training_pool 뷰를 거친다. eval 이 구조적으로 섞이지 않는다.
    train = store.training_pool()
    evals = [e for e in store.list_experiences(stage="approved", split="eval", limit=10_000)]

    overlap = {e.id for e in train} & {e.id for e in evals}
    if overlap:  # 뷰가 막고 있으므로 여기 걸리면 스키마가 깨진 것이다
        print(f"FATAL: {len(overlap)} experience(s) in both splits. Aborting.")
        return 1

    train_path = out_dir / "train.jsonl"
    eval_path = out_dir / "eval.jsonl"
    for path, rows in ((train_path, train), (eval_path, evals)):
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            for e in rows:
                fh.write(json.dumps(to_chat(e), ensure_ascii=False) + "\n")

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "run_id": args.run_id,
        "base_model": args.base_model,
        "train_count": len(train), "eval_count": len(evals),
        "train_ids": [e.id for e in train], "eval_ids": [e.id for e in evals],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.run_id:
        store.record_training_run(args.run_id, args.base_model, [e.id for e in train],
                                  notes=f"exported to {out_dir}")
        print(f"recorded training run {args.run_id} with {len(train)} experience(s)")

    print(f"train {len(train)} -> {train_path}")
    print(f"eval  {len(evals)} -> {eval_path}")
    print(f"manifest -> {out_dir / 'manifest.json'}")
    if not train:
        print("\nNothing to train on yet. Approve some experiences first:")
        print("  python scripts/experiences.py list --stage raw")
        print("  python scripts/experiences.py review <id> approved")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")

    p_imp = sub.add_parser("import-routes")
    p_imp.add_argument("--limit", type=int, default=1000)

    p_cor = sub.add_parser("import-corrections")
    p_cor.add_argument("--write", action="store_true",
                       help="실제로 저장한다. 없으면 무엇을 넣을지 보여주기만 한다.")

    p_add = sub.add_parser("add")
    p_add.add_argument("--file", required=True,
                       help="경험 JSON 파일. 객체 하나 또는 객체 목록.")
    p_add.add_argument("--write", action="store_true",
                       help="실제로 저장한다. 없으면 무엇을 넣을지 보여주기만 한다.")

    p_list = sub.add_parser("list")
    p_list.add_argument("--stage", default=None)
    p_list.add_argument("--split", default=None)
    p_list.add_argument("--kind", default=None)
    p_list.add_argument("--limit", type=int, default=20)

    p_rev = sub.add_parser("review")
    p_rev.add_argument("id")
    p_rev.add_argument("stage", choices=("candidate", "approved", "rejected"))
    p_rev.add_argument("--correction", default="")

    p_exp = sub.add_parser("export")
    p_exp.add_argument("--out", default="datasets/latest")
    p_exp.add_argument("--run-id", default="", help="e.g. ALEX-M1; records lineage when given")
    p_exp.add_argument("--base-model", default="")

    args = parser.parse_args(argv)
    handlers = {"status": cmd_status, "import-routes": cmd_import_routes,
                "import-corrections": cmd_import_corrections, "add": cmd_add,
                "list": cmd_list, "review": cmd_review, "export": cmd_export}
    try:
        return handlers[args.command](open_storage(), args)
    except StorageError as exc:
        print(f"storage error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
