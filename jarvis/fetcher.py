"""말로 내려받기(2026-10-22, ADR-104) — '이 파일 내려받아줘 https://…' 또는 /download 주소 [--yes].

권한 원칙(2026-10-18 합의): 내려받기는 정해진 다운로드 폴더(data/downloads)에만. 실행 파일·설치 파일과 아주 큰 파일은
권한 단계와 상관없이 사용자 승인 뒤에만 받고, 받은 파일은 **절대 실행하지 않는다**. 읽기 전용 대화에서는 하지 않는다.
- http·https 만. 이 PC·내부망 주소(localhost·10.x·192.168.x·169.254.x …)는 받지 않는다.
- 이름은 Content-Disposition 또는 주소 끝에서, 위험한 글자를 빼고 같은 이름이 있으면 (2)·(3) 을 붙인다(덮어쓰지 않음).
- 받는 동안은 .part 로 두고, 끝나면 이름을 바꾼다. 중단·실패하면 .part 를 지운다. SHA-256 을 함께 알려 준다.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import socket
import threading
import time
from typing import Callable
import urllib.parse
import urllib.request

from .providers import GenerationCancelled

EXECUTABLE = {".exe", ".msi", ".msix", ".appx", ".bat", ".cmd", ".com", ".scr", ".ps1", ".psm1", ".vbs", ".vbe", ".js",
              ".jse", ".wsf", ".wsh", ".hta", ".jar", ".dll", ".sys", ".lnk", ".reg", ".cpl", ".msc", ".sh", ".py", ".pyw"}
EXECUTABLE_TYPES = ("application/x-msdownload", "application/x-msi", "application/vnd.microsoft.portable-executable",
                    "application/x-dosexec", "application/java-archive")
ASK_ABOVE = 1_000_000_000          # 1GB 넘으면 승인 뒤에(모델 같은 큰 파일 — '설치·모델 다운로드는 승인 후')
HARD_LIMIT = 20_000_000_000        # 20GB — 그보다 크면 받지 않는다
CHUNK = 1 << 20
REQUEST = re.compile(r"(내려\s*받|다운\s*로드|다운\s*받|받아\s*(?:줘|주세요|줄래|놔|둬|와))", re.I)
URL = re.compile(r"https?://[^\s<>\"'`)\]]+", re.I)


class DownloadError(ValueError):
    pass


@dataclass
class Download:
    path: Path
    size: int
    sha256: str
    content_type: str
    seconds: float


def download_request(text: str) -> str | None:
    """'내려받아줘·다운로드해줘·받아줘' 와 주소가 함께 있으면 그 주소."""
    url = URL.search(text)
    return url.group(0).rstrip(".,;") if url and REQUEST.search(text) else None


def check_url(url: str, resolve: Callable[[str], list[str]] | None = None) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise DownloadError("http·https 주소만 받을 수 있습니다.")
    host = parsed.hostname
    lookup = resolve or (lambda name: [info[4][0] for info in socket.getaddrinfo(name, None)])
    try:
        addresses = lookup(host)
    except OSError as exc:
        raise DownloadError(f"주소를 찾지 못했습니다: {host}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise DownloadError(f"이 PC·내부망 주소({host})는 받지 않습니다.")
    return parsed


def safe_name(raw: str) -> str:
    name = urllib.parse.unquote(raw).replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .")
    if not name or name in (".", ".."):
        name = "download"
    stem, dot, suffix = name.rpartition(".")
    return (stem[:150] + dot + suffix[:20]) if dot else name[:170]


def is_executable(name: str, content_type: str = "") -> bool:
    return Path(name).suffix.lower() in EXECUTABLE or any(kind in content_type.lower() for kind in EXECUTABLE_TYPES)


def _name_from(response, parsed: urllib.parse.ParseResult) -> str:
    disposition = response.headers.get("Content-Disposition", "")
    match = re.search(r"filename\*=(?:UTF-8'')?([^;]+)|filename=\"?([^\";]+)\"?", disposition, flags=re.I)
    if match:
        return safe_name(match.group(1) or match.group(2))
    return safe_name(parsed.path or parsed.hostname or "download")


def _unique(folder: Path, name: str) -> Path:
    path = folder / name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while path.exists() or path.with_name(path.name + ".part").exists():
        path = folder / f"{stem} ({n}){suffix}"
        n += 1
    return path


def inspect(url: str, timeout: float = 30, resolve=None, opener=urllib.request.urlopen) -> tuple[str, int, str]:
    """(이름, 크기(모르면 -1), 형식) — 받기 전에 승인이 필요한지 보려고."""
    parsed = check_url(url, resolve)
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "ALEX/1.0 (local assistant)"})
    try:
        with opener(request, timeout=timeout) as response:
            check_url(response.geturl(), resolve)  # 다른 곳으로 넘겨졌으면 그 주소도 확인
            return (_name_from(response, urllib.parse.urlparse(response.geturl())),
                    int(response.headers.get("Content-Length") or -1), response.headers.get("Content-Type", ""))
    except DownloadError:
        raise
    except Exception:  # noqa: BLE001 — HEAD 를 막는 서버가 많다. 이름은 주소에서, 크기는 모름
        return safe_name(parsed.path or parsed.hostname or "download"), -1, ""


def fetch(url: str, folder: Path, *, cancel_event: threading.Event | None = None,
          progress: Callable[[str], None] | None = None, allow_executable: bool = False, allow_large: bool = False,
          timeout: float = 60, resolve=None, opener=urllib.request.urlopen) -> Download:
    say = progress or (lambda _text: None)
    check_url(url, resolve)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": "ALEX/1.0 (local assistant)"})
    with opener(request, timeout=timeout) as response:
        final = urllib.parse.urlparse(response.geturl())
        check_url(response.geturl(), resolve)
        name = _name_from(response, final)
        kind = response.headers.get("Content-Type", "")
        total = int(response.headers.get("Content-Length") or -1)
        if is_executable(name, kind) and not allow_executable:
            raise DownloadError(f"실행 파일({name})이라 승인 없이 받지 않습니다.")
        if total > HARD_LIMIT:
            raise DownloadError(f"{total / 1e9:.1f}GB — {HARD_LIMIT // 1_000_000_000}GB 보다 큰 파일은 받지 않습니다.")
        if total > ASK_ABOVE and not allow_large:
            raise DownloadError(f"{total / 1e9:.1f}GB 큰 파일이라 승인 없이 받지 않습니다.")
        target = _unique(folder, name)
        partial = target.with_name(target.name + ".part")
        digest, size, next_note = hashlib.sha256(), 0, time.monotonic() + 5
        try:
            with partial.open("xb") as handle:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise GenerationCancelled("내려받기를 중단했습니다.")
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > HARD_LIMIT or (size > ASK_ABOVE and not allow_large):
                        raise DownloadError("받는 도중 크기 한도를 넘어 멈췄습니다.")
                    digest.update(chunk)
                    handle.write(chunk)
                    if time.monotonic() >= next_note:
                        share = f" ({size * 100 // total}%)" if total > 0 else ""
                        say(f"[내려받는 중 {size / 1e6:,.0f}MB{share}]\n")
                        next_note += 5
            os.replace(partial, target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    return Download(target, size, digest.hexdigest(), kind, round(time.monotonic() - started, 1))


DOWNLOAD_NAME = re.compile(r"[^/\\\x00-\x1f]{1,200}")


def downloaded_path(folder: Path, name: str) -> Path | None:
    """화면에서 받은 파일을 열 때 — 다운로드 폴더 바로 아래의 실제 파일만."""
    if not DOWNLOAD_NAME.fullmatch(name) or name in (".", "..") or name.endswith(".part"):
        return None
    base = Path(folder).resolve()
    path = base / name
    if not path.is_file() or path.is_symlink() or path.resolve().parent != base:
        return None
    return path
