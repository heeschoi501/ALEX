"""LTX audio/video routing contracts with a fake ComfyUI; no real GPU use."""
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis.app import handle_command
from jarvis.imagegen import ComfyImageGenerator, LTX_FILES, LTX_LABEL, choose_video_model
from jarvis.providers import GenerationCancelled
from jarvis.router import Destination, route_request
from tests.test_imagegen import FakeComfy, PromptWriter, make_core


class LtxRoutingTests(unittest.TestCase):
    def test_long_audio_instruction_after_video_request(self):
        text = ("방금 그림의 여기사가 검을 들어 올리는 영상 만들어줘. "
                "바람 소리와 갑옷이 찰랑거리는 소리를 넣어줘.")
        self.assertEqual(route_request(text).destination, Destination.TOOL_VIDEO_GEN)
        for tail in ("바람 소리와 갑옷이 찰랑거리는 이유를 설명해줘.",
                     "소리도 넣어줘 라는 요청의 의미를 길게 설명해줘.",
                     "소리도 넣어줘. 이메일도 친구에게 보내줘."):
            self.assertNotEqual(route_request("여기사 영상 만들어줘. " + tail).destination,
                                Destination.TOOL_VIDEO_GEN, tail)

    def test_audio_keywords_and_explicit_choice(self):
        for text in ("음악과 고양이 영상", "목소리와 대사 영상", "cat video with sound", "LTX video"):
            self.assertEqual(choose_video_model(text), "ltx", text)
            self.assertEqual(choose_video_model(text, "wan"), "wan", text)
        self.assertEqual(choose_video_model("고양이 영상"), "wan")
        self.assertEqual(choose_video_model("고양이 영상", "ltx"), "ltx")


class LtxVideoTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeComfy()
        self.addCleanup(self.fake.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.models = self.root / "comfy" / "ComfyUI" / "models"
        for folder, name in LTX_FILES.values():
            path = self.models / folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fake model")
        self.gen = ComfyImageGenerator(self.root / "data/images", url=self.fake.url,
                                       comfy_dir=self.root / "comfy", poll_seconds=.01)
        self.writer = PromptWriter("A cat walks in a garden. Birds chirp and leaves rustle.")
        self.core = make_core(self.root, self.gen, self.writer)
        self.sid = self.core.get_or_create_session(False)

    def test_sound_request_reaches_ltx_with_audio_prompt_and_mux(self):
        reply, decision = self.core.handle(self.sid, "새 소리가 나는 고양이 영상 만들어줘")
        self.assertEqual(decision.destination, Destination.TOOL_VIDEO_GEN)
        self.assertIn(LTX_LABEL, reply)
        self.assertIn("synchronized audio", self.writer.seen[-1])
        graph = self.fake.prompts[-1]
        self.assertEqual(graph["10"]["inputs"]["audio"], ["62", 0])
        self.assertEqual(graph["43"]["inputs"]["length"], 121)
        self.assertEqual(graph["10"]["inputs"]["fps"], 24.0)
        self.assertEqual(self.fake.frees[-1], {"unload_models": True, "free_memory": False})

    def test_model_is_told_audio_video_is_available_only_when_ready(self):
        self.assertIn("소리 포함 영상", self.core._environment_line(Destination.LOCAL_GENERAL))
        folder, name = LTX_FILES["audio_vae"]
        (self.models / folder / name).unlink()
        self.assertNotIn("소리 포함 영상", self.core._environment_line(Destination.LOCAL_GENERAL))

    def test_direct_ltx_preserves_prompt_and_forced_wan_overrides_audio(self):
        prompt = "A cat walks with sound of birds chirping."
        for model in ("ltx", "wan"):
            reply = handle_command(self.core, self.sid, f"/video-prompt --model={model} {prompt}")[0]
            graph = self.fake.prompts[-1]
            self.assertEqual(graph["5"]["inputs"]["text"], prompt)
            self.assertEqual("audio" in graph["10"]["inputs"], model == "ltx")
            self.assertIn("직접 입력한 프롬프트: " + prompt, reply)
        self.assertEqual(self.writer.seen, [])

    def test_natural_ltx_keeps_audio_after_long_visual_description(self):
        visual = "An adult knight moves slowly through the ruined castle in cinematic light. " * 9
        audio = "Wind whistles and armor rattles audibly. No text, no black frames."
        writer = PromptWriter(visual + audio)
        self.core.local_summarizer = writer
        self.core.handle(self.sid, "바람 소리와 함께 여기사 영상 만들어줘")
        self.assertEqual(self.fake.prompts[-1]["5"]["inputs"]["text"], visual + audio)
        self.assertIn(audio, self.fake.prompts[-1]["5"]["inputs"]["text"])
        self.core.handle(self.sid, "여기사 영상 만들어줘")
        self.assertEqual(self.fake.prompts[-1]["5"]["inputs"]["text"], (visual + audio)[:600])

    def test_missing_ltx_files_use_wan_and_explain_absent_audio(self):
        folder, name = LTX_FILES["unet"]
        (self.models / folder / name).unlink()
        reply, _ = self.core.handle(self.sid, "음악이 나오는 고양이 영상 만들어줘")
        self.assertIn("Wan", reply)
        self.assertIn("소리는 포함되지 않습니다", reply)
        self.assertNotIn("synchronized audio", self.writer.seen[-1])
        self.assertNotIn("audio", self.fake.prompts[-1]["10"]["inputs"])

    def test_each_required_file_is_needed(self):
        for folder, name in LTX_FILES.values():
            path = self.models / folder / name
            data = path.read_bytes()
            path.unlink()
            self.assertFalse(self.gen.has_ltx(), name)
            path.write_bytes(data)
        self.assertTrue(self.gen.has_ltx())

    def test_two_stage_graph_has_valid_links_for_text_and_start_image(self):
        plan = self.gen.video_plan(model="ltx")
        self.assertEqual(plan.size, (1280, 704))
        for start in (None, "cat.png"):
            graph = self.gen.video_workflow("cat", 17, start, plan=plan)
            for node in graph.values():
                for value in node["inputs"].values():
                    if isinstance(value, list) and len(value) == 2:
                        self.assertIn(value[0], graph)
                        self.assertIsInstance(value[1], int)
                        self.assertGreaterEqual(value[1], 0)
            self.assertEqual(graph["55"]["inputs"]["audio_latent"], ["52", 1])
            self.assertEqual(graph["10"]["inputs"]["audio"], ["62", 0])
            if start:
                self.assertEqual(graph["46"]["inputs"]["video_latent"], ["44", 0])
                self.assertEqual(graph["55"]["inputs"]["video_latent"], ["54", 0])
                self.assertEqual(graph["40"]["inputs"]["image"], start)

    def test_ltx_dimensions_survive_half_size_latent_quantization(self):
        portrait = self.root / "portrait.png"
        portrait.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" + struct.pack(">II", 720, 1280)
                             + b"\x08\x02\x00\x00\x00" + b"\x00" * 4)
        for start in (False, portrait):
            plan = self.gen.video_plan(start, model="ltx")
            graph = self.gen.video_workflow("cat", 17, plan=plan)
            for axis in ("width", "height"):
                half = graph["43"]["inputs"][axis]
                self.assertEqual(half // 32 * 32 * 2, getattr(plan, axis))
        self.assertEqual(self.gen.video_plan(portrait, model="ltx").size, (704, 1280))

    def test_last_picture_conditions_both_video_stages(self):
        self.core.handle(self.sid, "고양이 그려줘")
        reply, _ = self.core.handle(self.sid, "방금 그림 움직이게 해줘, 소리도 포함해서")
        self.assertIn("앞 그림에서 시작", reply)
        graph = self.fake.prompts[-1]
        self.assertEqual(graph["46"]["inputs"]["video_latent"], ["44", 0])
        self.assertEqual(graph["55"]["inputs"]["video_latent"], ["54", 0])
        self.assertEqual(len(self.fake.uploads), 1)

    def test_live_knight_request_with_trailing_audio_runs_i2v(self):
        self.core.handle(self.sid, "고양이 그려줘")
        text = ("방금 그림의 여기사가 은발을 바람에 날리며 검을 천천히 들어 올리고 "
                "카메라가 천천히 다가가는 영상 만들어줘. 바람 소리와 갑옷이 찰랑거리는 소리를 넣어줘.")
        reply, decision = self.core.handle(self.sid, text)
        self.assertEqual(decision.destination, Destination.TOOL_VIDEO_GEN)
        self.assertIn("앞 그림에서 시작", reply)
        graph = self.fake.prompts[-1]
        self.assertEqual(graph["10"]["inputs"]["audio"], ["62", 0])
        self.assertEqual(graph["46"]["inputs"]["video_latent"], ["44", 0])
        self.assertEqual(graph["55"]["inputs"]["video_latent"], ["54", 0])

    def test_ltx_fhd_is_not_reported_or_generated_as_seedvr2(self):
        reply, _ = self.core.handle(self.sid, "FHD 소리 포함 고양이 영상 만들어줘")
        self.assertIn("별도 FHD 확대는 적용하지 않았습니다", reply)
        self.assertNotIn("SeedVR2(FHD)", reply)
        self.assertNotIn("더 큰 화면", reply)
        self.assertFalse(any(node["class_type"] == "SeedVR2Conditioning" for node in self.fake.prompts[-1].values()))
        result = self.gen.generate_video("cat", model="ltx", fhd=True)
        self.assertFalse(result.fhd)

    def test_empty_or_unknown_model_commands_do_not_submit(self):
        for command in ("/video-prompt --model=ltx", "/video-prompt --model=other cat", "/video-prompt --model=ltxwrong cat"):
            handle_command(self.core, self.sid, command)
        self.assertEqual(self.fake.prompts, [])

    def test_minor_sexual_request_is_blocked_for_direct_ltx(self):
        handle_command(self.core, self.sid, "/video-prompt --model=ltx nude 12 year old child")
        self.assertEqual(self.fake.prompts, [])

    def test_cancelled_ltx_still_returns_gpu(self):
        event = threading.Event()
        self.core.command_cancel_event = event
        with patch.object(self.gen, "generate_video", side_effect=GenerationCancelled("")) as generate:
            with self.assertRaises(GenerationCancelled):
                self.core._video_answer(self.sid, "cat", direct=True, model="ltx")
        self.assertIs(generate.call_args.kwargs["cancel_event"], event)
        self.assertEqual(self.fake.frees[-1], {"unload_models": True, "free_memory": False})


if __name__ == "__main__":
    unittest.main()
