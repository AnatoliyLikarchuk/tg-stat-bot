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

logger = logging.getLogger(__name__)


def setup_scheduler(application):
    """Настраивает планировщик отчётов."""
    if not config.REPORT_CHAT_ID:
        logger.warning("REPORT_CHAT_ID не задан, автоотчёты отключены")
        return

    # Парсим время из конфига (формат "HH:MM")
    hour, minute = map(int, config.ACTIVE_ROUTES_REPORT_TIME.split(":"))

    # Создаём time с timezone
    tz = pytz.timezone(config.TIMEZONE)
    report_time = time(hour=hour, minute=minute, tzinfo=tz)

    # Планируем ежедневный отчёт
    application.job_queue.run_daily(
        send_active_routes_report,
        time=report_time,
        name="active_routes_report"
    )

    logger.info(f"Планировщик запущен: активные маршруты в {config.ACTIVE_ROUTES_REPORT_TIME} ({config.TIMEZONE})")


async def send_active_routes_report(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет отчёт об активных маршрутах в 19:00."""
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            routes = sheets_manager.get_active_routes()

            # Если все маршруты закрыты — не отправляем (уведомление уже было)
            if not routes:
                logger.info("19:00 — активных маршрутов нет, пропускаем отчёт")
                return

            text = format_active_routes(routes)
            await context.bot.send_message(
                chat_id=config.REPORT_CHAT_ID,
                text=text
            )
            logger.info(f"Отправлен отчёт об активных маршрутах ({len(routes)} маршрутов)")
            return
        except Exception as e:
            logger.error(f"Ошибка отчёта об активных маршрутах (попытка {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                import asyncio
                await asyncio.sleep(5)


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


# Для обратной совместимости (на случай если где-то используется класс)
class ReportScheduler:
    """Устаревший класс. Используй setup_scheduler(application)."""

    def __init__(self, bot):
        logger.warning("ReportScheduler устарел. Используй setup_scheduler(application)")
        self.bot = bot

    def start(self):
        pass

    def stop(self):
        pass
