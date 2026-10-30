"""Ollama 휴대용 설치(jarvis/runtime.py)의 안전장치 — SHA-256 대조와 압축 밖 경로 거절(2026-10-26 보강).

인터넷·Windows 없이: 플랫폼과 다운로드를 가짜로 바꾸고 임시 폴더에 설치한다."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from jarvis import runtime

URL = "https://example.invalid/ollama-windows-amd64.zip"


def archive(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, data in entries.items():
            bundle.writestr(name, data)
    return buffer.getvalue()


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for target, value in ((runtime, "ROOT"), (runtime.platform, "system"), (runtime.platform, "machine")):
            patcher = mock.patch.object(target, value, {"ROOT": self.root, "system": lambda: "Windows",
                                                        "machine": lambda: "AMD64"}[value])
            patcher.start()
            self.addCleanup(patcher.stop)
        self.target = self.root / "runtime" / "ollama"

    def serve(self, payload: bytes, digest: str | None = None):
        release = {"tag_name": "v9.9.9", "assets": [{"name": "ollama-windows-amd64.zip", "size": len(payload),
                                                     "browser_download_url": URL,
                                                     "digest": digest if digest is not None
                                                     else "sha256:" + hashlib.sha256(payload).hexdigest()}]}

        def urlopen(request, timeout=None):
            url = request if isinstance(request, str) else request.full_url
            return Response(payload if url == URL else json.dumps(release).encode())
        patcher = mock.patch.object(runtime.urllib.request, "urlopen", urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)

    def install(self):
        return runtime.install(report=lambda *_args, **_kwargs: None)

    def test_a_verified_archive_is_extracted_and_recorded(self):
        payload = archive({"ollama.exe": b"MZ", "lib/a.dll": b"x"})
        self.serve(payload)
        self.assertEqual(self.install(), self.target / "ollama.exe")
        self.assertEqual((self.target / "lib" / "a.dll").read_bytes(), b"x")
        record = json.loads((self.root / "runtime" / "ollama-install.json").read_text(encoding="utf-8"))
        self.assertEqual((record["version"], record["sha256"]), ("v9.9.9", hashlib.sha256(payload).hexdigest()))
        self.assertEqual(self.install(), self.target / "ollama.exe")   # 이미 있으면 다시 받지 않는다

    def test_a_different_hash_is_not_extracted(self):
        self.serve(archive({"ollama.exe": b"MZ"}), digest="sha256:" + "0" * 64)
        with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
            self.install()
        self.assertFalse((self.target / "ollama.exe").exists())

    def test_a_release_without_a_digest_is_refused_before_download(self):
        self.serve(archive({"ollama.exe": b"MZ"}), digest="")
        with self.assertRaisesRegex(RuntimeError, "no SHA-256 digest"):
            self.install()
        self.assertFalse((self.root / "runtime" / "ollama-windows-amd64.zip").exists())

    def test_an_entry_that_leaves_the_folder_is_refused_and_nothing_is_written(self):
        self.serve(archive({"ollama.exe": b"MZ", "../../escaped.txt": b"x"}))
        with self.assertRaisesRegex(RuntimeError, "Unsafe archive entry"):
            self.install()
        self.assertFalse((self.root / "escaped.txt").exists())
        self.assertFalse((self.target / "ollama.exe").exists())

    def test_an_archive_without_ollama_is_an_error(self):
        self.serve(archive({"readme.txt": b"x"}))
        with self.assertRaisesRegex(RuntimeError, "did not contain ollama.exe"):
            self.install()

    def test_other_platforms_are_pointed_to_the_download_page(self):
        with mock.patch.object(runtime.platform, "system", lambda: "Linux"):
            with self.assertRaisesRegex(RuntimeError, "Windows x64"):
                self.install()


if __name__ == "__main__":
    unittest.main()
