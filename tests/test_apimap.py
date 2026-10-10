"""이 파일이 쓰는 객체의 API(ADR-072) — 시그니처만, 실패와 관련 있는 것부터, 켜졌을 때만 수정안 요청에 붙는다."""
import tempfile
import unittest
from pathlib import Path

from jarvis.apimap import api_block
from jarvis.storage import Storage
from jarvis.workspace import SafeWorkspaceEditor

TOOLS = '''class FileReader:
    def __init__(self, root):
        self.root = root

    def run(self, **kwargs) -> str:
        """파일을 읽어 글로 돌려준다."""
        return open(kwargs["path"]).read()

    def _secret(self):
        return 1


class Searcher:
    def search(self, query: str, limit: int = 5) -> list:
        return []
'''
APP = '''from .tools import FileReader, Searcher


class App:
    def __init__(self, root):
        self.file_reader = FileReader(root)
        self.searcher = Searcher()

    def ask_file(self, relative_path):
        return content.upper()
'''


class ApiBlockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "tools.py").write_text(TOOLS, encoding="utf-8")
        (self.root / "pkg" / "app.py").write_text(APP, encoding="utf-8")

    def test_the_objects_public_methods_are_shown_with_kwargs_keys_and_no_bodies(self):
        block = api_block(self.root, "pkg/app.py", APP, "NameError: name 'content' is not defined in ask_file file")
        self.assertIn("self.file_reader → class FileReader:", block)
        self.assertIn("def run(self, **kwargs) -> str  # kwargs: path / 파일을 읽어 글로 돌려준다.", block)
        self.assertIn("def search(self, query: str, limit: int=5) -> list", block)
        self.assertNotIn("_secret", block)
        self.assertNotIn("open(", block)  # 본문은 보이지 않는다
        self.assertLess(block.index("FileReader"), block.index("Searcher"))  # 실패와 겹치는 낱말(file)이 먼저

    def test_the_budget_keeps_the_most_related_class_whole_and_the_rest_as_names(self):
        block = api_block(self.root, "pkg/app.py", APP, "file reader content", budget=260)
        self.assertIn("def run(self, **kwargs) -> str", block)
        self.assertIn("self.searcher → class Searcher: 메서드 search", block)  # 이름만이라도 — 없는 메서드를 지어내지 않게
        self.assertNotIn("def search", block)

    def test_non_python_or_broken_files_give_nothing(self):
        self.assertEqual(api_block(self.root, "README.md", "# x", "x"), "")
        self.assertEqual(api_block(self.root, "pkg/app.py", "def broken(:\n", "x"), "")


STORE = '''class Store:
    def __init__(self, path):
        self.path = path

    def training_run_count(self) -> int:
        return 0

    def record_training_run(self, kind: str) -> None:
        pass
'''
CORE = '''from .store import Store


class Core:
    def __init__(self, storage: Store | None = None):
        self.storage = storage
'''
HELPERS = '''def build_summary(general, settings) -> str:
    """요약용 공급자를 만든다."""
    return ""


def _hidden():
    pass
'''
REPORT = '''from .core import Core
from .helpers import build_summary


def growth_report(core: Core) -> str:
    runs = core.storage.export_count()
    return build_summary(None, None) + str(runs)
'''


class WiderApiTests(unittest.TestCase):
    """2026-10-04 넓힘 — 역사 과제(PERFORMANCE 37)의 실패는 모두 '다른 파일에 있는 API 를 모름'이었고, 처음 판은 그 모양을 못 담았다."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "pkg").mkdir()
        for name, text in (("store.py", STORE), ("core.py", CORE), ("helpers.py", HELPERS), ("report.py", REPORT)):
            (self.root / "pkg" / name).write_text(text, encoding="utf-8")

    def test_an_annotated_parameter_reaches_the_attribute_class(self):
        block = api_block(self.root, "pkg/report.py", REPORT, "export count AttributeError")
        self.assertIn("core.storage → class Store:", block)  # core: Core → Core.__init__ 의 storage: Store
        self.assertIn("def training_run_count(self) -> int", block)

    def test_an_imported_function_shows_its_signature(self):
        block = api_block(self.root, "pkg/report.py", REPORT, "summary")
        self.assertIn("from helpers: def build_summary(general, settings) -> str  # 요약용 공급자를 만든다.", block)
        self.assertNotIn("_hidden", block)

    def test_an_attribute_set_from_an_annotated_parameter_is_known(self):
        block = api_block(self.root, "pkg/core.py", CORE, "storage")
        self.assertIn("self.storage → class Store:", block)


class CoreApiMapTests(unittest.TestCase):
    def setUp(self):
        from jarvis.core import JarvisCore
        from jarvis.hardware import HardwareInfo, PerformanceProfile
        from jarvis.providers import CloudStubProvider, MockLLMProvider

        test = self

        class Model(MockLLMProvider):
            def generate(self, messages, **kwargs):
                test.seen = "".join(m.content for m in messages)
                return ("FILE: pkg/app.py\n<<<<<<< SEARCH\n        return content.upper()\n=======\n"
                        "        content = self.file_reader.run(path=relative_path)\n        return content.upper()\n>>>>>>> REPLACE\n")

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "project"
        (self.root / "pkg").mkdir(parents=True)
        (self.root / "pkg" / "tools.py").write_text(TOOLS, encoding="utf-8")
        (self.root / "pkg" / "app.py").write_text(APP, encoding="utf-8")
        storage = Storage(base / "alex.db")
        self.core = JarvisCore(
            storage=storage, hardware=HardwareInfo("TestOS", "CPU", 32, "GPU", 8, PerformanceProfile.LAPTOP),
            local_general=MockLLMProvider("general", "GENERAL"), local_coder=Model("coder", "CODER"),
            cloud_coder=CloudStubProvider("cc", "CLOUD"), cloud_reasoning=CloudStubProvider("cr", "CLOUD"),
            workspace_editor=SafeWorkspaceEditor(self.root, base / "backups", storage=storage))
        self.session = self.core.get_or_create_session(False)

    def test_the_api_reaches_the_edit_prompt_only_when_enabled(self):
        self.core.propose_bundle_local(self.session, ["pkg/app.py"], "NameError: name 'content' is not defined")
        self.assertNotIn("이 파일이 쓰는 객체의 API", self.seen)  # 기본 꺼짐
        self.core.coding_api_map = True
        bundle = self.core.propose_bundle_local(self.session, ["pkg/app.py"], "NameError: name 'content' is not defined")
        self.assertIn("self.file_reader → class FileReader:", self.seen)
        self.assertIn("# kwargs: path", self.seen)
        self.assertIn("run(path=relative_path)", bundle.diff)


if __name__ == "__main__":
    unittest.main()
