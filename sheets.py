"""
Интеграция с Google Sheets.
Сохраняет события логистики в таблицу.
"""

import logging
import time
import gspread
import unicodedata
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import List, Optional
from parser import ParsedEvent
from config import config

logger = logging.getLogger(__name__)

# Timezone из конфига
TZ = ZoneInfo(config.TIMEZONE)


class SheetsManager:
    """Менеджер для работы с Google Sheets."""

    # Заголовки таблицы (колонка A - автонумерация формулой)
    HEADERS = ["№", "Дата", "Время", "Событие", "Маршрут", "Водитель", "Исходное сообщение", "Группа"]

    def __init__(self):
        self.client = None
        self.spreadsheet = None
        self.worksheet = None
        self._cache = None        # кэш последнего чтения _get_recent_records
        self._cache_ts = 0        # timestamp кэша
        self._CACHE_TTL = 5       # TTL кэша в секундах

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
                logger.error(f"Файл credentials не найден: {creds_path}")
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
                logger.info(f"Создана новая таблица: {config.GOOGLE_SHEETS_SPREADSHEET_NAME}")

            # Получаем первый лист
            self.worksheet = self.spreadsheet.sheet1

            # Проверяем/добавляем заголовки
            self._ensure_headers()

            logger.info(f"Подключено к таблице: {self.spreadsheet.url}")
            return True

        except Exception as e:
            logger.error(f"Ошибка подключения к Google Sheets: {e}")
            return False

    def _ensure_headers(self):
        """Проверяет наличие заголовков, добавляет если нужно."""
        try:
            first_row = self.worksheet.row_values(1)
            # Проверяем первые 8 колонок и очищаем лишние справа
            if not first_row or first_row[:8] != self.HEADERS:
                self.worksheet.update("A1:H1", [self.HEADERS])
                # Формула автонумерации (русская локаль - точка с запятой)
                self.worksheet.update("A2", [['=ARRAYFORMULA(IF(B2:B="";"";ROW(B2:B)-1))']], value_input_option='USER_ENTERED')
            # Очищаем лишние колонки справа (I, J, K) если там что-то есть
            if len(first_row) > 8:
                self.worksheet.batch_clear(["I1:K1"])
        except Exception:
            self.worksheet.update("A1:H1", [self.HEADERS])
            self.worksheet.update("A2", [['=ARRAYFORMULA(IF(B2:B="";"";ROW(B2:B)-1))']], value_input_option='USER_ENTERED')

    def _get_recent_records(self, max_rows: int = 200) -> List[dict]:
        """Читает только первые max_rows строк данных (новые сверху).

        Вместо get_all_records() который тянет ВСЕ строки (2000+),
        читаем только нужный диапазон. Для сегодняшних данных хватает ~200,
        для недельных ~500.

        Результат кэшируется на 5 секунд, чтобы множественные проверки
        (цепочка, несоответствие маршрутов) не дёргали API повторно.
        """
        now = time.time()
        if self._cache is not None and (now - self._cache_ts) < self._CACHE_TTL and max_rows <= 200:
            return self._cache

        # +1 для заголовка
        data = self.worksheet.get(f'A1:H{max_rows + 1}')
        if not data or len(data) < 2:
            return []

        headers = data[0]
        records = []
        for row in data[1:]:
            # Дополняем короткие строки пустыми значениями
            padded = row + [''] * (len(headers) - len(row))
            records.append(dict(zip(headers, padded)))

        if max_rows <= 200:
            self._cache = records
            self._cache_ts = now

        return records

    def invalidate_cache(self):
        """Сбрасывает кэш (вызывать после записи)."""
        self._cache = None

    # HTTP-коды, при которых имеет смысл повторить запрос
    _RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
    _RETRY_DELAYS = (1, 3, 9)  # секунды между попытками (всего 4 попытки)

    @classmethod
    def _is_transient_error(cls, exc: Exception) -> bool:
        """True если ошибку стоит ретраить (временная недоступность API)."""
        if isinstance(exc, gspread.exceptions.APIError):
            status = getattr(exc.response, "status_code", None)
            if status in cls._RETRY_STATUS_CODES:
                return True
        msg = str(exc).lower()
        return any(s in msg for s in ("unavailable", "timeout", "timed out", "connection"))

    def add_event(self, event: ParsedEvent, group_name: str = "") -> bool:
        """Добавляет событие в таблицу (вставляет сверху, сразу после заголовка).

        При временных ошибках Google API (503/429/таймауты) делает retry с backoff.
        """
        row = [
            "",  # A - автонумерация формулой
            datetime.now(TZ).strftime("%d.%m.%Y"),
            event.time or datetime.now(TZ).strftime("%H:%M"),
            event.event_type,
            event.route_number or "",
            event.driver or "",
            event.raw_text[:200],  # Ограничиваем длину
            group_name
        ]

        last_error: Optional[Exception] = None
        for attempt in range(len(self._RETRY_DELAYS) + 1):
            try:
                # Вставляем новую строку сразу после заголовка (позиция 2)
                self.worksheet.insert_row(row, index=2)
                # insert_row сдвигает формулу из A2 в A3. Восстанавливаем в A2
                # и ПОЛНОСТЬЮ очищаем A3 (запись '' оставила бы пустую строку, из-за
                # которой ARRAYFORMULA не может раскрыться — будет #REF!).
                self.worksheet.update("A2", [['=ARRAYFORMULA(IF(B2:B="";"";ROW(B2:B)-1))']], value_input_option='USER_ENTERED')
                self.worksheet.batch_clear(["A3"])
                self.invalidate_cache()
                if attempt > 0:
                    logger.info(f"Запись в таблицу удалась с попытки {attempt + 1}")
                return True
            except Exception as e:
                last_error = e
                if attempt < len(self._RETRY_DELAYS) and self._is_transient_error(e):
                    delay = self._RETRY_DELAYS[attempt]
                    logger.warning(f"Временная ошибка Sheets ({e}), retry через {delay}с (попытка {attempt + 2})")
                    time.sleep(delay)
                    continue
                break

        logger.error(f"Ошибка записи в таблицу: {last_error}")
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
        today = datetime.now(TZ).strftime("%d.%m.%Y")
        return self._get_stats_for_date(today)

    def get_stats_for_period(self, days: int = 7) -> dict:
        """Получает статистику за указанное количество дней."""
        try:
            all_records = self._get_recent_records(max_rows=days * 80)
            today = datetime.now(TZ)

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
                    diff = (today.replace(tzinfo=None) - record_date).days
                    if diff <= days:
                        self._add_to_stats(stats, record)
                except ValueError:
                    continue

            return stats

        except Exception as e:
            logger.error(f"Ошибка получения статистики: {e}")
            return {}

    def _get_stats_for_date(self, date_str: str) -> dict:
        """Получает статистику за конкретную дату."""
        try:
            all_records = self._get_recent_records(max_rows=200)

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
            logger.error(f"Ошибка получения статистики: {e}")
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

    # Статусы, означающие завершение маршрута
    CLOSED_STATUSES = {
        unicodedata.normalize('NFC', s) for s in [
            "маршрут_завершён",   # основной статус (с ё)
            "маршрут_завершен",   # вариант без ё (на случай ручного ввода)
            "все_выехали",
        ]
    }

    @staticmethod
    def _normalize_route(route_raw) -> str:
        """Приводит номер маршрута к строке (gspread может вернуть int или float)."""
        if isinstance(route_raw, float):
            return str(int(route_raw))
        if isinstance(route_raw, int):
            return str(route_raw)
        return str(route_raw).strip()

    def get_active_routes(self) -> List[dict]:
        """Получает активные маршруты (начаты, но не завершены).

        Проверяет ВСЕ записи за день для каждого маршрута, а не только
        верхнюю (последнюю вставленную). Это защищает от ситуации, когда
        события записываются не в хронологическом порядке (например, выезд
        отправлен позже завершения маршрута).
        """
        try:
            today = datetime.now(TZ).strftime("%d.%m.%Y")
            all_records = self._get_recent_records(max_rows=200)

            routes_info = {}    # {route_number: info для отображения}
            closed_routes = set()  # маршруты с событием завершения

            for record in all_records:
                if record.get("Дата") != today:
                    continue
                route = self._normalize_route(record.get("Маршрут", ""))
                if not route:
                    continue

                status = unicodedata.normalize('NFC', str(record.get("Событие", "")))

                # Если маршрут завершён — запоминаем
                if status in self.CLOSED_STATUSES:
                    closed_routes.add(route)

                # Берём первую (верхнюю) запись для отображения
                if route not in routes_info:
                    routes_info[route] = {
                        "route": route,
                        "driver": record.get("Водитель", ""),
                        "status": record.get("Событие", ""),
                        "time": record.get("Время", "")
                    }

            # Фильтруем: активные = есть записи, но НЕТ завершения
            active = [
                r for route, r in routes_info.items()
                if route not in closed_routes
            ]

            return active

        except Exception as e:
            logger.error(f"Ошибка получения активных маршрутов: {e}")
            return []

    def get_driver_departure_route(self, driver: str, date: str) -> Optional[str]:
        """Ищет номер маршрута, с которым водитель выехал сегодня."""
        try:
            all_records = self._get_recent_records(max_rows=200)
            driver_lower = driver.lower()

            for record in all_records:
                if record.get("Дата") != date:
                    continue
                if record.get("Событие") != "выезд":
                    continue
                record_driver = str(record.get("Водитель", "")).lower()
                if record_driver == driver_lower:
                    route = self._normalize_route(record.get("Маршрут", ""))
                    if route:
                        return route
            return None

        except Exception as e:
            logger.error(f"Ошибка поиска маршрута выезда: {e}")
            return None

    # Ожидаемая цепочка событий маршрута
    EVENT_CHAIN = ["начало_сборки", "сборка_завершена", "выезд", "маршрут_завершён"]

    def get_route_events_today(self, route_number: str, date: str) -> List[str]:
        """Возвращает список типов событий для маршрута за сегодня."""
        try:
            all_records = self._get_recent_records(max_rows=200)
            events = []
            for record in all_records:
                if record.get("Дата") != date:
                    continue
                route = self._normalize_route(record.get("Маршрут", ""))
                if route == route_number:
                    events.append(record.get("Событие", ""))
            return events
        except Exception as e:
            logger.error(f"Ошибка получения событий маршрута: {e}")
            return []

    def check_chain_violation(self, event_type: str, route_number: str, date: str) -> Optional[str]:
        """Проверяет нарушение цепочки событий маршрута.

        Возвращает описание пропущенного шага или None если всё ок.
        """
        if event_type not in self.EVENT_CHAIN or not route_number:
            return None

        current_idx = self.EVENT_CHAIN.index(event_type)
        if current_idx == 0:
            return None  # начало_сборки — первый шаг, проверять нечего

        existing_events = self.get_route_events_today(route_number, date)

        # Проверяем все предыдущие шаги цепочки
        missing = []
        for i in range(current_idx):
            expected = self.EVENT_CHAIN[i]
            if expected not in existing_events:
                missing.append(expected)

        if missing:
            missing_names = {
                "начало_сборки": "початок збірки",
                "сборка_завершена": "збірка завершена",
                "выезд": "виїзд",
                "маршрут_завершён": "завершення",
            }
            missing_str = ", ".join(missing_names.get(m, m) for m in missing)
            return missing_str

        return None


# Синглтон
sheets_manager = SheetsManager()
