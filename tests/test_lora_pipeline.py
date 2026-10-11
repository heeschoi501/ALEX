"""LoRA 학습 파이프라인(ADR-084) — 실제 학습 없이 지키는 약속들.

1. 학습 데이터는 승인된 train 만, 평가는 승인된 eval 만 — 둘은 섞이지 않는다(ADR-037 결정 6).
2. 데이터가 부족하면 시작하지 않는다(--allow-small 은 연결 확인용).
3. GPU 작업은 동의(--yes)·빈 그래픽카드·Ollama 모델 없음을 모두 확인한 뒤에만 시작한다. CPU 학습은 GPU 를 아예 숨긴다.
4. 학습 기록(training_runs)은 학습이 실제로 끝났을 때만 남는다.
5. Ollama 에는 합친 GGUF 로 넣는다(0.34.1 부터 ADAPTER 없음). 내보내기·평가가 실패해도 학습 결과는 남는다.
6. ALEX 설정은 바꾸지 않는다 — 채택은 사용자가 한다.

학습 일꾼·GGUF 변환기·ollama 는 가짜 스크립트로 바꿔 끼운다. torch 없이 돈다.
"""
import argparse
from contextlib import redirect_stdout
import io
import itertools
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import lora  # noqa: E402
from lora_worker import build_example, lr_at  # noqa: E402
from jarvis.storage import Storage  # noqa: E402

FAKE_WORKER = r'''
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
cmd = args[0]
def opt(name):
    return args[args.index(name) + 1]
if os.environ.get("FAKE_RECORD"):
    with open(os.environ["FAKE_RECORD"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"cmd": cmd, "args": args, "cuda": os.environ.get("CUDA_VISIBLE_DEVICES"),
                             "hf_home": os.environ.get("HF_HOME"), "offline": os.environ.get("HF_HUB_OFFLINE")}) + "\n")
mode = os.environ.get("FAKE_MODE", "")
if cmd == "train":
    if mode == "fail":
        sys.exit(1)
    if mode == "sleep":
        time.sleep(30)
    out = Path(opt("--out"))
    (out / "adapter").mkdir(parents=True)
    (out / "result.json").write_text(json.dumps({"device": opt("--device"), "steps": 3, "seconds": 1.0,
                                                 "eval_loss_before": 2.5, "eval_loss_after": 1.5}), encoding="utf-8")
elif cmd == "merge":
    out = Path(opt("--out"))
    out.mkdir(parents=True)
    (out / "model.safetensors").write_text("x", encoding="utf-8")
elif cmd == "fetch":
    snapshot = Path(os.environ["FAKE_SNAPSHOT"])
    snapshot.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"path": str(snapshot)}))
elif cmd == "sample":
    prompts = json.loads(Path(opt("--prompts")).read_text(encoding="utf-8"))
    tag = "학습" if "--adapter" in args else "바탕"
    Path(opt("--out")).write_text(json.dumps([{"prompt": p, "answer": tag + " 답입니다"} for p in prompts],
                                             ensure_ascii=False), encoding="utf-8")
'''
FAKE_CONVERTER = r'''
import os, sys
from pathlib import Path
args = sys.argv[1:]
if os.environ.get("FAKE_CONVERT_FAIL"):
    sys.exit(1)
Path(args[args.index("--outfile") + 1]).write_bytes(b"GGUF")
'''
FAKE_OLLAMA = r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
modelfile = Path(args[args.index("-f") + 1]).read_text(encoding="utf-8")
with open(os.environ["FAKE_OLLAMA_RECORD"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"args": args, "modelfile": modelfile, "host": os.environ.get("OLLAMA_HOST")}, ensure_ascii=False) + "\n")
'''


def namespace(**values):
    defaults = dict(run_id="t1", base=lora.DEFAULT_BASE, min_train=lora.MIN_TRAIN, allow_small=False, device="cpu",
                    yes=False, epochs=1, max_steps=0, lr=2e-4, rank=16, alpha=16, grad_accum=4, max_len=512,
                    vram_limit_gb=11.0, timeout=60.0, record=True, host="http://127.0.0.1:1", outtype="q8_0",
                    keep_merged=False, engine="hf", cpu=False, max_new=32)
    defaults.update(values)
    return argparse.Namespace(**defaults)


def read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.store = Storage(self.dir / "t.db")
        self.runs = self.dir / "runs"
        self.home = self.dir / "home"
        (self.home / "llama.cpp").mkdir(parents=True)
        (self.home / "llama.cpp" / "convert_hf_to_gguf.py").write_text(FAKE_CONVERTER, encoding="utf-8")
        self.worker = self.dir / "fake_worker.py"
        self.worker.write_text(FAKE_WORKER, encoding="utf-8")
        self.fake_ollama = self.dir / "fake_ollama.py"
        self.fake_ollama.write_text(FAKE_OLLAMA, encoding="utf-8")
        self.record = self.dir / "worker-calls.jsonl"
        self.ollama_record = self.dir / "ollama-calls.jsonl"
        env = patch.dict(os.environ, {"JARVIS_LORA_HOME": str(self.home), "FAKE_RECORD": str(self.record),
                                      "FAKE_OLLAMA_RECORD": str(self.ollama_record),
                                      "FAKE_SNAPSHOT": str(self.dir / "snapshot")})
        env.start()
        self.addCleanup(env.stop)
        # 실제 분할은 ID(내용+시각) 해시라 실행마다 다르다 — 시험에서는 네 번째마다 eval 로 고정한다.
        order = itertools.count()
        split = patch.object(Storage, "_split_for", classmethod(lambda cls, _id: "eval" if next(order) % 4 == 3 else "train"))
        split.start()
        self.addCleanup(split.stop)
        quiet = redirect_stdout(io.StringIO())  # 안내 문구는 화면 대신 버린다
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def approve(self, n: int, stage: str = "approved") -> list[str]:
        ids = []
        for i in range(n):
            experience_id = self.store.record_experience("answer", f"{stage} 질문 {i}", f"좋은 답 {i}", verdict="good",
                                                         source=f"test:{stage}:{i}")
            if stage != "raw":
                self.store.review_experience(experience_id, stage)
            ids.append(experience_id)
        return ids

    def prepared(self, n: int = 12) -> Path:
        self.approve(n)
        self.assertEqual(lora.cmd_prepare(self.store, self.runs, namespace(allow_small=True)), 0)
        return self.runs / "t1"

    def train(self, **values) -> int:
        return lora.cmd_train(self.store, self.runs, namespace(**values), python=Path(sys.executable), worker=self.worker,
                              memory=lambda: (500, 16303), loaded=lambda host: None)


class PrepareTests(Base):
    def test_too_few_approved_examples_do_not_start(self):
        self.approve(3)
        self.assertEqual(lora.cmd_prepare(self.store, self.runs, namespace()), 1)
        self.assertFalse((self.runs / "t1").exists())

    def test_dataset_is_approved_train_only_and_eval_never_mixes_in(self):
        self.approve(30)
        raw = self.approve(5, stage="raw")
        self.assertEqual(lora.cmd_prepare(self.store, self.runs, namespace(allow_small=True)), 0)
        path = self.runs / "t1"
        train_ids = [row["meta"]["id"] for row in read_lines(path / "dataset" / "train.jsonl")]
        eval_ids = [row["meta"]["id"] for row in read_lines(path / "dataset" / "eval.jsonl")]
        self.assertEqual(sorted(train_ids), sorted(e.id for e in self.store.training_pool()))
        self.assertEqual(sorted(eval_ids), sorted(e.id for e in self.store.list_experiences(stage="approved", split="eval")))
        self.assertTrue(train_ids and eval_ids)
        self.assertFalse(set(train_ids) & set(eval_ids))
        self.assertFalse(set(raw) & (set(train_ids) | set(eval_ids)))
        row = read_lines(path / "dataset" / "train.jsonl")[0]
        self.assertEqual([m["role"] for m in row["messages"]], ["user", "assistant"])
        run = lora.load_run(path)
        self.assertEqual((run["state"], run["dataset"]["small"]), ("prepared", True))

    def test_corrections_and_lessons_are_not_trained_as_answers(self):
        good = self.approve(12)
        correction = self.store.record_experience("answer", "사용자가 물었다: 게임도 할 수 있을까?", "ALEX 는 이렇게 답했다: …",
                                                  verdict="bad", correction="사용자가 바로잡았다: 아니야", source="test:c")
        lesson = self.store.record_experience("identity", "think 를 켤지 판단해야 했다", "한 번에 한 변수만 바꿔라", source="test:l")
        for experience_id in (correction, lesson):
            self.store.review_experience(experience_id, "approved")
        self.assertEqual(lora.cmd_prepare(self.store, self.runs, namespace(allow_small=True)), 0)
        path = self.runs / "t1"
        ids = {row["meta"]["id"] for name in ("train.jsonl", "eval.jsonl") for row in read_lines(path / "dataset" / name)}
        self.assertEqual(ids, set(good))
        self.assertEqual(lora.load_run(path)["dataset"]["skipped_not_answers"], 2)
        self.assertTrue(all(row["messages"][-1]["content"].startswith("좋은 답")
                            for row in read_lines(path / "dataset" / "train.jsonl")))

    def test_nothing_to_train_stops_even_with_allow_small(self):
        experience_id = self.store.record_experience("answer", "사용자가 물었다: 질문", "ALEX 는 이렇게 답했다: 답",
                                                     verdict="bad", correction="사용자가 바로잡았다: 틀림", source="test:c")
        self.store.review_experience(experience_id, "approved")
        self.assertEqual(lora.cmd_prepare(self.store, self.runs, namespace(allow_small=True)), 1)
        self.assertFalse((self.runs / "t1").exists())

    def test_bad_run_id_unknown_base_and_reused_id_are_refused(self):
        self.approve(3)
        self.assertEqual(lora.cmd_prepare(self.store, self.runs, namespace(run_id="Bad ID", allow_small=True)), 2)
        self.assertEqual(lora.cmd_prepare(self.store, self.runs, namespace(base="meta/unknown", allow_small=True)), 2)
        self.assertEqual(lora.cmd_prepare(self.store, self.runs, namespace(allow_small=True)), 0)
        self.assertEqual(lora.cmd_prepare(self.store, self.runs, namespace(allow_small=True)), 2)


class GpuGuardTests(Base):
    def test_gpu_work_needs_consent_an_idle_card_and_no_loaded_model(self):
        problems = lora.gpu_problems("h", False, memory=lambda: (15800, 16303), loaded=lambda host: ["qwen3.8:27b-q3"])
        self.assertEqual(len(problems), 3)
        self.assertEqual(lora.gpu_problems("h", True, memory=lambda: (600, 16303), loaded=lambda host: None), [])
        self.assertEqual(len(lora.gpu_problems("h", True, memory=lambda: None, loaded=lambda host: [])), 1)

    def test_gpu_training_stops_before_the_worker_starts(self):
        self.prepared()
        code = lora.cmd_train(self.store, self.runs, namespace(device="cuda", yes=True), python=Path(sys.executable),
                              worker=self.worker, memory=lambda: (9000, 16303), loaded=lambda host: None)
        self.assertEqual(code, 1)
        self.assertFalse(self.record.exists())
        self.assertEqual(lora.load_run(self.runs / "t1")["state"], "prepared")

    def test_cpu_training_hides_the_graphics_card_and_uses_the_training_cache(self):
        self.prepared()
        self.assertEqual(self.train(), 0)
        fetch, call = read_lines(self.record)
        self.assertEqual((call["cmd"], call["cuda"]), ("train", ""))
        self.assertIn("--device", call["args"])
        self.assertEqual(Path(call["hf_home"]), self.home / "hf-cache")
        # 바탕 모델은 학습 전에 한 번 확인(온라인)하고, 학습 자체는 오프라인 — 몰래 새 판을 받지 않는다
        self.assertEqual((fetch["cmd"], fetch["offline"], call["offline"]), ("fetch", None, "1"))
        self.assertEqual(lora.load_run(self.runs / "t1")["base_snapshot"], "snapshot")


class TrainTests(Base):
    def test_finished_training_is_recorded_with_its_examples(self):
        path = self.prepared()
        self.assertEqual(self.train(), 0)
        run = lora.load_run(path)
        self.assertEqual(run["state"], "trained")
        self.assertEqual(run["train"]["eval_loss_after"], 1.5)
        self.assertEqual(self.store.experiences_in_run("t1"), sorted(run["dataset"]["train_ids"]))
        self.assertEqual(self.store.lora_trained_run_count(), 1)  # /growth 가 '실제 LoRA 학습' 으로 센다

    def test_no_record_leaves_training_runs_untouched(self):
        self.prepared()
        self.assertEqual(self.train(record=False), 0)
        self.assertEqual(self.store.training_run_count(), 0)

    def test_failed_or_timed_out_training_records_nothing(self):
        path = self.prepared()
        with patch.dict(os.environ, {"FAKE_MODE": "fail"}):
            self.assertEqual(self.train(), 1)
        self.assertEqual(lora.load_run(path)["state"], "failed")
        with patch.dict(os.environ, {"FAKE_MODE": "sleep"}):
            self.assertEqual(self.train(timeout=1.0), 1)
        self.assertIn("시간 초과", (path / "train.log").read_text(encoding="utf-8"))
        self.assertEqual(self.store.training_run_count(), 0)


class ExportTests(Base):
    def export(self, **values) -> int:
        return lora.cmd_export(self.store, self.runs, namespace(**values), python=Path(sys.executable), worker=self.worker,
                               ollama=[sys.executable, str(self.fake_ollama)])

    def test_modelfile_points_at_the_gguf_with_the_chat_template_and_sampling(self):
        text = lora.modelfile_text(Path(r"C:\ALEX\jarvis\data\lora\t1\model-q8_0.gguf"), lora.DEFAULT_BASE)
        self.assertTrue(text.startswith("FROM C:\\ALEX\\jarvis\\data\\lora\\t1\\model-q8_0.gguf\n"))
        self.assertIn("<|im_start|>assistant", text)
        self.assertIn('PARAMETER stop "<|im_end|>"', text)
        self.assertIn("PARAMETER top_p 0.8", text)
        self.assertNotIn("ADAPTER", text)  # Ollama 0.34.1+ 는 ADAPTER 를 거부한다

    def test_export_merges_converts_registers_and_builds_the_base_only_once(self):
        path = self.prepared()
        self.assertEqual(self.train(), 0)
        self.assertEqual(self.export(), 0)
        run = lora.load_run(path)
        self.assertEqual(run["state"], "exported")
        self.assertEqual(run["export"]["ollama_model"], "alex-lora-t1")
        self.assertEqual(run["export"]["base_ollama_model"], "alex-lora-base-qwen3-4b-instruct-2507")
        self.assertFalse((path / "merged").exists())  # 16비트 합친 모델은 GGUF 를 만든 뒤 지운다
        created = [call["args"][1] for call in read_lines(self.ollama_record)]
        self.assertEqual(created, ["alex-lora-t1", "alex-lora-base-qwen3-4b-instruct-2507"])
        self.assertIn("model-q8_0.gguf", read_lines(self.ollama_record)[0]["modelfile"].splitlines()[0])
        merge = [c for c in read_lines(self.record) if c["cmd"] == "merge"][0]
        self.assertEqual(merge["cuda"], "")  # 합치기는 GPU 를 쓰지 않는다
        self.assertEqual(self.export(), 0)
        self.assertEqual(len(read_lines(self.ollama_record)), 3)  # 두 번째는 학습 모델만 다시 등록

    def test_failed_conversion_keeps_the_trained_adapter_usable(self):
        path = self.prepared()
        self.assertEqual(self.train(), 0)
        with patch.dict(os.environ, {"FAKE_CONVERT_FAIL": "1"}):
            self.assertEqual(self.export(), 1)
        run = lora.load_run(path)
        self.assertEqual(run["state"], "trained")
        self.assertIn("GGUF 변환", run["history"][-1]["note"])
        self.assertFalse(self.ollama_record.exists())
        self.assertEqual(self.export(), 0)


class EvalTests(Base):
    def exported(self) -> Path:
        path = self.prepared(30)
        self.assertEqual(self.train(), 0)
        run = lora.load_run(path)
        run["export"] = {"ollama_model": "alex-lora-t1", "base_ollama_model": "alex-lora-base-x"}
        lora.mark(run, "exported")
        lora.save_run(path, run)
        return path

    def test_cpu_eval_writes_a_side_by_side_report_without_changing_settings(self):
        path = self.exported()
        calls = []

        def chat(host, model, prompt, options):
            calls.append((model, options))
            return "" if model == "alex-lora-base-x" and prompt == lora.SANITY_PROMPTS[0] else f"{model} 의 한국어 답"

        code = lora.cmd_eval(self.store, self.runs, namespace(engine="ollama", cpu=True), chat=chat)
        self.assertEqual(code, 0)
        report = (path / "eval-report.md").read_text(encoding="utf-8")
        self.assertIn("기준 답(승인된 경험)", report)
        self.assertIn("alex-lora-t1 의 한국어 답", report)
        self.assertIn("⚠ 빈 답", report)
        self.assertIn("채택은 사용자가 결정합니다", report)
        self.assertTrue(all(options["num_gpu"] == 0 and options["temperature"] == 0 for _, options in calls))
        run = lora.load_run(path)
        self.assertEqual((run["state"], run["eval"]["flagged"]), ("evaluated", {"base": 1, "tuned": 0}))
        self.assertIn("정체성 채점", report)
        self.assertEqual(run["eval"]["identity"], {"base": 0, "tuned": 0, "cases": 7})

    def test_identity_cases_are_scored_for_both_models(self):
        self.exported()
        from grade_identity import CASES

        def chat(host, model, prompt, options):
            # 학습 모델만 시리얼 번호 질문에 '알 수 없' 다고 답한다
            return "그 정보는 알 수 없습니다." if model == "alex-lora-t1" and prompt == CASES[0]["prompt"] else "네 그렇습니다"

        self.assertEqual(lora.cmd_eval(self.store, self.runs, namespace(engine="ollama", cpu=True), chat=chat), 0)
        identity = lora.load_run(self.runs / "t1")["eval"]["identity"]
        self.assertEqual(identity["tuned"] - identity["base"], 1)

    def test_gpu_eval_needs_consent(self):
        self.exported()
        calls = []
        code = lora.cmd_eval(self.store, self.runs, namespace(engine="ollama"), chat=lambda *a: calls.append(a) or "",
                             memory=lambda: (500, 16303), loaded=lambda host: None)
        self.assertEqual((code, calls), (1, []))

    def test_hf_eval_runs_both_models_on_the_cpu_worker(self):
        path = self.exported()
        code = lora.cmd_eval(self.store, self.runs, namespace(engine="hf"), python=Path(sys.executable), worker=self.worker)
        self.assertEqual(code, 0)
        samples = [c for c in read_lines(self.record) if c["cmd"] == "sample"]
        self.assertEqual([("--adapter" in c["args"], c["cuda"]) for c in samples], [(False, ""), (True, "")])
        self.assertIn("학습 답입니다", (path / "eval-report.md").read_text(encoding="utf-8"))

    def test_answer_flags(self):
        self.assertEqual(lora.answer_flags(""), ["빈 답"])
        self.assertIn("틀 찌꺼기", lora.answer_flags("답<|im_end|>"))
        self.assertEqual(lora.answer_flags("This answer is in English only."), ["한국어 아님"])
        self.assertEqual(lora.answer_flags("정렬은 이렇게 합니다.\n```python\nsorted(d.items(), key=lambda kv: kv[1])\n```"), [])


class DependencyTests(unittest.TestCase):
    def test_only_the_worker_may_use_the_training_packages(self):
        import dependency_audit

        self.assertEqual(dependency_audit.audit(), [])
        # 학습 패키지는 학습 일꾼에만. 2026-10-11 문서 지식 기능의 PDF 일꾼(pypdf, 별도 venv)도 별도 환경이지만 학습 패키지는 없다.
        training = {"torch", "transformers", "peft", "huggingface_hub"}
        self.assertEqual({path for path, roots in dependency_audit.SEPARATE_ENV.items() if roots & training},
                         {"scripts/lora_worker.py"})
        self.assertEqual(set(dependency_audit.SEPARATE_ENV), {"scripts/lora_worker.py", "scripts/pdf_extract_worker.py"})
        # ALEX 쪽 지휘 스크립트는 표준 라이브러리만 — 앱 환경에 torch 를 들이지 않는다
        self.assertFalse({"torch", "transformers", "peft", "huggingface_hub"}
                         & dependency_audit.imported_roots(ROOT / "scripts" / "lora.py"))


class FakeTokenizer:
    """채팅 틀: <u>질문</u><a>답</a>. 토큰은 글자 하나씩."""

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=False):
        text = "".join(f"<{m['role'][0]}>{m['content']}</{m['role'][0]}>" for m in messages)
        return text + ("<a>" if add_generation_prompt else "")

    def __call__(self, text, add_special_tokens=False):
        return {"input_ids": [ord(c) for c in text]}


class WorkerHelperTests(unittest.TestCase):
    def test_loss_covers_only_the_answer(self):
        example = build_example(FakeTokenizer(), [{"role": "user", "content": "질문"},
                                                  {"role": "assistant", "content": "답"}], 100)
        prompt = "<u>질문</u><a>"
        self.assertEqual(example["labels"][:len(prompt)], [-100] * len(prompt))
        self.assertEqual("".join(chr(i) for i in example["labels"][len(prompt):]), "답</a>")
        self.assertEqual(len(example["input_ids"]), len(example["labels"]))

    def test_an_answer_cut_off_by_max_len_is_skipped_and_bad_shapes_are_refused(self):
        messages = [{"role": "user", "content": "아주 긴 질문"}, {"role": "assistant", "content": "답"}]
        self.assertIsNone(build_example(FakeTokenizer(), messages, 5))
        with self.assertRaises(ValueError):
            build_example(FakeTokenizer(), messages[:1], 100)

        class Mismatch(FakeTokenizer):
            def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=False):
                return "다른 앞부분" if add_generation_prompt else super().apply_chat_template(messages)

        with self.assertRaises(ValueError):
            build_example(Mismatch(), messages, 100)

    def test_learning_rate_warms_up_then_decays(self):
        rates = [lr_at(step, 10, 1.0, 2) for step in range(10)]
        self.assertEqual(rates[:2], [0.5, 1.0])
        self.assertTrue(all(a >= b for a, b in zip(rates[1:], rates[2:])))
        self.assertGreater(rates[-1], 0)
        self.assertEqual(lr_at(0, 0, 1.0, 1), 0.0)


if __name__ == "__main__":
    unittest.main()
