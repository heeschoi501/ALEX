"""대화 모델이 그림 모델 고르기(ADR-107, 2026-10-22) — 모델 이름·애니·정밀 같은 말이 없고 앞 그림을 고치는 것도 아니면
요약용 설정의 대화 모델이 부탁을 보고 zimage·qwen·anime 중 하나를 고른다. 못 고르면 예전 길이 규칙. 가짜 ComfyUI 만 쓴다."""
from pathlib import Path
import tempfile
import unittest

from jarvis.imagegen import (MODEL_FILES, QWEN_UNET, UNET, ComfyImageGenerator, choose_image_model, explicit_image_model,
                             parse_pick)
from jarvis.providers import ProviderError
from tests.test_imagegen import FakeComfy, PromptWriter, make_core


class PickingWriter(PromptWriter):
    """고르기 질문에는 pick 을, 프롬프트 쓰기에는 영어 프롬프트를 돌려준다."""

    def __init__(self, pick="qwen | 여러 인물 배치"):
        super().__init__("A knight on the left and a queen on the right in a ruined castle, low angle.")
        self.pick, self.picks = pick, 0

    def generate(self, messages, **kwargs):
        if messages[0].content.startswith("You choose which image model"):
            self.picks += 1
            self.seen.append("\n".join(m.content for m in messages))
            if isinstance(self.pick, Exception):
                raise self.pick
            return self.pick
        return super().generate(messages, **kwargs)


class ParseTests(unittest.TestCase):
    def test_model_and_reason(self):
        cases = {"qwen | 여러 인물 배치": ("qwen", "여러 인물 배치"), "Z-Image": ("zimage", ""), "zimage": ("zimage", ""),
                 "`anime` — 애니 그림체": ("anime", "애니 그림체"), "NoobAI: 만화풍": ("anime", "만화풍"),
                 "**qwen** | 포스터 [글자](x)": ("qwen", "포스터 글자x")}
        for reply, expected in cases.items():
            with self.subTest(reply=reply):
                self.assertEqual(parse_pick(reply), expected)

    def test_anything_else_is_not_a_pick(self):
        for reply in ("Prompt: A fluffy cat", "I think qwen is best", "", "flux | 실사", "qwenimage"):
            with self.subTest(reply=reply):
                self.assertIsNone(parse_pick(reply))

    def test_rules_come_first(self):
        self.assertTrue(explicit_image_model("애니 스타일 소녀 그려줘"))
        self.assertTrue(explicit_image_model("정밀하게 성 그려줘"))
        self.assertFalse(explicit_image_model("왕과 왕비와 기사 셋이 무너진 성 앞에 선 장면 그려줘"))
        self.assertEqual(choose_image_model("고양이 그려줘", picked="qwen")[0], "qwen")
        self.assertEqual(choose_image_model("애니 고양이 그려줘", picked="qwen")[0], "anime")
        self.assertEqual(choose_image_model("고양이 더 밝게", previous_model="zimage", picked="qwen")[0], "zimage")
        self.assertEqual(choose_image_model("고양이 그려줘", picked="flux")[0], "zimage")
        model, note = choose_image_model("교복 입은 학생 그려줘", picked="anime")
        self.assertEqual(model, "zimage")
        self.assertIn("미성년자", note)


class CorePickTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.fake = FakeComfy()
        self.addCleanup(self.fake.close)
        comfy = root / "comfy"
        for files in MODEL_FILES.values():
            for folder, name in files:
                target = comfy / "ComfyUI/models" / folder / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"fixture")
        self.generator = ComfyImageGenerator(root / "images", url=self.fake.url, comfy_dir=comfy, poll_seconds=0.01)
        self.writer = PickingWriter()
        self.core = make_core(root, self.generator, self.writer)
        self.sid = self.core.get_or_create_session(False)

    def unet(self):
        return self.fake.prompts[-1]["1"]["inputs"].get("unet_name")

    def test_chat_model_picks_and_the_answer_says_why(self):
        reply, _ = self.core.handle(self.sid, "왕과 왕비와 기사 셋이 무너진 성 앞에 선 장면 그려줘")
        self.assertEqual(self.writer.picks, 1)
        self.assertEqual(self.unet(), QWEN_UNET)
        self.assertIn("대화 모델이 부탁을 보고 Qwen-Image 2.1 로 골랐습니다 — 여러 인물 배치", reply)
        self.assertIn("왕과 왕비와 기사", self.writer.seen[0])

    def test_explicit_words_and_followups_do_not_ask(self):
        self.core.handle(self.sid, "고양이 빠르게 그려줘")
        self.assertEqual(self.unet(), UNET)
        self.core.handle(self.sid, "더 밝게 다시 그려줘")
        self.assertEqual(self.unet(), UNET)  # 고치는 그림은 앞 모델 그대로
        self.assertEqual(self.writer.picks, 0)

    def test_failed_or_unclear_pick_falls_back_to_the_old_rule(self):
        for pick in (ProviderError("끊김"), "잘 모르겠습니다"):
            with self.subTest(pick=pick):
                self.writer.pick = pick
                reply, _ = self.core.handle(self.sid, "바닷가 노을 그려줘")
                self.assertEqual(self.unet(), UNET)
                self.assertIn("그렸습니다", reply)
                self.assertNotIn("대화 모델이 부탁을 보고", reply)
                self.core._last_image.clear()

    def test_anime_pick_is_refused_for_minors(self):
        self.writer.pick = "anime | 애니 그림체"
        reply, _ = self.core.handle(self.sid, "교복 입은 학생 둘이 벚꽃 아래 걷는 그림 그려줘")
        self.assertEqual(self.unet(), UNET)
        self.assertIn("미성년자", reply)
        self.assertNotIn("대화 모델이 부탁을 보고", reply)


if __name__ == "__main__":
    unittest.main()
