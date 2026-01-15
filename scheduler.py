"""
Планировщик автоматических отчётов.
Отправляет ежедневные и еженедельные сводки.
"""

import asyncio
import schedule
import time
from datetime import datetime
from threading import Thread
from telegram import Bot

from config import config
from sheets import sheets_manager


class ReportScheduler:
    """Планировщик автоматических отчётов."""

    def __init__(self, bot: Bot):
        self.bot = bot
        self.running = False

    def start(self):
        """Запускает планировщик в отдельном потоке."""
        if not config.REPORT_CHAT_ID:
            print("REPORT_CHAT_ID не задан, автоотчёты отключены")
            return

        # Отчёт об активных маршрутах в 19:00
        schedule.every().day.at(config.ACTIVE_ROUTES_REPORT_TIME).do(
            self._run_async, self.send_active_routes_report
        )

        print(f"Планировщик запущен:")
        print(f"  - Активные маршруты в {config.ACTIVE_ROUTES_REPORT_TIME}")

        # Запуск в отдельном потоке
        self.running = True
        thread = Thread(target=self._run_scheduler, daemon=True)
        thread.start()

    def stop(self):
        """Останавливает планировщик."""
        self.running = False

    def _run_scheduler(self):
        """Цикл планировщика."""
        while self.running:
            schedule.run_pending()
            time.sleep(60)

    def _run_async(self, coro_func):
        """Запускает асинхронную функцию из синхронного контекста."""
        asyncio.run(coro_func())

    async def send_active_routes_report(self):
        """Отправляет отчёт об активных маршрутах в 19:00."""
        try:
            routes = sheets_manager.get_active_routes()

            # Если все маршруты закрыты — не отправляем (уведомление уже было)
            if not routes:
                print(f"19:00 — активных маршрутов нет, пропускаем отчёт")
                return

            text = self._format_active_routes(routes)
            await self.bot.send_message(
                chat_id=config.REPORT_CHAT_ID,
                text=text
            )
            print(f"Отправлен отчёт об активных маршрутах в {datetime.now()}")
        except Exception as e:
            print(f"Ошибка отправки отчёта об активных маршрутах: {e}")

    def _format_active_routes(self, routes: list) -> str:
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
