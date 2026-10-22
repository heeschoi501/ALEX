"""영상 손질(2026-10-19, ADR-099) — 영상 속 대상 바꾸기(/video-swap)와 긴 영상(/video-long)에 쓰는 ffmpeg 작업.

ffmpeg 는 C:\\ALEX\\tools\\video\\ffmpeg(ADR-098 과 같은 것). 셸 없이 고정 인자로만 실행한다.
- 바꾸기 준비: 원본을 16fps·VACE 크기(세로 480×832, 가로 832×480, 정사각 640×640)로 꽉 채워 자르고, 길이는 최대 12초
  (193프레임 — 2026-10-19 실측 그래픽카드 14.8GB, 16GB 의 한계 가까이). 움직임 안내는 윤곽(edgedetect+흐림) 영상.
- 긴 영상: 5초 조각들을 이어 붙인다. 다음 조각은 앞 조각의 마지막 장면에서 시작하므로 그 첫 장면은 빼고 붙인다.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import subprocess

FPS = 16
MAX_SWAP_FRAMES = 193      # 12초. 이 PC(16GB)에서 한 번에 만든 최대
MIN_SWAP_FRAMES = 17


class VideoEditError(RuntimeError):
    pass


@dataclass(frozen=True)
class SwapPlan:
    width: int
    height: int
    frames: int

    @property
    def seconds(self) -> float:
        return self.frames / FPS


def swap_plan(width: int, height: int, duration: float) -> SwapPlan:
    """원본 비율로 크기를, 길이(최대 12초)로 프레임 수(4k+1)를 정한다."""
    ratio = width / height if height else 1.0
    size = (480, 832) if ratio < 0.8 else (832, 480) if ratio > 1.25 else (640, 640)
    frames = min(MAX_SWAP_FRAMES, max(MIN_SWAP_FRAMES, math.ceil(duration * FPS)))
    frames = (frames - 1) // 4 * 4 + 1
    return SwapPlan(size[0], size[1], frames)


def clips_for(seconds: int, clip_seconds: int = 5) -> int:
    return max(1, math.ceil(seconds / clip_seconds))


class VideoEditor:
    def __init__(self, ffmpeg: Path, timeout: float = 180) -> None:
        self.ffmpeg = Path(ffmpeg)
        self.ffprobe = self.ffmpeg.with_name(self.ffmpeg.name.replace("ffmpeg", "ffprobe"))
        self.timeout = timeout

    def available(self) -> bool:
        return self.ffmpeg.is_file() and self.ffprobe.is_file()

    def _run(self, args: list[str]) -> str:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=self.timeout, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VideoEditError(f"영상 처리 도구를 실행하지 못했습니다: {exc}") from exc
        if result.returncode != 0:
            last = next((line for line in reversed(result.stderr.splitlines()) if line.strip()), "알 수 없는 오류")
            raise VideoEditError(f"영상 처리에 실패했습니다: {last[:200]}")
        return result.stdout

    def probe(self, video: Path) -> tuple[int, int, float, bool]:
        """(가로, 세로, 길이 초, 소리 있음). 회전 정보(휴대폰 세로 영상)는 ffmpeg 가 풀어 주는 크기로."""
        out = self._run([str(self.ffprobe), "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                         "-of", "json", str(video)])
        data = json.loads(out or "{}")
        streams = data.get("streams") or []
        visual = next((s for s in streams if s.get("codec_type") == "video"), None)
        if visual is None:
            raise VideoEditError("영상 화면을 찾지 못했습니다.")
        duration = float((data.get("format") or {}).get("duration") or 0)
        return int(visual["width"]), int(visual["height"]), duration, any(s.get("codec_type") == "audio" for s in streams)

    def prepare_swap(self, video: Path, folder: Path) -> tuple[SwapPlan, Path, Path]:
        """(계획, 원본(소리 포함, 맞춘 크기·길이), 윤곽 영상)."""
        width, height, duration, _ = self.probe(video)
        plan = swap_plan(width, height, duration)
        fill = (f"fps={FPS},scale={plan.width}:{plan.height}:force_original_aspect_ratio=increase,"
                f"crop={plan.width}:{plan.height},tpad=stop_mode=clone:stop_duration=1")
        source, control = folder / "swap-source.mp4", folder / "swap-control.mp4"
        self._run([str(self.ffmpeg), "-v", "error", "-y", "-i", str(video), "-vf", fill, "-frames:v", str(plan.frames),
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-af", "apad", "-t", f"{plan.seconds:.4f}",
                   str(source)])
        self._run([str(self.ffmpeg), "-v", "error", "-y", "-i", str(source), "-vf",
                   "edgedetect=low=0.08:high=0.25,gblur=sigma=1.2", "-frames:v", str(plan.frames), "-an",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", str(control)])
        return plan, source, control

    def prepare_masked(self, source: Path, folder: Path, python: Path, script: Path, frames: int,
                       progress=None) -> tuple[Path, Path]:
        """'영역만 다시 그리기'(ADR-099 보충): 장면마다 주인공 영역을 따서(scripts/video_masks.py, 따로 둔 rembg 환경)
        (원본 + 그 영역 회색 영상, 그 영역 흰색 영상)을 만든다. 장면 두 장에 한 번 따는 데 약 3.7초."""
        shots, out = folder / "frames", folder / "masked"
        shots.mkdir(exist_ok=True)
        self._run([str(self.ffmpeg), "-v", "error", "-y", "-i", str(source), "-frames:v", str(frames), str(shots / "%04d.png")])
        if progress is not None:
            progress(f"[장면 {frames}장에서 주인공 영역을 따는 중… 약 {max(1, round(frames / 2 * 3.7 / 60))}분]\n")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            result = subprocess.run([str(python), "-X", "utf8", str(script), "frames", str(shots), str(out), "25", "2"],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=120 + frames * 5, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VideoEditError(f"주인공 영역을 따지 못했습니다: {exc}") from exc
        if result.returncode != 0:
            raise VideoEditError("주인공 영역을 따지 못했습니다: " + (result.stderr.strip().splitlines() or ["?"])[-1][:200])
        control, mask = folder / "swap-control-masked.mp4", folder / "swap-mask.mp4"
        for name, target, quality in (("control", control, "12"), ("mask", mask, "0")):
            self._run([str(self.ffmpeg), "-v", "error", "-y", "-framerate", str(FPS), "-i", str(out / name / "%04d.png"),
                       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", quality, str(target)])
        return control, mask

    def matte_reference(self, python: Path, script: Path, photo: Path, out: Path, width: int, height: int) -> Path:
        """참고 사진에서 주인공만 흰 바탕에(사진의 바닥·목줄이 영상에 섞이지 않게)."""
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            result = subprocess.run([str(python), "-X", "utf8", str(script), "reference", str(photo), str(out), str(width),
                                     str(height)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=300, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VideoEditError(f"참고 사진의 배경을 지우지 못했습니다: {exc}") from exc
        if result.returncode != 0 or not out.is_file():
            raise VideoEditError("참고 사진의 배경을 지우지 못했습니다.")
        return out

    def fit_image(self, image: Path, out: Path, width: int, height: int) -> Path:
        """참고 사진을 영상 크기로 꽉 채워 가운데를 자른다."""
        self._run([str(self.ffmpeg), "-v", "error", "-y", "-i", str(image), "-vf",
                   f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}",
                   "-frames:v", "1", "-q:v", "2", str(out)])
        return out

    def snapshots(self, video: Path, folder: Path, count: int = 4) -> list[Path]:
        """고르게 장면 몇 장(대화 모델이 원본의 움직임을 보고 설명을 쓰게)."""
        _, _, duration, _ = self.probe(video)
        shots = []
        for n in range(count):
            out = folder / f"shot{n}.jpg"
            self._run([str(self.ffmpeg), "-v", "error", "-y", "-ss", f"{(n + 0.5) / count * duration:.2f}", "-i", str(video),
                       "-frames:v", "1", "-vf", "scale='min(512,iw)':-2", "-q:v", "4", str(out)])
            if out.is_file():
                shots.append(out)
        return shots

    def last_frame(self, video: Path, out: Path) -> Path:
        self._run([str(self.ffmpeg), "-v", "error", "-y", "-sseof", "-0.2", "-i", str(video), "-update", "1",
                   "-frames:v", "1", "-q:v", "2", str(out)])
        if not out.is_file():
            raise VideoEditError("마지막 장면을 뽑지 못했습니다.")
        return out

    def concat(self, clips: list[Path], out: Path) -> Path:
        """조각들을 하나로. 둘째 조각부터 첫 장면(앞 조각의 마지막 장면과 같다)을 뺀다. 소리는 넣지 않는다."""
        if not clips:
            raise VideoEditError("이어 붙일 영상이 없습니다.")
        width, height, _, _ = self.probe(clips[0])  # 크기가 조금 달라도(그림에서 시작한 조각) 첫 조각에 맞춘다
        inputs, parts = [], []
        for n, clip in enumerate(clips):
            inputs += ["-i", str(clip)]
            trim = "trim=start_frame=1," if n else ""
            parts.append(f"[{n}:v]{trim}setpts=PTS-STARTPTS,scale={width}:{height},setsar=1,format=yuv420p[v{n}]")
        graph = ";".join(parts) + ";" + "".join(f"[v{n}]" for n in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=0[out]"
        self._run([str(self.ffmpeg), "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[out]",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)])
        return out
