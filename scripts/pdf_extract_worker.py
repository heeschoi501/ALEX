"""Optional pypdf worker. Called with JSON over stdin, never runs PDF actions/scripts."""
from __future__ import annotations

import json
import sys

from pypdf import PdfReader


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        request = json.load(sys.stdin)
        reader = PdfReader(request["path"])
        if reader.is_encrypted:
            raise ValueError("암호화된 PDF입니다. 암호를 해제한 사본을 사용하세요.")
        if not 0 < len(reader.pages) <= 400:
            raise ValueError("PDF는 최대 400페이지까지 저장할 수 있습니다.")
        pages, total = [], 0
        for number, page in enumerate(reader.pages, 1):
            content = page.get_contents()
            if content is not None and len(content.get_data()) > 5_000_000:
                raise ValueError(f"{number}페이지의 내용이 너무 큽니다. PDF를 나눠 주세요.")
            text = page.extract_text() or ""
            total += len(text)
            if total > 600_000:
                raise ValueError("추출 본문이 60만 자를 넘습니다. 자료를 나눠 주세요.")
            pages.append({"page": number, "text": text})
        json.dump({"pages": pages}, sys.stdout, ensure_ascii=False)
        return 0
    except Exception as exc:
        json.dump({"error": str(exc)[:500]}, sys.stdout, ensure_ascii=False)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
