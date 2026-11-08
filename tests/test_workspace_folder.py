"""프로젝트 폴더 창의 '새 폴더 만들기'(2026-11-08 사용자: "새폴더를 만들거나 그런건되는지를 모르겠네, 확인해서 없으면 구현해줘")."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from jarvis.permissions import READ_ONLY_BLOCKED
from jarvis.web import ADMIN_PATHS, folder_name_problem, make_server
from test_web import make_state


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = make_state(self.root)

    def make(self, path, **kwargs):
        return self.state.create_workspace_folder(str(path), kwargs.pop("confirmed", True), **kwargs)

    def test_a_new_folder_is_made_once_and_not_opened(self):
        before = self.state.core.current_project_root()
        made = self.make(self.root / "새 프로젝트")
        self.assertTrue((self.root / "새 프로젝트").is_dir())
        self.assertEqual(Path(made["path"]), (self.root / "새 프로젝트").resolve())
        self.assertEqual(self.state.core.current_project_root(), before)   # opening stays a separate, confirmed step
        (self.root / "새 프로젝트" / "keep.txt").write_text("kept", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "이미 있습니다"):
            self.make(self.root / "새 프로젝트")
        self.assertEqual((self.root / "새 프로젝트" / "keep.txt").read_text(encoding="utf-8"), "kept")
        (self.root / "file.txt").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "이미 있습니다"):
            self.make(self.root / "file.txt")

    def test_unclear_requests_are_refused_without_touching_the_disk(self):
        for path, reason in ((self.root / "a" / "b", "위 폴더"), ("relative-folder", "전체 경로"),
                             (self.root / "con", "장치 이름"), (self.root / "bad?name", "쓸 수 없습니다"),
                             (self.root / "ends-with-dot.", "점으로 끝날"), (self.root / "x" / "..", "이름을 확인")):
            with self.subTest(path=str(path)), self.assertRaisesRegex(ValueError, reason):
                self.make(path)
        with self.assertRaisesRegex(ValueError, "명시적 확인"):
            self.make(self.root / "unconfirmed", confirmed=False)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["web.db"])

    def test_a_read_only_conversation_does_not_make_folders(self):
        viewer = self.state.owner_viewer()
        self.state.set_permission(viewer, self.state.session_id, "read")
        with self.assertRaisesRegex(ValueError, READ_ONLY_BLOCKED[:10]):
            self.make(self.root / "blocked", viewer=viewer, session_id=self.state.session_id)
        self.assertFalse((self.root / "blocked").exists())

    def test_names_that_work_on_every_os(self):
        for name in ("my-project", "새 프로젝트", "v1.2", "COM0"):
            self.assertEqual(folder_name_problem(name), "", name)
        for name in ("", ".", "..", "NUL", "lpt1.txt", "a:b", "tail ", "x" * 256):
            self.assertNotEqual(folder_name_problem(name), "", name)


class FolderHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = make_state(self.root)
        self.server = make_server(self.state, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def post(self, body):
        request = urllib.request.Request(self.url + "/api/workspace/mkdir", data=json.dumps(body).encode(),
                                         headers={"X-Jarvis-Token": "test-token", "Content-Type": "application/json"})
        return urllib.request.urlopen(request, timeout=30)

    def test_the_dialog_endpoint_is_admin_only_and_makes_the_folder(self):
        self.assertIn("/api/workspace/mkdir", ADMIN_PATHS)
        with self.post({"path": str(self.root / "from-dialog"), "confirmed": True}) as response:
            self.assertEqual(Path(json.load(response)["path"]), (self.root / "from-dialog").resolve())
        self.assertTrue((self.root / "from-dialog").is_dir())
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post({"path": str(self.root / "from-dialog"), "confirmed": True})
        self.assertEqual(caught.exception.code, 400)
        self.assertIn("이미 있습니다", json.load(caught.exception)["error"])
        caught.exception.close()


if __name__ == "__main__":
    unittest.main()
