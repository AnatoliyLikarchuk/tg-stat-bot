"""
Telegram-бот для сбора статистики логистики.
Парсит сообщения из групп и сохраняет в Google Sheets.
"""

import asyncio
import logging
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Клавиатура с командами
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📊 Статистика сегодня"), KeyboardButton("📈 За неделю")],
        [KeyboardButton("🚗 Активные маршруты"), KeyboardButton("❓ Помощь")]
    ],
    resize_keyboard=True
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


def check_access(update: Update) -> bool:
    """Проверяет доступ пользователя."""
    if not update.effective_user:
        return False
    return config.is_user_allowed(update.effective_user.id)


async def access_denied(update: Update):
    """Отправляет сообщение об отказе в доступе."""
    await update.message.reply_text("⛔ Доступ запрещён")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    if not check_access(update):
        await access_denied(update)
        return
    await update.message.reply_text(
        "Привет! Я бот для сбора статистики логистики.\n\n"
        "Используй кнопки ниже для работы со статистикой.",
        reply_markup=MAIN_KEYBOARD
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help."""
    if not check_access(update):
        await access_denied(update)
        return
    await update.message.reply_text(
        "📊 Бот статистики логистики\n\n"
        "Отслеживаемые события:\n"
        "• Начало сборки\n"
        "• Сборка завершена\n"
        "• Выезд маршрута\n"
        "• Завершение маршрута\n"
        "• Проблемы доставки\n\n"
        "Добавь бота в группу логистики, и он будет автоматически "
        "парсить сообщения и сохранять статистику."
    )


async def stats_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за сегодня."""
    if not check_access(update):
        await access_denied(update)
        return
    stats = sheets_manager.get_today_stats()

    if not stats or stats.get("total_events", 0) == 0:
        await update.message.reply_text("📊 За сегодня событий пока нет.")
        return

    text = format_stats(stats, "сегодня")
    await update.message.reply_text(text, parse_mode="Markdown")


async def stats_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за неделю."""
    if not check_access(update):
        await access_denied(update)
        return
    stats = sheets_manager.get_stats_for_period(7)

    if not stats or stats.get("total_events", 0) == 0:
        await update.message.reply_text("📊 За последние 7 дней событий нет.")
        return

    text = format_stats(stats, "неделю")
    await update.message.reply_text(text, parse_mode="Markdown")


async def active_routes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает активные маршруты."""
    if not check_access(update):
        await access_denied(update)
        return
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


async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки."""
    if not check_access(update):
        await access_denied(update)
        return
    text = update.message.text

    if text == "📊 Статистика сегодня":
        await stats_today(update, context)
    elif text == "📈 За неделю":
        await stats_week(update, context)
    elif text == "🚗 Активные маршруты":
        await active_routes(update, context)
    elif text == "❓ Помощь":
        await help_command(update, context)


async def handle_private_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик произвольного текста в личных сообщениях."""
    if not check_access(update):
        await access_denied(update)
        return
    await update.message.reply_text(
        "Используй кнопки ниже для работы со статистикой 👇",
        reply_markup=MAIN_KEYBOARD
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик всех текстовых сообщений в группе."""
    if not update.message or not update.message.text:
        return

    # В группах парсим сообщения от всех (белый список только для команд)
    text = update.message.text

    # Игнорируем кнопки в группах
    if text in ["📊 Статистика сегодня", "📈 За неделю", "🚗 Активные маршруты", "❓ Помощь"]:
        return

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

    # Создаём приложение с увеличенным таймаутом
    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .build()
    )

    # Регистрируем обработчик /start
    app.add_handler(CommandHandler("start", start))

    # Обработчик кнопок в личных сообщениях
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE &
        filters.Regex(r"^(📊 Статистика сегодня|📈 За неделю|🚗 Активные маршруты|❓ Помощь)$"),
        handle_buttons
    ))

    # Обработчик произвольного текста в личных сообщениях
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_private_text
    ))

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
