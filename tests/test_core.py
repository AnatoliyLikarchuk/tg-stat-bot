import core
from datetime import date
from core import sanitize_sheet_name, compute_active_routes, compute_stats, compute_chain_violation, count_stale_rows, paginate


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
