"""Interrupt a stalled urllib response body when a local request is cancelled.

Only the socket owned by this HTTP response is interrupted. Connection/header
setup still uses the caller's existing timeout; no unrelated process is stopped.
"""
from contextlib import contextmanager
import os
import socket
import threading


def _cut(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    # Windows 에서는 shutdown 이 다른 스레드의 막힌 recv 를 풀지 않는다(2026-09-25 실측: 5초 그대로 대기).
    # urllib 은 소켓을 makefile 로 감싸 두어 close() 도 참조가 남아 미뤄진다. 실제로 닫아야(_real_close)
    # 0.3초 만에 풀린다 — 이 응답 전용 소켓이라 닫아도 다른 연결에는 영향이 없다.
    if os.name == "nt":
        try:
            sock._real_close()
        except (AttributeError, OSError):
            pass


@contextmanager
def interrupt_on_cancel(response, cancel_event):
    # CPython's urllib HTTPResponse exposes the body socket through SocketIO.
    # In-memory test responses and already-closed bodies need no watcher.
    sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
    if cancel_event is None or not isinstance(sock, socket.socket):
        yield response
        return
    done = threading.Event()

    def watch():
        while not done.wait(0.05):
            if cancel_event.is_set():
                _cut(sock)
                return

    worker = threading.Thread(target=watch, name='alex-http-cancel', daemon=True)
    worker.start()
    try:
        yield response
    finally:
        done.set()
        worker.join(timeout=0.2)
