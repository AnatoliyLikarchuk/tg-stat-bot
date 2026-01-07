"""
Telegram-бот для сбора статистики логистики.
Парсит сообщения из групп и сохраняет в Google Sheets.
"""

import asyncio
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import config
from parser import MessageParser
from sheets import sheets_manager

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация парсера
parser = MessageParser()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    await update.message.reply_text(
        "Привет! Я бот для сбора статистики логистики.\n\n"
        "Команды:\n"
        "/stats — статистика за сегодня\n"
        "/stats_week — статистика за неделю\n"
        "/routes — активные маршруты\n"
        "/help — справка"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help."""
    await update.message.reply_text(
        "📊 *Бот статистики логистики*\n\n"
        "*Отслеживаемые события:*\n"
        "• Начало сборки\n"
        "• Сборка завершена\n"
        "• Выезд маршрута\n"
        "• Завершение маршрута\n"
        "• Проблемы доставки\n\n"
        "*Команды:*\n"
        "/stats — статистика за сегодня\n"
        "/stats_week — за последние 7 дней\n"
        "/routes — текущие активные маршруты\n\n"
        "Добавьте бота в группу логистики, и он будет автоматически "
        "парсить сообщения и сохранять статистику.",
        parse_mode="Markdown"
    )


async def stats_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за сегодня."""
    stats = sheets_manager.get_today_stats()

    if not stats or stats.get("total_events", 0) == 0:
        await update.message.reply_text("📊 За сегодня событий пока нет.")
        return

    text = format_stats(stats, "сегодня")
    await update.message.reply_text(text, parse_mode="Markdown")


async def stats_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за неделю."""
    stats = sheets_manager.get_stats_for_period(7)

    if not stats or stats.get("total_events", 0) == 0:
        await update.message.reply_text("📊 За последние 7 дней событий нет.")
        return

    text = format_stats(stats, "неделю")
    await update.message.reply_text(text, parse_mode="Markdown")


async def active_routes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает активные маршруты."""
    routes = sheets_manager.get_active_routes()

    if not routes:
        await update.message.reply_text("🚗 Активных маршрутов нет.")
        return

    text = "🚗 *Активные маршруты:*\n\n"
    for r in routes:
        text += f"• Маршрут {r['route']}"
        if r["driver"]:
            text += f" ({r['driver']})"
        text += f" — {r['status']} в {r['time']}\n"

    await update.message.reply_text(text, parse_mode="Markdown")


def format_stats(stats: dict, period: str) -> str:
    """Форматирует статистику для вывода."""
    text = f"📊 *Статистика за {period}*\n\n"
    text += f"Всего событий: {stats['total_events']}\n\n"

    if stats.get("by_type"):
        text += "*По типам:*\n"
        type_names = {
            "начало_сборки": "🔧 Начало сборки",
            "сборка_завершена": "✅ Сборка завершена",
            "выезд": "🚗 Выезд",
            "маршрут_завершён": "🏁 Маршрут завершён",
            "все_выехали": "🎉 Все выехали",
            "проблема": "⚠️ Проблемы"
        }
        for event_type, count in stats["by_type"].items():
            name = type_names.get(event_type, event_type)
            text += f"  {name}: {count}\n"
        text += "\n"

    if stats.get("by_driver"):
        text += "*По водителям:*\n"
        for driver, count in sorted(stats["by_driver"].items(), key=lambda x: -x[1]):
            text += f"  • {driver}: {count} событий\n"
        text += "\n"

    if stats.get("problems"):
        text += f"*Проблемы ({len(stats['problems'])}):\n*"
        for problem in stats["problems"][:5]:  # Максимум 5
            text += f"  — {problem[:50]}...\n"

    return text


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик всех текстовых сообщений в группе."""
    if not update.message or not update.message.text:
        return

    text = update.message.text
    events = parser.parse(text)

    if not events:
        return  # Сообщение не содержит логистических событий

    # Получаем название группы
    group_name = ""
    if update.effective_chat:
        group_name = update.effective_chat.title or ""

    # Сохраняем события
    saved = sheets_manager.add_events(events, group_name)

    if saved > 0:
        logger.info(f"Сохранено {saved} событий из группы '{group_name}'")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок."""
    logger.error(f"Update {update} caused error: {context.error}")


def main():
    """Запуск бота."""
    # Проверяем конфигурацию
    if not config.validate():
        return

    # Подключаемся к Google Sheets
    if not sheets_manager.connect():
        logger.warning("Google Sheets не подключен, данные не будут сохраняться")

    # Создаём приложение
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Регистрируем обработчики команд
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_today))
    app.add_handler(CommandHandler("stats_week", stats_week))
    app.add_handler(CommandHandler("routes", active_routes))

    # Обработчик всех текстовых сообщений в группах
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
        handle_message
    ))

    # Обработчик ошибок
    app.add_error_handler(error_handler)

    # Запуск
    logger.info("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
