"""Gemini 검토용 단일 패킷을 만든다.

사용법:
    python scripts/make_review_packet.py            # REVIEW_PACKET.md 생성
    python scripts/make_review_packet.py --out x.md

목적:
Gemini 에게 저장소를 통째로 올릴 수 없을 때, 검토 요청서 + 설계 문서 +
전체 소스 + 테스트 결과를 하나의 Markdown 으로 합쳐 붙여넣을 수 있게 한다.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent

# 순서가 곧 Gemini 가 읽는 순서다. 검토 기준 → 설계 → 코드 → 테스트.
DOC_ORDER = [
    "GEMINI_REVIEW_REQUEST.md",
    "PROJECT_JARVIS.md",
    "GPT_CLAUDE_IMPLEMENTATION_HANDOFF.md",
    "README.md",
    "RELEASE_NOTES.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "CLAUDE_NEXT.md",
    "GPT_NEXT.md",
    "DECISIONS.md",
    "TODO.md",
    "TEST_RESULTS.md",
    "HARDWARE_VALIDATION.md",
    "STUDY_GUIDE.md",
]

CODE_GLOBS = ["jarvis/*.py", "tests/*.py", "scripts/*.py"]

LANG_BY_SUFFIX = {".py": "python", ".md": "markdown", ".txt": "text"}


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return f"(파일을 읽지 못했습니다: {exc})"


def run_tests() -> str:
    """검토 시점의 전체 release verification 결과를 함께 담는다."""
    try:
        result = subprocess.run(
            [sys.executable, "scripts/verify_release.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            return f"[RELEASE VERIFICATION FAILED rc={result.returncode}]\n{output}"
        return output
    except (subprocess.SubprocessError, OSError) as exc:
        return f"(release verification을 실행하지 못했습니다: {exc})"


def build() -> str:
    parts: list[str] = [
        "# Project JARVIS — Gemini Review Packet",
        "",
        "이 문서는 `scripts/make_review_packet.py` 가 자동 생성했다.",
        "아래 순서로 읽고 `GEMINI_REVIEW_REQUEST.md` 의 A~I 형식으로 리뷰한다.",
        "",
        "---",
        "",
        "# PART 1 — 문서",
        "",
    ]

    for name in DOC_ORDER:
        path = ROOT / name
        if not path.exists():
            continue
        parts += [f"## FILE: {name}", "", read(path), "", "---", ""]

    parts += ["# PART 2 — 전체 소스 코드", ""]
    for pattern in CODE_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            if path.name == "__init__.py" and path.stat().st_size < 120:
                continue
            rel = path.relative_to(ROOT)
            lang = LANG_BY_SUFFIX.get(path.suffix, "")
            parts += [f"## FILE: {rel}", "", f"```{lang}", read(path), "```", ""]

    parts += [
        "---",
        "",
        "# PART 3 — 패킷 생성 시점 전체 Release Verification",
        "",
        "```text",
        run_tests(),
        "```",
        "",
        "---",
        "",
        "# PART 4 — Gemini 에게",
        "",
        "위 내용만으로 판단한다. 추측한 부분은 추측이라고 명시한다.",
        "리뷰는 `GEMINI_REVIEW_REQUEST.md` 의 A~I 형식을 그대로 따른다.",
        "I 항목(수정 지시)은 GPT 또는 Claude 가 바로 실행할 수 있는 체크리스트로 쓴다.",
        "",
    ]
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini 검토용 단일 Markdown 패킷 생성")
    parser.add_argument("--out", default="REVIEW_PACKET.md", help="출력 파일 경로")
    args = parser.parse_args()

    output_path = ROOT / args.out
    content = build()
    output_path.write_text(content, encoding="utf-8")
    print(f"생성 완료: {output_path}")
    print(f"크기: {len(content):,} chars / 약 {len(content)//3:,} tokens (한국어 기준 대략치)")


if __name__ == "__main__":
    main()
