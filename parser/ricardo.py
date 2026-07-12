from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode
from playwright.async_api import Browser, Page, async_playwright

from parser.formatter import (
    deep_get,
    format_price,
    parse_iso_datetime,
    pick_image_url,
    rating_from_score,
    relative_time_ru,
)
from parser.models import VoidParserItem, VoidParserResult

LISTING_HREF_RE = re.compile(r"/[a-z]{2}/a/\d+/?", re.I)
INTERSTITIAL_TITLES = {
    "just a moment...",
    "loading...",
    "attention required!",
    "ricardo captcha",
}


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


@dataclass
class ParserConfig:
    locale: str = "de"
    max_pages: int = 5
    max_items: int = 100
    headless: bool = True
    proxy_url: str | None = None


class RicardoParser:
    def __init__(self, config: ParserConfig):
        self.config = config
        self.base_url = f"https://www.ricardo.ch/{config.locale}"
        self._seen_links: set[str] = set()
        self._view_counts: dict[str, int] = {}
        self._seller_cache: dict[str, dict[str, Any]] = {}

    async def parse(
        self,
        start_url: str,
        progress: Callable[[str], Any] | Callable[[str], Awaitable[Any]] | None = None,
    ) -> VoidParserResult:
        async def notify(message: str) -> None:
            if not progress:
                return
            result = progress(message)
            if asyncio.iscoroutine(result):
                await result

        await notify("Запуск браузера...")
        async with async_playwright() as playwright:
            browser = await self._launch_browser(playwright)
            try:
                context = await self._create_context(browser)
                page = await context.new_page()
                listing_urls = await self._collect_listing_urls(page, start_url, notify)
                await notify(f"Найдено объявлений: {len(listing_urls)}")

                items: list[VoidParserItem] = []
                for index, listing_url in enumerate(listing_urls, start=1):
                    await notify(f"Парсинг {index}/{len(listing_urls)}")
                    try:
                        item = await self._parse_listing(page, listing_url)
                        if item:
                            items.append(item)
                    except Exception as exc:
                        await notify(f"Ошибка {listing_url}: {exc}")
                    await asyncio.sleep(0.4)

                return VoidParserResult(items=items)
            finally:
                await browser.close()

    async def _launch_browser(self, playwright) -> Browser:
        launch_kwargs: dict[str, Any] = {
            "headless": self.config.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        if self.config.proxy_url:
            launch_kwargs["proxy"] = parse_playwright_proxy(self.config.proxy_url)
        return await playwright.chromium.launch(**launch_kwargs)

    async def _create_context(self, browser: Browser):
        return await browser.new_context(
            locale=self.config.locale,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
        )

    async def _goto_ready(self, page: Page, url: str, timeout_ms: int = 120000) -> None:
        await page.goto(url, wait_until="commit", timeout=timeout_ms)
        for _ in range(60):
            title = (await page.title() or "").strip().lower()
            body = await page.content()
            if "__NEXT_DATA__" in body and title not in INTERSTITIAL_TITLES:
                return
            if title and title not in INTERSTITIAL_TITLES and "/de/a/" in page.url:
                return
            await asyncio.sleep(2)
        raise RuntimeError(f"Не удалось пройти защиту сайта для {url}")

    async def _extract_next_data(self, page: Page) -> dict[str, Any]:
        raw = await page.evaluate(
            """() => {
                const node = document.querySelector('#__NEXT_DATA__');
                if (!node || !node.textContent) return null;
                try { return JSON.parse(node.textContent); } catch (e) { return null; }
            }"""
        )
        if not raw:
            return {}
        return raw.get("props", {}).get("pageProps", {}) or {}

    async def _collect_listing_urls(
        self,
        page: Page,
        start_url: str,
        notify: Callable[[str], Awaitable[Any]],
    ) -> list[str]:
        normalized_start = self._normalize_start_url(start_url)
        collected: list[str] = []
        seen: set[str] = set()

        for page_num in range(1, self.config.max_pages + 1):
            page_url = self._with_page(normalized_start, page_num)
            await notify(f"Страница поиска {page_num}: {page_url}")
            await self._goto_ready(page, page_url)
            links = await self._extract_listing_links(page)
            if not links:
                break

            new_links = 0
            for link in links:
                if link in seen:
                    continue
                seen.add(link)
                collected.append(link)
                new_links += 1
                if len(collected) >= self.config.max_items:
                    return collected

            if new_links == 0:
                break

        return collected

    def _normalize_start_url(self, url: str) -> str:
        parsed = urlparse(url.strip())
        if not parsed.netloc:
            return urljoin(self.base_url + "/", url.lstrip("/"))
        return urlunparse(parsed)

    def _with_page(self, url: str, page_num: int) -> str:
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["page"] = str(page_num)
        return urlunparse(parsed._replace(query=urlencode(query)))

    async def _extract_listing_links(self, page: Page) -> list[str]:
        hrefs = await page.eval_on_selector_all(
            "a[href]",
            "elements => elements.map(el => el.getAttribute('href')).filter(Boolean)",
        )
        links: list[str] = []
        for href in hrefs:
            if not LISTING_HREF_RE.search(href):
                continue
            full = urljoin(self.base_url + "/", href)
            full = full.split("?")[0]
            if not full.endswith("/"):
                full += "/"
            links.append(full)
        return list(dict.fromkeys(links))

    async def _parse_listing(self, page: Page, listing_url: str) -> VoidParserItem | None:
        await self._goto_ready(page, listing_url)
        page_props = await self._extract_next_data(page)

        listing = (
            page_props.get("listing")
            or page_props.get("article")
            or page_props.get("offer")
            or page_props.get("product")
            or page_props.get("basicInfo")
            or page_props
        )
        seller = (
            deep_get(listing, "seller")
            or page_props.get("seller")
            or {}
        )

        nickname = (
            deep_get(seller, "nickname")
            or deep_get(seller, "name")
            or deep_get(listing, "sellerName")
            or ""
        )
        seller_id = str(deep_get(seller, "id") or deep_get(listing, "seller_id") or "")

        seller_stats = await self._get_seller_stats(page, seller, nickname, seller_id)

        title = (
            deep_get(listing, "title")
            or deep_get(page_props, "seoMeta", "title")
            or await page.locator("h1").first.inner_text()
        )
        title = str(title).strip()

        image = pick_image_url(
            deep_get(listing, "images")
            or deep_get(listing, "image")
            or deep_get(page_props, "images")
        )
        if not image:
            image = await self._first_image_from_dom(page)

        price = format_price(
            deep_get(listing, "offer", "price")
            or deep_get(listing, "buyNowPrice")
            or deep_get(listing, "buy_now_price")
            or deep_get(listing, "price")
            or deep_get(page_props, "basicInfo", "price")
        )

        created_raw = (
            deep_get(listing, "creationDate")
            or deep_get(listing, "creation_date")
            or deep_get(listing, "startDate")
            or deep_get(listing, "start_date")
            or deep_get(page_props, "basicInfo", "creationDate")
        )
        created_dt = parse_iso_datetime(created_raw)

        reg_raw = (
            seller_stats.get("registration_date")
            or deep_get(seller, "memberSince")
            or deep_get(seller, "registrationDate")
            or deep_get(seller, "createdAt")
        )
        reg_dt = parse_iso_datetime(reg_raw)

        parser_views = self._view_counts.get(listing_url, 0)
        self._view_counts[listing_url] = parser_views + 1

        item = VoidParserItem(
            item_title=title,
            item_photo=image,
            ads_number=int(seller_stats.get("ads_number") or deep_get(seller, "articleCount") or 0),
            parser_views=parser_views,
            ads_number_bought=int(seller_stats.get("ads_number_bought") or 0),
            ads_number_sold=int(seller_stats.get("ads_number_sold") or 0),
            gender="",
            email="",
            person_reg_date=relative_time_ru(reg_dt),
            item_price=price,
            views=None,
            rating=rating_from_score(deep_get(seller, "score") or seller_stats.get("rating")),
            created_date=relative_time_ru(created_dt),
            created_real_date="",
            phone=str(seller_stats.get("phone") or ""),
            item_desc="",
            location="",
            item_link=listing_url,
            person_link="",
            item_person_name=str(nickname or ""),
        )
        return item

    async def _first_image_from_dom(self, page: Page) -> str:
        src = await page.evaluate(
            """() => {
                const img = document.querySelector('img[src*="ricardostatic"]');
                return img ? img.src : '';
            }"""
        )
        return str(src or "")

    async def _get_seller_stats(
        self,
        page: Page,
        seller: dict[str, Any],
        nickname: str,
        seller_id: str,
    ) -> dict[str, Any]:
        cache_key = seller_id or nickname
        if cache_key and cache_key in self._seller_cache:
            return self._seller_cache[cache_key]

        stats = {
            "ads_number": deep_get(seller, "articleCount") or deep_get(seller, "article_count") or 0,
            "ads_number_bought": self._first_int(
                seller,
                "purchasesCount",
                "purchaseCount",
                "purchase_count",
                "boughtCount",
                "numberOfPurchases",
            ),
            "ads_number_sold": self._first_int(
                seller,
                "salesCount",
                "soldCount",
                "sales_count",
                "numberOfSales",
                "completedSales",
            ),
            "rating": deep_get(seller, "score"),
            "phone": self._extract_phone(seller),
            "registration_date": (
                deep_get(seller, "memberSince")
                or deep_get(seller, "registrationDate")
                or deep_get(seller, "createdAt")
            ),
        }

        seller_url = self._seller_profile_url(seller, nickname, seller_id)
        if seller_url:
            try:
                await self._goto_ready(page, seller_url)
                seller_props = await self._extract_next_data(page)
                seller_data = (
                    seller_props.get("seller")
                    or seller_props.get("user")
                    or seller_props.get("profile")
                    or seller_props
                )
                stats["ads_number"] = self._first_int(
                    seller_data,
                    "articleCount",
                    "article_count",
                    "openOffersCount",
                    default=stats["ads_number"],
                )
                stats["ads_number_bought"] = self._first_int(
                    seller_data,
                    "purchasesCount",
                    "purchaseCount",
                    "purchase_count",
                    "boughtCount",
                    "numberOfPurchases",
                    default=stats["ads_number_bought"],
                )
                stats["ads_number_sold"] = self._first_int(
                    seller_data,
                    "salesCount",
                    "soldCount",
                    "sales_count",
                    "numberOfSales",
                    "completedSales",
                    default=stats["ads_number_sold"],
                )
                stats["rating"] = deep_get(seller_data, "score") or stats["rating"]
                stats["phone"] = self._extract_phone(seller_data) or stats["phone"]
                stats["registration_date"] = (
                    deep_get(seller_data, "memberSince")
                    or deep_get(seller_data, "registrationDate")
                    or deep_get(seller_data, "createdAt")
                    or stats["registration_date"]
                )
            except Exception:
                pass

        if cache_key:
            self._seller_cache[cache_key] = stats
        return stats

    def _seller_profile_url(self, seller: dict[str, Any], nickname: str, seller_id: str) -> str | None:
        for key in ("shopUrl", "sellerUrl", "profileUrl", "url"):
            value = seller.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        if nickname:
            return f"{self.base_url}/shop/{nickname}/offers"
        if seller_id:
            return f"{self.base_url}/profile/{seller_id}"
        return None

    def _first_int(self, data: dict[str, Any], *keys: str, default: int = 0) -> int:
        for key in keys:
            value = data.get(key)
            if value is None or value == "":
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return int(default or 0)

    def _extract_phone(self, data: dict[str, Any]) -> str:
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
