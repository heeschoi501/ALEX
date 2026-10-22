"""기억을 뜻으로 찾기(ADR-094, 2026-10-18 사용자 "무엇을 이란 게 단어를 정확하게 알아야 하는 거잖아?").

낱말이 겹치지 않아도 비슷한 뜻의 기억을 찾는다. 임베딩 서버가 없거나 고장이면 예전처럼 낱말로만 찾는다.
여기서는 진짜 모델 대신 '뜻 사전' 으로 벡터를 만드는 가짜 서버·클라이언트를 쓴다."""
from array import array
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from jarvis.app import build_memory_vectors
from jarvis.config import ConfigError, Settings, validate
from jarvis.embeddings import (MIN_SIMILARITY, QUERY_INSTRUCT, EmbeddingClient, EmbeddingError, MemoryVectors,
                               reciprocal_rank_fusion)
from jarvis.launcher import embedding_command
from tests.test_memory_search import make_core

# 낱말 → 뜻 번호. 같은 번호면 같은 뜻으로 친다(진짜 모델이 '차'·'자동차' 를 가깝게 두는 것을 흉내).
MEANINGS = {"자동차": 0, "차": 0, "정비": 1, "점검": 1, "고양이": 2, "애완동물": 2, "반려묘": 2, "사료": 3, "밥": 3}


def fake_vector(text: str) -> list[float]:
    query = text.startswith(QUERY_INSTRUCT)
    text = text.replace(QUERY_INSTRUCT, "")
    vector = [0.0] * 8
    for word, meaning in MEANINGS.items():
        if word in text:
            vector[meaning] += 1.0
    vector[7 if query else 6] = 0.05  # 길이 0 을 피한다. 질문·기억이 다른 칸이라 '뜻 없음' 끼리는 비슷하지 않다
    return vector


class FakeClient:
    def __init__(self):
        self.calls = []
        self.down = False

    def profile(self):
        if self.down:
            raise EmbeddingError("down")
        return "fake|8"

    def embed(self, texts):
        if self.down:
            raise EmbeddingError("down")
        self.calls.append(list(texts))
        out = []
        for text in texts:
            raw = fake_vector(text)
            norm = math.sqrt(sum(x * x for x in raw))
            out.append(array("f", (x / norm for x in raw)))
        return out


class SemanticMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.core = make_core(self.root)
        self.client = FakeClient()
        self.core.memory_vectors = MemoryVectors(self.root / "embeddings.db", self.client)
        self.storage = self.core.storage

    def tearDown(self):
        self.temp.cleanup()

    def test_a_memory_is_found_by_meaning_without_shared_words(self):
        self.storage.remember("탈것", "2019년식 아반떼, 다음 점검 11월")
        for i in range(8):
            self.storage.remember(f"메모{i}", f"관계없는 기록 {i}")
        self.core.memory_context_limit = 1
        # '차 정비' 와 '탈것/점검' 은 두 글자 조각도 겹치지 않는다 — 낱말 검색만으로는 최근 기억(메모7)이 나온다.
        self.core.memory_vectors = None
        self.assertEqual(list(self.core._select_memories("내 차 정비 언제야?")), ["메모7"])
        self.core.memory_vectors = MemoryVectors(self.root / "embeddings.db", self.client)
        self.assertEqual(list(self.core._select_memories("내 차 정비 언제야?")), ["탈것"])

    def test_word_matches_still_count_and_both_lists_merge(self):
        self.storage.remember("반려묘", "두부, 5살")
        self.storage.remember("사료", "로얄캐닌 인도어")
        self.storage.remember("운동", "수영")
        chosen = list(self.core._select_memories("애완동물 밥 뭐 먹이지?"))
        self.assertEqual(set(chosen[:2]), {"반려묘", "사료"})

    def test_server_down_falls_back_to_word_search(self):
        self.storage.remember("자동차", "아반떼, 점검 11월")
        self.storage.remember("운동", "수영")
        self.client.down = True
        self.assertIn("자동차", self.core._select_memories("자동차 점검 언제?"))
        self.assertEqual(len(self.core._select_memories("완전히 다른 질문입니다")), 2)  # 예전처럼 최근 기억

    def test_unrelated_question_keeps_the_old_recent_behaviour(self):
        self.storage.remember("자동차", "아반떼")
        self.storage.remember("취미", "등산")
        self.assertEqual(len(self.core._select_memories("로마 제국은 언제 멸망했어?")), 2)

    def test_vectors_follow_updates_and_deletions(self):
        vectors = self.core.memory_vectors
        self.storage.remember("탈것", "아반떼 점검")
        self.assertEqual([k for _, k in vectors.search("owner", "차 정비", self.storage.list_memories())], ["탈것"])
        self.storage.remember("탈것", "자전거")  # 뜻이 바뀌면 다시 만든다
        self.assertEqual(vectors.search("owner", "차 정비", self.storage.list_memories()), [])
        self.storage.forget("탈것")
        self.assertEqual(vectors.search("owner", "차 정비", self.storage.list_memories()), [])
        self.assertEqual(self._rows(), [])  # 지워진 기억의 벡터도 치운다

    def test_unchanged_memories_are_not_embedded_again(self):
        self.storage.remember("탈것", "아반떼 점검")
        self.core._select_memories("차 정비")
        self.core._select_memories("차 정비 언제")
        documents = [t for call in self.client.calls for t in call if not t.startswith(QUERY_INSTRUCT)]
        self.assertEqual(documents, ["탈것: 아반떼 점검"])

    def test_a_new_model_rebuilds_and_drops_old_vectors(self):
        self.storage.remember("탈것", "아반떼 점검")
        self.core._select_memories("차 정비")
        self.client.profile = lambda: "another|8"
        self.assertIn("탈것", self.core._select_memories("차 정비"))
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self.root / "embeddings.db")) as conn:
            self.assertEqual(conn.execute("SELECT profile FROM memory_vectors").fetchall(), [("another|8",)])

    def test_other_peoples_memories_never_come_back(self):
        self.storage.remember("탈것", "아반떼 점검", owner="someone")
        self.storage.remember("취미", "등산")
        self.assertNotIn("탈것", self.core._select_memories("차 정비 언제야?"))
        self.assertIn("탈것", self.core._select_memories("차 정비 언제야?", owner="someone"))

    def _rows(self):
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self.root / "embeddings.db")) as conn:
            return conn.execute("SELECT owner, key FROM memory_vectors").fetchall()


class RankFusionTests(unittest.TestCase):
    def test_items_in_both_lists_come_first(self):
        self.assertEqual(reciprocal_rank_fusion([["a", "b"], ["c", "b"]])[0], "b")

    def test_threshold_is_a_sane_cosine(self):
        self.assertTrue(0 < MIN_SIMILARITY < 1)


class Reply(BaseHTTPRequestHandler):
    body: dict = {}

    def do_GET(self):  # /v1/models
        self._send({"data": [{"id": "C:\\ALEX\\tools\\embed\\models\\m.gguf", "meta": {"n_params": 1, "ftype": "Q8_0", "n_embd": 3}}]})

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self._send(type(self).body)

    def _send(self, value):
        data = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class EmbeddingClientTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Reply)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client = EmbeddingClient(f"http://127.0.0.1:{self.server.server_port}")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_vectors_are_normalised_and_ordered(self):
        Reply.body = {"data": [{"index": 1, "embedding": [0, 0, 2]}, {"index": 0, "embedding": [3, 4, 0]}]}
        first, second = self.client.embed(["a", "b"])
        self.assertAlmostEqual(first[0], 0.6, places=5)
        self.assertAlmostEqual(second[2], 1.0, places=5)
        self.assertEqual(self.client.profile(), "m.gguf|1|Q8_0|3")

    def test_bad_answers_are_refused(self):
        for body in ({"data": [{"index": 0, "embedding": [1, 0]}]},                       # 개수 다름
                     {"data": [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": [1, 0, 0]}]},  # 차원 다름
                     {"data": [{"index": 0, "embedding": [float("nan"), 1]}, {"index": 1, "embedding": [1, 1]}]},
                     {"data": [{"index": 0, "embedding": [0, 0]}, {"index": 1, "embedding": [1, 1]}]}):
            Reply.body = body
            with self.subTest(body=str(body)[:60]), self.assertRaises(EmbeddingError):
                self.client.embed(["a", "b"])

    def test_after_a_failure_it_waits_before_trying_again(self):
        now = [0.0]
        client = EmbeddingClient("http://127.0.0.1:9", timeout=0.5, retry_after=60, clock=lambda: now[0])
        with self.assertRaises(EmbeddingError):
            client.embed(["a"])
        with mock.patch("urllib.request.urlopen") as opened, self.assertRaises(EmbeddingError):
            client.embed(["a"])
        opened.assert_not_called()
        now[0] = 61
        with mock.patch("urllib.request.urlopen", side_effect=OSError("still down")) as opened, self.assertRaises(EmbeddingError):
            client.embed(["a"])
        opened.assert_called_once()


class SettingsAndLauncherTests(unittest.TestCase):
    def test_embedding_host_must_be_this_pc(self):
        base = Settings(file_search_root=Path.cwd())
        validate(replace(base, embedding_host="http://127.0.0.1:8081"))
        with self.assertRaises(ConfigError):
            validate(replace(base, embedding_host="http://192.168.0.5:8081"))
        with self.assertRaises(ConfigError):
            validate(replace(base, embedding_threads=0))

    def test_off_by_default(self):
        self.assertFalse(Settings().embedding_enabled)
        with mock.patch.dict(os.environ, {"JARVIS_EMBEDDING_ENABLED": "0"}):
            self.assertIsNone(build_memory_vectors(Settings()))

    def test_server_runs_on_cpu_only(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "bin").mkdir()
            (folder / "bin" / "llama-server.exe").write_bytes(b"")
            (folder / "m.gguf").write_bytes(b"")
            (folder / "embed.json").write_text(json.dumps({"server": "bin/llama-server.exe", "model": "m.gguf"}), encoding="utf-8")
            command = embedding_command(folder, 8081, 4)
            self.assertIn("--embedding", command)
            self.assertEqual(command[command.index("--device") + 1], "none")
            self.assertEqual(command[command.index("-ngl") + 1], "0")
            self.assertEqual(command[command.index("--pooling") + 1], "last")
            self.assertEqual(command[command.index("--host") + 1], "127.0.0.1")
            (folder / "m.gguf").unlink()
            with self.assertRaises(RuntimeError):
                embedding_command(folder, 8081, 4)


if __name__ == "__main__":
    unittest.main()
