"""Model Catalog contract tests. Ollama pulls use a local fake HTTP server."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from jarvis.hardware import HardwareInfo, PerformanceProfile
from jarvis.model_registry import (
    ModelCandidate, estimate_memory_fit, get_candidates, get_ollama_storage_info,
)
from jarvis.providers import OllamaProvider
from jarvis.router import Destination
from jarvis.web import WebAppState, make_server


QWEN = "qwen3.8-flash-next:125b-a6b-q4_K_M"
DESKTOP = HardwareInfo("Windows-11", "CPU", 96.0, "NVIDIA GeForce RTX 5080", 15.9,
                       PerformanceProfile.DESKTOP)


class CatalogTests(unittest.TestCase):
    def test_verified_qwen_metadata_and_memory_warning(self):
        candidate = next(c for c in get_candidates(DESKTOP) if c.tag == QWEN)
        self.assertEqual((candidate.params_b, candidate.active_params_b,
                          candidate.ngram_embedding_params_b), (125.0, 6.0, 51.0))
        self.assertEqual((candidate.architecture, candidate.quantization,
                          candidate.download_size_gb), ("MoE", "Q4_K_M", 120.0))
        self.assertEqual(estimate_memory_fit(candidate, DESKTOP)["status"], "insufficient_estimated")
        self.assertIn(candidate, get_candidates(DESKTOP))  # Warning does not block the catalog.

    def test_range_deepseek_and_mlx_are_filtered_without_hiding_local_models(self):
        base = get_candidates(DESKTOP)
        synthetic = [
            ModelCandidate("edge:122b", 122, None, None, None, "test", "https://example.com", "2026-09-25"),
            ModelCandidate("edge:397b", 397, None, None, None, "test", "https://example.com", "2026-09-25"),
            ModelCandidate("deepseek:200b", 200, None, None, None, "deepseek", "https://example.com", "2026-09-25"),
            ModelCandidate("example:200b-mlx", 200, None, None, None, "test", "https://example.com", "2026-09-25", runtime="mlx"),
        ]
        with patch("jarvis.model_registry._CATALOG", base + synthetic):
            tags = {c.tag for c in get_candidates(DESKTOP)}
            self.assertNotIn("edge:122b", tags)
            self.assertNotIn("edge:397b", tags)
            self.assertNotIn("deepseek:200b", tags)
            self.assertNotIn("example:200b-mlx", tags)
            mac = HardwareInfo("Darwin", "CPU", 192, "Apple GPU", 96, PerformanceProfile.UNKNOWN)
            self.assertIn("example:200b-mlx", {c.tag for c in get_candidates(mac)})

    def test_unknown_storage_path_does_not_create_a_folder(self):
        self.assertIsNone(get_ollama_storage_info(None)["free_gb"])
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing"
            self.assertIsNone(get_ollama_storage_info(missing)["free_gb"])
            self.assertFalse(missing.exists())
            self.assertIsNotNone(get_ollama_storage_info(Path(temp))["free_gb"])


class PullHTTPTests(unittest.TestCase):
    def setUp(self):
        class FakeOllama(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.server.requests.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                progress = b'{"status":"downloading","completed":1,"total":2}\n'
                success = b'{"status":"success"}\n'
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Content-Length", str(len(progress) + len(success)))
                self.end_headers()
                self.wfile.write(progress)
                self.wfile.flush()
                if self.server.hold_success:
                    self.server.release_success.wait(timeout=3)
                self.wfile.write(success)

            def log_message(self, *_args):
                pass

        self.ollama = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
        self.ollama.requests = []
        self.ollama.hold_success = False
        self.ollama.release_success = threading.Event()
        self.ollama_thread = threading.Thread(target=self.ollama.serve_forever, daemon=True)
        self.ollama_thread.start()
        host = f"http://127.0.0.1:{self.ollama.server_address[1]}"
        provider = OllamaProvider("mistral-large:123b", host=host)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        core = SimpleNamespace(
            providers={Destination.LOCAL_GENERAL: provider, Destination.LOCAL_CODER: provider},
            hardware=DESKTOP, require_cloud_approval=True, approval_callback=None,
            storage=SimpleNamespace(db_path=Path(self.temp.name) / "jarvis.db"),
        )
        self.state = WebAppState(core=core, session_id="test-session", token="test-token")
        self.server = make_server(self.state, 0)
        self.web_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.web_thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.web_thread.join(timeout=2)
        self.ollama.shutdown()
        self.ollama.server_close()
        self.ollama_thread.join(timeout=2)

    def post(self, path: str, payload: dict):
        return urllib.request.urlopen(urllib.request.Request(
            self.url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Jarvis-Token": "test-token"},
            method="POST",
        ), timeout=5)

    def test_model_status_keeps_installed_selected_loaded_separate(self):
        provider = self.state.core.providers[Destination.LOCAL_GENERAL]
        with patch.object(provider, "_fetch_installed_models", return_value={QWEN, "deepseek-v2:236b"}), \
             patch.object(provider, "_fetch_loaded_models", return_value={QWEN}):
            result = self.state.model_status()
        self.assertIn(QWEN, result["installed"])
        self.assertIn(QWEN, result["loaded"])
        self.assertEqual(result["selected_general"], "mistral-large:123b")
        self.assertIn("deepseek-v2:236b", result["installed"])
        self.assertNotIn("deepseek-v2:236b", {c["tag"] for c in result["catalog"]})
        qwen = next(c for c in result["catalog"] if c["tag"] == QWEN)
        self.assertEqual(qwen["memory_estimate"]["status"], "insufficient_estimated")
        self.assertIsNone(result["storage"]["free_gb"])

    def test_pull_requires_confirmation_and_exact_catalog_tag(self):
        for body in ({"model": QWEN}, {"model": "unknown:200b", "confirmed": True}):
            with self.subTest(body=body), self.assertRaises(urllib.error.HTTPError) as error:
                self.post("/api/pull", body)
            self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.ollama.requests, [])

    def test_confirmed_pull_streams_start_and_success_without_real_download(self):
        with self.post("/api/pull", {"model": QWEN, "confirmed": True}) as response:
            events = [json.loads(line) for line in response]
        self.assertEqual(events[0]["type"], "start")
        self.assertEqual(events[-1]["status"], "success")
        self.assertEqual(self.ollama.requests, [("/api/pull", {"model": QWEN, "stream": True})])
        for _ in range(20):
            if self.state.active_id is None:
                break
            time.sleep(.01)
        self.assertIsNone(self.state.active_id)

    def test_cancelled_pull_does_not_report_success(self):
        self.ollama.hold_success = True
        with self.post("/api/pull", {"model": QWEN, "confirmed": True}) as response:
            start = json.loads(response.readline())
            self.assertEqual(start["type"], "start")
            with self.post("/api/cancel", {"request_id": start["request_id"]}) as cancelled:
                self.assertTrue(json.load(cancelled)["cancelled"])
            self.ollama.release_success.set()
            events = [json.loads(line) for line in response]
        self.assertIn("cancelled", [event.get("status") for event in events])
        self.assertNotIn("success", [event.get("status") for event in events])
        for _ in range(20):
            if self.state.active_id is None:
                break
            time.sleep(.01)
        self.assertIsNone(self.state.active_id)


if __name__ == "__main__":
    unittest.main()
