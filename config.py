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
    REPORT_CHAT_ID: str = os.getenv("REPORT_CHAT_ID", "")
    DAILY_REPORT_TIME: str = os.getenv("DAILY_REPORT_TIME", "20:00")
    WEEKLY_REPORT_DAY: int = int(os.getenv("WEEKLY_REPORT_DAY", "0"))  # 0 = понедельник
    WEEKLY_REPORT_TIME: str = os.getenv("WEEKLY_REPORT_TIME", "09:00")

    # Часовой пояс
    TIMEZONE: str = os.getenv("TIMEZONE", "Europe/Kiev")

    @classmethod
    def validate(cls) -> bool:
        """Проверяет обязательные настройки."""
        if not cls.TELEGRAM_BOT_TOKEN:
            print("Ошибка: TELEGRAM_BOT_TOKEN не установлен")
            return False
        return True


# Синглтон для удобства импорта
config = Config()
