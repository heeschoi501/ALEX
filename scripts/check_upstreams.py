"""흡수한 원본이 그 뒤 무엇을 바꿨는지, ALEX 의 덩치와 결론 안 난 실험을 한 번에 보고한다(2026-10-27 claude).

ALEX 는 OpenCode·Hermes Agent 같은 바깥 프로젝트의 능력을 흡수해 자란다. 흡수한 부분은 원본이 고친 버그·보안 문제·더 나은 방식을
놓치지 않게 따라가야 하고(docs/upstreams.json 의 core), 결론 안 난 실험과 같은 일을 하는 여러 길은 정한 날까지 측정해 줄여야
덩치만 커지지 않는다(experiment·duplicate_paths).

    python -X utf8 scripts/check_upstreams.py                 # 원본 받아 비교 + 덩치 + 실험(인터넷 필요)
    python -X utf8 scripts/check_upstreams.py --offline       # 덩치 + 실험만
    python -X utf8 scripts/check_upstreams.py --out 보고.md --json 보고.json

원본은 기록한 버전(ref)과 추적 대상(기본 가지 또는 tag:접두)을 얕게(--depth 1) 받아 **등록한 파일만** 비교한다.
기준 ref 의 SHA 가 목록의 commit 과 일치해야 한다. 태그 추적은 다른 주요 버전의 정식 태그도 알린다.
받은 원본은 읽기만 하고 실행하지 않는다. 반영할지는 사람이 정해 ref·commit·checked 를 고친다.
조회·검증 실패는 보고서를 남기고 종료 코드 1 로 알린다. 보안 공지와 미등록 파일은 별도로 검토해야 한다.
"""
from __future__ import annotations

import argparse
import dataclasses
from datetime import date
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs" / "upstreams.json"
CACHE = ROOT / "logs" / "upstreams-cache"


def _git(folder: Path, *args: str, timeout: int = 600) -> str:
    done = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])}: {(done.stderr or done.stdout).strip()[:300]}")
    return done.stdout


def _version(tag: str) -> tuple:
    return tuple(int(n) if n.isdigit() else n for n in re.split(r"[.-]", tag.lstrip("v")))


def release_tags(folder: Path) -> list[str]:
    """Numeric stable-version tags; this is not a GitHub security-advisory check."""
    tags = [line.rsplit("refs/tags/", 1)[1] for line in _git(folder, "ls-remote", "--tags", "origin").splitlines()
            if "refs/tags/" in line and not line.endswith("^{}")]
    return [t for t in tags if re.fullmatch(r"v?\d+(\.\d+)*", t)]


def latest_tag(folder: Path, prefix: str) -> str:
    """prefix 로 시작하는 꼬리표 중 가장 높은 판(정식 판만 — 1.2.3 처럼 숫자로만 된 것)."""
    tags = [t for t in release_tags(folder) if t.startswith(prefix)]
    if not tags:
        raise RuntimeError(f"'{prefix}' 로 시작하는 정식 판 꼬리표가 없습니다")
    return max(tags, key=_version)


def fetch(repo: str, ref: str, cache: Path, track: str = "") -> tuple[Path, str, str]:
    """(저장소 폴더, 기록한 버전의 커밋, 최신 커밋). 얕게 받는다 — 두 커밋의 파일만 비교하면 되므로 역사는 필요 없다.
    track 이 'tag:접두' 면 최신을 기본 가지가 아니라 그 접두의 가장 높은 정식 판으로 본다(OpenCode 는 기본 가지가 다음 판 개발 중이라
    배포판은 v1.x 꼬리표다 — 2026-10-27 확인)."""
    folder = cache / re.sub(r"[^A-Za-z0-9_.-]+", "_", repo.removeprefix("https://"))
    if not (folder / ".git").is_dir():
        folder.mkdir(parents=True, exist_ok=True)
        _git(folder, "init", "-q")
        _git(folder, "remote", "add", "origin", repo)
    is_sha = re.fullmatch(r"[0-9a-f]{40}", ref) is not None
    _git(folder, "fetch", "-q", "--depth", "1", "origin", ref if is_sha else f"refs/tags/{ref}:refs/tags/{ref}")
    old = _git(folder, "rev-parse", f"{ref}^{{commit}}").strip()
    if track.startswith("tag:"):
        tag = latest_tag(folder, track[4:])
        _git(folder, "fetch", "-q", "--depth", "1", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
        new = _git(folder, "rev-parse", f"{tag}^{{commit}}").strip()
    else:
        _git(folder, "fetch", "-q", "--depth", "1", "origin", "HEAD")
        new = _git(folder, "rev-parse", "FETCH_HEAD^{commit}").strip()
    return folder, old, new


def compare(folder: Path, old: str, new: str, files: list[str]) -> list[dict]:
    """흡수한 원본 파일마다 바뀜(+/− 줄)·사라짐·그대로."""
    rows = []
    present = set(_git(folder, "ls-tree", "-r", "--name-only", new).splitlines())
    for path in files:
        if path not in present:
            rows.append({"file": path, "state": "missing"})   # 옮겨졌거나 지워졌다 — 사람이 새 자리를 찾는다
            continue
        stat = _git(folder, "diff", "--numstat", old, new, "--", path).strip()
        if not stat:
            rows.append({"file": path, "state": "same"})
            continue
        added, removed, _name = stat.split("\t", 2)
        rows.append({"file": path, "state": "changed", "added": int(added) if added.isdigit() else 0,
                     "removed": int(removed) if removed.isdigit() else 0})
    return rows


def upstream_report(manifest: dict, cache: Path, fetcher=fetch) -> list[dict]:
    out = []
    for source in manifest.get("upstreams", []):
        if source.get("kind") != "git":
            out.append({"name": source["name"], "kind": source.get("kind"), "note": "저장소가 아니라 자동 비교 없음 — 사람이 확인"})
            continue
        files = sorted({f for part in source["parts"] for f in part.get("upstream", [])})
        try:
            expected = source.get("commit", "")
            if not re.fullmatch(r"[0-9a-f]{40}", expected):
                raise RuntimeError("기준 commit 에 고정된 40자리 SHA 가 필요합니다")
            folder, old, new = fetcher(source["repo"], source["ref"], cache, source.get("track", ""))
            if old != expected:
                raise RuntimeError(f"기준 ref 의 SHA 불일치: 기록 {expected}, 조회 {old} — 기준을 자동으로 옮기지 않습니다")
            rows = compare(folder, old, new, files)
            latest, latest_release = "", ""
            if source.get("track", "").startswith("tag:"):
                prefix = source["track"][4:]
                pointed = [t for t in _git(folder, "tag", "--points-at", new).splitlines()
                           if t.startswith(prefix) and re.fullmatch(r"v?\d+(\.\d+)*", t)]
                latest = max(pointed, key=_version)
                latest_release = max(release_tags(folder), key=_version)
        except Exception as exc:  # noqa: BLE001 — 한 원본을 못 받아도 나머지는 보고한다
            out.append({"name": source["name"], "kind": "git", "checked": source.get("checked", ""), "error": str(exc)[:300]})
            continue
        by_file = {r["file"]: r for r in rows}
        parts = [{"id": p["id"], "status": p["status"], "alex": p.get("alex", []),
                  "changes": [by_file[f] for f in p.get("upstream", []) if by_file[f]["state"] != "same"]}
                 for p in source["parts"]]
        out.append({"name": source["name"], "kind": "git", "ref": source["ref"], "old": old, "new": new,
                    "latest": latest, "latest_release": latest_release,
                    "release_outside_track": bool(latest_release and _version(latest_release) > _version(latest)),
                    "checked": source.get("checked", ""), "moved": old != new, "parts": parts})
    return out


def size_report(root: Path = ROOT) -> dict:
    """덩치 — 코드·화면·시험 줄 수, 슬래시 명령·설정 수."""
    def lines(paths) -> int:
        return sum(len(p.read_text(encoding="utf-8", errors="replace").splitlines()) for p in paths)
    sys.path.insert(0, str(root))
    from jarvis.app import HELP_TEXT
    from jarvis.config import Settings
    fields = dataclasses.fields(Settings)
    return {"python_lines": lines(p for p in (root / "jarvis").rglob("*.py") if "__pycache__" not in p.parts),
            "screen_lines": lines(p for p in (root / "jarvis" / "static").glob("*") if p.suffix in (".js", ".html", ".css")),
            "test_lines": lines((root / "tests").rglob("*.py")),
            "commands": sum(1 for line in HELP_TEXT.splitlines() if line.startswith("  /")),
            "settings": len(fields),
            "default_off_flags": sum(1 for f in fields if f.type in ("bool", bool) and f.default is False)}


def pending(manifest: dict, today: date) -> dict:
    """결론 안 난 실험·같은 일의 여러 길 — 정한 날이 지났으면 overdue."""
    def mark(item: dict) -> dict:
        due = item.get("decide_by", "")
        return {**item, "overdue": bool(due) and date.fromisoformat(due) < today}
    return {"evaluation_policy": manifest.get("evaluation_policy", {}),
            "experiments": [mark(e) for e in manifest.get("experiments", [])],
            "duplicate_paths": [mark(d) for d in manifest.get("duplicate_paths", [])]}


def markdown(upstreams: list[dict] | None, size: dict, todo: dict, today: date) -> str:
    lines = [f"# 흡수 원본·덩치 점검 ({today.isoformat()})", ""]
    if upstreams is not None:
        lines.append("## 원본 변경")
        for source in upstreams:
            if "error" in source:
                lines.append(f"- **{source['name']}**: 받지 못함·검증 실패 — {source['error']} (마지막 검토: {source.get('checked') or '미기록'})")
                continue
            if source.get("kind") != "git":
                lines.append(f"- {source['name']}({source.get('kind')}): {source['note']}")
                continue
            changed = [p for p in source["parts"] if p["changes"]]
            head = (f"- **{source['name']}** {source['ref']} → 최신 {source.get('latest') or source['new'][:10]}: "
                    + ("원본이 그대로" if not source["moved"] else f"흡수한 부분 {len(changed)}/{len(source['parts'])}곳 바뀜"))
            lines.append(head)
            lines.append(f"  - 마지막 판정 기록: {source.get('checked') or '미기록'} · 조회 성공은 반영 판정을 뜻하지 않음")
            if source.get("release_outside_track"):
                lines.append(f"  - **추적 범위 밖 새 정식 태그 {source['latest_release']}** — 릴리스·보안 공지와 파일 위치를 검토해야 함")
            for part in changed:
                detail = ", ".join(f"{c['file'].rsplit('/', 1)[-1]} " + (f"+{c['added']}/−{c['removed']}" if c["state"] == "changed"
                                                                          else "사라짐(옮겨졌을 수 있음)") for c in part["changes"])
                lines.append(f"  - [{part['status']}] {part['id']} → ALEX {', '.join(part['alex'])}: {detail}")
        lines.append("")
    lines += ["## 덩치", f"- 파이썬 {size['python_lines']:,}줄 · 화면 {size['screen_lines']:,}줄 · 시험 {size['test_lines']:,}줄",
              f"- 슬래시 명령 {size['commands']}개 · 설정 {size['settings']}개(기본 꺼짐 켜고 끄기 {size['default_off_flags']}개)", ""]
    lines.append("## 결론 안 난 실험(정한 날까지 측정 → 기본으로 만들고 옛 길 지우기, 또는 이 기능 지우기)")
    policy = todo.get("evaluation_policy", {})
    if policy:
        lines.append(f"- 측정 조건: {policy['controls']}")
        lines.append(f"- 안전·품질: {policy['quality_gate']}")
        lines.append(f"- 결론 불충분: {policy['inconclusive']}")
    for item in todo["experiments"]:
        lines.append(f"- {'**지남** ' if item['overdue'] else ''}`{item['setting']}` ({item['adr']}, {item['decide_by']}까지): {item['measure']}")
        if item.get("accept"):
            lines.append(f"  - 채택 기준(제안): {item['accept']}")
    lines.append("")
    lines.append("## 같은 일을 하는 여러 길")
    for item in todo["duplicate_paths"]:
        lines.append(f"- {'**지남** ' if item['overdue'] else ''}{item['job']} ({item['decide_by']}까지): " + " · ".join(item["paths"]))
        lines.append(f"  - 정하는 법: {item['measure']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true", help="원본을 받지 않고 덩치·실험만")
    parser.add_argument("--manifest", default=str(MANIFEST))
    parser.add_argument("--cache", default=str(CACHE))
    parser.add_argument("--out", default="", help="보고서(Markdown) 저장")
    parser.add_argument("--json", default="", help="원자료(JSON) 저장")
    args = parser.parse_args(argv)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    today = date.today()
    upstreams = None if args.offline else upstream_report(manifest, Path(args.cache))
    size, todo = size_report(), pending(manifest, today)
    text = markdown(upstreams, size, todo, today)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps({"date": today.isoformat(), "upstreams": upstreams, "size": size, **todo},
                                              ensure_ascii=False, indent=1), encoding="utf-8")
    return int(any("error" in source for source in upstreams or []))


if __name__ == "__main__":
    raise SystemExit(main())
