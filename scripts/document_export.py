"""Export existing UTF-8 text without starting ALEX, Ollama or a GPU model."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.documents import DocumentError, export_document, MAX_TEXT


def main() -> int:
    parser = argparse.ArgumentParser(description="CPU-only ALEX document export")
    parser.add_argument("--format", required=True, choices=("xlsx", "pdf", "pptx", "docx"))
    parser.add_argument("--text-file", required=True, type=Path)
    parser.add_argument("--title", default="ALEX 문서")
    parser.add_argument("--data-dir", default=ROOT / "data", type=Path)
    args = parser.parse_args()
    try:
        if args.text_file.stat().st_size > MAX_TEXT * 4:
            raise DocumentError("입력 파일이 너무 큽니다. 내용을 나누어 주세요.")
        text = args.text_file.read_text(encoding="utf-8-sig")
        path = export_document(args.data_dir, args.format, text, args.title)
    except (DocumentError, OSError, UnicodeError) as exc:
        print(f"문서 저장 실패: {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
