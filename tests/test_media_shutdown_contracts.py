"""Engine shutdown must report busy work and preserve retryable failures."""
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock

from jarvis.imagegen import ComfyImageGenerator, ImageGenError


class MediaShutdownContracts(unittest.TestCase):
    def test_busy_start_refuses_shutdown_before_a_child_handle_exists(self):
        generator = ComfyImageGenerator(Path("unused"))
        with generator._lock:
            with self.assertRaises(ImageGenError):
                generator.close()
        self.assertFalse(generator.close())

    def test_cleanup_failures_are_reported_as_engine_errors_and_keep_retry_handle(self):
        for operation in ("poll", "wait", "kill", "final_wait"):
            with self.subTest(operation=operation):
                generator = ComfyImageGenerator(Path("unused"))
                process = Mock()
                process.poll.return_value = None
                if operation == "poll":
                    process.poll.side_effect = OSError("cannot inspect owned child")
                elif operation == "wait":
                    process.wait.side_effect = OSError("cannot wait for owned child")
                elif operation == "kill":
                    process.wait.side_effect = subprocess.TimeoutExpired("owned child", 5)
                    process.kill.side_effect = OSError("cannot kill owned child")
                else:
                    process.wait.side_effect = subprocess.TimeoutExpired("owned child", 5)
                generator._process = process
                with self.assertRaises(ImageGenError):
                    generator.close()
                self.assertIs(generator._process, process)
                self.assertTrue(generator._lock.acquire(blocking=False))
                generator._lock.release()


if __name__ == "__main__":
    unittest.main()
