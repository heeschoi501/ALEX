"""오픈코드를 ALEX 자동 수정 루프(/dev-auto)의 일꾼으로 쓴다 — 일은 검증된 에이전트가, 판정·안전장치는 ALEX 가(2026-10-05, ADR-083).

왜: 공개 자료는 Qwen3.8-27B 로컬 코딩에 에이전트를 새로 만들지 말고 검증된 것(Claude Code·OpenCode)을 붙이라고 한다
(NVIDIA Local Coding Agent 안내, 16GB·UD-Q3_K_XL·MTP 로 OpenCode 2시간 자율 개발 사례). 같은 날 이 PC 실측: 같은 ALEX 과제에서
OpenCode 46초, ALEX 수정안 방식 765초(`logs/claude-fixes-20261005/oc-compare-*.json`). OpenCode `/v1` 지연 버그는 이 PC 에서 없었다.

ALEX 가 지키는 것:
- OpenCode 는 /dev-auto 격리 복사본을 다시 복사한 임시 폴더에서만 일한다. 끝나면 ALEX 가 바뀐 파일을 읽어 묶음 수정안으로 만들고,
  작업 범위 밖·tests/ 변경은 버린다(기록에 남김). 그다음은 기존 루프 그대로 — 합격 테스트, 전체 회귀 검사, 덜어내기, 사용자 승인.
- OpenCode 권한: 셸은 `python -m unittest`·`python -m py_compile` 만, 인터넷(webfetch)·작업 폴더 밖 접근 금지, 공유·자동 업데이트 끔.
  운영체제 수준 격리는 아니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable
import urllib.request

from .providers import GenerationCancelled

SKIP_PARTS = {"__pycache__", ".git", ".opencode", "node_modules", ".pytest_cache"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".toml", ".cfg", ".ini", ".yml", ".yaml", ".js", ".html", ".css"}
PERMISSION = {
    "edit": "allow",
    "webfetch": "deny",
    "external_directory": "deny",
    # 마지막으로 맞는 규칙이 이긴다(OpenCode 문서). 나머지 셸 명령은 모두 거부.
    "bash": {"*": "deny", "python -m unittest *": "allow", "python -m unittest": "allow", "python -m py_compile *": "allow"},
}
RULES = ("\n\n[작업 규칙 — ALEX]\n"
         "- 위 범위의 파일만 고친다. tests/ 아래 파일은 고치지 않는다(합격 기준이다). 고쳐도 ALEX 가 버린다.\n"
         "- 쓸 수 있는 셸 명령은 `python -m unittest …` 와 `python -m py_compile …` 뿐이다. 인터넷은 쓰지 않는다.\n"
         "- 고친 뒤 위 테스트 명령을 직접 실행해 통과를 확인하고 끝낸다. 추측하지 말고 출력에 나온 사실로 고친다.")


@dataclass
class WorkerRun:
    changed: dict[str, str] = field(default_factory=dict)   # 상대 경로 → 새 내용(원래 있던 파일)
    created: dict[str, str] = field(default_factory=dict)   # 새로 만든 파일
    deleted: list[str] = field(default_factory=list)
    exit_code: int = 0
    timed_out: bool = False
    seconds: float = 0.0
    tools: dict[str, int] = field(default_factory=dict)
    log_tail: str = ""


def _hashes(root: Path) -> dict[str, str]:
    result = {}
    for path in root.rglob("*"):
        if path.is_file() and not SKIP_PARTS.intersection(path.relative_to(root).parts) and path.suffix != ".pyc":
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _copy_project(source: Path, target: Path) -> None:
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(*SKIP_PARTS, "*.pyc"))


class OpenCodeWorker:
    name, label = "opencode", "오픈코드"

    def __init__(self, home: Path, model: str, *, context: int = 32768, timeout: float = 900.0,
                 host: str = "http://127.0.0.1:11434", command: list[str] | None = None, backend: str = "ollama") -> None:
        self.home = Path(home)                      # C:\ALEX\tools\opencode — opencode.exe·xdg-cache(공급자 패키지)
        self.model = model.strip()                  # Ollama 모델 이름(문맥을 박은 별칭, 예: qwen3.8-q3-32k)
        self.context = int(context)
        self.timeout = float(timeout)
        self.host = host.rstrip("/")
        self.command = command                      # 시험용: 가짜 오픈코드 명령
        self.backend = backend                      # 'ollama' 또는 'strata'(ADR-086: 같은 OpenAI 호환 /v1, 확인 방법만 다르다)
        self.before_run: Callable[[], None] | None = None

    def executable(self) -> Path:
        return self.home / ("opencode.exe" if os.name == "nt" else "opencode")

    def unavailable_reason(self) -> str:
        """쓸 수 없으면 이유(빈 문자열이면 사용 가능). 모델 별칭이 없으면 OpenCode 가 모델을 못 찾는다."""
        if self.command is not None:
            return ""
        if not self.executable().is_file():
            return f"오픈코드 실행 파일이 없습니다: {self.executable()}"
        if self.backend == "strata":
            try:
                with urllib.request.urlopen(f"{self.host}/health", timeout=5) as response:
                    health = json.loads(response.read().decode("utf-8"))
            except Exception:  # noqa: BLE001 — 연결 실패·형식 오류 모두 '지금은 못 씀'
                health = None
            if not isinstance(health, dict) or health.get("service") != "strata":
                return f"Strata 서버({self.host})에 연결하지 못했습니다 — Strata 를 켰는지 확인하세요"
            return ""
        try:
            request = urllib.request.Request(f"{self.host}/api/show", data=json.dumps({"model": self.model}).encode(),
                                             headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=5) as response:
                response.read()
        except Exception:  # noqa: BLE001 — 연결 실패·404 모두 '지금은 못 씀'
            return f"Ollama 에 오픈코드용 모델 '{self.model}' 이(가) 없거나 Ollama 에 연결하지 못했습니다"
        return ""

    def config(self) -> dict:
        return {
            "$schema": "https://opencode.ai/config.json", "share": "disabled", "autoupdate": False,
            "model": f"ollama/{self.model}", "small_model": f"ollama/{self.model}",
            "provider": {"ollama": {"npm": "@ai-sdk/openai-compatible",
                                    "name": "Strata (local)" if self.backend == "strata" else "Ollama (local)",
                                    "options": {"baseURL": f"{self.host}/v1"},
                                    "models": {self.model: {"name": f"{self.model} (ALEX)",
                                                            "limit": {"context": self.context, "output": 8192}}}}},
            "permission": PERMISSION,
        }

    def run(self, project: Path, prompt: str, cancel_event: threading.Event | None = None) -> WorkerRun:
        if cancel_event is not None and cancel_event.is_set():
            raise GenerationCancelled("오픈코드 작업을 중단했습니다.")
        # 외부 일꾼은 OllamaProvider 를 거치지 않는다. 같은 GPU 반환 확인을 실행 전에 한다.
        if self.before_run is not None:
            self.before_run()
        if cancel_event is not None and cancel_event.is_set():
            raise GenerationCancelled("오픈코드 작업을 중단했습니다.")
        project = Path(project)
        with tempfile.TemporaryDirectory(prefix="alex-opencode-") as tmp:
            work = Path(tmp) / "project"
            _copy_project(project, work)
            before = _hashes(work)
            config = Path(tmp) / "opencode.json"
            config.write_text(json.dumps(self.config(), ensure_ascii=False, indent=2), encoding="utf-8")
            data = Path(tmp) / "data"
            data.mkdir()
            env = dict(os.environ, OPENCODE_CONFIG=str(config), OPENCODE_DISABLE_AUTOUPDATE="1",
                       OPENCODE_DISABLE_MODELS_FETCH="1", OPENCODE_DISABLE_LSP_DOWNLOAD="1", OPENCODE_DISABLE_SHARE="1",
                       XDG_CONFIG_HOME=str(self.home / "xdg-config"), XDG_CACHE_HOME=str(self.home / "xdg-cache"),
                       XDG_DATA_HOME=str(data), XDG_STATE_HOME=str(data),
                       PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
            git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
            if os.name == "nt" and git_bash.is_file():
                env["OPENCODE_GIT_BASH_PATH"] = str(git_bash)
            env.pop("SHELL", None)  # 비우지 않으면 WSL bash 를 고를 수 있다
            command = (self.command or [str(self.executable())]) + [
                "run", "--model", f"ollama/{self.model}", "--format", "json", prompt + RULES]
            log_path = Path(tmp) / "events.jsonl"
            started = time.monotonic()
            result = WorkerRun()
            with log_path.open("wb") as log:
                flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
                if cancel_event is not None and cancel_event.is_set():
                    raise GenerationCancelled("오픈코드 작업을 중단했습니다.")
                process = subprocess.Popen(command, cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           stdin=subprocess.DEVNULL, creationflags=flags)
                while process.poll() is None:
                    if cancel_event is not None and cancel_event.is_set():
                        _kill_tree(process)
                        raise GenerationCancelled("오픈코드 작업을 중단했습니다.")
                    if time.monotonic() - started > self.timeout:
                        _kill_tree(process)
                        result.timed_out = True
                        break
                    time.sleep(0.2)
            result.exit_code = process.returncode if process.returncode is not None else -1
            result.seconds = round(time.monotonic() - started, 1)
            raw = log_path.read_text(encoding="utf-8", errors="replace")
            result.log_tail = raw[-1500:]
            for line in raw.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                part = event.get("part") if isinstance(event, dict) and isinstance(event.get("part"), dict) else {}
                if part.get("tool"):
                    result.tools[str(part["tool"])] = result.tools.get(str(part["tool"]), 0) + 1
            after = _hashes(work)
            for path in sorted(set(before) | set(after)):
                if before.get(path) == after.get(path):
                    continue
                if path not in after:
                    result.deleted.append(path)
                    continue
                if Path(path).suffix.lower() not in TEXT_SUFFIXES:
                    continue
                text = (work / path).read_text(encoding="utf-8", errors="replace")
                (result.changed if path in before else result.created)[path] = text
            return result


def _kill_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
    else:
        process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
