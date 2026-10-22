"""ALEX 코딩 하네스(ADR-112, 2026-10-22) — OpenCode v1.18.34 방식을 ALEX 코드로. 고치기 9단계는 OpenCode 의 시험 사례
(test/tool/edit.test.ts)를 옮겨 같은 결과인지 보고, 도구 입출력 모양·반복(되풀이 막기·단계 상한·압축·생각 끄고 다시)은
각본대로 움직이는 가짜 모델로 본다."""
import json
from pathlib import Path
import tempfile
import unittest

from jarvis.harness import edit as editing
from jarvis.harness.agent import CLEARED, CodingAgent
from jarvis.harness.prompt import MAX_STEPS_PROMPT, system_prompt, tools as tool_specs
from jarvis.harness.tools import ShellPolicy, Workspace
from jarvis.harness.worker import HarnessWorker
from jarvis.providers import ContextExhausted


class ReplaceTests(unittest.TestCase):
    """OpenCode edit.test.ts 의 사례와 replacer 별 동작."""

    def test_basic_cases(self):
        self.assertEqual(editing.replace("line1\nline2\nline3", "line2", "new line 2\nextra line"),
                         "line1\nnew line 2\nextra line\nline3")
        self.assertEqual(editing.replace("foo bar foo baz foo", "foo", "qux", replace_all=True), "qux bar qux baz qux")
        for old, new, why in (("same", "same", "identical"), ("", "x", "cannot be empty"), ("nope", "x", "Could not find")):
            with self.subTest(why=why), self.assertRaisesRegex(editing.EditError, why):
                editing.replace("same text", old, new)
        with self.assertRaisesRegex(editing.EditError, "multiple matches"):
            editing.replace("a = 1\nb = 2\na = 1\n", "a = 1", "a = 3")

    def test_loose_block_anchor_is_refused(self):
        original = "\n".join(["function configure() {", "  keepImportantState()", "  removeAllUserData()",
                              "  archiveBackups()", "  auditLog()", "}"])
        with self.assertRaisesRegex(editing.EditError, "Could not find"):
            editing.replace(original, "function configure() {\n  const enabled = true\n}",
                            "function configure() {\n  const enabled = false\n}")
        with self.assertRaisesRegex(editing.EditError, "Could not find"):
            editing.replace("function configure() {\n  removeAllUserData()\n}",
                            "function configure() {\n  const enabled = true\n}", "x")

    def test_fuzzy_matches_the_model_usually_needs(self):
        code = "def f(x):\n    if x:\n        return 1\n    return 2\n"
        # 들여쓰기를 틀리게 준 경우(IndentationFlexible / LineTrimmed)
        self.assertIn("return 9", editing.replace(code, "if x:\n    return 1", "if x:\n        return 9"))
        # 줄 끝 빈칸이 다른 경우(LineTrimmed)
        self.assertIn("return 7", editing.replace(code, "    return 2   ", "    return 7"))
        # 가운데 줄 하나가 조금 다른 블록(BlockAnchor — 유사도 0.65 이상)
        block = "def g():\n    total = compute(a, b)\n    log(total)\n    return total\n"
        out = editing.replace(block, "def g():\n    total = compute(a,b)\n    log(total)\n    return total",
                              "def g():\n    return 0")
        self.assertEqual(out, "def g():\n    return 0\n")
        # 이스케이프로 준 경우(EscapeNormalized)
        self.assertIn("'b'", editing.replace("x = 'a'\n", "x = \\'a\\'", "x = 'b'"))
        # 빈칸이 여러 개(WhitespaceNormalized)
        self.assertEqual(editing.replace("value  =   3\n", "value = 3", "value = 4"), "value = 4\n")

    def test_disproportionate_span_is_refused(self):
        # 앞 단계들이 이미 크기를 맞춰 거의 생기지 않는 안전장치 — 판정만 직접 본다(OpenCode isDisproportionateMatch 와 같은 기준)
        self.assertTrue(editing._disproportionate("\n".join(["x"] * 10), "a\nb\nc"))
        self.assertFalse(editing._disproportionate("a\nb\nc", "a\nb\nc"))
        self.assertTrue(editing._disproportionate("a\n" + "y" * 900, "a\nb"))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.ws = Workspace(self.root, output_dir=Path(self.temp.name) / "out")

    def put(self, name: str, text: str, newline: str = "\n") -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline=newline)
        return path


class ToolTests(Fixture):
    def test_read_format_offset_and_directory(self):
        path = self.put("a.py", "".join(f"x{n} = {n}\n" for n in range(1, 6)))
        out = self.ws.run("read", {"filePath": str(path)})
        self.assertTrue(out.startswith(f"<path>{path.resolve()}</path>\n<type>file</type>\n<content>\n1: x1 = 1"))
        self.assertIn("(End of file - total 5 lines)", out)
        out = self.ws.run("read", {"filePath": "a.py", "offset": 2, "limit": 2})
        self.assertIn("2: x2 = 2\n3: x3 = 3", out)
        self.assertIn("Use offset=4 to continue", out)
        self.put("pkg/b.py", "")
        listing = self.ws.run("read", {"filePath": str(self.root)})
        self.assertIn("<type>directory</type>", listing)
        self.assertIn("pkg/", listing)

    def test_paths_outside_and_binary_and_missing(self):
        self.assertIn("outside the workspace", self.ws.run("read", {"filePath": str(Path(self.temp.name) / "x.txt")}))
        self.assertIn("outside the workspace", self.ws.run("write", {"filePath": "../evil.py", "content": "x"}))
        (self.root / "b.bin").write_bytes(b"\x00\x01")
        self.assertIn("Cannot read binary", self.ws.run("read", {"filePath": "b.bin"}))
        self.assertIn("File not found", self.ws.run("read", {"filePath": "nope.py"}))

    def test_edit_and_write_need_a_read_first_and_keep_line_endings(self):
        path = self.put("c.py", "a = 1\r\nb = 2\r\n", newline="")
        self.assertIn("must read file", self.ws.run("edit", {"filePath": "c.py", "oldString": "a = 1", "newString": "a = 3"}))
        self.ws.run("read", {"filePath": "c.py"})
        self.assertEqual(self.ws.run("edit", {"filePath": "c.py", "oldString": "a = 1\nb = 2", "newString": "a = 3\nb = 4"}),
                         "Edit applied successfully.")
        self.assertEqual(path.read_bytes(), b"a = 3\r\nb = 4\r\n")
        path.write_text("changed\n", encoding="utf-8")
        import os, time
        os.utime(path, (time.time() + 5, time.time() + 5))
        self.assertIn("modified since it was last read", self.ws.run("write", {"filePath": "c.py", "content": "x"}))
        self.assertEqual(self.ws.run("write", {"filePath": "new/d.py", "content": "ok = True\n"}), "Wrote file successfully.")

    def test_bom_is_kept_and_syntax_errors_come_back(self):
        path = self.root / "e.py"
        path.write_bytes("\ufeffusing = 1\n".encode("utf-8"))
        self.ws.run("read", {"filePath": "e.py"})
        out = self.ws.run("edit", {"filePath": "e.py", "oldString": "using = 1", "newString": "def broken(:\n"})
        self.assertIn("LSP errors detected in this file, please fix:", out)
        self.assertIn("ERROR [1:", out)
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_glob_and_grep_formats(self):
        self.put("src/one.py", "def alpha():\n    return 1\n")
        self.put("src/two.txt", "alpha beta\n")
        self.assertIn("one.py", self.ws.run("glob", {"pattern": "**/*.py"}))
        self.assertEqual(self.ws.run("glob", {"pattern": "*.rs"}), "No files found")
        out = self.ws.run("grep", {"pattern": r"def \w+", "include": "*.py"})
        self.assertTrue(out.startswith("Found 1 matches"))
        self.assertIn("  Line 1: def alpha():", out)
        self.assertEqual(self.ws.run("grep", {"pattern": "zzz"}), "No files found")

    def test_bash_policy_exit_code_and_truncation(self):
        self.put("test_ok.py", "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertTrue(True)\n")
        out = self.ws.run("bash", {"command": "python -m unittest test_ok"})
        self.assertIn("OK", out)
        self.assertIn("(exit code 0)", out)
        self.assertIn("not allowed", self.ws.run("bash", {"command": "del /q *"}))
        self.assertIn("shell operators", self.ws.run("bash", {"command": "python -m unittest && echo hi"}))
        long = "\n".join(f"line {n}" for n in range(3000))
        cut = self.ws.truncate(long, direction="tail")
        self.assertIn("line 2999", cut)
        saved = cut.split("saved to ", 1)[1].split(". Use", 1)[0]
        self.assertIn("line 0", self.ws.run("read", {"filePath": saved, "limit": 3}))  # 잘린 전체는 읽을 수 있다

    def test_todos_and_unknown_tool(self):
        out = self.ws.run("todowrite", {"todos": [{"content": "fix bug", "status": "in_progress"}, {"content": "test", "status": "pending"}]})
        self.assertTrue(out.startswith("2 todos"))
        self.assertIn("unknown tool", self.ws.run("rm", {}))


class Script:
    """chat_step 을 각본대로 — (글, 도구 호출들) 차례로. 받은 메시지·도구를 기억한다."""

    def __init__(self, steps):
        self.steps, self.seen, self.tools_seen = list(steps), [], []
        self.think, self.model = True, "fake-coder"

    def chat_step(self, messages, tools=None, on_chunk=None):
        self.seen.append(list(messages))
        self.tools_seen.append(tools)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def call(_tool, **args):
    return {"id": f"c_{_tool}", "function": {"name": _tool, "arguments": json.dumps(args)}}


class AgentTests(Fixture):
    def test_read_edit_test_then_answer(self):
        path = self.put("calc.py", "def add(a, b):\n    return a - b\n")
        self.put("test_calc.py", "import unittest\nfrom calc import add\nclass T(unittest.TestCase):\n"
                                 "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
        model = Script([
            ("", [call("read", filePath=str(path))]),
            ("", [call("edit", filePath=str(path), oldString="return a - b", newString="return a + b")]),
            ("", [call("bash", command="python -m unittest test_calc")]),
            ("add 의 뺄셈을 덧셈으로 고쳤고 시험이 통과했습니다.", []),
        ])
        notes = []
        result = CodingAgent(model, self.ws, progress=notes.append).run("add 버그 고쳐줘")
        self.assertEqual((result.stopped, result.steps), ("done", 4))
        self.assertIn("a + b", path.read_text())
        self.assertEqual(result.tools, {"read": 1, "edit": 1, "bash": 1})
        self.assertIn("(exit code 0)", model.seen[-1][-1].content)
        self.assertTrue(any(n.startswith("[고치기") for n in notes))
        system = model.seen[0][0].content
        self.assertIn("You are ALEX", system)
        self.assertIn("Write your final message to the user in Korean", system)
        self.assertNotIn("opencode.ai", system)
        self.assertEqual([t["function"]["name"] for t in model.tools_seen[0]],
                         ["read", "edit", "write", "apply_patch", "glob", "grep", "bash", "todowrite", "task"])

    def test_tool_errors_go_back_to_the_model(self):
        model = Script([("", [call("edit", filePath="x.py", oldString="a", newString="b")]),
                        ("", [{"function": {"name": "read", "arguments": "{broken"}}]), ("끝", [])])
        result = CodingAgent(model, self.ws).run("t")
        self.assertEqual(result.stopped, "done")
        self.assertIn("Error: File", model.seen[1][-1].content)
        self.assertIn("not valid JSON", model.seen[2][-1].content)

    def test_doom_loop_warns_then_stops(self):
        same = ("", [call("grep", pattern="nothing")])
        model = Script([same] * 6)
        result = CodingAgent(model, self.ws).run("t")
        self.assertEqual(result.stopped, "doom_loop")
        self.assertTrue(any("Stop repeating" in m.content for m in model.seen[3]))

    def test_max_steps_asks_for_a_summary_without_tools(self):
        model = Script([("", [call("glob", pattern=f"*{n}")]) for n in range(2)] + [("여기까지 했습니다", [])])
        result = CodingAgent(model, self.ws, max_steps=3).run("t")
        self.assertEqual((result.stopped, result.text), ("max_steps", "여기까지 했습니다"))
        self.assertIsNone(model.tools_seen[-1])
        self.assertEqual(model.seen[-1][-1].content, MAX_STEPS_PROMPT)

    def test_long_thinking_retries_that_step_without_thinking(self):
        model = Script([ContextExhausted("생각이 길어"), ("답", [])])
        result = CodingAgent(model, self.ws).run("t")
        self.assertEqual(result.text, "답")
        self.assertTrue(model.think)  # 되돌린다

    def test_context_is_pruned_then_compacted(self):
        big = "x" * 5000
        self.put("big.txt", big)
        steps = [("", [call("read", filePath="big.txt", offset=n + 1)]) for n in range(8)]
        model = Script(steps + [("## Goal\n- 큰 파일 보기", []), ("끝", [])])
        agent = CodingAgent(model, self.ws, context_chars=12_000)
        result = agent.run("t")
        self.assertEqual(result.stopped, "done")
        self.assertEqual(result.compactions, 1)
        self.assertTrue(any(m.content == CLEARED for m in model.seen[-2]) or result.compactions == 1)
        self.assertIn("Summary of the earlier part", model.seen[-1][2].content)

    def test_rules_lessons_and_agents_md_reach_the_prompt(self):
        self.put("AGENTS.md", "Run tests with python -m unittest.")
        text = system_prompt(self.root, rules="Do not touch tests/.", lessons="- 사용자: 주석은 한국어로")
        for piece in ("Instructions from:", "Run tests with python -m unittest.", "Do not touch tests/.", "주석은 한국어로",
                      "<env>", "Working directory:"):
            self.assertIn(piece, text)
        bash = next(t for t in tool_specs(("python -m unittest *",), 120) if t["function"]["name"] == "bash")
        self.assertIn("`python -m unittest *`", bash["function"]["description"])


class WorkerTests(Fixture):
    def test_worker_works_on_a_copy_and_reports_changes(self):
        self.put("calc.py", "def add(a, b):\n    return a - b\n")
        self.put("tests/test_calc.py", "x = 1\n")
        model = Script([
            ("", [call("read", filePath="calc.py")]),
            ("", [call("edit", filePath="calc.py", oldString="a - b", newString="a + b")]),
            ("", [call("write", filePath="notes.md", content="# notes\n")]),
            ("고쳤습니다", []),
        ])
        worker = HarnessWorker(lambda: model, timeout=60)
        run = worker.run(self.root, "add 고쳐줘")
        self.assertEqual(run.changed, {"calc.py": "def add(a, b):\n    return a + b\n"})
        self.assertEqual(run.created, {"notes.md": "# notes\n"})
        self.assertIn("a - b", (self.root / "calc.py").read_text())  # 원본은 그대로
        self.assertEqual((run.exit_code, worker.label, worker.model), (0, "ALEX 하네스", "fake-coder"))
        self.assertIn("Do not modify files under tests/", model.seen[0][0].content)

    def test_unavailable_without_a_tool_model(self):
        self.assertIn("도구 호출", HarnessWorker(lambda: object()).unavailable_reason())


class PatchTests(Fixture):
    def test_add_update_move_delete(self):
        self.put("app.py", "def greet():\n    print(\"Hi\")\n")
        self.put("old.txt", "bye\n")
        out = self.ws.run("apply_patch", {"patchText": "*** Begin Patch\n*** Add File: hello.txt\n+Hello world\n"
                                          "*** Update File: app.py\n*** Move to: main.py\n@@ def greet():\n-    print(\"Hi\")\n"
                                          "+    print(\"Hello, world!\")\n*** Delete File: old.txt\n*** End Patch"})
        self.assertTrue(out.startswith("Success. Updated the following files:"))
        self.assertEqual((self.root / "hello.txt").read_text(), "Hello world\n")
        self.assertEqual((self.root / "main.py").read_text(), "def greet():\n    print(\"Hello, world!\")\n")
        self.assertFalse((self.root / "app.py").exists() or (self.root / "old.txt").exists())
        self.assertEqual(sorted(self.ws.revert(0)), ["app.py", "hello.txt", "main.py", "old.txt"])  # 되돌리기
        self.assertTrue((self.root / "app.py").is_file() and (self.root / "old.txt").is_file())
        self.assertFalse((self.root / "main.py").exists() or (self.root / "hello.txt").exists())

    def test_context_matching_passes_and_errors(self):
        from jarvis.harness import patch as patching
        lines = ["x = “quote” — dash   ", "y = 2"]
        self.assertEqual(patching.seek(lines, ['x = "quote" - dash'], 0), 0)  # 유니코드·끝 빈칸 무시
        with self.assertRaisesRegex(patching.PatchError, "missing Begin/End"):
            patching.parse("*** Update File: a.py")
        self.put("a.py", "a = 1\n")
        self.assertIn("Failed to find expected lines", self.ws.run("apply_patch", {"patchText":
            "*** Begin Patch\n*** Update File: a.py\n@@\n-b = 1\n+b = 2\n*** End Patch"}))
        out = self.ws.run("apply_patch", {"patchText": "*** Begin Patch\n*** Update File: a.py\n@@\n-a = 1\n+a = (\n*** End Patch"})
        self.assertIn("LSP errors detected", out)


class PermissionAndSnapshotTests(Fixture):
    def test_last_matching_rule_wins_and_tiers(self):
        from jarvis.harness.permission import ALLOW, ASK, DENY, Rule, evaluate, tier_rules
        rules = [Rule("*", "*", ALLOW), Rule("bash", "*", DENY), Rule("bash", "python -m unittest *", ALLOW)]
        self.assertEqual(evaluate("bash", "python -m unittest t", rules), ALLOW)
        self.assertEqual(evaluate("bash", "rm -rf x", rules), DENY)
        self.assertEqual(evaluate("unknown", "x", []), ASK)
        self.assertEqual(evaluate("edit", "a.py", tier_rules("read")), DENY)
        self.assertEqual(evaluate("edit", "a.py", tier_rules("ask")), ASK)
        self.assertEqual(evaluate("bash", "python -m unittest", tier_rules("auto")), ALLOW)
        self.assertEqual(evaluate("bash", "git status", tier_rules("auto")), ASK)

    def test_ask_goes_to_the_person(self):
        from jarvis.harness.permission import tier_rules
        asked = []
        ws = Workspace(self.root, rules=tier_rules("ask"), ask=lambda p, pat, d: asked.append((p, pat)) or pat == "yes.py")
        ws.run("read", {"filePath": "x"})  # 읽기는 그냥
        self.assertEqual(ws.run("write", {"filePath": "yes.py", "content": "ok = 1\n"}), "Wrote file successfully.")
        self.assertIn("denied by the user", ws.run("write", {"filePath": "no.py", "content": "x"}))
        self.assertEqual(asked, [("edit", "yes.py"), ("edit", "no.py")])
        self.assertIn("denied by the user", ws.run("bash", {"command": "python -m unittest"}))

    def test_snapshot_per_step_and_diff(self):
        path = self.put("s.py", "v = 1\n")
        self.ws.run("read", {"filePath": "s.py"})
        self.ws.begin_step(1)
        self.ws.run("edit", {"filePath": "s.py", "oldString": "v = 1", "newString": "v = 2"})
        self.ws.begin_step(2)
        self.ws.run("edit", {"filePath": "s.py", "oldString": "v = 2", "newString": "v = 3"})
        self.ws.run("write", {"filePath": "new.py", "content": "n = 1\n"})
        self.assertIn("-v = 1\n+v = 3", self.ws.diff())
        self.assertEqual(sorted(self.ws.revert(1)), ["new.py", "s.py"])
        self.assertEqual(path.read_text(), "v = 2\n")
        self.assertFalse((self.root / "new.py").exists())


class MoreAgentTests(Fixture):
    def test_explore_subagent_cannot_edit_and_reports_back(self):
        self.put("m.py", "def target():\n    pass\n")
        model = Script([
            ("", [call("task", description="find target", prompt="Where is target defined?", subagent_type="explore")]),
            ("", [call("grep", pattern="def target")]),                               # 하위 에이전트
            ("", [call("edit", filePath="m.py", oldString="pass", newString="x")]),   # 하위 에이전트의 고치기 — 막힘
            ("m.py:1 에 있습니다", []),                                                  # 하위 에이전트 답
            ("찾았습니다", []),
        ])
        result = CodingAgent(model, self.ws).run("target 이 어디 있어?")
        self.assertEqual(result.text, "찾았습니다")
        task_result = model.seen[-1][-1].content
        self.assertIn('<task id="task_', task_result)
        self.assertIn("<task_result>\nm.py:1 에 있습니다", task_result)
        self.assertIn("not available", model.seen[3][-1].content)  # 탐색 에이전트에는 고치기 도구가 없다
        self.assertIn("pass", (self.root / "m.py").read_text())
        child_tools = [t["function"]["name"] for t in model.tools_seen[1]]
        self.assertNotIn("task", child_tools)  # 깊이 1
        self.assertIn("file search specialist", model.seen[1][0].content)

    def test_plan_mode_reads_only_then_builds(self):
        path = self.put("p.py", "a = 1\n")
        model = Script([
            ("", [call("read", filePath="p.py")]),
            ("", [call("edit", filePath="p.py", oldString="a = 1", newString="a = 2")]),
            ("계획: a 를 2 로", []),
            ("", [call("edit", filePath="p.py", oldString="a = 1", newString="a = 2")]),
            ("바꿨습니다", []),
        ])
        agent = CodingAgent(model, self.ws, mode="plan")
        plan = agent.run("a 를 2로 바꿀 계획")
        self.assertIn("Plan mode ACTIVE", model.seen[0][1].content)
        self.assertIn("not available", model.seen[2][-1].content)  # 계획 모드에는 고치기 도구가 없다
        self.assertNotIn("edit", [t["function"]["name"] for t in model.tools_seen[0]])
        self.assertEqual(path.read_text(), "a = 1\n")
        done = agent.switch_to_build(plan.text)
        self.assertEqual((done.text, path.read_text()), ("바꿨습니다", "a = 2\n"))
        self.assertIn("no longer in read-only mode", model.seen[3][-1].content)

    def test_question_stops_and_the_answer_resumes(self):
        from jarvis.harness.permission import tier_rules
        ws = Workspace(self.root, rules=tier_rules("auto"))
        model = Script([("", [call("question", questions=[{"question": "어느 파일?", "options": [{"label": "a.py"}]}])]),
                        ("a.py 로 하겠습니다", [])])
        agent = CodingAgent(model, ws)
        asked = agent.run("고쳐줘")
        self.assertEqual((asked.stopped, asked.text), ("question", "어느 파일?"))
        done = agent.answer("a.py")
        self.assertEqual(done.text, "a.py 로 하겠습니다")
        self.assertIn("User has answered your questions: a.py", model.seen[-1][-1].content)
        worker = CodingAgent(Script([("", [call("question", questions=[{"question": "?", "options": []}])]), ("끝", [])]), self.ws)
        self.assertEqual(worker.run("t").stopped, "done")  # 일꾼(사람 없음)은 질문이 거절된다

    def test_connection_errors_are_retried(self):
        from jarvis.providers import ProviderError
        waits = []
        model = Script([ProviderError("연결 끊김"), ProviderError("연결 끊김"), ("됐다", [])])
        result = CodingAgent(model, self.ws, sleep=waits.append).run("t")
        self.assertEqual((result.text, waits), ("됐다", [2, 4]))
        model = Script([ProviderError("x")] * 4)
        self.assertEqual(CodingAgent(model, self.ws, sleep=lambda _s: None).run("t").stopped, "error")

    def test_skills_web_and_instruction_files(self):
        self.put(".alex/skills/release/SKILL.md", "---\nname: release\ndescription: How to cut a release\n---\nRun tests then tag.")
        self.put("CLAUDE.md", "Use tabs.")
        from jarvis.harness.permission import tier_rules
        ws = Workspace(self.root, rules=tier_rules("auto"), fetch=lambda url, fmt: f"page {url} {fmt}",
                       search=lambda q: f"results for {q}")
        model = Script([("", [call("skill", name="release")]), ("", [call("websearch", query="ruff rules")]),
                        ("", [call("webfetch", url="https://example.com")]), ("끝", [])])
        approvals = []
        ws.ask = lambda p, pat, d: approvals.append(p) or True
        CodingAgent(model, ws).run("t")
        system = model.seen[0][0].content
        self.assertIn("<name>release</name>", system)
        self.assertIn("Instructions from:", system)
        self.assertIn("Use tabs.", system)
        self.assertIn("Run tests then tag.", model.seen[1][-1].content)
        self.assertEqual(model.seen[2][-1].content, "results for ruff rules")
        self.assertEqual(model.seen[3][-1].content, "page https://example.com markdown")
        self.assertEqual(approvals, ["webfetch"])  # 웹 읽기는 대화 단계에서 묻기

    def test_mcp_tools_are_listed_and_called(self):
        import sys
        from jarvis.harness.mcp import MCPHub, MCPServer
        server = self.root.parent / "fake_mcp.py"
        server.write_text(r"""
import json, sys
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m: continue
    if m["method"] == "initialize": r = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "f"}}
    elif m["method"] == "tools/list": r = {"tools": [{"name": "add", "description": "Add two numbers", "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}}}]}
    elif m["method"] == "tools/call": r = {"content": [{"type": "text", "text": str(m["params"]["arguments"]["a"] + m["params"]["arguments"]["b"])}]}
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
""", encoding="utf-8")
        hub = MCPHub([MCPServer("calc", [sys.executable, str(server)])])
        hub.start()
        self.addCleanup(hub.close)
        from jarvis.harness.permission import tier_rules
        ws = Workspace(self.root, rules=tier_rules("auto"), mcp=hub, ask=lambda *a: True)
        model = Script([("", [call("mcp__calc__add", a=2, b=3)]), ("5 입니다", [])])
        CodingAgent(model, ws).run("2+3")
        self.assertIn("mcp__calc__add", [t["function"]["name"] for t in model.tools_seen[0]])
        self.assertEqual(model.seen[1][-1].content, "5")
        self.assertIn("not allowed", Workspace(self.root, mcp=hub).run("mcp__calc__add", {"a": 1, "b": 1}))  # 일꾼은 거절


class DiagnosticsTests(Fixture):
    def test_python_undefined_names_and_verilog_syntax(self):
        from jarvis.harness import diagnostics
        py = self.put("u.py", "def f():\n    return missing + 1\n")
        if diagnostics.TOOLS["ruff"].is_file():
            self.assertIn("Undefined name `missing` (F821)", diagnostics.diagnose(py))
        self.assertEqual(diagnostics.diagnose(self.put("fine.py", "import os\n")), "")  # 안 쓴 import 는 경고 — 알리지 않음
        if diagnostics.TOOLS["yosys"].is_file():
            self.assertIn("ERROR [2:1] syntax error", diagnostics.diagnose(self.put("bad.v", "module m(input a, output b);\n  assign b = a &;\nendmodule\n")))
            self.assertEqual(diagnostics.diagnose(self.put("ok.v", "module m(input a, output b);\n  assign b = ~a;\nendmodule\n")), "")


if __name__ == "__main__":
    unittest.main()


class SessionTests(Fixture):
    """서버 API 의 몸통(sessions.py) — 세션·사건·허락 묻기·질문·중단·바뀐 내용·되돌리기·다시 켜서 이어 가기."""

    def make(self, model, turns=None):
        from contextlib import contextmanager
        from jarvis.harness.sessions import CodeSessions

        @contextmanager
        def turn(_session):
            if turns is not None:
                turns.append(1)
            yield
        return CodeSessions(lambda: model, model_turn=turn, store=Path(self.temp.name) / "sessions")

    def wait_for(self, sessions, sid, kind, since=0):
        import time
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for event in sessions.events(sid, since, wait=0.5):
                if event["type"] == kind:
                    return event
                since = event["n"] + 1
        self.fail(f"사건 {kind} 이 오지 않았습니다")

    def test_permission_events_diff_revert_and_reload(self):
        model = Script([("", [call("write", filePath="new.py", content="n = 1\n")]), ("만들었습니다", []),
                        ("이어서 답합니다", [])])
        turns = []
        sessions = self.make(model, turns)
        session = sessions.create("owner", self.root, "ask")
        sessions.prompt(session.id, "new.py 만들어줘", owner="owner")
        asked = self.wait_for(sessions, session.id, "permission.asked")
        self.assertEqual((asked["request"]["permission"], asked["request"]["pattern"]), ("edit", "new.py"))
        self.assertEqual(sessions.get(session.id).public()["permissions"][0]["id"], asked["request"]["id"])
        with self.assertRaises(KeyError):
            sessions.reply_permission(session.id, asked["request"]["id"], "once", owner="someone else")
        sessions.reply_permission(session.id, asked["request"]["id"], "once", owner="owner")
        idle = self.wait_for(sessions, session.id, "session.idle", asked["n"])
        self.assertEqual(idle["result"]["text"], "만들었습니다")
        self.assertEqual(len(turns), 2)  # 모델을 부를 때마다 차례
        self.assertIn("+n = 1", sessions.diff(session.id))
        self.assertIn("+n = 1", self.make(model).diff(session.id))  # 되돌리기 기록도 저장돼 다시 켜도 남는다
        self.assertEqual(sessions.revert(session.id, 0), ["new.py"])
        self.assertEqual(list((Path(self.temp.name) / "sessions" / f"{session.id}.blobs").glob("*")), [])
        self.assertFalse((self.root / "new.py").exists())
        # 다시 켜도 대화가 남아 이어 간다
        again = self.make(model)
        self.assertEqual([m["role"] for m in again.messages(session.id)], ["user", "assistant", "tool", "assistant"])
        again.prompt(session.id, "계속", owner="owner")
        self.assertEqual(self.wait_for(again, session.id, "session.idle")["result"]["text"], "이어서 답합니다")
        self.assertEqual(model.seen[-1][-1].content, "계속")
        self.assertEqual(len(model.seen[-1]), 6)  # 지시문 + 앞 대화 4 + 새 요청

    def test_always_question_plan_and_abort(self):
        model = Script([("", [call("write", filePath="a.py", content="a = 1\n")]),
                        ("", [call("write", filePath="a.py", content="a = 2\n")]),
                        ("", [call("question", questions=[{"question": "더 할까요?", "options": []}])]),
                        ("알겠습니다", []),
                        ("", [call("edit", filePath="a.py", oldString="a = 2", newString="a = 3")]), ("계획: a=3", []),
                        ("", [call("bash", command="git status")])])
        sessions = self.make(model)
        session = sessions.create("owner", self.root, "ask")
        sessions.prompt(session.id, "a.py", owner="owner")
        asked = self.wait_for(sessions, session.id, "permission.asked")
        sessions.reply_permission(session.id, asked["request"]["id"], "always")
        stopped = self.wait_for(sessions, session.id, "question.asked", asked["n"])
        self.assertEqual(stopped["questions"][0]["question"], "더 할까요?")
        self.assertEqual((self.root / "a.py").read_text(), "a = 2\n")  # 두 번째 쓰기는 묻지 않았다(always)
        self.assertEqual(sessions.get(session.id).status, "question")
        sessions.prompt(session.id, "아니요", owner="owner")
        self.wait_for(sessions, session.id, "session.idle", stopped["n"])
        self.assertIn("User has answered your questions: 아니요", model.seen[3][-1].content)
        sessions.prompt(session.id, "a 를 3 으로 할 계획", owner="owner", mode="plan")
        planned = self.wait_for(sessions, session.id, "session.idle", stopped["n"] + 3)
        self.assertEqual(planned["result"]["text"], "계획: a=3")
        self.assertEqual((self.root / "a.py").read_text(), "a = 2\n")  # 계획 모드는 고치지 않는다
        sessions.prompt(session.id, "실행해", owner="owner", mode="build")
        asked = self.wait_for(sessions, session.id, "permission.asked", planned["n"])
        self.assertEqual(asked["request"]["pattern"], "git status")
        sessions.abort(session.id)
        done = self.wait_for(sessions, session.id, "session.idle", asked["n"])
        self.assertEqual(done["result"]["stopped"], "cancelled")
        with self.assertRaises(ValueError):
            sessions.prompt(session.id, "", owner="owner")

    def test_commands_init_review_and_user_commands(self):
        from jarvis.harness.commands import expand, find_commands
        self.assertEqual(expand("fix $1 then $2", "a.py b c"), "fix a.py then b c")   # 마지막 번호는 남은 인자 전부
        self.assertEqual(expand("look at $ARGUMENTS", '"x y" z'), 'look at "x y" z')
        self.assertEqual(expand("plain", "extra"), "plain\n\nextra")
        self.assertEqual(expand("v: !`python -V`", "", lambda c: f"<{c}>"), "v: <python -V>")
        self.put(".alex/commands/test/all.md", "---\ndescription: run all tests\nagent: explore\n---\nRun tests in $1")
        found = find_commands(self.root)
        self.assertIn("AGENTS.md", found["init"].template)
        self.assertTrue(found["review"].subtask)
        self.assertEqual((found["test/all"].description, found["test/all"].agent), ("run all tests", "explore"))
        model = Script([("AGENTS.md 를 만들었습니다", []), ("검토: 문제 없음", [])])
        sessions = self.make(model)
        session = sessions.create("owner", self.root, "auto")
        self.assertIn("review", [c["name"] for c in sessions.commands(session.id)])
        sessions.command(session.id, "init", "짧게", owner="owner")
        first = self.wait_for(sessions, session.id, "session.idle")
        self.assertIn("Create or update `AGENTS.md`", model.seen[0][-1].content)
        self.assertIn("짧게", model.seen[0][-1].content)
        sessions.command(session.id, "review", owner="owner")
        done = self.wait_for(sessions, session.id, "session.idle", first["n"] + 1)
        self.assertIn('<task id="task_', done["result"]["text"])
        self.assertIn("검토: 문제 없음", done["result"]["text"])
        self.assertIn("You are a code reviewer", model.seen[1][1].content)   # 하위 에이전트의 새 대화
        self.assertEqual(sessions.messages(session.id)[-2]["content"], "/review")
        with self.assertRaises(KeyError):
            sessions.command(session.id, "nope")
