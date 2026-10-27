"""Reasoning settings and explicit picture retries; no real models or GPUs."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from jarvis import accounts
from jarvis.app import build_providers
from jarvis.config import Settings
from jarvis.imagegen import MODEL_FILES, QWEN_UNET, UNET, ComfyImageGenerator, image_model_retry
from jarvis.providers import OllamaProvider, StrataProvider
from jarvis.router import Destination
from jarvis.web import make_server
from test_imagegen import FakeComfy, PromptWriter, make_core
from test_web import make_state


class PictureRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fake = FakeComfy()
        self.addCleanup(self.fake.close)
        comfy = self.root / "comfy"
        for files in MODEL_FILES.values():
            for folder, name in files:
                target = comfy / "ComfyUI/models" / folder / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"fixture")
        self.generator = ComfyImageGenerator(self.root / "images", url=self.fake.url,
                                             comfy_dir=comfy, poll_seconds=0.01)
        self.writer = PromptWriter("An adult knight in silver armor, castle at sunset.")
        self.core = make_core(self.root, self.generator, self.writer)
        self.sid = self.core.get_or_create_session(False)

    def picture(self):
        self.core.handle(self.sid, "성 앞의 성인 기사 가로로 그려줘")

    def test_only_explicit_model_retry_commands_are_recognized(self):
        for text, model in (("Qwen으로 다시", "qwen"), ("큐웬으로 다시 해줘", "qwen"),
                            ("이번엔 Z-Image로 다시", "zimage"), ("애니로 다시", "anime"),
                            ("NoobAI로 다시 주세요", None)):
            self.assertEqual(image_model_retry(text), model, text)
        for text in ("Qwen으로 다시 코딩해줘", "Qwen으로 다시 하지 마", "Qwen으로 다시?",
                     "'Qwen으로 다시'는 무슨 뜻이야", "Qwen 모델 설명해줘"):
            self.assertIsNone(image_model_retry(text), text)

    def test_retry_changes_model_and_keeps_the_previous_prompt_and_size(self):
        self.picture()
        first = self.core._last_image[self.sid].copy()
        reply, decision = self.core.handle(self.sid, "Qwen으로 다시")
        self.assertEqual(decision.destination, Destination.TOOL_IMAGE_GEN)
        self.assertEqual(self.fake.prompts[-1]["1"]["inputs"]["unet_name"], QWEN_UNET)
        self.assertIn(first["prompt"], self.writer.seen[-1])
        self.assertEqual((self.core._last_image[self.sid]["width"], self.core._last_image[self.sid]["height"]),
                         (first["width"], first["height"]))
        self.assertIn("Qwen-Image", reply)
        self.core.handle(self.sid, "Z-Image로 다시")
        self.assertEqual(self.fake.prompts[-1]["1"]["inputs"]["unet_name"], UNET)

    def test_saved_picture_context_is_recovered_after_restart(self):
        self.picture()
        previous = self.core._last_image[self.sid].copy()
        self.core._last_image.clear()
        self.core.last_exchange = None
        _, decision = self.core.handle(self.sid, "Qwen으로 다시")
        self.assertEqual(decision.destination, Destination.TOOL_IMAGE_GEN)
        self.assertIn(previous["prompt"], self.writer.seen[-1])
        self.assertEqual((self.core._last_image[self.sid]["width"], self.core._last_image[self.sid]["height"]),
                         (previous["width"], previous["height"]))

    def test_a_picture_in_another_room_does_not_trigger_generation(self):
        self.picture()
        count = len(self.fake.prompts)
        other = self.core.storage.create_session()
        _, decision = self.core.handle(other, "Qwen으로 다시")
        self.assertNotEqual(decision.destination, Destination.TOOL_IMAGE_GEN)
        self.assertEqual(len(self.fake.prompts), count)

    def test_nonimage_conversation_and_negated_requests_do_not_generate(self):
        self.picture()
        self.core.handle(self.sid, "고마워")
        count = len(self.fake.prompts)
        self.core.handle(self.sid, "Qwen으로 다시")
        self.assertEqual(len(self.fake.prompts), count)
        self.picture()
        count = len(self.fake.prompts)
        self.core.handle(self.sid, "Qwen으로 다시 하지 마")
        self.assertEqual(len(self.fake.prompts), count)

    def test_readonly_permission_still_blocks_model_retry(self):
        self.picture()
        count = len(self.fake.prompts)
        self.core.permission_mode = "read"
        reply, _ = self.core.handle(self.sid, "Qwen으로 다시")
        self.assertIn("읽기 전용", reply)
        self.assertEqual(len(self.fake.prompts), count)


class ReasoningSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "settings.json"
        self.state = make_state(self.root)
        self.state.settings = Settings(use_ollama=True, local_backend="strata", ollama_think=True,
                                       strata_model="fixture-model", strata_num_ctx=196608,
                                       strata_max_output_tokens=65536)
        for context in (patch.dict(os.environ, {"JARVIS_CONFIG_FILE": str(self.config)}, clear=True),
                        patch.object(OllamaProvider, "health_check", return_value=True),
                        patch.object(StrataProvider, "health_check", return_value=True)):
            context.start()
            self.addCleanup(context.stop)
        general, coder, _ = build_providers(self.state.settings, self.state.core.hardware)
        self.state.core.providers[Destination.LOCAL_GENERAL] = general
        self.state.core.providers[Destination.LOCAL_CODER] = coder

    def test_reasoning_level_changes_without_engine_restart_or_budget_changes(self):
        from jarvis import model_switch
        with patch.object(model_switch, "start_backend") as start, \
                patch.object(model_switch, "release_current") as release:
            for level in ("low", "high", "medium"):
                self.state.save_settings({"strata_reasoning_effort": level, "ollama_think": True})
                provider = self.state.core.providers[Destination.LOCAL_GENERAL]
                self.assertEqual(provider.reasoning_effort, level)
                self.assertEqual((provider.model, provider.num_ctx, provider.num_predict),
                                 ("fixture-model", 196608, 65536))
                self.assertEqual(json.loads(self.config.read_text(encoding="utf-8"))["strata_reasoning_effort"], level)
            self.state.save_settings({"ollama_think": False})
            self.assertFalse(self.state.core.providers[Destination.LOCAL_GENERAL].think)
            self.assertEqual(self.state.settings.strata_reasoning_effort, "medium")
            start.assert_not_called()
            release.assert_not_called()

    def test_invalid_or_environment_fixed_values_do_not_write_settings(self):
        for change in ({"strata_reasoning_effort": "max"}, {"strata_reasoning_effort": 1},
                       {"strata_num_ctx": 32768}):
            with self.assertRaises(ValueError):
                self.state.save_settings(change)
        with patch.dict(os.environ, {"JARVIS_STRATA_REASONING_EFFORT": "medium"}), self.assertRaises(ValueError):
            self.state.save_settings({"strata_reasoning_effort": "high"})
        self.assertFalse(self.config.exists())

    def start_server(self):
        self.server = make_server(self.state, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def call(self, path, body, cookie=""):
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "X-Jarvis-Token": "test-token", "Cookie": cookie})
        # This fixture clears environment variables. A cached global proxy opener
        # can then lose NO_PROXY and send its loopback requests to an HTTP proxy.
        local = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with local.open(request, timeout=5) as response:
                return response.status, json.load(response), response.headers.get_all("Set-Cookie") or []
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error), error.headers.get_all("Set-Cookie") or []

    def test_shared_settings_reject_changes_during_another_request(self):
        self.start_server()
        self.state.lock.acquire()
        try:
            status, _, _ = self.call("/api/settings", {"settings": {"strata_reasoning_effort": "high"}})
            self.assertEqual(status, 409)
        finally:
            self.state.lock.release()
        self.assertEqual(self.state.settings.strata_reasoning_effort, "medium")
        self.assertFalse(self.config.exists())

    def test_member_cannot_change_shared_reasoning_settings(self):
        self.state.core.storage.create_user("Member", "member", accounts.hash_password("member-password"))
        self.start_server()
        status, _, cookies = self.call("/api/login", {"login": "member", "password": "member-password"}, "alex_manual=1")
        self.assertEqual(status, 200)
        session = next(c.split(";", 1)[0] for c in cookies if c.startswith("alex_session="))
        status, _, _ = self.call("/api/settings", {"settings": {"strata_reasoning_effort": "high"}},
                                  "alex_manual=1; " + session)
        self.assertEqual(status, 403)
        self.assertFalse(self.config.exists())


if __name__ == "__main__":
    unittest.main()
