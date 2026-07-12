from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, Message

from config import Settings, get_settings
from parser.models import VoidParserResult
from parser.ricardo import ParserConfig, RicardoParser

settings: Settings | None = None
active_tasks: dict[int, asyncio.Task] = {}
last_results: dict[int, str] = {}


def ensure_data_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


async def run_parse_job(user_id: int, bot: Bot, start_url: str) -> None:
    cfg = get_settings()
    ensure_data_dir(cfg.data_dir)

    async def progress(message: str) -> None:
        try:
            await bot.send_message(user_id, f"ℹ️ {message}")
        except Exception:
            pass

    loop = asyncio.get_running_loop()

    def sync_progress(message: str) -> None:
        asyncio.run_coroutine_threadsafe(progress(message), loop)

    parser = RicardoParser(
        ParserConfig(
            locale=cfg.locale,
            max_pages=cfg.max_pages,
            max_items=cfg.max_items,
            headless=cfg.headless,
            proxy_url=cfg.proxy_url,
            cookies_path=cfg.cookies_path,
        )
    )

    try:
        await progress("Парсинг запущен...")
        result: VoidParserResult = await parser.parse(start_url, progress=sync_progress)
        timestamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        filename = f"void-parser-result {timestamp}.json"
        filepath = os.path.join(cfg.data_dir, filename)
        payload = result.to_dict()
        with open(filepath, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)

        last_results[user_id] = filepath
        await bot.send_message(
            user_id,
            f"✅ Готово. Собрано объявлений: {len(result.items)}",
        )
        with open(filepath, "rb") as file:
            await bot.send_document(
                user_id,
                BufferedInputFile(file.read(), filename=filename),
                caption="Результат в формате void-parser",
            )
    except Exception as exc:
        await bot.send_message(user_id, f"❌ Ошибка парсинга: {exc}")
    finally:
        active_tasks.pop(user_id, None)


def is_ricardo_url(text: str) -> bool:
    lowered = text.lower().strip()
    return "ricardo.ch" in lowered and ("/s/" in lowered or "/c/" in lowered or "/a/" in lowered or "/q/" in lowered)


async def on_start(message: Message) -> None:
    await message.answer(
        "Привет! Я парсер ricardo.ch.\n\n"
        "Команды:\n"
        "/parse <url> — запустить парсинг\n"
        "/status — статус задачи\n"
        "/stop — остановить парсинг\n"
        "/results — отправить последний JSON\n\n"
        "Пример:\n"
        "/parse https://www.ricardo.ch/de/s/?q=laptop\n\n"
        "Токен бота берётся из Railway Variable `BOT_TOKEN`."
    )


async def start_parse(message: Message, start_url: str) -> None:
    user_id = message.from_user.id
    if user_id in active_tasks and not active_tasks[user_id].done():
        await message.answer("Уже идёт парсинг. Сначала /stop или дождись завершения.")
        return

    task = asyncio.create_task(run_parse_job(user_id, message.bot, start_url))
    active_tasks[user_id] = task
    await message.answer("Запускаю парсинг. Это может занять несколько минут.")


async def on_parse(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Укажи URL: /parse https://www.ricardo.ch/de/s/?q=laptop")
        return

    start_url = parts[1].strip()
    if not is_ricardo_url(start_url):
        await message.answer("Нужен URL ricardo.ch: поиск, категория или объявление.")
        return

    await start_parse(message, start_url)


async def on_status(message: Message) -> None:
    user_id = message.from_user.id
    task = active_tasks.get(user_id)
    if task and not task.done():
        await message.answer("Статус: парсинг выполняется.")
        return
    if user_id in last_results:
        await message.answer(f"Статус: последний файл — {last_results[user_id]}")
        return
    await message.answer("Статус: задач нет.")


async def on_stop(message: Message) -> None:
    user_id = message.from_user.id
    task = active_tasks.get(user_id)
    if not task or task.done():
        await message.answer("Нет активного парсинга.")
        return
    task.cancel()
    active_tasks.pop(user_id, None)
    await message.answer("Парсинг остановлен.")


async def on_results(message: Message) -> None:
    user_id = message.from_user.id
    filepath = last_results.get(user_id)
    if not filepath or not os.path.exists(filepath):
        await message.answer("Пока нет сохранённого результата.")
        return
    with open(filepath, "rb") as file:
        await message.answer_document(
            BufferedInputFile(file.read(), filename=os.path.basename(filepath)),
            caption="Последний результат",
        )


async def on_url_message(message: Message) -> None:
    text = (message.text or "").strip()
    if not text.startswith("http") or not is_ricardo_url(text):
        return
    await start_parse(message, text)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.message.register(on_start, CommandStart())
    dp.message.register(on_parse, Command("parse"))
    dp.message.register(on_status, Command("status"))
    dp.message.register(on_stop, Command("stop"))
    dp.message.register(on_results, Command("results"))
    dp.message.register(on_url_message, F.text.startswith("http"))
    return dp


async def main() -> None:
    global settings
    settings = get_settings()
    ensure_data_dir(settings.data_dir)
    bot = Bot(token=settings.bot_token)
    dp = build_dispatcher()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
