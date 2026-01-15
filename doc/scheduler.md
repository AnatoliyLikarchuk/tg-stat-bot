# Автоматические отчёты

**✅ Реализовано:** 15.01.2026 — коммит `5f37018`
**✅ Исправлен timezone:** 15.01.2026 — коммит `08a049b`

## Задача

Уведомлять группу о состоянии маршрутов:
1. **Мгновенно** — когда все маршруты завершены
2. **В 19:00** — если остались незавершённые маршруты

## Логика работы

### Сценарий 1: Все маршруты закрыты до 19:00

```
18:30 — водитель закрывает последний маршрут
18:30 — бот сразу отправляет: "✅ Всі маршрути завершені"
19:00 — ничего не отправляется (уже сообщили)
```

### Сценарий 2: Есть незакрытые маршруты в 19:00

```
19:00 — бот отправляет список активных маршрутов:
        "🚗 Активні маршрути:
         • Маршрут 3 (Петренко) — выезд в 14:30
         • Маршрут 5 (Коваль) — выезд в 15:10"
```

### Сценарий 3: Маршруты закрыты после 19:00

```
19:00 — бот отправил список активных (2 маршрута)
20:30 — закрыт последний маршрут
20:30 — бот отправляет: "✅ Всі маршрути завершені"
```

## Реализация

### 1. Мгновенное уведомление (bot.py)

В `handle_message()` после сохранения события `маршрут_завершён`:

```python
if event.event_type == "маршрут_завершён":
    active = sheets_manager.get_active_routes()
    if not active:
        await context.bot.send_message(
            chat_id=config.REPORT_CHAT_ID,
            text="✅ Всі маршрути завершені"
        )
```

### 2. Отчёт в 19:00 (scheduler.py)

Используется **JobQueue** из python-telegram-bot с поддержкой timezone:

```python
from datetime import time
import pytz

def setup_scheduler(application):
    tz = pytz.timezone(config.TIMEZONE)  # Europe/Kiev
    report_time = time(hour=19, minute=0, tzinfo=tz)

    application.job_queue.run_daily(
        send_active_routes_report,
        time=report_time,
        name="active_routes_report"
    )

async def send_active_routes_report(context):
    routes = sheets_manager.get_active_routes()
    if not routes:
        return  # Все закрыты, сообщение уже было

    text = format_active_routes(routes)
    await context.bot.send_message(chat_id=REPORT_CHAT_ID, text=text)
```

## Конфигурация

В `.env`:

```
REPORT_CHAT_ID=-1001927251688
ACTIVE_ROUTES_REPORT_TIME=19:00  # опционально, по умолчанию 19:00
TIMEZONE=Europe/Kiev             # обязательно для правильного времени
```

**Важно:** Для супергрупп Telegram добавляется префикс `-100` к ID.

## Требования

- Бот должен быть добавлен в группу
- Бот должен иметь права на отправку сообщений
- REPORT_CHAT_ID должен быть указан в .env
- `python-telegram-bot[job-queue]` — для APScheduler

## История изменений

| Дата | Коммит | Изменение |
|------|--------|-----------|
| 15.01.2026 | `5f37018` | Первая реализация (библиотека schedule) |
| 15.01.2026 | `08a049b` | Исправлен timezone — заменено на JobQueue |

### Баг с timezone (исправлен)

**Проблема:** Библиотека `schedule` использовала системное время сервера (UTC), а не `TIMEZONE` из конфигурации. Отчёт отправлялся в 19:00 UTC = 21:00 по Киеву.

**Решение:** Замена на встроенный JobQueue из python-telegram-bot, который:
- Правильно работает с timezone через pytz
- Интегрирован с event loop (без проблем с asyncio)
- Использует APScheduler под капотом
