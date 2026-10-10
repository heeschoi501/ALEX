"""웹 읽기 — 읽기 전용, 사용자가 명시적으로 부를 때만 (ADR-051).

**왜 넣었나.** ALEX 는 모르는 것을 물으면 "확인할 수 없습니다" 에서 멈췄다. 정직하지만
거기서 끝난다. 반도체 개발 보조라면 데이터시트, PDK 문서, 툴 문서, 에러 메시지를
찾아봐야 한다. 이 모듈은 그 "찾아본다" 의 최소 형태다.

**`local_only` 와 부딪히지 않는다.** `local_only` 는 대화와 기억을 클라우드 **LLM** 으로
보내지 않는 경계다(README "Cloud 경계"). 웹 페이지를 읽는 것은 LLM 호출이 아니고
토큰을 쓰지 않는다. 검색어는 검색엔진에 보이지만 대화·기억은 나가지 않는다.

**지키는 것**
- 읽기만 한다. GET 뿐이고 폼 전송·로그인·결제는 없다.
- 사설/로컬 주소로는 나가지 않는다. 이 기계의 Ollama(127.0.0.1:11434), ALEX 자신(8765),
  공유기 관리 화면, 클라우드 메타데이터(169.254.169.254) 를 웹 페이지가 가리키게 해서
  읽어오는 공격(SSRF)을 막는다. **실제로 연결된 소켓의 상대 주소**를 TLS 와 HTTP 전에
  검사하므로, DNS 가 검사 뒤에 답을 바꿔도(리바인딩) 통과하지 못한다.
- 리다이렉트는 http/https 로만, 5번까지. file:// ftp:// 는 처리기 자체를 싣지 않았다.
- 프록시 환경변수를 따르지 않는다. 따르면 연결 상대가 프록시(대개 사설 주소)가 된다.
- 크기(2MB)와 시간(15초)에 상한을 둔다. 텍스트가 아닌 문서(PDF, 이미지)는 읽지 않는다.

**지키지 않는 것 — 호출하는 쪽의 몫**
- 페이지 내용은 **믿을 수 없는 입력**이다. 여기서는 글자만 뽑는다. "지시가 아니라 자료로
  취급하라" 는 표시는 `JarvisCore._build_web_messages` 가 붙인다.
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import http.client
import ipaddress
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ALEX-local-assistant"
ALLOWED_SCHEMES = ("http", "https")
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
MAX_BYTES = 2_000_000
TIMEOUT = 15
MAX_REDIRECTS = 5
# 2026-09-25 실측: html.duckduckgo.com 은 봇 확인(202 + anomaly)을 돌려줬고
# lite.duckduckgo.com 은 결과 10건을 정상으로 돌려줬다. 키가 필요 없다.
SEARCH_URL = "https://lite.duckduckgo.com/lite/?q="
# 2026-09-25: 스스로 검색(ADR-056)을 시험하며 몇 분 사이 15번쯤 찾자 검색엔진이 자동 요청으로 막았다.
# 같은 검색어는 한동안 다시 보내지 않고, 막히면 잠시 쉰다 — 막힌 채로 계속 두드리면 차단이 길어진다.
SEARCH_CACHE_SECONDS = 600
SEARCH_BLOCK_BACKOFF = 300
BLOCKED_MESSAGE = "검색엔진이 자동 요청으로 보고 막았습니다. 잠시 뒤 다시 시도하십시오."


class WebError(RuntimeError):
    """사용자에게 그대로 보여줘도 되는 웹 읽기 실패."""


@dataclass
class Page:
    url: str
    title: str
    text: str
    truncated: bool = False


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


# ── 주소 검사 ────────────────────────────────────────────────────────────

def is_public_address(ip: str) -> bool:
    """인터넷에서 누구나 닿는 주소인가. 사설·루프백·링크로컬·예약은 전부 거짓."""
    try:
        address = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped  # ::ffff:127.0.0.1 로 돌아 들어오는 경로를 막는다
    return address.is_global and not address.is_multicast


def check_url(url: str, resolver=socket.getaddrinfo) -> str:
    """나가기 전에 한 번 거른다. 진짜 강제는 연결 단계의 `_verify_peer` 다.

    여기서 거르는 이유는 오류 문장을 알아듣게 만들기 위해서다 — 연결 단계에서 막히면
    사용자는 "사설 주소로는 나가지 않는다" 대신 소켓 오류를 보게 된다.
    """
    text = (url or "").strip()
    parts = urllib.parse.urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise WebError(f"http/https 주소만 읽습니다: {text[:80]!r}")
    if not parts.hostname:
        raise WebError(f"주소에 호스트가 없습니다: {text[:80]!r}")
    if parts.username or parts.password:
        raise WebError("주소에 계정 정보가 들어 있어 읽지 않습니다.")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise WebError("주소의 포트가 올바르지 않습니다.") from None
    try:
        infos = resolver(parts.hostname, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise WebError(f"주소를 찾지 못했습니다: {parts.hostname}") from None
    for info in infos:
        ip = info[4][0]
        if not is_public_address(ip):
            raise WebError(f"사설/로컬 주소로는 나가지 않습니다: {parts.hostname} -> {ip}")
    return text


# ── 연결 단계의 강제 ─────────────────────────────────────────────────────

def _verify_peer(sock: socket.socket) -> None:
    peer = sock.getpeername()[0]
    if not is_public_address(peer):
        raise WebError(f"사설/로컬 주소로는 나가지 않습니다: {peer}")


class _GuardMixin:
    """TCP 연결 직후, TLS 핸드셰이크와 HTTP 요청 **전에** 상대 주소를 검사한다.

    `http.client.HTTPConnection.connect` 는 `self._create_connection` 으로 소켓을 연다.
    그것을 감싸면 HTTP 와 HTTPS 가 같은 지점에서 걸린다.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        opener = self._create_connection

        def guarded(address, *a, **kw):
            sock = opener(address, *a, **kw)
            try:
                _verify_peer(sock)
            except BaseException:
                sock.close()
                raise
            return sock

        self._create_connection = guarded


class _GuardedHTTPConnection(_GuardMixin, http.client.HTTPConnection):
    pass


class _GuardedHTTPSConnection(_GuardMixin, http.client.HTTPSConnection):
    pass


class _GuardedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_GuardedHTTPConnection, req)


class _GuardedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_GuardedHTTPSConnection, req, context=self._context)


class _RedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        scheme = urllib.parse.urlsplit(newurl).scheme.lower()
        if scheme not in ALLOWED_SCHEMES:
            raise WebError(f"허용되지 않은 주소로 넘기려 했습니다: {scheme or '(없음)'}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def guarded_opener() -> urllib.request.OpenerDirector:
    """`build_opener` 를 쓰지 않는다. 그것은 file/ftp/data 처리기와 환경변수 프록시를 싣는다."""
    director = urllib.request.OpenerDirector()
    for handler in (
        _GuardedHTTPHandler(),
        _GuardedHTTPSHandler(context=ssl.create_default_context()),
        _RedirectHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
        # 없으면 file:// 같은 주소에 오류 대신 None 을 돌려준다(2026-09-25 테스트로 확인).
        # 읽지는 않지만 조용히 넘어가는 것은 거부가 아니다.
        urllib.request.UnknownHandler(),
    ):
        director.add_handler(handler)
    return director


# ── HTML 에서 글자 뽑기 ──────────────────────────────────────────────────

SKIP_TAGS = frozenset({
    "script", "style", "noscript", "svg", "template", "iframe", "canvas",
    "nav", "footer", "aside", "form", "button", "select",
})
# "head" 는 넣지 않는다. HTML5 는 </head> 를 생략할 수 있어서, 넣으면 그런 페이지의
# 본문 전체가 버려진다. head 안의 script/style 은 위 규칙으로 이미 걸러지고 meta/link 는 글이 없다.
MINIMAL_SKIP = frozenset({"script", "style", "noscript", "svg", "template"})
BLOCK_TAGS = frozenset({
    "p", "div", "section", "article", "main", "header", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "ul", "ol", "tr", "table", "br", "pre", "blockquote", "dd", "dt", "dl",
    "figure", "figcaption", "hr", "td", "th", "caption", "summary", "details",
})
VOID_TAGS = frozenset({"br", "hr", "img", "meta", "link", "input", "area", "base", "col",
                       "embed", "source", "track", "wbr", "param"})
# 소스의 줄바꿈은 의미가 없다(문장 중간에서 끊긴다). 줄은 블록 태그가 만든다.
_WS = re.compile(r"\s+")
_MANY_NEWLINES = re.compile(r"\n{3,}")
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([A-Za-z0-9_\-]+)""", re.I)


class _TextExtractor(HTMLParser):
    def __init__(self, skip: frozenset[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = skip
        self.skip_depth = 0
        self.pre_depth = 0
        self.main_depth = 0
        self.in_title = False
        self.title_parts: list[str] = []
        self.all_parts: list[str] = []
        self.main_parts: list[str] = []

    def _emit(self, text: str) -> None:
        self.all_parts.append(text)
        if self.main_depth:
            self.main_parts.append(text)

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
            return
        if tag in VOID_TAGS:
            if tag in BLOCK_TAGS and not self.skip_depth:
                self._emit("\n")
            return
        if tag in self.skip:
            self.skip_depth += 1
            return
        if tag in ("main", "article"):
            self.main_depth += 1
        if tag == "pre":
            self.pre_depth += 1
        if tag in BLOCK_TAGS and not self.skip_depth:
            self._emit("\n")
        if tag == "li" and not self.skip_depth:
            self._emit("- ")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
            return
        if tag in self.skip:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag in BLOCK_TAGS and not self.skip_depth:
            self._emit("\n")
        if tag == "pre":
            self.pre_depth = max(0, self.pre_depth - 1)
        if tag in ("main", "article"):
            self.main_depth = max(0, self.main_depth - 1)

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
            return
        if self.skip_depth:
            return
        # 코드 블록의 들여쓰기는 의미다. <pre> 안에서는 공백을 건드리지 않는다.
        self._emit(data if self.pre_depth else _WS.sub(" ", data))


def _tidy(parts: list[str]) -> str:
    text = "".join(parts)
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(line if line.strip() else "" for line in lines)
    return _MANY_NEWLINES.sub("\n\n", text).strip()


def extract_text(markup: str) -> tuple[str, str]:
    """(제목, 본문). `<main>`/`<article>` 이 충분히 길면 그것만 쓴다.

    닫는 태그가 빠진 `<nav>` 하나가 나머지 페이지를 통째로 삼킬 수 있다. 그래서 결과가
    지나치게 짧으면 script/style 만 거르는 최소 규칙으로 한 번 더 뽑는다.
    """
    def run(skip: frozenset[str]) -> _TextExtractor:
        parser = _TextExtractor(skip)
        parser.feed(markup)
        parser.close()
        return parser

    parser = run(SKIP_TAGS)
    title = _WS.sub(" ", "".join(parser.title_parts)).strip()
    main = _tidy(parser.main_parts)
    body = main if len(main) >= 400 else _tidy(parser.all_parts)
    if len(body) < 200:
        # 정상적인 짧은 페이지까지 여기로 오면 메뉴·저작권 문구가 되살아난다(테스트로 확인).
        # 걸러낸 쪽이 압도적으로 클 때만 — 즉 닫히지 않은 태그가 본문을 삼켰을 때만 쓴다.
        fallback = _tidy(run(MINIMAL_SKIP).all_parts)
        if len(fallback) >= 200 and len(fallback) > 3 * len(body):
            body = fallback
    return title, body


# ── 검색 결과 파싱 (DuckDuckGo lite) ─────────────────────────────────────

class _LiteResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self.in_link = False
        self.in_snippet = False
        self.buffer: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "a" and "result-link" in classes:
            self.in_link = True
            self.buffer = []
            self.results.append({"href": attributes.get("href") or "", "title": "", "snippet": ""})
        elif tag == "td" and "result-snippet" in classes and self.results:
            self.in_snippet = True
            self.buffer = []

    def handle_endtag(self, tag):
        if tag == "a" and self.in_link:
            self.results[-1]["title"] = _WS.sub(" ", "".join(self.buffer)).strip()
            self.in_link = False
        elif tag == "td" and self.in_snippet:
            self.results[-1]["snippet"] = _WS.sub(" ", " ".join(self.buffer)).strip()
            self.in_snippet = False

    def handle_data(self, data):
        if self.in_link or self.in_snippet:
            self.buffer.append(data)


def _resolve_result_url(href: str) -> str:
    """`//duckduckgo.com/l/?uddg=<실제 주소>` 를 풀어 실제 주소를 돌려준다."""
    if href.startswith("//"):
        href = "https:" + href
    parts = urllib.parse.urlsplit(href)
    if parts.hostname and parts.hostname.endswith("duckduckgo.com"):
        target = urllib.parse.parse_qs(parts.query).get("uddg", [""])[0]
        return target
    return href


def parse_search_results(markup: str, limit: int) -> list[SearchResult]:
    parser = _LiteResultParser()
    parser.feed(markup)
    parser.close()
    results: list[SearchResult] = []
    seen: set[str] = set()
    for item in parser.results:
        url = _resolve_result_url(item["href"])
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ALLOWED_SCHEMES or not parts.hostname:
            continue
        # 광고는 duckduckgo.com/y.js 로 돌아 들어온다. 결과가 아니다.
        if parts.hostname.endswith("duckduckgo.com") or url in seen:
            continue
        seen.add(url)
        results.append(SearchResult(item["title"] or url, url, item["snippet"]))
        if len(results) >= limit:
            break
    return results


# ── 읽기 ────────────────────────────────────────────────────────────────

class WebReader:
    """페이지 읽기와 검색. 테스트는 `open_func` 와 `resolver` 를 바꿔 네트워크 없이 돈다."""

    def __init__(self, *, max_bytes: int = MAX_BYTES, timeout: float = TIMEOUT,
                 open_func=None, resolver=socket.getaddrinfo, max_results: int = 6, clock=time.monotonic) -> None:
        self.max_bytes = max_bytes
        self._clock = clock
        self._cache: dict[str, tuple[float, list[SearchResult]]] = {}
        self._blocked_until = 0.0
        self.timeout = timeout
        self.resolver = resolver
        self.max_results = max_results
        self._open = open_func or guarded_opener().open

    def _get(self, url: str) -> tuple[str, str, str, bool]:
        """(최종 주소, content-type, 글자, 잘렸는가)."""
        check_url(url, self.resolver)
        request = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
            "Accept-Language": "ko,en;q=0.8",
        })
        try:
            with self._open(request, timeout=self.timeout) as response:
                content_type = response.headers.get_content_type()
                if content_type not in TEXT_TYPES:
                    raise WebError(f"글이 아닌 문서는 읽지 않습니다: {content_type}")
                raw = response.read(self.max_bytes + 1)
                final_url = response.geturl() or url
                charset = response.headers.get_content_charset()
                encoding = (response.headers.get("Content-Encoding") or "").lower()
        except WebError:
            raise
        except urllib.error.HTTPError as exc:
            raise WebError(f"페이지가 HTTP {exc.code} 를 돌려줬습니다.") from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, WebError):
                raise reason from None
            raise WebError(f"접속하지 못했습니다: {reason}") from None
        except (TimeoutError, socket.timeout):
            raise WebError(f"{self.timeout:g}초 안에 응답이 없습니다.") from None
        except (http.client.HTTPException, OSError) as exc:
            raise WebError(f"읽는 중 연결이 끊겼습니다: {type(exc).__name__}") from None

        truncated = len(raw) > self.max_bytes
        raw = raw[: self.max_bytes]
        if encoding in ("gzip", "deflate"):
            # 요청에 Accept-Encoding 을 싣지 않지만 그래도 압축해 보내는 서버가 있다.
            try:
                raw = zlib.decompressobj(32 + zlib.MAX_WBITS).decompress(raw, self.max_bytes)
            except zlib.error:
                raise WebError("압축을 풀지 못했습니다.") from None
        if not charset:
            match = _META_CHARSET.search(raw[:4096])
            charset = match.group(1).decode("ascii", "ignore") if match else "utf-8"
        try:
            text = raw.decode(charset, errors="replace")
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
        return final_url, content_type, text, truncated

    def fetch(self, url: str) -> Page:
        final_url, content_type, text, truncated = self._get(url)
        if content_type == "text/plain":
            return Page(final_url, "", text.strip(), truncated)
        title, body = extract_text(text)
        if not body:
            raise WebError("페이지에서 읽을 글을 찾지 못했습니다(스크립트로만 그리는 페이지일 수 있습니다).")
        return Page(final_url, title, body, truncated)

    def search(self, query: str) -> list[SearchResult]:
        query = (query or "").strip()
        if not query:
            raise WebError("검색어가 비어 있습니다.")
        now = self._clock()
        key = " ".join(query.lower().split())
        cached = self._cache.get(key)
        if cached and now - cached[0] < SEARCH_CACHE_SECONDS:
            return list(cached[1])
        if now < self._blocked_until:
            raise WebError(f"{BLOCKED_MESSAGE} (약 {int(self._blocked_until - now) + 1}초 뒤에 다시 찾습니다)")
        _, _, markup, _ = self._get(SEARCH_URL + urllib.parse.quote(query))
        results = parse_search_results(markup, self.max_results)
        if not results:
            if "anomaly" in markup.lower():
                self._blocked_until = now + SEARCH_BLOCK_BACKOFF
                raise WebError(BLOCKED_MESSAGE)
            raise WebError("검색 결과가 없습니다.")
        self._cache[key] = (now, results)
        while len(self._cache) > 200:
            self._cache.pop(next(iter(self._cache)))
        return list(results)
