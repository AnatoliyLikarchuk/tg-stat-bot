import asyncio
import inspect
from types import SimpleNamespace

import bot


class FakeContext:
    def __init__(self):
        self.user_data = {}


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text, reply_markup=None):
        self.replies.append((text, reply_markup))


class FakeQuery:
    def __init__(self, data, user_id=101):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.answers = []
        self.edits = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


class FakeUpdate:
    def __init__(self, *, message=None, query=None, chat_type="private"):
        self.message = message
        self.callback_query = query
        self.effective_chat = SimpleNamespace(type=chat_type)
        self.effective_user = SimpleNamespace(id=101)


class FakeSheetsManager:
    def __init__(self, roster=None):
        self.roster = roster or {"active": {}, "archived": {}}
        self.roster_calls = 0
        self.add_calls = []
        self.archive_calls = []
        self.restore_calls = []
        self.aliases = {"марченко": "Марченко"}
        self.alias_add_calls = []
        self.alias_remove_calls = []
        self.add_result = result(True, "added", "Марченко", "Киев")
        self.archive_result = result(True, "archived", "Марченко", "Киев")
        self.restore_results = [result(True, "restored", "Марченко", "Киев")]

    def get_driver_roster(self):
        self.roster_calls += 1
        return self.roster

    def add_mileage_driver(self, city, driver, fuel_rate):
        self.add_calls.append((city, driver, fuel_rate))
        return self.add_result

    def archive_mileage_driver(self, city, driver):
        self.archive_calls.append((city, driver))
        return self.archive_result

    def restore_mileage_driver(self, driver, target_city):
        self.restore_calls.append((driver, target_city))
        return self.restore_results.pop(0)

    def get_driver_aliases(self):
        return {"ok": True, "aliases": self.aliases}

    def add_driver_alias(self, driver, alias):
        self.alias_add_calls.append((driver, alias))
        return SimpleNamespace(
            ok=True, code="alias_added", alias=alias, driver=driver
        )

    def remove_driver_alias(self, driver, alias):
        self.alias_remove_calls.append((driver, alias))
        return SimpleNamespace(
            ok=True, code="alias_removed", alias=alias, driver=driver
        )


def result(ok, code, driver=None, city=None):
    return SimpleNamespace(ok=ok, code=code, driver=driver, city=city)


def run_callback(data, context, *, chat_type="private"):
    query = FakeQuery(data)
    update = FakeUpdate(query=query, chat_type=chat_type)
    asyncio.run(bot.on_driver_callback(update, context))
    return query


def run_private_text(text, context):
    message = FakeMessage(text)
    update = FakeUpdate(message=message)
    asyncio.run(bot.handle_private_text(update, context))
    return message


def callback_values(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def allow_driver_ui(monkeypatch, manager):
    monkeypatch.setattr(bot.config, "is_user_allowed", lambda user_id: True)
    monkeypatch.setattr(bot, "check_access", lambda update: True)
    monkeypatch.setattr(bot, "sheets_manager", manager)


def test_driver_button_and_inline_menu_are_available(monkeypatch):
    manager = FakeSheetsManager()
    allow_driver_ui(monkeypatch, manager)
    context = FakeContext()
    message = FakeMessage("👥 Водители")

    asyncio.run(bot.handle_buttons(FakeUpdate(message=message), context))

    assert "👥 Водители" in bot.BUTTON_LABELS
    assert bot.MAIN_KEYBOARD.keyboard[0][0].text == "👥 Водії"
    assert bot.MAIN_KEYBOARD.keyboard[-1][0].text == "🧮 Заповнити формули"
    text, markup = message.replies[-1]
    assert "Керування водіями" in text
    assert markup.inline_keyboard[0][0].text == "➕ Додати водія"
    assert markup.inline_keyboard[1][0].text == "📦 Перемістити до звільнених"
    assert callback_values(markup) == [
        "drv|add", "drv|archive", "drv|restore", "drv|aliases"
    ]


def test_roster_read_failure_is_not_reported_as_an_empty_table(monkeypatch):
    manager = FakeSheetsManager({"ok": False, "active": {}, "archived": {}})
    allow_driver_ui(monkeypatch, manager)
    context = FakeContext()

    query = run_callback("drv|add", context)

    assert "Таблиця зараз недоступна" in query.edits[-1][0]
    assert manager.add_calls == []


def test_add_driver_flow_validates_text_and_accepts_comma_rate(monkeypatch):
    manager = FakeSheetsManager({
        "active": {"Одесса": ["Косич"], "Киев": ["Попович"]},
        "archived": {},
    })
    allow_driver_ui(monkeypatch, manager)
    context = FakeContext()

    add_menu = run_callback("drv|add", context)
    add_city_callback = callback_values(add_menu.edits[-1][1])[0]
    query = run_callback(add_city_callback, context)
    assert "Місто: Киев" in query.edits[-1][0]

    invalid_name = run_private_text("Марченко Иван", context)
    assert "канонічне прізвище" in invalid_name.replies[-1][0]
    assert context.user_data[bot.DRIVER_FLOW_KEY]["step"] == "driver"

    surname = run_private_text("Марченко", context)
    assert "Водій: Марченко" in surname.replies[-1][0]
    assert context.user_data[bot.DRIVER_FLOW_KEY]["step"] == "fuel_rate"

    invalid_rate = run_private_text("0", context)
    assert "більше за 0" in invalid_rate.replies[-1][0]
    assert context.user_data[bot.DRIVER_FLOW_KEY]["step"] == "fuel_rate"

    confirmation = run_private_text("12,5", context)
    assert "12,5 л/100 км" in confirmation.replies[-1][0]
    add_confirm_callback = callback_values(confirmation.replies[-1][1])[0]
    assert add_confirm_callback.startswith("drv|add_confirm|")

    completed = run_callback(add_confirm_callback, context)

    assert manager.add_calls == [("Киев", "Марченко", 12.5)]
    assert "Марченко додано до міста Киев" in completed.edits[-1][0]
    assert bot.DRIVER_FLOW_KEY not in context.user_data


def test_add_result_code_is_shown_and_flow_is_cleared(monkeypatch):
    manager = FakeSheetsManager()
    manager.add_result = result(False, "duplicate_driver", "Марченко", "Киев")
    allow_driver_ui(monkeypatch, manager)
    context = FakeContext()
    context.user_data[bot.DRIVER_FLOW_KEY] = {
        "action": "add",
        "step": "confirm",
        "city": "Киев",
        "driver": "Марченко",
        "fuel_rate": 12.5,
        "token": "addtoken",
    }

    query = run_callback("drv|add_confirm|addtoken", context)

    assert "уже є в таблиці" in query.edits[-1][0]
    assert bot.DRIVER_FLOW_KEY not in context.user_data


def test_archive_offers_undo_and_repeated_undo_is_idempotent(monkeypatch):
    manager = FakeSheetsManager({
        "active": {"Киев": ["Марченко"]},
        "archived": {},
    })
    manager.restore_results = [
        result(True, "restored", "Марченко", "Киев"),
        result(False, "already_active", "Марченко", "Киев"),
    ]
    allow_driver_ui(monkeypatch, manager)
    context = FakeContext()

    archive_menu = run_callback("drv|archive", context)
    city_callback = callback_values(archive_menu.edits[-1][1])[0]
    city_menu = run_callback(city_callback, context)
    driver_callback = callback_values(city_menu.edits[-1][1])[0]
    confirm_menu = run_callback(driver_callback, context)
    confirm_callback = callback_values(confirm_menu.edits[-1][1])[0]
    archived = run_callback(confirm_callback, context)

    assert manager.archive_calls == [("Киев", "Марченко")]
    buttons = callback_values(archived.edits[-1][1])
    undo_callback = next(value for value in buttons if value.startswith("drv|undo|"))
    assert len(undo_callback.split("|")[2]) == 8
    assert bot.DRIVER_FLOW_KEY not in context.user_data

    undone = run_callback(undo_callback, context)
    repeated = run_callback(undo_callback, context)

    assert manager.restore_calls == [
        ("Марченко", "Киев"),
        ("Марченко", "Киев"),
    ]
    assert "Переміщення скасовано" in undone.edits[-1][0]
    assert "уже серед чинних" in repeated.edits[-1][0]


def test_lost_undo_token_is_safe_and_does_not_touch_sheets(monkeypatch):
    manager = FakeSheetsManager()
    allow_driver_ui(monkeypatch, manager)
    context = FakeContext()

    query = run_callback("drv|undo|missing1", context)

    assert "застаріла" in query.edits[-1][0]
    assert manager.restore_calls == []


def test_old_confirmation_token_cannot_apply_a_newer_flow(monkeypatch):
    manager = FakeSheetsManager()
    allow_driver_ui(monkeypatch, manager)
    context = FakeContext()
    context.user_data[bot.DRIVER_FLOW_KEY] = {
        "action": "archive",
        "step": "confirm",
        "city": "Киев",
        "driver": "Марченко",
        "token": "newtoken",
    }

    query = run_callback("drv|archive_confirm|oldtoken", context)

    assert "меню застаріло" in query.edits[-1][0]
    assert manager.archive_calls == []


def test_restore_can_target_another_active_city(monkeypatch):
    manager = FakeSheetsManager({
        "active": {"Киев": ["Попович"], "Одесса": ["Косич"]},
        "archived": {"Киев": ["Марченко"]},
    })
    manager.restore_results = [result(True, "restored", "Марченко", "Одесса")]
    allow_driver_ui(monkeypatch, manager)
    context = FakeContext()

    restore_menu = run_callback("drv|restore", context)
    driver_callback = callback_values(restore_menu.edits[-1][1])[0]
    cities = run_callback(driver_callback, context)
    city_callbacks = callback_values(cities.edits[-1][1])[:2]
    assert all(value.startswith("drv|restore_city|") for value in city_callbacks)
    confirm_menu = run_callback(city_callbacks[1], context)
    confirm_callback = callback_values(confirm_menu.edits[-1][1])[0]
    restored = run_callback(confirm_callback, context)

    assert manager.restore_calls == [("Марченко", "Одесса")]
    assert "Марченко повернуто до чинних. Місто: Одесса" in restored.edits[-1][0]
    assert bot.DRIVER_FLOW_KEY not in context.user_data


def test_cancel_clears_active_flow(monkeypatch):
    manager = FakeSheetsManager()
    allow_driver_ui(monkeypatch, manager)
    context = FakeContext()
    context.user_data[bot.DRIVER_FLOW_KEY] = {
        "action": "add", "step": "driver", "city": "Киев"
    }

    query = run_callback("drv|cancel", context)

    assert query.edits[-1][0] == "Дію скасовано."
    assert bot.DRIVER_FLOW_KEY not in context.user_data


def test_driver_callbacks_are_rejected_in_group(monkeypatch):
    manager = FakeSheetsManager({"active": {"Киев": []}, "archived": {}})
    allow_driver_ui(monkeypatch, manager)
    context = FakeContext()

    query = run_callback("drv|add", context, chat_type="group")

    assert query.answers == [(
        "Керування водіями доступне лише в особистих повідомленнях", True
    )]
    assert manager.roster_calls == 0


def test_canonical_driver_is_not_silently_transliterated(monkeypatch):
    manager = FakeSheetsManager({"active": {"Київ": []}, "archived": {}})
    allow_driver_ui(monkeypatch, manager)
    context = FakeContext()
    context.user_data[bot.DRIVER_FLOW_KEY] = {
        "action": "add", "step": "driver", "city": "Київ"
    }

    invalid = run_private_text("Сыров", context)

    assert "українською" in invalid.replies[-1][0]
    assert context.user_data[bot.DRIVER_FLOW_KEY]["step"] == "driver"


def test_alias_can_be_added_and_removed_from_driver_menu(monkeypatch):
    manager = FakeSheetsManager({
        "active": {"Київ": ["Сергеєв"]}, "archived": {}
    })
    allow_driver_ui(monkeypatch, manager)
    context = FakeContext()

    choose_driver = run_callback("drv|alias_add", context)
    driver_callback = callback_values(choose_driver.edits[-1][1])[0]
    prompt = run_callback(driver_callback, context)
    assert "Канонічне прізвище: Сергеєв" in prompt.edits[-1][0]

    confirmation = run_private_text("Сергеев", context)
    confirm_callback = callback_values(confirmation.replies[-1][1])[0]
    completed = run_callback(confirm_callback, context)
    assert manager.alias_add_calls == [("Сергеєв", "Сергеев")]
    assert "Аліас «Сергеев» додано" in completed.edits[-1][0]

    manager.aliases = {"сергеев": "Сергеєв"}
    choose_alias = run_callback("drv|alias_remove", context)
    remove_callback = callback_values(choose_alias.edits[-1][1])[0]
    confirmation = run_callback(remove_callback, context)
    confirm_callback = callback_values(confirmation.edits[-1][1])[0]
    removed = run_callback(confirm_callback, context)
    assert manager.alias_remove_calls == [("Сергеєв", "сергеев")]
    assert "видалено" in removed.edits[-1][0]


def test_driver_callback_handler_is_registered_before_general_handler():
    source = inspect.getsource(bot.main)

    driver_handler = source.index(
        'CallbackQueryHandler(on_driver_callback, pattern=r"^drv\\|")'
    )
    general_handler = source.index("CallbackQueryHandler(on_city_callback)")
    assert driver_handler < general_handler
