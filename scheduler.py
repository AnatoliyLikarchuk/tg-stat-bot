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

        # Ежедневный отчёт
        schedule.every().day.at(config.DAILY_REPORT_TIME).do(
            self._run_async, self.send_daily_report
        )

        # Еженедельный отчёт (0 = понедельник)
        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        day_name = days[config.WEEKLY_REPORT_DAY]
        getattr(schedule.every(), day_name).at(config.WEEKLY_REPORT_TIME).do(
            self._run_async, self.send_weekly_report
        )

        print(f"Планировщик запущен:")
        print(f"  - Ежедневный отчёт в {config.DAILY_REPORT_TIME}")
        print(f"  - Еженедельный отчёт: {day_name} в {config.WEEKLY_REPORT_TIME}")

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

    async def send_daily_report(self):
        """Отправляет ежедневный отчёт."""
        try:
            stats = sheets_manager.get_today_stats()
            text = self._format_daily_report(stats)
            await self.bot.send_message(
                chat_id=config.REPORT_CHAT_ID,
                text=text,
                parse_mode="Markdown"
            )
            print(f"Отправлен ежедневный отчёт в {datetime.now()}")
        except Exception as e:
            print(f"Ошибка отправки ежедневного отчёта: {e}")

    async def send_weekly_report(self):
        """Отправляет еженедельный отчёт."""
        try:
            stats = sheets_manager.get_stats_for_period(7)
            text = self._format_weekly_report(stats)
            await self.bot.send_message(
                chat_id=config.REPORT_CHAT_ID,
                text=text,
                parse_mode="Markdown"
            )
            print(f"Отправлен еженедельный отчёт в {datetime.now()}")
        except Exception as e:
            print(f"Ошибка отправки еженедельного отчёта: {e}")

    def _format_daily_report(self, stats: dict) -> str:
        """Форматирует ежедневный отчёт."""
        today = datetime.now().strftime("%d.%m.%Y")
        text = f"📊 *Ежедневный отчёт за {today}*\n\n"

        if not stats or stats.get("total_events", 0) == 0:
            text += "Событий не зафиксировано."
            return text

        text += f"Всего событий: {stats['total_events']}\n\n"

        # По типам
        if stats.get("by_type"):
            type_names = {
                "начало_сборки": "🔧 Сборка начата",
                "сборка_завершена": "✅ Собрано",
                "выезд": "🚗 Выехало",
                "маршрут_завершён": "🏁 Завершено",
                "проблема": "⚠️ Проблем"
            }
            for event_type, count in stats["by_type"].items():
                name = type_names.get(event_type, event_type)
                text += f"{name}: {count}\n"

        # Водители
        if stats.get("by_driver"):
            text += "\n*Водители:*\n"
            for driver, count in sorted(stats["by_driver"].items(), key=lambda x: -x[1])[:5]:
                text += f"  • {driver}: {count}\n"

        return text

    def _format_weekly_report(self, stats: dict) -> str:
        """Форматирует еженедельный отчёт."""
        text = "📈 *Еженедельная сводка*\n\n"

        if not stats or stats.get("total_events", 0) == 0:
            text += "За эту неделю событий не было."
            return text

        text += f"Всего событий за 7 дней: {stats['total_events']}\n\n"

        # По типам
        if stats.get("by_type"):
            text += "*События:*\n"
            type_names = {
                "начало_сборки": "🔧 Сборок начато",
                "сборка_завершена": "✅ Сборок завершено",
                "выезд": "🚗 Выездов",
                "маршрут_завершён": "🏁 Маршрутов завершено",
                "проблема": "⚠️ Проблем"
            }
            for event_type, count in stats["by_type"].items():
                name = type_names.get(event_type, event_type)
                text += f"  {name}: {count}\n"

        # Топ водителей
        if stats.get("by_driver"):
            text += "\n*Топ водителей:*\n"
            for driver, count in sorted(stats["by_driver"].items(), key=lambda x: -x[1])[:10]:
                text += f"  • {driver}: {count} событий\n"

        # Проблемы
        if stats.get("problems"):
            text += f"\n⚠️ *Проблемы за неделю: {len(stats['problems'])}*\n"

        return text
