"""Чистые функции вычислений логистики — без обращений к Google API.

Выделены из sheets.py, чтобы покрыть фильтрацию и аналитику
юнит-тестами без моков gspread.
"""

import unicodedata

# Запрещённые символы в имени листа Google Sheets: []:*?/\
# Скобки заменяются на пробел, остальное удаляется
_CHARS_TO_REPLACE = set("[]")
_CHARS_TO_DELETE = set(":*?/\\")

# Служебные имена листов, которые нельзя занимать под город
RESERVED_SHEET_NAMES = {"Пробіг"}

# Ожидаемая цепочка событий маршрута
EVENT_CHAIN = ["начало_сборки", "сборка_завершена", "выезд", "маршрут_завершён"]

# Статусы завершения маршрута (NFC-нормализованы — варианты с ё/е)
CLOSED_STATUSES = {
    unicodedata.normalize("NFC", s) for s in [
        "маршрут_завершён", "маршрут_завершен", "все_выехали",
    ]
}

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


def sanitize_sheet_name(title: str, fallback: str) -> str:
    """Название Telegram-чата → корректное имя листа Google Sheets.

    Правила Google Sheets: 1..100 символов, без []:*?/\\,
    не начинается/заканчивается апострофом. Пустое или служебное
    имя → fallback (обычно строковый chat_id).
    """
    name = (title or "").strip()
    result = []
    for ch in name:
        if ch in _CHARS_TO_REPLACE:
            result.append(" ")
        elif ch not in _CHARS_TO_DELETE:
            result.append(ch)
    name = "".join(result)
    name = name.strip("'").strip()
    name = name[:100].strip()
    if not name or name in RESERVED_SHEET_NAMES:
        return fallback
    return name
