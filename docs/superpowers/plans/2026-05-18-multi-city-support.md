# Поддержка нескольких городов — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Научить бота обслуживать 20+ городов: статистика каждого города — на отдельном листе Google Sheets, с автоочисткой старых строк, выбором города в личке кнопками и учётом пробега блоками по городам.

**Architecture:** Каждый город получает собственный лист (вкладку) внутри той же таблицы — имя листа = название Telegram-чата. Бот создаёт лист автоматически при первом сообщении из нового чата. Вся «фильтрация по городу» сводится к «работе с нужным листом», поэтому оптимизация чтения «верхние N строк» продолжает работать. Чистая логика аналитики выносится в новый модуль `core.py` без обращений к Google API — это делает её юнит-тестируемой. Старые строки (>90 дней) удаляет ночной job. Лист «Пробіг» остаётся общим, первой вкладкой, водители сгруппированы блоками по городам.

**Tech Stack:** Python 3.11, python-telegram-bot 21.0 (JobQueue, CallbackQueryHandler), gspread 6.0.0, pytest 9.0.2.

---

## File Structure

| Файл | Ответственность | Действие |
|------|------------------|----------|
| `core.py` | Чистые функции: вычисление активных маршрутов, статистики, нарушений цепочки, очистки, пагинации, имени листа. Без I/O. | **Создать** |
| `sheets.py` | Работа с Google Sheets: листы городов (get-or-create), запись событий, чтение, чистка, лист «Пробіг». Использует `core.py`. | Изменить |
| `bot.py` | Определение города из чата, проброс города, inline-кнопки выбора города, уведомления. | Изменить |
| `scheduler.py` | Отчёт 19:00 по каждому городу, ночная чистка. | Изменить |
| `config.py` | `REPORT_CHAT_IDS` (список чатов), параметры чистки. | Изменить |
| `tests/` | Юнит-тесты `core.py`. | **Создать** |
| `requirements-dev.txt` | pytest для локального прогона (не ставится на VPS). | **Создать** |

**Принцип тестирования:** Google API не мокаем. Вся проверяемая логика (фильтрация, аналитика, очистка, пагинация) живёт в `core.py` как чистые функции `list[dict] -> результат`. Тесты покрывают `core.py`. Код в `sheets.py` остаётся тонкой обёрткой «прочитал лист → отдал в `core` → записал».

---

## Phase A — Чистое ядро `core.py` + тесты

### Task 1: Тест-инфраструктура

**Files:**
- Create: `requirements-dev.txt`
- Create: `tests/__init__.py` (пустой)
- Create: `tests/test_core.py`
- Create: `core.py`

- [ ] **Step 1: Создать `requirements-dev.txt`**

```
# Зависимости только для локальной разработки и тестов.
# НЕ ставится на VPS (CI ставит requirements.txt).
pytest==9.0.2
```

- [ ] **Step 2: Создать `core.py` с заглушкой модуля**

```python
"""Чистые функции вычислений логистики — без обращений к Google API.

Выделены из sheets.py, чтобы покрыть фильтрацию и аналитику
юнит-тестами без моков gspread.
"""
```

- [ ] **Step 3: Создать `tests/__init__.py`** — пустой файл.

- [ ] **Step 4: Создать `tests/test_core.py` с проверкой импорта**

```python
import core


def test_module_imports():
    assert core is not None
```

- [ ] **Step 5: Запустить тест — убедиться, что проходит**

Run: `python3 -m pytest tests/test_core.py -v`
Expected: PASS (1 passed)

- [ ] **Step 6: Commit**

```bash
git add requirements-dev.txt tests/__init__.py tests/test_core.py core.py
git commit -m "🧪 Тест-инфраструктура: pytest + модуль core.py"
```

---

### Task 2: `sanitize_sheet_name` — имя листа из названия чата

**Files:**
- Modify: `core.py`
- Test: `tests/test_core.py`

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_core.py`:

```python
from core import sanitize_sheet_name


def test_sanitize_keeps_normal_name():
    assert sanitize_sheet_name("Логістика Суми", "fallback") == "Логістика Суми"


def test_sanitize_strips_forbidden_chars():
    # []:*?/\ запрещены в именах листов Google Sheets
    assert sanitize_sheet_name("Суми [2]/гілка", "fb") == "Суми  2  гілка"


def test_sanitize_trims_to_100_chars():
    assert len(sanitize_sheet_name("я" * 200, "fb")) == 100


def test_sanitize_empty_returns_fallback():
    assert sanitize_sheet_name("", "fallback") == "fallback"
    assert sanitize_sheet_name("   ", "fallback") == "fallback"


def test_sanitize_reserved_name_returns_fallback():
    # "Пробіг" — служебный лист, занимать нельзя
    assert sanitize_sheet_name("Пробіг", "fallback") == "fallback"


def test_sanitize_strips_apostrophes():
    assert sanitize_sheet_name("'Суми'", "fb") == "Суми"
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `python3 -m pytest tests/test_core.py -v`
Expected: FAIL — `ImportError: cannot import name 'sanitize_sheet_name'`

- [ ] **Step 3: Реализовать в `core.py`**

```python
# Запрещённые символы в имени листа Google Sheets
_FORBIDDEN_SHEET_CHARS = set("[]:*?/\\")

# Служебные имена листов, которые нельзя занимать под город
RESERVED_SHEET_NAMES = {"Пробіг"}


def sanitize_sheet_name(title: str, fallback: str) -> str:
    """Название Telegram-чата → корректное имя листа Google Sheets.

    Правила Google Sheets: 1..100 символов, без []:*?/\\,
    не начинается/заканчивается апострофом. Пустое или служебное
    имя → fallback (обычно строковый chat_id).
    """
    name = (title or "").strip()
    name = "".join(" " if ch in _FORBIDDEN_SHEET_CHARS else ch for ch in name)
    name = name.strip("'").strip()
    name = name[:100].strip()
    if not name or name in RESERVED_SHEET_NAMES:
        return fallback
    return name
```

- [ ] **Step 4: Запустить — убедиться, что проходят**

Run: `python3 -m pytest tests/test_core.py -v`
Expected: PASS (все тесты)

- [ ] **Step 5: Commit**

```bash
git add core.py tests/test_core.py
git commit -m "✨ core.sanitize_sheet_name — имя листа из названия чата"
```

---

### Task 3: `compute_active_routes` — активные маршруты

**Files:**
- Modify: `core.py`
- Test: `tests/test_core.py`

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_core.py`:

```python
from core import compute_active_routes


def _rec(date, event, route, driver="", time="08:00"):
    return {"Дата": date, "Событие": event, "Маршрут": route,
            "Водитель": driver, "Время": time}


def test_active_route_in_progress():
    records = [_rec("18.05.2026", "выезд", "1", "Косич")]
    active = compute_active_routes(records, "18.05.2026")
    assert len(active) == 1
    assert active[0]["route"] == "1"
    assert active[0]["status"] == "выезд"


def test_closed_route_excluded():
    records = [
        _rec("18.05.2026", "выезд", "1", "Косич"),
        _rec("18.05.2026", "маршрут_завершён", "1", "Косич"),
    ]
    assert compute_active_routes(records, "18.05.2026") == []


def test_other_day_ignored():
    records = [_rec("17.05.2026", "выезд", "1", "Косич")]
    assert compute_active_routes(records, "18.05.2026") == []


def test_status_is_most_advanced_step_not_top_row():
    # Порядок строк не хронологический: выезд записан ВЫШЕ сборки.
    # Статус должен быть "выезд" (продвинутее), а не "начало_сборки".
    records = [
        _rec("18.05.2026", "выезд", "1", "Косич"),
        _rec("18.05.2026", "начало_сборки", "1", "Косич"),
    ]
    active = compute_active_routes(records, "18.05.2026")
    assert active[0]["status"] == "выезд"


def test_all_departed_closes_route():
    records = [
        _rec("18.05.2026", "выезд", "2", "Сергеєв"),
        _rec("18.05.2026", "все_выехали", "2", ""),
    ]
    assert compute_active_routes(records, "18.05.2026") == []
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `python3 -m pytest tests/test_core.py -k active -v`
Expected: FAIL — `ImportError: cannot import name 'compute_active_routes'`

- [ ] **Step 3: Реализовать в `core.py`**

```python
import unicodedata

# Ожидаемая цепочка событий маршрута
EVENT_CHAIN = ["начало_сборки", "сборка_завершена", "выезд", "маршрут_завершён"]

# Статусы завершения маршрута (NFC-нормализованы — варианты с ё/е)
CLOSED_STATUSES = {
    unicodedata.normalize("NFC", s) for s in [
        "маршрут_завершён", "маршрут_завершен", "все_выехали",
    ]
}


def normalize_route(route_raw) -> str:
    """Номер маршрута → строка (gspread может вернуть int/float)."""
    if isinstance(route_raw, float):
        return str(int(route_raw))
    if isinstance(route_raw, int):
        return str(route_raw)
    return str(route_raw).strip()


def compute_active_routes(records: list, today_str: str) -> list:
    """Активные маршруты (начаты, но не завершены) за today_str.

    records — список dict с ключами Дата/Время/Событие/Маршрут/Водитель.
    Для каждого маршрута берётся самый продвинутый шаг цепочки —
    порядок строк не всегда хронологический.
    """
    routes_info = {}
    closed_routes = set()
    for r in records:
        if r.get("Дата") != today_str:
            continue
        route = normalize_route(r.get("Маршрут", ""))
        if not route:
            continue
        status = unicodedata.normalize("NFC", str(r.get("Событие", "")))
        if status in CLOSED_STATUSES:
            closed_routes.add(route)
        rank = EVENT_CHAIN.index(status) if status in EVENT_CHAIN else -1
        prev = routes_info.get(route)
        if prev is None or rank > prev["_rank"]:
            routes_info[route] = {
                "route": route,
                "driver": r.get("Водитель", ""),
                "status": r.get("Событие", ""),
                "time": r.get("Время", ""),
                "_rank": rank,
            }
    active = [info for route, info in routes_info.items()
              if route not in closed_routes]
    for info in active:
        del info["_rank"]
    return active
```

- [ ] **Step 4: Запустить — убедиться, что проходят**

Run: `python3 -m pytest tests/test_core.py -v`
Expected: PASS (все тесты)

- [ ] **Step 5: Commit**

```bash
git add core.py tests/test_core.py
git commit -m "✨ core.compute_active_routes + тесты"
```

---

### Task 4: `compute_stats` — статистика за период

**Files:**
- Modify: `core.py`
- Test: `tests/test_core.py`

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_core.py`:

```python
from core import compute_stats


def test_stats_counts_by_type_and_driver():
    records = [
        {"Дата": "18.05.2026", "Событие": "выезд", "Водитель": "Косич",
         "Маршрут": "1", "Исходное сообщение": ""},
        {"Дата": "18.05.2026", "Событие": "выезд", "Водитель": "Сергеєв",
         "Маршрут": "2", "Исходное сообщение": ""},
        {"Дата": "17.05.2026", "Событие": "проблема", "Водитель": "Косич",
         "Маршрут": "1", "Исходное сообщение": "точку не доставив"},
    ]
    stats = compute_stats(records, lambda r: r.get("Дата") == "18.05.2026")
    assert stats["total_events"] == 2
    assert stats["by_type"]["выезд"] == 2
    assert stats["by_driver"]["Косич"] == 1


def test_stats_collects_problems():
    records = [{"Дата": "18.05.2026", "Событие": "проблема", "Водитель": "",
                "Маршрут": "", "Исходное сообщение": "поломка"}]
    stats = compute_stats(records, lambda r: True)
    assert stats["problems"] == ["поломка"]


def test_stats_empty_when_predicate_excludes_all():
    records = [{"Дата": "01.01.2020", "Событие": "выезд", "Водитель": "X",
                "Маршрут": "1", "Исходное сообщение": ""}]
    stats = compute_stats(records, lambda r: False)
    assert stats["total_events"] == 0
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `python3 -m pytest tests/test_core.py -k stats -v`
Expected: FAIL — `ImportError: cannot import name 'compute_stats'`

- [ ] **Step 3: Реализовать в `core.py`**

```python
def compute_stats(records: list, in_period) -> dict:
    """Статистика по записям, для которых in_period(record) истинно.

    in_period — предикат record -> bool (фильтр по дате/периоду).
    """
    stats = {
        "total_events": 0,
        "by_type": {},
        "by_driver": {},
        "by_route": {},
        "problems": [],
    }
    for r in records:
        if not in_period(r):
            continue
        stats["total_events"] += 1
        event_type = r.get("Событие", "unknown")
        stats["by_type"][event_type] = stats["by_type"].get(event_type, 0) + 1
        driver = r.get("Водитель", "")
        if driver:
            stats["by_driver"][driver] = stats["by_driver"].get(driver, 0) + 1
        route = r.get("Маршрут", "")
        if route:
            stats["by_route"][route] = stats["by_route"].get(route, 0) + 1
        if event_type == "проблема":
            stats["problems"].append(r.get("Исходное сообщение", ""))
    return stats
```

- [ ] **Step 4: Запустить — убедиться, что проходят**

Run: `python3 -m pytest tests/test_core.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core.py tests/test_core.py
git commit -m "✨ core.compute_stats + тесты"
```

---

### Task 5: `compute_chain_violation` — нарушение цепочки событий

**Files:**
- Modify: `core.py`
- Test: `tests/test_core.py`

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_core.py`:

```python
from core import compute_chain_violation


def test_chain_ok_when_all_steps_present():
    existing = ["начало_сборки", "сборка_завершена"]
    assert compute_chain_violation("выезд", existing) is None


def test_chain_first_step_never_violates():
    assert compute_chain_violation("начало_сборки", []) is None


def test_chain_reports_missing_steps():
    # Завершение есть, но не было сборки и выезда
    result = compute_chain_violation("маршрут_завершён", ["начало_сборки"])
    assert result == "збірка завершена, виїзд"


def test_chain_ignores_non_chain_event():
    assert compute_chain_violation("проблема", []) is None
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `python3 -m pytest tests/test_core.py -k chain -v`
Expected: FAIL — `ImportError: cannot import name 'compute_chain_violation'`

- [ ] **Step 3: Реализовать в `core.py`**

```python
# Человекочитаемые названия шагов цепочки (укр) для предупреждений
_CHAIN_STEP_NAMES = {
    "начало_сборки": "початок збірки",
    "сборка_завершена": "збірка завершена",
    "выезд": "виїзд",
    "маршрут_завершён": "завершення",
}


def compute_chain_violation(event_type: str, existing_event_types: list):
    """Описание пропущенных шагов цепочки или None, если всё ок.

    existing_event_types — типы событий, уже зафиксированные
    для этого маршрута за день.
    """
    if event_type not in EVENT_CHAIN:
        return None
    current_idx = EVENT_CHAIN.index(event_type)
    if current_idx == 0:
        return None  # начало_сборки — первый шаг, проверять нечего
    missing = [
        EVENT_CHAIN[i] for i in range(current_idx)
        if EVENT_CHAIN[i] not in existing_event_types
    ]
    if not missing:
        return None
    return ", ".join(_CHAIN_STEP_NAMES.get(m, m) for m in missing)
```

- [ ] **Step 4: Запустить — убедиться, что проходят**

Run: `python3 -m pytest tests/test_core.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core.py tests/test_core.py
git commit -m "✨ core.compute_chain_violation + тесты"
```

---

### Task 6: `count_stale_rows` — сколько строк удалить при чистке

**Files:**
- Modify: `core.py`
- Test: `tests/test_core.py`

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_core.py`:

```python
from datetime import date
from core import count_stale_rows


def test_stale_none_when_all_fresh():
    dates = ["18.05.2026", "17.05.2026", "16.05.2026"]
    assert count_stale_rows(dates, date(2026, 1, 1)) == 0


def test_stale_counts_bottom_old_rows():
    # Новые строки сверху. Cutoff = 01.05.2026.
    dates = ["18.05.2026", "10.05.2026", "20.04.2026", "01.04.2026"]
    # Старше cutoff — две нижние
    assert count_stale_rows(dates, date(2026, 5, 1)) == 2


def test_stale_keeps_row_below_a_fresh_one():
    # Локальная неупорядоченность: старая дата ВЫШЕ свежей.
    # Удаляем только то, ниже чего нет свежих дат.
    dates = ["18.05.2026", "01.01.2020", "17.05.2026", "01.04.2026"]
    assert count_stale_rows(dates, date(2026, 5, 1)) == 1


def test_stale_unparseable_date_is_kept():
    dates = ["18.05.2026", "мусор", "01.04.2026"]
    # "мусор" считается свежим (не удаляем), значит ниже него — 1 строка
    assert count_stale_rows(dates, date(2026, 5, 1)) == 1
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `python3 -m pytest tests/test_core.py -k stale -v`
Expected: FAIL — `ImportError: cannot import name 'count_stale_rows'`

- [ ] **Step 3: Реализовать в `core.py`**

```python
from datetime import datetime


def count_stale_rows(date_strings: list, cutoff) -> int:
    """Сколько НИЖНИХ строк можно удалить при чистке.

    date_strings — даты строк сверху вниз (формат %d.%m.%Y),
    новые сверху. cutoff — datetime.date: строки строго старше
    удаляются. Возвращает количество строк с конца, ниже которых
    нет ни одной свежей или нераспознанной даты — устойчиво к
    локальной неупорядоченности.
    """
    last_fresh = -1
    for i, ds in enumerate(date_strings):
        try:
            d = datetime.strptime(str(ds), "%d.%m.%Y").date()
        except (ValueError, TypeError):
            last_fresh = i  # нераспознанную дату не трогаем
            continue
        if d >= cutoff:
            last_fresh = i
    return len(date_strings) - 1 - last_fresh
```

- [ ] **Step 4: Запустить — убедиться, что проходят**

Run: `python3 -m pytest tests/test_core.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core.py tests/test_core.py
git commit -m "✨ core.count_stale_rows — расчёт чистки старых строк"
```

---

### Task 7: `paginate` — пагинация списка городов

**Files:**
- Modify: `core.py`
- Test: `tests/test_core.py`

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_core.py`:

```python
from core import paginate


def test_paginate_first_page():
    items = list(range(20))
    page_items, total = paginate(items, page=0, page_size=8)
    assert page_items == list(range(8))
    assert total == 3


def test_paginate_last_partial_page():
    items = list(range(20))
    page_items, total = paginate(items, page=2, page_size=8)
    assert page_items == [16, 17, 18, 19]
    assert total == 3


def test_paginate_clamps_out_of_range_page():
    items = list(range(20))
    page_items, total = paginate(items, page=99, page_size=8)
    assert page_items == [16, 17, 18, 19]


def test_paginate_empty_list():
    page_items, total = paginate([], page=0, page_size=8)
    assert page_items == []
    assert total == 1
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `python3 -m pytest tests/test_core.py -k paginate -v`
Expected: FAIL — `ImportError: cannot import name 'paginate'`

- [ ] **Step 3: Реализовать в `core.py`**

```python
def paginate(items: list, page: int, page_size: int):
    """Возвращает (элементы_страницы, всего_страниц).

    page — 0-based, выходит за границы → зажимается в [0, total-1].
    Пустой список → одна пустая страница.
    """
    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    return items[start:start + page_size], total_pages
```

- [ ] **Step 4: Запустить — убедиться, что проходят**

Run: `python3 -m pytest tests/test_core.py -v`
Expected: PASS (весь файл целиком)

- [ ] **Step 5: Commit**

```bash
git add core.py tests/test_core.py
git commit -m "✨ core.paginate — пагинация для inline-кнопок"
```

---

## Phase B — Листы городов в `sheets.py`

### Task 8: `_get_city_sheet` — get-or-create лист города + чтение по городу

**Files:**
- Modify: `sheets.py:41-53` (`__init__`), `sheets.py:54-101` (`connect`), `sheets.py:119-153` (`_get_recent_records`, `invalidate_cache`)

- [ ] **Step 1: Расширить `__init__` — кэш листов и записей по городам**

Заменить блок кэша в `__init__` (`sheets.py:47-52`):

```python
        self._cache = None        # кэш последнего чтения _get_recent_records
        self._cache_ts = 0        # timestamp кэша
        self._CACHE_TTL = 5       # TTL кэша в секундах
        self._mileage_lock = Lock()
        self._driver_rows_cache = None  # {driver_name: row_index_1_based}
        self._driver_rows_ts = 0
```

на:

```python
        self._city_sheets = {}    # {city_name: worksheet}
        self._cache = {}          # {city_name: (records, timestamp)}
        self._CACHE_TTL = 5       # TTL кэша в секундах
        self._mileage_lock = Lock()
        self._driver_rows_cache = None  # {driver_name: row_index_1_based}
        self._driver_rows_ts = 0
```

- [ ] **Step 2: Убрать привязку к `sheet1` в `connect`**

В `connect` удалить строки `sheets.py:80-84`:

```python
            # Получаем первый лист
            self.worksheet = self.spreadsheet.sheet1

            # Проверяем/добавляем заголовки
            self._ensure_headers()
```

Лист статистики больше не один — листы городов создаются по требованию. `_ensure_headers` удаляется целиком (Step 5).

- [ ] **Step 3: Добавить `import core` в начало `sheets.py`**

После `from config import config` (`sheets.py:17`):

```python
import core
```

- [ ] **Step 4: Добавить метод `_get_city_sheet`** (вставить после `connect`, перед удаляемым `_ensure_headers`)

```python
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
```

- [ ] **Step 5: Удалить `_ensure_headers`** (`sheets.py:103-117`) — он работал с единственным `self.worksheet`; заголовки теперь ставит `_get_city_sheet`.

- [ ] **Step 6: Переписать `_get_recent_records` под город**

Заменить `_get_recent_records` и `invalidate_cache` (`sheets.py:119-153`):

```python
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
```

- [ ] **Step 7: Проверить, что модуль импортируется**

Run: `python3 -c "import sheets; print('ok')"`
Expected: `ok` (синтаксис корректен; падающие ссылки на `self.worksheet` устранятся в Task 9)

- [ ] **Step 8: Commit**

```bash
git add sheets.py
git commit -m "♻️ sheets: листы на город (get-or-create) + кэш per-city"
```

---

### Task 9: Запись и аналитика по городу через `core`

**Files:**
- Modify: `sheets.py` — `add_event` (192-223), `get_today_stats` (233-236), `get_stats_for_period` (238-265), `_get_stats_for_date` (267-288), `get_active_routes` (326-382), `get_driver_departure_route` (384-404), `get_route_events_today` (409-423), `check_chain_violation` (425-456)
- Удалить из `sheets.py` дублирующие константы/методы, переехавшие в `core`: `_normalize_route` (317-324), `CLOSED_STATUSES` (309-315), `EVENT_CHAIN` (407), `_add_to_stats` (290-306), `add_events` (225-231, не используется)

- [ ] **Step 1: Переписать `add_event` — приём `city`**

Заменить сигнатуру и тело (`sheets.py:192-223`). Ключевые отличия: параметр `city`, лист берётся через `_get_city_sheet`, кэш инвалидируется по городу:

```python
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
```

- [ ] **Step 2: Удалить неиспользуемый `add_events`** (`sheets.py:225-231`).

- [ ] **Step 3: Переписать статистику под город**

Заменить `get_today_stats`, `get_stats_for_period`, `_get_stats_for_date`, `_add_to_stats` (`sheets.py:233-306`) на:

```python
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
                except ValueError:
                    return False
                return (today - rec_date).days <= days

            return core.compute_stats(records, in_period)
        except Exception as e:
            logger.error(f"Ошибка статистики за период ('{city}'): {e}")
            return {}
```

- [ ] **Step 4: Переписать `get_active_routes` под город**

Заменить `get_active_routes`, `CLOSED_STATUSES`, `_normalize_route` (`sheets.py:308-382`) на:

```python
    def get_active_routes(self, city: str) -> List[dict]:
        """Активные маршруты города (начаты, но не завершены) за сегодня."""
        try:
            today = datetime.now(TZ).strftime("%d.%m.%Y")
            records = self._get_recent_records(city, max_rows=200)
            return core.compute_active_routes(records, today)
        except Exception as e:
            logger.error(f"Ошибка активных маршрутов ('{city}'): {e}")
            return []
```

- [ ] **Step 5: Переписать `get_driver_departure_route` под город**

Заменить (`sheets.py:384-404`):

```python
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
```

- [ ] **Step 6: Переписать `get_route_events_today` и `check_chain_violation` под город**

Заменить `EVENT_CHAIN`, `get_route_events_today`, `check_chain_violation` (`sheets.py:406-456`):

```python
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
```

- [ ] **Step 7: Добавить метод списка городов**

Вставить после `check_chain_violation`:

```python
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
```

- [ ] **Step 8: Запустить весь набор тестов и проверить импорт**

Run: `python3 -m pytest tests/ -v && python3 -c "import sheets, bot, scheduler; print('imports ok')"`
Expected: тесты PASS. `imports ok` ожидаемо может ещё не пройти — `bot.py` чинится в Task 10. Допустимо: `python3 -c "import sheets; print('sheets ok')"` → `sheets ok`.

- [ ] **Step 9: Commit**

```bash
git add sheets.py
git commit -m "♻️ sheets: запись и аналитика по городу через core.py"
```

---

## Phase C — Интеграция города в `bot.py`

### Task 10: Определение города из чата + проброс + уведомления

**Files:**
- Modify: `bot.py` — `import` (38-41), `handle_message` (242-410), обработчики кнопок `stats_today`/`stats_week`/`active_routes`/`mileage_week` (100-209), `main` (418-475)

- [ ] **Step 1: Добавить импорт `core` и функцию определения города**

После `from scheduler import setup_scheduler` (`bot.py:41`):

```python
import core


def city_of(update: Update) -> str:
    """Имя листа-города для чата апдейта."""
    chat = update.effective_chat
    title = chat.title if chat else ""
    fallback = str(chat.id) if chat else "unknown"
    return core.sanitize_sheet_name(title, fallback)
```

- [ ] **Step 2: Пробросить город в `handle_message`**

В `handle_message` после получения `group_name` (`bot.py:301-304`) заменить блок на:

```python
    # Город = имя листа (из названия чата)
    group_name = update.effective_chat.title or "" if update.effective_chat else ""
    city = city_of(update)
```

Затем в цикле сохранения (`bot.py:310-316`) заменить вызов `add_event`:

```python
        else:
            ok = sheets_manager.add_event(event, city, group_name)
```

(Вызов `upsert_mileage` не меняется — пробег в общем листе.)

- [ ] **Step 3: Пробросить город в проверки цепочки**

В блоке проверок (`bot.py:351-382`) заменить три вызова:

- `sheets_manager.get_driver_departure_route(event.driver, today_str)` → `sheets_manager.get_driver_departure_route(city, event.driver, today_str)`
- `sheets_manager.check_chain_violation(event.event_type, event.route_number, today_str)` → `sheets_manager.check_chain_violation(city, event.event_type, event.route_number, today_str)`

- [ ] **Step 4: Уведомление о завершении — в текущий чат, по городу**

Заменить блок проверки завершения (`bot.py:386-410`) на:

```python
        # Закрыли последний маршрут города — уведомляем этот же чат
        has_route_completed = any(
            e.event_type == "маршрут_завершён" for e in events
        )
        if has_route_completed and update.effective_chat:
            await asyncio.sleep(1)  # дать Google API синхронизироваться
            remaining = sheets_manager.get_active_routes(city)
            logger.info(f"[DEBUG] '{city}': активных маршрутов = {len(remaining)}")
            if not remaining:
                try:
                    now = datetime.now(tz)
                    if now.hour < 19:
                        text = ("✅ Всі маршрути завершені\n\n"
                                "🎉 Сьогодні усі колеги-водії завершили "
                                "до 19:00. Дякуємо! 👏🚚")
                    else:
                        text = "✅ Всі маршрути завершені"
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id, text=text
                    )
                    logger.info(f"Уведомление о завершении: '{city}' (hour={now.hour})")
                except Exception as e:
                    logger.warning(f"Не удалось отправить уведомление: {e}")
```

Это устраняет ловушку из `bot.py:391` (локальная `active_routes`, перетиравшая функцию) — переменная переименована в `remaining`.

- [ ] **Step 5: Запустить тесты и проверить импорт всех модулей**

Run: `python3 -m pytest tests/ -q && python3 -c "import bot; print('bot ok')"`
Expected: тесты PASS, `bot ok`

- [ ] **Step 6: Commit**

```bash
git add bot.py
git commit -m "♻️ bot: определение города из чата, проброс в запись и проверки"
```

---

## Phase D — Конфигурация и отчёты по городам

### Task 11: `REPORT_CHAT_IDS` + отчёт 19:00 по каждому городу

**Files:**
- Modify: `config.py:29-34`, `.env.example`
- Modify: `scheduler.py` целиком

- [ ] **Step 1: Заменить `REPORT_CHAT_ID` на список в `config.py`**

В `config.py` заменить блок «Автоотчёты» (`config.py:29-34`):

```python
    # Автоотчёты
    # REPORT_CHAT_IDS — чаты для отчёта 19:00, через запятую.
    # Обратная совместимость: одиночный REPORT_CHAT_ID тоже принимается.
    _report_ids_raw = os.getenv("REPORT_CHAT_IDS", "") or os.getenv("REPORT_CHAT_ID", "")
    REPORT_CHAT_IDS: list = [
        c.strip() for c in _report_ids_raw.split(",") if c.strip()
    ]
    ACTIVE_ROUTES_REPORT_TIME: str = os.getenv("ACTIVE_ROUTES_REPORT_TIME", "19:00")

    # Чистка старых строк
    RETENTION_DAYS: int = int(os.getenv("RETENTION_DAYS", "90"))
    CLEANUP_TIME: str = os.getenv("CLEANUP_TIME", "03:00")
```

- [ ] **Step 2: Обновить `.env.example`**

Заменить строку `REPORT_CHAT_ID=` на:

```
# ID чатов для автоотчётов 19:00 (через запятую, для всех городов)
REPORT_CHAT_IDS=

# Хранение статистики: строки старше RETENTION_DAYS дней удаляются
RETENTION_DAYS=90
CLEANUP_TIME=03:00
```

- [ ] **Step 3: Переписать `scheduler.py` — отчёт по каждому городу**

Заменить `setup_scheduler` и `send_active_routes_report` (`scheduler.py:18-63`):

```python
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
```

- [ ] **Step 4: Добавить импорт `core` и job чистки в `scheduler.py`**

В начале `scheduler.py` после `from sheets import sheets_manager`:

```python
import core
```

Добавить функцию `cleanup_old_rows_job` в конец `scheduler.py`:

```python
async def cleanup_old_rows_job(context: ContextTypes.DEFAULT_TYPE):
    """Ночная чистка строк старше RETENTION_DAYS во всех листах городов."""
    try:
        removed = sheets_manager.cleanup_old_rows(config.RETENTION_DAYS)
        logger.info(f"Чистка завершена: удалено строк — {removed}")
    except Exception as e:
        logger.error(f"Ошибка ночной чистки: {e}")
```

- [ ] **Step 5: Проверить импорт**

Run: `python3 -c "import config, scheduler; print('ok')"`
Expected: `ok` (`cleanup_old_rows` появится в Task 12; импорт scheduler не падает, т.к. вызов внутри async-функции)

- [ ] **Step 6: Commit**

```bash
git add config.py scheduler.py .env.example
git commit -m "✨ Отчёты 19:00 по каждому городу + конфиг REPORT_CHAT_IDS"
```

---

## Phase E — Чистка старых строк

### Task 12: `cleanup_old_rows` в `sheets.py`

**Files:**
- Modify: `sheets.py` — добавить метод `cleanup_old_rows`

- [ ] **Step 1: Реализовать `cleanup_old_rows`**

Вставить в `SheetsManager` после `list_city_sheets`:

```python
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
                self.invalidate_cache(city)
                total_removed += stale
                logger.info(f"Чистка '{city}': удалено {stale} строк")
            except Exception as e:
                logger.error(f"Ошибка чистки листа '{city}': {e}")
        return total_removed
```

- [ ] **Step 2: Проверить импорт всех модулей**

Run: `python3 -c "import sheets, scheduler; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Прогнать все тесты**

Run: `python3 -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add sheets.py
git commit -m "✨ Чистка строк старше RETENTION_DAYS в листах городов"
```

---

## Phase F — Выбор города кнопками в личке

### Task 13: Inline-кнопки выбора города + `CallbackQueryHandler`

**Files:**
- Modify: `bot.py` — обработчики кнопок (100-228), `main` (418-475)

- [ ] **Step 1: Добавить импорты inline-клавиатуры**

В `bot.py` расширить импорт telegram (`bot.py:11`):

```python
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton, ReactionTypeEmoji,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
```

И в импорт `telegram.ext` (`bot.py:12-18`) добавить `CallbackQueryHandler`.

- [ ] **Step 2: Добавить построитель клавиатуры выбора города**

Вставить после `city_of` (из Task 10):

```python
CITY_PAGE_SIZE = 8

# Какому действию какая функция-рендер соответствует
ACTION_LABELS = {
    "today": "📊 Статистика сегодня",
    "week": "📈 За неделю",
    "active": "🚗 Активные маршруты",
}


def build_city_keyboard(action: str, page: int) -> InlineKeyboardMarkup:
    """Inline-клавиатура: список городов + пагинация для действия action."""
    cities = sorted(sheets_manager.list_city_sheets())
    page_cities, total_pages = core.paginate(cities, page, CITY_PAGE_SIZE)

    rows = [[InlineKeyboardButton(c, callback_data=f"city|{action}|{c}")]
            for c in page_cities]

    nav = []
    if total_pages > 1:
        if page > 0:
            nav.append(InlineKeyboardButton(
                "◀", callback_data=f"page|{action}|{page - 1}"))
        nav.append(InlineKeyboardButton(
            f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(
                "▶", callback_data=f"page|{action}|{page + 1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)
```

- [ ] **Step 3: Переписать обработчики кнопок — показывать выбор города**

Заменить тела `stats_today`, `stats_week`, `active_routes` и обработчик `handle_buttons` (`bot.py:100-153, 212-229`). Кнопки личного меню теперь не считают статистику сразу, а показывают список городов:

```python
async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на reply-кнопки в личке."""
    if not check_access(update):
        await access_denied(update)
        return
    text = update.message.text

    action_by_label = {
        "📊 Статистика сегодня": "today",
        "📈 За неделю": "week",
        "🚗 Активные маршруты": "active",
    }
    if text in action_by_label:
        action = action_by_label[text]
        cities = sheets_manager.list_city_sheets()
        if not cities:
            await update.message.reply_text("Пока нет ни одного города.")
            return
        await update.message.reply_text(
            f"{text} — выбери город:",
            reply_markup=build_city_keyboard(action, page=0),
        )
    elif text == "❓ Помощь":
        await help_command(update, context)
    elif text == "📏 Километраж за неделю":
        await mileage_week(update, context)
```

Старые `stats_today`, `stats_week`, `active_routes` (как самостоятельные обработчики) удалить — их роль берёт `render_city_data` (Step 4). `format_stats` и `mileage_week` сохранить без изменений.

- [ ] **Step 4: Добавить обработчик callback-запросов**

Вставить новую функцию:

```python
async def on_city_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает inline-кнопки выбора города и пагинации."""
    query = update.callback_query
    await query.answer()
    if not config.is_user_allowed(query.from_user.id):
        return

    parts = query.data.split("|")
    kind = parts[0]

    if kind == "noop":
        return

    if kind == "page":
        action, page = parts[1], int(parts[2])
        await query.edit_message_reply_markup(
            reply_markup=build_city_keyboard(action, page)
        )
        return

    if kind == "city":
        action, city = parts[1], parts[2]
        text = render_city_data(action, city)
        await query.edit_message_text(text)


def render_city_data(action: str, city: str) -> str:
    """Текст ответа для действия action по городу city."""
    if action == "today":
        stats = sheets_manager.get_today_stats(city)
        if not stats or stats.get("total_events", 0) == 0:
            return f"📊 {city}: за сегодня событий пока нет."
        return f"🏙 {city}\n" + format_stats(stats, "сегодня")
    if action == "week":
        stats = sheets_manager.get_stats_for_period(city, 7)
        if not stats or stats.get("total_events", 0) == 0:
            return f"📊 {city}: за последние 7 дней событий нет."
        return f"🏙 {city}\n" + format_stats(stats, "неделю")
    if action == "active":
        routes = sheets_manager.get_active_routes(city)
        if not routes:
            return f"🚗 {city}: активных маршрутов нет."
        text = f"🚗 {city} — активные маршруты:\n\n"
        for r in routes:
            driver = f" ({r['driver']})" if r.get("driver") else ""
            status = (r.get("status") or "").replace("_", " ")
            text += f"• Маршрут {r.get('route') or '?'}{driver} — {status} в {r.get('time') or ''}\n"
        return text
    return "Неизвестное действие."
```

- [ ] **Step 5: Зарегистрировать `CallbackQueryHandler` в `main`**

В `main` после обработчика `/start` (`bot.py:439`) добавить:

```python
    app.add_handler(CallbackQueryHandler(on_city_callback))
```

- [ ] **Step 6: Проверить импорт и тесты**

Run: `python3 -m pytest tests/ -q && python3 -c "import bot; print('bot ok')"`
Expected: тесты PASS, `bot ok`

- [ ] **Step 7: Commit**

```bash
git add bot.py
git commit -m "✨ Выбор города inline-кнопками с пагинацией"
```

---

## Phase G — Лист «Пробіг»: блоки по городам, снять лимит 11

### Task 14: Динамический набор строк водителей

**Files:**
- Modify: `sheets.py` — `MILEAGE_DRIVER_ROWS_RANGE` (33), `_create_month_block` (667-830), `get_weekly_mileage` (832-895)

**Контекст:** Сейчас формулы создаются жёстко для строк 4–14 (`range(4, 15)`), а недельный пробег читает `range(3, 14)`. При блоках по городам водители занимают строки 4–N с разрывами под подзаголовки. Подзаголовок города человек ставит в колонку A, колонку B на этой строке оставляет пустой — `_get_driver_rows` уже пропускает пустые B (`sheets.py:490-493`). Нужно сделать набор строк водителей динамическим.

- [ ] **Step 1: Расширить диапазон имён водителей**

В `sheets.py:33` заменить:

```python
    MILEAGE_DRIVER_ROWS_RANGE = "B4:B300"  # имена водителей (блоки по городам)
```

- [ ] **Step 2: Формулы `_create_month_block` — для фактических строк водителей**

В `_create_month_block` заменить блок построения `formula_requests` (`sheets.py:681-697`). Вместо `range(4, 15)` — строки из `_get_driver_rows()`:

```python
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
```

- [ ] **Step 3: `get_weekly_mileage` — читать все строки водителей**

В `get_weekly_mileage` заменить диапазон чтения и цикл (`sheets.py:845-888`). Читать до строки 300 и идти по непустым именам в колонке B:

```python
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
```

- [ ] **Step 4: Проверить импорт и тесты**

Run: `python3 -m pytest tests/ -q && python3 -c "import sheets; print('sheets ok')"`
Expected: тесты PASS, `sheets ok`

- [ ] **Step 5: Commit**

```bash
git add sheets.py
git commit -m "✨ Пробіг: динамический набор водителей, снят лимит 11 (блоки по городам)"
```

---

### Task 15: Backfill формул «Пробіг» в существующих блоках

**Files:**
- Modify: `core.py` — добавить `find_mileage_blocks`
- Modify: `sheets.py` — добавить `backfill_mileage_formulas`
- Modify: `bot.py` — команда `/backfill`
- Test: `tests/test_core.py`

**Контекст:** Task 14 заполняет формулы новым водителям только при создании *нового* блока месяца. Для уже существующих блоков формулы новым водителям (строки >14) надо досоздать. Эта задача делает это командой `/backfill` — идемпотентной (существующие формулы не трогает, можно запускать многократно).

**Структура листа «Пробіг» (проверено на реальной таблице):** строка 1 — метки (`Расчёт`, `Расчёт`, `M2026-05`…); строка 3 — заголовки (`Пробег км`, `Расход топл`, дни); колонка C — `Плановый расход на 100км`. Формула пробега строки 4: `=SUMIFS($4:$4;$1:$1;"M2026-05")`, формула расхода: `=D4/100*C4`.

- [ ] **Step 1: Написать падающие тесты `find_mileage_blocks`**

Добавить в `tests/test_core.py`:

```python
from core import find_mileage_blocks


def test_find_blocks_single():
    row1 = ["", "", "", "Расчёт", "Расчёт", "M2026-05", "M2026-05"]
    row3 = ["№", "Водитель", "Плановый расход", "Пробег км", "Расход топл",
            "16.05.26", "15.05.26"]
    assert find_mileage_blocks(row1, row3) == [(3, "M2026-05")]


def test_find_blocks_two_months():
    # Свежий блок слева (Июнь), старый справа (Май)
    row1 = ["", "", "", "Расчёт", "Расчёт", "M2026-06",
            "Расчёт", "Расчёт", "M2026-05", "M2026-05"]
    row3 = ["№", "Водитель", "Плановый расход", "Пробег км", "Расход топл",
            "01.06.26", "Пробег км", "Расход топл", "16.05.26", "15.05.26"]
    assert find_mileage_blocks(row1, row3) == [(3, "M2026-06"), (6, "M2026-05")]


def test_find_blocks_none():
    assert find_mileage_blocks(["", ""], ["№", "Водитель"]) == []
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `python3 -m pytest tests/test_core.py -k blocks -v`
Expected: FAIL — `ImportError: cannot import name 'find_mileage_blocks'`

- [ ] **Step 3: Реализовать `find_mileage_blocks` в `core.py`**

```python
def find_mileage_blocks(row1: list, row3: list) -> list:
    """Находит блоки месяцев на листе «Пробіг».

    row1 — строка меток (M{YYYY-MM} над колонками дней),
    row3 — строка заголовков («Пробег км», «Расход топл», дни).
    Возвращает [(индекс_колонки_«Пробег км», метка_месяца), ...].
    """
    blocks = []
    for i, header in enumerate(row3):
        if header != "Пробег км":
            continue
        label = row1[i + 2] if i + 2 < len(row1) else ""
        if isinstance(label, str) and label.startswith("M"):
            blocks.append((i, label))
    return blocks
```

- [ ] **Step 4: Запустить — убедиться, что проходят**

Run: `python3 -m pytest tests/test_core.py -v`
Expected: PASS

- [ ] **Step 5: Реализовать `backfill_mileage_formulas` в `sheets.py`**

Вставить в `SheetsManager` после `cleanup_old_rows`:

```python
    def backfill_mileage_formulas(self) -> int:
        """Проставляет формулы «Пробег км»/«Расход топл» всем водителям
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
                for row_1based in driver_rows:
                    row_idx0 = row_1based - 1
                    existing = grid[row_idx0] if row_idx0 < len(grid) else []
                    for mcol, month_label in blocks:
                        current = existing[mcol] if mcol < len(existing) else ""
                        if str(current).strip():
                            continue  # формула/значение уже есть
                        mileage_formula = (
                            f"=SUMIFS(${row_1based}:${row_1based};"
                            f'$1:$1;"{month_label}")'
                        )
                        fuel_formula = (
                            "=" + self._col_letter(mcol + 1) + str(row_1based)
                            + "/100*C" + str(row_1based)
                        )
                        requests.append({"updateCells": {
                            "range": {"sheetId": sheet_id,
                                      "startRowIndex": row_idx0,
                                      "endRowIndex": row_1based,
                                      "startColumnIndex": mcol,
                                      "endColumnIndex": mcol + 2},
                            "rows": [{"values": [
                                {"userEnteredValue": {"formulaValue": mileage_formula}},
                                {"userEnteredValue": {"formulaValue": fuel_formula}},
                            ]}],
                            "fields": "userEnteredValue",
                        }})

                if not requests:
                    logger.info("backfill: все формулы уже на месте")
                    return 0

                self.spreadsheet.batch_update({"requests": requests})
                logger.info(f"backfill: заполнено {len(requests)} строк-формул")
                return len(requests)
            except Exception as e:
                logger.error(f"Ошибка backfill формул пробега: {e}", exc_info=True)
                return 0
```

- [ ] **Step 6: Добавить команду `/backfill` в `bot.py`**

Вставить функцию после `help_command`:

```python
async def backfill_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Одноразовая команда: досоздать формулы пробега всем водителям."""
    if not check_access(update):
        await access_denied(update)
        return
    await update.message.reply_text("⏳ Заповнюю формули пробігу...")
    count = sheets_manager.backfill_mileage_formulas()
    await update.message.reply_text(f"✅ Готово. Заповнено формул: {count}")
```

В `main` после `app.add_handler(CommandHandler("start", start))` (`bot.py:439`) добавить:

```python
    app.add_handler(CommandHandler("backfill", backfill_command))
```

- [ ] **Step 7: Запустить тесты и проверить импорт**

Run: `python3 -m pytest tests/ -q && python3 -c "import bot, sheets, core; print('imports ok')"`
Expected: тесты PASS, `imports ok`

- [ ] **Step 8: Commit**

```bash
git add core.py sheets.py bot.py tests/test_core.py
git commit -m "✨ Команда /backfill — досоздание формул «Пробіг» в старых блоках"
```

---

## Финальная проверка

### Task 16: Прогон, обновление документации

**Files:**
- Modify: `CLAUDE.md`, `README.md`

- [ ] **Step 1: Полный прогон тестов**

Run: `python3 -m pytest tests/ -v`
Expected: все PASS

- [ ] **Step 2: Проверить парсер не сломан**

Run: `python3 parser.py`
Expected: вывод тестовых сообщений без ошибок

- [ ] **Step 3: Проверить импорт всех модулей**

Run: `python3 -c "import bot, sheets, scheduler, config, core, parser; print('all imports ok')"`
Expected: `all imports ok`

- [ ] **Step 4: Обновить `CLAUDE.md`**

Внести: лист на город, лист «Пробіг» первым с блоками по городам, переменные `REPORT_CHAT_IDS`/`RETENTION_DAYS`/`CLEANUP_TIME`, модуль `core.py`, папка `tests/`, чистка строк >90 дней.

- [ ] **Step 5: Обновить `README.md`**

Синхронизировать раздел переменных окружения и структуру проекта.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "📝 Доки: поддержка нескольких городов"
```

---

## Phase H — Поэтапный запуск

### Task 17: Города только для пробега (`FULL_STATS_CHAT_IDS`)

**Цель:** Поэтапный запуск бота по городам. Для нового города бот сначала читает только пробег (в общий лист «Пробіг»), а статистику событий маршрутов (сборка/выезд/завершение, отдельный лист города) ведёт только для явно перечисленных чатов. Когда город «готов» к полной статистике — его chat_id добавляется в `FULL_STATS_CHAT_IDS`.

**Files:**
- Modify: `config.py` — `FULL_STATS_CHAT_IDS` + `is_full_stats_chat`
- Modify: `.env.example`
- Modify: `bot.py` — `handle_message`

**Контекст:** Пробег (`upsert_mileage`) уже город-независим — пишет в общий лист «Пробіг» по имени водителя. Менять надо только запись событий маршрутов: `add_event` для не-перечисленных чатов не вызывать, лист города для них не создавать, проверки цепочки и уведомление о завершении — пропускать.

- [ ] **Step 1: Добавить `FULL_STATS_CHAT_IDS` и `is_full_stats_chat` в `config.py`**

После блока с `RETENTION_DAYS`/`CLEANUP_TIME` добавить:
```python
    # Чаты с полной статистикой событий маршрутов (chat_id через запятую).
    # Если задан — события сборки/выезда/завершения пишутся в лист города
    # только для этих чатов; для остальных бот ведёт только пробег.
    # Пусто — полная статистика для всех чатов (обратная совместимость).
    _full_stats_raw = os.getenv("FULL_STATS_CHAT_IDS", "")
    FULL_STATS_CHAT_IDS: list = [
        c.strip() for c in _full_stats_raw.split(",") if c.strip()
    ]
```

Рядом с методом `is_user_allowed` добавить classmethod:
```python
    @classmethod
    def is_full_stats_chat(cls, chat_id) -> bool:
        """Вести ли полную статистику событий маршрутов для чата.

        Пустой FULL_STATS_CHAT_IDS → True для всех (обратная совместимость).
        """
        if not cls.FULL_STATS_CHAT_IDS:
            return True
        return str(chat_id) in cls.FULL_STATS_CHAT_IDS
```

- [ ] **Step 2: Обновить `.env.example`**

После строк `RETENTION_DAYS`/`CLEANUP_TIME` добавить:
```
# Чаты с полной статистикой маршрутов (chat_id через запятую).
# Пусто = полная статистика для всех. Для поэтапного запуска укажи
# только готовые города; остальные чаты ведут только пробег.
FULL_STATS_CHAT_IDS=
```

- [ ] **Step 3: Развилка в `handle_message` (`bot.py`)**

В `handle_message` после вычисления `chat_id` (он уже считается для дедупликации: `chat_id = update.effective_chat.id if update.effective_chat else 0`) определить флаг:
```python
    full_stats = config.is_full_stats_chat(chat_id)
```

В цикле сохранения событий заменить ветку записи события так, чтобы не-mileage событие писалось только при `full_stats`:
```python
        if event.event_type == parser.EVENT_MILEAGE:
            ok = sheets_manager.upsert_mileage(
                event.driver, event.mileage_km, datetime.now(tz)
            )
        elif full_stats:
            ok = sheets_manager.add_event(event, city, group_name)
        else:
            continue  # mileage-only чат — события маршрутов не пишем
```

Блок «Проверка цепочки событий и предупреждения» (цикл `for event in events` с `check_chain_violation`/`get_driver_departure_route`) и блок «Закрыли последний маршрут города — уведомляем» выполнять только при `full_stats` — обернуть оба в `if full_stats:`. Реакцию 🏆 (`msg.set_reaction`) оставить как есть — она уместна и для записанного пробега.

- [ ] **Step 4: Проверка**

Run: `python3 -m pytest tests/ -q && python3 -c "import bot, config; print('ok')"`
Expected: тесты PASS, `ok`

- [ ] **Step 5: Commit**

```bash
git add config.py bot.py .env.example
git commit -m "✨ FULL_STATS_CHAT_IDS — поэтапный запуск (mileage-only города)"
```

---

## Развёртывание (после слияния)

Перед `git push` в `main` (CI задеплоит автоматически) — вручную на сервере:

1. **Создать лист «Пробіг» первой вкладкой** (если ещё не первая) — переместить.
2. **Перенести текущие данные**: существующий Лист1 переименовать в название чата Киева (точно как Telegram-title) — он станет листом города.
3. **Заполнить `.env` на сервере**: `REPORT_CHAT_IDS` (бывший `REPORT_CHAT_ID` подхватится автоматически). Для поэтапного запуска — `FULL_STATS_CHAT_IDS` (chat_id Киева): новые города при этом ведут только пробег, пока их chat_id не добавлен в этот список.
4. **Лист «Пробіг»**: расставить подзаголовки городов в колонке A, водителей — в колонку B блоками, норму расхода — в колонку C. Затем отправить боту команду `/backfill` — она проставит формулы «Пробег км»/«Расход топл» всем водителям во всех существующих блоках месяцев.

---

## Self-Review

**Spec coverage:**
- ✅ Лист на город — Task 8, 9
- ✅ Идентификатор = telegram title — `sanitize_sheet_name` (Task 2), `city_of` (Task 10)
- ✅ Выбор города кнопками с пагинацией — Task 7, 13
- ✅ Чистка >90 дней — Task 6, 11, 12
- ✅ «Пробіг» общий, блоки по городам, лимит 11 снят — Task 14
- ✅ Backfill формул «Пробіг» в существующих блоках — Task 15 (`find_mileage_blocks`, `/backfill`)
- ✅ Поэтапный запуск: города только для пробега — Task 17 (`FULL_STATS_CHAT_IDS`)
- ✅ Юнит-тесты на фильтрацию аналитики — Task 1–7, 15 (`tests/test_core.py`)
- ✅ Отчёт 19:00 по каждому городу — Task 11
- ✅ Уведомление о завершении в нужный чат — Task 10

**Type consistency:** `add_event(event, city, group_name)`, `get_*(city, ...)`, `core.compute_*` — сигнатуры согласованы между Task 9, 10, 11. `count_stale_rows`/`paginate`/`sanitize_sheet_name`/`find_mileage_blocks` вызываются ровно с теми параметрами, что определены в Task 2, 6, 7, 15.

**Известные ограничения (приняты осознанно):**
- Переименование Telegram-чата создаст новый лист — данные «раздвоятся» (цена выбора title как идентификатора).
- Два чата с одинаковым названием → один лист (коллизия). Маловероятно для разных городов.
- Формулы «Пробіг» новым водителям проставляются автоматически при создании нового блока месяца; для уже существующих блоков — одноразовая команда `/backfill` (Task 15), идемпотентная.
