"""Lifecycle controls only the matching local ALEX server."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock

from jarvis.imagegen import ComfyImageGenerator, ImageGenError
from jarvis.launcher import instance_id
from jarvis.lifecycle import LifecycleError, health, stop_server
from jarvis.web import make_server
from tests.test_web import make_state


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = make_state(Path(self.temp.name))
        self.server = make_server(self.state, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_refuses_unrelated_instance(self):
        with self.assertRaises(LifecycleError):
            stop_server(self.base, expected_instance="different")
        self.assertIsNotNone(health(self.base))

    def test_busy_server_refuses_shutdown(self):
        expected = instance_id(self.state.core.storage.db_path.parent)
        with self.state.lock, self.assertRaises(LifecycleError):
            stop_server(self.base, expected_instance=expected)
        self.assertFalse(self.state.shutting_down)

    def test_stop_waits_for_web_exit_and_is_idempotent(self):
        expected = instance_id(self.state.core.storage.db_path.parent)
        self.assertTrue(stop_server(self.base, expected_instance=expected))
        self.assertIsNone(health(self.base))
        self.assertFalse(stop_server(self.base, expected_instance=expected))

    @unittest.skip("공개판에는 이미지 엔진이 없습니다")
    def test_shutdown_reaps_only_the_image_engine_it_started(self):
        generator = ComfyImageGenerator(Path(self.temp.name) / "images")
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def reap():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        self.addCleanup(reap)
        generator._process = process
        self.state.core.image_generator = generator
        expected = instance_id(self.state.core.storage.db_path.parent)
        self.assertTrue(stop_server(self.base, expected_instance=expected))
        self.assertIsNotNone(process.poll())
        self.assertIsNone(generator._process)

    @unittest.skip("공개판에는 이미지 엔진이 없습니다")
    def test_shutdown_leaves_preexisting_image_service_untouched(self):
        generator = ComfyImageGenerator(Path(self.temp.name) / "images")
        generator._request = Mock(side_effect=AssertionError("external service must not receive stop/free"))
        self.state.core.image_generator = generator
        result = self.state.close_owned_services()
        self.assertEqual(result["services"], {"ollama": "preserved", "comfyui": "not_owned"})
        generator._request.assert_not_called()

    def test_cleanup_failure_is_reported_and_web_shutdown_still_completes(self):
        generator = Mock()
        generator.close.side_effect = ImageGenError("engine could not stop")
        self.state.core.image_generator = generator
        result = self.state.close_owned_services()
        self.assertEqual(result["services"]["comfyui"], "failed")
        self.assertEqual(result["warnings"], ["engine could not stop"])
        expected = instance_id(self.state.core.storage.db_path.parent)
        self.assertTrue(stop_server(self.base, expected_instance=expected))

    def test_busy_shutdown_does_not_start_engine_cleanup(self):
        generator = Mock()
        self.state.core.image_generator = generator
        expected = instance_id(self.state.core.storage.db_path.parent)
        with self.state.lock, self.assertRaises(LifecycleError):
            stop_server(self.base, expected_instance=expected)
        generator.close.assert_not_called()

    def test_browser_disconnect_during_cleanup_does_not_cancel_shutdown(self):
        entered, proceed = threading.Event(), threading.Event()
        generator = Mock()
        def close():
            entered.set()
            proceed.wait(timeout=2)
            return True
        generator.close.side_effect = close
        self.state.core.image_generator = generator
        body = json.dumps({}).encode()
        with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2) as connection:
            request = (f"POST /api/shutdown HTTP/1.1\r\nHost: 127.0.0.1:{self.server.server_port}\r\n"
                       f"X-Jarvis-Token: {self.state.token}\r\nContent-Type: application/json\r\n"
                       f"Content-Length: {len(body)}\r\n\r\n").encode() + body
            connection.sendall(request)
            self.assertTrue(entered.wait(timeout=2))
            connection.shutdown(socket.SHUT_RDWR)
        proceed.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and health(self.base) is not None:
            time.sleep(.05)
        self.assertIsNone(health(self.base))


if __name__ == "__main__":
    unittest.main()
