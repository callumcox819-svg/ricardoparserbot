from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    n1 = n % 10
    if 11 <= n <= 19:
        return many
    if n1 == 1:
        return one
    if 2 <= n1 <= 4:
        return few
    return many


def relative_time_ru(dt: datetime | None) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 0:
        seconds = 0

    if seconds < 60:
        word = _plural_ru(seconds, "секунда", "секунды", "секунд")
        return f"{seconds} {word} назад"

    minutes = seconds // 60
    if minutes < 60:
        word = _plural_ru(minutes, "минута", "минуты", "минут")
        return f"{minutes} {word} назад"

    hours = minutes // 60
    if hours < 24:
        word = _plural_ru(hours, "час", "часа", "часов")
        return f"{hours} {word} назад"

    days = hours // 24
    if days < 30:
        word = _plural_ru(days, "день", "дня", "дней")
        return f"{days} {word} назад"

    months = days // 30
    if months < 12:
        word = _plural_ru(months, "месяц", "месяца", "месяцев")
        return f"{months} {word} назад"

    years = days // 365
    word = _plural_ru(years, "год", "года", "лет")
    return f"{years} {word} назад"


def format_price(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        if float(value).is_integer():
            return f"{int(value)} .-"
        return f"{value} .-"
    text = str(value).strip()
    if text.endswith(".-"):
        return text
    digits = "".join(ch for ch in text if ch.isdigit() or ch == ".")
    if digits:
        return f"{digits} .-"
    return text


def rating_from_score(score: Any) -> int:
    if score is None:
        return 0
    try:
        value = float(score)
    except (TypeError, ValueError):
        return 0
    if value <= 1:
        return int(round(value * 100))
    return int(round(value))


def pick_image_url(images: Any) -> str:
    if not images:
        return ""
    if isinstance(images, str):
        return images
    if isinstance(images, list):
        first = images[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            for key in ("url", "bigSizeUrl", "previewUrl", "image"):
                if first.get(key):
                    return str(first[key])
    return ""


def deep_get(data: Any, *keys: str, default: Any = None) -> Any:
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.isdigit() and len(text) == 4:
        return datetime(int(text), 1, 1, tzinfo=timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
