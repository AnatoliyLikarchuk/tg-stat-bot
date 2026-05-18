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

    # Чаты с полной статистикой событий маршрутов (chat_id через запятую).
    # Если задан — события сборки/выезда/завершения пишутся в лист города
    # только для этих чатов; для остальных бот ведёт только пробег.
    # Пусто — полная статистика для всех чатов (обратная совместимость).
    _full_stats_raw = os.getenv("FULL_STATS_CHAT_IDS", "")
    FULL_STATS_CHAT_IDS: list = [
        c.strip() for c in _full_stats_raw.split(",") if c.strip()
    ]

    # Часовой пояс
    TIMEZONE: str = os.getenv("TIMEZONE", "Europe/Kiev")

    # Белый список пользователей (Telegram user_id через запятую).
    # Читается только из .env. Пусто → set() → личные команды и кнопки
    # бота недоступны всем (парсинг сообщений в группах не затрагивается).
    _allowed_users_str = os.getenv("ALLOWED_USERS", "")
    ALLOWED_USERS: set = {
        int(uid.strip()) for uid in _allowed_users_str.split(",") if uid.strip()
    }

    @classmethod
    def is_user_allowed(cls, user_id: int) -> bool:
        """Проверяет, разрешён ли доступ пользователю."""
        return user_id in cls.ALLOWED_USERS

    @classmethod
    def is_full_stats_chat(cls, chat_id) -> bool:
        """Вести ли полную статистику событий маршрутов для чата.

        Пустой FULL_STATS_CHAT_IDS → True для всех (обратная совместимость).
        """
        if not cls.FULL_STATS_CHAT_IDS:
            return True
        return str(chat_id) in cls.FULL_STATS_CHAT_IDS

    @classmethod
    def validate(cls) -> bool:
        """Проверяет обязательные настройки."""
        if not cls.TELEGRAM_BOT_TOKEN:
            print("Ошибка: TELEGRAM_BOT_TOKEN не установлен")
            return False
        return True


# Синглтон для удобства импорта
config = Config()
