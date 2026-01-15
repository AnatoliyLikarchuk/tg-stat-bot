"""
Планировщик автоматических отчётов.
Использует JobQueue из python-telegram-bot с поддержкой timezone.
"""

from datetime import time
import pytz
from telegram.ext import ContextTypes

from config import config
from sheets import sheets_manager


def setup_scheduler(application):
    """Настраивает планировщик отчётов."""
    if not config.REPORT_CHAT_ID:
        print("REPORT_CHAT_ID не задан, автоотчёты отключены")
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

    print(f"Планировщик запущен:")
    print(f"  - Активные маршруты в {config.ACTIVE_ROUTES_REPORT_TIME} ({config.TIMEZONE})")


async def send_active_routes_report(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет отчёт об активных маршрутах в 19:00."""
    try:
        routes = sheets_manager.get_active_routes()

        # Если все маршруты закрыты — не отправляем (уведомление уже было)
        if not routes:
            print(f"19:00 — активных маршрутов нет, пропускаем отчёт")
            return

        text = format_active_routes(routes)
        await context.bot.send_message(
            chat_id=config.REPORT_CHAT_ID,
            text=text
        )
        print(f"Отправлен отчёт об активных маршрутах")
    except Exception as e:
        print(f"Ошибка отправки отчёта об активных маршрутах: {e}")


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
        print("ВНИМАНИЕ: ReportScheduler устарел. Используй setup_scheduler(application)")
        self.bot = bot

    def start(self):
        pass

    def stop(self):
        pass
