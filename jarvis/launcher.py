"""Windowed launcher: checks local models, starts background services, opens chat.

Only setup downloads use the internet. Daily launch uses loopback requests.
No shell=True, no system installation, no paid APIs, no automatic services.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from .app import effective_local_models
from .config import load_settings
from .hardware import PerformanceProfile, detect_hardware
from .runtime import install

ROOT = Path(__file__).resolve().parent.parent
WEB = "http://127.0.0.1:8765"
OLLAMA = "http://127.0.0.1:11434"
FULL_NAME = "Adaptive Learning & Evolving eXperience"


class NeedsDownload(RuntimeError):
    pass


def get_json(url: str, timeout: float = 2) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError("서버 응답 형식이 잘못되었습니다.")
    return value


def instance_id(data_dir: Path) -> str:
    return hashlib.sha256(os.path.normcase(str(data_dir.resolve())).encode()).hexdigest()[:20]


PACKAGE = Path(__file__).resolve().parent
CODE_SUFFIXES = (".py", ".html", ".css", ".js")


def code_fingerprint(package: Path = PACKAGE) -> str:
    """앱 코드의 내용 지문. 서버는 시작할 때 한 번, 런처는 켤 때마다 잰다.

    둘이 다르면 떠 있는 서버는 옛 코드다. 2026-09-25 에 웹 읽기를 넣고 런처를 다시 켰는데,
    07:56 부터 떠 있던 서버를 그대로 써서 `/status` 에 새 기능이 없었다. 런처는 같은 프로젝트의
    서버가 떠 있으면 재사용하도록 돼 있고, 그때 코드가 바뀌었는지는 보지 않았다.
    수정 시각이 아니라 내용으로 잰다 — 압축 해제나 복사는 시각만 바꾼다.
    """
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*")):
        if path.is_file() and path.suffix in CODE_SUFFIXES and "__pycache__" not in path.parts:
            digest.update(path.relative_to(package).as_posix().encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()[:16]


# 이 데스크톱(RTX 5080)에서 플래시 어텐션 CUDA 워밍업에 죽은 모델 계열(ADR-054, llama-server 0xc0000409).
FA_CRASH_FAMILIES = ("qwen3.5:",)


def ollama_environment(profile: PerformanceProfile, models: tuple[str, ...] = ()) -> dict:
    """런처가 띄우는 Ollama 의 환경. 이미 떠 있는 Ollama 에는 적용되지 않는다.

    플래시 어텐션은 Ollama 기본값(켜짐)이다. 데스크톱의 기본 모델이 FA 에서 죽은 계열일 때만 끈다(ADR-064).
    ADR-054 는 데스크톱 전체에서 껐는데, 그 원인 qwen3.5:122b 가 결선에서 떨어졌고(PERFORMANCE 26-8) 끄면
    qwen3.8:27b 가 27%, 20b 가 22% 느려졌다(26-2). 설정 화면에서 모델만 바꾸면 떠 있는 Ollama 는 그대로다 —
    qwen3.5 로 바꾼 뒤에는 Ollama 까지 새로 떠야 FA 가 꺼진다.

    데스크톱은 KV 캐시를 8비트(q8_0)로 둔다(2026-10-04, 사용자 승인). KV 가 절반이 되어 qwen3.8:27b-q3 66층이
    16K 문맥에서 16GB 에 다 올라간다(자동 56층 16 tok/s → 전부 GPU 약 65 tok/s). 사용자가 환경변수로 정한 값이
    있으면 그것을 따른다. FA 를 끈 경우 Ollama 는 KV 양자화를 쓰지 않는다.
    """
    environment = dict(os.environ, OLLAMA_HOST="127.0.0.1:11434", OLLAMA_MODELS=str(ROOT / "runtime" / "models"),
                       OLLAMA_NO_CLOUD="1", OLLAMA_MAX_LOADED_MODELS="1", OLLAMA_NUM_PARALLEL="1")
    if profile == PerformanceProfile.DESKTOP:
        environment.setdefault("OLLAMA_KV_CACHE_TYPE", "q8_0")
        if any(model.startswith(FA_CRASH_FAMILIES) for model in models):
            environment["OLLAMA_FLASH_ATTENTION"] = "0"
    return environment


def strata_dir() -> Path:
    return Path(os.getenv("JARVIS_STRATA_DIR") or (ROOT.parent / "tools" / "strata"))


def strata_command(folder: Path, port: int) -> list[str]:
    """Strata 서버를 띄우는 명령 — Strata 설치가 만든 run-<모델>.bat 과 같은 것(설정 파일 strata-<모델>.json, ADR-086)."""
    python = folder / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise RuntimeError(f"Strata 가 설치되어 있지 않습니다({folder}). START-HERE.bat 으로 먼저 설치하세요.")
    selected = os.getenv("JARVIS_STRATA_CONFIG", "").strip()
    if selected:
        config = Path(selected)
        if not config.is_absolute():
            config = folder / config
        if not config.is_file():
            raise RuntimeError(f"지정한 Strata 설정 파일이 없습니다: {config}")
    else:
        configs = sorted(folder.glob("strata-*.json"))
        if not configs:
            raise RuntimeError(f"Strata 가 설치되어 있지 않습니다({folder}). START-HERE.bat 으로 먼저 설치하세요.")
        if len(configs) > 1:
            raise RuntimeError("Strata 모델이 여러 개 설치되어 있습니다: " + ", ".join(c.name for c in configs)
                               + " — JARVIS_STRATA_CONFIG 로 하나를 고르세요.")
        config = configs[0]
    return [str(python), str(folder / "serve" / "server.py"), "--engine", "strata", "--config", str(config),
            "--port", str(port)]


def embed_dir() -> Path:
    return Path(os.getenv("JARVIS_EMBED_DIR") or (ROOT.parent / "tools" / "embed"))


def embedding_command(folder: Path, port: int, threads: int) -> list[str]:
    """기억 뜻 검색용 임베딩 서버(ADR-094). folder/embed.json 이 고정한 llama-server·모델을 **CPU 전용**으로 띄운다.

    --device none·-ngl 0: 그래픽카드에 아무것도 올리지 않는다(Strata 가 16GB 중 약 14GB 를 쓴다).
    -t 스레드: Strata 의 CPU 계산과 나눠 쓰도록 적게(기본 4/16). --pooling last 는 Qwen3-Embedding 방식(모델 카드).
    문맥 4096 을 2슬롯으로(슬롯당 2048토큰 — 기억 하나·질문 하나에 충분, 메모리 약 0.5GB).
    """
    try:
        config = json.loads((folder / "embed.json").read_text(encoding="utf-8"))
        server, model = folder / config["server"], folder / config["model"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"임베딩 서버 설정(embed.json)을 읽지 못했습니다({folder}): {exc}") from exc
    for path in (server, model):
        if not path.is_file():
            raise RuntimeError(f"임베딩 서버 파일이 없습니다: {path}")
    return [str(server), "-m", str(model), "--embedding", "--pooling", "last", "--host", "127.0.0.1", "--port", str(port),
            "--device", "none", "-ngl", "0", "-t", str(threads), "-tb", str(threads), "-c", "4096", "-b", "2048",
            "-ub", "2048", "--parallel", "2", "--no-webui"]


def ensure_embedding(host: str, threads: int, report, log_dir: Path, flags: int, wait_seconds: float = 60) -> bool:
    """임베딩 서버가 떠 있게 한다. 실패해도 ALEX 는 켠다(기억은 낱말로만 찾는다) — 오류를 내지 않고 False."""
    try:
        if get_json(host + "/health").get("status") == "ok":
            return True
    except (OSError, ValueError):
        pass
    try:
        command = embedding_command(embed_dir(), int(host.rsplit(":", 1)[1]), threads)
    except RuntimeError as exc:
        report(f"기억 뜻 검색을 쓰지 않습니다: {exc}")
        return False
    report("기억 검색 도우미(임베딩, CPU)를 켜는 중…")
    if os.name == "nt":
        flags |= subprocess.BELOW_NORMAL_PRIORITY_CLASS  # 대화(Strata)보다 뒤로
    with (log_dir / "embedding-server.log").open("a", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=embed_dir(), stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                   creationflags=flags)
    (ROOT / "runtime").mkdir(exist_ok=True)
    (ROOT / "runtime" / "embedding.pid").write_text(str(process.pid))
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        try:
            if get_json(host + "/health").get("status") == "ok":
                return True
        except (OSError, ValueError):
            pass
        if process.poll() is not None:
            break
        time.sleep(0.5)
    report("기억 검색 도우미가 켜지지 않았습니다(logs/embedding-server.log). 기억은 낱말로만 찾습니다.")
    return False


def ensure_strata(host: str, report, log_dir: Path, flags: int, wait_seconds: float = 900) -> None:
    """Strata 모드면 Strata 서버가 떠 있게 한다. 처음 올릴 때 35~55GB 를 RAM 에 올리느라 1~3분 PC 가 느려진다(Strata 안내)."""
    try:
        if get_json(host + "/health").get("service") == "strata":
            return
    except (OSError, ValueError):
        pass
    command = strata_command(strata_dir(), int(host.rsplit(":", 1)[1]))
    report("큰 모델(Strata)을 올리는 중… 처음에는 1~3분 걸리고 그동안 PC 가 느릴 수 있습니다.")
    with (log_dir / "strata-server.log").open("a", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=strata_dir(), stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                   creationflags=flags)
    (ROOT / "runtime").mkdir(exist_ok=True)
    (ROOT / "runtime" / "strata.pid").write_text(str(process.pid))
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        try:
            health = get_json(host + "/health")
            if health.get("service") == "strata" and health.get("loaded"):
                return
        except (OSError, ValueError):
            pass
        if process.poll() is not None:
            raise RuntimeError("Strata 가 시작되지 않았습니다. logs/strata-server.log 를 확인하세요.")
        time.sleep(1)
    raise RuntimeError("Strata 시작 시간이 초과되었습니다. logs/strata-server.log 를 확인하세요.")


def launch(allow_download: bool = False, report=print) -> str:
    os.chdir(ROOT)
    # This marker is passed to the web process only when this launch started
    # Ollama with a known model store. Existing servers may use another drive.
    os.environ.pop("JARVIS_OLLAMA_MODELS", None)
    # The everyday launcher always guarantees the user's no-cloud requirement.
    os.environ["JARVIS_USE_OLLAMA"] = "1"
    os.environ["JARVIS_LOCAL_ONLY"] = "1"
    os.environ["JARVIS_OLLAMA_HOST"] = OLLAMA
    os.environ["PYTHONUTF8"] = "1"
    settings = load_settings()
    try:
        existing = get_json(WEB + "/health")
    except urllib.error.HTTPError as exc:
        raise RuntimeError("8765 포트를 다른 프로그램이 사용합니다. 해당 프로그램을 종료해 주세요.") from exc
    except (OSError, ValueError):
        existing = None
    if existing:
        if existing.get("app") != "jarvis" or existing.get("instance") != instance_id(settings.data_dir):
            raise RuntimeError("8765 포트에 다른 프로젝트가 실행 중입니다. 먼저 그 창에서 앱 종료를 누르세요.")
        if not existing.get("local_only"):
            raise RuntimeError("기존 서버가 로컬 전용 모드가 아닙니다. 종료 후 다시 실행하세요.")
        # 재사용은 의도다(두 개 띄우지 않는다). 다만 그 뒤에 코드가 바뀌었으면 조용히 옛 버전을
        # 쓰게 된다(ADR-049 와 같은 종류). 지문을 모르는 서버는 이 검사 이전 버전이므로 역시 옛 코드다.
        if existing.get("code") != code_fingerprint():
            raise RuntimeError(
                "ALEX 가 이미 켜져 있는데, 그 뒤에 코드가 바뀌었습니다(업데이트).\n"
                "열려 있는 ALEX 화면에서 '앱 종료' 를 누른 뒤 다시 실행하세요.\n"
                "그대로 쓰면 새 기능이 빠진 옛 버전입니다."
            )
        return WEB
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    if settings.local_backend == "strata":
        report("로컬 모델 서버(Strata) 확인 중…")
        ensure_strata(settings.strata_host, report, log_dir, flags)
    else:
        # 프로필은 Ollama 를 띄우기 전에 정한다 — Ollama 환경이 프로필에 따라 다르다(ADR-054).
        hardware = detect_hardware()
        if settings.force_profile:
            hardware.profile = PerformanceProfile(settings.force_profile)
        # 모델도 Ollama 를 띄우기 전에 정한다 — FA 를 끌지는 기본 모델에 달렸다(ADR-064).
        general, coder = effective_local_models(settings, hardware.profile)
        report("로컬 모델 서버 확인 중…")
        try:
            tags = get_json(OLLAMA + "/api/tags")
        except (OSError, ValueError):
            executable = ROOT / "runtime" / "ollama" / "ollama.exe"
            if not executable.exists():
                if not allow_download:
                    raise NeedsDownload("첫 실행 준비가 필요합니다. Ollama 실행 파일과 공개 모델을 다운로드합니다.\n노트북은 약 6.5GB 다운로드, 설치 공간 약 10GB가 필요합니다. 데스크톱 모델은 더 큽니다.")
                install(report=lambda text, **_: report(text))
            environment = ollama_environment(hardware.profile, (general, coder))
            with (log_dir / "ollama-server.log").open("a", encoding="utf-8") as log:
                process = subprocess.Popen([str(executable), "serve"], cwd=ROOT, env=environment, stdout=log, stderr=log, creationflags=flags)
            (ROOT / "runtime" / "ollama.pid").write_text(str(process.pid))
            for _ in range(80):
                try:
                    tags = get_json(OLLAMA + "/api/tags")
                    break
                except (OSError, ValueError):
                    if process.poll() is not None:
                        raise RuntimeError("Ollama가 시작되지 않았습니다. logs/ollama-server.log를 확인하세요.")
                    time.sleep(.25)
            else:
                raise RuntimeError("Ollama 시작 시간이 초과되었습니다. 잠시 후 다시 실행하세요.")
            os.environ["JARVIS_OLLAMA_MODELS"] = environment["OLLAMA_MODELS"]
        models = [general] if hardware.profile != PerformanceProfile.DESKTOP and settings.single_model_on_laptop else list(dict.fromkeys([general, coder]))
        installed = {m.get("name") for m in tags.get("models", []) if isinstance(m, dict)}
        missing = [m for m in models if m not in installed and (":" in m or m + ":latest" not in installed)]
        if missing and not allow_download:
            raise NeedsDownload("이 장비의 모델을 준비해야 합니다:\n" + "\n".join(missing) + "\n최초 다운로드 후에는 오프라인으로 사용할 수 있습니다.")
        for model in missing:
            report(f"모델 다운로드: {model}")
            request = urllib.request.Request(OLLAMA + "/api/pull", data=json.dumps({"model": model, "stream": True}).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=600) as response:
                last = ""
                succeeded = False
                for raw in response:
                    item = json.loads(raw)
                    if item.get("error"):
                        raise RuntimeError(item["error"])
                    text = item.get("status", "")
                    if item.get("total"):
                        text += f" {100 * item.get('completed', 0) // item['total']}%"
                    if text != last:
                        report(f"{model}\n{text}")
                        last = text
                    succeeded = item.get("status") == "success"
                if not succeeded:
                    raise RuntimeError("모델 다운로드가 완료되지 않았습니다. 다시 실행하면 이어받습니다.")
    if settings.embedding_enabled:
        ensure_embedding(settings.embedding_host, settings.embedding_threads, report, log_dir, flags)
    report("채팅창을 시작합니다…")
    with (log_dir / "web-server.log").open("a", encoding="utf-8") as log:
        web_process = subprocess.Popen([sys.executable, "-m", "jarvis.web", "--port", "8765"], cwd=ROOT, env=os.environ.copy(), stdout=log, stderr=log, creationflags=flags)
    for _ in range(100):
        try:
            health = get_json(WEB + "/health")
            if health.get("instance") == instance_id(settings.data_dir):
                return WEB
        except (OSError, ValueError):
            pass
        if web_process.poll() is not None:
            raise RuntimeError("채팅 서버가 시작되지 않았습니다. logs/web-server.log를 확인하세요.")
        time.sleep(.3)
    raise RuntimeError("채팅 서버 시작 시간이 초과되었습니다. logs/web-server.log를 확인하세요.")


def run_background(log_dir: Path | None = None) -> int:
    """창도 브라우저도 없이 ALEX 를 띄운다 — 데스크톱이 켜질 때 원격 접속용으로(ADR-052).

    모델 다운로드는 하지 않는다(누를 사람이 없다). 처음 한 번은 보통 실행으로 준비해 둬야 한다.
    실패하면 logs/autostart.log 에만 남긴다 — 로그인할 때마다 오류 창이 뜨면 안 된다.
    """
    log_dir = log_dir or ROOT / "logs"
    try:
        launch(False, report=lambda text: None)
        return 0
    except Exception as exc:  # noqa: BLE001 — 무엇이든 기록만 한다
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "autostart.log").open("a", encoding="utf-8") as log:
            log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {type(exc).__name__}: {exc}\n")
        return 1


def main() -> None:
    if "--background" in sys.argv[1:]:
        raise SystemExit(run_background())
    import tkinter as tk
    from tkinter import ttk

    window = tk.Tk()
    window.title("ALEX · 로컬 AI")
    window.geometry("530x305")
    window.configure(bg="#151e23")
    window.resizable(False, False)
    tk.Label(window, text="ALEX", fg="#b9ebc8", bg="#151e23", font=("Segoe UI", 25, "bold")).pack(pady=(25, 0))
    # 이름의 뜻을 이름 아래에 보인다(PROJECT_ALEX 1절, 사용자 요청 2026-09-25).
    tk.Label(window, text=FULL_NAME, fg="#95a5ad", bg="#151e23", font=("Segoe UI", 10)).pack(pady=(0, 8))
    status = tk.StringVar(value="이 기기의 로컬 AI를 준비합니다…")
    tk.Label(window, textvariable=status, wraplength=480, fg="#dae5e7", bg="#151e23", font=("Malgun Gothic", 10)).pack(padx=20, pady=10)
    progress = ttk.Progressbar(window, mode="indeterminate", length=460)
    progress.pack(pady=10)
    events: queue.Queue = queue.Queue()
    action = tk.Button(window, text="모델 다운로드 시작", command=lambda: start(True))
    running = False

    def worker(download: bool) -> None:
        try:
            url = launch(download, report=lambda text: events.put(("status", text)))
            events.put(("done", url))
        except NeedsDownload as exc:
            events.put(("download", str(exc)))
        except Exception as exc:
            events.put(("error", str(exc)))

    def start(download: bool = False) -> None:
        nonlocal running
        if running:
            return
        running = True
        action.pack_forget()
        progress.start(15)
        threading.Thread(target=worker, args=(download,), daemon=True).start()

    def poll() -> None:
        nonlocal running
        while not events.empty():
            kind, text = events.get_nowait()
            if kind == "done":
                webbrowser.open(text)
                window.destroy()
                return
            status.set(text)
            if kind in {"error", "download"}:
                running = False
                progress.stop()
                action.configure(text="모델 다운로드 시작" if kind == "download" else "다시 시도", command=lambda k=kind: start(k == "download"))
                action.pack(pady=10)
        window.after(100, poll)

    start()
    window.after(100, poll)
    window.mainloop()


if __name__ == "__main__":
    main()
