"""Optional HWP 5.0 worker(2026-10-22, ADR-109). JSON {"path": …} over stdin → {"pages": [{"page", "text"}]}.

한글(HWP 5.x) 파일은 OLE 복합 문서다. olefile 로 FileHeader 와 BodyText/Section* 스트림만 읽는다 — 매크로(Scripts)·그림
(BinData)·OLE 개체는 읽지도 실행하지도 않는다. 본문은 레코드(태그 10비트·수준 10비트·크기 12비트) 묶음이고, 문단 글자는
HWPTAG_PARA_TEXT(67)의 UTF-16LE 다. 쪽은 구역과 문단 머리(HWPTAG_PARA_HEADER, 66)의 쪽·구역 나누기로 센다(자동 쪽 넘김은
파일에 없어 실제 쪽 수보다 적을 수 있다). 표 칸의 문단도 순서대로 나온다(칸 구분 없음).
배포용(읽기 전용) 문서는 ViewText/Section* 이 AES-128(ECB)로 잠겨 있다 — 첫 레코드(DISTRIBUTE_DOC_DATA 256바이트)를
MSVC rand() 로 만든 수열과 XOR 해 열쇠를 꺼내 푼다(한컴 공개 문서 'HWP 5.0 배포용 문서', hahnlee/hwp-rs 구현과 같은 방식).
형식: 한컴 'HWP 5.0 문서 파일 형식' 공개 문서(revision 1.3). 시험 파일: hahnlee/hwp-rs(Apache-2.0)의 공개 시험 문서.
"""
from __future__ import annotations

import json
import re
import struct
import sys
import zlib

import olefile
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PARA_HEADER, PARA_TEXT, DISTRIBUTE_DOC_DATA = 66, 67, 28
# 제어 문자(0~31) 중 자기 포함 8글자(16바이트)를 차지하는 것 — 인라인·확장 제어(표·그림·각주·필드 등)
WIDE_CONTROLS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}
MAX_SECTION_BYTES = 80_000_000  # 압축을 푼 구역 하나의 상한(작은 파일이 거대하게 풀리는 것을 막는다)
MAX_TEXT = 600_000


def records(data: bytes):
    position = 0
    while position + 4 <= len(data):
        (header,) = struct.unpack_from("<I", data, position)
        position += 4
        tag, size = header & 0x3FF, header >> 20
        if size == 0xFFF:
            (size,) = struct.unpack_from("<I", data, position)
            position += 4
        yield tag, data[position:position + size]
        position += size


def paragraph_text(payload: bytes) -> str:
    out = bytearray()
    units = len(payload) // 2
    index = 0
    while index < units:
        code = payload[2 * index] | payload[2 * index + 1] << 8
        if code < 32:
            if code in WIDE_CONTROLS:
                if code == 9:
                    out += "\t".encode("utf-16-le")
                index += 8
                continue
            if code == 10:
                out += "\n".encode("utf-16-le")
            elif code in (24,):
                out += "-".encode("utf-16-le")
            elif code in (30, 31):
                out += " ".encode("utf-16-le")
            index += 1
            continue
        out += payload[2 * index:2 * index + 2]
        index += 1
    return out.decode("utf-16-le", errors="replace")


def distribution_plain(raw: bytes) -> bytes:
    """배포용 문서 구역 → 풀린 바이트(압축은 그대로). 첫 레코드에서 열쇠를 꺼내 나머지를 AES-128 ECB 로 푼다."""
    records_iter = records(raw)
    tag, data = next(records_iter, (None, b""))
    if tag != DISTRIBUTE_DOC_DATA or len(data) != 256:
        raise ValueError("배포용 한글 문서의 형식을 읽지 못했습니다. 한글에서 PDF 로 저장해 올려 주세요.")
    (seed,) = struct.unpack_from("<I", data, 0)
    state, mask, index = seed, bytearray(256), 0

    def rand() -> int:
        nonlocal state
        state = (state * 214013 + 2531011) & 0xFFFFFFFF
        return (state >> 16) & 0x7FFF
    while index < 256:
        fill, times = rand() & 0xFF, (rand() & 0x0F) + 1
        for _ in range(times):
            if index == 256:
                break
            mask[index] = fill
            index += 1
    plain = bytes(a ^ b for a, b in zip(data, mask))
    offset = (seed & 0x0F) + 4
    key = plain[offset:offset + 16]
    start = 4 + 256  # 레코드 머리(4바이트) + 256바이트
    body = raw[start:start + (len(raw) - start) // 16 * 16]
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return decryptor.update(body) + decryptor.finalize()


def section_number(entry: list[str]) -> int:
    match = re.fullmatch(r"Section(\d+)", entry[-1])
    return int(match.group(1)) if match else -1


def read(path: str) -> list[dict]:
    if not olefile.isOleFile(path):
        raise ValueError("한글(HWP) 파일 형식이 아닙니다. HWP 3.0 이하 옛 파일은 한글에서 새 형식이나 PDF 로 저장해 주세요.")
    with olefile.OleFileIO(path) as ole:
        if not ole.exists("FileHeader"):
            raise ValueError("한글(HWP) 파일 형식이 아닙니다.")
        header = ole.openstream("FileHeader").read(256)
        if not header.startswith(b"HWP Document File"):
            raise ValueError("한글(HWP) 5.0 형식이 아닙니다. 한글에서 다시 저장하거나 PDF 로 저장해 주세요.")
        (properties,) = struct.unpack_from("<I", header, 36)
        if properties & 0x2:
            raise ValueError("암호가 걸린 한글 문서입니다. 암호를 푼 사본을 올려 주세요.")
        distribution = bool(properties & 0x4)
        compressed = bool(properties & 0x1)
        storage = "ViewText" if distribution else "BodyText"
        sections = sorted((entry for entry in ole.listdir() if len(entry) == 2 and entry[0] == storage
                           and section_number(entry) >= 0), key=section_number)
        if not sections:
            raise ValueError("한글 문서에서 본문을 찾지 못했습니다.")
        pages: list[list[str]] = []
        total = 0
        for entry in sections:
            raw = ole.openstream(entry).read()
            if distribution:
                raw = distribution_plain(raw)
            if compressed:
                inflater = zlib.decompressobj(-zlib.MAX_WBITS)
                raw = inflater.decompress(raw, MAX_SECTION_BYTES)
                if inflater.unconsumed_tail:
                    raise ValueError("한글 문서의 본문이 너무 큽니다.")
            pages.append([])
            for tag, payload in records(raw):
                if tag == PARA_HEADER and len(payload) >= 12 and payload[11] & 0x05 and pages[-1]:
                    pages.append([])  # 구역(0x01)·쪽(0x04) 나누기
                elif tag == PARA_TEXT:
                    text = paragraph_text(payload).rstrip()
                    total += len(text)
                    if total > MAX_TEXT:
                        raise ValueError("문서의 글자가 너무 많습니다(60만 자 이하). 나눠서 올려 주세요.")
                    pages[-1].append(text)
        pages = [lines for lines in pages if any(line.strip() for line in lines)] or [[]]
        if len(pages) > 400:  # 쪽 나누기가 아주 많으면 이어 붙여 400쪽 안으로
            size = -(-len(pages) // 400)
            pages = [sum(pages[n:n + size], []) for n in range(0, len(pages), size)]
        return [{"page": number, "text": "\n".join(lines).strip()} for number, lines in enumerate(pages, 1)]


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        request = json.load(sys.stdin)
        json.dump({"pages": read(request["path"])}, sys.stdout, ensure_ascii=False)
        return 0
    except Exception as exc:  # noqa: BLE001 — 사용자에게 보일 한 줄
        json.dump({"error": str(exc)[:500]}, sys.stdout, ensure_ascii=False)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
