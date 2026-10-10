"""scripts/replay_session.py — 모델만 바꿔 같은 조건으로 재는 도구. 모델 없이 도는 부분만 확인한다."""
from contextlib import closing
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import replay_session  # noqa: E402
from jarvis.config import Settings  # noqa: E402


class ReplayToolTests(unittest.TestCase):
    def test_the_model_override_reaches_every_profile_slot(self):
        """노트북 프로필이든 데스크톱 프로필이든 같은 모델이 대답해야 비교가 된다."""
        s = replay_session.with_model(Settings(), "gpt-oss:120b", Path("x"))
        self.assertEqual({s.general_model, s.coder_model, s.desktop_general_model, s.desktop_coder_model},
                         {"gpt-oss:120b"})
        self.assertEqual(s.data_dir, Path("x"))

    def test_no_model_keeps_the_configured_one(self):
        base = Settings()
        s = replay_session.with_model(base, None, Path("x"))
        self.assertEqual(s.general_model, base.general_model)

    def test_the_copy_keeps_rowids_and_leaves_the_original_alone(self):
        """--cut-at 은 rowid 로 자른다. 복사에서 rowid 가 바뀌면 엉뚱한 곳을 자른다."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a" / "jarvis.db"
            src.parent.mkdir()
            with closing(sqlite3.connect(src)) as conn, conn:
                conn.execute("CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT)")
                for i in range(5):
                    conn.execute("INSERT INTO messages VALUES ('s', 'user', ?)", (f"m{i}",))
                conn.execute("DELETE FROM messages WHERE content = 'm1'")  # rowid 에 구멍을 낸다
            target_dir = Path(tmp) / "b"
            target_dir.mkdir()
            copy = replay_session.copy_database(src, target_dir)
            with closing(sqlite3.connect(copy)) as conn:
                rows = conn.execute("SELECT rowid, content FROM messages ORDER BY rowid").fetchall()
            self.assertEqual(rows, [(1, "m0"), (3, "m2"), (4, "m3"), (5, "m4")])
            with closing(sqlite3.connect(src)) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 4)


if __name__ == "__main__":
    unittest.main()
