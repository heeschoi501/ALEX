"""영상 링크 읽기(2026-10-18, ADR-098) — 사용자 "영상 링크 같은 것도 못 보는데, 볼 수 있도록 못 하나?"

ALEX 는 영상을 '보는' 대신 자막을 읽는다. yt-dlp(C:\\ALEX\\tools\\video, 공식 배포본)로 제목·채널·길이·설명과 자막 목록을
받고(유튜브의 보안 검사 때문에 Deno 가 필요하다), 직접 단 자막 → 원래 말의 자동 자막 → 한국어·영어 순으로 하나 고른다.
자막은 시간 표시([mm:ss])를 남긴 글로 바꿔 그 대화의 문서로 붙인다(ADR-096 과 같은 길) — 요약·질문에 답할 수 있다.

자막이 없거나 짧은 영상(90초 이하 — 릴스·쇼츠는 화면이 내용이다)은 영상을 잠깐 받아 ffmpeg 로 장면을 고르게 뽑고, 대화 모델이
그림으로 본다(같은 날 사용자 "보는 게 가능하도록 수정해야 하지 않나" — 인스타그램 릴스는 자막 0개·설명 8자였다). 받은 영상은
장면을 뽑고 바로 지운다.
자막이 없으면 소리를 받아 적는다(2026-10-22, ADR-108 — whisper.cpp CPU, C:\\ALEX\\tools\\whisper: whisper-cli b5454 +
large-v3-turbo q8_0 + Silero VAD). 말소리 구간만 받아 적어 음악뿐인 영상에서 지어낸 말(ご視聴ありがとうございました)을 막는다.
yt-dlp 는 셸 없이 고정 인자로만 실행하고(사용자 설정 파일 무시), 주소는 '--' 뒤에 둔다.
"""
from __future__ import annotations

from dataclasses import dataclass
import html
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from typing import Callable
import urllib.parse
import urllib.request

from .providers import GenerationCancelled

VIDEO_URL = re.compile(
    r"https?://(?:(?:www|m|music)\.)?(?:youtube\.com/(?:watch\?\S*?v=|shorts/|live/|embed/)[\w-]{11}|youtu\.be/[\w-]{11})\S*"
    r"|https?://(?:www\.)?(?:vimeo\.com/\d+|tv\.naver\.com/v/\d+|dailymotion\.com/video/\w+)\S*"
    r"|https?://(?:www\.)?instagram\.com/(?:[\w.]+/)?(?:reels?|p|tv)/[\w-]+\S*"
    r"|https?://(?:(?:www|m)\.)?tiktok\.com/@[\w.]+/video/\d+\S*|https?://vm\.tiktok\.com/\w+\S*", re.I)
MAX_INFO_BYTES = 20_000_000
MAX_CAPTION_BYTES = 8_000_000
SHORT_VIDEO_SECONDS = 90         # 이보다 짧으면 자막이 있어도 장면을 본다
MAX_WATCH_SECONDS = 40 * 60      # 자막 없는 영상을 받아 장면을 볼 수 있는 최대 길이
MAX_VIDEO_BYTES = "300M"
YOUTUBE_CAPTION_HOSTS = ("www.youtube.com", "youtube.com", "m.youtube.com")
# 받아쓰기 속도 — 이 PC(Ryzen 7 9800X3D, 8스레드) 실측: 한국어 5분 소리를 62~65초(약 4.6배). 진행 안내의 예상 시간에만 쓴다.
WHISPER_SPEED = 4.5
WHISPER_MODEL = "ggml-large-v3-turbo-q8_0.bin"
WHISPER_VAD = "ggml-silero-v6.2.0.bin"
# 말소리 구간에서도 가끔 나오는 Whisper 의 단골 지어낸 말 — 문장 전체가 이것뿐이면 버린다.
_HALLUCINATION = re.compile(r"^(?:시청해\s*주셔서\s*감사합니다|구독과\s*좋아요(?:\s*부탁드립니다)?|ご視聴ありがとうございました|"
                            r"thank\s+you\s+for\s+watching|thanks\s+for\s+watching|subtitles?\s+by.*)[\s.!。]*$", re.I)


class VideoError(RuntimeError):
    """영상 정보·자막을 가져오지 못했다. 사용자에게 그대로 보여 줄 문장."""


@dataclass(frozen=True)
class VideoTranscript:
    video_id: str
    title: str
    channel: str
    duration: int
    url: str
    language: str
    kind: str          # '직접 단 자막' | '자동 생성 자막' | '받아쓰기' | '말소리 없음' | '자막 없음'
    text: str          # 머리말(정보) + 시간 표시가 있는 자막(없으면 머리말만)
    frames: tuple = ()  # ((초, JPEG 바이트), …) — 모델이 그림으로 볼 장면

    @property
    def document_name(self) -> str:
        """대화 문서 이름 — 같은 영상을 다시 받지 않도록 영상 ID 를 앞에 둔다."""
        title = re.sub(r"[\\/:*?\"<>|\r\n\x00]", " ", self.title).strip()[:80] or "제목 없음"
        return f"영상 {self.video_id} - {title}.txt"


def find_video_url(text: str) -> str | None:
    match = VIDEO_URL.search(text)
    return match.group(0).rstrip(").,]>'\"") if match else None


def clock(seconds: float) -> str:
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60:02d}:{rest % 60:02d}"


def choose_track(info: dict) -> tuple[str, str, list[dict]] | None:
    """(언어, 종류, 형식 목록). 직접 단 자막(영상 언어 → 한국어 → 영어 → 아무거나) → 원래 말의 자동 자막 → 자동 한국어·영어."""
    language = str(info.get("language") or "").split("-")[0].lower()
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if v and k != "live_chat"}
    auto = {k: v for k, v in (info.get("automatic_captions") or {}).items() if v}

    def first(tracks: dict, wanted: list[str]) -> str | None:
        for want in wanted:
            for key in tracks:
                if want and (key.lower() == want or key.lower().startswith(want + "-")):
                    return key
        return None

    key = first(manual, [language, "ko", "en"]) or (next(iter(manual)) if manual else None)
    if key:
        return key, "직접 단 자막", manual[key]
    key = first(auto, [f"{language}-orig"] if language else []) or next((k for k in auto if k.endswith("-orig")), None) \
        or first(auto, [language, "ko", "en"])
    return (key, "자동 생성 자막", auto[key]) if key else None


def parse_json3(data: bytes) -> list[tuple[float, str]]:
    """유튜브 json3: 이벤트마다 시작 시각(ms)과 글 조각들. 줄바꿈만 있는 이벤트(자동 자막의 이어 붙이기)는 뺀다."""
    lines = []
    for event in json.loads(data.decode("utf-8")).get("events", []):
        text = "".join(seg.get("utf8", "") for seg in event.get("segs") or [])
        text = " ".join(text.split())
        if text:
            lines.append((event.get("tStartMs", 0) / 1000, text))
    return lines


_CUE_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})\s*-->")


def parse_vtt(data: bytes) -> list[tuple[float, str]]:
    """WebVTT·SRT: 시각 줄 다음의 글. 태그(<c>·<00:00:01.000>)를 지우고, 자동 자막이 앞 줄을 되풀이하는 것은 한 번만."""
    lines, start, seen = [], None, set()
    for raw in data.decode("utf-8-sig", errors="replace").splitlines():
        match = _CUE_TIME.search(raw)
        if match:
            hours, minutes, seconds, _ = match.groups()
            start = int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
            continue
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", "", raw)).split())
        if start is None or not text or text.isdigit() or text in seen:
            continue
        seen.add(text)
        lines.append((start, text))
    return lines


def format_transcript(lines: list[tuple[float, str]], every: int = 60) -> str:
    """약 1분마다 [mm:ss] 를 붙인 문단으로. 바로 앞과 같은 줄은 뺀다."""
    paragraphs, current, mark, previous = [], [], None, ""
    for start, text in lines:
        if text == previous:
            continue
        previous = text
        if mark is None or start - mark >= every:
            if current:
                paragraphs.append(" ".join(current))
            current, mark = [f"[{clock(start)}]"], start
        current.append(text)
    if current:
        paragraphs.append(" ".join(current))
    return "\n".join(paragraphs)


class Transcriber:
    """소리 → 시간 표시가 있는 글(ADR-108). whisper.cpp 를 CPU 로만 돌린다(그래픽카드는 Strata 가 쓴다). 셸 없이 고정 인자."""

    def __init__(self, tool_dir: Path, threads: int | None = None) -> None:
        self.tool_dir = Path(tool_dir)
        self.threads = threads or max(1, min(8, (os.cpu_count() or 2) // 2))

    @property
    def cli(self) -> Path:
        return self.tool_dir / "bin" / "Release" / ("whisper-cli.exe" if os.name == "nt" else "whisper-cli")

    @property
    def model(self) -> Path:
        return self.tool_dir / "models" / WHISPER_MODEL

    @property
    def vad(self) -> Path:
        return self.tool_dir / "models" / WHISPER_VAD

    def available(self) -> bool:
        return self.cli.is_file() and self.model.is_file() and self.vad.is_file()

    def command(self, wav: Path, language: str, out: Path) -> list[str]:
        return [str(self.cli), "-m", str(self.model), "-f", str(wav), "-l", language or "auto", "-t", str(self.threads),
                "-oj", "-of", str(out), "-np", "--vad", "-vm", str(self.vad)]

    def transcribe(self, wav: Path, language: str = "auto", seconds: float = 0, cancel_event: threading.Event | None = None,
                   progress: Callable[[str], None] | None = None) -> tuple[list[tuple[float, str]], str]:
        """([(초, 문장)…], 알아낸 언어). 말소리가 없으면 빈 목록. 중단하면 GenerationCancelled."""
        say = progress or (lambda _text: None)
        out = wav.with_name(wav.stem + "-whisper")
        log = wav.with_name(wav.stem + "-whisper.log")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        limit = 300 + seconds  # 실측 속도의 4배 넘게 여유
        started = time.monotonic()
        next_note = started + 20
        with log.open("wb") as errors:
            process = subprocess.Popen(self.command(wav, language, out), stdout=subprocess.DEVNULL, stderr=errors,
                                       creationflags=flags)
            try:
                while process.poll() is None:
                    if cancel_event is not None and cancel_event.is_set():
                        raise GenerationCancelled("받아 적기를 중단했습니다.")
                    now = time.monotonic()
                    if now - started > limit:
                        raise VideoError("소리를 받아 적는 데 시간이 너무 걸려 멈췄습니다.")
                    if now >= next_note:
                        say(f"[소리를 받아 적는 중… {int(now - started)}초 지남"
                            + (f", 예상 약 {max(1, round(seconds / WHISPER_SPEED / 60))}분" if seconds else "") + "]\n")
                        next_note += 20
                    time.sleep(0.5)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(10)
        result = out.with_suffix(".json")
        if process.returncode != 0 or not result.is_file():
            detail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:] if log.is_file() else []
            raise VideoError("소리를 받아 적지 못했습니다" + (f": {detail[0][:200]}" if detail else "."))
        data = json.loads(result.read_text(encoding="utf-8", errors="replace"))
        lines = []
        for item in data.get("transcription") or []:
            text = " ".join(str(item.get("text", "")).split())
            if text and not _HALLUCINATION.match(text):
                lines.append((int((item.get("offsets") or {}).get("from") or 0) / 1000, text))
        return lines, str((data.get("result") or {}).get("language") or language or "")


class VideoReader:
    """yt-dlp + Deno 로 영상 정보와 자막을 가져온다. 도구가 없으면 available() 이 False — ALEX 는 예전처럼 페이지 글만 읽는다."""

    def __init__(self, tool_dir: Path, timeout: float = 90, transcriber: Transcriber | None = None) -> None:
        self.tool_dir = Path(tool_dir)
        self.timeout = timeout
        self.transcriber = transcriber

    def can_listen(self) -> bool:
        return self.transcriber is not None and self.transcriber.available() and self.can_watch()

    @property
    def ytdlp(self) -> Path:
        return self.tool_dir / "yt-dlp.exe" if os.name == "nt" else self.tool_dir / "yt-dlp"

    @property
    def deno(self) -> Path:
        return self.tool_dir / "deno" / ("deno.exe" if os.name == "nt" else "deno")

    @property
    def ffmpeg(self) -> Path:
        return self.tool_dir / "ffmpeg" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")

    def available(self) -> bool:
        return self.ytdlp.is_file() and self.deno.is_file()

    def can_watch(self) -> bool:
        return self.ffmpeg.is_file()

    def measure(self, video: Path) -> float:
        """받은 영상의 길이(초) — 인스타그램처럼 정보에 길이가 없을 때."""
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            out = subprocess.run([str(self.ffmpeg.with_name(self.ffmpeg.name.replace("ffmpeg", "ffprobe"))), "-v", "error",
                                  "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(video)],
                                 capture_output=True, text=True, timeout=30, creationflags=flags).stdout
            return max(0.0, float(out.strip() or 0))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return 0.0

    def download(self, url: str, folder: Path, info: dict | None = None) -> Path:
        """영상 파일을 folder 에 받는다(720p 이하·300MB 이하). 장면 보기와 영상 속 대상 바꾸기(ADR-099)가 쓴다."""
        self._run(["--ffmpeg-location", str(self.ffmpeg.parent), "-f", "best[height<=720]/best",
                   "--max-filesize", MAX_VIDEO_BYTES, "-o", "video.%(ext)s"],
                  str((info or {}).get("webpage_url") or url), cwd=folder)
        video = next((f for f in folder.glob("video.*") if f.suffix not in (".part", ".ytdl")), None)
        if video is None:
            raise VideoError("영상을 받지 못했습니다(너무 크거나 받을 수 없는 형식).")
        return video

    def frames(self, info: dict, url: str) -> tuple[tuple[tuple[float, bytes], ...], float]:
        """영상을 임시 폴더에 받아(720p 이하·300MB 이하) 고르게 장면을 뽑는다 — 1분 이하 6장, 5분 이하 8장, 그 이상 12장.
        (장면들, 길이 초)를 돌려준다. 길이 정보가 없으면 받은 파일에서 잰다."""
        shots, duration, _heard = self.watch(info, url)
        return shots, duration

    def watch(self, info: dict, url: str, *, look: bool = True, listen: bool = False,
              cancel_event: threading.Event | None = None, progress: Callable[[str], None] | None = None
              ) -> tuple[tuple[tuple[float, bytes], ...], float, tuple[list[tuple[float, str]], str] | None]:
        """영상을 한 번 받아 장면을 뽑고(look), 소리를 받아 적는다(listen — 자막이 없을 때, ADR-108).
        (장면들, 길이 초, (받아 적은 줄들, 언어) 또는 None)."""
        duration = float(info.get("duration") or 0)
        if duration > MAX_WATCH_SECONDS:
            raise VideoError(f"영상이 너무 길어서({clock(duration)}) 장면을 다 볼 수 없습니다. {MAX_WATCH_SECONDS // 60}분 이하 영상만 봅니다.")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with tempfile.TemporaryDirectory(prefix="alex-video-") as temp:
            folder = Path(temp)
            video = self.download(url, folder, info)
            duration = duration or self.measure(video)
            if duration > MAX_WATCH_SECONDS:
                raise VideoError(f"영상이 너무 길어서({clock(duration)}) 장면을 다 볼 수 없습니다.")
            count = (6 if duration <= 60 else 8 if duration <= 300 else 12) if look else 0
            shots = []
            for n in range(count):
                at = round((n + 0.5) / count * duration, 2) if duration else n
                out = folder / f"frame{n}.jpg"
                try:
                    subprocess.run([str(self.ffmpeg), "-v", "error", "-y", "-ss", str(at), "-i", str(video), "-frames:v", "1",
                                    "-vf", "scale='min(768,iw)':-2", "-q:v", "4", str(out)],
                                   capture_output=True, timeout=60, creationflags=flags)
                except (OSError, subprocess.TimeoutExpired):
                    continue
                if out.is_file() and out.stat().st_size:
                    shots.append((at, out.read_bytes()))
            if look and not shots:
                raise VideoError("영상에서 장면을 뽑지 못했습니다.")
            heard = None
            if listen and self.transcriber is not None:
                wav = folder / "sound.wav"
                done = subprocess.run([str(self.ffmpeg), "-v", "error", "-y", "-i", str(video), "-vn", "-ar", "16000", "-ac", "1",
                                       "-c:a", "pcm_s16le", str(wav)], capture_output=True, timeout=300, creationflags=flags)
                if done.returncode == 0 and wav.is_file() and wav.stat().st_size > 1000:
                    wanted = str(info.get("language") or "").split("-")[0].lower()
                    (progress or (lambda _t: None))(f"[자막이 없어 소리를 받아 적습니다(Whisper, 이 PC 의 CPU) — "
                                                    f"{clock(duration)} 분량, 예상 약 {max(1, round(duration / WHISPER_SPEED / 60))}분]\n")
                    heard = self.transcriber.transcribe(wav, wanted if re.fullmatch(r"[a-z]{2,3}", wanted) else "auto",
                                                        duration, cancel_event, progress)
                else:
                    heard = ([], "")  # 소리 트랙이 없는 영상
            return tuple(shots), duration, heard

    def _run(self, extra: list[str], url: str, cwd: Path | None = None) -> bytes:
        command = [str(self.ytdlp), "--ignore-config", "--no-playlist", "--no-warnings", "--js-runtimes",
                   f"deno:{self.deno}", *extra, "--", url]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            result = subprocess.run(command, capture_output=True, timeout=self.timeout, cwd=cwd, creationflags=flags)
        except subprocess.TimeoutExpired as exc:
            raise VideoError("영상 정보를 가져오는 데 시간이 너무 걸립니다. 잠시 뒤 다시 시도해 주세요.") from exc
        except OSError as exc:
            raise VideoError(f"영상 도구(yt-dlp)를 실행하지 못했습니다: {exc}") from exc
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", errors="replace")
            if "429" in message:
                raise VideoError("유튜브가 잠시 요청을 막았습니다(429). 몇 분 뒤 다시 시도해 주세요.")
            if re.search(r"Private video|Sign in|members-only|age", message, re.I):
                raise VideoError("비공개·로그인 필요·연령 제한 영상이라 가져올 수 없습니다.")
            if re.search(r"video (?:is )?unavailable|has been removed|does not exist", message, re.I):
                raise VideoError("없는 영상이거나 삭제·차단된 영상입니다. 주소를 확인해 주세요.")
            last = next((line for line in reversed(message.splitlines()) if line.strip()), "알 수 없는 오류")
            raise VideoError(f"영상 정보를 가져오지 못했습니다: {last[:200]}")
        return result.stdout

    def info(self, url: str) -> dict:
        data = self._run(["--skip-download", "--dump-single-json"], url)
        if len(data) > MAX_INFO_BYTES:
            raise VideoError("영상 정보가 너무 큽니다.")
        try:
            return json.loads(data.decode("utf-8"))
        except ValueError as exc:
            raise VideoError("영상 정보 형식이 올바르지 않습니다.") from exc

    def caption(self, info: dict, language: str, formats: list[dict]) -> list[tuple[float, str]]:
        """유튜브는 json3 주소를 바로 받는다(주소가 유튜브인지 확인). 다른 사이트는 yt-dlp 가 그 자막 하나만 받게 한다."""
        by_ext = {f.get("ext"): f for f in formats if isinstance(f, dict)}
        chosen = by_ext.get("json3")
        host = urllib.parse.urlsplit(str((chosen or {}).get("url", ""))).hostname or ""
        if chosen and host in YOUTUBE_CAPTION_HOSTS and str(chosen["url"]).startswith("https://"):
            request = urllib.request.Request(chosen["url"], headers={"User-Agent": "Mozilla/5.0"})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = response.read(MAX_CAPTION_BYTES + 1)
            except OSError as exc:
                if "429" in str(exc):
                    raise VideoError("유튜브가 잠시 자막 요청을 막았습니다(429). 몇 분 뒤 다시 시도해 주세요.") from exc
                raise VideoError(f"자막을 받지 못했습니다: {exc}") from exc
            if len(data) > MAX_CAPTION_BYTES:
                raise VideoError("자막이 너무 큽니다.")
            return parse_json3(data)
        with tempfile.TemporaryDirectory(prefix="alex-captions-") as temp:
            self._run(["--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", re.escape(language),
                       "--sub-format", "vtt/srt/best", "-o", "caption.%(ext)s"], str(info.get("webpage_url") or ""),
                      cwd=Path(temp))
            files = sorted(Path(temp).glob("caption.*"))
            if not files:
                raise VideoError("자막 파일을 받지 못했습니다.")
            data = files[0].read_bytes()[:MAX_CAPTION_BYTES]
            return parse_json3(data) if files[0].suffix == ".json3" else parse_vtt(data)

    def read(self, url: str, *, cancel_event: threading.Event | None = None,
             progress: Callable[[str], None] | None = None) -> VideoTranscript:
        info = self.info(url)
        duration = int(info.get("duration") or 0)
        track = choose_track(info)
        lines, language, kind = [], "", "자막 없음"
        if track is not None:
            language, kind, formats = track
            lines = self.caption(info, language, formats)
        frames = ()
        look = (not lines or duration <= SHORT_VIDEO_SECONDS) and self.can_watch()
        if not lines and self.can_listen():  # 자막이 없으면 받아 적는다 — 장면도 같은 영상 파일에서
            frames, measured, heard = self.watch(info, url, look=look, listen=True, cancel_event=cancel_event,
                                                 progress=progress)
            duration = duration or int(round(measured))
            if heard is not None:
                lines, language = heard
                kind = "받아쓰기" if lines else "말소리 없음"
        elif look:
            frames, measured = self.frames(info, url)
            duration = duration or int(round(measured))
        if not lines and not frames:
            raise VideoError("이 영상에는 자막도 말소리도 없고 장면을 볼 도구(ffmpeg)도 없어 내용을 읽을 수 없습니다."
                             if kind == "말소리 없음" else
                             "이 영상에는 자막이 없고 장면을 볼 도구(ffmpeg)도 없어 내용을 읽을 수 없습니다.")
        chapters = [f"- [{clock(c.get('start_time', 0))}] {c.get('title', '')}" for c in info.get("chapters") or []][:60]
        uploaded = str(info.get("upload_date") or "")
        header = [f"[영상] {info.get('title', '')}",
                  f"채널: {info.get('channel') or info.get('uploader') or '?'} · 길이: {clock(duration)}"
                  + (f" · 올린 날: {uploaded[:4]}-{uploaded[4:6]}-{uploaded[6:]}" if len(uploaded) == 8 else ""),
                  f"주소: {info.get('webpage_url') or url}",
                  (f"자막: 없어서 ALEX 가 소리를 받아 적었다(Whisper, {language}) — 틀린 낱말이 있을 수 있다." if kind == "받아쓰기" else
                   f"자막: {kind}({language}) — 말을 글로 옮긴 것이라 틀린 낱말이 있을 수 있다." if lines else
                   "자막: 없음 — 소리를 들어 봤지만 말소리가 없다(음악·효과음뿐)." if kind == "말소리 없음" else
                   "자막: 없음 — 말소리 내용은 모른다.")
                  + (f" 장면 {len(frames)}장을 그림으로 함께 봤다." if frames else " 화면 내용(그림·글자)은 들어 있지 않다.")]
        description = " ".join(str(info.get("description") or "").split())[:1500]
        if description:
            header.append(f"설명: {description}")
        if chapters:
            header.append("챕터:\n" + "\n".join(chapters))
        text = "\n".join(header) + ("\n\n[자막]\n" + format_transcript(lines) if lines else "")
        return VideoTranscript(str(info.get("id") or "unknown"), str(info.get("title") or ""),
                               str(info.get("channel") or info.get("uploader") or ""), duration,
                               str(info.get("webpage_url") or url), language, kind, text, frames)
