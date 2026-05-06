"""
Telegram-бот для сбора статистики логистики.
Парсит сообщения из групп и сохраняет в Google Sheets.
"""

import asyncio
import logging
import time as time_module
from datetime import datetime, timedelta
import pytz
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReactionTypeEmoji
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
        [KeyboardButton("🚗 Активные маршруты"), KeyboardButton("❓ Помощь")],
        [KeyboardButton("📏 Километраж за неделю")],
    ],
    resize_keyboard=True
)

BUTTON_LABELS = {
    "📊 Статистика сегодня",
    "📈 За неделю",
    "🚗 Активные маршруты",
    "❓ Помощь",
    "📏 Километраж за неделю",
}

from config import config
from parser import MessageParser
from sheets import sheets_manager
from scheduler import setup_scheduler

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация парсера
parser = MessageParser()

# Дедупликация: {(chat_id, event_type, route, driver): timestamp}
_recent_events: dict = {}
DEDUP_WINDOW_SEC = 60


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
    await update.message.reply_text(text)


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
    await update.message.reply_text(text)


async def active_routes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает активные маршруты."""
    if not check_access(update):
        await access_denied(update)
        return
    routes = sheets_manager.get_active_routes()

    if not routes:
        await update.message.reply_text("🚗 Активных маршрутов нет.")
        return

    text = "🚗 Активные маршруты:\n\n"
    for r in routes:
        route_num = r['route'] or '?'
        driver = r['driver'] or ''
        status = r['status'].replace('_', ' ')  # маршрут_завершён → маршрут завершён
        time = r['time'] or ''

        text += f"• Маршрут {route_num}"
        if driver:
            text += f" ({driver})"
        text += f" — {status} в {time}\n"

    await update.message.reply_text(text)


def format_stats(stats: dict, period: str) -> str:
    """Форматирует статистику для вывода."""
    text = f"📊 Статистика за {period}\n\n"
    text += f"Всего событий: {stats['total_events']}\n\n"

    if stats.get("by_type"):
        text += "По типам:\n"
        type_names = {
            "начало_сборки": "🔧 Начало сборки",
            "сборка_завершена": "✅ Сборка завершена",
            "выезд": "🚗 Выезд",
            "маршрут_завершён": "🏁 Маршрут завершён",
            "все_выехали": "🎉 Все выехали",
            "проблема": "⚠️ Проблемы"
        }
        for event_type, count in stats["by_type"].items():
            name = type_names.get(event_type, event_type.replace('_', ' '))
            text += f"  {name}: {count}\n"
        text += "\n"

    if stats.get("by_driver"):
        text += "По водителям:\n"
        for driver, count in sorted(stats["by_driver"].items(), key=lambda x: -x[1]):
            text += f"  • {driver}: {count} событий\n"
        text += "\n"

    if stats.get("problems"):
        text += f"Проблемы ({len(stats['problems'])}):\n"
        for problem in stats["problems"][:5]:  # Максимум 5
            text += f"  — {problem[:50]}...\n"

    return text


async def mileage_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Километраж по водителям за последние 7 дней."""
    if not check_access(update):
        await access_denied(update)
        return
    rows = sheets_manager.get_weekly_mileage()

    if not rows:
        await update.message.reply_text("📏 За последние 7 дней пробег не записан.")
        return

    today = datetime.now(pytz.timezone(config.TIMEZONE)).date()
    start = today - timedelta(days=6)
    text = f"📏 Километраж {start:%d.%m}—{today:%d.%m}\n\n"
    total = 0
    for r in rows:
        text += f"  • {r['driver']}: {r['km']} км\n"
        total += r["km"]
    text += f"\nИтого: {total} км"
    await update.message.reply_text(text)


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
    elif text == "📏 Километраж за неделю":
        await mileage_week(update, context)


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
    """Обработчик всех текстовых сообщений в группе (включая отредактированные)."""
    msg = update.message or update.edited_message
    if not msg:
        return

    is_edited = update.edited_message is not None

    # Получаем текст из message.text или message.caption (для фото/документов)
    text = msg.text or msg.caption
    if not text:
        return

    # DEBUG: логируем все сообщения из групп
    is_caption = msg.caption is not None
    edit_tag = "(edited)" if is_edited else ""
    caption_tag = "(caption)" if is_caption else ""
    logger.info(f"[DEBUG] Получено{edit_tag}{caption_tag}: '{text[:100]}' от {update.effective_user.first_name if update.effective_user else 'unknown'}")

    # Игнорируем кнопки в группах
    if text in BUTTON_LABELS:
        return

    events = parser.parse(text)

    # DEBUG: логируем результат парсинга
    if events:
        logger.info(f"[DEBUG] Распознано {len(events)} событий: {[e.event_type for e in events]}")
    else:
        logger.debug(f"[DEBUG] Не распознано: '{text[:50]}'")

    if not events:
        return  # Сообщение не содержит логистических событий

    # Дедупликация: фильтруем события, которые уже были недавно
    chat_id = update.effective_chat.id if update.effective_chat else 0
    now = time_module.time()

    # Чистим старые записи (старше 5 минут)
    cutoff = now - 300
    stale_keys = [k for k, t in _recent_events.items() if t < cutoff]
    for k in stale_keys:
        del _recent_events[k]

    unique_events = []
    event_keys = []  # параллельный список ключей дедупа для rollback при ошибке
    for event in events:
        key = (chat_id, event.event_type, event.route_number, event.driver)
        if key in _recent_events and (now - _recent_events[key]) < DEDUP_WINDOW_SEC:
            logger.info(f"[DEDUP] Пропущен дубликат: {event.event_type} маршрут={event.route_number} водитель={event.driver}")
            continue
        _recent_events[key] = now
        unique_events.append(event)
        event_keys.append(key)

    events = unique_events
    if not events:
        return  # Все события — дубликаты

    # Получаем название группы
    group_name = ""
    if update.effective_chat:
        group_name = update.effective_chat.title or ""

    # Сохраняем события по одному — чтобы откатить дедуп для тех,
    # которые не записались (тогда следующая копия сообщения сможет повторить запись)
    saved = 0
    tz = pytz.timezone(config.TIMEZONE)
    for event, key in zip(events, event_keys):
        if event.event_type == parser.EVENT_MILEAGE:
            ok = sheets_manager.upsert_mileage(
                event.driver, event.mileage_km, datetime.now(tz)
            )
        else:
            ok = sheets_manager.add_event(event, group_name)

        if ok:
            saved += 1
        else:
            # Запись не удалась — убираем из дедупа, чтобы retry-копия прошла
            _recent_events.pop(key, None)
            logger.warning(f"[DEDUP] Откат кэша после ошибки записи: {event.event_type} маршрут={event.route_number} водитель={event.driver}")

    if saved > 0:
        logger.info(f"Сохранено {saved} событий из группы '{group_name}'")
        # Ставим реакцию 🏆 как подтверждение записи
        try:
            await msg.set_reaction(reaction=[ReactionTypeEmoji("🏆")])
        except Exception as e:
            logger.warning(f"Не удалось поставить реакцию: {e}")

        # Проверка цепочки событий и предупреждения
        tz = pytz.timezone(config.TIMEZONE)
        today_str = datetime.now(tz).strftime("%d.%m.%Y")
        for event in events:
            try:
                # Предупреждение: выезд без номера маршрута
                if event.event_type == "выезд" and not event.route_number and event.driver:
                    warn_text = (
                        f"⚠️ {event.driver} — виїзд зафіксовано, "
                        f"але номер маршруту не вказано. Вкажіть номер маршруту."
                    )
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=warn_text
                    )
                    logger.info(f"[WARN] Выезд без номера маршрута: {event.driver}")

                # Проверка несоответствия маршрутов: водитель закрыл не тот маршрут
                route_mismatch = False
                if event.event_type == "маршрут_завершён" and event.driver and event.route_number:
                    departed_route = sheets_manager.get_driver_departure_route(event.driver, today_str)
                    if departed_route and departed_route != event.route_number:
                        route_mismatch = True
                        warn_text = (
                            f"⚠️ Увага: {event.driver} виїхав на маршрут {departed_route}, "
                            f"але закрив маршрут {event.route_number}. Перевірте номер маршруту."
                        )
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=warn_text
                        )
                        logger.info(f"[WARN] Несоответствие маршрутов: {event.driver} выехал {departed_route}, закрыл {event.route_number}")

                # Проверка цепочки: начало_сборки → сборка_завершена → выезд → маршрут_завершён
                # Пропускаем если уже сработало несоответствие маршрутов (более информативно)
                if event.route_number and not route_mismatch:
                    missing = sheets_manager.check_chain_violation(
                        event.event_type, event.route_number, today_str
                    )
                    if missing:
                        driver_info = f" ({event.driver})" if event.driver else ""
                        warn_text = (
                            f"⚠️ Маршрут {event.route_number}{driver_info}: "
                            f"зафіксовано «{event.event_type}», але не було: {missing}"
                        )
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=warn_text
                        )
                        logger.info(f"[WARN] Нарушение цепочки: маршрут {event.route_number} — {event.event_type}, не хватает: {missing}")
            except Exception as e:
                logger.warning(f"Ошибка проверки цепочки событий: {e}")

        # Проверяем: если закрыли последний маршрут — уведомляем группу
        has_route_completed = any(e.event_type == "маршрут_завершён" for e in events)
        if has_route_completed and config.REPORT_CHAT_ID:
            # Небольшая задержка чтобы Google API синхронизировал данные
            await asyncio.sleep(1)
            active_routes = sheets_manager.get_active_routes()
            logger.info(f"[DEBUG] Проверка завершения: активных маршрутов = {len(active_routes)}")
            if active_routes:
                logger.info(f"[DEBUG] Активные: {[r.get('route') for r in active_routes]}")
            if not active_routes:
                try:
                    # Проверяем время для благодарности
                    now = datetime.now(tz)
                    if now.hour < 19:
                        text = "✅ Всі маршрути завершені\n\n🎉 Сьогодні усі колеги-водії завершили до 19:00. Дякуємо! 👏🚚"
                    else:
                        text = "✅ Всі маршрути завершені"

                    await context.bot.send_message(
                        chat_id=config.REPORT_CHAT_ID,
                        text=text
                    )
                    logger.info(f"Отправлено уведомление: все маршруты завершены (hour={now.hour})")
                except Exception as e:
                    logger.warning(f"Не удалось отправить уведомление о завершении: {e}")


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
        filters.Regex(r"^(📊 Статистика сегодня|📈 За неделю|🚗 Активные маршруты|❓ Помощь|📏 Километраж за неделю)$"),
        handle_buttons
    ))

    # Обработчик произвольного текста в личных сообщениях
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_private_text
    ))

    # Обработчик всех текстовых сообщений в группах (включая подписи к фото/документам)
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.CAPTION) & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
        handle_message
    ))

    # Обработчик отредактированных сообщений в группах
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.CAPTION) & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP)
        & filters.UpdateType.EDITED_MESSAGE,
        handle_message
    ))

    # Обработчик ошибок
    app.add_error_handler(error_handler)

    # Запускаем планировщик автоотчётов (с поддержкой timezone)
    setup_scheduler(app)

    # Запуск
    logger.info("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
