"""Explicit local web-server lifecycle commands.

Use ``python -m jarvis.lifecycle status|start|stop|restart``. Ollama is an
independent service and is deliberately left running by stop/restart: an
existing Ollama process may belong to another application.
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request

from .config import load_settings
from .launcher import instance_id, launch, web_url


class LifecycleError(RuntimeError):
    pass


def health(base_url: str | None = None) -> dict | None:
    base_url = base_url or web_url(load_settings())
    try:
        with urllib.request.urlopen(base_url + "/health", timeout=3) as response:
            result = json.load(response)
        return result if isinstance(result, dict) else None
    except (OSError, ValueError):
        return None


def _verify_own_server(result: dict, expected_instance: str) -> None:
    if result.get("app") != "jarvis" or result.get("instance") != expected_instance:
        raise LifecycleError("이 포트의 서버는 현재 ALEX 인스턴스가 아닙니다. 종료하지 않았습니다.")


def stop_server(base_url: str | None = None, *, expected_instance: str | None = None,
                timeout: float = 30) -> bool:
    """Ask the authenticated web server to shut down; refuse while it is busy."""
    base_url = base_url or web_url(load_settings())
    result = health(base_url)
    if result is None:
        return False
    expected = expected_instance or instance_id(load_settings().data_dir)
    _verify_own_server(result, expected)
    with urllib.request.urlopen(base_url + "/", timeout=5) as response:
        page = response.read().decode("utf-8")
    match = re.search(r'<meta name="jarvis-token" content="([A-Za-z0-9_-]+)">', page)
    if not match:
        raise LifecycleError("웹 인증 토큰을 찾지 못해 종료하지 않았습니다.")
    request = urllib.request.Request(
        base_url + "/api/shutdown", data=b"{}", method="POST",
        headers={"Content-Type": "application/json", "X-Jarvis-Token": match.group(1)})
    try:
        # An owned image/video engine has a bounded 10-second stop/reap budget.
        with urllib.request.urlopen(request, timeout=15) as response:
            answer = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            raise LifecycleError("ALEX가 작업 중이어서 종료를 거절했습니다. 작업이 끝난 뒤 다시 시도하세요.") from exc
        raise LifecycleError(f"종료 요청이 HTTP {exc.code} 오류로 실패했습니다.") from exc
    if answer.get("stopped") is not True:
        raise LifecycleError("웹 서버가 종료 확인을 보내지 않았습니다.")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = health(base_url)
        if current is None:
            return True
        _verify_own_server(current, expected)
        time.sleep(0.2)
    raise LifecycleError("종료 요청 후에도 웹 서버가 실행 중입니다.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ALEX local web lifecycle")
    parser.add_argument("action", choices=("status", "start", "stop", "restart"))
    args = parser.parse_args(argv)
    try:
        if args.action == "status":
            result = health()
            if result is None:
                print("ALEX 웹 서버: 꺼짐")
            else:
                _verify_own_server(result, instance_id(load_settings().data_dir))
                print(f"ALEX 웹 서버: 실행 중 (코드 {result.get('code', '?')})")
            return 0
        if args.action in ("stop", "restart"):
            stopped = stop_server()
            print("ALEX 웹 서버: 종료 완료" if stopped else "ALEX 웹 서버: 이미 꺼짐")
        if args.action in ("start", "restart"):
            launch(allow_download=False, report=print)
            print("ALEX 웹 서버: 실행 중. 기존 브라우저 탭은 F5로 새로고침하세요.")
        if args.action in ("stop", "restart"):
            print("Ollama는 별도 서비스이므로 계속 실행됩니다.")
        return 0
    except (LifecycleError, RuntimeError, OSError) as exc:
        print(f"ALEX lifecycle 오류: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
