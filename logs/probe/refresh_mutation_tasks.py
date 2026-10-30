"""낡은 변이 과제를 현재 소스에 다시 맞춘다(2026-10-26 claude).

과제를 만든 뒤 코드가 바뀌면 변이 자리(find)가 소스에서 사라져 평가가 그 과제를 건너뛴다(2026-10-22 video_router).
같은 버그를 지금 소스에 다시 심을 수 있게, 사라진 find 마다 지금 소스에서 가장 닮은 구간을 찾고 3-way 병합
(git merge-file: 기준=옛 find, 우리=지금 구간, 그쪽=옛 replace)으로 같은 변경을 옮긴다. 충돌하면 그 과제는 손으로 고칠
목록에 남기고 옮기지 않는다. 원래 과제 파일은 건드리지 않고 새 파일로 쓴다.

    python -X utf8 logs/probe/refresh_mutation_tasks.py logs/probe/history_tasks_contracts_v3_20261004.json \
        --out logs/probe/history_tasks_contracts_v4_20261026.json [--check]

--check 는 복사본에서 과제마다 ① 고치기 전 합격 검사 통과 ② 변이 뒤 합격 검사 실패를 확인한다(모델 없음, 시험만 실행).
옮긴 변이는 원래 버그와 '같은 변경'을 지금 코드에 적용한 것이다. 버그의 난이도가 원래와 같다는 보장은 아니다.
"""
from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SLACK = 12
# 자동으로 옮기지 못하거나(병합 충돌) 자동으로 옮기면 뒤에 생긴 다른 기능까지 지워지는 과제 — 같은 버그를 손으로 다시 심었다.
MANUAL = {
    "hist_codex-backup-20260925-learning-awareness_app_test_lessons": [[
        '        f"5. 학습(LoRA) 후보    {len(core.storage.training_pool())}건 · 실행 기록 {core.storage.training_run_count()}건"\n'
        '        f"(그중 실제 LoRA 학습 {core.storage.lora_trained_run_count()}건)",\n'
        '        "LoRA 학습은 사람이 scripts/lora.py 로 실행합니다(이 PC 는 작은 4B 모델만 — 본 모델 27B 는 16GB 로 불가). "\n'
        '        "학습 모델은 ALEX 설정에 자동으로 들어가지 않습니다. 내보내기 기록은 학습 완료 증거가 아닙니다.",\n',
        '        f"5. 학습(LoRA) 후보    {len(core.storage.training_pool())}건 · 학습 실행 {core.storage.training_run_count()}회",\n',
    ]],
    "hist_codex-backup-20260925-summary-perf_web_test_runtime_settings": [
        ["from .app import (auto_apply_proposals, build_core, build_providers, build_summary_provider, engine_context_limits,"
         " handle_command,\n",
         "from .app import (auto_apply_proposals, build_core, build_providers, engine_context_limits, handle_command,\n"],
        ["        self.core.providers[Destination.LOCAL_CODER] = coder\n"
         "        self.core.local_summarizer = build_summary_provider(general, candidate)\n",
         "        self.core.providers[Destination.LOCAL_CODER] = coder\n"],
    ],
    # 영상 계획(video_plan)의 모델·시간·크기 대신 5B 의 옛 숫자를 박아 넣은 버그. 지금 코드는 LTX 도 계획으로 고르므로
    # 계획(plan.fhd 등)은 그대로 두고, 사용자·대화 모델에게 보이는 숫자만 옛 값으로 되돌린다.
    "hist_claude-backup-20261003-wan14b_core_test_imagegen": [
        ["        \"\"\"자연어 영상 부탁(ADR-075): 영어 프롬프트 → Ollama 모델 내림 → ComfyUI Wan 2.2(14B — 그림에서 시작하면 14B I2V,"
         " 없으면 5B,\n        5초, 'FHD' 면 SeedVR2).\n",
         "        \"\"\"자연어 영상 부탁(ADR-075): 영어 프롬프트 → Ollama 모델 내림 → ComfyUI Wan 2.2(720p 5초, 'FHD' 면 SeedVR2).\n"],
        ["        self._progress(f\"[영상 만드는 중: {prompt}]\\n({plan.model}, 5초 영상에 약 {plan.minutes}분\"\n"
         "                       f\"{'(FHD 로 키우기 포함)' if plan.fhd else ''} 걸립니다. \"\n",
         "        self._progress(f\"[영상 만드는 중: {prompt}]\\n(5초 영상에 약 4분{', FHD 로 키우기까지 약 8분' if plan.fhd else ''}"
         " 걸립니다. \"\n"],
        ["        lines = [f\"영상을 만들었습니다 — {result.model}{' + SeedVR2(FHD)' if result.fhd else ''}, ",
         "        lines = [f\"영상을 만들었습니다 — Wan 2.2 5B{' + SeedVR2(FHD)' if result.fhd else ''}, "],
        ["            big = generator.video_plan(start or False, True)\n"
         "            lines.append(f\"더 큰 화면({big.size[0]}×{big.size[1]})이 필요하면 'FHD로 ○○ 영상 만들어줘'처럼 말해 주세요"
         "(약 {big.minutes}분).\")\n",
         "            lines.append(\"더 큰 화면(1920×1056)이 필요하면 'FHD로 ○○ 영상 만들어줘'처럼 말해 주세요(약 8분).\")\n"],
        ["            clip, big = self.image_generator.video_plan(), self.image_generator.video_plan(fhd=True)\n", ""],
        ["                         f\" 영상도 만든다({clip.model}): '○○ 영상 만들어줘' 하면 5초 영상({clip.width}×{clip.height},"
         " 약 {clip.minutes}분), \"\n"
         "                         f\"'FHD로' 를 붙이면 {big.size[0]}×{big.size[1]}(약 {big.minutes}분), \"\n",
         "                         \" 영상도 만든다(Wan 2.2): '○○ 영상 만들어줘' 하면 5초 영상(720p, 약 4분), 'FHD로' 를 붙이면"
         " 1920×1056(약 8분), \"\n"],
    ],
    # 자동으로 옮기면 그 구간에 나중에 생긴 /self-check 까지 지운다 — /dev-outcomes·/dev-learn·/dev-insights 만 지운다.
    "hist_claude-backup-20260930-outcomes_app_test_developer": [
        ["  /dev-outcomes            최근 개발 작업의 결과 기록(시험·수정안·적용·전체 검사) 요약\n"
         "  /dev-learn               결과 기록에서 코딩 절차 교훈을 뽑는다(로컬 모델, ADR-070)\n", ""],
        ["  /dev-insights [remove N|skill N [이름]] 코딩 절차 교훈 목록 / 지우기 / 골라서 스킬로 (붙이기: coding_insights_enabled)\n", ""],
        ['    if text == "/dev-outcomes":\n        return DeveloperMode(core).outcomes(), True, session_id\n\n', ""],
        ['    if text == "/dev-insights" or text.startswith("/dev-insights ") or text == "/dev-learn":\n'
         "        try:\n            mode = DeveloperMode(core)\n"
         '            if text == "/dev-learn":\n                return mode.learn(), True, session_id\n'
         '            return mode.insights(text.removeprefix("/dev-insights").strip()), True, session_id\n'
         "        except ToolError as exc:\n            return str(exc), True, session_id\n\n", ""],
    ],
}


def _locate(source: str, find: str) -> str | None:
    """지금 소스에서 find 와 가장 닮은 연속 구간(줄 단위). 한 번만 나오는 구간이어야 한다."""
    lines, wanted = source.splitlines(keepends=True), find.splitlines(keepends=True)
    if not wanted:
        return None
    matcher = difflib.SequenceMatcher(None, lines, wanted, autojunk=False)
    i, j, size = matcher.find_longest_match(0, len(lines), 0, len(wanted))
    if size == 0 or not "".join(wanted[j:j + size]).strip():
        return None
    low = max(0, i - j - SLACK)
    high = min(len(lines), i - j + len(wanted) + SLACK)
    region = lines[low:high]
    blocks = [b for b in difflib.SequenceMatcher(None, region, wanted, autojunk=False).get_matching_blocks() if b.size]
    if not blocks:
        return None
    first, last = blocks[0], blocks[-1]
    start = max(0, low + first.a - first.b)                       # find 앞쪽의 안 맞은 줄(빈 줄 등)만큼 넓힌다
    end = min(len(lines), low + last.a + last.size + (len(wanted) - last.b - last.size))
    window = "".join(lines[start:end])
    matched = sum(b.size for b in blocks)
    if matched < max(2, len(wanted) // 2) or source.count(window) != 1:
        return None
    return window


def _merge(base: str, ours: str, theirs: str) -> str | None:
    with tempfile.TemporaryDirectory(prefix="alex-merge-") as tmp:
        paths = []
        for name, text in (("ours", ours), ("base", base), ("theirs", theirs)):
            path = Path(tmp) / name
            path.write_text(text, encoding="utf-8", newline="")
            paths.append(str(path))
        done = subprocess.run(["git", "merge-file", "-p", *paths], capture_output=True)
    if done.returncode != 0:  # 양수 = 충돌 수, 음수 = 오류
        return _transplant(base, ours, theirs)
    return done.stdout.decode("utf-8")


def _transplant(base: str, ours: str, theirs: str) -> str | None:
    """병합이 충돌할 때(옛 변이가 지운 구간 안을 그 뒤에 고친 경우) 줄 단위로 옮긴다.

    지우기는 지금 구간에서 같은 자리(앞뒤 맞는 줄 사이)를 통째로 지운다 — 그 사이에 새로 생긴 줄도 같은 기능이라 함께 지운다.
    바꾸기는 지금 줄이 옛 줄과 똑같을 때만 바꾼다. 자리를 정할 수 없으면 None(손으로 고칠 목록).
    """
    a, w, r = (text.splitlines(keepends=True) for text in (base, ours, theirs))
    to_w = {}
    for block in difflib.SequenceMatcher(None, a, w, autojunk=False).get_matching_blocks():
        for k in range(block.size):
            to_w[block.a + k] = block.b + k
    changes = []
    for tag, a1, a2, r1, r2 in difflib.SequenceMatcher(None, a, r, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        start = to_w[a1 - 1] + 1 if a1 - 1 in to_w else to_w.get(a1)
        end = to_w[a2] if a2 in to_w else (to_w[a2 - 1] + 1 if a2 - 1 in to_w else None)
        if start is None or end is None or end < start:
            return None
        if tag == "replace" and w[start:end] != a[a1:a2]:
            return None
        changes.append((start, end, r[r1:r2]))
    for start, end, lines in sorted(changes, reverse=True):
        w[start:end] = lines
    return "".join(w)


def refresh_task(task: dict, source: str) -> tuple[dict | None, str]:
    """(새 과제, 메모). 옮길 수 없으면 (None, 이유)."""
    edits = task.get("edits") or [[task["find"], task["replace"]]]
    new_edits, moved = [], 0
    if task["id"] in MANUAL and any(source.count(find) != 1 for find, _ in edits):
        edits, moved = MANUAL[task["id"]], -len(MANUAL[task["id"]])   # 음수 = 손으로 다시 심은 변이 수
        if any(source.count(find) != 1 for find, _ in edits):
            return None, "손으로 만든 변이 자리도 없음(소스가 또 바뀜)"
    for find, replace in edits:
        if source.count(find) == 1:
            new_edits.append([find, replace])
            continue
        window = _locate(source, find)
        if window is None:
            return None, "닮은 구간을 찾지 못함"
        merged = _merge(find, window, replace)
        if merged is None:
            return None, "3-way 병합 충돌"
        if merged == window:
            return None, "옮긴 변이가 아무것도 바꾸지 않음"
        new_edits.append([window, merged])
        moved += 1
    mutated = source
    for find, replace in new_edits:
        if mutated.count(find) != 1:
            return None, "변이끼리 겹침"
        mutated = mutated.replace(find, replace, 1)
    try:
        compile(mutated, task["file"], "exec")
    except SyntaxError as exc:
        return None, f"변이 뒤 문법 오류: {exc}"
    refreshed = {**{k: v for k, v in task.items() if k not in ("find", "replace")}, "edits": new_edits}
    if moved < 0:
        refreshed["refreshed"] = "manual"
        return refreshed, f"손으로 다시 심음 {-moved}곳"
    if moved:
        refreshed["refreshed"] = "auto"
        refreshed["refreshed_edits"] = moved
    return refreshed, f"옮김 {moved}곳" if moved else "그대로"


def _unittest(project: Path, pattern: str) -> int:
    names = [p.strip().removeprefix("tests/") for p in pattern.split(",")]
    worst = 0
    for name in names:
        done = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", name],
                              cwd=project, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        worst = worst or done.returncode
    return worst


def check_task(task: dict, root: Path = ROOT) -> dict:
    """복사본에서 고치기 전 통과·변이 뒤 실패를 확인한다."""
    with tempfile.TemporaryDirectory(prefix="alex-refresh-check-") as tmp:
        project = Path(tmp) / "project"
        for folder in ("jarvis", "tests"):
            shutil.copytree(root / folder, project / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        clean = _unittest(project, task["test"])
        target = project / task["file"]
        source = target.read_text(encoding="utf-8")
        for find, replace in task["edits"]:
            source = source.replace(find, replace, 1)
        target.write_text(source, encoding="utf-8")
        mutated = _unittest(project, task["test"])
    return {"clean_exit": clean, "mutated_exit": mutated, "usable": clean == 0 and mutated != 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tasks")
    parser.add_argument("--out", required=True)
    parser.add_argument("--check", action="store_true", help="복사본에서 시험을 돌려 쓸 수 있는 과제만 남긴다")
    args = parser.parse_args(argv)
    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
    out, report = [], []
    for task in tasks:
        source = (ROOT / task["file"]).read_text(encoding="utf-8")
        refreshed, note = refresh_task(task, source)
        row = {"task": task["id"], "note": note}
        if refreshed is not None and args.check:
            row.update(check_task(refreshed))
            if not row["usable"]:
                refreshed = None
        if refreshed is not None:
            out.append(refreshed)
        report.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(out)}/{len(tasks)} tasks written to {args.out}")
    return 0 if out else 1


if __name__ == "__main__":
    raise SystemExit(main())
