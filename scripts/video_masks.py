"""영상 속 대상 바꾸기의 '영역만 다시 그리기' 준비(ADR-099 보충, 2026-10-19) — 따로 둔 환경(C:\\ALEX\\tools\\video\\matting-venv,
rembg birefnet-general-lite, CPU)에서 실행한다. ALEX 본체는 표준 라이브러리만 쓴다(dependency_audit).

    python video_masks.py frames  장면폴더 출력폴더 [넓힘=25] [간격=2]
        장면폴더의 PNG 마다 주인공 영역을 따서 출력폴더/control(원본 + 그 영역만 회색 = VACE 의 빈칸)·mask(그 영역 흰색)에
        같은 이름으로 쓴다. 얼굴선 같은 원래 주인공의 모양은 넘기지 않는다 — 새 얼굴은 참고 사진을 따르게.
        간격 n 이면 n 장에 한 번만 따고 사이는 앞 마스크(넓혀 둔)를 쓴다(장면당 약 3.7초 → 절반).
    python video_masks.py reference 사진 출력.jpg 가로 세로
        참고 사진에서 주인공만 남기고 흰 바탕 가운데에 놓는다(사진의 바닥·목줄이 영상에 섞이지 않게).
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps
from rembg import new_session, remove

MODEL = "birefnet-general-lite"


def frames(source: Path, out: Path, grow: int = 25, step: int = 2) -> int:
    session = new_session(MODEL)
    (out / "control").mkdir(parents=True, exist_ok=True)
    (out / "mask").mkdir(parents=True, exist_ok=True)
    paths = sorted(source.glob("*.png"))
    mask = None
    for n, path in enumerate(paths):
        image = Image.open(path).convert("RGB")
        if mask is None or n % step == 0:
            alpha = remove(image, session=session, only_mask=True)
            mask = alpha.point(lambda a: 255 if a > 60 else 0).filter(ImageFilter.MaxFilter(grow | 1))
        Image.composite(Image.new("RGB", image.size, (128, 128, 128)), image, mask).save(out / "control" / path.name)
        mask.convert("RGB").save(out / "mask" / path.name)
        if n % 20 == 0:
            print(f"{n}/{len(paths)}", flush=True)
    return len(paths)


def reference(photo: Path, out: Path, width: int, height: int) -> None:
    image = ImageOps.exif_transpose(Image.open(photo)).convert("RGB")
    image.thumbnail((1400, 1400))
    cut = remove(image, session=new_session(MODEL))
    box = cut.getchannel("A").point(lambda a: 255 if a > 40 else 0).getbbox()
    if box:
        cut = cut.crop(box)
    subject = Image.new("RGB", cut.size, "white")
    subject.paste(cut, mask=cut.getchannel("A"))
    subject.thumbnail((int(width * 0.92), int(height * 0.92)))
    canvas = Image.new("RGB", (width, height), "white")
    canvas.paste(subject, ((width - subject.width) // 2, (height - subject.height) // 2))
    canvas.save(out, quality=95)


if __name__ == "__main__":
    if sys.argv[1] == "frames":
        count = frames(Path(sys.argv[2]), Path(sys.argv[3]), *(int(v) for v in sys.argv[4:6]))
        print(f"done {count}")
    elif sys.argv[1] == "reference":
        reference(Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]))
        print("done")
    else:
        raise SystemExit("사용법: video_masks.py frames|reference …")
