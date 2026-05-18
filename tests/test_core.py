import core
from core import sanitize_sheet_name


def test_module_imports():
    assert core is not None


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
