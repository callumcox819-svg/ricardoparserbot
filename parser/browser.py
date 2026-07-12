from __future__ import annotations

import json
import logging
import os
from typing import Any

from camoufox.sync_api import Camoufox
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from parser.proxy import parse_playwright_proxy
from pin_camoufox_browser import ensure_pinned_browser

logger = logging.getLogger(__name__)

INTERSTITIAL_PREFIXES = (
    "loading",
    "just a moment",
    "please wait",
    "checking your browser",
    "attention required",
    "ricardo captcha",
)


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
        self.page = self.browser.new_page()
        self.page.set_default_timeout(45000)
        self.page.on("pageerror", self._on_page_error)
        self.page.on("crash", self._on_page_crash)
        self._load_cookies()
        return self

    def _on_page_error(self, error: BaseException) -> None:
        # Ricardo sometimes throws JS errors; Playwright can crash while reporting them.
        logger.warning("Page JS error (ignored): %s", error)

    def _on_page_crash(self, _page: Page) -> None:
        logger.error("Browser page crashed")

    def __exit__(self, *exc_info: object) -> None:
        self._save_cookies()
        if self._camoufox is not None:
            self._camoufox.__exit__(*exc_info)

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
            if attempt < attempts - 1:
                self.page.wait_for_timeout(wait_ms)
        return None

    def wait_for_next_data(self, *, attempts: int = 8, wait_ms: int = 1500) -> bool:
        script = "document.getElementById('__NEXT_DATA__')?.textContent || null"
        return bool(self.evaluate_with_retry(script, attempts=attempts, wait_ms=wait_ms))
