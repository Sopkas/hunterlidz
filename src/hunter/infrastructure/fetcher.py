"""Safe HTTP(S) reader for public company pages. (H-04, FR-03, T-09)

Doc 09 "Защита чтения источников": only http/https, loopback / private / link-local /
metadata addresses are blocked, DNS is checked and every redirect is re-checked, size and
content type are limited, no JavaScript is executed and page text is data, never instructions.

The connection goes to the IP address that passed the check (TLS still verifies the host
name), so a second DNS answer cannot redirect the request to an internal address
(DNS rebinding). Proxies from the environment are not used.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import socket
import ssl
from collections.abc import Callable
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from hunter.application.ports import FetchResult
from hunter.domain.entities import FetchStatus
from hunter.domain.listings import SOURCE_TEXT_LIMIT

TIMEOUT_SECONDS = 15  # doc 08: a single HTTP read is limited to 15 s
MAX_BYTES = 1_000_000
MAX_REDIRECTS = 3
ALLOWED_TYPES = ("text/html", "text/plain")
USER_AGENT = "HunterResearch/0.1 (+manual research, respects robots)"

Resolver = Callable[[str, int], list[str]]


def system_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


class PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, ip: str, port: int, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, ip: str, port: int, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def pinned_connection(scheme: str, host: str, ip: str, port: int, timeout: float):
    cls = PinnedHTTPSConnection if scheme == "https" else PinnedHTTPConnection
    return cls(host, ip, port, timeout)


class _Denied(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def is_public(ip: str) -> bool:
    address = ipaddress.ip_address(ip)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


class SafeHttpFetcher:
    def __init__(
        self,
        resolver: Resolver = system_resolver,
        connection_factory=pinned_connection,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self._resolve = resolver
        self._connect = connection_factory
        self._timeout = timeout

    def fetch(self, url: str) -> FetchResult:
        current = url
        try:
            for _ in range(MAX_REDIRECTS + 1):
                scheme, host, port, path = self._check_url(current)
                ip = self._check_host(host, port)
                status, headers, body = self._get(scheme, host, ip, port, path)
                if status in (301, 302, 303, 307, 308):
                    location = headers.get("location")
                    if not location:
                        return _fail(current, FetchStatus.ERROR, "redirect_without_location")
                    current = urljoin(current, location)
                    continue
                if status != 200:
                    return _fail(current, FetchStatus.UNAVAILABLE, f"http_{status}")
                return _success(current, headers, body)
            return _fail(current, FetchStatus.ERROR, "too_many_redirects")
        except _Denied as denied:
            return _fail(current, FetchStatus.DENIED, denied.code)
        except (TimeoutError, socket.timeout):
            return _fail(current, FetchStatus.UNAVAILABLE, "timeout")
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            return _fail(current, FetchStatus.UNAVAILABLE, f"network_{type(exc).__name__}")

    def _check_url(self, url: str) -> tuple[str, str, int, str]:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise _Denied("scheme_not_allowed")
        if not parts.hostname or parts.username or parts.password:
            raise _Denied("bad_url")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        return parts.scheme, parts.hostname, port, path

    def _check_host(self, host: str, port: int) -> str:
        try:
            ips = self._resolve(host, port)
        except OSError:
            raise _Denied("dns_failed") from None
        if not ips:
            raise _Denied("dns_empty")
        if not all(is_public(ip) for ip in ips):
            raise _Denied("private_address")
        return ips[0]

    def _get(self, scheme: str, host: str, ip: str, port: int, path: str):
        conn = self._connect(scheme, host, ip, port, self._timeout)
        try:
            conn.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
            response = conn.getresponse()
            headers = {k.lower(): v for k, v in response.getheaders()}
            if response.status != 200:
                return response.status, headers, b""
            content_type = headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type not in ALLOWED_TYPES:
                raise _Denied("content_type_not_allowed")
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise _Denied("too_large")
            return response.status, headers, body
        finally:
            conn.close()


def _fail(url: str, status: FetchStatus, code: str) -> FetchResult:
    return FetchResult(url=url, status=status, text=None, content_hash=None, error_code=code)


def _success(url: str, headers: dict[str, str], body: bytes) -> FetchResult:
    charset = "utf-8"
    for part in headers.get("content-type", "").split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            charset = value.strip('"')
    raw = body.decode(charset, errors="replace")
    is_html = headers.get("content-type", "").startswith("text/html")
    text = html_to_text(raw) if is_html else raw
    return FetchResult(
        url=url,
        status=FetchStatus.FETCHED,
        text=text[:SOURCE_TEXT_LIMIT],
        content_hash=hashlib.sha256(body).hexdigest(),
        truncated=len(text) > SOURCE_TEXT_LIMIT,
    )


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.parts.append(" ".join(data.split()))


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return "\n".join(parser.parts)
