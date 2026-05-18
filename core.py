"""Чистые функции вычислений логистики — без обращений к Google API.

Выделены из sheets.py, чтобы покрыть фильтрацию и аналитику
юнит-тестами без моков gspread.
"""

# Запрещённые символы в имени листа Google Sheets: []:*?/\
# Скобки заменяются на пробел, остальное удаляется
_CHARS_TO_REPLACE = set("[]")
_CHARS_TO_DELETE = set(":*?/\\")

# Служебные имена листов, которые нельзя занимать под город
RESERVED_SHEET_NAMES = {"Пробіг"}


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
