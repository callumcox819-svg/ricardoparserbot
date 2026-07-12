from __future__ import annotations

import json
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

from camoufox.sync_api import Camoufox
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from parser.proxy import parse_playwright_proxy
from pin_camoufox_browser import ensure_pinned_browser

logger = logging.getLogger(__name__)

NEXT_DATA_SCRIPT = "document.getElementById('__NEXT_DATA__')?.textContent || null"
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

INTERSTITIAL_PREFIXES = (
    "loading",
    "just a moment",
    "please wait",
    "checking your browser",
    "attention required",
    "ricardo captcha",
)

BLOCKED_HOST_PARTS = (
    "googletagmanager.com",
    "google-analytics.com",
    "hotjar.com",
    "facebook.net",
    "doubleclick.net",
    "criteo.com",
    "taboola.com",
)

ALLOWED_HOST_SUFFIXES = (
    "ricardo.ch",
    "ricardostatic.ch",
    "kxcdn.com",
)

ERROR_SWALLOW_INIT_SCRIPT = """
() => {
  const swallow = (event) => {
    if (event && event.preventDefault) event.preventDefault();
    if (event && event.stopImmediatePropagation) event.stopImmediatePropagation();
    return true;
  };
  window.addEventListener('error', swallow, true);
  window.addEventListener('unhandledrejection', swallow, true);
  window.onerror = () => true;
}
"""


class BrowserSession:
    def __init__(
        self,
        *,
        headless: bool = True,
        locale: str = "de",
        proxy_url: str | None = None,
        cookies_path: str | None = None,
    ):
        self.headless = headless
        self.locale = locale
        self.proxy_url = proxy_url
        self.cookies_path = cookies_path
        self._camoufox: Camoufox | None = None
        self.browser = None
        self.page: Page | None = None
        self._js_context = None
        self._nojs_context = None

    def __enter__(self) -> "BrowserSession":
        ensure_pinned_browser()
        launch_kwargs: dict[str, Any] = {
            "headless": self.headless,
            "humanize": True,
            "locale": f"{self.locale}-CH",
        }
        proxy = parse_playwright_proxy(self.proxy_url)
        if proxy:
            launch_kwargs["proxy"] = proxy
            launch_kwargs["geoip"] = True

        self._camoufox = Camoufox(**launch_kwargs)
        self.browser = self._camoufox.__enter__()
        self._js_context = self.browser.new_context(locale=f"{self.locale}-CH")
        self.page = self._js_context.new_page()
        self._configure_page(self.page)
        self._load_cookies()
        return self

    def switch_to_nojs_mode(self) -> None:
        if not self.page or not self.browser:
            raise RuntimeError("Browser session is not initialized")
        if self._nojs_context is not None:
            return

        cookies = self.page.context.cookies()
        self._nojs_context = self.browser.new_context(
            java_script_enabled=False,
            locale=f"{self.locale}-CH",
        )
        if cookies:
            self._nojs_context.add_cookies(cookies)

    def refresh_search_session(self, search_url: str) -> None:
        if not self.page:
            raise RuntimeError("Browser page is not initialized")
        self.goto(search_url)
        self.wait_for_search_results()

    def fetch_seller_shop_stats(self, nickname: str) -> dict[str, Any] | None:
        if not nickname or not self.browser:
            return None

        from parser.extract import extract_seller_stats_from_shop_html

        self.switch_to_nojs_mode()
        page: Page | None = None
        try:
            page = self._nojs_context.new_page()
            page.set_default_timeout(45000)
            url = f"https://www.ricardo.ch/{self.locale}/shop/{nickname}/offers"
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            html = page.content()
            stats = extract_seller_stats_from_shop_html(html)
            return stats or None
        except Exception as exc:
            logger.warning("Shop fetch failed for %s: %s", nickname, exc)
            return None
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    def fetch_listing_next_data(self, url: str) -> dict[str, Any] | None:
        if not self.browser or not self._js_context:
            raise RuntimeError("Browser session is not initialized")

        for mode in ("js", "nojs"):
            page: Page | None = None
            try:
                if mode == "js":
                    page = self._js_context.new_page()
                    page.set_default_timeout(45000)
                    page.add_init_script(ERROR_SWALLOW_INIT_SCRIPT)
                    page.route("**/*", self._route_strict)
                else:
                    self.switch_to_nojs_mode()
                    page = self._nojs_context.new_page()
                    page.set_default_timeout(45000)

                page.goto(url, timeout=45000, wait_until="domcontentloaded")
                raw = self._read_next_data(page, mode=mode)
                if raw:
                    return json.loads(raw)
            except Exception as exc:
                logger.warning("Listing fetch %s via %s failed: %s", url, mode, exc)
            finally:
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass
        return None

    def _read_next_data(self, page: Page, *, mode: str) -> str | None:
        if mode == "js":
            title = (page.title() or "").lower()
            if title and any(title.startswith(prefix) for prefix in INTERSTITIAL_PREFIXES):
                return None
            return page.evaluate(f"() => {NEXT_DATA_SCRIPT}")

        html = page.content()
        if "Ricardo Captcha" in html or "ricardo captcha" in html.lower():
            return None
        match = NEXT_DATA_RE.search(html)
        return match.group(1) if match else None

    @staticmethod
    def _route_strict(route: Any, request: Any) -> None:
        host = urlparse(request.url).netloc.lower()
        if any(part in host for part in BLOCKED_HOST_PARTS):
            route.abort()
            return
        if request.resource_type == "script" and not any(
            host == suffix or host.endswith(f".{suffix}") for suffix in ALLOWED_HOST_SUFFIXES
        ):
            route.abort()
            return
        route.continue_()

    def fetch_html(self, url: str, *, timeout: int = 45000) -> str:
        if not self.page:
            raise RuntimeError("Browser page is not initialized")
        self.page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        return self.page.content()

    def _configure_page(self, page: Page) -> None:
        page.set_default_timeout(45000)
        page.add_init_script(ERROR_SWALLOW_INIT_SCRIPT)
        page.on("pageerror", self._on_page_error)
        page.on("crash", self._on_page_crash)
        try:
            page.context.on("pageerror", self._on_page_error)
        except Exception:
            pass
        page.route("**/*", self._route_request)

    @staticmethod
    def _route_request(route: Any, request: Any) -> None:
        host = urlparse(request.url).netloc.lower()
        if any(part in host for part in BLOCKED_HOST_PARTS):
            route.abort()
        else:
            route.continue_()

    def is_alive(self) -> bool:
        if not self.page:
            return False
        try:
            self.page.evaluate("() => true")
            return True
        except Exception:
            return False

    def _on_page_error(self, error: BaseException) -> None:
        # Ricardo sometimes throws JS errors; Playwright can crash while reporting them.
        logger.warning("Page JS error (ignored): %s", error)

    def _on_page_crash(self, _page: Page) -> None:
        logger.error("Browser page crashed")

    def __exit__(self, *exc_info: object) -> None:
        self._save_cookies()
        for context in (self._nojs_context, self._js_context):
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
        if self._camoufox is not None:
            try:
                self._camoufox.__exit__(*exc_info)
            except Exception as exc:
                logger.warning("Camoufox shutdown error (ignored): %s", exc)

    def _load_cookies(self) -> None:
        if not self.cookies_path or not os.path.exists(self.cookies_path) or not self.page:
            return
        try:
            with open(self.cookies_path, "r", encoding="utf-8") as file:
                cookies = json.load(file)
            if cookies:
                self.page.context.add_cookies(cookies)
        except Exception:
            pass

    def _save_cookies(self) -> None:
        if not self.cookies_path or not self.page:
            return
        try:
            os.makedirs(os.path.dirname(self.cookies_path) or ".", exist_ok=True)
            cookies = self.page.context.cookies()
            with open(self.cookies_path, "w", encoding="utf-8") as file:
                json.dump(cookies, file, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def export_cookies_for_requests(self) -> dict[str, str]:
        if not self.page:
            return {}
        cookies: dict[str, str] = {}
        for cookie in self.page.context.cookies():
            name = cookie.get("name")
            value = cookie.get("value")
            if name and value is not None:
                cookies[name] = value
        return cookies

    def _safe_title(self) -> str:
        if not self.page:
            return ""
        try:
            return (self.page.title() or "").strip()
        except PlaywrightError as exc:
            logger.warning("title() failed: %s", exc)
            return ""

    def _safe_content(self) -> str:
        if not self.page:
            return ""
        try:
            return self.page.content()
        except PlaywrightError as exc:
            logger.warning("content() failed: %s", exc)
            return ""

    def goto(self, url: str, *, attempts: int = 4, settle_ms: int = 1500) -> None:
        if not self.page:
            raise RuntimeError("Browser page is not initialized")

        is_search = f"/{self.locale}/s/" in url or url.rstrip("/").endswith(f"/{self.locale}/s")
        listing_marker = f"/{self.locale}/a/"

        for attempt in range(attempts):
            try:
                self.page.goto(url, timeout=90000, wait_until="domcontentloaded")
            except PlaywrightError as exc:
                if attempt < attempts - 1 and "interrupted by another navigation" in str(exc).lower():
                    self.page.wait_for_timeout(settle_ms)
                    continue
                raise

            for tick in range(30):
                self.page.wait_for_timeout(settle_ms)
                title = self._safe_title().lower()
                if title and any(title.startswith(prefix) for prefix in INTERSTITIAL_PREFIXES):
                    continue

                body = self._safe_content()
                if not body:
                    continue
                if 'data-testid="regular-results"' in body:
                    return
                if listing_marker in body and (is_search or listing_marker in url):
                    return
                if "__NEXT_DATA__" in body and not is_search:
                    return
                if is_search and "__NEXT_DATA__" in body and listing_marker in body:
                    return
        raise RuntimeError(f"Не удалось пройти защиту ricardo.ch для {url}")

    def wait_for_search_results(self, *, attempts: int = 12, wait_ms: int = 1500) -> bool:
        if not self.page:
            raise RuntimeError("Browser page is not initialized")

        listing_marker = f"/{self.locale}/a/"
        script = f"""() => {{
          if (document.querySelector('[data-testid="regular-results"]')) return true;
          return document.querySelectorAll('a[href*="{listing_marker}"]').length > 0;
        }}"""
        return bool(self.evaluate_with_retry(script, attempts=attempts, wait_ms=wait_ms))

    def evaluate(self, script: str) -> Any:
        if not self.page:
            raise RuntimeError("Browser page is not initialized")
        try:
            return self.page.evaluate(script)
        except PlaywrightError as exc:
            logger.warning("evaluate() failed: %s", exc)
            return None

    def evaluate_with_retry(self, script: str, *, attempts: int = 4, wait_ms: int = 1500) -> Any:
        for attempt in range(attempts):
            result = self.evaluate(script)
            if result:
                return result
            if attempt < attempts - 1 and self.page:
                try:
                    self.page.wait_for_timeout(wait_ms)
                except PlaywrightError:
                    return None
        return None

    def wait_for_next_data(self, *, attempts: int = 8, wait_ms: int = 1500) -> bool:
        script = "document.getElementById('__NEXT_DATA__')?.textContent || null"
        return bool(self.evaluate_with_retry(script, attempts=attempts, wait_ms=wait_ms))
