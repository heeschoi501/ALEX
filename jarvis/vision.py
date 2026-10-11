"""Local screenshot attachments, independent of the image generation service."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import secrets
import struct

from .imagegen import IMAGE_TYPES, MAX_UPLOAD_BYTES, image_kind

MAX_ATTACHMENTS = 3
MAX_PIXELS = 32_000_000
ATTACHMENT_NAME = re.compile(r"[a-f0-9]{24}\.(?:png|jpg|webp)\Z")
ATTACHMENT_LINE = re.compile(r"^!\[([^\]\n]{0,120})\]\(alex-attachment:([a-f0-9]{24}\.(?:png|jpg|webp))\)$", re.M)


def image_dimensions(data: bytes, kind: str) -> tuple[int, int]:
    """Read bounded format headers without adding a runtime dependency."""
    if kind == ".png" and len(data) >= 33 and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])
    if kind == ".jpg":
        at = 2
        while at + 4 <= len(data):
            if data[at] != 0xFF:
                break
            while at < len(data) and data[at] == 0xFF:
                at += 1
            if at >= len(data):
                break
            marker = data[at]
            at += 1
            if marker in (0xD9, 0xDA):
                break
            if marker == 0x01 or 0xD0 <= marker <= 0xD7:
                continue
            if at + 2 > len(data):
                break
            size = int.from_bytes(data[at:at+2], "big")
            if size < 2 or at + size > len(data):
                break
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF) and size >= 8:
                height, width = struct.unpack(">HH", data[at+3:at+7])
                return width, height
            at += size
    if kind == ".webp":
        at = 12
        while at + 8 <= len(data):
            chunk = data[at:at+4]
            size = int.from_bytes(data[at+4:at+8], "little")
            payload = at + 8
            if payload + size > len(data):
                break
            if chunk == b"VP8X" and size >= 10:
                return (1 + int.from_bytes(data[payload+4:payload+7], "little"),
                        1 + int.from_bytes(data[payload+7:payload+10], "little"))
            if chunk == b"VP8L" and size >= 5 and data[payload] == 0x2F:
                bits = int.from_bytes(data[payload+1:payload+5], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if chunk == b"VP8 " and size >= 10 and data[payload+3:payload+6] == b"\x9d\x01\x2a":
                width, height = struct.unpack("<HH", data[payload+6:payload+10])
                return width & 0x3FFF, height & 0x3FFF
            at = payload + size + (size & 1)
    raise ValueError("이미지 크기를 읽지 못했습니다. PNG·JPEG·WebP 파일을 다시 저장해 주세요.")


@dataclass(frozen=True)
class ImageAttachment:
    name: str
    mime: str
    width: int
    height: int
    data: bytes

    def data_url(self) -> str:
        return f"data:{self.mime};base64," + base64.b64encode(self.data).decode("ascii")

    def marker(self) -> str:
        return f"![첨부 이미지 {self.width}×{self.height}](alex-attachment:{self.name})"


class ImageAttachmentStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def _folder(self, session_id: str) -> Path:
        key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
        folder = self.root / key
        if folder.resolve().parent != self.root:
            raise ValueError("첨부 이미지 저장 경로가 잘못되었습니다.")
        return folder

    @staticmethod
    def _decode(name: str, data: bytes) -> ImageAttachment:
        if not data or len(data) > MAX_UPLOAD_BYTES:
            raise ValueError("이미지는 파일당 10MB 이하로 보내 주세요.")
        kind = image_kind(data)
        if kind is None:
            raise ValueError("PNG·JPEG·WebP 이미지만 첨부할 수 있습니다.")
        width, height = image_dimensions(data, kind)
        if not (0 < width <= 16384 and 0 < height <= 16384 and width * height <= MAX_PIXELS):
            raise ValueError("이미지 해상도가 너무 큽니다. 3200만 화소 이하로 줄여 주세요.")
        return ImageAttachment(name, IMAGE_TYPES[kind], width, height, data)

    def store(self, session_id: str, data: bytes, mime: str) -> ImageAttachment:
        image = self._decode("", data)
        if image.mime != mime:
            raise ValueError("파일의 실제 이미지 형식과 전송 형식이 다릅니다.")
        suffix = next(key for key, value in IMAGE_TYPES.items() if value == mime)
        name = secrets.token_hex(12) + suffix
        folder = self._folder(session_id)
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / name).open("xb") as stream:
            stream.write(data)
        return ImageAttachment(name, image.mime, image.width, image.height, data)

    def load(self, session_id: str, name: str) -> ImageAttachment:
        if not isinstance(name, str) or ATTACHMENT_NAME.fullmatch(name) is None:
            raise ValueError("첨부 이미지 이름이 잘못되었습니다.")
        folder = self._folder(session_id)
        path = folder / name
        if path.is_symlink() or path.resolve().parent != folder.resolve() or not path.is_file():
            raise ValueError("이 대화의 첨부 이미지를 찾을 수 없습니다. 다시 첨부해 주세요.")
        with path.open("rb") as stream:
            return self._decode(name, stream.read(MAX_UPLOAD_BYTES + 1))
