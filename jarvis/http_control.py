"""Interrupt a stalled urllib response body when a local request is cancelled.

Only the socket owned by this HTTP response is interrupted. Connection/header
setup still uses the caller's existing timeout; no unrelated process is stopped.
"""
from contextlib import contextmanager
import http.client
import os
import socket
import threading
import time
import urllib.request


class RequestControl:
    """요청 전용 취소·기한. 부모 신호나 다른 요청의 설정은 바꾸지 않는다."""

    def __init__(self, *events, deadline=None):
        self.events = [e for e in events if e is not None]
        self.deadline = deadline

    @property
    def cancelled(self):
        return any(e.cancelled if isinstance(e, RequestControl) else e.is_set() for e in self.events)

    @property
    def expired(self):
        return ((self.deadline is not None and time.monotonic() >= self.deadline)
                or any(e.expired for e in self.events if isinstance(e, RequestControl)))

    def is_set(self):
        return self.cancelled or self.expired

    def set(self):
        for event in self.events:
            event.set()

    def wait(self, timeout):
        end = time.monotonic() + timeout
        while not self.is_set() and time.monotonic() < end:
            time.sleep(min(0.02, max(0, end - time.monotonic())))
        return self.is_set()


@contextmanager
def open_interruptible(request, *, timeout, cancel_event):
    """로컬 모델 요청의 헤더 대기부터 본문까지, 이 연결의 소켓만 끊는다."""
    sockets, done = [], threading.Event()

    class Connection(http.client.HTTPConnection):
        def connect(self):
            super().connect()
            sockets.append(self.sock)
            if cancel_event.is_set():
                _cut(self.sock)

    class SecureConnection(http.client.HTTPSConnection):
        def connect(self):
            super().connect()
            sockets.append(self.sock)
            if cancel_event.is_set():
                _cut(self.sock)

    class HTTP(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(Connection, req)

    class HTTPS(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(SecureConnection, req, context=self._context, check_hostname=self._check_hostname)

    def watch():
        while not done.wait(0.02):
            if cancel_event.is_set():
                for sock in list(sockets):
                    _cut(sock)
                return

    watcher = threading.Thread(target=watch, name='alex-model-request-control', daemon=True)
    watcher.start()
    try:
        with urllib.request.build_opener(HTTP(), HTTPS()).open(request, timeout=timeout) as response:
            yield response
    finally:
        done.set()
        watcher.join(timeout=0.2)


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
