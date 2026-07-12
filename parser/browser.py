from __future__ import annotations

import json
import os
from typing import Any

from camoufox.sync_api import Camoufox
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from parser.proxy import parse_playwright_proxy
from pin_camoufox_browser import ensure_pinned_browser

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
        self._load_cookies()
        return self

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

    def goto(self, url: str, *, attempts: int = 4, settle_ms: int = 2000) -> None:
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

            for _ in range(40):
                self.page.wait_for_timeout(settle_ms)
                title = (self.page.title() or "").strip().lower()
                if title and any(title.startswith(prefix) for prefix in INTERSTITIAL_PREFIXES):
                    continue

                body = self.page.content()
                if 'data-testid="regular-results"' in body:
                    return
                if listing_marker in body and (is_search or listing_marker in url):
                    return
                if "__NEXT_DATA__" in body and not is_search:
                    return
        raise RuntimeError(f"Не удалось пройти защиту ricardo.ch для {url}")

    def wait_for_search_results(self, *, attempts: int = 15, wait_ms: int = 2000) -> bool:
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
        return self.page.evaluate(script)

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
