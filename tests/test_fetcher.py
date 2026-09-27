"""T-09: private / metadata addresses are refused, including via redirects and DNS."""

import pytest

from hunter.domain.entities import FetchStatus
from hunter.infrastructure.fetcher import MAX_BYTES, SafeHttpFetcher, html_to_text, is_public


class FakeResponse:
    def __init__(self, status=200, headers=None, body=b""):
        self.status = status
        self._headers = headers or {}
        self._body = body

    def getheaders(self):
        return list(self._headers.items())

    def read(self, limit):
        return self._body[:limit]


class FakeConnections:
    """Connection factory keyed by host; records which IP each request was pinned to."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, scheme, host, ip, port, timeout):
        factory = self

        class Conn:
            def request(self, method, path, headers):
                factory.calls.append((host, ip, path))

            def getresponse(self):
                return factory.responses[host]

            def close(self):
                pass

        return Conn()


def resolver(table):
    return lambda host, port: table[host]


HTML = {"content-type": "text/html; charset=utf-8"}


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.1",
        "169.254.169.254",
        "::1",
        "::ffff:10.0.0.1",
        "0.0.0.0",
    ],
    ids=["loopback", "private10", "private192", "metadata", "v6-loopback", "v4-mapped", "zero"],
)
def test_internal_addresses_denied(ip):
    fetcher = SafeHttpFetcher(resolver({"evil.example": [ip]}), FakeConnections({}))
    result = fetcher.fetch("https://evil.example/")
    assert result.status is FetchStatus.DENIED and result.error_code == "private_address"


def test_any_private_answer_in_dns_denies():
    fetcher = SafeHttpFetcher(
        resolver({"mixed.example": ["93.184.216.34", "10.1.1.1"]}), FakeConnections({})
    )
    assert fetcher.fetch("https://mixed.example/").status is FetchStatus.DENIED


def test_redirect_to_internal_host_denied():
    conns = FakeConnections(
        {"good.example": FakeResponse(302, {"location": "http://internal.example/admin"})}
    )
    fetcher = SafeHttpFetcher(
        resolver({"good.example": ["93.184.216.34"], "internal.example": ["10.0.0.7"]}), conns
    )
    result = fetcher.fetch("https://good.example/")
    assert result.status is FetchStatus.DENIED and result.url == "http://internal.example/admin"
    assert [c[0] for c in conns.calls] == ["good.example"]  # never connected to the internal host


@pytest.mark.parametrize(
    "url",
    ["ftp://site.example/", "file:///etc/passwd", "https://user:pw@site.example/"],
    ids=["ftp", "file", "userinfo"],
)
def test_bad_urls_denied(url):
    fetcher = SafeHttpFetcher(resolver({}), FakeConnections({}))
    assert fetcher.fetch(url).status is FetchStatus.DENIED


def test_success_pins_ip_and_extracts_text():
    page = b"<html><script>steal()</script><h1>Autoservice</h1><p>Two new branches</p></html>"
    conns = FakeConnections({"site.example": FakeResponse(200, HTML, page)})
    fetcher = SafeHttpFetcher(resolver({"site.example": ["93.184.216.34"]}), conns)
    result = fetcher.fetch("https://site.example/")
    assert result.status is FetchStatus.FETCHED
    assert result.text == "Autoservice\nTwo new branches"
    assert conns.calls == [("site.example", "93.184.216.34", "/")]


def test_wrong_type_and_oversize_denied():
    conns = FakeConnections(
        {
            "pdf.example": FakeResponse(200, {"content-type": "application/pdf"}, b"%PDF"),
            "big.example": FakeResponse(200, HTML, b"x" * (MAX_BYTES + 10)),
        }
    )
    table = {"pdf.example": ["93.184.216.34"], "big.example": ["93.184.216.34"]}
    fetcher = SafeHttpFetcher(resolver(table), conns)
    assert fetcher.fetch("https://pdf.example/").error_code == "content_type_not_allowed"
    assert fetcher.fetch("https://big.example/").error_code == "too_large"


def test_http_error_and_dns_failure_are_explicit():
    def failing(host, port):
        raise OSError("no such host")

    conns = FakeConnections({"gone.example": FakeResponse(404)})
    assert (
        SafeHttpFetcher(resolver({"gone.example": ["93.184.216.34"]}), conns)
        .fetch("https://gone.example/")
        .error_code
        == "http_404"
    )
    assert SafeHttpFetcher(failing, conns).fetch("https://x.example/").error_code == "dns_failed"


def test_is_public():
    assert is_public("93.184.216.34")
    assert not is_public("172.16.0.1")


def test_html_to_text_skips_styles():
    assert html_to_text("<style>a{}</style><p> a   b </p>") == "a b"
