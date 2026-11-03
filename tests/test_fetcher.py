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


@pytest.mark.parametrize(
    "url",
    [
        "https://site.example:wrong/",
        "https://site.example:99999/",
        "https://[broken/",
        "https://site.example:0/",
        "https://site.example/\r\nX: evil",
        "https://@site.example/",
        "https://u:@site.example/",
    ],
)
def test_malformed_urls_fail_closed_without_connecting(url):
    conns = FakeConnections({})
    result = SafeHttpFetcher(resolver({}), conns).fetch(url)
    assert result.status is FetchStatus.DENIED and not conns.calls


def test_relative_redirect_and_all_redirect_codes_are_checked():
    for status in (301, 302, 303, 307, 308):
        conns = FakeConnections({"site.example": FakeResponse(status, {"location": "/again"})})
        f = SafeHttpFetcher(resolver({"site.example": ["93.184.216.34"]}), conns)
        assert f.fetch("https://site.example/").error_code == "too_many_redirects"
        assert len(conns.calls) == 4


def test_redirect_dns_rebinding_is_denied_before_second_connection():
    calls = []

    def changing(host, port):
        calls.append(host)
        return ["93.184.216.34"] if len(calls) == 1 else ["127.0.0.1"]

    conns = FakeConnections({"site.example": FakeResponse(302, {"location": "/next"})})
    r = SafeHttpFetcher(changing, conns).fetch("https://site.example/")
    assert r.error_code == "private_address" and len(conns.calls) == 1


def test_declared_size_and_encoding_are_checked_before_read():
    class Unreadable(FakeResponse):
        def read(self, limit):
            pytest.fail("must refuse headers before reading body")

    for headers, code in [
        ({**HTML, "content-length": str(MAX_BYTES + 1)}, "too_large"),
        ({**HTML, "content-encoding": "gzip"}, "content_encoding_not_allowed"),
    ]:
        conns = FakeConnections({"site.example": Unreadable(200, headers)})
        assert (
            SafeHttpFetcher(resolver({"site.example": ["93.184.216.34"]}), conns)
            .fetch("https://site.example/")
            .error_code
            == code
        )


def test_unknown_charset_and_network_error_are_explicit():
    conns = FakeConnections(
        {
            "site.example": FakeResponse(
                200, {"content-type": "text/plain; charset=no-such-codec"}, b"a"
            )
        }
    )
    f = SafeHttpFetcher(resolver({"site.example": ["93.184.216.34"]}), conns)
    assert f.fetch("https://site.example/").error_code == "unknown_charset"


def test_dns_deadline_does_not_wait_for_resolver():
    import threading

    released = threading.Event()

    def blocked(host, port):
        released.wait(1)
        return ["93.184.216.34"]

    try:
        result = SafeHttpFetcher(blocked, FakeConnections({}), timeout=0.02).fetch(
            "https://site.example/"
        )
        assert result.status is FetchStatus.UNAVAILABLE and result.error_code == "timeout"
    finally:
        released.set()


def test_connection_closes_on_body_failure():
    closed = []

    class Conn:
        def request(self, *args, **kw):
            pass

        def getresponse(self):
            raise OSError("secret in network error")

        def close(self):
            closed.append(True)

    f = SafeHttpFetcher(lambda *a: ["93.184.216.34"], lambda *a: Conn())
    result = f.fetch("https://site.example/")
    assert closed and result.status is FetchStatus.UNAVAILABLE
    assert "secret" not in result.error_code


@pytest.mark.parametrize(
    "ip",
    [
        "::ffff:127.0.0.1",
        "64:ff9b::a00:1",
        "2002:0a00:0001::1",
        "ff02::1",
        "fe80::1%eth0",
        "100.64.0.1",
    ],
)
def test_special_and_tunneled_addresses_are_not_public(ip):
    assert not is_public(ip)


def test_deadline_interrupts_stalled_headers_and_closes_connection():
    import threading

    closed = threading.Event()

    class Conn:
        def request(self, *a, **kw):
            pass

        def getresponse(self):
            assert closed.wait(1)
            raise TimeoutError()

        def close(self):
            closed.set()

    result = SafeHttpFetcher(lambda *a: ["93.184.216.34"], lambda *a: Conn(), timeout=0.02).fetch(
        "https://example.com/"
    )
    assert result.error_code == "timeout" and closed.is_set()


def test_https_pins_ip_but_validates_original_hostname(monkeypatch):
    from hunter.infrastructure.fetcher import PinnedHTTPSConnection

    addresses, names = [], []

    class Sock:
        def close(self):
            pass

    sock = Sock()

    def connect(address, timeout):
        addresses.append(address)
        return sock

    monkeypatch.setattr("socket.create_connection", connect)

    class Context:
        def wrap_socket(self, value, server_hostname):
            assert value is sock
            names.append(server_hostname)
            return sock

    monkeypatch.setattr("ssl.create_default_context", Context)
    conn = PinnedHTTPSConnection("example.com", "93.184.216.34", 443, 1)
    conn.connect()
    assert addresses == [("93.184.216.34", 443)] and names == ["example.com"]
    conn.close()


def test_hash_covers_full_body_and_text_limit_is_explicit():
    import hashlib

    from hunter.infrastructure.fetcher import SOURCE_TEXT_LIMIT

    body = b"x" * (SOURCE_TEXT_LIMIT + 1)
    conns = FakeConnections(
        {"site.example": FakeResponse(200, {"content-type": "text/plain"}, body)}
    )
    r = SafeHttpFetcher(lambda *a: ["93.184.216.34"], conns).fetch("https://site.example/")
    assert r.truncated and len(r.text) == SOURCE_TEXT_LIMIT
    assert r.content_hash == hashlib.sha256(body).hexdigest()
