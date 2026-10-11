"""Storage persistence, memory and failure-mode tests."""
import tempfile
import unittest
from pathlib import Path

from jarvis.storage import Storage, StorageError


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name)
        self.storage = Storage(self.path / "test.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_session_and_messages_persist(self):
        session_id = self.storage.create_session()
        self.storage.add_message(session_id, "user", "hello")
        self.storage.add_message(session_id, "assistant", "hi")
        messages = self.storage.get_messages(session_id)
        self.assertEqual([m.content for m in messages], ["hello", "hi"])
        self.assertEqual(self.storage.latest_session_id(), session_id)

    def test_memory_roundtrip(self):
        self.storage.remember("project", "jarvis")
        self.assertEqual(self.storage.recall("project"), "jarvis")

    def test_memory_overwrite(self):
        self.storage.remember("mode", "study")
        self.storage.remember("mode", "work")
        self.assertEqual(self.storage.recall("mode"), "work")

    def test_forget(self):
        self.storage.remember("temp", "x")
        self.assertTrue(self.storage.forget("temp"))
        self.assertFalse(self.storage.forget("temp"))
        self.assertIsNone(self.storage.recall("temp"))

    def test_empty_memory_key_rejected(self):
        with self.assertRaises(StorageError):
            self.storage.remember("   ", "value")

    def test_list_memories_limit(self):
        for i in range(5):
            self.storage.remember(f"k{i}", str(i))
        self.assertEqual(len(self.storage.list_memories(limit=2)), 2)

    def test_get_messages_limit_keeps_recent(self):
        session_id = self.storage.create_session()
        for i in range(10):
            self.storage.add_message(session_id, "user", f"m{i}")
        messages = self.storage.get_messages(session_id, limit=3)
        self.assertEqual([m.content for m in messages], ["m7", "m8", "m9"])

    def test_list_sessions(self):
        first = self.storage.create_session()
        self.storage.add_message(first, "user", "a")
        second = self.storage.create_session()
        self.storage.add_message(second, "user", "b")
        self.storage.add_message(second, "user", "c")
        sessions = self.storage.list_sessions()
        self.assertEqual(sessions[0].session_id, second)
        self.assertEqual(sessions[0].message_count, 2)

    def test_no_session_returns_none(self):
        empty = Storage(self.path / "empty.db")
        self.assertIsNone(empty.latest_session_id())

    def test_corrupted_database_message(self):
        """손상된 파일을 열면 무엇을 해야 하는지 알려줘야 한다."""
        broken = self.path / "broken.db"
        broken.write_bytes(b"this is definitely not sqlite" * 20)
        with self.assertRaises(StorageError) as ctx:
            Storage(broken)
        self.assertIn("broken.db", str(ctx.exception))
        self.assertIn("이름을 바꾸거나", str(ctx.exception))

    def test_creates_parent_directory(self):
        nested = self.path / "a" / "b" / "c.db"
        Storage(nested)
        self.assertTrue(nested.exists())


if __name__ == "__main__":
    unittest.main()


class StorageV012Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_summary_roundtrip_and_candidates(self):
        storage = Storage(self.path / "s.db")
        sid = storage.create_session()
        for i in range(8):
            storage.add_message(sid, "user", f"m{i}")
        candidates = storage.get_messages_for_summary(sid, keep_recent=3)
        self.assertEqual([m.content for m in candidates], ["m0", "m1", "m2", "m3", "m4"])
        storage.update_session_summary(sid, "old summary", candidates[-1].message_id)
        self.assertEqual(storage.get_session_summary(sid)[0], "old summary")
        self.assertEqual(storage.get_messages_for_summary(sid, keep_recent=3), [])

    def test_route_event_and_correction(self):
        storage = Storage(self.path / "r.db")
        sid = storage.create_session()
        event_id = storage.record_route_event(sid, "hello", "local_general", "default", "")
        event = storage.get_route_event(event_id)
        self.assertEqual(event.text, "hello")
        self.assertIsNone(event.correction)
        self.assertTrue(storage.correct_route_event(event_id, "local_coder"))
        self.assertEqual(storage.get_route_event(event_id).correction, "local_coder")
        self.assertEqual(storage.route_feedback_counts()["local_general"], 1)

    def test_integrity_check(self):
        storage = Storage(self.path / "i.db")
        ok, detail = storage.integrity_check()
        self.assertTrue(ok)
        self.assertEqual(detail.lower(), "ok")

    def test_existing_v011_database_is_migrated(self):
        import sqlite3

        db = self.path / "old.db"
        conn = sqlite3.connect(db)
        try:
            conn.executescript(
                """
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE memories (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO sessions(id, created_at, updated_at) VALUES ('legacy', 'a', 'b');
                """
            )
            conn.commit()
        finally:
            conn.close()
        storage = Storage(db)
        self.assertEqual(storage.latest_session_id(), "legacy")
        self.assertEqual(storage.get_session_summary("legacy"), ("", 0))
        migration_backups = list((self.path / "backups").glob("pre-migration-v*-old.db"))
        self.assertEqual(len(migration_backups), 1)
        # Backup is the pre-migration shape and still contains the original session.
        backup_conn = sqlite3.connect(migration_backups[0])
        try:
            columns = {row[1] for row in backup_conn.execute("PRAGMA table_info(sessions)")}
            self.assertNotIn("summary", columns)
            self.assertEqual(backup_conn.execute("SELECT id FROM sessions").fetchone()[0], "legacy")
        finally:
            backup_conn.close()


class StorageBackupTests(unittest.TestCase):
    def test_backup_contains_persisted_data(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root / "main.db")
            sid = storage.create_session()
            storage.add_message(sid, "user", "backup-me")
            storage.remember("project", "jarvis")
            backup = storage.backup_database(root / "copy.db")
            copied = Storage(backup)
            self.assertEqual(copied.recall("project"), "jarvis")
            self.assertEqual(copied.get_messages(sid)[0].content, "backup-me")


class SessionNavigationExportTests(unittest.TestCase):
    def test_session_exists_and_export(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root / "jarvis.db")
            sid = storage.create_session()
            storage.add_message(sid, "user", "hello")
            storage.add_message(sid, "assistant", "world")
            self.assertTrue(storage.session_exists(sid))
            self.assertFalse(storage.session_exists("missing"))
            path = storage.export_session_markdown(sid)
            text = path.read_text(encoding="utf-8")
            self.assertIn("hello", text)
            self.assertIn("world", text)
            self.assertIn(sid, text)


class BackupSafetyTests(unittest.TestCase):
    def test_backup_cannot_overwrite_source(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "main.db"
            storage = Storage(path)
            with self.assertRaises(StorageError):
                storage.backup_database(path)


class StorageV013Tests(unittest.TestCase):
    def test_v5_migration_backs_up_once_and_preserves_active_work(self):
        import sqlite3
        from contextlib import closing
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'v5.db'
            store = Storage(path)
            store.set_state('developer.active', '{"goal":"legacy"}')
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute('DROP TABLE developer_runs')
                conn.execute('DROP TABLE edit_proposals')
                conn.execute("UPDATE schema_meta SET value='5' WHERE key='schema_version'")
            upgraded = Storage(path)
            self.assertEqual(upgraded.get_state('developer.active'), '{"goal":"legacy"}')
            backups = list((Path(td) / 'backups').glob('pre-migration-v*'))
            self.assertEqual(len(backups), 1)
            with closing(sqlite3.connect(backups[0])) as conn:
                self.assertEqual(conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0], '5')
            Storage(path)
            self.assertEqual(len(list((Path(td) / 'backups').glob('pre-migration-v*'))), 1)

    def test_v6_migration_gives_existing_data_to_the_owner(self):
        """여러 사람이 쓰기(2026-10-11): 있던 대화·기억·할 일은 지우지 않고 모두 이 PC 주인 것이 된다. 바꾸기 전에 한 번 백업한다."""
        import sqlite3
        from contextlib import closing
        from jarvis.storage import OWNER
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'v6.db'
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.executescript("""
                    CREATE TABLE sessions (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                        summary TEXT NOT NULL DEFAULT '', summary_through_id INTEGER NOT NULL DEFAULT 0, title TEXT NOT NULL DEFAULT '');
                    CREATE TABLE memories (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
                    CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
                        priority INTEGER NOT NULL DEFAULT 2, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                    CREATE TABLE developer_runs (id TEXT PRIMARY KEY, workspace TEXT NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL);
                    CREATE TABLE edit_proposals (workspace TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                        updated_at TEXT NOT NULL, PRIMARY KEY(workspace, id));
                    INSERT INTO sessions(id, created_at, updated_at, title) VALUES ('s1', '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00+00:00', '옛 대화');
                    INSERT INTO memories VALUES ('선호언어', '한국어', '2026-10-01T00:00:00+00:00');
                    INSERT INTO tasks(text, created_at, updated_at) VALUES ('옛 할 일', '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00+00:00');
                """)
            store = Storage(path)
            self.assertEqual(store.session_owner('s1'), OWNER)
            self.assertEqual(store.list_memories(), {'선호언어': '한국어'})
            self.assertEqual([t.text for t in store.list_tasks()], ['옛 할 일'])
            self.assertEqual(store.list_memories(owner='someone'), {})
            self.assertEqual(store.list_tasks(owner='someone'), [])
            self.assertEqual(store.get_user(OWNER).role, 'admin')
            store.remember('선호언어', 'English', owner='someone')  # 같은 이름의 기억도 사람마다 따로
            self.assertEqual(store.recall('선호언어'), '한국어')
            self.assertEqual(len(list((Path(td) / 'backups').glob('pre-migration-v*'))), 1)
            Storage(path)
            self.assertEqual(len(list((Path(td) / 'backups').glob('pre-migration-v*'))), 1)

    def test_schema_meta_wal_and_stats(self):
        with tempfile.TemporaryDirectory() as td:
            storage = Storage(Path(td) / "jarvis.db")
            sid = storage.create_session()
            storage.add_message(sid, "user", "hello")
            storage.remember("mode", "study")
            stats = storage.database_stats()
            self.assertEqual(stats["schema_version"], "7")  # 7: 대화·기억·할 일에 주인(여러 사람이 쓰기)
            self.assertEqual(str(stats["journal_mode"]).lower(), "wal")
            self.assertEqual(stats["sessions"], 1)
            self.assertEqual(stats["messages"], 1)
            self.assertEqual(stats["memories"], 1)

    def test_session_rename_roundtrip_and_listing(self):
        with tempfile.TemporaryDirectory() as td:
            storage = Storage(Path(td) / "jarvis.db")
            sid = storage.create_session()
            storage.rename_session(sid, "  JARVIS   개발  ")
            self.assertEqual(storage.session_title(sid), "JARVIS 개발")
            listed = storage.list_sessions()
            self.assertEqual(listed[0].title, "JARVIS 개발")

    def test_session_title_migrates_from_v012_shape(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "old.db"
            import sqlite3
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', "
                "summary_through_id INTEGER NOT NULL DEFAULT 0)"
            )
            conn.execute("INSERT INTO sessions VALUES ('abc','x','x','',0)")
            conn.commit()
            conn.close()
            storage = Storage(db)
            self.assertEqual(storage.session_title("abc"), "")
            storage.rename_session("abc", "Migrated")
            self.assertEqual(storage.session_title("abc"), "Migrated")

class SessionDeletionTests(unittest.TestCase):
    def test_delete_session_cascades_messages_and_routes(self):
        with tempfile.TemporaryDirectory() as td:
            storage = Storage(Path(td) / "x.db")
            sid = storage.create_session()
            storage.add_message(sid, "user", "hello")
            storage.record_route_event(sid, "hello", "local_general", "test")
            self.assertTrue(storage.delete_session(sid))
            self.assertFalse(storage.session_exists(sid))
            stats = storage.database_stats()
            self.assertEqual(stats["messages"], 0)
            self.assertEqual(stats["route_events"], 0)
            self.assertFalse(storage.delete_session(sid))

class MemoryBoundsTests(unittest.TestCase):
    def test_memory_key_and_value_are_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            storage = Storage(Path(td) / "x.db")
            with self.assertRaises(StorageError):
                storage.remember("", "value")
            with self.assertRaises(StorageError):
                storage.remember("k" * 101, "value")
            with self.assertRaises(StorageError):
                storage.remember("key", "")
            with self.assertRaises(StorageError):
                storage.remember("key", "x" * 10001)
            storage.remember("key", "value")
            self.assertEqual(storage.recall("key"), "value")


class MemoryRecencyOrderingTests(unittest.TestCase):
    """연속 저장에서도 '가장 최근 메모' 순서가 뒤집히지 않아야 한다.

    Windows 시계 해상도(~15ms) 안에서 여러 번 저장하면 updated_at 이 같아져
    ORDER BY 가 동률이 되고, 정렬이 삽입 순서로 조용히 뒤집혔다.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp_dir.name) / "test.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_rapid_writes_keep_newest_first(self):
        keys = [f"k{i}" for i in range(10)]
        for key in keys:
            self.storage.remember(key, "v")
        self.assertEqual(list(self.storage.list_memories()), list(reversed(keys)))

    def test_rapid_writes_get_distinct_timestamps(self):
        for key in ("a", "b", "c"):
            self.storage.remember(key, "v")
        with self.storage._connection() as conn:
            stamps = [r["updated_at"] for r in conn.execute("SELECT updated_at FROM memories")]
        self.assertEqual(len(set(stamps)), len(stamps))

    def test_update_moves_existing_memory_to_front(self):
        self.storage.remember("first", "v")
        self.storage.remember("second", "v")
        self.storage.remember("first", "updated")
        self.assertEqual(list(self.storage.list_memories())[0], "first")
