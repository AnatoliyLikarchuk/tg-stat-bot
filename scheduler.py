"""
Планировщик автоматических отчётов.
Использует JobQueue из python-telegram-bot с поддержкой timezone.
"""

import logging
from datetime import time
import pytz
from telegram.ext import ContextTypes

from config import config
from sheets import sheets_manager
import core

logger = logging.getLogger(__name__)


def setup_scheduler(application):
    """Настраивает планировщик отчётов и чистки."""
    tz = pytz.timezone(config.TIMEZONE)

    if config.REPORT_CHAT_IDS:
        hour, minute = map(int, config.ACTIVE_ROUTES_REPORT_TIME.split(":"))
        application.job_queue.run_daily(
            send_active_routes_report,
            time=time(hour=hour, minute=minute, tzinfo=tz),
            name="active_routes_report",
        )
        logger.info(
            f"Отчёт активных маршрутов в {config.ACTIVE_ROUTES_REPORT_TIME} "
            f"для {len(config.REPORT_CHAT_IDS)} чатов"
        )
    else:
        logger.warning("REPORT_CHAT_IDS не задан, автоотчёты отключены")

    cl_hour, cl_minute = map(int, config.CLEANUP_TIME.split(":"))
    application.job_queue.run_daily(
        cleanup_old_rows_job,
        time=time(hour=cl_hour, minute=cl_minute, tzinfo=tz),
        name="cleanup_old_rows",
    )
    logger.info(f"Чистка старых строк в {config.CLEANUP_TIME} ({config.TIMEZONE})")


async def send_active_routes_report(context: ContextTypes.DEFAULT_TYPE):
    """Отчёт об активных маршрутах в каждый настроенный чат."""
    for chat_id in config.REPORT_CHAT_IDS:
        try:
            chat = await context.bot.get_chat(chat_id)
            city = core.sanitize_sheet_name(chat.title or "", str(chat_id))
            routes = sheets_manager.get_active_routes(city)
            if not routes:
                logger.info(f"19:00 — '{city}': активных маршрутов нет")
                continue
            await context.bot.send_message(
                chat_id=chat_id, text=format_active_routes(routes)
            )
            logger.info(f"Отчёт активных маршрутов '{city}': {len(routes)}")
        except Exception as e:
            logger.error(f"Ошибка отчёта для чата {chat_id}: {e}")


def format_active_routes(routes: list) -> str:
    """Форматирует список активных маршрутов."""
    text = "🚗 Активні маршрути:\n\n"
    for r in routes:
        route_num = r.get('route') or '?'
        driver = r.get('driver') or ''
        status = (r.get('status') or '').replace('_', ' ')
        time_str = r.get('time') or ''

        text += f"• Маршрут {route_num}"
        if driver:
            text += f" ({driver})"
        if status and time_str:
            text += f" — {status} в {time_str}"
        text += "\n"

    return text


async def cleanup_old_rows_job(context: ContextTypes.DEFAULT_TYPE):
    """Ночная чистка строк старше RETENTION_DAYS во всех листах городов."""
    try:
        removed = sheets_manager.cleanup_old_rows(config.RETENTION_DAYS)
        logger.info(f"Чистка завершена: удалено строк — {removed}")
    except Exception as e:
        logger.error(f"Ошибка ночной чистки: {e}")
