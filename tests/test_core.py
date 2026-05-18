import core
from core import sanitize_sheet_name, compute_active_routes


def test_module_imports():
    assert core is not None


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


def test_sanitize_keeps_normal_name():
    assert sanitize_sheet_name("Логістика Суми", "fallback") == "Логістика Суми"


def test_sanitize_strips_forbidden_chars():
    # []:*?/\ запрещены в именах листов Google Sheets
    assert sanitize_sheet_name("Суми [2]/гілка", "fb") == "Суми  2 гілка"


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
