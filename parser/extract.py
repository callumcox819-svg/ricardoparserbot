from __future__ import annotations

import json
import re
from typing import Any

from parser.browser import BrowserSession
from parser.formatter import deep_get

LISTING_HREF_RE = re.compile(r"/[a-z]{2}/a/\d+/?", re.I)
LISTING_PATH_RE = re.compile(r"/[a-z]{2}/a/\d+[^\"\\]*", re.I)

SEARCH_SUMMARY_JS = """
() => {
  const locale = LOCALE;
  const container = document.querySelector('[data-testid="regular-results"]');
  if (!container) return [];
  const cards = Array.from(container.querySelectorAll('a[href^="/' + locale + '/a/"]'));
  return cards.map(card => {
    const href = card.getAttribute('href');
    const idMatch = href.match(/-(\\d+)\\/?$/);
    const img = card.querySelector('img[fetchpriority="high"]') || card.querySelector('img');
    const prices = Array.from(card.querySelectorAll('span'))
      .map(s => s.textContent.trim())
      .filter(t => /^\\d+[.,]?\\d*$/.test(t))
      .map(t => parseFloat(t.replace(',', '.')));
    return {
      id: idMatch ? idMatch[1] : null,
      title: img ? img.getAttribute('alt') : null,
      url: href,
      price: prices.length ? prices[prices.length - 1] : null,
      image: img ? img.getAttribute('src') : null,
    };
  });
}
"""


def extract_article(next_data: dict[str, Any] | None) -> dict[str, Any]:
    if not next_data:
        return {}
    page_props = deep_get(next_data, "props", "pageProps", default={}) or {}
    return (
        page_props.get("article")
        or page_props.get("listing")
        or page_props.get("offer")
        or {}
    )


def extract_seller_stats_from_state(next_data: dict[str, Any] | None) -> dict[str, Any]:
    if not next_data:
        return {}
    queries = deep_get(next_data, "props", "pageProps", "dehydratedState", "queries", default=[]) or []
    for query in queries:
        state_data = deep_get(query, "state", "data", default={}) or {}
        if not isinstance(state_data, dict):
            continue
        seller = state_data.get("seller") or state_data
        if isinstance(seller, dict) and (
            "articleCount" in seller
            or "purchasesCount" in seller
            or "salesCount" in seller
            or "purchase_count" in seller
            or "sales_count" in seller
        ):
            return seller
    return {}


def first_int(data: dict[str, Any], *keys: str, default: int = 0) -> int:
    for key in keys:
        value = data.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return int(default or 0)


def extract_phone(data: dict[str, Any]) -> str:
    for key in ("phone", "phoneNumber", "mobile", "telephone"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for subkey in ("number", "value", "formatted"):
                subvalue = value.get(subkey)
                if isinstance(subvalue, str) and subvalue.strip():
                    return subvalue.strip()
    identification = data.get("identification")
    if isinstance(identification, dict):
        phone_block = identification.get("phoneNumber") or identification.get("phone_number")
        if isinstance(phone_block, dict):
            for subkey in ("number", "value", "formatted"):
                subvalue = phone_block.get(subkey)
                if isinstance(subvalue, str) and subvalue.strip():
                    return subvalue.strip()
    return ""


def extract_search_summaries_from_next_data(
    next_data: dict[str, Any] | None,
    *,
    locale: str = "de",
    base_url: str = "https://www.ricardo.ch/de",
) -> list[dict[str, Any]]:
    if not next_data:
        return []

    summaries: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_item(item: dict[str, Any]) -> None:
        href = item.get("url") or item.get("href") or item.get("permalink")
        if not isinstance(href, str) or not LISTING_HREF_RE.search(href):
            return
        normalized = href.split("?")[0]
        if not normalized.endswith("/"):
            normalized += "/"
        if normalized in seen:
            return
        seen.add(normalized)
        seller = item.get("seller") if isinstance(item.get("seller"), dict) else {}
        summaries.append(
            {
                "url": normalized,
                "title": str(item.get("title") or item.get("name") or ""),
                "price": item.get("price") or item.get("buyNowPrice") or item.get("buy_now_price"),
                "image": item.get("image") or item.get("thumbnailUrl") or "",
                "seller_name": str(seller.get("nickname") or seller.get("name") or ""),
            }
        )

    queries = deep_get(next_data, "props", "pageProps", "dehydratedState", "queries", default=[]) or []
    for query in queries:
        data = deep_get(query, "state", "data")
        if not isinstance(data, dict):
            continue
        for key in ("listings", "articles", "items", "results", "searchResults", "regularResults"):
            listings = data.get(key)
            if not isinstance(listings, list):
                continue
            for item in listings:
                if isinstance(item, dict):
                    add_item(item)

    return summaries


def extract_listing_urls_from_next_data(
    next_data: dict[str, Any] | None,
    *,
    locale: str = "de",
) -> list[str]:
    if not next_data:
        return []

    urls: list[str] = []
    seen: set[str] = set()

    def add_url(href: str) -> None:
        if not LISTING_HREF_RE.search(href):
            return
        normalized = href.split("?")[0]
        if not normalized.endswith("/"):
            normalized += "/"
        if normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)

    queries = deep_get(next_data, "props", "pageProps", "dehydratedState", "queries", default=[]) or []
    for query in queries:
        data = deep_get(query, "state", "data")
        if not isinstance(data, dict):
            continue
        for key in ("listings", "articles", "items", "results", "searchResults", "regularResults"):
            listings = data.get(key)
            if not isinstance(listings, list):
                continue
            for item in listings:
                if not isinstance(item, dict):
                    continue
                href = item.get("url") or item.get("href") or item.get("permalink")
                if isinstance(href, str):
                    add_url(href)

    raw = json.dumps(next_data, ensure_ascii=False)
    for match in LISTING_PATH_RE.findall(raw):
        add_url(match)

    return urls


def extract_next_data(session: BrowserSession) -> dict[str, Any] | None:
    raw = session.evaluate_with_retry(
        "document.getElementById('__NEXT_DATA__')?.textContent || null"
    )
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def extract_product_jsonld(session: BrowserSession) -> dict[str, Any] | None:
    raw = session.evaluate_with_retry(
        "document.getElementById('pdp-json-ld')?.textContent || null"
    )
    if not raw:
        return None
    data = json.loads(raw)
    for node in data.get("@graph", []):
        if node.get("@type") == "Product":
            return node
    return None
