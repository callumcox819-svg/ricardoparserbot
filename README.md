# Ricardo Parser Bot

Telegram-бот для парсинга `ricardo.ch` с выдачей JSON в формате `void-parser`.

Парсер использует **Camoufox** (антидетект Firefox), а не обычный Playwright Chromium. Это нужно для прохождения Cloudflare на `ricardo.ch`.

## Railway Variables

| Variable | Обязательно | Описание |
|----------|-------------|----------|
| `BOT_TOKEN` | да | токен Telegram-бота |
| `PROXY_URL` | рекомендуется | `http://user:pass@host:port` |
| `MAX_PAGES` | нет | страниц поиска, по умолчанию `5` |
| `MAX_ITEMS` | нет | лимит объявлений, по умолчанию `100` |
| `HEADLESS` | нет | `true` / `false` |
| `LOCALE` | нет | `de`, `fr`, `it` |
| `COOKIES_PATH` | нет | путь к cookies, по умолчанию `data/ricardo_cookies.json` |

Пример:

```env
BOT_TOKEN=123456:ABC...
PROXY_URL=http://fVLm8A9YP4:0NH53d4bHa@proxy.lomaproxy.com:38175
MAX_PAGES=3
MAX_ITEMS=50
HEADLESS=true
```

## Команды бота

- `/start`
- `/parse <url>`
- `/status`
- `/stop`
- `/results`

Можно просто отправить URL `ricardo.ch` в чат.

## Формат результата

```json
{
  "items": [
    {
      "item_title": "...",
      "item_photo": "...",
      "ads_number": 5,
      "parser_views": 0,
      "ads_number_bought": 34,
      "ads_number_sold": 632,
      "item_price": "12 .-",
      "rating": 91,
      "created_date": "5 минут назад",
      "item_link": "https://www.ricardo.ch/de/a/.../",
      "item_person_name": "seller"
    }
  ]
}
```

## Деплой на Railway

1. Подключи репозиторий.
2. Railway соберёт `Dockerfile` (Linux + pinned Camoufox).
3. Добавь `BOT_TOKEN` и `PROXY_URL`.
4. Deploy.

## Локальный запуск через Docker

```bash
docker build -t ricardoparserbot .
docker run --rm -e BOT_TOKEN=your_token -e PROXY_URL=http://user:pass@host:port ricardoparserbot
```

На Windows без Docker локальный Camoufox может быть нестабилен; для продакшена используй Docker/Railway.
