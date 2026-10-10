"""노트북에서 데스크톱 ALEX 에 붙는다 — Tailscale 위의 SSH 터널 (ADR-052).

ALEX 는 127.0.0.1 에서만 듣고 로그인 기능이 없다(SECURITY "Web"). 그 설계를 바꾸지 않는다.
SSH 가 로그인과 암호화를 맡고, 노트북의 127.0.0.1:8766 을 데스크톱의 127.0.0.1:8765 로 잇는다.
데스크톱 ALEX 에게는 접속이 자기 자신에게서 온 것으로 보이므로 Host 검사를 그대로 통과한다.
Tailscale 이 집 밖에서도 두 기기를 사설망으로 이어 주므로 카페에서도 같은 명령으로 붙는다.

    python -m jarvis.remote setup --host <데스크톱 이름> --user <데스크톱 사용자>   # 처음 한 번
    python -m jarvis.remote                                                        # 연결
    python -m jarvis.remote check                                                  # 준비 상태

이 파일이 하는 일은 연결을 **여는 것과 무엇이 안 되는지 말해 주는 것**뿐이다. 데스크톱의 SSH
서버·방화벽·키 등록은 `scripts/remote_setup_desktop.ps1`(관리자)이 한다.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

from .config import LOCAL_FIELDS, Settings, load_settings, local_config_path, validate
from .launcher import code_fingerprint

ROOT = Path(__file__).resolve().parent.parent
KEY_NAME = "alex_desktop_ed25519"
LOG_NAME = "remote-ssh.log"
# 두 값은 ssh 명령줄에 들어간다. '-' 로 시작하면 옵션으로 읽혀서(-oProxyCommand=...) 임의 프로그램이
# 실행될 수 있다. 그래서 '-' 시작, 공백, 셸·ssh 특수문자를 받지 않는다.
HOST_RE = re.compile(r"(?!-)[A-Za-z0-9](?:[A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?")
USER_RE = re.compile(r"(?!-)[\w.\-]{1,64}")  # 윈도우 계정은 한글일 수 있다

SETUP_HELP = """데스크톱 연결이 아직 설정되지 않았습니다. 처음 한 번만 하면 됩니다.

  1) 노트북과 데스크톱 둘 다 Tailscale 을 설치하고 같은 계정으로 로그인하십시오.
  2) 데스크톱에서 관리자 PowerShell 로 scripts\\remote_setup_desktop.ps1 을 실행하십시오.
     끝에 '데스크톱 이름' 과 '사용자 이름' 을 알려 줍니다.
  3) 이 노트북에서:
       connect_desktop.cmd setup --host <데스크톱 이름> --user <사용자 이름>
     를 실행하면 키를 만들고, 데스크톱에 붙여 넣을 명령을 알려 줍니다.

자세한 순서: REMOTE_SETUP.md"""

NO_SSH_HELP = """이 노트북에 ssh 가 없습니다. 윈도우 설정 → 시스템 → 선택적 기능 → 'OpenSSH 클라이언트' 를
추가한 뒤 다시 실행하십시오."""

DESKTOP_ALEX_DOWN = """데스크톱에는 연결됐는데 ALEX 가 응답하지 않습니다.
  - 데스크톱에서 ALEX 가 켜져 있는지 보십시오. remote_setup_desktop.ps1 을 실행했다면 로그인할 때
    자동으로 켜집니다. 방금 켰다면 30초쯤 뒤에 다시 연결하십시오.
  - 데스크톱 ALEX 를 켰는데도 이러면 데스크톱의 logs\\web-server.log 를 보십시오."""

# ssh 가 남긴 영어 오류 → 무엇을 하면 되는지. 위에서부터 먼저 걸리는 것을 쓴다.
DIAGNOSES = (
    (("could not resolve hostname", "no such host", "name or service not known"),
     "데스크톱 이름을 찾지 못했습니다. Tailscale 앱에서 두 기기가 모두 '연결됨' 인지, remote_host 가 "
     "Tailscale 에 보이는 기기 이름과 같은지 확인하십시오."),
    (("timed out", "no route to host", "network is unreachable", "host is down"),
     "데스크톱에 닿지 않습니다. 데스크톱이 꺼져 있거나 Tailscale 이 꺼져 있습니다. 집 밖에서는 데스크톱을 "
     "원격으로 켤 수 없습니다(원격 켜기는 같은 공유기 안에서만 됩니다)."),
    (("connection refused",),
     "데스크톱은 켜져 있지만 SSH 서버가 받지 않습니다. 데스크톱에서 관리자 PowerShell 로 "
     "scripts\\remote_setup_desktop.ps1 을 실행했는지 확인하십시오."),
    (("permission denied", "too many authentication failures"),
     "로그인에 실패했습니다. remote_user 가 데스크톱의 윈도우 사용자 이름과 같은지, 노트북 키를 데스크톱에 "
     "등록했는지 확인하십시오(connect_desktop.cmd setup 이 등록 명령을 알려 줍니다). 키가 없으면 "
     "데스크톱 윈도우 비밀번호로도 들어갈 수 있습니다."),
    (("host key verification failed", "remote host identification has changed"),
     "데스크톱의 SSH 신원이 바뀌었습니다. 데스크톱을 새로 설치했다면 %USERPROFILE%\\.ssh\\known_hosts 에서 "
     "그 데스크톱 줄을 지우고 다시 연결하십시오. 새로 설치한 적이 없다면 연결하지 마십시오."),
    (("address already in use", "cannot listen to port", "could not request local forwarding"),
     "노트북의 {port} 번 포트를 다른 프로그램이 쓰고 있습니다. 다른 연결 창이 열려 있는지 보거나, "
     "jarvis.local.json 의 remote_local_port 를 바꾸십시오."),
)


class RemoteError(RuntimeError):
    """사용자에게 그대로 보여 줘도 되는 원격 연결 실패."""


def validate_target(host: str, user: str) -> tuple[str, str]:
    host, user = (host or "").strip(), (user or "").strip()
    if not HOST_RE.fullmatch(host):
        raise RemoteError(f"데스크톱 이름이 올바르지 않습니다: {host!r}. Tailscale 기기 이름이나 100.x.x.x 주소를 쓰십시오.")
    if not USER_RE.fullmatch(user):
        raise RemoteError(f"사용자 이름이 올바르지 않습니다: {user!r}. 데스크톱의 윈도우 사용자 이름을 쓰십시오.")
    return host, user


def key_path() -> Path:
    return Path.home() / ".ssh" / KEY_NAME


def ssh_command(ssh: str, host: str, user: str, local_port: int, alex_port: int,
                identity: Path | None, log_file: Path | None = None) -> list[str]:
    """고정된 인자 목록. 셸을 거치지 않는다(ADR-044 와 같은 규칙)."""
    host, user = validate_target(host, user)
    command = [
        ssh, "-N",
        "-o", "ExitOnForwardFailure=yes",          # 포트를 못 열면 떠 있는 척하지 말고 끝난다
        "-o", "ServerAliveInterval=30",            # 와이파이가 끊기면 1분 반 안에 알아챈다
        "-o", "ServerAliveCountMax=3",
        "-o", "ConnectTimeout=15",
        "-o", "StrictHostKeyChecking=accept-new",  # 처음 보는 데스크톱은 기억하고, 바뀐 신원은 거부한다
        # 127.0.0.1 로 명시한다 — 카페 와이파이의 다른 사람은 노트북의 이 포트에 붙지 못한다.
        "-L", f"127.0.0.1:{local_port}:127.0.0.1:{alex_port}",
    ]
    if identity is not None and identity.exists():
        command += ["-i", str(identity), "-o", "IdentitiesOnly=yes"]
    if log_file is not None:
        command += ["-E", str(log_file)]           # 오류는 파일로 받아 한국어로 풀어 준다
    command.append(f"{user}@{host}")
    return command


def diagnose(log_text: str, returncode: int | None, local_port: int) -> str:
    text = (log_text or "").lower()
    for needles, advice in DIAGNOSES:
        if any(needle in text for needle in needles):
            return advice.format(port=local_port)
    last = next((line.strip() for line in reversed((log_text or "").splitlines()) if line.strip()), "")
    detail = f" ssh 마지막 기록: {last}" if last else ""
    return f"데스크톱에 연결하지 못했습니다(ssh 종료 코드 {returncode}).{detail}"


def port_open(port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def read_health(port: int, timeout: float = 5.0) -> dict:
    """터널 너머가 정말 ALEX 인지. 프록시 환경변수를 따르지 않는다 — 127.0.0.1 로 곧장 간다."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise RemoteError(f"ALEX 응답 없음: {type(exc).__name__}") from None
    if not isinstance(data, dict) or data.get("app") != "jarvis":
        raise RemoteError("그 포트에서 응답한 것은 ALEX 가 아닙니다.")
    if not data.get("local_only"):
        raise RemoteError("데스크톱 ALEX 가 로컬 전용 모드가 아닙니다. 데스크톱에서 ALEX 를 런처로 다시 켜십시오.")
    return data


def version_note(health: dict) -> str:
    code = health.get("code")
    if not code:
        return "주의: 데스크톱 ALEX 가 옛 버전입니다(코드 지문이 없음). 이 노트북의 새 기능이 없을 수 있습니다."
    if code != code_fingerprint():
        return ("주의: 데스크톱 ALEX 와 이 노트북의 코드가 다릅니다. 현재 연결에서는 데스크톱 코드가 "
                "실행되고 대화도 데스크톱에 저장됩니다. 노트북 코드 수정은 자동 반영되지 않습니다. "
                "데스크톱을 기준으로 쓴다면 코드는 데스크톱 프로젝트에서 수정하십시오.")
    return "데스크톱 ALEX 와 이 노트북의 코드가 같습니다."


def _wait_for_health(port: int, process, seconds: float) -> dict:
    """데스크톱 ALEX 가 막 켜지는 중일 수 있어 잠깐 더 기다린다."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            return read_health(port)
        except RemoteError:
            if time.monotonic() >= deadline or process.poll() is not None:
                raise
            time.sleep(1.0)


def connect(settings: Settings, *, open_browser=webbrowser.open, popen=subprocess.Popen,
            which=shutil.which, log_dir: Path | None = None, wait_limit: float = 120.0,
            health_wait: float = 10.0, out=print) -> int:
    host, user = settings.remote_host.strip(), settings.remote_user.strip()
    if not host or not user:
        out(SETUP_HELP)
        return 2
    try:
        validate_target(host, user)
    except RemoteError as exc:
        out(str(exc))
        return 2
    local, alex = settings.remote_local_port, settings.remote_alex_port
    url = f"http://127.0.0.1:{local}"

    if port_open(local):
        # 다른 연결 창이 이미 열려 있으면 그것을 쓴다. ALEX 가 아니면 쓰지 않는다.
        try:
            health = read_health(local)
        except RemoteError:
            out(DIAGNOSES[-1][1].format(port=local))
            return 1
        out("이미 연결돼 있습니다. 브라우저를 엽니다.")
        out(version_note(health))
        open_browser(url)
        return 0

    ssh = which("ssh")
    if not ssh:
        out(NO_SSH_HELP)
        return 2
    log_dir = log_dir or ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / LOG_NAME
    log_file.write_text("", encoding="utf-8")
    command = ssh_command(ssh, host, user, local, alex, key_path(), log_file)

    out(f"{user}@{host} 에 연결합니다… (키를 등록하지 않았다면 데스크톱 윈도우 비밀번호를 묻습니다)")
    process = popen(command)
    deadline = time.monotonic() + wait_limit
    while not port_open(local):
        if process.poll() is not None:
            out(diagnose(log_file.read_text(encoding="utf-8", errors="replace"), process.returncode, local))
            return 1
        if time.monotonic() >= deadline:
            process.terminate()
            out(f"{int(wait_limit)}초 안에 연결되지 않았습니다. "
                + diagnose(log_file.read_text(encoding="utf-8", errors="replace"), None, local))
            return 1
        time.sleep(0.3)

    try:
        health = _wait_for_health(local, process, health_wait)
    except RemoteError:
        process.terminate()
        out(DESKTOP_ALEX_DOWN)
        return 1

    out(f"연결됐습니다. 데스크톱 ALEX: {url}")
    out(version_note(health))
    open_browser(url)
    out("이 창을 닫으면 연결이 끊깁니다. 대화는 데스크톱에 저장됩니다.")
    try:
        process.wait()
    except KeyboardInterrupt:
        process.terminate()
        return 0
    if process.returncode:
        out("연결이 끊겼습니다. " + diagnose(log_file.read_text(encoding="utf-8", errors="replace"),
                                         process.returncode, local))
        return 1
    out("연결이 끊겼습니다.")
    return 0


def save_target(host: str, user: str, *, path: Path | None = None) -> Path:
    """웹 설정 저장과 같은 방식 — 검증 → 임시 파일 → 원자적 교체. 원래 파일은 .bak 으로 남긴다."""
    host, user = validate_target(host, user)
    candidate = validate(replace(load_settings(), remote_host=host, remote_user=user))
    path = path or local_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak-remote-" + time.strftime("%Y%m%d-%H%M%S")))
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps({k: getattr(candidate, k) for k in LOCAL_FIELDS},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def setup(host: str, user: str, *, run=subprocess.run, which=shutil.which,
          config_path: Path | None = None, key: Path | None = None, out=print) -> int:
    try:
        host, user = validate_target(host, user)
    except RemoteError as exc:
        out(str(exc))
        return 2
    key = key or key_path()
    public = key.with_name(key.name + ".pub")
    if not key.exists():
        keygen = which("ssh-keygen")
        if not keygen:
            out(NO_SSH_HELP)
            return 2
        key.parent.mkdir(parents=True, exist_ok=True)
        # 암호 없는 키다 — 한 번 누르면 붙게 하려고. 노트북을 잃어버리면 데스크톱에서 이 키 줄을 지우거나
        # Tailscale 관리 화면에서 노트북을 빼면 된다(REMOTE_SETUP.md).
        # 설명(-C)은 ASCII 로 고정한다. 컴퓨터 이름이 한글이면 데스크톱의 키 파일(ASCII)에서 깨진다.
        run([keygen, "-t", "ed25519", "-f", str(key), "-N", "", "-C", "alex-laptop"], check=True)
        out(f"키를 만들었습니다: {key}")
    else:
        out(f"이미 있는 키를 씁니다: {key}")
    saved = save_target(host, user, path=config_path)
    out(f"연결 대상을 저장했습니다: {user}@{host} ({saved})")
    public_text = public.read_text(encoding="utf-8").strip()
    out("")
    out("데스크톱에서 관리자 PowerShell 을 열고, ALEX 폴더에서 아래 한 줄을 실행하십시오")
    out("(이미 remote_setup_desktop.ps1 을 실행했어도 다시 실행하면 키만 더 등록합니다):")
    out("")
    out(f'  powershell -ExecutionPolicy Bypass -File scripts\\remote_setup_desktop.ps1 -PublicKey "{public_text}"')
    out("")
    out("그다음부터는 connect_desktop.cmd 를 두 번 클릭하면 됩니다.")
    return 0


def check(settings: Settings, *, which=shutil.which, out=print) -> int:
    """연결 전에 무엇이 준비됐는지. 데스크톱의 22번 포트까지 닿는지도 본다."""
    ok = True
    host, user = settings.remote_host.strip(), settings.remote_user.strip()
    out(f"연결 대상: {user + '@' + host if host and user else '(설정 안 됨 — connect_desktop.cmd setup)'}")
    ok &= bool(host and user)
    ssh = which("ssh")
    out(f"ssh: {ssh or '없음 — OpenSSH 클라이언트를 추가하십시오'}")
    ok &= bool(ssh)
    out(f"노트북 키: {key_path() if key_path().exists() else '없음 — 비밀번호로 접속하게 됩니다'}")
    tailscale = which("tailscale")
    out(f"Tailscale: {'설치됨' if tailscale else '명령을 찾지 못함 — 설치했는지 확인하십시오'}")
    if host and user:
        try:
            validate_target(host, user)
            with socket.create_connection((host, 22), timeout=5):
                out(f"데스크톱 SSH(22번): 닿음")
        except RemoteError as exc:
            out(str(exc))
            ok = False
        except OSError as exc:
            out(f"데스크톱 SSH(22번): 닿지 않음 ({type(exc).__name__}) — 데스크톱이 꺼져 있거나 Tailscale·SSH 서버가 꺼져 있습니다.")
            ok = False
    out(f"노트북 쪽 포트 {settings.remote_local_port}: {'사용 중' if port_open(settings.remote_local_port) else '비어 있음'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(prog="python -m jarvis.remote", description="데스크톱 ALEX 에 연결 (ADR-052)")
    sub = parser.add_subparsers(dest="command")
    p_setup = sub.add_parser("setup", help="처음 한 번: 키를 만들고 연결 대상을 저장")
    p_setup.add_argument("--host", required=True, help="데스크톱의 Tailscale 기기 이름 또는 100.x.x.x")
    p_setup.add_argument("--user", required=True, help="데스크톱의 윈도우 사용자 이름")
    sub.add_parser("check", help="준비 상태 확인")
    sub.add_parser("connect", help="연결(기본)")
    args = parser.parse_args(argv)
    os.chdir(ROOT)
    if args.command == "setup":
        return setup(args.host, args.user)
    settings = load_settings()
    if args.command == "check":
        return check(settings)
    return connect(settings)


if __name__ == "__main__":
    raise SystemExit(main())
