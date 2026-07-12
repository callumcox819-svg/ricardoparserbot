import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    proxy_url: str | None
    max_pages: int
    max_items: int
    headless: bool
    locale: str
    data_dir: str
    cookies_path: str
    enrich_details: bool


def get_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is not set. Add it in Railway Variables.")

    proxy = os.getenv("PROXY_URL", "").strip() or None
    max_pages = int(os.getenv("MAX_PAGES", "5"))
    max_items = int(os.getenv("MAX_ITEMS", "100"))
    headless = os.getenv("HEADLESS", "true").lower() in {"1", "true", "yes", "on"}
    enrich_details = os.getenv("ENRICH_DETAILS", "false").lower() in {"1", "true", "yes", "on"}
    locale = os.getenv("LOCALE", "de").strip() or "de"
    data_dir = os.getenv("DATA_DIR", "data").strip() or "data"
    cookies_path = os.getenv("COOKIES_PATH", os.path.join(data_dir, "ricardo_cookies.json"))

    return Settings(
        bot_token=token,
        proxy_url=proxy,
        max_pages=max_pages,
        max_items=max_items,
        headless=headless,
        locale=locale,
        data_dir=data_dir,
        cookies_path=cookies_path,
        enrich_details=enrich_details,
    )
