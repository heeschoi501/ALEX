"""기억을 '뜻'으로 찾는 색인(2026-10-18, ADR-094).

낱말 색인(storage.memory_search)은 '차 정비' 로 물으면 '자동차 점검' 기억을 못 찾는다. 이 PC 의 작은 임베딩 모델
(Qwen3-Embedding 0.6B, CPU 전용 llama-server, 127.0.0.1:8081)이 글을 뜻을 담은 숫자 목록(벡터)으로 바꾸고, 질문과
가까운 기억을 고른다. 대화 모델(Strata)과 따로 돌고 그래픽카드를 쓰지 않는다.

- 벡터는 data/embeddings.db 에 두는 **다시 만들 수 있는 파생 색인**이다. 기억 원본은 jarvis.db 에 그대로 있다.
  지워도 다음 질문 때 다시 만든다. 모델이 바뀌면(profile) 예전 벡터는 쓰지 않고 새로 만든다.
- 기억이 바뀌면(내용 해시) 다시 만든다. 다른 사람 기억은 owner 로 나눠 읽지 않는다.
- 임베딩 서버가 꺼졌거나 고장이면 None 을 돌려준다 — 부르는 쪽은 낱말 검색만으로 예전처럼 동작한다.
  한 번 실패하면 retry_after 초 동안은 다시 부르지 않는다(질문마다 연결 대기하지 않게).

시험(logs/claude-embed-20261018, 가상 기억 35·질문 45): 낱말이 거의 안 겹치는 질문 28개 중 낱말 검색 11개,
뜻 검색 상위 5개 27개. 질문 하나 약 80ms(CPU 4스레드). 쉬지 않고 색인해도 Strata 대화 속도 -8%.
"""
from __future__ import annotations

from array import array
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
from typing import Callable, Iterable
import urllib.request

# Qwen3-Embedding 은 질문 쪽에만 영어 지시문을 붙이라고 권한다(모델 카드: 없으면 검색 1~5% 하락). 기억(문서) 쪽은 그대로.
QUERY_INSTRUCT = "Instruct: Given a user's question, retrieve the user's saved personal notes that help answer it\nQuery:"
# 이보다 덜 비슷하면 '관련 있음' 으로 치지 않는다. 뜻 검색은 무관한 질문에도 가장 가까운 것을 내놓기 때문이다.
# 시험에서 정답 기억은 대부분 0.37 이상, 무관 질문의 1위는 대부분 0.30 미만이었다(둘이 겹치는 구간이 있어 완벽한 값은 없다).
MIN_SIMILARITY = 0.30
# 한 번의 질문에서 새로 임베딩하는 기억 수 상한(하나에 약 45ms). 나머지는 다음 질문 때 이어서 만든다.
MAX_NEW_PER_QUERY = 32
BATCH = 8
MAX_INPUT_CHARS = 2000


class EmbeddingError(RuntimeError):
    """임베딩 서버를 쓸 수 없다(꺼짐·시간 초과·형식 오류). 부르는 쪽은 낱말 검색으로 대신한다."""


class EmbeddingClient:
    """llama-server 의 OpenAI 호환 `/v1/embeddings`. 이 PC(loopback) 주소만 쓴다(config 가 검사)."""

    def __init__(self, host: str, timeout: float = 5.0, retry_after: float = 60.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.retry_after = retry_after
        self._clock = clock
        self._down_until = 0.0
        self._profile: str | None = None
        self._lock = threading.Lock()

    def _call(self, path: str, body: dict | None = None) -> dict:
        if self._clock() < self._down_until:
            raise EmbeddingError("임베딩 서버를 잠시 쉬는 중입니다(직전 실패).")
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.host + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                value = json.load(response)
        except (OSError, ValueError) as exc:  # 연결 거부·시간 초과·HTTP 오류·JSON 오류
            self._down_until = self._clock() + self.retry_after
            self._profile = None  # 다시 켜질 때 다른 모델일 수 있다
            raise EmbeddingError(f"임베딩 서버 응답 없음: {exc}") from exc
        if not isinstance(value, dict):
            self._down_until = self._clock() + self.retry_after
            raise EmbeddingError("임베딩 서버 응답 형식이 잘못되었습니다.")
        return value

    def profile(self) -> str:
        """지금 서버에 올라간 모델의 이름·크기·차원. 이것이 바뀌면 예전 벡터와 섞지 않는다."""
        with self._lock:
            if self._profile is None:
                models = self._call("/v1/models").get("data")
                if not isinstance(models, list) or not models or not isinstance(models[0], dict):
                    raise EmbeddingError("임베딩 서버가 모델 정보를 주지 않았습니다.")
                meta = models[0].get("meta") or {}
                # 파일 이름만 — 폴더가 다른 기기(노트북·Mac)에서도 같은 모델이면 같은 색인으로 본다
                name = str(models[0].get("id", "?")).replace("\\", "/").rsplit("/", 1)[-1]
                self._profile = f"{name}|{meta.get('n_params', '?')}|{meta.get('ftype', '?')}|{meta.get('n_embd', '?')}"
            return self._profile

    def embed(self, texts: list[str]) -> list[array]:
        if not texts:
            return []
        value = self._call("/v1/embeddings", {"input": [text[:MAX_INPUT_CHARS] for text in texts]})
        items = value.get("data")
        if not isinstance(items, list) or len(items) != len(texts):
            raise EmbeddingError("임베딩 개수가 요청과 다릅니다.")
        vectors: list[array] = []
        for item in sorted(items, key=lambda d: d.get("index", 0) if isinstance(d, dict) else 0):
            raw = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(raw, list) or not raw or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in raw):
                raise EmbeddingError("임베딩 값이 잘못되었습니다(비었거나 숫자가 아님).")
            norm = math.sqrt(sum(x * x for x in raw))
            if norm == 0:
                raise EmbeddingError("임베딩 길이가 0 입니다.")
            vectors.append(array("f", (x / norm for x in raw)))
        if len({len(v) for v in vectors}) != 1:
            raise EmbeddingError("임베딩 차원이 서로 다릅니다.")
        return vectors


def memory_text(key: str, value: str) -> str:
    return f"{key}: {value}"


def memory_hash(key: str, value: str) -> str:
    return hashlib.sha256(f"{key}\0{value}".encode("utf-8")).hexdigest()[:32]


class MemoryVectors:
    """사람별 기억의 벡터 색인. search() 가 모자란 벡터를 채우고 질문과 비슷한 기억을 돌려준다."""

    def __init__(self, db_path: Path, client: EmbeddingClient) -> None:
        self.db_path = Path(db_path)
        self.client = client
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS memory_vectors (profile TEXT NOT NULL, owner TEXT NOT NULL, "
                         "key TEXT NOT NULL, source_hash TEXT NOT NULL, vector BLOB NOT NULL, "
                         "PRIMARY KEY (profile, owner, key))")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=10)

    def _stored(self, conn: sqlite3.Connection, profile: str, owner: str) -> dict[str, tuple[str, bytes]]:
        return {key: (source_hash, blob) for key, source_hash, blob in conn.execute(
            "SELECT key, source_hash, vector FROM memory_vectors WHERE profile = ? AND owner = ?", (profile, owner))}

    def _sync(self, profile: str, owner: str, memories: dict[str, str]) -> dict[str, array]:
        """원본과 맞는 벡터만 돌려준다. 없거나 낡은 것은 (상한까지) 새로 만들고, 지워진 기억의 벡터는 치운다."""
        with closing(self._connect()) as conn:
            stored = self._stored(conn, profile, owner)
        hashes = {key: memory_hash(key, value) for key, value in memories.items()}
        missing = [key for key, digest in hashes.items() if stored.get(key, ("",))[0] != digest][:MAX_NEW_PER_QUERY]
        fresh: list[tuple[str, array]] = []
        for start in range(0, len(missing), BATCH):
            chunk = missing[start:start + BATCH]
            fresh.extend(zip(chunk, self.client.embed([memory_text(key, memories[key]) for key in chunk])))
        removed = [key for key in stored if key not in memories]
        with closing(self._connect()) as conn:
            stale_profiles = conn.execute("SELECT 1 FROM memory_vectors WHERE owner = ? AND profile != ? LIMIT 1",
                                          (owner, profile)).fetchone()
        if fresh or removed or stale_profiles:
            with closing(self._connect()) as conn, conn:
                # 모델이 바뀌면 예전 모델의 벡터는 쓸 수 없다(다른 공간) — 치운다
                conn.execute("DELETE FROM memory_vectors WHERE owner = ? AND profile != ?", (owner, profile))
                conn.executemany("INSERT OR REPLACE INTO memory_vectors(profile, owner, key, source_hash, vector) VALUES (?, ?, ?, ?, ?)",
                                 [(profile, owner, key, hashes[key], vector.tobytes()) for key, vector in fresh])
                conn.executemany("DELETE FROM memory_vectors WHERE profile = ? AND owner = ? AND key = ?",
                                 [(profile, owner, key) for key in removed])
        vectors = {key: vector for key, vector in fresh}
        for key, (digest, blob) in stored.items():
            if key not in vectors and hashes.get(key) == digest:
                vector = array("f")
                vector.frombytes(blob)
                vectors[key] = vector
        return vectors

    def search(self, owner: str, query: str, memories: dict[str, str], limit: int = 40) -> list[tuple[float, str]] | None:
        """(비슷한 정도, 기억 이름) 를 비슷한 순으로. MIN_SIMILARITY 미만은 뺀다. 서버를 못 쓰면 None."""
        if not query.strip():
            return []
        try:
            profile = self.client.profile()
            vectors = self._sync(profile, owner, memories)
            if not vectors:
                return []
            question = self.client.embed([QUERY_INSTRUCT + query])[0]
        except (EmbeddingError, sqlite3.Error):
            return None
        scored = []
        for key, vector in vectors.items():
            if len(vector) != len(question):
                continue
            similarity = sum(a * b for a, b in zip(question, vector))
            if similarity >= MIN_SIMILARITY:
                scored.append((similarity, key))
        scored.sort(reverse=True)
        return scored[:limit]


def reciprocal_rank_fusion(rankings: Iterable[list[str]], k: int = 60) -> list[str]:
    """여러 순위를 합친다: 점수 = Σ 1/(k + 순위). 낱말 점수와 유사도는 단위가 달라 직접 더하지 않는다(RRF, Cormack 2009)."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda key: -scores[key])
