"""영상 명령(ADR-099, 2026-10-19 사용자 "둘 다 필요할 것 같네, 완성되면 진행해").

/video-swap: 원본 영상의 움직임은 그대로, 주인공을 이 대화의 '방금 그림'으로(VACE, 최대 12초).
/video-long: 5초 조각을 이어 10~60초. 둘째 조각부터 앞 조각의 마지막 장면에서 시작하고, 이을 때 겹친 첫 장면은 뺀다.
ffmpeg 가 이 PC(C:\\ALEX\\tools\\video\\ffmpeg)에 있으면 작은 시험 영상으로 실제 처리까지 확인한다."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from jarvis.app import handle_command
from jarvis.core import VIDEO_LONG_REASON, VIDEO_SWAP_REASON
from jarvis.imagegen import SWAP_NEGATIVE, ComfyImageGenerator, VideoResult
from jarvis.permissions import READ_ONLY_BLOCKED
from jarvis.videoedit import VideoEditor, clips_for, swap_plan
from tests.test_memory_search import make_core

FFMPEG = Path(r"C:\ALEX\tools\video\ffmpeg\ffmpeg.exe")
HAS_FFMPEG = FFMPEG.is_file()


def make_clip(path: Path, frames: int, size: str = "64x64", audio: bool = False) -> Path:
    args = [str(FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=16"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
    args += ["-frames:v", str(frames), "-pix_fmt", "yuv420p"]
    args += ["-shortest"] if audio else []
    subprocess.run(args + [str(path)], check=True, capture_output=True)
    return path


def count_frames(video: Path) -> int:
    out = subprocess.run([str(FFMPEG.with_name("ffprobe.exe")), "-v", "error", "-count_frames", "-select_streams", "v:0",
                          "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True, check=True).stdout
    return int(out.strip())


class PlanTests(unittest.TestCase):
    def test_size_follows_the_shape_and_length_is_4k_plus_1_up_to_12_seconds(self):
        self.assertEqual(swap_plan(1080, 1920, 12.05), swap_plan(480, 854, 30))  # 세로, 12초로 자름
        self.assertEqual((swap_plan(1080, 1920, 30).width, swap_plan(1080, 1920, 30).frames), (480, 193))
        self.assertEqual((swap_plan(1920, 1080, 5.1).width, swap_plan(1920, 1080, 5.1).height, swap_plan(1920, 1080, 5.1).frames),
                         (832, 480, 81))
        self.assertEqual(swap_plan(1000, 1000, 3).width, 640)
        self.assertEqual(swap_plan(480, 832, 7.3).frames, 117)  # 올림(117) — 모자란 끝은 마지막 장면 복제
        self.assertEqual(swap_plan(480, 832, 0.5).frames, 17)

    def test_clip_count(self):
        self.assertEqual([clips_for(s) for s in (10, 12, 60)], [2, 3, 12])


class WorkflowTests(unittest.TestCase):
    def test_swap_graph(self):
        graph = ComfyImageGenerator.swap_workflow("a dog", 7, "c.mp4", "s.mp4", "r.jpg", 480, 832, 193)
        vace = graph["23"]["inputs"]
        self.assertEqual((vace["length"], vace["width"], vace["height"], vace["strength"]), (193, 480, 832, 0.8))
        self.assertEqual(graph["24"]["inputs"]["trim_amount"], ["23", 3])  # 앞에 붙은 참고 장면을 잘라 낸다
        self.assertEqual(graph["10"]["inputs"]["audio"], ["31", 1])         # 원본 소리
        self.assertTrue(graph["6"]["inputs"]["text"].endswith(SWAP_NEGATIVE))
        self.assertEqual(graph["11"]["class_type"], "SaveVideo")
        self.assertNotIn("control_masks", vace)

    def test_masked_graph_redraws_only_the_subject(self):
        graph = ComfyImageGenerator.swap_workflow("a dog", 7, "c.mp4", "s.mp4", "r.jpg", 480, 832, 81, 1.0, mask="m.mp4")
        self.assertEqual(graph["23"]["inputs"]["control_masks"], ["42", 0])
        self.assertEqual((graph["40"]["inputs"]["file"], graph["42"]["inputs"]["channel"]), ("m.mp4", "red"))


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg 이 없는 기기")
class EditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.editor = VideoEditor(FFMPEG)

    def tearDown(self):
        self.temp.cleanup()

    def test_prepare_swap_makes_same_length_source_and_outline(self):
        video = make_clip(self.dir / "in.mp4", 48, size="360x640", audio=True)  # 세로 3초
        plan, source, control = self.editor.prepare_swap(video, self.dir)
        self.assertEqual((plan.width, plan.height, plan.frames), (480, 832, 45))
        self.assertEqual((count_frames(source), count_frames(control)), (45, 45))
        self.assertTrue(self.editor.probe(source)[3])  # 소리를 지킨다
        ref = self.editor.fit_image(self.editor.snapshots(video, self.dir, 2)[0], self.dir / "r.jpg", 480, 832)
        self.assertEqual(self.editor.probe(ref)[:2], (480, 832))

    def test_concat_drops_the_repeated_first_frame(self):
        a, b = make_clip(self.dir / "a.mp4", 17), make_clip(self.dir / "b.mp4", 17, size="80x80")
        out = self.editor.concat([a, b], self.dir / "out.mp4")
        self.assertEqual(count_frames(out), 33)
        self.assertEqual(self.editor.probe(out)[:2], (64, 64))
        self.assertTrue(self.editor.last_frame(a, self.dir / "last.jpg").is_file())


class FakeGenerator:
    def __init__(self, folder: Path):
        self.folder, self.calls, self.freed = folder, [], 0

    def has_vace(self):
        return True

    def video_plan(self, *args, **kwargs):
        return SimpleNamespace(minutes=4)

    def generate_video(self, prompt, start_image=None, **kwargs):
        self.calls.append((prompt, start_image))
        path = make_clip(self.folder / f"20261019-0000{len(self.calls):02d}-{len(self.calls)}.mp4", 17)
        return VideoResult(path, prompt, 1, 64, 64, 1.0, False)

    def free(self):
        self.freed += 1


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg 이 없는 기기")
class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.core = make_core(self.dir)
        self.core.image_generator = FakeGenerator(self.dir)
        self.core.video_reader = SimpleNamespace(ffmpeg=FFMPEG, can_watch=lambda: True)
        self.sid = self.core.get_or_create_session(False)

    def tearDown(self):
        self.temp.cleanup()

    def test_long_video_chains_clips_from_the_last_frame(self):
        reply, _, _ = handle_command(self.core, self.sid, "/video-long 12 강아지가 뛴다 | 강아지가 앉는다")
        calls = self.core.image_generator.calls
        self.assertEqual(len(calls), 3)                      # 12초 → 5초 조각 3개
        self.assertIsNone(calls[0][1])                       # 첫 조각은 글에서
        self.assertTrue(all(start is not None for _, start in calls[1:]))  # 나머지는 앞 조각의 마지막 장면에서
        self.assertIn("긴 영상을 만들었습니다 — 5초 조각 3개", reply)
        name = reply.split("(alex-video:", 1)[1].split(")", 1)[0]
        self.assertEqual(count_frames(self.dir / name), 17 * 3 - 2)
        self.assertEqual(self.core.image_generator.freed, 1)
        saved = self.core.storage.message_page(self.sid)["messages"]
        self.assertEqual(saved[0]["content"], "/video-long 12 강아지가 뛴다 | 강아지가 앉는다")  # 대화 기록에 남는다

    def test_long_video_rejects_bad_lengths(self):
        self.assertIn("사용법", handle_command(self.core, self.sid, "/video-long 강아지")[0])
        self.assertIn("10~60초", handle_command(self.core, self.sid, "/video-long 90 강아지")[0])

    def test_swap_needs_a_picture_first(self):
        reply, _, _ = handle_command(self.core, self.sid, "/video-swap https://www.instagram.com/reel/DekIwaqhK2n/")
        self.assertIn("참고 그림 올리기", reply)
        self.assertIn("사용법", handle_command(self.core, self.sid, "/video-swap 그냥 글")[0])

    def test_read_only_blocks_both(self):
        self.core.permission_mode = "read"
        self.assertEqual(handle_command(self.core, self.sid, "/video-long 10 강아지")[0], READ_ONLY_BLOCKED)


class RouteTests(unittest.TestCase):
    def test_reasons_are_distinct(self):
        self.assertNotEqual(VIDEO_SWAP_REASON, VIDEO_LONG_REASON)


if __name__ == "__main__":
    unittest.main()
