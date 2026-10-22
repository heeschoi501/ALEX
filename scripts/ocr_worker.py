"""Optional OCR worker(2026-10-22, ADR-109) for scanned PDF pages. JSON {"path", "pages": [1-based], "dpi"} over stdin
→ {"pages": [{"page", "text"}]}. C:\\ALEX\\tools\\ocr\\venv(rapidocr 3.9.2 + onnxruntime 1.30.0 + pypdfium2 5.13.0)에서 CPU 로만 돈다.

PDF 쪽을 pypdfium2 로 그림으로 그리고(PDF 안의 스크립트·동작은 실행하지 않음) RapidOCR(PP-OCRv5 검출 mobile + 한국어 인식
mobile — 한글·영문·숫자)로 읽는다. 글 방향 분류기는 끈다: 10-22 시험에서 바른 줄을 거꾸로 보고 한 줄을 버렸다(끄면 18/18 줄,
깨끗한 쪽 97.9%·흐리고 기운 쪽 96.6%, 쪽당 1초 안팎). 같은 높이의 글 상자는 한 줄로 이어 붙인다.
"""
from __future__ import annotations

import json
import sys

MAX_SIDE = 2400  # 큰 쪽(포스터·도면)은 긴 변을 이 픽셀 안으로


def lines(boxes, texts) -> list[str]:
    """글 상자를 위→아래로 줄을 묶고 줄 안에서는 왼→오른쪽으로."""
    items = []
    for box, text in zip(boxes, texts):
        ys = [point[1] for point in box]
        xs = [point[0] for point in box]
        items.append((min(ys), max(ys), min(xs), text))
    items.sort(key=lambda item: (item[0], item[2]))
    rows: list[list[tuple]] = []
    for item in items:
        if rows:
            top, bottom = rows[-1][0][0], rows[-1][0][1]
            if item[0] < (top + bottom) / 2:  # 앞 줄의 가운데보다 위에서 시작하면 같은 줄
                rows[-1].append(item)
                continue
        rows.append([item])
    return [" ".join(text for *_, text in sorted(row, key=lambda item: item[2])) for row in rows]


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        request = json.load(sys.stdin)
        import pypdfium2 as pdfium
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

        engine = RapidOCR(params={"Det.ocr_version": OCRVersion.PPOCRV5, "Det.model_type": ModelType.MOBILE,
                                  "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE,
                                  "Rec.lang_type": LangRec.KOREAN, "Global.use_cls": False, "Global.log_level": "error"})
        document = pdfium.PdfDocument(request["path"])
        dpi = int(request.get("dpi") or 200)
        out = []
        for number in request["pages"]:
            if not 1 <= number <= len(document):
                continue
            page = document[number - 1]
            width, height = page.get_size()
            scale = min(dpi / 72, MAX_SIDE / max(width, height, 1))
            image = page.render(scale=scale).to_pil()
            result = engine(image)
            text = "\n".join(lines(result.boxes, result.txts)) if result.txts else ""
            out.append({"page": number, "text": text})
            print(json.dumps({"progress": number}), file=sys.stderr, flush=True)
        json.dump({"pages": out}, sys.stdout, ensure_ascii=False)
        return 0
    except Exception as exc:  # noqa: BLE001 — 사용자에게 보일 한 줄
        json.dump({"error": str(exc)[:500]}, sys.stdout, ensure_ascii=False)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
