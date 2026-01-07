"""
Интеграция с Google Sheets.
Сохраняет события логистики в таблицу.
"""

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from parser import ParsedEvent
from config import config


class SheetsManager:
    """Менеджер для работы с Google Sheets."""

    # Заголовки таблицы (колонка A - автонумерация формулой)
    HEADERS = ["№", "Дата", "Время", "Событие", "Маршрут", "Водитель", "Исходное сообщение", "Группа"]

    def __init__(self):
        self.client = None
        self.spreadsheet = None
        self.worksheet = None

    def connect(self) -> bool:
        """Подключается к Google Sheets."""
        try:
            # Авторизация через service account
            scope = [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"
            ]

            creds_path = Path(__file__).parent / config.GOOGLE_SHEETS_CREDENTIALS_FILE
            if not creds_path.exists():
                print(f"Ошибка: Файл credentials не найден: {creds_path}")
                return False

            credentials = ServiceAccountCredentials.from_json_keyfile_name(
                str(creds_path), scope
            )
            self.client = gspread.authorize(credentials)

            # Открываем или создаём таблицу
            try:
                self.spreadsheet = self.client.open(config.GOOGLE_SHEETS_SPREADSHEET_NAME)
            except gspread.SpreadsheetNotFound:
                self.spreadsheet = self.client.create(config.GOOGLE_SHEETS_SPREADSHEET_NAME)
                print(f"Создана новая таблица: {config.GOOGLE_SHEETS_SPREADSHEET_NAME}")

            # Получаем первый лист
            self.worksheet = self.spreadsheet.sheet1

            # Проверяем/добавляем заголовки
            self._ensure_headers()

            print(f"Подключено к таблице: {self.spreadsheet.url}")
            return True

        except Exception as e:
            print(f"Ошибка подключения к Google Sheets: {e}")
            return False

    def _ensure_headers(self):
        """Проверяет наличие заголовков, добавляет если нужно."""
        try:
            first_row = self.worksheet.row_values(1)
            if not first_row or first_row != self.HEADERS:
                self.worksheet.update("A1:H1", [self.HEADERS])
                # Формула автонумерации (русская локаль - точка с запятой)
                self.worksheet.update("A2", [['=ARRAYFORMULA(IF(B2:B="";"";ROW(B2:B)-1))']], value_input_option='USER_ENTERED')
        except Exception:
            self.worksheet.update("A1:H1", [self.HEADERS])
            self.worksheet.update("A2", [['=ARRAYFORMULA(IF(B2:B="";"";ROW(B2:B)-1))']], value_input_option='USER_ENTERED')

    def add_event(self, event: ParsedEvent, group_name: str = "") -> bool:
        """Добавляет событие в таблицу."""
        try:
            row = [
                datetime.now().strftime("%d.%m.%Y"),
                event.time or datetime.now().strftime("%H:%M"),
                event.event_type,
                event.route_number or "",
                event.driver or "",
                event.raw_text[:200],  # Ограничиваем длину
                group_name
            ]
            # Находим следующую пустую строку и пишем в B:H (A - автонумерация)
            next_row = len(self.worksheet.get_all_values()) + 1
            self.worksheet.update(f"B{next_row}:H{next_row}", [row])
            return True
        except Exception as e:
            print(f"Ошибка записи в таблицу: {e}")
            return False

    def add_events(self, events: List[ParsedEvent], group_name: str = "") -> int:
        """Добавляет несколько событий. Возвращает количество успешных записей."""
        count = 0
        for event in events:
            if self.add_event(event, group_name):
                count += 1
        return count

    def get_today_stats(self) -> dict:
        """Получает статистику за сегодня."""
        today = datetime.now().strftime("%d.%m.%Y")
        return self._get_stats_for_date(today)

    def get_stats_for_period(self, days: int = 7) -> dict:
        """Получает статистику за указанное количество дней."""
        try:
            all_records = self.worksheet.get_all_records()
            today = datetime.now()

            stats = {
                "total_events": 0,
                "by_type": {},
                "by_driver": {},
                "by_route": {},
                "problems": []
            }

            for record in all_records:
                try:
                    record_date = datetime.strptime(record.get("Дата", ""), "%d.%m.%Y")
                    diff = (today - record_date).days
                    if diff <= days:
                        self._add_to_stats(stats, record)
                except ValueError:
                    continue

            return stats

        except Exception as e:
            print(f"Ошибка получения статистики: {e}")
            return {}

    def _get_stats_for_date(self, date_str: str) -> dict:
        """Получает статистику за конкретную дату."""
        try:
            all_records = self.worksheet.get_all_records()

            stats = {
                "total_events": 0,
                "by_type": {},
                "by_driver": {},
                "by_route": {},
                "problems": []
            }

            for record in all_records:
                if record.get("Дата") == date_str:
                    self._add_to_stats(stats, record)

            return stats

        except Exception as e:
            print(f"Ошибка получения статистики: {e}")
            return {}

    def _add_to_stats(self, stats: dict, record: dict):
        """Добавляет запись в статистику."""
        stats["total_events"] += 1

        event_type = record.get("Событие", "unknown")
        stats["by_type"][event_type] = stats["by_type"].get(event_type, 0) + 1

        driver = record.get("Водитель", "")
        if driver:
            stats["by_driver"][driver] = stats["by_driver"].get(driver, 0) + 1

        route = record.get("Маршрут", "")
        if route:
            stats["by_route"][route] = stats["by_route"].get(route, 0) + 1

        if event_type == "проблема":
            stats["problems"].append(record.get("Исходное сообщение", ""))

    def get_active_routes(self) -> List[dict]:
        """Получает активные маршруты (начаты, но не завершены)."""
        try:
            today = datetime.now().strftime("%d.%m.%Y")
            all_records = self.worksheet.get_all_records()

            routes_status = {}  # {route_number: last_status}

            for record in all_records:
                if record.get("Дата") == today:
                    route = record.get("Маршрут", "")
                    if route:
                        routes_status[route] = {
                            "route": route,
                            "driver": record.get("Водитель", ""),
                            "status": record.get("Событие", ""),
                            "time": record.get("Время", "")
                        }

            # Фильтруем только активные (не завершённые)
            active = [
                r for r in routes_status.values()
                if r["status"] not in ["маршрут_завершён", "все_выехали"]
            ]

            return active

        except Exception as e:
            print(f"Ошибка получения активных маршрутов: {e}")
            return []


# Синглтон
sheets_manager = SheetsManager()
