"""흡수 원본·덩치 점검(scripts/check_upstreams.py, 2026-10-27) — 인터넷 없이 이 PC 의 git 저장소를 원본으로 흉내 낸다."""
from datetime import date
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts import check_upstreams as check


def git(folder: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, check=True).stdout


class UpstreamTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.origin = self.root / "origin"
        self.origin.mkdir()
        git(self.origin, "init", "-q", "-b", "dev")
        git(self.origin, "config", "user.email", "t@example.com")
        git(self.origin, "config", "user.name", "t")
        git(self.origin, "config", "uploadpack.allowAnySHA1InWant", "true")
        self.commit({"src/edit.ts": "a\nb\n", "src/read.ts": "r\n", "src/gone.ts": "g\n"}, tag="v1.0.0")
        self.first = git(self.origin, "rev-parse", "HEAD").strip()
        self.commit({"src/edit.ts": "a\nb\nc\n"}, tag="v1.1.0")
        (self.origin / "src" / "gone.ts").unlink()
        self.commit({"src/read.ts": "r\n"}, tag="v1.2.0-beta")       # 정식 판이 아닌 꼬리표는 따라가지 않는다
        self.commit({"src/edit.ts": "다음 판 개발 중\n"})              # 기본 가지는 꼬리표보다 앞서 있다
        self.url = self.origin.as_uri()

    def commit(self, files: dict, tag: str = "") -> None:
        for name, text in files.items():
            (self.origin / name).parent.mkdir(parents=True, exist_ok=True)
            (self.origin / name).write_text(text, encoding="utf-8")
        git(self.origin, "add", "-A")
        git(self.origin, "commit", "-q", "-m", "c")
        if tag:
            git(self.origin, "tag", tag)

    def manifest(self, ref: str, track: str = "") -> dict:
        return {"upstreams": [{"name": "tool", "kind": "git", "repo": self.url, "ref": ref, "track": track, "parts": [
            {"id": "edit", "status": "core", "alex": ["jarvis/harness/edit.py"], "upstream": ["src/edit.ts"]},
            {"id": "read", "status": "core", "alex": ["jarvis/harness/tools.py"], "upstream": ["src/read.ts", "src/gone.ts"]}]},
            {"name": "paper", "kind": "paper", "parts": []}]}

    def test_release_tags_are_followed_and_only_absorbed_files_are_compared(self):
        report = check.upstream_report(self.manifest("v1.0.0", "tag:v1."), self.root / "cache")
        tool = report[0]
        self.assertEqual((tool["latest"], tool["moved"]), ("v1.1.0", True))
        edit, read = tool["parts"]
        self.assertEqual(edit["changes"], [{"file": "src/edit.ts", "state": "changed", "added": 1, "removed": 0}])
        self.assertEqual(read["changes"], [])                      # read.ts 는 그대로, gone.ts 는 v1.1.0 에는 아직 있다
        self.assertIn("자동 비교 없음", report[1]["note"])
        text = check.markdown(report, {"python_lines": 1, "screen_lines": 1, "test_lines": 1, "commands": 1, "settings": 1,
                                       "default_off_flags": 0}, {"experiments": [], "duplicate_paths": []}, date(2026, 10, 27))
        self.assertIn("v1.0.0 → 최신 v1.1.0: 흡수한 부분 1/2곳 바뀜", text)
        self.assertIn("edit.ts +1/−0", text)

    def test_a_commit_ref_follows_the_default_branch_and_a_removed_file_is_flagged(self):
        tool = check.upstream_report(self.manifest(self.first), self.root / "cache")[0]
        self.assertTrue(tool["moved"])
        read = tool["parts"][1]
        self.assertEqual([c["state"] for c in read["changes"]], ["missing"])

    def test_an_unreachable_source_is_reported_and_the_rest_continue(self):
        manifest = self.manifest("v1.0.0", "tag:v1.")
        manifest["upstreams"][0]["repo"] = (self.root / "nowhere").as_uri()
        report = check.upstream_report(manifest, self.root / "cache")
        self.assertIn("error", report[0])
        self.assertEqual(report[1]["kind"], "paper")


class PendingAndSizeTests(unittest.TestCase):
    def test_overdue_experiments_are_marked(self):
        todo = check.pending({"experiments": [{"setting": "a", "adr": "X", "decide_by": "2026-11-30", "measure": "m"},
                                              {"setting": "b", "adr": "Y", "decide_by": "2026-10-01", "measure": "m"}],
                              "duplicate_paths": []}, date(2026, 10, 27))
        self.assertEqual([e["overdue"] for e in todo["experiments"]], [False, True])

    def test_the_real_manifest_names_existing_alex_files_and_every_flag_exists(self):
        manifest = json.loads(check.MANIFEST.read_text(encoding="utf-8"))
        root = check.ROOT
        for source in manifest["upstreams"]:
            for part in source["parts"]:
                for path in part.get("alex", []):
                    self.assertTrue((root / path).is_file(), path)
        from jarvis.config import Settings
        names = {f for f in Settings.__dataclass_fields__}
        for item in manifest["experiments"]:
            self.assertIn(item["setting"], names)
            date.fromisoformat(item["decide_by"])
        size = check.size_report()
        self.assertGreater(size["python_lines"], 10_000)
        self.assertGreater(size["commands"], 50)


if __name__ == "__main__":
    unittest.main()
