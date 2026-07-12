from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlparse, urlunparse, parse_qsl, urlencode

from parser.browser import BrowserSession
from parser.extract import (
    LISTING_HREF_RE,
    SEARCH_SUMMARY_JS,
    extract_article,
    extract_next_data,
    extract_phone,
    extract_product_jsonld,
    extract_search_summaries_from_next_data,
    extract_seller_stats_from_state,
    first_int,
)
from parser.formatter import (
    deep_get,
    format_price,
    parse_iso_datetime,
    pick_image_url,
    rating_from_score,
    relative_time_ru,
)
from parser.models import VoidParserItem, VoidParserResult

logger = logging.getLogger(__name__)


@dataclass
class ParserConfig:
    locale: str = "de"
    max_pages: int = 5
    max_items: int = 100
    headless: bool = True
    proxy_url: str | None = None
    cookies_path: str | None = None


@dataclass
class SearchSummary:
    url: str
    title: str = ""
    price: Any = None
    image: str = ""
    seller_name: str = ""


class RicardoParser:
    def __init__(self, config: ParserConfig):
        self.config = config
        self.base_url = f"https://www.ricardo.ch/{config.locale}"
        self._view_counts: dict[str, int] = {}
        self._seller_cache: dict[str, dict[str, Any]] = {}

    async def parse(self, start_url: str, progress: Callable[[str], Any] | None = None):
        import asyncio

        return await asyncio.to_thread(self._parse_sync, start_url, progress)

    def _session(self) -> BrowserSession:
        return BrowserSession(
            headless=self.config.headless,
            locale=self.config.locale,
            proxy_url=self.config.proxy_url,
            cookies_path=self.config.cookies_path,
        )

    def _parse_sync(self, start_url: str, progress: Callable[[str], Any] | None = None) -> VoidParserResult:
        def notify(message: str) -> None:
            logger.info(message)
            if progress:
                progress(message)

        notify("Запуск Camoufox...")
        with self._session() as session:
            summaries = self._collect_listings(session, start_url, notify)

        notify(f"Найдено объявлений: {len(summaries)}")
        items: list[VoidParserItem] = []
        session: BrowserSession | None = None

        def ensure_session() -> BrowserSession:
            nonlocal session
            if session is None or not session.is_alive():
                if session is not None:
                    try:
                        session.__exit__(None, None, None)
                    except Exception:
                        pass
                session = self._session()
                session.__enter__()
            return session

        try:
            for index, summary in enumerate(summaries, start=1):
                notify(f"Парсинг {index}/{len(summaries)}")
                item: VoidParserItem | None = None
                try:
                    item = self._parse_listing(ensure_session(), summary)
                except Exception as exc:
                    logger.warning("Listing parse failed for %s: %s", summary.url, exc)
                    notify(f"Детали недоступны ({index}), беру из поиска")
                    if session and not session.is_alive():
                        session = None
                if item is None:
                    item = self._item_from_summary(summary)
                items.append(item)
                time.sleep(0.6)
        finally:
            if session is not None:
                try:
                    session.__exit__(None, None, None)
                except Exception:
                    pass

        return VoidParserResult(items=items)

    def _item_from_summary(self, summary: SearchSummary) -> VoidParserItem:
        parser_views = self._view_counts.get(summary.url, 0)
        self._view_counts[summary.url] = parser_views + 1
        return VoidParserItem(
            item_title=str(summary.title or "").strip(),
            item_photo=str(summary.image or ""),
            item_price=format_price(summary.price),
            item_link=summary.url,
            item_person_name=str(summary.seller_name or ""),
            parser_views=parser_views,
        )

    def _collect_listings(
        self,
        session: BrowserSession,
        start_url: str,
        notify: Callable[[str], None],
    ) -> list[SearchSummary]:
        normalized = self._normalize_start_url(start_url)
        if LISTING_HREF_RE.search(urlparse(normalized).path):
            return [SearchSummary(url=normalized)]

        collected: list[SearchSummary] = []
        seen: set[str] = set()
        js = SEARCH_SUMMARY_JS.replace("LOCALE", json.dumps(self.config.locale))

        for page_num in range(1, self.config.max_pages + 1):
            page_url = self._with_page(normalized, page_num)
            notify(f"Страница поиска {page_num}: {page_url}")
            try:
                session.goto(page_url)
            except Exception as exc:
                notify(f"Ошибка загрузки страницы: {exc}")
                break

            notify("Жду результаты поиска...")
            if not session.wait_for_search_results():
                title = session.evaluate("() => document.title || ''") or ""
                current_url = session.page.url if session.page else page_url
                notify(f"Результаты не загрузились. title={title!r}, url={current_url}")
                break
            session.wait_for_next_data()

            page_summaries: list[SearchSummary] = []
            summaries = session.evaluate(js) or []
            if summaries:
                for item in summaries:
                    href = item.get("url")
                    if not href:
                        continue
                    full = urljoin(self.base_url + "/", href)
                    if not full.endswith("/"):
                        full += "/"
                    page_summaries.append(
                        SearchSummary(
                            url=full,
                            title=str(item.get("title") or ""),
                            price=item.get("price"),
                            image=str(item.get("image") or ""),
                        )
                    )
            else:
                next_data = extract_next_data(session)
                for item in extract_search_summaries_from_next_data(
                    next_data,
                    locale=self.config.locale,
                    base_url=self.base_url,
                ):
                    page_summaries.append(
                        SearchSummary(
                            url=item["url"],
                            title=str(item.get("title") or ""),
                            price=item.get("price"),
                            image=str(item.get("image") or ""),
                            seller_name=str(item.get("seller_name") or ""),
                        )
                    )
                if not page_summaries:
                    for link in self._extract_listing_links(session):
                        page_summaries.append(SearchSummary(url=link))

            if not page_summaries:
                title = session.evaluate("() => document.title || ''") or ""
                notify(f"Объявления не найдены на странице. title={title!r}")
                break

            new_count = 0
            for summary in page_summaries:
                if summary.url in seen:
                    continue
                seen.add(summary.url)
                collected.append(summary)
                new_count += 1
                if len(collected) >= self.config.max_items:
                    return collected

            if new_count == 0:
                break

        return collected

    def _extract_listing_links(self, session: BrowserSession) -> list[str]:
        hrefs = session.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]'))
                .map(a => a.getAttribute('href'))
                .filter(Boolean)"""
        )
        links: list[str] = []
        for href in hrefs or []:
            if not LISTING_HREF_RE.search(href):
                continue
            full = urljoin(self.base_url + "/", href).split("?")[0]
            if not full.endswith("/"):
                full += "/"
            links.append(full)
        return list(dict.fromkeys(links))

    def _parse_listing(self, session: BrowserSession, summary: SearchSummary) -> VoidParserItem | None:
        listing_url = summary.url
        session.goto(listing_url)
        if not session.wait_for_next_data():
            raise RuntimeError("Не удалось получить __NEXT_DATA__")

        next_data = extract_next_data(session)
        article = extract_article(next_data)
        product = extract_product_jsonld(session)

        seller = article.get("seller") or {}
        offer = article.get("offer") or {}
        nickname = str(
            seller.get("nickname")
            or summary.seller_name
            or deep_get(product, "offers", "seller", "name")
            or ""
        )
        seller_id = str(seller.get("id") or "")

        seller_stats = self._get_seller_stats(session, seller, nickname, seller_id)

        title = article.get("title") or deep_get(product, "name") or summary.title or ""
        image = pick_image_url(article.get("images") or deep_get(product, "image")) or summary.image
        price_value = (
            offer.get("price")
            or deep_get(product, "offers", "price")
            or article.get("buyNowPrice")
            or article.get("buy_now_price")
            or summary.price
        )
        created_raw = (
            article.get("creationDate")
            or article.get("creation_date")
            or offer.get("start_date")
            or offer.get("startDate")
        )
        reg_raw = (
            seller_stats.get("registration_date")
            or seller.get("memberSince")
            or seller.get("registrationDate")
            or seller.get("createdAt")
        )

        parser_views = self._view_counts.get(listing_url, 0)
        self._view_counts[listing_url] = parser_views + 1

        return VoidParserItem(
            item_title=str(title).strip(),
            item_photo=image,
            ads_number=first_int(
                seller_stats,
                "ads_number",
                "articleCount",
                "article_count",
                default=first_int(seller, "articleCount", "article_count"),
            ),
            parser_views=parser_views,
            ads_number_bought=first_int(
                seller_stats,
                "ads_number_bought",
                "purchasesCount",
                "purchaseCount",
                "purchase_count",
                "boughtCount",
                default=first_int(seller, "purchasesCount", "purchaseCount", "purchase_count", "boughtCount"),
            ),
            ads_number_sold=first_int(
                seller_stats,
                "ads_number_sold",
                "salesCount",
                "soldCount",
                "sales_count",
                "completedSales",
                default=first_int(seller, "salesCount", "soldCount", "sales_count", "completedSales"),
            ),
            gender="",
            email="",
            person_reg_date=relative_time_ru(parse_iso_datetime(reg_raw)),
            item_price=format_price(price_value),
            views=None,
            rating=rating_from_score(seller_stats.get("rating") or seller.get("score")),
            created_date=relative_time_ru(parse_iso_datetime(created_raw)),
            created_real_date="",
            phone=str(seller_stats.get("phone") or extract_phone(seller)),
            item_desc="",
            location="",
            item_link=listing_url,
            person_link="",
            item_person_name=nickname,
        )

    def _get_seller_stats(
        self,
        session: BrowserSession,
        seller: dict[str, Any],
        nickname: str,
        seller_id: str,
    ) -> dict[str, Any]:
        cache_key = seller_id or nickname
        if cache_key and cache_key in self._seller_cache:
            return self._seller_cache[cache_key]

        stats = {
            "ads_number": first_int(seller, "articleCount", "article_count"),
            "ads_number_bought": first_int(
                seller, "purchasesCount", "purchaseCount", "purchase_count", "boughtCount"
            ),
            "ads_number_sold": first_int(
                seller, "salesCount", "soldCount", "sales_count", "completedSales"
            ),
            "rating": seller.get("score"),
            "phone": extract_phone(seller),
            "registration_date": seller.get("memberSince") or seller.get("registrationDate") or seller.get("createdAt"),
        }

        seller_url = self._seller_profile_url(seller, nickname, seller_id)
        needs_profile = seller_url and (
            not stats["ads_number_bought"] or not stats["ads_number_sold"] or not stats["registration_date"]
        )
        if needs_profile and session.is_alive():
            try:
                session.goto(seller_url)
                session.wait_for_next_data()
                seller_next = extract_next_data(session)
                seller_data = extract_seller_stats_from_state(seller_next) or extract_article(seller_next).get("seller") or {}
                if seller_data:
                    stats["ads_number"] = first_int(
                        seller_data, "articleCount", "article_count", default=stats["ads_number"]
                    )
                    stats["ads_number_bought"] = first_int(
                        seller_data,
                        "purchasesCount",
                        "purchaseCount",
                        "purchase_count",
                        "boughtCount",
                        default=stats["ads_number_bought"],
                    )
                    stats["ads_number_sold"] = first_int(
                        seller_data,
                        "salesCount",
                        "soldCount",
                        "sales_count",
                        "completedSales",
                        default=stats["ads_number_sold"],
                    )
                    stats["rating"] = seller_data.get("score") or stats["rating"]
                    stats["phone"] = extract_phone(seller_data) or stats["phone"]
                    stats["registration_date"] = (
                        seller_data.get("memberSince")
                        or seller_data.get("registrationDate")
                        or seller_data.get("createdAt")
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

    def _normalize_start_url(self, url: str) -> str:
        parsed = urlparse(url.strip())
        if not parsed.netloc:
            parsed = urlparse(urljoin(self.base_url + "/", url.lstrip("/")))

        locale = self.config.locale
        path = parsed.path.rstrip("/") or "/"
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))

        if re.fullmatch(rf"/{locale}/s", path, re.I):
            search_query = query.pop("q", None) or query.pop("query", None)
            if search_query:
                path = f"/{locale}/s/{quote(search_query, safe='')}"

        return urlunparse(parsed._replace(path=path, query=urlencode(query)))

    def _with_page(self, url: str, page_num: int) -> str:
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["page"] = str(page_num)
        return urlunparse(parsed._replace(query=urlencode(query)))
