from __future__ import annotations

from urllib.parse import urlparse


def parse_playwright_proxy(proxy_url: str | None) -> dict[str, str] | None:
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url.strip())
    if not parsed.hostname or not parsed.port:
        raise ValueError("PROXY_URL must look like http://user:pass@host:port")
    scheme = parsed.scheme or "http"
    proxy: dict[str, str] = {"server": f"{scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy
