"""말로 문서 만들기·내려받기(ADR-104, 2026-10-22 "미뤄둔 것과 보강할 것 지금 진행해").

문서: '워드로 만들어줘'(앞 답) · '회의록 양식을 엑셀로 정리해줘'(새로 써서) → ALEX 데이터 폴더 exports 의 새 파일 + 내려받기 버튼.
내려받기: data/downloads 에만, http·https 만, 내부망 주소는 안 받음, 실행 파일·1GB 넘으면 승인 뒤에만, 받은 파일은 실행하지 않음.
인터넷 대신 가짜 응답을 쓴다."""
import io
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

from jarvis.app import handle_command
from jarvis.documents import document_request
from jarvis.fetcher import (DownloadError, check_url, download_request, downloaded_path, fetch, is_executable, safe_name)
from jarvis.permissions import READ_ONLY_BLOCKED
from jarvis.providers import GenerationCancelled, MockLLMProvider
from jarvis.web import make_server
from tests.test_memory_search import make_core
from tests.test_web import make_state

PUBLIC = lambda _host: ["93.184.216.34"]  # noqa: E731 — 가짜 이름 풀이(공인 주소)


class FakeResponse(io.BytesIO):
    def __init__(self, url: str, data: bytes, headers: dict):
        super().__init__(data)
        self._url, self.headers = url, headers

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def opener_for(data: bytes, headers: dict | None = None, final: str | None = None):
    def opener(request, timeout=0):
        return FakeResponse(final or request.full_url, data, headers or {"Content-Length": str(len(data))})
    return opener


class DetectTests(unittest.TestCase):
    def test_document_requests(self):
        cases = {"워드로 만들어줘": ("docx", "previous"), "이거 PDF로 저장해줘": ("pdf", "previous"),
                 "회의록 양식을 엑셀로 정리해줘": ("xlsx", "new"), "RISC-V 소개 발표 자료 만들어줘": ("pptx", "new"),
                 "보고서를 문서로 만들어줘": ("docx", "new"), "주간 업무 보고서 워드로 작성해 줘": ("docx", "new")}
        for text, expected in cases.items():
            self.assertEqual(document_request(text), expected, text)
        for text in ("이 문서 정리해줘", "엑셀 함수로 어떻게 해?", "파이썬으로 엑셀 파일 만들어 주는 코드 짜줘", "PPT 만드는 법 알려줘"):
            self.assertIsNone(document_request(text), text)

    def test_download_requests(self):
        self.assertEqual(download_request("이 파일 내려받아줘 https://example.com/a.zip."), "https://example.com/a.zip")
        self.assertEqual(download_request("https://x.org/b.pdf 다운로드해 줘"), "https://x.org/b.pdf")
        self.assertIsNone(download_request("이 페이지 요약해줘 https://example.com"))
        self.assertIsNone(download_request("내려받는 법 알려줘"))


class FetcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "downloads"

    def test_internal_and_odd_addresses_are_refused(self):
        for url, ip in (("http://localhost/x", "127.0.0.1"), ("http://nas/x", "192.168.0.10"),
                        ("http://meta/x", "169.254.169.254"), ("http://v6/x", "::1")):
            with self.assertRaisesRegex(DownloadError, "내부망"):
                check_url(url, lambda _h, ip=ip: [ip])
        with self.assertRaisesRegex(DownloadError, "http"):
            check_url("file:///C:/Windows/win.ini", PUBLIC)

    def test_saves_with_a_safe_unique_name_and_hash(self):
        first = fetch("https://example.com/files/%EB%B3%B4%EA%B3%A0%EC%84%9C.pdf", self.folder, resolve=PUBLIC,
                      opener=opener_for(b"%PDF-1.4 hello"))
        second = fetch("https://example.com/files/%EB%B3%B4%EA%B3%A0%EC%84%9C.pdf", self.folder, resolve=PUBLIC,
                       opener=opener_for(b"%PDF-1.4 again"))
        self.assertEqual((first.path.name, second.path.name), ("보고서.pdf", "보고서 (2).pdf"))
        self.assertEqual(first.path.read_bytes(), b"%PDF-1.4 hello")
        self.assertEqual(len(first.sha256), 64)
        self.assertEqual(safe_name("..\\..\\evil:name?.txt"), "evil_name_.txt")
        named = fetch("https://example.com/dl?id=3", self.folder, resolve=PUBLIC, opener=opener_for(
            b"x", {"Content-Length": "1", "Content-Disposition": 'attachment; filename="spec v2.md"'}))
        self.assertEqual(named.path.name, "spec v2.md")

    def test_executables_and_huge_files_need_approval_and_redirects_are_checked(self):
        with self.assertRaisesRegex(DownloadError, "실행 파일"):
            fetch("https://example.com/setup.exe", self.folder, resolve=PUBLIC, opener=opener_for(b"MZ"))
        got = fetch("https://example.com/setup.exe", self.folder, resolve=PUBLIC, opener=opener_for(b"MZ"),
                    allow_executable=True)
        self.assertEqual(got.path.name, "setup.exe")
        self.assertTrue(is_executable("run.ps1") and is_executable("x.bin", "application/x-msdownload"))
        with self.assertRaisesRegex(DownloadError, "큰 파일"):
            fetch("https://example.com/model.gguf", self.folder, resolve=PUBLIC,
                  opener=opener_for(b"x", {"Content-Length": str(5_000_000_000)}))

        def resolve(host):
            return ["10.0.0.5"] if host == "inside" else ["93.184.216.34"]
        with self.assertRaisesRegex(DownloadError, "내부망"):
            fetch("https://example.com/a", self.folder, resolve=resolve, opener=opener_for(b"x", final="http://inside/a"))

    def test_cancel_leaves_no_partial_file(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(GenerationCancelled):
            fetch("https://example.com/big.zip", self.folder, resolve=PUBLIC, opener=opener_for(b"x" * 10), cancel_event=stop)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_served_files_stay_inside_the_folder(self):
        self.folder.mkdir()
        (self.folder / "ok.txt").write_text("hi", encoding="utf-8")
        (self.folder / "half.zip.part").write_text("x", encoding="utf-8")
        self.assertIsNotNone(downloaded_path(self.folder, "ok.txt"))
        for name in ("../m.db", "half.zip.part", "..", "nope.txt"):
            self.assertIsNone(downloaded_path(self.folder, name), name)


class RecordingModel(MockLLMProvider):
    def __init__(self, answer: str):
        super().__init__("general", "GENERAL")
        self.answer, self.seen = answer, []

    def generate(self, messages, **kwargs):
        self.seen.append(messages)
        return self.answer


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.core = make_core(Path(self.temp.name))
        self.sid = self.core.get_or_create_session(False)
        self.exports = Path(self.temp.name) / "exports"

    def test_previous_answer_becomes_a_word_file(self):
        self.core.storage.add_message(self.sid, "user", "회의록 써줘")
        self.core.storage.add_message(self.sid, "assistant", "# 10월 회의록\n\n- 안건 1\n- 안건 2\n![그림](alex-image:x.png)")
        reply, _ = self.core.handle(self.sid, "이거 워드로 저장해줘")
        self.assertIn("앞 답을 워드(DOCX) 문서로 저장했습니다 — 제목 '10월 회의록'", reply)
        name = reply.split("alex-document:", 1)[1].rstrip(")")
        self.assertTrue((self.exports / name).is_file())
        self.assertEqual(self.core.storage.message_page(self.sid)["messages"][-2]["content"], "이거 워드로 저장해줘")

    def test_new_document_is_written_with_a_document_hint(self):
        model = RecordingModel("# 회의록 양식\n\n| 날짜 | 안건 | 담당 |\n|---|---|---|\n| | | |")
        self.core.providers[next(iter(k for k in self.core.providers if k.value == "local_general"))] = model
        reply, decision = self.core.handle(self.sid, "회의록 양식을 엑셀로 정리해줘")
        self.assertEqual(decision.reason, "document request")
        self.assertIn("| 날짜 | 안건 | 담당 |", reply)
        self.assertIn("엑셀(XLSX) 문서로 저장했습니다 — 제목 '회의록 양식'", reply)
        self.assertTrue(any("이번 답은 그대로 엑셀(XLSX) 문서 파일이 된다" in m.content for m in model.seen[0]))
        self.assertEqual(model.seen[0][-1].content, "회의록 양식을 엑셀로 정리해줘")
        self.assertFalse(self.core._turn_hint)
        self.assertEqual(len(list(self.exports.glob("*.xlsx"))), 1)

    def test_nothing_earlier_to_save(self):
        reply, _ = self.core.handle(self.sid, "PDF로 저장해줘")
        self.assertIn("만들 앞 답이 없습니다", reply)

    def test_download_paths(self):
        url = "https://example.com/tool/setup.exe"
        with mock.patch("jarvis.core.inspect_download", return_value=("setup.exe", 1000, "")), \
                mock.patch("jarvis.core.fetch") as fake_fetch:
            reply, _ = self.core.handle(self.sid, f"이거 내려받아줘 {url}")
            self.assertIn(f"- 받기: /download {url} --yes", reply)
            fake_fetch.assert_not_called()
            target = Path(self.temp.name) / "downloads" / "setup.exe"
            fake_fetch.return_value = mock.Mock(path=target, size=1000, sha256="a" * 64, seconds=0.1)
            reply = handle_command(self.core, self.sid, f"/download {url} --yes")[0]
            self.assertEqual(fake_fetch.call_args.kwargs["allow_executable"], True)
            self.assertIn("[받은 파일: setup.exe](alex-download:setup.exe)", reply)
            self.assertIn("실행하지 않았습니다", reply)
        self.core.permission_mode = "read"
        self.assertEqual(self.core.handle(self.sid, f"내려받아줘 {url}")[0], READ_ONLY_BLOCKED)
        self.core.permission_mode = "ask"
        self.assertIn("영상 사이트", self.core.handle(self.sid, "이 영상 내려받아줘 https://youtu.be/dQw4w9WgXcQ")[0])
        self.assertIn("사용법", handle_command(self.core, self.sid, "/download")[0])

    def test_alex_knows_it_can_hand_off_when_asked(self):
        line = self.core._environment_line(next(k for k in self.core.providers if k.value == "local_general"))
        self.assertIn("클라우드로 넘겨줘", line)
        self.assertIn("ALEX 가 스스로 넘기지 않는다", line)


class WebTests(unittest.TestCase):
    def test_only_the_owner_gets_the_downloaded_file(self):
        with tempfile.TemporaryDirectory() as temp:
            state = make_state(Path(temp))
            folder = state.core.storage.db_path.parent / "downloads"
            folder.mkdir()
            (folder / "자료 1.txt").write_text("hello", encoding="utf-8")
            quoted = urllib.parse.quote("자료 1.txt")
            server = make_server(state, 0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{server.server_address[1]}/api/downloaded?name=" + urllib.parse.quote(quoted)
            request = urllib.request.Request(base, headers={"X-Jarvis-Token": "test-token"})
            try:
                with urllib.request.urlopen(request, timeout=5) as response:      # 기록 없는 예전 것은 주인 것(기존 규칙)
                    self.assertEqual(response.read(), "hello".encode("utf-8"))
                    self.assertIn("attachment", response.headers["Content-Disposition"])
                (folder / "남의 것.txt").write_text("secret", encoding="utf-8")
                other = urllib.parse.quote("남의 것.txt")
                state.core.storage.record_artifacts([other], "someone-else")
                with self.assertRaises(urllib.error.HTTPError):                     # 다른 사람이 받은 파일은 못 받는다
                    urllib.request.urlopen(urllib.request.Request(base.replace(urllib.parse.quote(quoted),
                                                                               urllib.parse.quote(other)),
                                                                  headers={"X-Jarvis-Token": "test-token"}), timeout=5)
                bad = base.rsplit("=", 1)[0] + "=" + urllib.parse.quote("..%2Fweb.db")
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(urllib.request.Request(bad, headers={"X-Jarvis-Token": "test-token"}), timeout=5)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
