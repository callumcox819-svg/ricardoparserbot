# Ricardo Parser Bot

Telegram-бот для парсинга `ricardo.ch` с выдачей JSON в формате `void-parser`.

## Переменные Railway

| Variable | Обязательно | Описание |
|----------|-------------|----------|
| `BOT_TOKEN` | да | токен Telegram-бота |
| `PROXY_URL` | нет | прокси, например `http://user:pass@host:port` |
| `MAX_PAGES` | нет | страниц поиска, по умолчанию `5` |
| `MAX_ITEMS` | нет | лимит объявлений, по умолчанию `100` |
| `HEADLESS` | нет | `true` / `false`, по умолчанию `true` |
| `LOCALE` | нет | `de`, `fr`, `it` |

## Команды бота

- `/start`
- `/parse <url>`
- `/status`
- `/stop`
- `/results`

## Локальный запуск

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
set BOT_TOKEN=your_token
python -m bot.main
```

## Деплой на Railway

1. Подключи репозиторий `ricardoparserbot`.
2. Добавь Variable `BOT_TOKEN`.
3. При необходимости добавь `PROXY_URL`.
4. Deploy.

Проект использует Docker с Playwright Chromium.
