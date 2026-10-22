"""자연어 그림 그리기(ADR-073) — 가짜 ComfyUI·가짜 Ollama 로 잰다. 실제 그림은 PERFORMANCE 에 사람이 본 결과로 적는다."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from jarvis.core import JarvisCore
from jarvis.hardware import HardwareInfo, PerformanceProfile
from jarvis.imagegen import (ANIME_CKPT, IMAGE_NAME, MARKER, MODEL_FILES, QWEN_UNET, UNET, WAN14_FILES, WAN_NEGATIVE,
                             ComfyImageGenerator, ImageGenError, anime_prompts, blocked_reason, choose_image_model,
                             clean_prompt, image_size, image_subject, is_change_request)
from jarvis.providers import CloudStubProvider, GenerationCancelled, MockLLMProvider, OllamaProvider, ProviderError
from jarvis.router import Destination
from jarvis.storage import Storage

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"fake-mp4-body"


class OwnedEngineCloseTests(unittest.TestCase):
    def test_start_reserves_1_5gb_of_vram(self):
        # 10-04 사용자 요청으로 뺐다가(GPU_STABILITY_FIX_20261004), 10-19 23:53 GPU lost 뒤 사용자 "1.5gb만 지키면 될 듯"(10-20)으로 다시.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative in ("python_embeded/python.exe", "ComfyUI/main.py"):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch()
            generator = ComfyImageGenerator(root / "images", comfy_dir=root)
            with patch.object(generator, "is_running", side_effect=[False, True]), \
                    patch("jarvis.imagegen.subprocess.Popen") as popen:
                generator.ensure_running()
            args = popen.call_args.args[0]
            self.assertEqual(args[args.index("--reserve-vram") + 1], "1.5")

    def test_termination_timeout_kills_and_waits_for_owned_child(self):
        generator = ComfyImageGenerator(Path("unused"))
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("owned child", 5), 0]
        generator._process = process
        self.assertTrue(generator.close())
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)
        self.assertIsNone(generator._process)
        self.assertFalse(generator.close())

    def test_busy_generation_preserves_the_process(self):
        generator = ComfyImageGenerator(Path("unused"))
        process = Mock()
        generator._process = process
        with generator._lock, self.assertRaises(ImageGenError):
            generator.close()
        self.assertIs(generator._process, process)
        process.terminate.assert_not_called()

    def test_failed_cleanup_keeps_handle_for_retry(self):
        generator = ComfyImageGenerator(Path("unused"))
        process = Mock()
        process.poll.return_value = None
        process.terminate.side_effect = OSError("access denied")
        generator._process = process
        with self.assertRaises(ImageGenError):
            generator.close()
        self.assertIs(generator._process, process)


class _Server:
    def __init__(self, handler):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _reply(handler, code, body, ctype="application/json"):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


class FakeComfy(_Server):
    """ComfyUI 의 /system_stats·/prompt·/history·/view·/free·/interrupt 만 흉내 낸다."""

    def __init__(self):
        self.prompts, self.frees, self.interrupts, self.viewed = [], [], 0, None
        self.uploads = []  # 기준 그림 올리기(POST /upload/image) — 올린 파일 이름
        self.fail = False
        self.pending_polls = 1  # 처음 몇 번은 아직 그리는 중
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                if url.path == "/system_stats":
                    return _reply(self, 200, {"system": {"comfyui_version": "test"}})
                if url.path.startswith("/history/"):
                    prompt_id = url.path.rsplit("/", 1)[1]
                    if fake.pending_polls > 0:
                        fake.pending_polls -= 1
                        return _reply(self, 200, {})
                    if fake.fail:
                        return _reply(self, 200, {prompt_id: {"outputs": {}, "status": {
                            "status_str": "error", "completed": False,
                            "messages": [["execution_error", {"node_type": "KSampler",
                                                              "exception_message": "CUDA out of memory"}]]}}})
                    if fake.prompts and any(node["class_type"] == "SaveVideo" for node in fake.prompts[-1].values()):
                        return _reply(self, 200, {prompt_id: {"status": {"status_str": "success", "completed": True},
                                                              "outputs": {"11": {"images": [{"filename": "clip_00001_.mp4",
                                                                                             "subfolder": "alex-video",
                                                                                             "type": "output"}]}}}})
                    return _reply(self, 200, {prompt_id: {"status": {"status_str": "success", "completed": True},
                                                          "outputs": {"10": {"images": [{"filename": "ComfyUI_temp_00001_.png",
                                                                                          "subfolder": "", "type": "temp"}]}}}})
                if url.path == "/view":
                    fake.viewed = urllib.parse.parse_qs(url.query)
                    if fake.viewed["filename"][0].endswith(".mp4"):
                        return _reply(self, 200, MP4, "video/mp4")
                    return _reply(self, 200, PNG, "image/png")
                return _reply(self, 404, {"error": "unknown"})

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.path == "/upload/image":
                    name = raw.split(b'filename="', 1)[1].split(b'"', 1)[0].decode()
                    fake.uploads.append(name)
                    return _reply(self, 200, {"name": name, "subfolder": "", "type": "input"})
                payload = json.loads(raw or b"{}")
                if self.path == "/prompt":
                    fake.prompts.append(payload["prompt"])
                    return _reply(self, 200, {"prompt_id": f"p{len(fake.prompts)}", "number": 1, "node_errors": {}})
                if self.path == "/free":
                    fake.frees.append(payload)
                    return _reply(self, 200, {})
                if self.path == "/interrupt":
                    fake.interrupts += 1
                    return _reply(self, 200, {})
                return _reply(self, 404, {})

        super().__init__(Handler)


class FakeOllama(_Server):
    """/api/ps 와 keep_alive 0 만 — 그리기 전에 대화 모델을 내리는지 본다."""

    def __init__(self, loaded):
        self.loaded = list(loaded)
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                _reply(self, 200, {"models": [{"name": n, "model": n} for n in fake.loaded]})

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if payload.get("keep_alive") == 0 and payload.get("model") in fake.loaded:
                    fake.loaded.remove(payload["model"])
                _reply(self, 200, {"done": True})

        super().__init__(Handler)


class PromptWriter(MockLLMProvider):
    """한국어 부탁을 영어 프롬프트로 옮기는 로컬 모델 흉내 — 받은 글을 기억한다."""

    def __init__(self, reply="Prompt: A fluffy orange cat sleeping on a sunny windowsill, soft morning light."):
        super().__init__("writer", "WRITER")
        self.reply, self.seen = reply, []

    def generate(self, messages, **kwargs):
        self.seen.append("\n".join(m.content for m in messages))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def make_core(root: Path, generator=None, writer=None, general=None) -> JarvisCore:
    return JarvisCore(
        storage=Storage(root / "alex.db"),
        hardware=HardwareInfo("TestOS", "CPU", 32, "GPU", 16, PerformanceProfile.DESKTOP),
        local_general=general or MockLLMProvider("general", "GENERAL"),
        local_coder=MockLLMProvider("coder", "CODER"),
        cloud_coder=CloudStubProvider("cc", "CLOUD"), cloud_reasoning=CloudStubProvider("cr", "CLOUD"),
        local_summarizer=writer or PromptWriter(),
        image_generator=generator,
    )


class TextRuleTests(unittest.TestCase):
    def test_size_words(self):
        self.assertEqual(image_size("고양이 그려줘"), (1024, 1024))
        self.assertEqual(image_size("가로로 긴 노을 그려줘"), (1344, 768))
        self.assertEqual(image_size("폰 배경화면 만들어줘"), (768, 1344))  # '폰 배경' 이 '배경화면' 보다 먼저

    def test_subject_drops_the_request_words(self):
        self.assertEqual(image_subject("바닷가 노을 그림 그려줘"), "바닷가 노을")
        self.assertEqual(image_subject("고양이 이미지를 만들어줘"), "고양이")
        self.assertEqual(image_subject("draw a cat on the moon"), "cat on the moon")

    def test_the_writer_is_told_fantasy_and_monsters_are_fine_but_not_harming_real_people(self):
        from jarvis.imagegen import PROMPT_RULES

        # 2026-10-11 사용자가 고친 문구에 맞춘다 — 허구의 괴물·잔혹 장면은 거절하지 않고, 실존 인물·실제 해악은 막는다.
        self.assertIn("monsters", PROMPT_RULES)
        self.assertIn("rather than refusing", PROMPT_RULES)
        self.assertIn("real identifiable people", PROMPT_RULES)
        self.assertIn("Never sexualize minors", PROMPT_RULES)

    def test_prompt_cleanup_keeps_one_plain_prompt(self):
        self.assertEqual(clean_prompt('Prompt: "A cat on the moon."\n\nNote: I added lighting.'), "A cat on the moon.")
        self.assertEqual(clean_prompt("<think>hmm</think>A red car"), "A red car")

    def test_change_requests(self):
        for text in ("더 밝게 해줘", "한 장 더", "배경을 밤으로 바꿔줘", "다시 해줘"):
            self.assertTrue(is_change_request(text), text)
        for text in ("고마워", "좋네 저장해줘", "오늘 날씨 어때?"):
            self.assertFalse(is_change_request(text), text)


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeComfy()
        self.addCleanup(self.fake.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / "images"
        self.gen = ComfyImageGenerator(self.out, url=self.fake.url, poll_seconds=0.01)

    def test_generate_sends_the_z_image_workflow_and_saves_the_png(self):
        result = self.gen.generate("a cat", 1344, 768, seed=7)
        self.assertEqual(result.path.read_bytes(), PNG)
        self.assertRegex(result.path.name, IMAGE_NAME)
        self.assertEqual(result.path.parent, self.out)
        graph = self.fake.prompts[0]
        nodes = {node["class_type"]: node["inputs"] for node in graph.values()}
        self.assertEqual(nodes["UNETLoader"]["unet_name"], "z_image_turbo_bf16.safetensors")
        self.assertEqual(nodes["CLIPLoader"]["type"], "lumina2")
        self.assertEqual(nodes["CLIPTextEncode"]["text"], "a cat")
        self.assertEqual((nodes["KSampler"]["seed"], nodes["KSampler"]["steps"], nodes["KSampler"]["cfg"]), (7, 8, 1.0))
        self.assertEqual((nodes["EmptySD3LatentImage"]["width"], nodes["EmptySD3LatentImage"]["height"]), (1344, 768))
        self.assertIn("PreviewImage", nodes)  # ComfyUI 쪽에는 임시 파일만 — 사본은 ALEX data/images 에만
        self.assertEqual(self.fake.viewed["type"], ["temp"])

    def test_a_failed_run_is_reported_with_comfys_reason(self):
        self.fake.fail = True
        with self.assertRaisesRegex(ImageGenError, "CUDA out of memory"):
            self.gen.generate("a cat")

    def test_cancel_interrupts_the_run(self):
        self.fake.pending_polls = 10_000
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(GenerationCancelled):
            self.gen.generate("a cat", cancel_event=cancel)
        self.assertEqual(self.fake.interrupts, 1)

    def test_free_releases_vram_and_optionally_ram(self):
        self.assertTrue(self.gen.free())
        self.assertTrue(self.gen.free(release_ram=True))
        self.assertEqual(self.fake.frees, [{"unload_models": True, "free_memory": False},
                                           {"unload_models": True, "free_memory": True}])

    def test_only_this_pc(self):
        with self.assertRaises(ValueError):
            ComfyImageGenerator(self.out, url="http://192.168.0.5:8188")

    def test_a_missing_install_says_where_it_looked(self):
        gen = ComfyImageGenerator(self.out, url="http://127.0.0.1:9", comfy_dir=Path(self.temp.name) / "nope")
        with self.assertRaisesRegex(ImageGenError, "ComfyUI 가 꺼져 있고"):
            gen.generate("a cat")

    def test_image_path_only_serves_app_made_names(self):
        saved = self.gen.generate("a cat", seed=1).path
        self.assertEqual(self.gen.image_path(saved.name), saved)
        for name in ("../alex.db", "x.png", saved.name + "/..", "20261003-112233-1.jpg"):
            self.assertIsNone(self.gen.image_path(name), name)


class CoreImageTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeComfy()
        self.addCleanup(self.fake.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gen = ComfyImageGenerator(self.root / "images", url=self.fake.url, poll_seconds=0.01)
        self.writer = PromptWriter()
        self.core = make_core(self.root, self.gen, self.writer)
        self.sid = self.core.get_or_create_session(False)

    def test_the_answer_names_the_model_and_how_to_switch(self):
        # 2026-10-18 사용자: 어떤 모델로 그렸는지 보이고 'Qwen으로 다시' 처럼 쉽게 바꾸게(그 문구만 고침)
        reply, _ = self.core.handle(self.sid, "창가에서 자는 고양이 그려줘")
        self.assertIn("그렸습니다 — 모델 Z-Image Turbo", reply)
        switch = next(line for line in reply.splitlines() if line.startswith("다른 모델로: "))
        self.assertIn("'Qwen으로 다시 그려줘'", switch)
        self.assertIn("'애니로 다시 그려줘'", switch)
        self.assertNotIn("Z-Image로", switch)  # 방금 쓴 모델은 빼고 안내한다

    def test_a_natural_request_draws_and_shows_the_picture(self):
        reply, decision = self.core.handle(self.sid, "창가에서 자는 고양이 그려줘")
        self.assertEqual(decision.destination, Destination.TOOL_IMAGE_GEN)
        self.assertIn(f"![창가에서 자는 고양이]({MARKER}", reply)
        self.assertIn("영어 프롬프트: A fluffy orange cat", reply)
        self.assertEqual(self.fake.prompts[0]["5"]["inputs"]["text"],
                         "A fluffy orange cat sleeping on a sunny windowsill, soft morning light.")
        self.assertIn("창가에서 자는 고양이 그려줘", self.writer.seen[0])
        self.assertEqual(self.fake.frees, [{"unload_models": True, "free_memory": False}])  # 다 그리면 VRAM 을 돌려준다
        saved = self.core.storage.message_page(self.sid, 10**9)
        self.assertIn(MARKER, json.dumps(saved, ensure_ascii=False))

    def test_a_short_change_after_a_picture_redraws_from_the_last_prompt(self):
        self.core.handle(self.sid, "창가에서 자는 고양이 그려줘")
        reply, decision = self.core.handle(self.sid, "더 밝게 해줘")
        self.assertEqual(decision.destination, Destination.TOOL_IMAGE_GEN)
        self.assertIn("Previous prompt: A fluffy orange cat", self.writer.seen[-1])
        self.assertEqual(len(self.fake.prompts), 2)
        _, decision = self.core.handle(self.sid, "고마워")
        self.assertEqual(decision.destination, Destination.LOCAL_GENERAL)

    def test_the_same_change_words_without_a_picture_stay_conversation(self):
        _, decision = self.core.handle(self.sid, "더 밝게 해줘")
        self.assertEqual(decision.destination, Destination.LOCAL_GENERAL)

    def test_without_a_writer_model_the_request_is_used_as_is(self):
        core = make_core(self.root / "b", self.gen, PromptWriter(ProviderError("down")))
        (self.root / "b").mkdir(exist_ok=True)
        reply, _ = core.handle(core.get_or_create_session(False), "노을 지는 바닷가 그려줘")
        self.assertIn("요청 문장을 그대로", reply)
        self.assertEqual(self.fake.prompts[-1]["5"]["inputs"]["text"], "노을 지는 바닷가")

    def test_comfy_failure_is_an_answer_not_a_crash(self):
        self.fake.fail = True
        reply, _ = self.core.handle(self.sid, "고양이 그려줘")
        self.assertIn("그림을 그리지 못했습니다", reply)
        self.assertIn("CUDA out of memory", reply)

    def test_switched_off_explains_how_to_turn_on(self):
        core = make_core(self.root / "c")
        reply, decision = core.handle(core.get_or_create_session(False), "고양이 그려줘")
        self.assertEqual(decision.destination, Destination.TOOL_IMAGE_GEN)
        self.assertIn("그림 그리기가 꺼져 있습니다", reply)

    def test_the_model_is_told_it_can_draw_only_when_enabled(self):
        self.assertIn("ComfyUI", self.core._environment_line(Destination.LOCAL_GENERAL))
        self.assertNotIn("ComfyUI", make_core(self.root / "d")._environment_line(Destination.LOCAL_GENERAL))

    def test_the_chat_model_is_unloaded_before_drawing(self):
        ollama = FakeOllama(["qwen3.8:27b"])
        self.addCleanup(ollama.close)
        core = make_core(self.root / "e", self.gen, PromptWriter(), general=OllamaProvider(model="qwen3.8:27b", host=ollama.url))
        reply, _ = core.handle(core.get_or_create_session(False), "고양이 그려줘")
        self.assertEqual(ollama.loaded, [])
        self.assertIn("대화 모델(qwen3.8:27b)을 내렸습니다", reply)

    def test_unload_failure_blocks_image_and_video_submission(self):
        from jarvis.app import handle_command
        provider = OllamaProvider(model="test", host="http://127.0.0.1:9")
        core = make_core(self.root / "guard", self.gen, self.writer, general=provider)
        sid = core.get_or_create_session(False)
        for command in ("/image-prompt cat", "/video-prompt cat walking"):
            with self.subTest(command=command), patch("jarvis.core.unload_local_models", side_effect=ProviderError("busy")):
                reply = handle_command(core, sid, command)[0]
                self.assertIn("GPU 반환을 확인하지 못해", reply)
                self.assertEqual(self.fake.prompts, [])

    def test_still_loaded_model_blocks_media_after_successful_unload_request(self):
        from jarvis.app import handle_command
        provider = OllamaProvider(model="test", host="http://127.0.0.1:9")
        core = make_core(self.root / "sticky", self.gen, self.writer, general=provider)
        with patch("jarvis.core.unload_local_models", return_value=["test"]), \
                patch("jarvis.core.loaded_local_models", return_value=["test"]):
            reply = handle_command(core, core.get_or_create_session(False), "/image-prompt cat")[0]
        self.assertIn("GPU에 남아 있는", reply)
        self.assertEqual(self.fake.prompts, [])

    def test_commands(self):
        from jarvis.app import HELP_TEXT, handle_command

        self.assertIn("/image", HELP_TEXT)
        self.assertIn("무엇을 그릴까요", handle_command(self.core, self.sid, "/image")[0])
        self.assertIn(MARKER, handle_command(self.core, self.sid, "/image 노을 지는 바닷가")[0])
        reply = handle_command(self.core, self.sid, "/unload")[0]  # Ollama 를 안 쓰는 설정이어도 그림 모델은 내린다
        self.assertIn("그림 모델(ComfyUI)도 내렸습니다", reply)
        self.assertEqual(self.fake.frees[-1], {"unload_models": True, "free_memory": True})

    def test_direct_image_prompt_reaches_comfy_without_chat_rewrite(self):
        from jarvis.app import handle_command

        prompt = "An orange cat beside a window.\nSoft evening light, detailed fur."
        reply = handle_command(self.core, self.sid, "/image-prompt " + prompt)[0]
        self.assertIn("직접 입력한 프롬프트: " + prompt, reply)
        self.assertEqual(self.fake.prompts[-1]["5"]["inputs"]["text"], prompt)
        self.assertEqual(self.writer.seen, [])
        self.assertIn(MARKER, reply)
        self.assertEqual(self.core.storage.message_page(self.sid, 10**9)["messages"][0]["content"], prompt)

    def test_direct_image_prompt_still_checks_minor_safety(self):
        from jarvis.app import handle_command

        reply = handle_command(self.core, self.sid, "/image-prompt A sexy schoolgirl in a bikini")[0]
        self.assertIn("미성년자", reply)
        self.assertEqual(self.fake.prompts, [])
        self.assertEqual(self.writer.seen, [])


class BoundaryTests(unittest.TestCase):
    """2026-10-03: 미성년자 관련은 앱이 항상 막고, 성인 표현은 대화 모델의 판단에 맡긴다. 모델의 거절 문장은 그리지 않는다."""

    def setUp(self):
        self.fake = FakeComfy()
        self.addCleanup(self.fake.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gen = ComfyImageGenerator(self.root / "images", url=self.fake.url, poll_seconds=0.01)
        self.writer = PromptWriter("A fantasy scene, cinematic lighting.")
        self.core = make_core(self.root, self.gen, self.writer)
        self.sid = self.core.get_or_create_session(False)

    def test_minors_with_anything_sexual_are_refused_before_the_model_or_comfy(self):
        for text in ("비키니 입은 초등학생 그려줘", "섹시한 여고생 그려줘", "옷이 비치는 소녀 그려줘"):
            reply, decision = self.core.handle(self.sid, text)
            self.assertEqual(decision.destination, Destination.TOOL_IMAGE_GEN, text)
            self.assertIn("미성년자", reply, text)
        self.assertEqual((self.writer.seen, self.fake.prompts), ([], []))

    def test_a_writer_prompt_that_sexualizes_a_minor_is_not_drawn(self):
        self.writer.reply = "A sexy schoolgirl in a bikini."
        reply, _ = self.core.handle(self.sid, "해변 그려줘")
        self.assertIn("그리지 않았습니다", reply)
        self.assertEqual(self.fake.prompts, [])

    def test_fantasy_and_adult_pictures_are_not_blocked_by_the_app(self):
        for text in ("투명한 날개의 요정 소녀 그려줘", "비키니 입은 성인 여성 그려줘", "어린 왕자 그려줘", "용과 싸우는 기사 그려줘"):
            reply, _ = self.core.handle(self.sid, text)
            self.assertIn(MARKER, reply, text)

    def test_a_refusing_writer_is_reported_not_drawn(self):
        self.writer.reply = "I'm sorry, but I can't help with that request."
        reply, _ = self.core.handle(self.sid, "강아지 그려줘")
        self.assertIn("거절", reply)
        self.assertEqual(self.fake.prompts, [])


class MinorGuardTests(unittest.TestCase):
    """2026-10-11 사용자 "Danbooru·e621 은 붙이되 미성년자건 강력하게 금지" — 태그·나이·학년·로리/쇼타까지 막고,
    성인 그림과 비성적인 아이 그림은 막지 않는다."""

    def test_minor_words_tags_ages_and_grades_with_anything_revealing_are_blocked(self):
        for text in ("비키니 입은 초등학생 그려줘", "A sexy schoolgirl in a bikini.", "1girl, female_child, nude",
                     "15살 여자 수영복", "16 year old girl, lingerie", "고2 여자 속옷", "교복 입은 여자 야한 포즈",
                     "1girl, school uniform, nsfw", "young girl in bikini", "e621 cub, explicit", "꼬마 알몸",
                     "under 18, nude", "15-year-old, bikini", "미소녀 비키니", "teen girl, cleavage", "1girl, aged_down, sexy"):
            self.assertIn("미성년자", blocked_reason(text) or "", text)

    def test_loli_and_shota_are_always_blocked(self):
        for text in ("로리 캐릭터 그려줘", "loli, smile", "shota", "lolicon", "쇼타 캐릭터"):
            self.assertIn("미성년자", blocked_reason(text) or "", text)

    def test_adults_and_innocent_pictures_are_not_blocked(self):
        for text in ("비키니 입은 성인 여성 그려줘", "1girl, adult, bikini, beach", "young woman in a swimsuit",
                     "25 year old woman, lingerie", "아이가 공원에서 노는 그림", "성적표 받은 초등학생", "비에 젖은 소년",
                     "모자를 벗은 소년", "브라운 머리 소녀", "15세기 성 그려줘", "칼로리 높은 음식", "쇼타임 무대",
                     "lion cub in savanna", "baby blue dress woman, cleavage"):
            self.assertIsNone(blocked_reason(text), text)


class ModelRouterTests(unittest.TestCase):
    """그림 종류에 따라 Z-Image·Qwen-Image 2.1·NoobAI-XL 을 고르고, 모델마다 다른 프롬프트를 쓴다(2026-10-11)."""

    LONG = ("검은 갑옷을 입은 남자가 왼손으로 검을 들고 있고 오른쪽에는 붉은 드레스를 입은 여성이 서 있으며 뒤에서는 성이 "
            "무너지고 있고 하늘에는 번개가 치고 카메라는 아래쪽에서 35mm 렌즈로 올려다보는 구도로 그려줘")

    def setUp(self):
        self.fake = FakeComfy()
        self.addCleanup(self.fake.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        comfy = self.root / "comfy"
        for files in MODEL_FILES.values():
            for folder, name in files:
                (comfy / "ComfyUI" / "models" / folder).mkdir(parents=True, exist_ok=True)
                (comfy / "ComfyUI" / "models" / folder / name).write_bytes(b"x")
        self.gen = ComfyImageGenerator(self.root / "images", url=self.fake.url, comfy_dir=comfy, poll_seconds=0.01)
        self.writer = PromptWriter("1girl, knight, silver armor, holding sword, castle, from below")
        self.core = make_core(self.root, self.gen, self.writer)
        self.sid = self.core.get_or_create_session(False)

    @staticmethod
    def kinds(graph):
        return {node["class_type"] for node in graph.values()}

    def test_choice_from_words(self):
        self.assertEqual(choose_image_model("노을 지는 바닷가 그려줘")[0], "zimage")
        self.assertEqual(choose_image_model("애니 스타일 기사 그려줘")[0], "anime")
        self.assertEqual(choose_image_model("furry wolf warrior 그려줘")[0], "anime")
        self.assertEqual(choose_image_model("정밀하게 도시 야경 그려줘")[0], "qwen")
        self.assertEqual(choose_image_model(self.LONG)[0], "qwen")
        self.assertEqual(choose_image_model("z-image로 애니 캐릭터 그려줘")[0], "zimage")
        self.assertEqual(choose_image_model("더 밝게 다시 그려줘", "anime", "1girl, knight")[0], "anime")

    def test_the_anime_model_is_never_used_with_a_minor_word(self):
        model, note = choose_image_model("애니풍 교복 소녀 그려줘")
        self.assertEqual(model, "zimage")
        self.assertIn("미성년자", note)
        self.assertEqual(choose_image_model("애니로 다시 그려줘", "zimage", "a girl, 12 years old")[0], "zimage")

    def test_anime_prompts_always_push_away_children(self):
        positive, negative = anime_prompts("1girl, knight, armor")
        self.assertTrue(positive.startswith("masterpiece, best quality"))
        self.assertIn("loli, shota, child", negative)
        self.assertIn("nsfw", negative)  # 성적인 부탁이 아니면 노출을 막는다
        self.assertIn("furry", negative)
        positive, negative = anime_prompts("1girl, bikini, beach")
        self.assertIn("adult, 1girl", positive)
        self.assertIn("loli, shota, child", negative)
        self.assertNotIn("nsfw", negative)

    def test_an_anime_request_writes_danbooru_tags_and_uses_noobai(self):
        reply, _ = self.core.handle(self.sid, "애니 스타일 기사 그려줘")
        self.assertIn("NoobAI-XL 1.1", reply)
        graph = self.fake.prompts[-1]
        self.assertEqual(graph["1"]["inputs"]["ckpt_name"], ANIME_CKPT)
        self.assertIn("1girl, knight", graph["5"]["inputs"]["text"])
        self.assertIn("loli", graph["6"]["inputs"]["text"])
        self.assertIn("Danbooru", self.writer.seen[0])
        self.core.handle(self.sid, "더 밝게 다시 그려줘")  # 고치면 같은 모델
        self.assertEqual(self.fake.prompts[-1]["1"]["inputs"]["ckpt_name"], ANIME_CKPT)

    def test_a_long_detailed_request_uses_qwen_image(self):
        self.writer.reply = "A knight in black armor holds a sword in his left hand; a woman in a red dress stands on the right."
        reply, _ = self.core.handle(self.sid, self.LONG)
        self.assertIn("Qwen-Image 2.1", reply)
        graph = self.fake.prompts[-1]
        self.assertEqual(graph["1"]["inputs"]["unet_name"], QWEN_UNET)
        self.assertIn("TextEncodeQwenImage21", self.kinds(graph))
        self.assertIn("Qwen-Image 2.1", self.writer.seen[-1])  # seen[0] 은 대화 모델에게 그림 모델을 묻는 것(ADR-107)

    def test_tags_with_a_minor_word_fall_back_to_z_image(self):
        self.writer.reply = "1girl, school uniform, classroom"
        reply, _ = self.core.handle(self.sid, "애니 스타일 교실 그려줘")
        self.assertIn("미성년자로 보이는 표현", reply)
        self.assertEqual(self.fake.prompts[-1]["1"]["inputs"]["unet_name"], UNET)

    def test_the_direct_prompt_dialog_can_pick_any_model_even_for_realism(self):
        from jarvis.app import handle_command

        reply = handle_command(self.core, self.sid, "/image-prompt --model=anime 1girl, realistic, photorealistic")[0]
        self.assertIn("NoobAI-XL 1.1", reply)
        graph = self.fake.prompts[-1]
        self.assertEqual(graph["1"]["inputs"]["ckpt_name"], ANIME_CKPT)
        self.assertIn("1girl, realistic, photorealistic", graph["5"]["inputs"]["text"])
        self.assertNotIn("--model", graph["5"]["inputs"]["text"])
        handle_command(self.core, self.sid, "/image-prompt --model=qwen A rainy neon alley")
        self.assertEqual(self.fake.prompts[-1]["1"]["inputs"]["unet_name"], QWEN_UNET)
        handle_command(self.core, self.sid, "/image-prompt A rainy neon alley")  # 고르지 않으면 자동
        self.assertEqual(self.fake.prompts[-1]["1"]["inputs"]["unet_name"], UNET)
        self.assertEqual(self.writer.seen, [])  # 직접 입력은 다시 쓰지 않는다

    def test_a_chosen_anime_model_still_refuses_minor_words(self):
        from jarvis.app import handle_command

        reply = handle_command(self.core, self.sid, "/image-prompt --model=anime 1girl, school uniform, classroom")[0]
        self.assertIn("미성년자로 보이는 표현", reply)
        self.assertEqual(self.fake.prompts[-1]["1"]["inputs"]["unet_name"], UNET)
        reply = handle_command(self.core, self.sid, "/image-prompt --model=anime 1girl, school uniform, nsfw")[0]
        self.assertIn("미성년자", reply)
        self.assertEqual(len(self.fake.prompts), 1)

    def test_missing_model_files_draw_with_z_image(self):
        (self.root / "comfy" / "ComfyUI" / "models" / "checkpoints" / ANIME_CKPT).unlink()
        reply, _ = self.core.handle(self.sid, "애니 스타일 기사 그려줘")
        self.assertIn("NoobAI-XL 1.1 파일이 아직 없어", reply)
        self.assertEqual(self.fake.prompts[-1]["1"]["inputs"]["unet_name"], UNET)


class CharacterTests(unittest.TestCase):
    """같은 캐릭터로 다른 장면(ADR-074) — 저장·이름으로 쓰기·방금 그림 쓰기·두 캐릭터·고치기·명령."""

    def setUp(self):
        self.fake = FakeComfy()
        self.addCleanup(self.fake.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gen = ComfyImageGenerator(self.root / "data" / "images", url=self.fake.url, poll_seconds=0.01)
        self.writer = PromptWriter("A woman with a short silver bob and a red bomber jacket, webtoon style.")
        self.core = make_core(self.root, self.gen, self.writer)
        self.sid = self.core.get_or_create_session(False)

    def draw_and_save(self, name, text="은발 단발에 빨간 점퍼를 입은 여성 그려줘"):
        self.core.handle(self.sid, text)
        reply, decision = self.core.handle(self.sid, f"이 캐릭터를 {name}로 저장해줘")
        self.assertEqual((decision.destination, decision.reason), (Destination.TOOL_IMAGE_GEN, "save character"))
        return reply

    @staticmethod
    def nodes(graph):
        return [node["class_type"] for node in graph.values()]

    def test_save_copies_the_picture_and_its_prompt(self):
        reply = self.draw_and_save("은비")
        self.assertIn("'은비' 캐릭터로 저장했습니다", reply)
        entry = self.gen.characters.get("은비")
        self.assertTrue(self.gen.characters.image(entry).is_file())
        self.assertIn("silver bob", entry["prompt"])
        self.assertEqual(len(self.fake.prompts), 1)  # 저장은 그리지 않는다

    def test_a_saved_name_draws_with_the_reference_on_the_edit_model(self):
        self.draw_and_save("은비")
        self.writer.reply = "The same woman from image 1, same face and red jacket, drinking coffee in a cafe."
        reply, decision = self.core.handle(self.sid, "은비가 카페에서 커피 마시는 장면 그려줘")
        self.assertEqual(decision.destination, Destination.TOOL_IMAGE_GEN)
        self.assertIn("FLUX.2 Klein 4B(캐릭터 유지: 은비)", reply)
        self.assertEqual(len(self.fake.uploads), 1)
        graph = self.fake.prompts[-1]
        self.assertEqual(self.nodes(graph).count("ReferenceLatent"), 2)
        self.assertIn({"class_type": "UNETLoader", "inputs": {"unet_name": "flux-2-klein-4b-fp8.safetensors",
                                                              "weight_dtype": "default"}}, graph.values())
        self.assertIn({"class_type": "LoadImage", "inputs": {"image": self.fake.uploads[0]}}, graph.values())
        self.assertIn("Image 1: character '은비' — A woman with a short silver bob", self.writer.seen[-1])

    def test_the_picture_just_drawn_can_be_used_without_saving(self):
        self.core.handle(self.sid, "은발 단발에 빨간 점퍼를 입은 여성 그려줘")
        reply, _ = self.core.handle(self.sid, "방금 그림의 캐릭터로 바닷가에 있는 장면 그려줘")
        self.assertIn("캐릭터 유지: 앞 그림", reply)
        self.assertIn("Image 1: the previous picture", self.writer.seen[-1])
        self.assertEqual(len(self.fake.uploads), 1)

    def test_two_saved_characters_go_in_the_order_they_are_named(self):
        self.draw_and_save("은비")
        self.writer.reply = "A tall man with black hair and a navy coat, webtoon style."
        self.draw_and_save("민준이", "검은 머리에 남색 코트를 입은 남자 그려줘")
        reply, _ = self.core.handle(self.sid, "민준과 은비가 대화하는 장면 그려줘")  # '민준이' 로 저장해도 '민준과' 를 찾는다
        self.assertIn("캐릭터 유지: 민준이, 은비", reply)
        self.assertEqual(self.nodes(self.fake.prompts[-1]).count("ReferenceLatent"), 4)
        seen = self.writer.seen[-1]
        self.assertLess(seen.index("Image 1: character '민준이'"), seen.index("Image 2: character '은비'"))

    def test_changing_a_character_picture_keeps_the_same_reference(self):
        self.draw_and_save("은비")
        self.core.handle(self.sid, "은비가 카페에 있는 장면 그려줘")
        reply, decision = self.core.handle(self.sid, "더 밝게 다시 그려줘")
        self.assertEqual(decision.destination, Destination.TOOL_IMAGE_GEN)
        self.assertIn("캐릭터 유지: 은비", reply)
        self.assertIn("Previous prompt:", self.writer.seen[-1])

    def test_saving_works_after_a_restart_from_the_conversation_record(self):
        self.core.handle(self.sid, "은발 단발에 빨간 점퍼를 입은 여성 그려줘")
        restarted = make_core(self.root, self.gen, self.writer)  # 같은 DB, 메모리의 '마지막 그림' 은 없다
        reply, _ = restarted.handle(self.sid, "이 캐릭터를 은비로 저장해줘")
        self.assertIn("'은비' 캐릭터로 저장했습니다", reply)
        self.assertIn("silver bob", self.gen.characters.get("은비")["prompt"])

    def test_nothing_to_save_explains_what_to_do(self):
        reply, _ = self.core.handle(self.sid, "이 캐릭터를 은비로 저장해줘")
        self.assertIn("저장할 그림이 없습니다", reply)

    def test_character_commands(self):
        from jarvis.app import HELP_TEXT, handle_command

        self.assertIn("/characters", HELP_TEXT)
        self.assertIn("저장한 캐릭터가 없습니다", handle_command(self.core, self.sid, "/characters")[0])
        self.core.handle(self.sid, "은발 단발에 빨간 점퍼를 입은 여성 그려줘")
        self.assertIn("'은비' 캐릭터로 저장했습니다", handle_command(self.core, self.sid, "/character save 은비")[0])
        self.assertIn("- 은비", handle_command(self.core, self.sid, "/characters")[0])
        self.assertIn("지웠습니다", handle_command(self.core, self.sid, "/character delete 은비")[0])
        self.assertEqual(self.gen.characters.names(), [])

    def test_bad_names_are_refused(self):
        self.core.handle(self.sid, "은발 단발에 빨간 점퍼를 입은 여성 그려줘")
        self.assertIn("2~12자", self.core.save_character(self.sid, "a"))


class VideoTests(unittest.TestCase):
    """영상 만들기(ADR-075) — 2026-10-03 사용자 "영상 못 만든다고 그러는데?"."""

    def setUp(self):
        self.fake = FakeComfy()
        self.addCleanup(self.fake.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gen = ComfyImageGenerator(self.root / "data" / "images", url=self.fake.url, poll_seconds=0.01)
        self.core = make_core(self.root, self.gen, PromptWriter("A cat chasing a butterfly in a sunny garden, camera follows."))
        self.sid = self.core.get_or_create_session(False)

    def test_routing(self):
        from jarvis.router import route_request

        for text in ("고양이가 뛰어노는 영상 만들어줘", "방금 그림 움직이게 해줘", "make a video of a dragon"):
            self.assertEqual(route_request(text).destination, Destination.TOOL_VIDEO_GEN, text)
        for text in ("이 영상 요약해줘", "유튜브 영상 찾아줘", "고양이 그려줘"):
            self.assertNotEqual(route_request(text).destination, Destination.TOOL_VIDEO_GEN, text)

    def test_a_natural_request_makes_a_720p_clip_and_shows_it(self):
        reply, decision = self.core.handle(self.sid, "고양이가 나비를 쫓는 영상 만들어줘")
        self.assertEqual(decision.destination, Destination.TOOL_VIDEO_GEN)
        self.assertIn("alex-video:", reply)
        name = re.search(r"alex-video:([^)]+)", reply).group(1)
        self.assertEqual(self.gen.video_path(name).read_bytes(), MP4)
        kinds = [node["class_type"] for node in self.fake.prompts[-1].values()]
        self.assertIn("Wan22ImageToVideoLatent", kinds)
        self.assertNotIn("SeedVR2Conditioning", kinds)
        self.assertEqual(self.fake.frees[-1], {"unload_models": True, "free_memory": False})

    def test_direct_video_prompt_reaches_comfy_without_chat_rewrite(self):
        from jarvis.app import handle_command

        prompt = "A cat runs through a garden, camera follows smoothly."
        reply = handle_command(self.core, self.sid, "/video-prompt " + prompt)[0]
        self.assertIn("직접 입력한 프롬프트: " + prompt, reply)
        self.assertEqual(self.fake.prompts[-1]["5"]["inputs"]["text"], prompt)
        self.assertEqual(self.core.local_summarizer.seen, [])
        self.assertIn("alex-video:", reply)

    def test_fhd_adds_the_upscaler(self):
        reply, _ = self.core.handle(self.sid, "FHD로 고양이가 나비를 쫓는 영상 만들어줘")
        self.assertIn("1920×1056", reply)
        self.assertIn("SeedVR2Conditioning", [node["class_type"] for node in self.fake.prompts[-1].values()])

    def test_animate_the_picture_just_drawn(self):
        self.core.handle(self.sid, "창가에서 자는 고양이 그려줘")
        reply, _ = self.core.handle(self.sid, "방금 그림 움직이게 해줘")
        self.assertIn("앞 그림에서 시작", reply)
        latent = next(node for node in self.fake.prompts[-1].values() if node["class_type"] == "Wan22ImageToVideoLatent")
        self.assertIn("start_image", latent["inputs"])
        self.assertEqual(len(self.fake.uploads), 1)

    def test_the_model_is_told_it_can_make_videos(self):
        self.assertIn("영상을 못 만든다고 말하지 않는다", self.core._environment_line(Destination.LOCAL_GENERAL))

    def test_only_app_made_video_names(self):
        for name in ("../alex.db", "x.mp4", "20261003-120000-1.png"):
            self.assertIsNone(self.gen.video_path(name), name)


class Wan14VideoTests(unittest.TestCase):
    """14B 파일이 있으면 글로 만드는 영상은 14B(ADR-075 보충) — 2026-10-03 사용자 "14b 설치했어 … 진행해"."""

    def setUp(self):
        self.fake = FakeComfy()
        self.addCleanup(self.fake.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.models = self.root / "comfy" / "ComfyUI" / "models"
        for folder, name in WAN14_FILES.values():
            (self.models / folder).mkdir(parents=True, exist_ok=True)
            (self.models / folder / name).write_bytes(b"")
        self.gen = ComfyImageGenerator(self.root / "data" / "images", url=self.fake.url, comfy_dir=self.root / "comfy",
                                       poll_seconds=0.01)
        self.core = make_core(self.root, self.gen, PromptWriter("A cat chasing a butterfly in a sunny garden, camera follows."))
        self.sid = self.core.get_or_create_session(False)

    def nodes(self, kind):
        return [node["inputs"] for node in self.fake.prompts[-1].values() if node["class_type"] == kind]

    def test_a_text_request_uses_14b_with_the_4_step_lora(self):
        reply, _ = self.core.handle(self.sid, "고양이가 나비를 쫓는 영상 만들어줘")
        self.assertIn("Wan 2.2 14B, 1280×720", reply)
        self.assertEqual(sorted(node["unet_name"] for node in self.nodes("UNETLoader")),
                         sorted([WAN14_FILES["high"][1], WAN14_FILES["low"][1]]))
        self.assertEqual(len(self.nodes("LoraLoaderModelOnly")), 2)
        high, low = sorted(self.nodes("KSamplerAdvanced"), key=lambda node: node["start_at_step"])
        self.assertEqual((high["end_at_step"], high["add_noise"], high["return_with_leftover_noise"]), (2, "enable", "enable"))
        self.assertEqual((low["start_at_step"], low["end_at_step"], low["add_noise"]), (2, 4, "disable"))
        self.assertEqual({high["cfg"], low["cfg"], high["steps"], low["steps"]}, {1.0, 4})
        latent = self.nodes("EmptyHunyuanLatentVideo")[0]
        self.assertEqual((latent["width"], latent["height"], latent["length"]), (1280, 720, 81))
        self.assertEqual(self.nodes("CreateVideo")[0]["fps"], 16.0)
        self.assertEqual(self.fake.prompts[-1]["6"]["inputs"]["text"], WAN_NEGATIVE)
        graph = self.fake.prompts[-1]
        links = [value[0] for node in graph.values() for value in node["inputs"].values()
                 if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)]
        self.assertTrue(links and all(link in graph for link in links), links)

    def test_fhd_on_14b_is_full_1080p(self):
        reply, _ = self.core.handle(self.sid, "FHD로 고양이가 나비를 쫓는 영상 만들어줘")
        self.assertIn("1920×1080", reply)
        self.assertEqual(self.nodes("ResizeImageMaskNode")[0]["input"], ["9", 0])

    def test_animating_a_picture_still_uses_5b(self):
        self.core.handle(self.sid, "창가에서 자는 고양이 그려줘")
        reply, _ = self.core.handle(self.sid, "방금 그림 움직이게 해줘")
        self.assertIn("Wan 2.2 5B, 1280×704", reply)
        self.assertIn("start_image", self.nodes("Wan22ImageToVideoLatent")[0])

    def test_direct_prompt_goes_to_14b_unchanged(self):
        from jarvis.app import handle_command

        prompt = "A dragon flies over snowy mountains at dawn, the camera tracks it."
        reply = handle_command(self.core, self.sid, "/video-prompt " + prompt)[0]
        self.assertIn("Wan 2.2 14B", reply)
        self.assertEqual(self.fake.prompts[-1]["5"]["inputs"]["text"], prompt)

    def test_the_chat_model_hears_the_14b_numbers(self):
        line = self.core._environment_line(Destination.LOCAL_GENERAL)
        self.assertIn("영상도 만든다(Wan 2.2 14B)", line)
        self.assertIn("1920×1080", line)

    def test_a_missing_file_falls_back_to_5b(self):
        folder, name = WAN14_FILES["low_lora"]
        (self.models / folder / name).unlink()
        self.assertEqual(self.gen.video_plan().model, "Wan 2.2 5B")

    def install_i2v(self):
        from jarvis.imagegen import WAN14_I2V_FILES

        for folder, name in WAN14_I2V_FILES.values():
            (self.models / folder).mkdir(parents=True, exist_ok=True)
            (self.models / folder / name).write_bytes(b"")
        return WAN14_I2V_FILES

    def test_animating_a_picture_uses_14b_i2v_when_installed(self):
        """2026-10-11: 그림에서 시작하는 영상도 14B — 템플릿 video_wan2_2_14B_i2v(Lightning LoRA 켬)."""
        files = self.install_i2v()
        self.core.handle(self.sid, "창가에서 자는 고양이 그려줘")
        reply, _ = self.core.handle(self.sid, "방금 그림 움직이게 해줘")
        self.assertIn("Wan 2.2 14B, 1280×720", reply)  # 시험 그림은 크기를 못 읽어 가로(비율은 아래 시험)
        self.assertEqual(sorted(node["unet_name"] for node in self.nodes("UNETLoader")),
                         sorted([files["high"][1], files["low"][1]]))
        self.assertEqual(sorted(node["lora_name"] for node in self.nodes("LoraLoaderModelOnly")),
                         sorted([files["high_lora"][1], files["low_lora"][1]]))
        i2v = self.nodes("WanImageToVideo")[0]
        self.assertEqual((i2v["width"], i2v["height"], i2v["length"], i2v["start_image"]), (1280, 720, 81, ["40", 0]))
        high, low = sorted(self.nodes("KSamplerAdvanced"), key=lambda node: node["start_at_step"])
        self.assertEqual((high["positive"], high["negative"], high["latent_image"]), (["7", 0], ["7", 1], ["7", 2]))
        self.assertEqual((low["positive"], low["latent_image"], low["end_at_step"]), (["7", 0], ["8", 0], 4))
        self.assertEqual(self.nodes("EmptyHunyuanLatentVideo"), [])
        graph = self.fake.prompts[-1]
        links = [value[0] for node in graph.values() for value in node["inputs"].values()
                 if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)]
        self.assertTrue(links and all(link in graph for link in links), links)

    def test_i2v_size_follows_the_picture_shape(self):
        import struct
        from jarvis.imagegen import wan14_i2v_size

        def png(width, height):
            path = self.root / f"{width}x{height}.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00" + b"\x00" * 4)
            return path

        self.assertEqual(wan14_i2v_size(png(1024, 1024)), (960, 960))
        self.assertEqual(wan14_i2v_size(png(768, 1344)), (720, 1280))
        self.assertEqual(wan14_i2v_size(png(1344, 768)), (1280, 720))
        self.assertEqual(wan14_i2v_size(self.root / "missing.png"), (1280, 720))
        self.install_i2v()
        self.assertEqual(self.gen.video_plan(png(768, 1344), fhd=True).size, (1080, 1920))


class WebImageTests(unittest.TestCase):
    def setUp(self):
        from jarvis.web import WebAppState, make_server

        self.fake = FakeComfy()
        self.addCleanup(self.fake.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.gen = ComfyImageGenerator(root / "images", url=self.fake.url, poll_seconds=0.01)
        core = make_core(root, self.gen)
        self.saved = self.gen.generate("a cat", seed=3).path
        self.core = core
        self.state = WebAppState(core, core.get_or_create_session(False), token="test-token")
        self.server = make_server(self.state, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def get(self, path, token="test-token"):
        return urllib.request.urlopen(urllib.request.Request(self.url + path, headers={"X-Jarvis-Token": token}), timeout=3)

    def test_the_picture_is_served_only_with_the_token_and_a_valid_name(self):
        with self.get(f"/api/image?name={self.saved.name}") as r:
            self.assertEqual(r.read(), PNG)
            self.assertEqual(r.headers["Content-Type"], "image/png")
            self.assertIn("img-src blob:", r.headers["Content-Security-Policy"])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get(f"/api/image?name={self.saved.name}", token="wrong")
        self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/api/image?name=..%2Falex.db")
        self.assertEqual(caught.exception.code, 404)

    def upload(self, data, content_type="image/png", token="test-token", name="흑태자.png"):
        request = urllib.request.Request(self.url + "/api/image/upload", data=data, method="POST", headers={
            "X-Jarvis-Token": token, "Content-Type": content_type, "X-File-Name": urllib.parse.quote(name)})
        return urllib.request.urlopen(request, timeout=5)

    def test_an_uploaded_picture_becomes_the_last_picture_and_can_be_saved_as_a_character(self):
        """2026-10-03: "창세기전2 흑태자 그려줘" 에 ALEX 가 인터넷 그림을 볼 수 없다고 답했다 — 참고 그림을 올리는 입구."""
        jpeg = b"\xff\xd8\xff\xe0" + b"fake-jpeg-body"
        with self.upload(jpeg, "image/jpeg") as r:
            reply = json.load(r)["response"]
        self.assertIn("올린 그림을 받았습니다", reply)
        name = re.search(r"alex-image:([^)]+)", reply).group(1)
        self.assertTrue(name.endswith(".jpg"))
        with self.get(f"/api/image?name={name}") as r:
            self.assertEqual((r.read(), r.headers["Content-Type"]), (jpeg, "image/jpeg"))
        saved, _ = self.core.handle(self.state.session_id, "이 캐릭터를 흑태자로 저장해줘")
        self.assertIn("'흑태자' 캐릭터로 저장했습니다", saved)
        self.assertTrue(self.gen.characters.image(self.gen.characters.get("흑태자")).name.endswith(".jpg"))
        reply, _ = self.core.handle(self.state.session_id, "흑태자가 성 위에 서 있는 장면 그려줘")
        self.assertIn("캐릭터 유지: 흑태자", reply)
        self.assertTrue(self.fake.uploads[-1].endswith(".jpg"))

    def test_uploads_need_the_token_a_real_picture_and_a_small_size(self):
        for data, kind, token, code in ((PNG, "image/png", "wrong", 403), (b"<html>", "image/png", "test-token", 400),
                                        (PNG, "text/plain", "test-token", 415)):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.upload(data, kind, token)
            self.assertEqual(caught.exception.code, code)
        with self.assertRaisesRegex(ValueError, "10MB"):  # 너무 큰 본문은 서버가 읽기 전에 413 으로 끊는다 — 저장 쪽 한도도 같다
            self.gen.store_picture(b"\x89PNG\r\n\x1a\n" + b"0" * 10_000_001)

    def test_character_add_from_a_local_file(self):
        from jarvis.app import handle_command

        picture = Path(self.temp.name) / "ref.png"
        picture.write_bytes(PNG)
        reply = handle_command(self.core, self.state.session_id, f"/character add 흑태자 {picture}")[0]
        self.assertIn("'흑태자' 캐릭터로 저장했습니다", reply)
        self.assertIn("파일을 찾지 못했습니다", handle_command(self.core, self.state.session_id, "/character add 아무개 C:/nope.png")[0])
        self.assertIn("사용법", handle_command(self.core, self.state.session_id, "/character add 흑태자")[0])

    def test_the_model_is_told_it_can_use_reference_pictures(self):
        line = self.core._environment_line(Destination.LOCAL_GENERAL)
        self.assertIn("그림 올리기", line)
        self.assertIn("인터넷 그림을 볼 수 없다", line)

    def test_the_screen_has_the_upload_button(self):
        html = (Path(__file__).resolve().parents[1] / "jarvis" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="attach"', html)
        self.assertIn('accept="image/png,image/jpeg,image/webp"', html)

    def test_videos_are_served_with_the_token(self):
        folder = self.gen.out_dir.parent / "videos"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "20261003-130000-7.mp4").write_bytes(MP4)
        with self.get("/api/video?name=20261003-130000-7.mp4") as r:
            self.assertEqual((r.read(), r.headers["Content-Type"]), (MP4, "video/mp4"))
            self.assertIn("media-src blob:", r.headers["Content-Security-Policy"])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/api/video?name=20261003-130000-7.mp4", token="wrong")
        self.assertEqual(caught.exception.code, 403)

    def test_the_screen_turns_the_marker_line_into_a_picture(self):
        js = (Path(__file__).resolve().parents[1] / "jarvis" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("alex-image:", js)
        self.assertIn("/api/image?name=", js)
        self.assertIn("URL.createObjectURL", js)


if __name__ == "__main__":
    unittest.main()
