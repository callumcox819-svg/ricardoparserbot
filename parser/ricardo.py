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
    extract_search_summaries_from_next_data,
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
    enrich_details: bool = True
    visit_seller_profile: bool = True
    listing_delay_sec: float = 1.0
    session_refresh_every: int = 20


@dataclass
class SearchSummary:
    url: str
    title: str = ""
    price: Any = None
    image: str = ""
    seller_name: str = ""
    person_link: str = ""
    created_date: str = ""


class RicardoParser:
    def __init__(self, config: ParserConfig):
        self.config = config
        self.base_url = f"https://www.ricardo.ch/{config.locale}"
        self._view_counts: dict[str, int] = {}
        self._seller_cache: dict[str, dict[str, Any]] = {}
        self._last_search_url = ""

    def _search_refresh_url(self, start_url: str) -> str:
        normalized = self._normalize_start_url(start_url)
        return self._with_page(normalized, 1)

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
        refresh_url = self._search_refresh_url(start_url)
        with self._session() as session:
            summaries = self._collect_listings(session, start_url, notify)
            notify(f"Найдено объявлений: {len(summaries)}")
            if not self.config.enrich_details:
                notify("Собираю JSON из результатов поиска (без детальных страниц)")
                items = [self._item_from_summary(summary) for summary in summaries]
                notify(f"Готово к сохранению: {len(items)} объявлений")
                return VoidParserResult(items=items)

            notify("Загружаю детали объявлений...")
            items = self._enrich_summaries(session, summaries, refresh_url, notify)
            return VoidParserResult(items=items)

    def _enrich_summaries(
        self,
        session: BrowserSession,
        summaries: list[SearchSummary],
        refresh_url: str,
        notify: Callable[[str], None],
    ) -> list[VoidParserItem]:
        items: list[VoidParserItem] = []
        enriched = 0
        failed = 0

        for index, summary in enumerate(summaries, start=1):
            if index == 1 or index % 10 == 0 or index == len(summaries):
                notify(f"Парсинг {index}/{len(summaries)}")

            if index > 1 and index % self.config.session_refresh_every == 0:
                notify("Обновляю сессию после captcha-лимита...")
                try:
                    session.refresh_search_session(refresh_url)
                except Exception as exc:
                    logger.warning("Session refresh failed: %s", exc)

            item: VoidParserItem | None = None
            try:
                next_data = session.fetch_listing_next_data(summary.url)
                if not next_data:
                    raise RuntimeError("captcha or missing __NEXT_DATA__")
                item = self._build_item_from_payload(summary, {"next_data": next_data, "product": {}})
                if not item:
                    raise RuntimeError("empty article")
                if self.config.visit_seller_profile and item.item_person_name:
                    shop_stats = self._get_shop_seller_stats(session, item.item_person_name)
                    item = self._apply_seller_stats(item, shop_stats)
                if item.item_person_name or item.ads_number or item.rating:
                    enriched += 1
            except Exception as exc:
                failed += 1
                logger.warning("Listing fetch failed for %s: %s", summary.url, exc)
                item = self._item_from_summary(summary)

            items.append(item)
            if self.config.listing_delay_sec:
                time.sleep(self.config.listing_delay_sec)

        notify(f"Детали получены для {enriched}/{len(summaries)} объявлений")
        if failed:
            notify(f"Не удалось загрузить {failed} карточек (captcha/блокировка)")
        return items

    def _apply_seller_stats(self, item: VoidParserItem, stats: dict[str, Any]) -> VoidParserItem:
        if not stats:
            return item
        reg_raw = stats.get("memberSince") or stats.get("registrationDate") or stats.get("createdAt")
        item.ads_number = first_int(stats, "articleCount", "article_count", default=item.ads_number)
        item.ads_number_bought = first_int(
            stats,
            "purchasesCount",
            "purchaseCount",
            "purchase_count",
            "articlesBought",
            "boughtCount",
            default=item.ads_number_bought,
        )
        item.ads_number_sold = first_int(
            stats,
            "salesCount",
            "soldCount",
            "sales_count",
            "articlesSold",
            "completedSales",
            default=item.ads_number_sold,
        )
        item.rating = rating_from_score(stats.get("score")) or item.rating
        item.person_reg_date = relative_time_ru(parse_iso_datetime(reg_raw)) or item.person_reg_date
        phone = extract_phone(stats)
        if phone:
            item.phone = phone
        return item

    def _build_item_from_payload(
        self,
        summary: SearchSummary,
        payload: dict[str, Any],
    ) -> VoidParserItem | None:
        next_data = payload.get("next_data")
        if not next_data:
            return None

        article = extract_article(next_data)
        if not article:
            return None

        product = payload.get("product") or {}
        seller = article.get("seller") or {}
        offer = article.get("offer") or {}
        nickname = str(
            seller.get("nickname")
            or summary.seller_name
            or deep_get(product, "offers", "seller", "name")
            or ""
        )
        seller_id = str(seller.get("id") or "")

        seller_stats = self._seller_stats_from_data(seller, nickname, seller_id)

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

        listing_url = summary.url
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
            created_date=relative_time_ru(parse_iso_datetime(created_raw)) or summary.created_date,
            created_real_date="",
            phone=str(seller_stats.get("phone") or extract_phone(seller)),
            item_desc="",
            location="",
            item_link=listing_url,
            person_link=str(self._seller_profile_url(seller, nickname, seller_id) or summary.person_link or ""),
            item_person_name=nickname,
        )

    def _get_shop_seller_stats(self, session: BrowserSession, nickname: str) -> dict[str, Any]:
        cache_key = f"shop:{nickname}"
        if cache_key in self._seller_cache:
            return self._seller_cache[cache_key]

        stats = session.fetch_seller_shop_stats(nickname) or {}
        self._seller_cache[cache_key] = stats
        return stats

    def _seller_stats_from_data(
        self,
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

        if cache_key:
            self._seller_cache[cache_key] = stats
        return stats

    def _item_from_summary(self, summary: SearchSummary) -> VoidParserItem:
        parser_views = self._view_counts.get(summary.url, 0)
        self._view_counts[summary.url] = parser_views + 1
        return VoidParserItem(
            item_title=str(summary.title or "").strip(),
            item_photo=str(summary.image or ""),
            item_price=format_price(summary.price),
            item_link=summary.url,
            item_person_name=str(summary.seller_name or ""),
            person_link=str(summary.person_link or ""),
            created_date=str(summary.created_date or ""),
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
                    person_link = str(item.get("person_link") or "")
                    if person_link and not person_link.startswith("http"):
                        person_link = urljoin(self.base_url + "/", person_link)
                    page_summaries.append(
                        SearchSummary(
                            url=full,
                            title=str(item.get("title") or ""),
                            price=item.get("price"),
                            image=str(item.get("image") or ""),
                            seller_name=str(item.get("seller_name") or ""),
                            person_link=person_link,
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
                            person_link=str(item.get("person_link") or ""),
                            created_date=str(item.get("created_date") or ""),
                        )
                    )
                if not page_summaries:
                    for link in self._extract_listing_links(session):
                        page_summaries.append(SearchSummary(url=link))

            if page_summaries:
                next_data = extract_next_data(session)
                extras = {
                    item["url"]: item
                    for item in extract_search_summaries_from_next_data(
                        next_data,
                        locale=self.config.locale,
                        base_url=self.base_url,
                    )
                }
                for summary in page_summaries:
                    extra = extras.get(summary.url)
                    if not extra:
                        continue
                    if not summary.seller_name:
                        summary.seller_name = str(extra.get("seller_name") or "")
                    if not summary.person_link:
                        summary.person_link = str(extra.get("person_link") or "")
                    if not summary.created_date:
                        summary.created_date = str(extra.get("created_date") or "")

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
