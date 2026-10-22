"""자막 없는 영상 받아쓰기(ADR-108, 2026-10-22) — whisper.cpp(CPU)로 소리를 받아 적어 영상 문서에 넣는다.
가짜 whisper 로 흐름(해석·지어낸 말 거르기·중단·실패)을 보고, 이 PC 에 실제 도구가 있으면 말소리 없는 소리로 실제 실행도 본다."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from jarvis.providers import GenerationCancelled
from jarvis.videolink import Transcriber, VideoError, VideoReader

TOOLS = Path(__file__).resolve().parents[2] / "tools"
FAKE = r'''
import json, sys, time
args = sys.argv[1:]
out = args[args.index("-of") + 1]
mode = args[args.index("--mode") + 1]
if mode == "slow":
    time.sleep(30)
if mode == "fail":
    sys.stderr.write("error: failed to read audio\n"); sys.exit(2)
items = [{"offsets": {"from": 670}, "text": " 한국경제에서 가장 중요한 산업은 무엇일까요?"},
         {"offsets": {"from": 3950}, "text": " 시청해 주셔서 감사합니다."},
         {"offsets": {"from": 8210}, "text": "   "},
         {"offsets": {"from": 61500}, "text": " 반도체일 것입니다."}]
json.dump({"result": {"language": "ko"}, "transcription": items}, open(out + ".json", "w", encoding="utf-8"), ensure_ascii=False)
'''


class FakeTranscriber(Transcriber):
    def __init__(self, folder: Path, mode: str = "ok"):
        super().__init__(folder, threads=2)
        self.mode, self.script = mode, folder / "fake_whisper.py"
        self.script.write_text(FAKE, encoding="utf-8")

    def available(self):
        return True

    def command(self, wav, language, out):
        self.seen = super().command(wav, language, out)
        return [sys.executable, str(self.script), "-of", str(out), "--mode", self.mode]


class TranscriberTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.wav = self.folder / "sound.wav"
        self.wav.write_bytes(b"RIFF")

    def test_command_uses_vad_language_and_threads(self):
        command = Transcriber(Path("C:/tools/whisper"), threads=8).command(self.wav, "ko", self.folder / "out")
        self.assertEqual(command[command.index("-l") + 1], "ko")
        self.assertEqual(command[command.index("-t") + 1], "8")
        self.assertIn("--vad", command)
        self.assertTrue(command[command.index("-vm") + 1].endswith("ggml-silero-v6.2.0.bin"))
        self.assertFalse(Transcriber(self.folder).available())

    def test_lines_have_times_and_known_hallucinations_are_dropped(self):
        notes = []
        lines, language = FakeTranscriber(self.folder).transcribe(self.wav, "ko", 62, progress=notes.append)
        self.assertEqual(language, "ko")
        self.assertEqual(lines, [(0.67, "한국경제에서 가장 중요한 산업은 무엇일까요?"), (61.5, "반도체일 것입니다.")])

    def test_cancel_stops_the_process(self):
        cancel = threading.Event()
        threading.Timer(0.8, cancel.set).start()
        with self.assertRaises(GenerationCancelled):
            FakeTranscriber(self.folder, "slow").transcribe(self.wav, "auto", 10, cancel_event=cancel)

    def test_failure_says_why(self):
        with self.assertRaisesRegex(VideoError, "failed to read audio"):
            FakeTranscriber(self.folder, "fail").transcribe(self.wav)


class ReadTests(unittest.TestCase):
    def reader(self, heard, captions=()):
        reader = VideoReader(Path("C:/tools"), transcriber=FakeTranscriber(Path(tempfile.mkdtemp())))
        reader.info = lambda url: {"id": "DekIwaqhK2n", "title": "릴스", "language": "ko"}
        reader.caption = lambda info, language, formats: list(captions)
        reader.can_watch = lambda: True
        self.calls = []

        def watch(info, url, *, look=True, listen=False, cancel_event=None, progress=None):
            self.calls.append((look, listen))
            return ((1.0, b"jpeg"), (5.0, b"jpeg")), 12.4, heard
        reader.watch = watch
        return reader

    def test_no_captions_means_listen_and_look_from_one_download(self):
        video = self.reader(([(0.67, "안녕하세요 오늘은 반도체 이야기")], "ko")).read("https://www.instagram.com/reel/DekIwaqhK2n/")
        self.assertEqual(self.calls, [(True, True)])
        self.assertEqual((video.kind, len(video.frames), video.duration, video.language), ("받아쓰기", 2, 12, "ko"))
        self.assertIn("ALEX 가 소리를 받아 적었다(Whisper, ko)", video.text)
        self.assertIn("[00:00] 안녕하세요 오늘은 반도체 이야기", video.text)

    def test_music_only_video_says_no_speech(self):
        video = self.reader(([], "")).read("u")
        self.assertEqual(video.kind, "말소리 없음")
        self.assertIn("말소리가 없다(음악·효과음뿐)", video.text)
        self.assertEqual(len(video.frames), 2)

    def test_videos_with_captions_do_not_listen(self):
        info = {"id": "a", "title": "t", "duration": 1704, "language": "ko", "automatic_captions": {"ko-orig": [{"ext": "json3"}]}}
        reader = self.reader(([], ""), captions=[(0, "말")])
        reader.info = lambda url: info
        self.assertEqual(reader.read("u").kind, "자동 생성 자막")
        self.assertEqual(self.calls, [])


@unittest.skipUnless(Transcriber(TOOLS / "whisper").available() and (TOOLS / "video" / "ffmpeg" / "ffmpeg.exe").is_file(),
                     "이 PC 의 whisper.cpp·ffmpeg 없음")
class RealWhisperTests(unittest.TestCase):
    def test_a_tone_without_speech_gives_no_lines(self):
        with tempfile.TemporaryDirectory() as temp:
            wav = Path(temp) / "tone.wav"
            subprocess.run([str(TOOLS / "video" / "ffmpeg" / "ffmpeg.exe"), "-v", "error", "-y", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=4", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)], check=True)
            lines, _language = Transcriber(TOOLS / "whisper").transcribe(wav, "auto", 4)
            self.assertEqual(lines, [])


if __name__ == "__main__":
    unittest.main()
