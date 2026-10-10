"""Small, explicit device-control primitives.

v0.2.0 only includes Wake-on-LAN. It is disabled by default and uses one
preconfigured MAC address; natural-language model output cannot choose a host.
"""
from __future__ import annotations

import re
import socket


class DeviceError(RuntimeError):
    pass


def normalize_mac(mac: str) -> str:
    compact = re.sub(r"[:-]", "", mac.strip()).upper()
    if not re.fullmatch(r"[0-9A-F]{12}", compact):
        raise DeviceError("WOL MAC 주소 형식이 올바르지 않습니다. 예: AA:BB:CC:DD:EE:FF")
    return compact


def magic_packet(mac: str) -> bytes:
    raw = bytes.fromhex(normalize_mac(mac))
    return b"\xff" * 6 + raw * 16


class WakeOnLanTool:
    """Send one standard WOL magic packet to a configured target."""

    def __init__(self, mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> None:
        self.mac = normalize_mac(mac)
        self.broadcast = broadcast.strip()
        self.port = int(port)
        if not self.broadcast:
            raise DeviceError("WOL broadcast 주소가 비어 있습니다.")
        if self.port < 1 or self.port > 65535:
            raise DeviceError("WOL port는 1~65535 범위여야 합니다.")

    def run(self) -> str:
        packet = magic_packet(self.mac)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.sendto(packet, (self.broadcast, self.port))
        except OSError as exc:
            raise DeviceError(f"WOL packet 전송 실패: {exc}") from exc
        masked = f"**:**:**:**:{self.mac[-4:-2]}:{self.mac[-2:]}"
        return f"WOL magic packet 전송 완료: {masked} -> {self.broadcast}:{self.port}"

class VSCodeLauncher:
    """Launch VS Code for one approved project directory without a shell."""

    def __init__(self, project_root, executable: str = "code") -> None:
        from pathlib import Path
        self.project_root = Path(project_root).resolve()
        self.executable = executable.strip() or "code"

    def run(self) -> str:
        import subprocess
        if not self.project_root.is_dir():
            raise DeviceError(f"VS Code project root가 없습니다: {self.project_root}")
        try:
            subprocess.Popen(
                [self.executable, "."],
                cwd=self.project_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            raise DeviceError(f"VS Code 실행 실패: {exc}") from exc
        return f"VS Code 실행 요청 완료: {self.project_root}"
