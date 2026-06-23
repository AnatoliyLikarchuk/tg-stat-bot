"""
Интеграция с Google Sheets.
Сохраняет события логистики в таблицу.
"""

import logging
import time
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from threading import Lock
from typing import List, Optional
from parser import ParsedEvent
from config import config
import core

logger = logging.getLogger(__name__)

# Timezone из конфига
TZ = ZoneInfo(config.TIMEZONE)


class SheetsManager:
    """Менеджер для работы с Google Sheets."""

    # Заголовки таблицы (колонка A - автонумерация формулой)
    HEADERS = ["№", "Дата", "Время", "Событие", "Маршрут", "Водитель", "Исходное сообщение", "Группа"]

    # Лист пробега водителей. См. формат в /Users/anatoliy/.claude/plans/1-jiggly-treehouse.md
    MILEAGE_SHEET_NAME = "Пробіг"
    MILEAGE_DRIVER_ROWS_RANGE = "B4:B300"  # имена водителей (блоки по городам)
    _MILEAGE_DRIVERS_TTL = 3600  # TTL кэша списка водителей

    MONTH_NAMES_UA = {
        1: "Січень", 2: "Лютий", 3: "Березень", 4: "Квітень", 5: "Травень", 6: "Червень",
        7: "Липень", 8: "Серпень", 9: "Вересень", 10: "Жовтень", 11: "Листопад", 12: "Грудень",
    }

    def __init__(self):
        self.client = None
        self.spreadsheet = None
        self.mileage_sheet = None
        self.mileage_sheet_id = None
        self._city_sheets = {}    # {city_name: worksheet}
        self._cache = {}          # {city_name: (records, timestamp)}
        self._CACHE_TTL = 5       # TTL кэша в секундах
        self._mileage_lock = Lock()
        self._driver_rows_cache = None  # {driver_name: row_index_1_based}
        self._driver_rows_ts = 0

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

            # Подключаем лист учёта километража (опционально — если нет, фича отключена)
            try:
                self.mileage_sheet = self.spreadsheet.worksheet(self.MILEAGE_SHEET_NAME)
                self.mileage_sheet_id = self.mileage_sheet.id
                logger.info(f"Подключён лист учёта километража: {self.MILEAGE_SHEET_NAME}")
            except gspread.WorksheetNotFound:
                self.mileage_sheet = None
                self.mileage_sheet_id = None
                logger.warning(f"Лист '{self.MILEAGE_SHEET_NAME}' не найден — учёт километража отключён")

            logger.info(f"Подключено к таблице: {self.spreadsheet.url}")
            return True

        except Exception as e:
            logger.error(f"Ошибка подключения к Google Sheets: {e}")
            return False

    def _get_city_sheet(self, city: str):
        """Возвращает worksheet города, создаёт лист при отсутствии.

        Новый лист получает заголовки и ARRAYFORMULA автонумерации.
        """
        ws = self._city_sheets.get(city)
        if ws is not None:
            return ws
        try:
            ws = self.spreadsheet.worksheet(city)
        except gspread.WorksheetNotFound:
            ws = self.spreadsheet.add_worksheet(title=city, rows=1000, cols=8)
            ws.update("A1:H1", [self.HEADERS])
            ws.update(
                "A2",
                [['=ARRAYFORMULA(IF(B2:B="";"";ROW(B2:B)-1))']],
                value_input_option="USER_ENTERED",
            )
            logger.info(f"Создан лист города: {city}")
        self._city_sheets[city] = ws
        return ws

    def _get_recent_records(self, city: str, max_rows: int = 200) -> List[dict]:
        """Читает первые max_rows строк листа города (новые сверху).

        Результат кэшируется на 5 секунд per-city, чтобы множественные
        проверки (цепочка, несоответствие маршрутов) не дёргали API.
        """
        now = time.time()
        cached = self._cache.get(city)
        if cached is not None and (now - cached[1]) < self._CACHE_TTL and max_rows <= 200:
            return cached[0]

        ws = self._get_city_sheet(city)
        data = ws.get(f"A1:H{max_rows + 1}")
        if not data or len(data) < 2:
            return []

        headers = data[0]
        records = []
        for row in data[1:]:
            padded = row + [""] * (len(headers) - len(row))
            records.append(dict(zip(headers, padded)))

        if max_rows <= 200:
            self._cache[city] = (records, now)
        return records

    def invalidate_cache(self, city: str = None):
        """Сбрасывает кэш чтения. Без аргумента — для всех городов."""
        if city is None:
            self._cache.clear()
        else:
            self._cache.pop(city, None)

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

    def _with_retry(self, op_name: str, fn):
        """Выполняет fn() с retry+backoff при временных ошибках Google API.

        При нерет­райной ошибке (или исчерпании попыток) — ре-рейзит исключение.
        Вызывающий код решает, что делать (вернуть False, залогировать и т.п.).
        """
        for attempt in range(len(self._RETRY_DELAYS) + 1):
            try:
                result = fn()
                if attempt > 0:
                    logger.info(f"{op_name}: успех с попытки {attempt + 1}")
                return result
            except Exception as e:
                if attempt < len(self._RETRY_DELAYS) and self._is_transient_error(e):
                    delay = self._RETRY_DELAYS[attempt]
                    logger.warning(
                        f"{op_name}: временная ошибка ({e}), retry через {delay}с"
                        f" (попытка {attempt + 2}/{len(self._RETRY_DELAYS) + 1})"
                    )
                    time.sleep(delay)
                    continue
                raise

    def add_event(self, event: ParsedEvent, city: str, group_name: str = "") -> bool:
        """Добавляет событие в лист города (строка 2, сразу после заголовка).

        При временных ошибках Google API делает retry с backoff.
        """
        row = [
            "",  # A — автонумерация формулой
            datetime.now(TZ).strftime("%d.%m.%Y"),
            event.time or datetime.now(TZ).strftime("%H:%M"),
            event.event_type,
            event.route_number or "",
            event.driver or "",
            event.raw_text[:200],
            group_name,
        ]

        def _do():
            ws = self._get_city_sheet(city)
            ws.insert_row(row, index=2)
            ws.update(
                "A2",
                [['=ARRAYFORMULA(IF(B2:B="";"";ROW(B2:B)-1))']],
                value_input_option="USER_ENTERED",
            )
            ws.batch_clear(["A3"])
            self.invalidate_cache(city)
            return True

        try:
            return self._with_retry("Запись события", _do)
        except Exception as e:
            logger.error(f"Ошибка записи в лист '{city}': {e}")
            return False

    def get_today_stats(self, city: str) -> dict:
        """Статистика города за сегодня."""
        try:
            today = datetime.now(TZ).strftime("%d.%m.%Y")
            records = self._get_recent_records(city, max_rows=200)
            return core.compute_stats(records, lambda r: r.get("Дата") == today)
        except Exception as e:
            logger.error(f"Ошибка статистики за сегодня ('{city}'): {e}")
            return {}

    def get_stats_for_period(self, city: str, days: int = 7) -> dict:
        """Статистика города за последние `days` дней."""
        try:
            records = self._get_recent_records(city, max_rows=days * 80)
            today = datetime.now(TZ).replace(tzinfo=None)

            def in_period(r):
                try:
                    rec_date = datetime.strptime(r.get("Дата", ""), "%d.%m.%Y")
                except (ValueError, TypeError):
                    return False
                return (today - rec_date).days <= days

            return core.compute_stats(records, in_period)
        except Exception as e:
            logger.error(f"Ошибка статистики за период ('{city}'): {e}")
            return {}

    def get_active_routes(self, city: str) -> List[dict]:
        """Активные маршруты города (начаты, но не завершены) за сегодня."""
        try:
            today = datetime.now(TZ).strftime("%d.%m.%Y")
            records = self._get_recent_records(city, max_rows=200)
            return core.compute_active_routes(records, today)
        except Exception as e:
            logger.error(f"Ошибка активных маршрутов ('{city}'): {e}")
            return []

    def get_driver_departure_route(self, city: str, driver: str,
                                   date: str) -> Optional[str]:
        """Номер маршрута, с которым водитель выехал сегодня в этом городе."""
        try:
            records = self._get_recent_records(city, max_rows=200)
            driver_lower = driver.lower()
            for r in records:
                if r.get("Дата") != date or r.get("Событие") != "выезд":
                    continue
                if str(r.get("Водитель", "")).lower() == driver_lower:
                    route = core.normalize_route(r.get("Маршрут", ""))
                    if route:
                        return route
            return None
        except Exception as e:
            logger.error(f"Ошибка поиска маршрута выезда ('{city}'): {e}")
            return None

    def get_route_events_today(self, city: str, route_number: str,
                               date: str) -> List[str]:
        """Типы событий маршрута за день в этом городе."""
        try:
            records = self._get_recent_records(city, max_rows=200)
            events = []
            for r in records:
                if r.get("Дата") != date:
                    continue
                if core.normalize_route(r.get("Маршрут", "")) == route_number:
                    events.append(r.get("Событие", ""))
            return events
        except Exception as e:
            logger.error(f"Ошибка событий маршрута ('{city}'): {e}")
            return []

    def check_chain_violation(self, city: str, event_type: str,
                              route_number: str, date: str) -> Optional[str]:
        """Описание пропущенного шага цепочки или None."""
        if not route_number:
            return None
        existing = self.get_route_events_today(city, route_number, date)
        return core.compute_chain_violation(event_type, existing)

    def list_city_sheets(self) -> List[str]:
        """Имена листов-городов (без служебных)."""
        try:
            return [
                ws.title for ws in self.spreadsheet.worksheets()
                if ws.title not in core.RESERVED_SHEET_NAMES
            ]
        except Exception as e:
            logger.error(f"Ошибка получения списка городов: {e}")
            return []

    def cleanup_old_rows(self, retention_days: int) -> int:
        """Удаляет строки старше retention_days из всех листов городов.

        Новые строки сверху → старые внизу. Для каждого листа считаем
        через core.count_stale_rows, сколько нижних строк удалить.
        Возвращает общее число удалённых строк.
        """
        cutoff = (datetime.now(TZ) - timedelta(days=retention_days)).date()
        total_removed = 0
        for city in self.list_city_sheets():
            try:
                ws = self._get_city_sheet(city)
                date_col = ws.col_values(2)[1:]  # колонка B без заголовка
                stale = core.count_stale_rows(date_col, cutoff)
                if stale <= 0:
                    continue
                last_row = len(date_col) + 1  # +1 за заголовок
                first_stale = last_row - stale + 1
                ws.delete_rows(first_stale, last_row)
                if first_stale == 2:
                    # Удалены все строки данных — вместе с ними ушла
                    # ARRAYFORMULA автонумерации в A2. Восстанавливаем.
                    ws.update(
                        "A2",
                        [['=ARRAYFORMULA(IF(B2:B="";"";ROW(B2:B)-1))']],
                        value_input_option="USER_ENTERED",
                    )
                self.invalidate_cache(city)
                total_removed += stale
                logger.info(f"Чистка '{city}': удалено {stale} строк")
            except Exception as e:
                logger.error(f"Ошибка чистки листа '{city}': {e}")
        return total_removed

    def backfill_mileage_formulas(self) -> int:
        """Проставляет формулы «Пробіг км»/«Витрата палива» всем водителям
        во всех существующих блоках месяцев листа «Пробіг».

        Идемпотентна: ячейки, где формула уже есть, не трогает.
        Возвращает количество заполненных строк-формул.
        """
        if self.mileage_sheet is None:
            logger.warning("Лист пробега не подключён — backfill пропущен")
            return 0

        with self._mileage_lock:
            try:
                grid = self.mileage_sheet.get_values(
                    "A1:ZZ300", value_render_option="FORMULA"
                )
                if len(grid) < 4:
                    return 0

                row1 = (grid[0] if len(grid) > 0 else []) + [""] * 300
                row3 = (grid[2] if len(grid) > 2 else []) + [""] * 300
                blocks = core.find_mileage_blocks(row1, row3)
                if not blocks:
                    logger.info("backfill: блоков месяцев нет")
                    return 0

                driver_rows = sorted(self._get_driver_rows().values())
                sheet_id = self.mileage_sheet_id
                requests = []
                filled_count = 0
                for row_1based in driver_rows:
                    row_idx0 = row_1based - 1
                    existing = grid[row_idx0] if row_idx0 < len(grid) else []
                    for mcol, month_label in blocks:
                        mileage_formula = (
                            f"=SUMIFS(${row_1based}:${row_1based};"
                            f'$1:$1;"{month_label}")'
                        )
                        fuel_formula = (
                            "=" + self._col_letter(mcol + 1) + str(row_1based)
                            + "/100*C" + str(row_1based)
                        )

                        current_mileage = existing[mcol] if mcol < len(existing) else ""
                        current_fuel = existing[mcol + 1] if mcol + 1 < len(existing) else ""
                        missing = [
                            (mcol, mileage_formula, current_mileage),
                            (mcol + 1, fuel_formula, current_fuel),
                        ]
                        start = None
                        values = []
                        for col_idx, formula, current in missing:
                            if str(current).strip():
                                if values:
                                    requests.append({"updateCells": {
                                        "range": {"sheetId": sheet_id,
                                                  "startRowIndex": row_idx0,
                                                  "endRowIndex": row_1based,
                                                  "startColumnIndex": start,
                                                  "endColumnIndex": col_idx},
                                        "rows": [{"values": values}],
                                        "fields": "userEnteredValue",
                                    }})
                                    filled_count += len(values)
                                    start = None
                                    values = []
                                continue

                            if start is None:
                                start = col_idx
                            values.append({"userEnteredValue": {"formulaValue": formula}})

                        if values:
                            requests.append({"updateCells": {
                                "range": {"sheetId": sheet_id,
                                          "startRowIndex": row_idx0,
                                          "endRowIndex": row_1based,
                                          "startColumnIndex": start,
                                          "endColumnIndex": start + len(values)},
                                "rows": [{"values": values}],
                                "fields": "userEnteredValue",
                            }})
                            filled_count += len(values)

                if not requests:
                    logger.info("backfill: все формулы уже на месте")
                    return 0

                self.spreadsheet.batch_update({"requests": requests})
                logger.info(f"backfill: заполнено {filled_count} формул")
                return filled_count
            except Exception as e:
                logger.error(f"Ошибка backfill формул пробега: {e}", exc_info=True)
                return 0

    # ==================== Учёт километража (лист "Пробіг") ====================

    # Цвета (0..1) для форматирования блоков месяца
    _COLOR_MONTH_HEADER = {"red": 0.812, "green": 0.886, "blue": 0.953}  # голубой
    _COLOR_DAY_HEADER = {"red": 0.937, "green": 0.937, "blue": 0.937}    # серый
    _COLOR_MILEAGE = {"red": 0.851, "green": 0.918, "blue": 0.823}       # зелёный
    _COLOR_FUEL = {"red": 1.0, "green": 0.945, "blue": 0.8}              # жёлтый
    _COLOR_WHITE = {"red": 1, "green": 1, "blue": 1}
    _COLOR_BORDER = {"red": 0.4, "green": 0.4, "blue": 0.4}

    @staticmethod
    def _col_letter(n_1_based: int) -> str:
        """1 → 'A', 26 → 'Z', 27 → 'AA'."""
        s = ''
        while n_1_based > 0:
            n_1_based, r = divmod(n_1_based - 1, 26)
            s = chr(65 + r) + s
        return s

    def _get_driver_rows(self) -> dict:
        """Возвращает {имя_водителя: row_index_1_based}. Кэш на час."""
        now = time.time()
        if (self._driver_rows_cache is not None
                and (now - self._driver_rows_ts) < self._MILEAGE_DRIVERS_TTL):
            return self._driver_rows_cache

        if self.mileage_sheet is None:
            return {}

        cells = self.mileage_sheet.get_values(self.MILEAGE_DRIVER_ROWS_RANGE)
        result = {}
        for offset, row in enumerate(cells):
            name = (row[0] if row else "").strip()
            if name:
                result[name] = 4 + offset  # B4 = строка 4
        self._driver_rows_cache = result
        self._driver_rows_ts = now
        return result

    def get_mileage_drivers(self) -> set:
        """Множество имён водителей из листа «Пробіг» (колонка B).

        Источник правды о валидных водителях для учёта пробега.
        Переиспользует кэш _get_driver_rows() (TTL 1 час). Если лист
        недоступен — возвращает пустое множество.
        """
        return set(self._get_driver_rows().keys())

    def upsert_mileage(self, driver: str, km: int, dt: datetime) -> bool:
        """Записывает пробег водителя за день в лист 'Пробіг'.

        Если колонки дня нет — вставляет (свежий день слева в блоке месяца).
        Если блока месяца нет совсем — создаёт новый блок (3 колонки) слева.
        Перезаписывает значение если колонка дня уже существует.

        При временных ошибках Google API делает retry с backoff. Ретрай безопасен:
        при повторе перечитываем верхние 3 строки и решаем заново — если первый
        batch_update успел применится, увидим колонку и просто обновим ячейку.
        """
        if self.mileage_sheet is None:
            logger.warning("Лист пробега не подключён — пропускаем запись")
            return False

        with self._mileage_lock:
            try:
                return self._with_retry(
                    f"Пробег {driver}",
                    lambda: self._upsert_mileage_impl(driver, km, dt),
                )
            except Exception as e:
                logger.error(f"Ошибка записи пробега для {driver}: {e}", exc_info=True)
                return False

    def _upsert_mileage_impl(self, driver: str, km: int, dt: datetime) -> bool:
        """Реализация upsert_mileage без try/except — оборачивается в _with_retry."""
        month_label = f"M{dt:%Y-%m}"
        day_label = dt.strftime("%d.%m.%y")
        month_title = f"{self.MONTH_NAMES_UA[dt.month]} {dt.year}"

        driver_rows = self._get_driver_rows()
        driver_row = driver_rows.get(driver)
        if driver_row is None:
            logger.warning(f"Водитель '{driver}' отсутствует в листе пробега")
            return False

        # Читаем верхние 3 строки одним запросом
        top = self.mileage_sheet.get_values('A1:ZZ3')
        row1 = (top[0] if len(top) > 0 else []) + [''] * 200
        row2 = (top[1] if len(top) > 1 else []) + [''] * 200
        row3 = (top[2] if len(top) > 2 else []) + [''] * 200

        # Колонки дней этого месяца (с меткой M{YYYY-MM})
        day_columns_of_month = [i for i, v in enumerate(row1) if v == month_label]

        if day_columns_of_month:
            # Блок месяца с днями есть — ищем конкретный день
            matching = [i for i in day_columns_of_month if row3[i] == day_label]
            if matching:
                col_1_based = matching[0] + 1
                self.mileage_sheet.update_cell(driver_row, col_1_based, km)
                logger.info(f"[пробег] {driver} {day_label}: {km} км (перезапись)")
                return True

            # Найти границы текущего объединения шапки месяца
            if month_title in row2:
                title_idx = row2.index(month_title)
            else:
                title_idx = day_columns_of_month[0] - 2  # расчётные слева
            last_col = max(day_columns_of_month)
            insert_at = day_columns_of_month[0]  # свежий слева
            self._extend_month_block(
                title_idx, last_col, insert_at,
                month_label, day_label, driver_row, km,
            )
            logger.info(f"[пробег] {driver} {day_label}: {km} км (новый день)")
            return True

        # Колонок-дней нет. Проверяем — есть ли уже расчётные блока (заголовок месяца)
        if month_title in row2:
            title_idx = row2.index(month_title)
            # Расчётные занимают title_idx и title_idx+1, день вставляем после
            last_col = title_idx + 1
            insert_at = title_idx + 2
            self._extend_month_block(
                title_idx, last_col, insert_at,
                month_label, day_label, driver_row, km,
            )
            logger.info(f"[пробег] {driver} {day_label}: {km} км (первый день месяца)")
            return True

        # Совсем новый месяц — создаём блок из 3 колонок слева от существующих
        self._create_month_block(month_label, month_title, day_label, driver_row, km)
        logger.info(f"[пробег] {driver} {day_label}: {km} км (новый месяц)")
        return True

    def _extend_month_block(self, title_idx: int, last_col: int, insert_at: int,
                            month_label: str, day_label: str, driver_row: int, km: int):
        """Вставляет одну колонку дня в существующий блок месяца, расширяя объединение шапки."""
        sheet_id = self.mileage_sheet_id
        new_last_col = last_col + 1  # после вставки одной колонки

        requests = [
            # Размержить текущую шапку
            {"unmergeCells": {"range": {
                "sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2,
                "startColumnIndex": title_idx, "endColumnIndex": last_col + 1,
            }}},
            # Вставить колонку
            {"insertDimension": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": insert_at, "endIndex": insert_at + 1},
                "inheritFromBefore": False,
            }},
            # Записать значения в новую колонку
            {"updateCells": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                          "startColumnIndex": insert_at, "endColumnIndex": insert_at + 1},
                "rows": [{"values": [{"userEnteredValue": {"stringValue": month_label}}]}],
                "fields": "userEnteredValue",
            }},
            {"updateCells": {
                "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": 3,
                          "startColumnIndex": insert_at, "endColumnIndex": insert_at + 1},
                "rows": [{"values": [{"userEnteredValue": {"stringValue": day_label}}]}],
                "fields": "userEnteredValue",
            }},
            {"updateCells": {
                "range": {"sheetId": sheet_id,
                          "startRowIndex": driver_row - 1, "endRowIndex": driver_row,
                          "startColumnIndex": insert_at, "endColumnIndex": insert_at + 1},
                "rows": [{"values": [{"userEnteredValue": {"numberValue": km}}]}],
                "fields": "userEnteredValue",
            }},
            # Замержить расширенную шапку
            {"mergeCells": {"range": {
                "sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2,
                "startColumnIndex": title_idx, "endColumnIndex": new_last_col + 1,
            }, "mergeType": "MERGE_ALL"}},
            # Форматы
            {"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2,
                          "startColumnIndex": title_idx, "endColumnIndex": new_last_col + 1},
                "cell": {"userEnteredFormat": {
                    "backgroundColor": self._COLOR_MONTH_HEADER,
                    "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
                    "textFormat": {"bold": True, "fontSize": 11},
                }},
                "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,textFormat)",
            }},
            {"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": 3,
                          "startColumnIndex": insert_at, "endColumnIndex": insert_at + 1},
                "cell": {"userEnteredFormat": {
                    "backgroundColor": self._COLOR_DAY_HEADER,
                    "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
                    "textFormat": {"bold": True},
                }},
                "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,textFormat)",
            }},
            {"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 3, "endRowIndex": 100,
                          "startColumnIndex": insert_at, "endColumnIndex": insert_at + 1},
                "cell": {"userEnteredFormat": {
                    "backgroundColor": self._COLOR_WHITE,
                    "horizontalAlignment": "CENTER",
                }},
                "fields": "userEnteredFormat(backgroundColor,horizontalAlignment)",
            }},
            # Ширина новой колонки
            {"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": insert_at, "endIndex": insert_at + 1},
                "properties": {"pixelSize": 75}, "fields": "pixelSize",
            }},
        ]
        self.spreadsheet.batch_update({"requests": requests})

    def _create_month_block(self, month_label: str, month_title: str, day_label: str,
                            driver_row: int, km: int):
        """Создаёт новый блок месяца (3 колонки) слева от существующих блоков.

        Структура нового блока: [Пробіг км | Витрата палива | первый день].
        Старые блоки автоматически уезжают вправо.
        """
        sheet_id = self.mileage_sheet_id
        # Вставка перед колонкой D (index=3) — сразу после "Планова витрата на 100км"
        ins = 3

        # Формулы для всех строк, где реально есть водитель (а не 4..14).
        # SUMIFS по полной строке инвариантен к вставке колонок справа.
        driver_rows = sorted(self._get_driver_rows().values())
        formula_requests = []
        for row_1_based in driver_rows:
            mileage_formula = (
                f'=SUMIFS(${row_1_based}:${row_1_based};$1:$1;"{month_label}")'
            )
            fuel_formula = (
                "=" + self._col_letter(ins + 1) + str(row_1_based)
                + "/100*" + self._col_letter(3) + str(row_1_based)
            )
            formula_requests.append({"updateCells": {
                "range": {"sheetId": sheet_id,
                          "startRowIndex": row_1_based - 1, "endRowIndex": row_1_based,
                          "startColumnIndex": ins, "endColumnIndex": ins + 2},
                "rows": [{"values": [
                    {"userEnteredValue": {"formulaValue": mileage_formula}},
                    {"userEnteredValue": {"formulaValue": fuel_formula}},
                ]}],
                "fields": "userEnteredValue",
            }})

        requests = [
            # Вставить 3 новые колонки слева
            {"insertDimension": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": ins, "endIndex": ins + 3},
                "inheritFromBefore": False,
            }},
            # Строка 1 (служебные метки)
            {"updateCells": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                          "startColumnIndex": ins, "endColumnIndex": ins + 3},
                "rows": [{"values": [
                    {"userEnteredValue": {"stringValue": "Розрахунок"}},
                    {"userEnteredValue": {"stringValue": "Розрахунок"}},
                    {"userEnteredValue": {"stringValue": month_label}},
                ]}],
                "fields": "userEnteredValue",
            }},
            # Строка 2 (заголовок месяца)
            {"updateCells": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2,
                          "startColumnIndex": ins, "endColumnIndex": ins + 1},
                "rows": [{"values": [{"userEnteredValue": {"stringValue": month_title}}]}],
                "fields": "userEnteredValue",
            }},
            # Строка 3 (заголовки полей блока)
            {"updateCells": {
                "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": 3,
                          "startColumnIndex": ins, "endColumnIndex": ins + 3},
                "rows": [{"values": [
                    {"userEnteredValue": {"stringValue": "Пробіг км"}},
                    {"userEnteredValue": {"stringValue": "Витрата палива"}},
                    {"userEnteredValue": {"stringValue": day_label}},
                ]}],
                "fields": "userEnteredValue",
            }},
        ]
        # Формулы для всех водителей
        requests.extend(formula_requests)
        # Записать значение пробега для текущего водителя в колонку дня (ins+2)
        requests.append({"updateCells": {
            "range": {"sheetId": sheet_id,
                      "startRowIndex": driver_row - 1, "endRowIndex": driver_row,
                      "startColumnIndex": ins + 2, "endColumnIndex": ins + 3},
            "rows": [{"values": [{"userEnteredValue": {"numberValue": km}}]}],
            "fields": "userEnteredValue",
        }})
        # Объединить заголовок месяца над всем блоком
        requests.append({"mergeCells": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2,
                      "startColumnIndex": ins, "endColumnIndex": ins + 3},
            "mergeType": "MERGE_ALL",
        }})
        # Форматы
        requests.extend([
            # Шапка месяца
            {"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2,
                          "startColumnIndex": ins, "endColumnIndex": ins + 3},
                "cell": {"userEnteredFormat": {
                    "backgroundColor": self._COLOR_MONTH_HEADER,
                    "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
                    "textFormat": {"bold": True, "fontSize": 11},
                }},
                "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,textFormat)",
            }},
            # Шапка полей блока (Пробег / Расход / День)
            {"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": 3,
                          "startColumnIndex": ins, "endColumnIndex": ins + 3},
                "cell": {"userEnteredFormat": {
                    "backgroundColor": self._COLOR_DAY_HEADER,
                    "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
                    "textFormat": {"bold": True},
                }},
                "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,textFormat)",
            }},
            # Колонка "Пробіг км" — зелёный + жирный
            {"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 3, "endRowIndex": 100,
                          "startColumnIndex": ins, "endColumnIndex": ins + 1},
                "cell": {"userEnteredFormat": {
                    "backgroundColor": self._COLOR_MILEAGE,
                    "horizontalAlignment": "CENTER",
                    "textFormat": {"bold": True},
                }},
                "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)",
            }},
            # Колонка "Витрата палива" — жёлтый
            {"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 3, "endRowIndex": 100,
                          "startColumnIndex": ins + 1, "endColumnIndex": ins + 2},
                "cell": {"userEnteredFormat": {
                    "backgroundColor": self._COLOR_FUEL,
                    "horizontalAlignment": "CENTER",
                }},
                "fields": "userEnteredFormat(backgroundColor,horizontalAlignment)",
            }},
            # Колонка дня — белая, центр
            {"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 3, "endRowIndex": 100,
                          "startColumnIndex": ins + 2, "endColumnIndex": ins + 3},
                "cell": {"userEnteredFormat": {
                    "backgroundColor": self._COLOR_WHITE,
                    "horizontalAlignment": "CENTER",
                }},
                "fields": "userEnteredFormat(backgroundColor,horizontalAlignment)",
            }},
            # Толстая левая граница на первой колонке нового блока
            {"updateBorders": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 100,
                          "startColumnIndex": ins, "endColumnIndex": ins + 1},
                "left": {"style": "SOLID_THICK", "color": self._COLOR_BORDER},
            }},
            # Ширины колонок: 90 / 100 / 75
            {"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": ins, "endIndex": ins + 1},
                "properties": {"pixelSize": 90}, "fields": "pixelSize",
            }},
            {"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": ins + 1, "endIndex": ins + 2},
                "properties": {"pixelSize": 100}, "fields": "pixelSize",
            }},
            {"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": ins + 2, "endIndex": ins + 3},
                "properties": {"pixelSize": 75}, "fields": "pixelSize",
            }},
        ])
        self.spreadsheet.batch_update({"requests": requests})

    def get_weekly_mileage(self) -> List[dict]:
        """Возвращает суммы пробега по водителям за последние 7 дней.

        Формат: [{"driver": "Косич", "km": 720}, ...] — по убыванию.
        Только водители у которых пробег > 0 за окно.
        """
        if self.mileage_sheet is None:
            return []

        try:
            today = datetime.now(TZ).date()
            week_start = today - timedelta(days=6)

            data = self.mileage_sheet.get_values("A1:ZZ300")
            if len(data) < 4:
                return []

            row1 = data[0] + [""] * 200
            row3 = data[2] + [""] * 200

            window_cols = []
            for i, label in enumerate(row1):
                if not label or not label.startswith("M"):
                    continue
                date_str = row3[i] if i < len(row3) else ""
                if not date_str:
                    continue
                try:
                    d = datetime.strptime(date_str, "%d.%m.%y").date()
                except ValueError:
                    continue
                if week_start <= d <= today:
                    window_cols.append(i)

            if not window_cols:
                return []

            results = []
            for row_idx in range(3, len(data)):  # строки с 4-й до конца
                row = data[row_idx] + [""] * 200
                name = (row[1] or "").strip()  # колонка B
                if not name:
                    continue  # строка-подзаголовок города или пустая
                total = 0
                for c in window_cols:
                    val = row[c] if c < len(row) else ""
                    try:
                        total += int(float(str(val).replace(",", "."))) if val else 0
                    except (ValueError, TypeError):
                        continue
                if total > 0:
                    results.append({"driver": name, "km": total})

            results.sort(key=lambda x: x["km"], reverse=True)
            return results

        except Exception as e:
            logger.error(f"Ошибка получения недельного пробега: {e}", exc_info=True)
            return []


# Синглтон
sheets_manager = SheetsManager()
