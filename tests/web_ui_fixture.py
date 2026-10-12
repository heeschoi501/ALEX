"""Temporary, deterministic local server for the optional real-browser check."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.providers import MockLLMProvider, OllamaProvider
from jarvis.router import Destination
from jarvis.web import make_server
from tests.test_web import make_state


class RecordingModel(MockLLMProvider):
    def __init__(self, record: Path):
        super().__init__("ui-test", "UI")
        self.record = record

    def generate(self, messages, **kwargs):
        self.record.write_text(json.dumps(
            [{"role": m.role, "content": m.content} for m in messages],
            ensure_ascii=False), encoding="utf-8")
        return "UI 검증 응답입니다."


class TitleModel(OllamaProvider):
    def __init__(self):
        super().__init__("ui-title-test")

    def health_check(self):
        return True

    def generate(self, messages, **kwargs):
        return "UI 검증 대화"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="alex-ui-fixture-") as directory:
        state = make_state(Path(directory))
        state.core.auto_lessons = False
        model = RecordingModel(args.record)
        state.core.providers[Destination.LOCAL_GENERAL] = model
        state.core.providers[Destination.LOCAL_CODER] = model
        state.core.local_summarizer = TitleModel()
        server = make_server(state, 0)
        print(json.dumps({"port": server.server_port}), flush=True)
        signal.signal(signal.SIGTERM, lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt()))
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
