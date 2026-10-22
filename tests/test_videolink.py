"""영상 링크 읽기(ADR-098, 2026-10-18 사용자 "영상 링크 같은 것도 못 보는데 볼 수 있도록 못 하나?").

영상 대신 자막을 읽는다: 직접 단 자막 → 원래 말의 자동 자막 → 한국어·영어. 시간 표시를 남긴 글을 그 대화의 문서로
붙이고, 같은 대화의 다음 질문에도 쓴다. 도구가 없거나 못 받으면 예전처럼 페이지 글만 읽고 이유를 답 끝에 붙인다.
실제 yt-dlp·네트워크는 쓰지 않는다(가짜 읽기 도구)."""
import base64
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jarvis.core import VIDEO_REASON
from jarvis.router import Destination
from jarvis.videolink import (VideoError, VideoReader, VideoTranscript, choose_track, find_video_url, format_transcript,
                              parse_json3, parse_vtt)
from tests.test_memory_search import make_core


class UrlTests(unittest.TestCase):
    def test_video_links_are_found(self):
        for text, url in (
                ("이거 요약해줘 https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s", "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s"),
                ("https://youtu.be/dQw4w9WgXcQ?si=abc 봐줘", "https://youtu.be/dQw4w9WgXcQ?si=abc"),
                ("(https://www.youtube.com/shorts/AysV2zIzY94)", "https://www.youtube.com/shorts/AysV2zIzY94"),
                ("https://m.youtube.com/watch?feature=share&v=bAXxxmXCk1o.", "https://m.youtube.com/watch?feature=share&v=bAXxxmXCk1o"),
                ("https://tv.naver.com/v/12345678 내용?", "https://tv.naver.com/v/12345678"),
                ("https://www.instagram.com/reel/DekIwaqhK2n/?vrfl=MWo4 확인해", "https://www.instagram.com/reel/DekIwaqhK2n/?vrfl=MWo4"),
                ("https://www.tiktok.com/@some.one/video/7400000000000000000", "https://www.tiktok.com/@some.one/video/7400000000000000000")):
            with self.subTest(text=text):
                self.assertEqual(find_video_url(text), url)

    def test_other_links_are_not_videos(self):
        for text in ("https://www.youtube.com/@channel", "https://news.example.com/watch?v=dQw4w9WgXcQ", "그냥 질문"):
            with self.subTest(text=text):
                self.assertIsNone(find_video_url(text))


class TrackTests(unittest.TestCase):
    F = [{"ext": "json3", "url": "https://www.youtube.com/api/timedtext?x"}]

    def test_human_subtitles_in_the_video_language_come_first(self):
        info = {"language": "ja", "subtitles": {"en": self.F, "ja": self.F}, "automatic_captions": {"ja-orig": self.F}}
        self.assertEqual(choose_track(info)[:2], ("ja", "직접 단 자막"))
        info = {"language": None, "subtitles": {"en-US": self.F, "de": self.F}}
        self.assertEqual(choose_track(info)[0], "en-US")

    def test_auto_captions_prefer_the_original_speech_over_translations(self):
        info = {"language": "ko", "subtitles": {"live_chat": self.F},
                "automatic_captions": {"en": self.F, "ko": self.F, "ko-orig": self.F}}
        self.assertEqual(choose_track(info)[:2], ("ko-orig", "자동 생성 자막"))
        self.assertEqual(choose_track({"automatic_captions": {"fr": self.F, "es-orig": self.F}})[0], "es-orig")

    def test_no_captions(self):
        self.assertIsNone(choose_track({"subtitles": {}, "automatic_captions": {}}))


class ParseTests(unittest.TestCase):
    def test_json3_joins_word_pieces_and_drops_line_breaks(self):
        data = json.dumps({"events": [
            {"tStartMs": 0, "segs": [{"utf8": "안녕"}, {"utf8": "하세요"}]},
            {"tStartMs": 1500, "aAppend": 1, "segs": [{"utf8": "\n"}]},
            {"tStartMs": 61000, "segs": [{"utf8": "반도체 "}, {"utf8": " 공정"}]}]}).encode()
        self.assertEqual(parse_json3(data), [(0.0, "안녕하세요"), (61.0, "반도체 공정")])

    def test_vtt_removes_tags_and_rolling_repeats(self):
        vtt = ("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n<c>첫</c> 줄&amp;\n\n"
               "00:00:03.000 --> 00:00:05.000\n첫 줄&\n둘째 줄\n\n1\n01:02:03,500 --> 01:02:05,000\n셋째 줄\n").encode()
        self.assertEqual(parse_vtt(vtt), [(1, "첫 줄&"), (3, "둘째 줄"), (3723, "셋째 줄")])

    def test_transcript_has_a_time_mark_about_every_minute(self):
        text = format_transcript([(0, "가"), (30, "나"), (30, "나"), (65, "다"), (3700, "라")])
        self.assertEqual(text, "[00:00] 가 나\n[01:05] 다\n[1:01:40] 라")


class ReaderTests(unittest.TestCase):
    def test_missing_tools_mean_not_available(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertFalse(VideoReader(Path(temp)).available())

    def test_tool_errors_become_plain_sentences(self):
        reader = VideoReader(Path("C:/nowhere"))
        for stderr, expected in ((b"ERROR: HTTP Error 429: Too Many Requests", "429"),
                                 (b"ERROR: Private video. Sign in", "비공개"),
                                 (b"ERROR: [youtube] x: This video is unavailable", "없는 영상"),
                                 (b"ERROR: something else broke", "something else broke")):
            done = subprocess.CompletedProcess([], 1, b"", stderr)
            with self.subTest(stderr=stderr), mock.patch("subprocess.run", return_value=done), \
                    self.assertRaisesRegex(VideoError, expected):
                reader.info("https://youtu.be/dQw4w9WgXcQ")

    def test_the_url_goes_after_double_dash_and_user_config_is_ignored(self):
        reader = VideoReader(Path("C:/tools"))
        done = subprocess.CompletedProcess([], 0, b"{}", b"")
        with mock.patch("subprocess.run", return_value=done) as run:
            reader.info("https://youtu.be/dQw4w9WgXcQ")
        command = run.call_args.args[0]
        self.assertEqual(command[-2:], ["--", "https://youtu.be/dQw4w9WgXcQ"])
        self.assertIn("--ignore-config", command)
        self.assertNotIn("shell", run.call_args.kwargs)


TINY_JPEG = base64.b64decode("/9j//gAQTGF2YzYyLjI4LjEwMwD/2wBDAAgQEBMQExYWFhYWFhoYGhsbGxoaGhobGxsdHR0iIiIdHR0bGx0dICAiIiUmJSMjIiMmJigoKDAwLi44ODpFRVP/xABhAAEBAQEBAAAAAAAAAAAAAAAEBgMFBwEBAQEAAAAAAAAAAAAAAAAAAgEDEAACAgICAQUBAQAAAAAAAAABAgMRABIhMQSRUXEjsRNBEQEBAQAAAAAAAAAAAAAAAAAAESH/wAARCAAcABADASIAAhEAAxEA/9oADAMBAAIRAxEAPwD04FE2VwpF8CgOP857wss68CNVAHfA/ci/OmWC5maUsqtrHyY2c9F9RdD5rOf4E48j7dnDMBvHR/mrDvTYXR9rOBpqtdFlFHqqOHSNIVIAGajjCSMfXAsf/9k=")


class WatchTests(unittest.TestCase):
    """자막이 없거나 짧은 영상은 장면을 뽑아 본다(같은 날 사용자: 인스타그램 릴스를 '보는 게 가능하도록')."""

    def reader(self, info, captions, can_watch=True):
        reader = VideoReader(Path("C:/tools"))
        reader.info = lambda url: info
        reader.caption = lambda info, language, formats: captions
        reader.can_watch = lambda: can_watch
        reader.frames = lambda info, url: (((1.0, TINY_JPEG), (5.0, TINY_JPEG)), 12.4)
        return reader

    def test_no_captions_means_watch_the_frames(self):
        video = self.reader({"id": "DekIwaqhK2n", "title": "Video by x"}, []).read("https://www.instagram.com/reel/DekIwaqhK2n/")
        self.assertEqual((video.kind, len(video.frames), video.duration), ("자막 없음", 2, 12))  # 길이 정보가 없으면 받은 영상에서 잰다
        self.assertIn("장면 2장을 그림으로 함께 봤다", video.text)

    def test_short_videos_are_watched_even_with_captions_long_ones_are_not(self):
        info = {"id": "a", "title": "t", "language": "ko", "automatic_captions": {"ko-orig": [{"ext": "json3"}]}}
        self.assertEqual(len(self.reader({**info, "duration": 40}, [(0, "말")]).read("u").frames), 2)
        self.assertEqual(self.reader({**info, "duration": 1704}, [(0, "말")]).read("u").frames, ())

    def test_nothing_to_read_without_captions_or_ffmpeg(self):
        with self.assertRaisesRegex(VideoError, "ffmpeg"):
            self.reader({"id": "a", "title": "t", "duration": 12}, [], can_watch=False).read("u")


class FakeReader:
    def __init__(self, fail: str | None = None):
        self.calls, self.fail = [], fail

    def read(self, url, **_kwargs):  # cancel_event·progress(ADR-108)
        self.calls.append(url)
        if self.fail:
            raise VideoError(self.fail)
        return VideoTranscript("dQw4w9WgXcQ", "반도체 8대 공정", "bRd 3D", 1704, url, "ko-orig", "자동 생성 자막",
                               "[영상] 반도체 8대 공정\n\n[자막]\n[00:00] 웨이퍼 제조가 첫 번째 공정입니다\n[01:00] 산화 공정")


class CoreVideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.core = make_core(Path(self.temp.name))
        self.core.video_reader = FakeReader()
        self.sid = self.core.get_or_create_session(False)

    def tearDown(self):
        self.temp.cleanup()

    def test_the_answer_uses_the_captions_not_the_web_page(self):
        reply, decision = self.core.handle(self.sid, "이 영상 첫 공정이 뭐야? https://youtu.be/dQw4w9WgXcQ")
        self.assertEqual((decision.destination, decision.reason), (Destination.LOCAL_GENERAL, VIDEO_REASON))
        self.assertIn("웨이퍼 제조가 첫 번째 공정", reply)  # 가짜 모델은 받은 마지막 메시지를 그대로 돌려준다
        self.assertIn("영상 기준 — 반도체 8대 공정 · 28분 24초 · 자동 생성 자막", reply)
        titles = [d["title"] for d in self.core.knowledge.documents(f"session:{self.sid}")]
        self.assertEqual(titles, ["영상 dQw4w9WgXcQ - 반도체 8대 공정.txt"])

    def test_follow_up_questions_keep_using_it_and_it_is_fetched_once(self):
        self.core.handle(self.sid, "요약해줘 https://youtu.be/dQw4w9WgXcQ")
        self.assertIn("산화 공정", self.core.handle(self.sid, "두 번째 공정은?")[0])
        reply, _ = self.core.handle(self.sid, "https://www.youtube.com/watch?v=dQw4w9WgXcQ 다시 정리")
        self.assertEqual(len(self.core.video_reader.calls), 1)
        self.assertIn("이미 붙인", reply)

    def test_failure_is_explained_and_falls_back_to_the_old_way(self):
        self.core.video_reader = FakeReader(fail="이 영상에는 자막이 없어서")
        reply, decision = self.core.handle(self.sid, "요약해줘 https://youtu.be/dQw4w9WgXcQ")
        self.assertNotEqual(decision.reason, VIDEO_REASON)
        self.assertIn("영상을 확인하지 못해 웹 페이지 글만 참고했습니다: 이 영상에는 자막이 없어서", reply)
        self.assertEqual(self.core.knowledge.documents(f"session:{self.sid}"), [])

    def test_frames_go_to_the_image_reader_and_are_shown(self):
        reader = FakeReader()
        reader.read = lambda url, **_kwargs: VideoTranscript("DekIwaqhK2n", "Video by x", "x", 12, url, "", "자막 없음",
                                                  "[영상] Video by x\n자막: 없음", ((1.0, TINY_JPEG), (5.0, TINY_JPEG)))
        self.core.video_reader = reader
        reply, decision = self.core.handle(self.sid, "이거 뭐야 https://www.instagram.com/reel/DekIwaqhK2n/")
        self.assertEqual(decision.reason, "local image understanding")  # 그림 이해 길(가짜 모델이라 분석은 실패한다)
        self.assertIn("영상 기준 — Video by x · 0분 12초 · 장면 2장. 말소리는 자막이 없어 모릅니다", reply)
        saved = self.core.storage.message_page(self.sid)["messages"][0]["content"]
        self.assertEqual(saved.count("alex-attachment:"), 2)  # 화면에 본 장면이 보인다

    def test_without_the_tool_nothing_changes(self):
        self.core.video_reader = None
        reply, decision = self.core.handle(self.sid, "요약해줘 https://youtu.be/dQw4w9WgXcQ")
        self.assertNotEqual(decision.reason, VIDEO_REASON)
        self.assertNotIn("영상 자막", reply)


if __name__ == "__main__":
    unittest.main()
