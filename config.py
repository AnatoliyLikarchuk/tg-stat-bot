"""
Конфигурация бота.
Загружает настройки из .env файла.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем .env из директории проекта
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)


class Config:
    """Настройки приложения."""

    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

    # Google Sheets
    GOOGLE_SHEETS_CREDENTIALS_FILE: str = os.getenv(
        "GOOGLE_SHEETS_CREDENTIALS_FILE", "credentials.json"
    )
    GOOGLE_SHEETS_SPREADSHEET_NAME: str = os.getenv(
        "GOOGLE_SHEETS_SPREADSHEET_NAME", "Логистика Статистика"
    )

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

    # Часовой пояс
    TIMEZONE: str = os.getenv("TIMEZONE", "Europe/Kiev")

    # Белый список пользователей (Telegram user_id через запятую)
    # Дефолтные разрешённые пользователи (если ALLOWED_USERS не задан)
    _DEFAULT_ALLOWED_USERS: set = {148941669, 8386112275}

    ALLOWED_USERS: set = _DEFAULT_ALLOWED_USERS.copy()
    _allowed_users_str = os.getenv("ALLOWED_USERS", "")
    if _allowed_users_str:
        ALLOWED_USERS = {int(uid.strip()) for uid in _allowed_users_str.split(",") if uid.strip()}

    @classmethod
    def is_user_allowed(cls, user_id: int) -> bool:
        """Проверяет, разрешён ли доступ пользователю."""
        return user_id in cls.ALLOWED_USERS

    @classmethod
    def validate(cls) -> bool:
        """Проверяет обязательные настройки."""
        if not cls.TELEGRAM_BOT_TOKEN:
            print("Ошибка: TELEGRAM_BOT_TOKEN не установлен")
            return False
        return True


# Синглтон для удобства импорта
config = Config()
