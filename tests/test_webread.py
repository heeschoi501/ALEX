"""웹 읽기 모듈 (ADR-051). 인터넷에 나가지 않는다.

가장 중요한 것은 **로컬·사설 주소 차단**이다. 웹 페이지가 `http://127.0.0.1:11434` 를
가리키게 하면, 막지 않는 한 ALEX 가 이 기계의 Ollama 를 대신 읽어서 모델에게 넘긴다.
그래서 차단은 가짜로 흉내 내지 않고 **실제로 127.0.0.1 에 서버를 띄워** 증명한다 —
서버는 살아 있고, 평범한 urllib 로는 닿고, ALEX 의 opener 로는 안 닿아야 한다.
"""
import email.message
import gzip
import http.server
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.webread import (  # noqa: E402
    SEARCH_BLOCK_BACKOFF, SEARCH_CACHE_SECONDS, WebError, WebReader, _RedirectHandler, check_url, extract_text,
    guarded_opener, is_public_address, parse_search_results,
)


def public_resolver(host, port, type=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def resolver_to(*ips):
    def resolve(host, port, type=0):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 (ip, port)) for ip in ips]
    return resolve


class FakeResponse:
    def __init__(self, body: bytes, content_type="text/html; charset=utf-8",
                 url="https://example.com/", content_encoding=""):
        self._body = body
        self._url = url
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type
        if content_encoding:
            self.headers["Content-Encoding"] = content_encoding

    def read(self, n=-1):
        return self._body if n is None or n < 0 else self._body[:n]

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def reader_returning(response=None, error=None, **kwargs):
    def open_func(request, timeout=None):
        if error is not None:
            raise error
        return response
    return WebReader(open_func=open_func, resolver=public_resolver, **kwargs)


class PublicAddressTests(unittest.TestCase):
    def test_private_and_local_addresses_are_not_public(self):
        for ip in ("127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.0.1", "169.254.169.254",
                   "0.0.0.0", "100.64.0.1", "::1", "fe80::1", "::ffff:127.0.0.1",
                   "::ffff:192.168.0.1", "224.0.0.1", "not-an-ip"):
            self.assertFalse(is_public_address(ip), ip)

    def test_public_addresses_pass(self):
        for ip in ("8.8.8.8", "93.184.216.34", "2606:4700:4700::1111"):
            self.assertTrue(is_public_address(ip), ip)


class CheckUrlTests(unittest.TestCase):
    def test_only_http_and_https(self):
        for url in ("file:///C:/Windows/win.ini", "ftp://example.com/", "data:text/plain,hi",
                    "javascript:alert(1)", "example.com/no-scheme"):
            with self.assertRaises(WebError, msg=url):
                check_url(url, public_resolver)

    def test_credentials_in_url_are_refused(self):
        with self.assertRaises(WebError):
            check_url("http://user:pw@example.com/", public_resolver)

    def test_bad_port_is_refused(self):
        with self.assertRaises(WebError):
            check_url("http://example.com:99999/", public_resolver)

    def test_a_name_that_resolves_to_a_private_address_is_refused(self):
        """공개 도메인처럼 보여도 DNS 가 사설 주소를 주면 막는다."""
        with self.assertRaises(WebError):
            check_url("http://innocent.example/", resolver_to("192.168.0.1"))

    def test_every_resolved_address_must_be_public(self):
        with self.assertRaises(WebError):
            check_url("http://mixed.example/", resolver_to("93.184.216.34", "127.0.0.1"))

    def test_public_url_passes(self):
        self.assertEqual(check_url(" https://example.com/docs ", public_resolver),
                         "https://example.com/docs")


class _Quiet(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><body><p>secret local service</p></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ConnectionGuardTests(unittest.TestCase):
    """`check_url` 을 건너뛰어도 연결 단계에서 막히는지. DNS 리바인딩 방어가 여기다."""

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), _Quiet)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_the_local_server_is_really_reachable(self):
        """이게 통과해야 아래 차단이 '서버가 없어서' 가 아니라 '막아서' 임이 증명된다."""
        with urllib.request.urlopen(self.url, timeout=5) as response:
            self.assertIn(b"secret local service", response.read())

    def test_guarded_opener_refuses_at_the_socket(self):
        with self.assertRaises(WebError):
            guarded_opener().open(self.url, timeout=5)

    def test_reader_refuses_before_connecting(self):
        with self.assertRaises(WebError):
            WebReader().fetch(self.url)

    def test_file_and_ftp_handlers_are_not_loaded(self):
        for url in ("file:///C:/Windows/win.ini", "ftp://example.com/x"):
            with self.assertRaises(urllib.error.URLError, msg=url):
                guarded_opener().open(url, timeout=5)

    def test_redirect_to_a_non_web_scheme_is_refused(self):
        request = urllib.request.Request("https://example.com/")
        with self.assertRaises(WebError):
            _RedirectHandler().redirect_request(request, None, 302, "Found", {},
                                                "file:///C:/Windows/win.ini")


class ExtractTextTests(unittest.TestCase):
    def test_script_style_nav_footer_are_dropped(self):
        title, body = extract_text(
            "<html><head><title>Doc</title><style>.x{}</style></head><body>"
            "<nav>MENU</nav><script>evil()</script><p>Real content here.</p>"
            "<footer>COPYRIGHT</footer></body></html>")
        self.assertEqual(title, "Doc")
        self.assertIn("Real content here.", body)
        for junk in ("MENU", "evil()", "COPYRIGHT", ".x{}"):
            self.assertNotIn(junk, body)

    def test_code_indentation_in_pre_survives(self):
        _, body = extract_text("<body><pre>always @(posedge clk)\n    q &lt;= d;</pre></body>")
        self.assertIn("    q <= d;", body)

    def test_source_line_breaks_do_not_split_sentences(self):
        _, body = extract_text("<body><p>Icarus Verilog is\n      a simulator.</p></body>")
        self.assertIn("Icarus Verilog is a simulator.", body)

    def test_main_is_preferred_when_substantial(self):
        main = "<p>" + "Datasheet paragraph. " * 40 + "</p>"
        _, body = extract_text(f"<body><div>SIDEBAR LINKS</div><main>{main}</main></body>")
        self.assertNotIn("SIDEBAR LINKS", body)
        self.assertIn("Datasheet paragraph.", body)

    def test_html5_page_without_closing_head_keeps_its_body(self):
        """HTML5 는 </head> 를 생략할 수 있다. head 를 건너뛰는 규칙이었다면 본문이 통째로 사라진다."""
        _, body = extract_text("<html><head><title>T</title><body><p>Body survives.</p>")
        self.assertIn("Body survives.", body)

    def test_unclosed_nav_does_not_swallow_the_page(self):
        page = "<body><nav>menu<p>" + "Important text. " * 30 + "</p></body>"
        _, body = extract_text(page)
        self.assertIn("Important text.", body)

    def test_entities_are_decoded(self):
        _, body = extract_text("<body><p>a &amp; b &lt;= c</p></body>")
        self.assertIn("a & b <= c", body)


class FetchTests(unittest.TestCase):
    def test_html_is_reduced_to_text(self):
        page = reader_returning(FakeResponse(
            b"<html><title>T</title><body><p>Hello docs</p></body></html>")).fetch("https://example.com/")
        self.assertEqual(page.title, "T")
        self.assertIn("Hello docs", page.text)

    def test_non_text_documents_are_refused(self):
        with self.assertRaises(WebError):
            reader_returning(FakeResponse(b"%PDF-1.7", "application/pdf")).fetch("https://example.com/a.pdf")

    def test_size_cap_marks_the_page_truncated(self):
        body = b"<body><p>" + b"x" * 5000 + b"</p></body>"
        page = reader_returning(FakeResponse(body), max_bytes=1000).fetch("https://example.com/")
        self.assertTrue(page.truncated)

    def test_charset_from_header(self):
        body = "<body><p>반도체 공정</p></body>".encode("euc-kr")
        page = reader_returning(FakeResponse(body, "text/html; charset=euc-kr")).fetch("https://example.com/")
        self.assertIn("반도체 공정", page.text)

    def test_charset_from_meta_when_header_is_silent(self):
        body = '<html><head><meta charset="euc-kr"></head><body><p>리소그래피</p></body>'.encode("euc-kr")
        page = reader_returning(FakeResponse(body, "text/html")).fetch("https://example.com/")
        self.assertIn("리소그래피", page.text)

    def test_gzip_body_is_unpacked(self):
        body = gzip.compress(b"<body><p>compressed page</p></body>")
        page = reader_returning(FakeResponse(body, content_encoding="gzip")).fetch("https://example.com/")
        self.assertIn("compressed page", page.text)

    def test_plain_text_passes_through(self):
        page = reader_returning(FakeResponse(b"line one\nline two", "text/plain")).fetch("https://example.com/r.txt")
        self.assertEqual(page.text, "line one\nline two")

    def test_http_errors_become_readable(self):
        error = urllib.error.HTTPError("https://example.com/", 404, "Not Found", {}, None)
        with self.assertRaises(WebError) as ctx:
            reader_returning(error=error).fetch("https://example.com/")
        self.assertIn("404", str(ctx.exception))

    def test_network_errors_become_readable(self):
        with self.assertRaises(WebError):
            reader_returning(error=urllib.error.URLError("no route")).fetch("https://example.com/")

    def test_a_page_with_no_text_is_reported_not_answered(self):
        with self.assertRaises(WebError):
            reader_returning(FakeResponse(b"<html><body><script>render()</script></body></html>")).fetch(
                "https://example.com/")

    def test_private_target_never_reaches_open(self):
        calls = []
        reader = WebReader(open_func=lambda *a, **k: calls.append(a), resolver=resolver_to("10.0.0.5"))
        with self.assertRaises(WebError):
            reader.fetch("http://intranet.example/")
        self.assertEqual(calls, [])


LITE_FIXTURE = """
<table>
<tr><td>1.&nbsp;</td><td>
<a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgithub.com%2Fsteveicarus%2Fiverilog%2Freleases&amp;rut=a" class='result-link'>Releases · steveicarus/iverilog</a>
</td></tr>
<tr><td>&nbsp;</td><td class='result-snippet'>
    Icarus Verilog. Contribute to steveicarus/<b>iverilog</b> development.
</td></tr>
<tr><td>2.&nbsp;</td><td>
<a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fduckduckgo.com%2Fy.js%3Fad_domain%3Dshop.example&amp;rut=b" class='result-link'>Sponsored thing</a>
</td></tr>
<tr><td>3.&nbsp;</td><td>
<a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fsteveicarus.github.io%2Fiverilog%2F&amp;rut=c" class='result-link'>Icarus Verilog documentation</a>
</td></tr>
<tr><td>&nbsp;</td><td class='result-snippet'>Welcome to the documentation.</td></tr>
<tr><td>4.&nbsp;</td><td>
<a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgithub.com%2Fsteveicarus%2Fiverilog%2Freleases&amp;rut=d" class='result-link'>Duplicate</a>
</td></tr>
<tr><td>5.&nbsp;</td><td>
<a rel="nofollow" href="//duckduckgo.com/l/?uddg=javascript%3Aalert(1)&amp;rut=e" class='result-link'>Bad scheme</a>
</td></tr>
</table>
"""


class SearchParseTests(unittest.TestCase):
    def test_redirect_links_are_unwrapped(self):
        results = parse_search_results(LITE_FIXTURE, 10)
        self.assertEqual(results[0].url, "https://github.com/steveicarus/iverilog/releases")
        self.assertEqual(results[0].title, "Releases · steveicarus/iverilog")
        self.assertIn("Contribute to steveicarus/ iverilog", results[0].snippet)

    def test_ads_duplicates_and_bad_schemes_are_dropped(self):
        urls = [r.url for r in parse_search_results(LITE_FIXTURE, 10)]
        self.assertEqual(urls, ["https://github.com/steveicarus/iverilog/releases",
                                "https://steveicarus.github.io/iverilog/"])

    def test_limit(self):
        self.assertEqual(len(parse_search_results(LITE_FIXTURE, 1)), 1)

    def test_search_reports_a_bot_block_instead_of_answering(self):
        reader = reader_returning(FakeResponse(b"<html><div class='anomaly-modal'>are you human</div></html>"))
        with self.assertRaises(WebError) as ctx:
            reader.search("iverilog")
        self.assertIn("막았습니다", str(ctx.exception))

    def test_search_with_no_results_is_an_error_not_an_empty_answer(self):
        with self.assertRaises(WebError):
            reader_returning(FakeResponse(b"<html><body>nothing</body></html>")).search("zzzz")

    def test_empty_query_is_refused(self):
        with self.assertRaises(WebError):
            reader_returning(FakeResponse(b"")).search("   ")

    def test_search_returns_parsed_results(self):
        results = reader_returning(FakeResponse(LITE_FIXTURE.encode("utf-8"))).search("iverilog")
        self.assertEqual(len(results), 2)


class SearchPacingTests(unittest.TestCase):
    """같은 검색어는 다시 보내지 않고, 막히면 쉰다 (2026-09-25, ADR-056).

    스스로 검색을 시험하며 몇 분 사이 15번쯤 찾자 검색엔진이 자동 요청으로 막았다. 막힌 채로 계속
    보내면 차단이 길어진다.
    """

    def setUp(self):
        self.now = 1000.0
        self.sent = []
        self.body = LITE_FIXTURE.encode("utf-8")

    def reader(self):
        def open_func(request, timeout=None):
            self.sent.append(request.full_url)
            return FakeResponse(self.body)
        return WebReader(open_func=open_func, resolver=public_resolver, clock=lambda: self.now)

    def test_the_same_query_is_not_sent_again_for_a_while(self):
        reader = self.reader()
        reader.search("iverilog")
        reader.search("  IVERILOG ")
        self.assertEqual(len(self.sent), 1)
        self.now += SEARCH_CACHE_SECONDS + 1
        reader.search("iverilog")
        self.assertEqual(len(self.sent), 2)

    def test_after_a_block_it_waits_instead_of_knocking_again(self):
        self.body = b"<html><div class='anomaly-modal'>are you human</div></html>"
        reader = self.reader()
        with self.assertRaises(WebError):
            reader.search("first")
        with self.assertRaises(WebError) as ctx:
            reader.search("second")
        self.assertEqual(len(self.sent), 1)
        self.assertIn("초 뒤에 다시", str(ctx.exception))
        self.body = LITE_FIXTURE.encode("utf-8")
        self.now += SEARCH_BLOCK_BACKOFF + 1
        self.assertEqual(len(reader.search("second")), 2)

    def test_a_caller_cannot_change_what_is_cached(self):
        reader = self.reader()
        reader.search("iverilog").clear()
        self.assertEqual(len(reader.search("iverilog")), 2)


if __name__ == "__main__":
    unittest.main()
