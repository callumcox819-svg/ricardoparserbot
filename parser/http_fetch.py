from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

from parser.proxy import parse_requests_proxy

logger = logging.getLogger(__name__)

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*type="application/json"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
JSON_LD_RE = re.compile(
    r'<script id="pdp-json-ld"[^>]*type="application/json"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def fetch_listing_payload(
    url: str,
    *,
    cookies: dict[str, str] | None = None,
    proxy_url: str | None = None,
    locale: str = "de",
    timeout: int = 30,
) -> dict[str, Any]:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
    )
    session.headers["Accept-Language"] = f"{locale}-CH,{locale};q=0.9,en;q=0.8"

    if cookies:
        session.cookies.update(cookies)

    proxies = parse_requests_proxy(proxy_url)
    response = session.get(url, timeout=timeout, proxies=proxies, allow_redirects=True)
    response.raise_for_status()

    html = response.text
    next_data = _extract_json(NEXT_DATA_RE, html)
    product_jsonld = _extract_product_jsonld(html)
    return {"next_data": next_data, "product": product_jsonld}


def _extract_json(pattern: re.Pattern[str], html: str) -> dict[str, Any] | None:
    match = pattern.search(html)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _extract_product_jsonld(html: str) -> dict[str, Any] | None:
    data = _extract_json(JSON_LD_RE, html)
    if not data:
        return None
    for node in data.get("@graph", []):
        if node.get("@type") == "Product":
            return node
    return None
