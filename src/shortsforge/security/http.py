"""SSRF-safe HTTP client — rejects requests to private/loopback/link-local addresses."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx


class SSRFError(ValueError):
    """Raised when a URL would reach a restricted network address."""


# RFC 1918 + loopback + link-local + multicast ranges to block
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),  # loopback
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("10.0.0.0/8"),  # RFC 1918
    ipaddress.ip_network("172.16.0.0/12"),  # RFC 1918
    ipaddress.ip_network("192.168.0.0/16"),  # RFC 1918
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique-local
    ipaddress.ip_network("224.0.0.0/4"),  # multicast
    ipaddress.ip_network("240.0.0.0/4"),  # reserved
    ipaddress.ip_network("100.64.0.0/10"),  # shared address space
]

_ALLOWED_SCHEMES = {"https", "http"}
_TIMEOUT = httpx.Timeout(30.0)


def _check_url(url: str) -> None:
    """Raise SSRFError if *url* resolves to a restricted address."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise SSRFError(f"Scheme {parsed.scheme!r} is not allowed")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFError("URL has no hostname")

    # Resolve all addresses the hostname maps to
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise SSRFError(f"Cannot resolve hostname {hostname!r}: {exc}") from exc

    for info in infos:
        addr_str = info[4][0]
        try:
            addr = ipaddress.ip_address(addr_str)
        except ValueError:
            continue
        for blocked in _BLOCKED_NETWORKS:
            if addr in blocked:
                raise SSRFError(
                    f"URL {url!r} resolves to restricted address {addr_str}"
                )


async def safe_get(url: str, *, timeout: float = 30.0) -> httpx.Response:
    """Perform an SSRF-safe GET request.

    Resolves the hostname BEFORE connecting and raises SSRFError if the
    destination is in a private/loopback/link-local range.
    Follows redirects but re-validates each redirect destination.
    """
    _check_url(url)

    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(timeout),
    ) as client:
        response = await client.get(url)

        # Manually follow redirects to re-validate each hop
        redirect_count = 0
        while response.is_redirect and redirect_count < 5:
            redirect_url = response.headers.get("location", "")
            if not redirect_url:
                break
            _check_url(redirect_url)
            response = await client.get(redirect_url)
            redirect_count += 1

        return response
