import asyncio

import bot


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


class FakeSheetsManager:
    def __init__(self, count):
        self.count = count
        self.called = False

    def backfill_mileage_formulas(self):
        self.called = True
        return self.count


def test_backfill_command_calls_sheets_manager_and_replies(monkeypatch):
    message = FakeMessage()
    update = type("FakeUpdate", (), {"message": message})()
    manager = FakeSheetsManager(count=3)
    monkeypatch.setattr(bot, "check_access", lambda update: True)
    monkeypatch.setattr(bot, "sheets_manager", manager)

    asyncio.run(bot.backfill_command(update, None))

    assert manager.called is True
    assert message.replies == [
        "⏳ Заповнюю формули пробігу...",
        "✅ Готово. Заповнено формул: 3",
    ]
