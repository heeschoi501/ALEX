"""Existing picture-to-video and video generation requests must keep their route."""
import unittest

from jarvis.router import Destination, route_request


class MediaRoutingContracts(unittest.TestCase):
    def test_a_picture_used_for_a_video_does_not_trigger_still_image_generation(self):
        for request in ("이 그림으로 영상 만들어줘", "이 이미지로 동영상 만들어줘", "이 사진을 짧은 영상으로 만들어줘"):
            with self.subTest(request=request):
                self.assertEqual(route_request(request).destination, Destination.TOOL_VIDEO_GEN)

    def test_existing_video_generation_phrasings_stay_supported(self):
        for request in ("render a short video of a dragon", "고양이가 뛰는 영상 뽑아줘"):
            with self.subTest(request=request):
                self.assertEqual(route_request(request).destination, Destination.TOOL_VIDEO_GEN)


if __name__ == "__main__":
    unittest.main()
