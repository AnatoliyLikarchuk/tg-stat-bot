# Список валидных водителей из листа «Пробіг» — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Вынести захардкоженную константу `KNOWN_DRIVERS` из `parser.py` — список валидных водителей берётся из листа «Пробіг» (колонка B), добавление водителя больше не требует правки кода и редеплоя.

**Architecture:** Парсер остаётся чистым модулем без Google API — список валидных водителей приходит параметром в `parse()`. `bot.py` берёт список из листа «Пробіг» через новый публичный метод `SheetsManager.get_mileage_drivers()` (переиспользует кэш `_get_driver_rows()`, TTL 1 час) и передаёт в парсер.

**Tech Stack:** Python 3.11, python-telegram-bot, gspread.

**Spec:** `docs/superpowers/specs/2026-05-19-known-drivers-from-sheet-design.md`

---

## File Structure

| Файл | Ответственность | Действие |
|------|------------------|----------|
| `parser.py` | Парсинг сообщений. Убрать константу `KNOWN_DRIVERS`, `parse()` принимает список водителей параметром. | Изменить |
| `sheets.py` | Работа с Google Sheets. Новый публичный метод `get_mileage_drivers()`. | Изменить |
| `bot.py` | Точка вызова парсера: взять список водителей из листа и передать в `parse()`. | Изменить |

Тесты парсера — встроенный self-тест (`python parser.py`), как и сейчас в проекте. Новый pytest-файл не создаём. `get_mileage_drivers()` — тонкая обёртка над `_get_driver_rows()` без чистой логики, отдельным юнит-тестом не покрывается (Google API в проекте не мокается).

---

## Task 1: `parser.py` — `parse()` принимает `known_drivers`, убрать `KNOWN_DRIVERS`

**Files:**
- Modify: `parser.py` (константа на строках 56–61, сигнатура `parse` на строке 189, проверка на строке 212, self-тест на строках 499–552)

- [ ] **Step 1: Дописать падающий self-тест**

В блоке `if __name__ == "__main__"` (начинается на строке 499) после строки `parser = MessageParser()` добавить тестовое множество водителей:

```python
if __name__ == "__main__":
    parser = MessageParser()

    test_drivers = {"Косич"}
```

В список `test_messages` перед закрывающей `]` добавить два кейса пробега:

```python
        # Формат "выехал ИМЯ мршт" (без номера маршрута)
        "11:47 выехал Роговский мршт ",
        # Пробег: известный водитель → событие, неизвестный → молча игнорируется
        "Косич 120 км",
        "Сегодня проехали 200 км",
    ]
```

Изменить вызов в цикле — передать `test_drivers`:

```python
    for msg in test_messages:
        print(f"\n--- {msg} ---")
        events = parser.parse(msg, test_drivers)
        for e in events:
            print(f"  {e.event_type}: маршрут={e.route_number}, водитель={e.driver}, время={e.time}")
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `python parser.py`
Expected: FAIL — `TypeError: MessageParser.parse() takes 2 positional arguments but 3 were given`

- [ ] **Step 3: Изменить сигнатуру `parse()` и проверку, удалить `KNOWN_DRIVERS`**

Изменить сигнатуру `parse` (строка 189):

```python
    def parse(self, text: str, known_drivers=frozenset()) -> List[ParsedEvent]:
```

Изменить проверку известного водителя (строка 212):

```python
            if name in known_drivers:
```

Удалить константу `KNOWN_DRIVERS` вместе с комментарием — заменить блок:

```python
    }

    # Список валидных водителей для учёта километража.
    # Сообщение от водителя не из этого списка → молча игнорируется.
    KNOWN_DRIVERS = {
        "Буркало", "Галунько", "Горбатко", "Горобець", "Грабіченко",
        "Карпенко", "Качаєнко", "Косич", "Овчаренко", "Роговський", "Сергеєв",
    }

    # Транслитерация рус → укр (для автоматической нормализации)
```

на:

```python
    }

    # Транслитерация рус → укр (для автоматической нормализации)
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `python parser.py`
Expected: PASS — выводятся все события. Проверить визуально:
- блок `--- Косич 120 км ---` содержит строку `пробег: маршрут=None, водитель=Косич, время=None`;
- блок `--- Сегодня проехали 200 км ---` не содержит ни одной строки события (водитель «Сегодня» не в `test_drivers`).

- [ ] **Step 5: Commit**

```bash
git add parser.py
git commit -m "♻️ parser: список водителей через параметр parse(), убран хардкод KNOWN_DRIVERS"
```

---

## Task 2: `sheets.py` — публичный метод `get_mileage_drivers()`

**Files:**
- Modify: `sheets.py` (добавить метод после `_get_driver_rows()`, который заканчивается на строке 452)

- [ ] **Step 1: Добавить метод `get_mileage_drivers()`**

Сразу после метода `_get_driver_rows()` (перед `def upsert_mileage` на строке 454) добавить:

```python
    def get_mileage_drivers(self) -> set:
        """Множество имён водителей из листа «Пробіг» (колонка B).

        Источник правды о валидных водителях для учёта пробега.
        Переиспользует кэш _get_driver_rows() (TTL 1 час). Если лист
        недоступен — возвращает пустое множество.
        """
        return set(self._get_driver_rows().keys())
```

- [ ] **Step 2: Проверить, что модуль импортируется**

Run: `python -c "import sheets; print('ok')"`
Expected: вывод `ok` (без SyntaxError / ImportError)

- [ ] **Step 3: Commit**

```bash
git add sheets.py
git commit -m "✨ sheets: get_mileage_drivers() — список водителей из листа «Пробіг»"
```

---

## Task 3: `bot.py` — прокинуть список водителей в `parser.parse()`

**Files:**
- Modify: `bot.py:338` (вызов `events = parser.parse(text)`)

- [ ] **Step 1: Передать список водителей в парсер**

Заменить строку 338:

```python
    events = parser.parse(text)
```

на:

```python
    known_drivers = sheets_manager.get_mileage_drivers()
    events = parser.parse(text, known_drivers)
```

(`sheets_manager` уже импортирован в `bot.py` на строке 44, `parser` — экземпляр `MessageParser` на строке 104.)

- [ ] **Step 2: Проверить импорт и юнит-тесты**

Run: `python -c "import bot; print('ok')" && python -m pytest tests/ -q`
Expected: вывод `ok`, затем `30 passed` (тесты `core.py` не затронуты).

- [ ] **Step 3: Commit**

```bash
git add bot.py
git commit -m "🔧 bot: список водителей из «Пробіг» прокинут в parser.parse()"
```

---

## После реализации (вне объёма плана)

Развёртывание — мерж в `main`, CI задеплоит автоматически. После деплоя — ручная проверка (из спецификации):
- известный водитель пишет «Имя 120 км» → пробег записывается в лист «Пробіг»;
- сообщение «Сегодня проехали 200 км» → событие пробега не создаётся;
- добавить строку водителя в лист «Пробіг», перезапустить бота → новый водитель пишется без правок кода.

---

## Self-Review

**Spec coverage:**
- ✅ Список из листа «Пробіг» — Task 2 (`get_mileage_drivers()`), Task 3 (проброс в `bot.py`)
- ✅ Удаление `KNOWN_DRIVERS` — Task 1
- ✅ Парсер остаётся чистым (список параметром) — Task 1 (`parse(text, known_drivers)`)
- ✅ Дефолт `frozenset()` — безопасный вызов без параметра — Task 1, Step 3
- ✅ Мягкая деградация при недоступном листе — `_get_driver_rows()` возвращает `{}` → `get_mileage_drivers()` возвращает пустое множество (Task 2)
- ✅ Self-тест парсера с кейсами пробега — Task 1, Step 1

**Type consistency:** `get_mileage_drivers() -> set` (Task 2) вызывается в `bot.py` и результат передаётся в `parse(text, known_drivers=frozenset())` (Task 1) — типы согласованы (`set` ⊆ ожидаемого множества). Проверка `name in known_drivers` работает с любым множеством.

**Объём:** 3 файла, мелкие диффы, один независимый блок изменений — декомпозиции на отдельные планы не требует.
