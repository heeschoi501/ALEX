"""SQLite persistence for sessions, messages, summaries, memory and route feedback.

Study focus:
- sqlite3 기본 사용법
- ``contextlib.contextmanager`` 로 connection 을 **반드시 close** 하는 방법
- 작은 schema migration
- 예외를 사용자 친화적 메시지로 바꾸기
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
from typing import Iterator
import uuid


SCHEMA_VERSION = 7
# 여러 사람이 쓰기(2026-10-11): 대화·기억·할 일에 주인을 단다. 예전 데이터와 CLI 는 모두 이 PC 주인(관리자) 것이다.
OWNER = "owner"


class StorageError(RuntimeError):
    """DB 손상/권한 문제 등을 사용자에게 설명하기 위한 예외."""


@dataclass(slots=True)
class StoredMessage:
    role: str
    content: str
    created_at: str


@dataclass(slots=True)
class SummaryMessage:
    message_id: int
    role: str
    content: str


@dataclass(slots=True)
class SessionSummary:
    session_id: str
    message_count: int
    updated_at: str
    has_summary: bool = False
    title: str = ""
    project_id: str = ""


@dataclass(slots=True)
class RouteEvent:
    event_id: int
    session_id: str
    text: str
    destination: str
    reason: str
    matched: str
    correction: str | None
    created_at: str


@dataclass(frozen=True)
class Experience:
    """ADR-037. 모델 독립 경험 단위. 본문 다섯 필드는 불변이다."""

    id: str
    created_at: str
    kind: str
    situation: str
    judgment: str
    rationale: str
    choice: str
    outcome: str
    verdict: str
    correction: str
    source: str
    model: str
    stage: str
    split: str
    reviewed_at: str | None
    reviewed_by: str
    content_hash: str



@dataclass(slots=True)
class TaskItem:
    task_id: int
    text: str
    status: str
    priority: int
    created_at: str
    updated_at: str


@dataclass(slots=True)
class UserAccount:
    user_id: str
    name: str
    login: str
    role: str  # admin | member
    password_hash: str
    tailscale_login: str
    disabled: bool
    created_at: str


class Storage:
    """One small SQLite database keeps persistence understandable.

    Important implementation detail:
    ``sqlite3.Connection`` 자체의 ``with`` 문은 commit/rollback 만 관리하고
    connection 을 닫아주지 않는다. 따라서 모든 DB 접근은 ``_connection()`` 을
    사용해 finally 에서 close 한다. v0.1.1 의 ResourceWarning 을 이곳에서 해결한다.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(f"데이터 디렉터리를 만들 수 없습니다: {self.db_path.parent} ({exc})") from exc
        self._backup_before_migration_if_needed()
        self._initialize()

    def _backup_before_migration_if_needed(self) -> Path | None:
        """Back up an existing legacy DB before schema migration.

        A new/current DB returns None. If a legacy DB needs migration but cannot be backed up,
        startup fails instead of modifying the only copy.
        """
        if not self.db_path.exists():
            return None
        try:
            if self.db_path.stat().st_size <= 0:
                return None
        except OSError as exc:
            raise StorageError(f"기존 DB 상태를 확인할 수 없습니다: {self.db_path} ({exc})") from exc

        try:
            with closing(sqlite3.connect(self.db_path, timeout=10)) as probe:
                table = probe.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'"
                ).fetchone()
                if not table:
                    return None
                columns = {str(row[1]) for row in probe.execute("PRAGMA table_info(sessions)").fetchall()}
                required = {"summary", "summary_through_id", "title"}
                agent_tables = {row[0] for row in probe.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name IN ('developer_runs', 'edit_proposals')")}
                owned = all("owner" in {str(row[1]) for row in probe.execute(f"PRAGMA table_info({name})")}
                            for name in ("sessions", "tasks", "memories")) and "project_id" in columns
                if required.issubset(columns) and owned and len(agent_tables) == 2:
                    return None

                stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                destination = self.db_path.parent / "backups" / (
                    f"pre-migration-v{SCHEMA_VERSION}-{stamp}-{self.db_path.name}"
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(destination)) as target:
                    probe.backup(target)
                return destination
        except sqlite3.Error as exc:
            raise StorageError(
                "DB migration 전 검사/안전 백업에 실패했습니다. 원본은 수정하지 않았습니다.\n"
                + self._db_help(exc)
            ) from exc
        except OSError as exc:
            raise StorageError(
                f"DB migration 전 안전 백업에 실패했습니다. 원본은 수정하지 않았습니다.\n"
                f"대상 파일: {self.db_path}\n오류: {exc}"
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
        except sqlite3.Error as exc:
            raise StorageError(self._db_help(exc)) from exc
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Commit/rollback + close 를 모두 보장하는 connection context manager."""
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _db_help(self, exc: Exception) -> str:
        return (
            f"SQLite 오류: {exc}\n"
            f"대상 파일: {self.db_path}\n"
            "파일이 손상된 것 같다면 먼저 백업한 뒤 해당 파일의 이름을 바꾸거나 옮기고 "
            "다시 실행하세요. 새 DB가 자동으로 생성됩니다."
        )

    def _initialize(self) -> None:
        try:
            with self._connection() as conn:
                # Connection-level durability/concurrency policy must be set before schema writes.
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA synchronous = NORMAL")
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        summary TEXT NOT NULL DEFAULT '',
                        summary_through_id INTEGER NOT NULL DEFAULT 0,
                        title TEXT NOT NULL DEFAULT '',
                        owner TEXT NOT NULL DEFAULT 'owner',
                        project_id TEXT NOT NULL DEFAULT ''
                    );

                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_messages_session
                        ON messages(session_id, id);

                    CREATE TABLE IF NOT EXISTS memories (
                        owner TEXT NOT NULL DEFAULT 'owner',
                        key TEXT NOT NULL,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(owner, key)
                    );

                    CREATE TABLE IF NOT EXISTS route_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        text TEXT NOT NULL,
                        destination TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        matched TEXT NOT NULL DEFAULT '',
                        correction TEXT,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_route_events_session
                        ON route_events(session_id, id);

                    CREATE TABLE IF NOT EXISTS tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        text TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'open',
                        priority INTEGER NOT NULL DEFAULT 2,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        owner TEXT NOT NULL DEFAULT 'owner'
                    );

                    CREATE INDEX IF NOT EXISTS idx_tasks_status_priority
                        ON tasks(status, priority, id);

                    CREATE TABLE IF NOT EXISTS app_state (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS schema_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS developer_runs (
                        id TEXT PRIMARY KEY,
                        workspace TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_developer_runs_workspace
                        ON developer_runs(workspace, updated_at);
                    CREATE TABLE IF NOT EXISTS edit_proposals (
                        workspace TEXT NOT NULL,
                        id TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(workspace, id)
                    );

                    -- ADR-037. memories 와 달리 사용자가 고칠 수 없는 학습/평가 코퍼스다.
                    -- 본문 다섯 필드는 모델 독립이며 불변이다(content_hash 로 강제).
                    CREATE TABLE IF NOT EXISTS experiences (
                        id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        situation TEXT NOT NULL,
                        judgment TEXT NOT NULL,
                        rationale TEXT NOT NULL DEFAULT '',
                        choice TEXT NOT NULL DEFAULT '',
                        outcome TEXT NOT NULL DEFAULT '',
                        verdict TEXT NOT NULL DEFAULT 'unrated',
                        correction TEXT NOT NULL DEFAULT '',
                        source TEXT NOT NULL DEFAULT '',
                        model TEXT NOT NULL DEFAULT '',
                        stage TEXT NOT NULL DEFAULT 'raw',
                        split TEXT NOT NULL,
                        reviewed_at TEXT,
                        reviewed_by TEXT NOT NULL DEFAULT '',
                        content_hash TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_experiences_stage
                        ON experiences(stage, split);
                    CREATE INDEX IF NOT EXISTS idx_experiences_source
                        ON experiences(source);

                    -- ADR-037 결정 7. 어느 경험이 어느 모델 버전에 들어갔는가.
                    CREATE TABLE IF NOT EXISTS training_runs (
                        id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        base_model TEXT NOT NULL,
                        notes TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE IF NOT EXISTS training_members (
                        run_id TEXT NOT NULL,
                        experience_id TEXT NOT NULL,
                        PRIMARY KEY (run_id, experience_id)
                    );

                    -- ADR-037 결정 6. 학습 추출은 반드시 이 뷰만 쓴다.
                    -- 평가셋 오염을 약속이 아니라 구조로 막는다.
                    CREATE VIEW IF NOT EXISTS training_pool AS
                        SELECT * FROM experiences
                        WHERE stage = 'approved' AND split = 'train';

                    -- 여러 사람이 쓰기(2026-10-11). 비밀번호는 해시만, 로그인 쿠키도 해시만 저장한다.
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        login TEXT NOT NULL UNIQUE,
                        role TEXT NOT NULL DEFAULT 'member',
                        password_hash TEXT NOT NULL DEFAULT '',
                        tailscale_login TEXT NOT NULL DEFAULT '',
                        disabled INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS auth_sessions (
                        token_hash TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );
                    -- 기억 찾기 색인(2026-10-18): 예전에는 최근 기억 50개 안에서만 골라, 기억이 많아지면 오래된 관련 기억이 조용히 빠졌다.
                    CREATE VIRTUAL TABLE IF NOT EXISTS memory_search USING fts5(owner UNINDEXED, key UNINDEXED, terms);
                    -- 프로젝트(2026-10-11, Gemini Gems·Claude Projects 처럼): 이름 + 지침 + 자료 + 그 안의 대화.
                    CREATE TABLE IF NOT EXISTS projects (
                        id TEXT PRIMARY KEY,
                        owner TEXT NOT NULL,
                        name TEXT NOT NULL,
                        instructions TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    -- 앱이 만든 그림·영상·문서의 주인. 기록이 없는 예전 파일은 이 PC 주인 것으로 본다.
                    CREATE TABLE IF NOT EXISTS artifacts (
                        name TEXT PRIMARY KEY,
                        owner TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    """
                )
                self._migrate_sessions(conn)
                self._migrate_owners(conn)
                self._sync_memory_search(conn)
                conn.execute(
                    "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(SCHEMA_VERSION),),
                )
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    @staticmethod
    def _migrate_sessions(conn: sqlite3.Connection) -> None:
        """v0.1.1 DB 를 데이터 삭제 없이 v0.1.2 schema 로 올린다."""
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "summary" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
        if "summary_through_id" not in columns:
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN summary_through_id INTEGER NOT NULL DEFAULT 0"
            )
        if "title" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _memory_terms(key: str, value: str) -> str:
        from .knowledge import search_terms  # 학습 자료 검색과 같은 낱말 조각(한글은 두 글자 조각 — 조사·어미가 달라도 맞는다)
        return " ".join(sorted(search_terms(f"{key} {value}")))

    @classmethod
    def _sync_memory_search(cls, conn: sqlite3.Connection) -> None:
        """기억 색인이 기억 표와 어긋나면(처음 만들 때·예전 DB) 통째로 다시 만든다."""
        count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        if conn.execute("SELECT COUNT(*) FROM memory_search").fetchone()[0] == count:
            return
        conn.execute("DELETE FROM memory_search")
        conn.executemany("INSERT INTO memory_search(owner, key, terms) VALUES (?, ?, ?)",
                         [(row["owner"], row["key"], cls._memory_terms(row["key"], row["value"]))
                          for row in conn.execute("SELECT owner, key, value FROM memories")])

    @staticmethod
    def _migrate_owners(conn: sqlite3.Connection) -> None:
        """v7: 대화·할 일·기억에 주인을 단다. 있던 것은 모두 이 PC 주인 것이 된다(지우는 것 없음)."""
        for table in ("sessions", "tasks"):
            if "owner" not in {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN owner TEXT NOT NULL DEFAULT '{OWNER}'")
        if "project_id" not in {str(row["name"]) for row in conn.execute("PRAGMA table_info(sessions)")}:
            conn.execute("ALTER TABLE sessions ADD COLUMN project_id TEXT NOT NULL DEFAULT ''")
        if "owner" not in {str(row["name"]) for row in conn.execute("PRAGMA table_info(memories)")}:
            # 기억은 이름(key)이 주인마다 따로여야 해서 기본 키가 바뀐다 — 새 표로 옮겨 담는다.
            conn.execute("ALTER TABLE memories RENAME TO memories_v6")
            conn.execute("CREATE TABLE memories (owner TEXT NOT NULL DEFAULT 'owner', key TEXT NOT NULL, "
                         "value TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(owner, key))")
            conn.execute("INSERT INTO memories(owner, key, value, updated_at) "
                         f"SELECT '{OWNER}', key, value, updated_at FROM memories_v6")
            conn.execute("DROP TABLE memories_v6")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner, status)")
        if conn.execute("SELECT 1 FROM users WHERE id = ?", (OWNER,)).fetchone() is None:
            conn.execute("INSERT INTO users(id, name, login, role, created_at) VALUES (?, '관리자', 'admin', 'admin', ?)",
                         (OWNER, datetime.now(timezone.utc).isoformat()))

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_session(self, owner: str = OWNER, project_id: str = "") -> str:
        session_id = uuid.uuid4().hex[:12]
        now = self._now()
        try:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO sessions(id, created_at, updated_at, owner, project_id) VALUES (?, ?, ?, ?, ?)",
                    (session_id, now, now, owner, project_id),
                )
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return session_id

    def latest_session_id(self, owner: str = OWNER) -> str | None:
        try:
            with self._connection() as conn:
                # rowid 로 동점을 깬다. Windows 시계 해상도가 약 15ms 라서 연달아 만든
                # 세션은 updated_at 이 같고, 그러면 순서가 임의가 된다 — 앱이 시작할 때
                # 이 함수로 대화를 이어가므로 엉뚱한 대화가 열릴 수 있다.
                # 메모는 _next_memory_timestamp 로 같은 문제를 이미 고쳤고 여기가 남아 있었다.
                row = conn.execute(
                    "SELECT id FROM sessions WHERE owner = ? ORDER BY updated_at DESC, rowid DESC LIMIT 1", (owner,)
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return str(row["id"]) if row else None

    def add_message(self, session_id: str, role: str, content: str) -> None:
        now = self._now()
        try:
            with self._connection() as conn:
                cursor = conn.execute(
                    "INSERT INTO messages(session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                    (session_id, role, content, now),
                )
                if cursor.rowcount != 1:
                    raise StorageError("메시지를 저장하지 못했습니다.")
                conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (now, session_id),
                )
        except sqlite3.IntegrityError as exc:
            raise StorageError(f"존재하지 않는 세션입니다: {session_id}") from exc
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def message_page(self, session_id: str, before: int | None = None, limit: int = 100, *, after: int | None = None) -> dict:
        """Bounded keyset pagination; after reads oldest unseen rows first."""
        limit = max(1, min(200, limit))
        order = "ASC" if after is not None else "DESC"
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    "SELECT id, role, content, created_at FROM messages WHERE session_id=? "
                    f"AND id < ? AND id > ? ORDER BY id {order} LIMIT ?",
                    (session_id, before if before is not None else 9223372036854775807,
                     after if after is not None else 0, limit + 1),
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        page = rows[:limit] if after is not None else reversed(rows[:limit])
        return {"messages": [dict(row) for row in page], "has_more": len(rows) > limit}

    def get_messages(self, session_id: str, limit: int = 12) -> list[StoredMessage]:
        if limit < 1:
            return []
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    """
                    SELECT role, content, created_at
                    FROM messages
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (session_id, limit),
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return [StoredMessage(**dict(row)) for row in reversed(rows)]

    def message_count(self, session_id: str) -> int:
        try:
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM messages WHERE session_id = ?", (session_id,)
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return int(row["n"]) if row else 0

    # --------------------------------------------------------- conversation summary

    def get_session_summary(self, session_id: str) -> tuple[str, int]:
        try:
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT summary, summary_through_id FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        if not row:
            return "", 0
        return str(row["summary"] or ""), int(row["summary_through_id"] or 0)

    def update_session_summary(self, session_id: str, summary: str, through_id: int) -> None:
        try:
            with self._connection() as conn:
                cursor = conn.execute(
                    """
                    UPDATE sessions
                    SET summary = ?, summary_through_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (summary.strip(), int(through_id), self._now(), session_id),
                )
                if cursor.rowcount != 1:
                    raise StorageError(f"요약을 저장할 세션을 찾을 수 없습니다: {session_id}")
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def get_messages_for_summary(
        self, session_id: str, keep_recent: int, max_items: int = 40
    ) -> list[SummaryMessage]:
        """아직 요약되지 않았고 recent window 밖에 있는 오래된 메시지를 반환한다."""
        _, through_id = self.get_session_summary(session_id)
        try:
            with self._connection() as conn:
                # 최신 keep_recent 의 시작 id 보다 작은 메시지만 summary 후보로 삼는다.
                cutoff = conn.execute(
                    """
                    SELECT MIN(id) AS cutoff_id FROM (
                        SELECT id FROM messages
                        WHERE session_id = ?
                        ORDER BY id DESC LIMIT ?
                    )
                    """,
                    (session_id, max(1, keep_recent)),
                ).fetchone()
                cutoff_id = int(cutoff["cutoff_id"]) if cutoff and cutoff["cutoff_id"] is not None else 0
                if cutoff_id <= 0:
                    return []
                rows = conn.execute(
                    """
                    SELECT id, role, content
                    FROM messages
                    WHERE session_id = ? AND id > ? AND id < ?
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (session_id, through_id, cutoff_id, max_items),
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return [
            SummaryMessage(int(row["id"]), str(row["role"]), str(row["content"])) for row in rows
        ]

    # --------------------------------------------------------- persistent memory

    def _next_memory_timestamp(self, conn: sqlite3.Connection) -> str:
        """저장된 어떤 메모보다도 엄격하게 새로운 timestamp 를 돌려준다.

        Windows 시계 해상도는 약 15ms 라서 연속 저장이 같은 ``updated_at`` 을 받는다.
        그러면 ``ORDER BY updated_at DESC`` 가 동률이 되어 "가장 최근 메모" 순서가
        삽입 순서로 뒤집힌다. 동률일 때만 1마이크로초 밀어 순서를 항상 결정적으로 만든다.
        """
        now = self._now()
        row = conn.execute("SELECT MAX(updated_at) AS newest FROM memories").fetchone()
        newest = row["newest"] if row is not None else None
        if newest and now <= str(newest):
            return (datetime.fromisoformat(str(newest)) + timedelta(microseconds=1)).isoformat()
        return now

    def remember(self, key: str, value: str, owner: str = OWNER) -> None:
        clean_key = key.strip()
        clean_value = value.strip()
        if not clean_key:
            raise StorageError("메모 key 는 비어 있을 수 없습니다.")
        if len(clean_key) > 100:
            raise StorageError("메모 key 는 100자 이하로 입력하세요.")
        if not clean_value:
            raise StorageError("메모 value 는 비어 있을 수 없습니다.")
        if len(clean_value) > 10_000:
            raise StorageError("메모 value 는 10000자 이하로 입력하세요. 긴 문서는 프로젝트 파일/RAG로 관리하세요.")
        try:
            with self._connection() as conn:
                conn.execute(
                    """
                    INSERT INTO memories(owner, key, value, updated_at) VALUES (?, ?, ?, ?)
                    ON CONFLICT(owner, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                    """,
                    (owner, clean_key, clean_value, self._next_memory_timestamp(conn)),
                )
                conn.execute("DELETE FROM memory_search WHERE owner = ? AND key = ?", (owner, clean_key))
                conn.execute("INSERT INTO memory_search(owner, key, terms) VALUES (?, ?, ?)",
                             (owner, clean_key, self._memory_terms(clean_key, clean_value)))
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def recall(self, key: str, owner: str = OWNER) -> str | None:
        try:
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT value FROM memories WHERE owner = ? AND key = ?", (owner, key.strip())
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return str(row["value"]) if row else None

    def forget(self, key: str, owner: str = OWNER) -> bool:
        """메모를 지운다. 지워졌으면 True."""
        try:
            with self._connection() as conn:
                cursor = conn.execute("DELETE FROM memories WHERE owner = ? AND key = ?", (owner, key.strip()))
                conn.execute("DELETE FROM memory_search WHERE owner = ? AND key = ?", (owner, key.strip()))
                return cursor.rowcount > 0
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def list_memories(self, limit: int | None = None, owner: str = OWNER) -> dict[str, str]:
        query = "SELECT key, value FROM memories WHERE owner = ? ORDER BY updated_at DESC"
        params: tuple[object, ...] = (owner,)
        if limit is not None:
            query += " LIMIT ?"
            params = (owner, max(0, limit))
        try:
            with self._connection() as conn:
                rows = conn.execute(query, params).fetchall()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return {str(row["key"]): str(row["value"]) for row in rows}

    def search_memories(self, terms: Iterable[str], owner: str = OWNER, limit: int = 50) -> dict[str, str]:
        """질문의 낱말 조각이 들어 있는 기억을 오래된 것까지 모두 찾아 관련도 순으로(색인 검색). 없으면 빈 dict."""
        words = sorted({str(term) for term in terms if str(term).strip()})[:80]
        if not words:
            return {}
        expression = " OR ".join('"' + word.replace('"', '""') + '"' for word in words)
        try:
            with self._connection() as conn:
                keys = [row["key"] for row in conn.execute(
                    "SELECT key FROM memory_search WHERE memory_search MATCH ? AND owner = ? "
                    "ORDER BY bm25(memory_search) LIMIT ?", (expression, owner, max(1, limit)))]
                found = {}
                for key in keys:
                    row = conn.execute("SELECT value FROM memories WHERE owner = ? AND key = ?", (owner, key)).fetchone()
                    if row is not None:
                        found[str(key)] = str(row["value"])
                return found
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    # --------------------------------------------------------- session navigation / export

    # ── 경험 저장소 (ADR-037) ────────────────────────────────────────────────
    # memories 와 달리 append-only 다. 본문은 고칠 수 없고 검토 메타데이터만 바뀐다.

    EXPERIENCE_KINDS = ("route", "answer", "edit", "codegen", "identity")
    EXPERIENCE_STAGES = ("raw", "candidate", "approved", "rejected")
    EVAL_RATIO = 0.2

    @staticmethod
    def _experience_hash(situation: str, judgment: str, rationale: str,
                         choice: str, outcome: str) -> str:
        """본문 다섯 필드만으로 해시한다. 검토 메타데이터는 포함하지 않는다."""
        payload = "\x00".join((situation, judgment, rationale, choice, outcome))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def _split_for(cls, experience_id: str) -> str:
        """ID 해시로 결정적 분할 (ADR-037 결정 5).

        사람이 고르면 흥미로운 사례가 학습으로 몰려 평가셋이 분포를 대표하지 못한다.
        같은 ID 는 언제 어디서 계산해도 같은 쪽으로 간다.
        """
        digest = hashlib.sha256(experience_id.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
        return "eval" if bucket < cls.EVAL_RATIO else "train"

    def record_experience(self, kind: str, situation: str, judgment: str, *,
                          rationale: str = "", choice: str = "", outcome: str = "",
                          verdict: str = "unrated", correction: str = "",
                          source: str = "", model: str = "") -> str:
        clean_kind = kind.strip()
        if clean_kind not in self.EXPERIENCE_KINDS:
            raise StorageError(
                f"알 수 없는 경험 종류입니다: {kind!r}. 가능: {', '.join(self.EXPERIENCE_KINDS)}"
            )
        if not situation.strip() or not judgment.strip():
            raise StorageError("경험에는 situation 과 judgment 가 반드시 있어야 합니다.")
        content_hash = self._experience_hash(situation, judgment, rationale, choice, outcome)
        # ID 는 내용과 시각으로 만든다. 같은 내용을 두 번 적어도 별개 경험이다.
        stamp = self._now()
        experience_id = hashlib.sha256(f"{content_hash}{stamp}".encode("utf-8")).hexdigest()[:24]
        try:
            with self._connection() as conn:
                conn.execute(
                    """
                    INSERT INTO experiences(
                        id, created_at, kind, situation, judgment, rationale, choice, outcome,
                        verdict, correction, source, model, stage, split, content_hash
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'raw',?,?)
                    """,
                    (experience_id, stamp, clean_kind, situation, judgment, rationale,
                     choice, outcome, verdict, correction, source, model,
                     self._split_for(experience_id), content_hash),
                )
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return experience_id

    EXPERIENCE_COLUMNS = (
        "id", "created_at", "kind", "situation", "judgment", "rationale", "choice",
        "outcome", "verdict", "correction", "source", "model", "stage", "split",
        "reviewed_at", "reviewed_by", "content_hash",
    )

    def import_experience(self, record: dict) -> bool:
        """다른 기기에서 온 경험을 **id 를 보존한 채** 넣는다. 새로 넣었으면 True.

        `record_experience` 를 쓰면 안 된다. 그것은 새 id 를 만들고, split 은 id 해시에서
        나온다(`_split_for`). 같은 내용이 노트북에서는 train, 데스크톱에서는 eval 이 될 수
        있고 **그러면 한쪽이 학습한 것을 다른 쪽이 평가에 쓴다.** 평가셋 오염이다.

        id 와 created_at 을 그대로 보존하면 split 도 그대로다. 기기가 몇 대든 같은 경험은
        같은 쪽에 남는다.

        본문 5필드로 content_hash 를 다시 계산해 대조한다. 어긋나면 옮기는 중에 변조된
        것이므로 거부한다 — 그래서 이 함수는 `verify_experience_integrity` 를 우회하지 않는다.
        """
        missing = [c for c in ("id", "created_at", "kind", "situation", "judgment",
                               "split", "content_hash") if not str(record.get(c, "")).strip()]
        if missing:
            raise StorageError(f"경험에 필수 항목이 없습니다: {', '.join(missing)}")
        kind = str(record["kind"]).strip()
        if kind not in self.EXPERIENCE_KINDS:
            raise StorageError(f"알 수 없는 경험 종류입니다: {kind!r}")
        if str(record["split"]).strip() not in ("train", "eval"):
            raise StorageError(f"split 은 train/eval 이어야 합니다: {record['split']!r}")
        expected = self._experience_hash(
            str(record["situation"]), str(record["judgment"]), str(record.get("rationale", "")),
            str(record.get("choice", "")), str(record.get("outcome", "")))
        if expected != str(record["content_hash"]):
            raise StorageError(
                f"경험 {record['id']} 의 content_hash 가 본문과 맞지 않습니다. "
                "옮기는 중에 내용이 바뀌었습니다.")
        values = []
        for column in self.EXPERIENCE_COLUMNS:
            value = record.get(column)
            values.append(None if column == "reviewed_at" and not value else
                          ("" if value is None else str(value)))
        placeholders = ", ".join("?" for _ in self.EXPERIENCE_COLUMNS)
        try:
            with closing(sqlite3.connect(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    f"INSERT OR IGNORE INTO experiences ({', '.join(self.EXPERIENCE_COLUMNS)}) "
                    f"VALUES ({placeholders})", values)
                conn.commit()
                return cursor.rowcount == 1
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def review_experience(self, experience_id: str, stage: str, *,
                          reviewed_by: str = "human", correction: str = "") -> bool:
        """검토 결과만 바꾼다. 본문은 절대 바뀌지 않는다 (ADR-037 결정 3)."""
        if stage not in self.EXPERIENCE_STAGES:
            raise StorageError(
                f"알 수 없는 단계입니다: {stage!r}. 가능: {', '.join(self.EXPERIENCE_STAGES)}"
            )
        try:
            with self._connection() as conn:
                sets = "stage = ?, reviewed_at = ?, reviewed_by = ?"
                params: list[object] = [stage, self._now(), reviewed_by]
                if correction:
                    sets += ", correction = ?"
                    params.append(correction)
                params.append(experience_id)
                cursor = conn.execute(f"UPDATE experiences SET {sets} WHERE id = ?", params)
                return cursor.rowcount > 0
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def _row_to_experience(self, row: sqlite3.Row) -> Experience:
        return Experience(
            id=str(row["id"]), created_at=str(row["created_at"]), kind=str(row["kind"]),
            situation=str(row["situation"]), judgment=str(row["judgment"]),
            rationale=str(row["rationale"]), choice=str(row["choice"]),
            outcome=str(row["outcome"]), verdict=str(row["verdict"]),
            correction=str(row["correction"]), source=str(row["source"]),
            model=str(row["model"]), stage=str(row["stage"]), split=str(row["split"]),
            reviewed_at=str(row["reviewed_at"]) if row["reviewed_at"] is not None else None,
            reviewed_by=str(row["reviewed_by"]), content_hash=str(row["content_hash"]),
        )

    def list_experiences(self, *, stage: str | None = None, split: str | None = None,
                         kind: str | None = None, limit: int = 100) -> list[Experience]:
        query = "SELECT * FROM experiences"
        where: list[str] = []
        params: list[object] = []
        for column, value in (("stage", stage), ("split", split), ("kind", kind)):
            if value:
                where.append(f"{column} = ?")
                params.append(value)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        try:
            with self._connection() as conn:
                return [self._row_to_experience(r) for r in conn.execute(query, params).fetchall()]
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def training_pool(self, limit: int = 10_000) -> list[Experience]:
        """학습 추출은 반드시 이 경로만 쓴다 (ADR-037 결정 6).

        뷰가 stage='approved' AND split='train' 을 강제하므로 평가셋이 구조적으로 섞이지 않는다.
        """
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM training_pool ORDER BY created_at LIMIT ?",
                    (max(1, int(limit)),),
                ).fetchall()
                return [self._row_to_experience(r) for r in rows]
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def verify_experience_integrity(self) -> list[str]:
        """본문이 변조된 경험의 ID 를 돌려준다. 빈 리스트면 정상이다."""
        broken: list[str] = []
        try:
            with self._connection() as conn:
                for row in conn.execute("SELECT * FROM experiences").fetchall():
                    expected = self._experience_hash(
                        str(row["situation"]), str(row["judgment"]), str(row["rationale"]),
                        str(row["choice"]), str(row["outcome"]),
                    )
                    if expected != str(row["content_hash"]):
                        broken.append(str(row["id"]))
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return broken

    def experience_counts(self) -> dict[str, int]:
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    "SELECT stage, split, COUNT(*) AS n FROM experiences GROUP BY stage, split"
                ).fetchall()
                return {f"{r['stage']}/{r['split']}": int(r["n"]) for r in rows}
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def record_training_run(self, run_id: str, base_model: str, experience_ids: Sequence[str],
                            notes: str = "") -> None:
        """어느 경험이 어느 모델 버전에 들어갔는지 남긴다 (ADR-037 결정 7)."""
        if not run_id.strip():
            raise StorageError("학습 실행 ID 가 필요합니다 (예: ALEX-M1).")
        try:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO training_runs(id, created_at, base_model, notes) VALUES (?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET base_model=excluded.base_model, notes=excluded.notes",
                    (run_id.strip(), self._now(), base_model, notes),
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO training_members(run_id, experience_id) VALUES (?,?)",
                    [(run_id.strip(), e) for e in experience_ids],
                )
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def experiences_in_run(self, run_id: str) -> list[str]:
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    "SELECT experience_id FROM training_members WHERE run_id = ? ORDER BY experience_id",
                    (run_id,),
                ).fetchall()
                return [str(r["experience_id"]) for r in rows]
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def has_experience_source(self, source: str) -> bool:
        """같은 출처(source)의 경험이 이미 있는가. 자동 학습이 같은 것을 두 번 배우지 않게 한다(ADR-057)."""
        try:
            with self._connection() as conn:
                return conn.execute("SELECT 1 FROM experiences WHERE source = ? LIMIT 1", (source,)).fetchone() is not None
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def training_run_count(self) -> int:
        try:
            with self._connection() as conn:
                return int(conn.execute("SELECT COUNT(*) FROM training_runs").fetchone()[0])
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    # scripts/lora.py 가 학습이 실제로 끝났을 때만 이 말로 시작하는 기록을 남긴다(ADR-084). 내보내기 기록과 구분한다.
    LORA_TRAINED_NOTE = "LoRA 학습 완료"

    def lora_trained_run_count(self) -> int:
        try:
            with self._connection() as conn:
                return int(conn.execute("SELECT COUNT(*) FROM training_runs WHERE notes LIKE ?",
                                        (self.LORA_TRAINED_NOTE + "%",)).fetchone()[0])
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc


    def session_exists(self, session_id: str) -> bool:
        try:
            with self._connection() as conn:
                row = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return row is not None

    def session_owner(self, session_id: str) -> str | None:
        try:
            with self._connection() as conn:
                row = conn.execute("SELECT owner FROM sessions WHERE id = ?", (session_id,)).fetchone()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return str(row["owner"]) if row else None

    def rename_session(self, session_id: str, title: str) -> None:
        clean = " ".join(title.strip().split())
        if not clean:
            raise StorageError("세션 제목은 비어 있을 수 없습니다.")
        if len(clean) > 100:
            raise StorageError("세션 제목은 100자 이하로 입력하세요.")
        try:
            with self._connection() as conn:
                cursor = conn.execute(
                    "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                    (clean, self._now(), session_id),
                )
                if cursor.rowcount != 1:
                    raise StorageError(f"세션을 찾을 수 없습니다: {session_id}")
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def session_title(self, session_id: str) -> str:
        try:
            with self._connection() as conn:
                row = conn.execute("SELECT title FROM sessions WHERE id = ?", (session_id,)).fetchone()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return str(row["title"] or "") if row else ""

    def delete_session(self, session_id: str) -> bool:
        """Explicit local deletion. FK cascade removes messages and route feedback too."""
        try:
            with self._connection() as conn:
                cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                return cursor.rowcount > 0
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def search_messages(self, query: str, owner: str = OWNER, limit: int = 20, per_session: int = 3) -> list[dict]:
        """지난 대화 내용 찾기(2026-10-26, Hermes Agent 의 지난 대화 검색 참고) — 이 주인의 대화만, 제목이 아니라 메시지 본문에서.

        따로 색인을 두지 않는다: 대화를 지우면(대화방·프로젝트 삭제의 FK cascade) 바로 찾을 수 없게 된다. 한글은 학습 자료 검색과 같은
        두 글자 조각이라 조사가 달라도 맞는다('회의록은' ↔ '회의록을'). 조각의 3분의 1 이상이 들어 있는 메시지를 많이 맞은 순·최근 순으로."""
        from .knowledge import search_terms
        terms = sorted(search_terms(query or ""), key=lambda t: (-len(t), t))[:12]
        if not terms:
            return []
        need = max(1, -(-len(terms) // 3))
        clause = " OR ".join("lower(m.content) LIKE ? ESCAPE '\\'" for _ in terms)
        like = ["%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for t in terms]
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    "SELECT m.id, m.session_id, m.role, m.content, m.created_at, s.title, COALESCE(p.name, '') AS project "
                    "FROM messages m JOIN sessions s ON s.id = m.session_id LEFT JOIN projects p ON p.id = s.project_id "
                    f"WHERE s.owner = ? AND ({clause}) ORDER BY m.id DESC LIMIT 2000", (owner, *like)).fetchall()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        scored = []
        for row in rows:
            text = str(row["content"])
            lowered = text.lower()
            hits = [t for t in terms if t in lowered]
            if len(hits) < need:
                continue
            at = min(lowered.find(t) for t in hits)
            start = max(0, at - 40)
            snippet = " ".join(text[start:start + 160].split())
            scored.append((len(hits), int(row["id"]), {
                "message_id": int(row["id"]), "session_id": str(row["session_id"]), "title": str(row["title"] or ""),
                "project": str(row["project"]), "role": str(row["role"]), "created_at": str(row["created_at"]),
                "snippet": ("…" if start else "") + snippet + ("…" if start + 160 < len(text) else "")}))
        found, per = [], {}
        for _hits, _id, item in sorted(scored, key=lambda x: (-x[0], -x[1])):
            if per.get(item["session_id"], 0) >= per_session:
                continue
            per[item["session_id"]] = per.get(item["session_id"], 0) + 1
            found.append(item)
            if len(found) >= limit:
                break
        return found

    def messages_after(self, last_id: int, limit: int = 5000) -> list[dict]:
        """월별 대화 기록(archive.py)용 — last_id 다음 메시지들을 대화방 정보(주인·제목·프로젝트 이름)와 함께, 오래된 순으로."""
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    "SELECT m.id, m.session_id, m.role, m.content, m.created_at, s.owner, s.title, "
                    "COALESCE(p.name, '') AS project FROM messages m JOIN sessions s ON s.id = m.session_id "
                    "LEFT JOIN projects p ON p.id = s.project_id WHERE m.id > ? ORDER BY m.id ASC LIMIT ?",
                    (int(last_id), int(limit))).fetchall()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return [dict(row) for row in rows]

    def export_session_markdown(self, session_id: str, destination: Path | None = None) -> Path:
        """세션 전체를 로컬 Markdown 으로 내보낸다. Cloud 전송은 하지 않는다."""
        if not self.session_exists(session_id):
            raise StorageError(f"세션을 찾을 수 없습니다: {session_id}")
        if destination is None:
            destination = self.db_path.parent / "exports" / f"session-{session_id}.md"
        destination = Path(destination)
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    "SELECT role, content, created_at FROM messages WHERE session_id = ? ORDER BY id ASC",
                    (session_id,),
                ).fetchall()
            summary, _ = self.get_session_summary(session_id)
            title = self.session_title(session_id)
            destination.parent.mkdir(parents=True, exist_ok=True)
            heading = f"# {title} — ALEX Session {session_id}" if title else f"# ALEX Session {session_id}"
            parts = [heading, ""]
            if summary:
                parts += ["## Rolling Summary", "", summary, ""]
            parts += ["## Conversation", ""]
            for row in rows:
                role = str(row["role"]).capitalize()
                parts += [f"### {role} — {row['created_at']}", "", str(row["content"]), ""]
            destination.write_text("\n".join(parts), encoding="utf-8")
        except OSError as exc:
            raise StorageError(f"세션 export 에 실패했습니다: {destination} ({exc})") from exc
        return destination

    # --------------------------------------------------------- router feedback

    def record_route_event(
        self,
        session_id: str,
        text: str,
        destination: str,
        reason: str,
        matched: str = "",
    ) -> int:
        try:
            with self._connection() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO route_events(
                        session_id, text, destination, reason, matched, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (session_id, text, destination, reason, matched, self._now()),
                )
                return int(cursor.lastrowid)
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def last_route_destination(self, session_id: str) -> str | None:
        """그 세션의 바로 앞 요청이 간 곳. 코딩 대화의 후속 질문을 코딩 모델로 이어 보낼 때 쓴다."""
        try:
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT destination FROM route_events WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return str(row[0]) if row else None

    def recent_routes(self, session_id: str, limit: int = 1) -> list[tuple[str, str]]:
        """그 세션의 최근 요청이 간 곳과 이유(새것부터). /build 로 시작한 만들기를 "고쳐줘" 로 이어 갈 때 쓴다."""
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    "SELECT destination, reason FROM route_events WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                    (session_id, max(1, int(limit))),
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return [(str(row[0]), str(row[1])) for row in rows]

    def correct_route_event(self, event_id: int, correction: str) -> bool:
        try:
            with self._connection() as conn:
                cursor = conn.execute(
                    "UPDATE route_events SET correction = ? WHERE id = ?",
                    (correction.strip() or "unspecified", int(event_id)),
                )
                return cursor.rowcount > 0
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def corrected_route_events(self, limit: int = 1000) -> list[RouteEvent]:
        """사용자가 /wrong 으로 정정한 route 기록만 돌려준다.

        이 신호는 오래전부터 쌓이고 있었지만 통계 출력 외에 쓰이는 곳이 없었다.
        ADR-037 이후 경험 저장소로 흡수한다.
        """
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    """
                    SELECT id, session_id, text, destination, reason, matched, correction, created_at
                    FROM route_events
                    WHERE correction IS NOT NULL AND correction != ''
                    ORDER BY id DESC LIMIT ?
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return [
            RouteEvent(
                event_id=int(r["id"]), session_id=str(r["session_id"]), text=str(r["text"]),
                destination=str(r["destination"]), reason=str(r["reason"]),
                matched=str(r["matched"]), created_at=str(r["created_at"]),
                correction=str(r["correction"]),
            )
            for r in rows
        ]

    def get_route_event(self, event_id: int) -> RouteEvent | None:
        try:
            with self._connection() as conn:
                row = conn.execute(
                    """
                    SELECT id, session_id, text, destination, reason, matched, correction, created_at
                    FROM route_events WHERE id = ?
                    """,
                    (int(event_id),),
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        if not row:
            return None
        return RouteEvent(
            event_id=int(row["id"]),
            session_id=str(row["session_id"]),
            text=str(row["text"]),
            destination=str(row["destination"]),
            reason=str(row["reason"]),
            matched=str(row["matched"]),
            correction=str(row["correction"]) if row["correction"] is not None else None,
            created_at=str(row["created_at"]),
        )

    def route_feedback_counts(self) -> dict[str, int]:
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    """
                    SELECT destination, COUNT(*) AS n
                    FROM route_events
                    WHERE correction IS NOT NULL
                    GROUP BY destination
                    ORDER BY n DESC
                    """
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return {str(row["destination"]): int(row["n"]) for row in rows}

    # --------------------------------------------------------- health / listing

    def integrity_check(self) -> tuple[bool, str]:
        try:
            with self._connection() as conn:
                row = conn.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError as exc:
            return False, self._db_help(exc)
        result = str(row[0]) if row else "unknown"
        return result.lower() == "ok", result

    def backup_database(self, destination: Path | None = None) -> Path:
        """SQLite online backup API 로 일관된 로컬 백업을 만든다."""
        if destination is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            destination = self.db_path.parent / "backups" / f"{self.db_path.stem}-{stamp}.db"
        destination = Path(destination)
        try:
            if destination.resolve() == self.db_path.resolve():
                raise StorageError("원본 DB 파일 자체를 backup destination 으로 사용할 수 없습니다.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as source, closing(sqlite3.connect(destination)) as target:
                source.backup(target)
        except StorageError:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise StorageError(f"DB 백업에 실패했습니다: {destination} ({exc})") from exc
        return destination

    def database_stats(self) -> dict[str, object]:
        """Small diagnostics snapshot without exposing conversation content."""
        try:
            with self._connection() as conn:
                stats = {
                    "sessions": int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]),
                    "messages": int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]),
                    "memories": int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]),
                    "route_events": int(conn.execute("SELECT COUNT(*) FROM route_events").fetchone()[0]),
                    "tasks": int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]),
                    "app_state": int(conn.execute("SELECT COUNT(*) FROM app_state").fetchone()[0]),
                    "schema_version": str(
                        (conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone() or ["?"])[0]
                    ),
                    "journal_mode": str(conn.execute("PRAGMA journal_mode").fetchone()[0]),
                }
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        try:
            stats["db_bytes"] = self.db_path.stat().st_size if self.db_path.exists() else 0
        except OSError:
            stats["db_bytes"] = -1
        return stats

    def set_state(self, key: str, value: str) -> None:
        name = key.strip()
        if not name or len(name) > 100:
            raise StorageError("상태 key는 1~100자여야 합니다.")
        if len(value) > 10_000:
            raise StorageError("상태 value는 10000자 이하이어야 합니다.")
        try:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO app_state(key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (name, value, self._now()),
                )
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def get_state(self, key: str) -> str | None:
        try:
            with self._connection() as conn:
                row = conn.execute("SELECT value FROM app_state WHERE key=?", (key.strip(),)).fetchone()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return str(row["value"]) if row is not None else None

    def save_developer_run(self, run_id: str, workspace: str, payload: str) -> None:
        """Archive a run and update the active snapshot in one transaction."""
        if not run_id or len(payload) > 10_000:
            raise StorageError("개발 작업 기록이 비어 있거나 너무 큽니다.")
        try:
            with self._connection() as conn:
                now = self._now()
                conn.execute(
                    "INSERT INTO developer_runs VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                    (run_id, workspace, payload, now))
                conn.execute(
                    "INSERT INTO app_state VALUES ('developer.active', ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (payload, now))
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def developer_runs(self, workspace: str, run_id: str | None = None) -> list[str]:
        try:
            with self._connection() as conn:
                if run_id is not None:
                    rows = conn.execute("SELECT payload FROM developer_runs WHERE workspace=? AND id=?",
                                        (workspace, run_id)).fetchall()
                else:
                    rows = conn.execute("SELECT payload FROM developer_runs WHERE workspace=? "
                                        "ORDER BY updated_at DESC, rowid DESC LIMIT 20", (workspace,)).fetchall()
                return [str(row[0]) for row in rows]
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def save_edit_proposal(self, workspace: str, proposal_id: str, payload: str) -> None:
        if len(payload.encode("utf-8")) > 5_000_000:
            raise StorageError("편집 제안 저장 한도를 초과했습니다.")
        try:
            with self._connection() as conn:
                count = conn.execute("SELECT COUNT(*) FROM edit_proposals WHERE workspace=?",
                                     (workspace,)).fetchone()[0]
                if count >= 100:
                    raise StorageError("프로젝트의 미처리 수정안이 100개입니다. 이전 수정안을 먼저 폐기하세요.")
                conn.execute("INSERT INTO edit_proposals VALUES (?, ?, ?, ?)",
                             (workspace, proposal_id, payload, self._now()))
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def get_edit_proposal(self, workspace: str, proposal_id: str) -> str | None:
        try:
            with self._connection() as conn:
                row = conn.execute("SELECT payload FROM edit_proposals WHERE workspace=? AND id=?",
                                   (workspace, proposal_id)).fetchone()
                return str(row[0]) if row else None
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def delete_edit_proposal(self, workspace: str, proposal_id: str) -> bool:
        try:
            with self._connection() as conn:
                return conn.execute("DELETE FROM edit_proposals WHERE workspace=? AND id=?",
                                    (workspace, proposal_id)).rowcount > 0
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def add_task(self, text: str, priority: int = 2, owner: str = OWNER) -> int:
        value = text.strip()
        if not value:
            raise StorageError("할 일 내용은 비어 있을 수 없습니다.")
        if len(value) > 1000:
            raise StorageError("할 일은 1000자 이하로 입력하세요.")
        if priority not in {1, 2, 3}:
            raise StorageError("우선순위는 1(높음), 2(보통), 3(낮음) 중 하나여야 합니다.")
        now = self._now()
        try:
            with self._connection() as conn:
                cur = conn.execute(
                    "INSERT INTO tasks(text, status, priority, created_at, updated_at, owner) VALUES (?, 'open', ?, ?, ?, ?)",
                    (value, priority, now, now, owner),
                )
                return int(cur.lastrowid)
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def list_tasks(self, *, include_done: bool = False, limit: int = 100, owner: str = OWNER) -> list[TaskItem]:
        where = "WHERE owner = ?" if include_done else "WHERE owner = ? AND status = 'open'"
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    f"SELECT id, text, status, priority, created_at, updated_at FROM tasks {where} "
                    "ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, priority ASC, id ASC LIMIT ?",
                    (owner, max(1, min(int(limit), 500))),
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return [TaskItem(int(r['id']), str(r['text']), str(r['status']), int(r['priority']), str(r['created_at']), str(r['updated_at'])) for r in rows]

    def set_task_status(self, task_id: int, status: str, owner: str = OWNER) -> bool:
        if status not in {"open", "done"}:
            raise StorageError("Task 상태는 open 또는 done 이어야 합니다.")
        try:
            with self._connection() as conn:
                cur = conn.execute(
                    "UPDATE tasks SET status=?, updated_at=? WHERE id=? AND owner=?",
                    (status, self._now(), int(task_id), owner),
                )
                return cur.rowcount > 0
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def delete_task(self, task_id: int, owner: str = OWNER) -> bool:
        try:
            with self._connection() as conn:
                cur = conn.execute("DELETE FROM tasks WHERE id=? AND owner=?", (int(task_id), owner))
                return cur.rowcount > 0
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def list_sessions(self, limit: int = 10, owner: str = OWNER, project_id: str | None = None) -> list[SessionSummary]:
        """최근 세션 요약. CLI `/sessions` 에서 사용한다. project_id 가 None 이면 프로젝트 대화도 함께, ''이면 프로젝트 밖 대화만."""
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    """
                    SELECT s.id AS id,
                           s.updated_at AS updated_at,
                           s.summary AS summary,
                           s.title AS title,
                           s.project_id AS project_id,
                           COUNT(m.id) AS message_count
                    FROM sessions s
                    LEFT JOIN messages m ON m.session_id = s.id
                    WHERE s.owner = ? AND (? IS NULL OR s.project_id = ?)
                    GROUP BY s.id
                    ORDER BY s.updated_at DESC, s.rowid DESC
                    LIMIT ?
                    """,
                    (owner, project_id, project_id, max(1, limit)),
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return [
            SessionSummary(
                str(row["id"]),
                int(row["message_count"]),
                str(row["updated_at"]),
                bool(str(row["summary"] or "").strip()),
                str(row["title"] or ""),
                str(row["project_id"] or ""),
            )
            for row in rows
        ]

    # --------------------------------------------------------- 여러 사람이 쓰기: 계정·로그인·만든 파일의 주인

    @staticmethod
    def _user(row: sqlite3.Row) -> UserAccount:
        return UserAccount(str(row["id"]), str(row["name"]), str(row["login"]), str(row["role"]),
                           str(row["password_hash"]), str(row["tailscale_login"]), bool(row["disabled"]),
                           str(row["created_at"]))

    def _user_where(self, where: str, value: str) -> UserAccount | None:
        try:
            with self._connection() as conn:
                row = conn.execute(f"SELECT * FROM users WHERE {where} = ?", (value,)).fetchone()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return self._user(row) if row else None

    def get_user(self, user_id: str) -> UserAccount | None:
        return self._user_where("id", user_id)

    def user_by_login(self, login: str) -> UserAccount | None:
        return self._user_where("login", login.strip().lower())

    def user_by_tailscale(self, login: str) -> UserAccount | None:
        clean = login.strip().lower()
        return self._user_where("tailscale_login", clean) if clean else None

    def list_users(self) -> list[UserAccount]:
        try:
            with self._connection() as conn:
                rows = conn.execute("SELECT * FROM users ORDER BY CASE id WHEN ? THEN 0 ELSE 1 END, created_at",
                                    (OWNER,)).fetchall()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return [self._user(row) for row in rows]

    def create_user(self, name: str, login: str, password_hash: str, role: str = "member",
                    tailscale_login: str = "") -> UserAccount:
        user_id = uuid.uuid4().hex[:12]
        try:
            with self._connection() as conn:
                conn.execute("INSERT INTO users(id, name, login, role, password_hash, tailscale_login, created_at) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (user_id, name, login.strip().lower(), role, password_hash,
                              tailscale_login.strip().lower(), self._now()))
        except sqlite3.IntegrityError as exc:
            raise StorageError("이미 있는 아이디입니다.") from exc
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return self.get_user(user_id)  # type: ignore[return-value]

    def update_user(self, user_id: str, **fields: object) -> bool:
        allowed = {"name", "role", "password_hash", "tailscale_login", "disabled"}
        if not fields or set(fields) - allowed:
            raise StorageError("바꿀 수 없는 계정 항목입니다.")
        if "tailscale_login" in fields:
            fields["tailscale_login"] = str(fields["tailscale_login"]).strip().lower()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        try:
            with self._connection() as conn:
                if fields.get("tailscale_login") and conn.execute(
                        "SELECT 1 FROM users WHERE tailscale_login = ? AND id != ?",
                        (fields["tailscale_login"], user_id)).fetchone():
                    raise StorageError("이 Tailscale 계정은 이미 다른 사용자에게 연결되어 있습니다.")
                changed = conn.execute(f"UPDATE users SET {assignments} WHERE id = ?",
                                       (*fields.values(), user_id)).rowcount > 0
                if changed and ("password_hash" in fields or fields.get("disabled")):
                    conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))  # 다른 기기의 로그인도 끝낸다
                return changed
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def create_auth_session(self, token_hash: str, user_id: str, expires_at: str) -> None:
        try:
            with self._connection() as conn:
                conn.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (self._now(),))
                conn.execute("INSERT INTO auth_sessions(token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                             (token_hash, user_id, self._now(), expires_at))
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def auth_session_user(self, token_hash: str) -> str | None:
        try:
            with self._connection() as conn:
                row = conn.execute("SELECT user_id FROM auth_sessions WHERE token_hash = ? AND expires_at > ?",
                                   (token_hash, self._now())).fetchone()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return str(row["user_id"]) if row else None

    def delete_auth_session(self, token_hash: str) -> None:
        try:
            with self._connection() as conn:
                conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def record_artifacts(self, names: Sequence[str], owner: str) -> None:
        if not names:
            return
        try:
            with self._connection() as conn:
                conn.executemany("INSERT OR IGNORE INTO artifacts(name, owner, created_at) VALUES (?, ?, ?)",
                                 [(name, owner, self._now()) for name in names])
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def artifact_owner(self, name: str) -> str:
        try:
            with self._connection() as conn:
                row = conn.execute("SELECT owner FROM artifacts WHERE name = ?", (name,)).fetchone()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return str(row["owner"]) if row else OWNER

    # --------------------------------------------------------- 프로젝트(이름 + 지침 + 자료 + 그 안의 대화)

    PROJECT_INSTRUCTIONS_LIMIT = 8000

    @classmethod
    def _clean_project(cls, name: object = None, instructions: object = None) -> dict[str, str]:
        fields: dict[str, str] = {}
        if name is not None:
            clean = " ".join(str(name).split())
            if not clean or len(clean) > 60:
                raise StorageError("프로젝트 이름은 1~60자로 적어 주세요.")
            fields["name"] = clean
        if instructions is not None:
            text = str(instructions).strip()
            if len(text) > cls.PROJECT_INSTRUCTIONS_LIMIT:
                raise StorageError(f"지침은 {cls.PROJECT_INSTRUCTIONS_LIMIT}자 이하로 적어 주세요.")
            fields["instructions"] = text
        return fields

    def create_project(self, owner: str, name: str, instructions: str = "") -> str:
        fields = self._clean_project(name, instructions)
        project_id = uuid.uuid4().hex[:12]
        now = self._now()
        try:
            with self._connection() as conn:
                conn.execute("INSERT INTO projects(id, owner, name, instructions, created_at, updated_at) "
                             "VALUES (?, ?, ?, ?, ?, ?)", (project_id, owner, fields["name"], fields["instructions"], now, now))
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return project_id

    def get_project(self, project_id: str, owner: str | None = None) -> dict | None:
        """프로젝트 하나. owner 를 주면 그 사람 것일 때만."""
        try:
            with self._connection() as conn:
                row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        if row is None or (owner is not None and row["owner"] != owner):
            return None
        return dict(row)

    def list_projects(self, owner: str) -> list[dict]:
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    "SELECT p.*, (SELECT COUNT(*) FROM sessions s WHERE s.project_id = p.id) AS session_count "
                    "FROM projects p WHERE p.owner = ? ORDER BY p.updated_at DESC, p.rowid DESC", (owner,)).fetchall()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return [dict(row) for row in rows]

    def update_project(self, project_id: str, owner: str, name: object = None, instructions: object = None) -> bool:
        fields = self._clean_project(name, instructions)
        if not fields:
            raise StorageError("바꿀 내용이 없습니다.")
        assignments = ", ".join(f"{key} = ?" for key in fields)
        try:
            with self._connection() as conn:
                return conn.execute(f"UPDATE projects SET {assignments}, updated_at = ? WHERE id = ? AND owner = ?",
                                    (*fields.values(), self._now(), project_id, owner)).rowcount > 0
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def delete_project(self, project_id: str, owner: str) -> list[str]:
        """프로젝트와 그 안의 대화를 지운다. 지운 대화 ID 목록을 돌려준다(없는 프로젝트면 StorageError)."""
        try:
            with self._connection() as conn:
                if conn.execute("SELECT 1 FROM projects WHERE id = ? AND owner = ?", (project_id, owner)).fetchone() is None:
                    raise StorageError("프로젝트를 찾을 수 없습니다.")
                sessions = [str(row["id"]) for row in conn.execute(
                    "SELECT id FROM sessions WHERE project_id = ? AND owner = ?", (project_id, owner))]
                conn.execute("DELETE FROM sessions WHERE project_id = ? AND owner = ?", (project_id, owner))
                conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
                return sessions
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc

    def session_project(self, session_id: str) -> dict | None:
        """이 대화가 속한 프로젝트(없으면 None)."""
        try:
            with self._connection() as conn:
                row = conn.execute("SELECT p.* FROM sessions s JOIN projects p ON p.id = s.project_id "
                                   "WHERE s.id = ? AND p.owner = s.owner", (session_id,)).fetchone()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return dict(row) if row else None

    def empty_session(self, owner: str, project_id: str = "") -> str | None:
        """아직 아무 말도 없는 대화 — '새 대화'를 여러 번 눌러도 빈 대화가 쌓이지 않게 다시 쓴다."""
        try:
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT s.id FROM sessions s WHERE s.owner = ? AND s.project_id = ? AND NOT EXISTS "
                    "(SELECT 1 FROM messages m WHERE m.session_id = s.id) ORDER BY s.updated_at DESC, s.rowid DESC LIMIT 1",
                    (owner, project_id)).fetchone()
        except sqlite3.DatabaseError as exc:
            raise StorageError(self._db_help(exc)) from exc
        return str(row["id"]) if row else None
