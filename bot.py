"""
Telegram-бот для сбора статистики логистики.
Парсит сообщения из групп и сохраняет в Google Sheets.
"""

import asyncio
import logging
import re
import secrets
import time as time_module
from datetime import datetime, timedelta
from typing import Optional
import pytz
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton, ReactionTypeEmoji,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from telegram.error import BadRequest, NetworkError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
)

# Клавиатура с командами
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("👥 Водії")],
        [KeyboardButton("📊 Статистика сьогодні"), KeyboardButton("📈 За тиждень")],
        [KeyboardButton("🚗 Активні маршрути"), KeyboardButton("❓ Допомога")],
        [KeyboardButton("📏 Кілометраж за тиждень")],
        [KeyboardButton("🧮 Заповнити формули")],
    ],
    resize_keyboard=True
)

BUTTON_LABELS = {
    "📊 Статистика сьогодні",
    "📈 За тиждень",
    "🚗 Активні маршрути",
    "❓ Допомога",
    "📏 Кілометраж за тиждень",
    "🧮 Заповнити формули",
    "👥 Водії",
    # Старые reply-кнопки остаются валидными после обновления Telegram-клавиатуры.
    "📊 Статистика сегодня",
    "📈 За неделю",
    "🚗 Активные маршруты",
    "❓ Помощь",
    "📏 Километраж за неделю",
    "🧮 Заполнить формулы",
    "👥 Водители",
}

from config import config
from parser import MessageParser
from sheets import sheets_manager
from scheduler import setup_scheduler
import core


def city_of(update: Update) -> str:
    """Имя листа-города для чата апдейта."""
    chat = update.effective_chat
    title = chat.title if chat else ""
    fallback = str(chat.id) if chat else "unknown"
    return core.sanitize_sheet_name(title, fallback)


CITY_PAGE_SIZE = 8

# Какому действию какая функция-рендер соответствует
ACTION_LABELS = {
    "today": "📊 Статистика сьогодні",
    "week": "📈 За тиждень",
    "active": "🚗 Активні маршрути",
}


def build_city_keyboard(action: str, page: int) -> InlineKeyboardMarkup:
    """Inline-клавиатура: список городов + пагинация для действия action.

    Город кодируется в callback_data индексом в отсортированном списке,
    а не именем: имя может превысить лимит callback_data Telegram
    (64 байта) или содержать разделитель «|».
    """
    cities = sorted(sheets_manager.list_city_sheets())
    indexes, total_pages = core.paginate(
        list(range(len(cities))), page, CITY_PAGE_SIZE)

    rows = [[InlineKeyboardButton(cities[i], callback_data=f"city|{action}|{i}")]
            for i in indexes]

    nav = []
    if total_pages > 1:
        if page > 0:
            nav.append(InlineKeyboardButton(
                "◀", callback_data=f"page|{action}|{page - 1}"))
        nav.append(InlineKeyboardButton(
            f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(
                "▶", callback_data=f"page|{action}|{page + 1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация парсера
parser = MessageParser()

# Состояние диалога управления водителями хранится только в user_data.
# Так кадровые кнопки не влияют на обычный парсинг групповых сообщений.
DRIVER_FLOW_KEY = "driver_management_flow"
DRIVER_UNDO_KEY = "driver_management_undo"
DRIVER_UNDO_LIMIT = 5
DRIVER_CANONICAL_NAME_RE = re.compile(
    r"^[А-ЩЬЮЯІЇЄҐ][а-щьюяіїєґ']+$", re.IGNORECASE
)
DRIVER_ALIAS_RE = re.compile(r"^[А-ЯІЇЄҐЁ][а-яіїєґё']+$", re.IGNORECASE)


def build_driver_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное inline-меню управления водителями."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Додати", callback_data="drv|add")],
        [InlineKeyboardButton("📦 До звільнених", callback_data="drv|archive")],
        [InlineKeyboardButton("♻️ Повернути до чинних", callback_data="drv|restore")],
        [InlineKeyboardButton("🔤 Аліаси", callback_data="drv|aliases")],
    ])


def _driver_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Скасувати", callback_data="drv|cancel")
    ]])


def _driver_menu_return_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👥 До водіїв", callback_data="drv|menu")
    ]])


def _driver_choice_keyboard(
    labels: list[str], callback_action: str, token: str
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            label,
            callback_data=f"drv|{callback_action}|{token}|{idx}",
        )]
        for idx, label in enumerate(labels)
    ]
    rows.append([InlineKeyboardButton("❌ Скасувати", callback_data="drv|cancel")])
    return InlineKeyboardMarkup(rows)


def _clear_driver_flow(context: ContextTypes.DEFAULT_TYPE):
    user_data = getattr(context, "user_data", None)
    if isinstance(user_data, dict):
        user_data.pop(DRIVER_FLOW_KEY, None)


def _get_driver_flow(context: ContextTypes.DEFAULT_TYPE) -> Optional[dict]:
    user_data = getattr(context, "user_data", None)
    if not isinstance(user_data, dict):
        return None
    flow = user_data.get(DRIVER_FLOW_KEY)
    return flow if isinstance(flow, dict) else None


def _set_driver_flow(context: ContextTypes.DEFAULT_TYPE, **flow) -> str:
    token = secrets.token_hex(4)
    flow["token"] = token
    context.user_data[DRIVER_FLOW_KEY] = flow
    return token


def _get_driver_roster() -> dict:
    """Возвращает безопасную копию двух разделов roster-контракта."""
    roster = sheets_manager.get_driver_roster() or {}
    active = roster.get("active") if isinstance(roster, dict) else {}
    archived = roster.get("archived") if isinstance(roster, dict) else {}
    return {
        "ok": bool(roster.get("ok", True)) if isinstance(roster, dict) else False,
        "active": active if isinstance(active, dict) else {},
        "archived": archived if isinstance(archived, dict) else {},
    }


def _get_driver_aliases() -> dict:
    data = sheets_manager.get_driver_aliases() or {}
    aliases = data.get("aliases") if isinstance(data, dict) else {}
    return {
        "ok": bool(data.get("ok")) if isinstance(data, dict) else False,
        "aliases": aliases if isinstance(aliases, dict) else {},
    }


def _format_fuel_rate(rate: float) -> str:
    return f"{rate:g}".replace(".", ",")


def _driver_result_error(code: str) -> str:
    messages = {
        "sheet_unavailable": "Таблиця зараз недоступна. Спробуй ще раз пізніше.",
        "aliases_sheet_unavailable": "Довідник аліасів зараз недоступний.",
        "invalid_city": "Місто не пройшло перевірку. Відкрий меню та вибери його знову.",
        "invalid_driver": "Прізвище не пройшло перевірку.",
        "invalid_alias": "Аліас має бути одним прізвищем кирилицею.",
        "invalid_fuel_rate": "Норма витрати має бути більшою за 0 і не більшою за 100 л/100 км.",
        "duplicate_driver": "Такий водій уже є в таблиці — серед чинних або звільнених.",
        "city_not_found": "Місто більше не знайдено. Онови меню та повтори дію.",
        "driver_not_found": "Водія більше не знайдено. Можливо, список уже змінився.",
        "already_archived": "Водій уже перебуває серед звільнених.",
        "already_active": "Водія вже повернуто до чинних.",
        "alias_matches_driver": "Аліас збігається з канонічним прізвищем і не потрібен.",
        "alias_is_driver": "Такий аліас збігається з прізвищем іншого водія.",
        "alias_exists": "Цей аліас уже додано цьому водієві.",
        "alias_conflict": "Цей аліас уже належить іншому водієві.",
        "alias_not_found": "Аліас більше не знайдено.",
        "sheets_error": (
            "Не вдалося підтвердити операцію в Google Sheets. "
            "Відкрий меню та перевір актуальний список."
        ),
    }
    return messages.get(code, "Не вдалося виконати операцію. Спробуй ще раз.")


def _remember_driver_undo(context: ContextTypes.DEFAULT_TYPE, driver: str, city: str) -> str:
    undo_actions = context.user_data.setdefault(DRIVER_UNDO_KEY, {})
    if not isinstance(undo_actions, dict):
        undo_actions = {}
        context.user_data[DRIVER_UNDO_KEY] = undo_actions

    # Старая кнопка того же водителя не должна отменить его
    # более позднее, уже другое перемещение.
    stale_driver_tokens = [
        old_token for old_token, old_action in undo_actions.items()
        if isinstance(old_action, dict) and old_action.get("driver") == driver
    ]
    for old_token in stale_driver_tokens:
        del undo_actions[old_token]

    token = secrets.token_hex(4)
    while token in undo_actions:
        token = secrets.token_hex(4)
    undo_actions[token] = {"driver": driver, "city": city}

    while len(undo_actions) > DRIVER_UNDO_LIMIT:
        oldest = next(iter(undo_actions))
        del undo_actions[oldest]
    return token

# Дедупликация: {(chat_id, event_type, route, driver): timestamp}
_recent_events: dict = {}
DEDUP_WINDOW_SEC = 60

# Реакция — подтверждение уже выполненной записи, поэтому её сетевой запрос
# должен быть коротким, но с несколькими попытками на случай временного сбоя.
REACTION_RETRY_DELAYS = (1.0, 3.0)
REACTION_REQUEST_TIMEOUT_SEC = 5.0


async def set_saved_reaction(msg) -> bool:
    """Поставить реакцию-подтверждение с retry при сетевых ошибках Telegram."""
    attempts = len(REACTION_RETRY_DELAYS) + 1

    for attempt in range(1, attempts + 1):
        try:
            await msg.set_reaction(
                reaction=[ReactionTypeEmoji("🏆")],
                connect_timeout=REACTION_REQUEST_TIMEOUT_SEC,
                read_timeout=REACTION_REQUEST_TIMEOUT_SEC,
                write_timeout=REACTION_REQUEST_TIMEOUT_SEC,
                pool_timeout=REACTION_REQUEST_TIMEOUT_SEC,
            )
            return True
        except BadRequest as e:
            # Ошибку прав или валидации повтором не исправить.
            logger.warning(f"Не удалось поставить реакцию: {e}")
            return False
        except NetworkError as e:
            if attempt == attempts:
                logger.warning(
                    f"Не удалось поставить реакцию после {attempts} попыток: {e}"
                )
                return False

            delay = REACTION_RETRY_DELAYS[attempt - 1]
            logger.warning(
                f"Не удалось поставить реакцию "
                f"(попытка {attempt}/{attempts}): {e}; retry через {delay:g}с"
            )
            await asyncio.sleep(delay)
        except Exception as e:
            logger.warning(f"Не удалось поставить реакцию: {e}")
            return False

    return False


def check_access(update: Update) -> bool:
    """Проверяет доступ пользователя."""
    if not update.effective_user:
        return False
    return config.is_user_allowed(update.effective_user.id)


async def access_denied(update: Update):
    """Отправляет сообщение об отказе в доступе."""
    await update.message.reply_text("⛔ Доступ заборонено")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    if not check_access(update):
        await access_denied(update)
        return
    await update.message.reply_text(
        "Привіт! Я бот для збору статистики логістики.\n\n"
        "Використовуй кнопки нижче для роботи зі статистикою.",
        reply_markup=MAIN_KEYBOARD
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help."""
    if not check_access(update):
        await access_denied(update)
        return
    await update.message.reply_text(
        "📊 Бот статистики логістики\n\n"
        "Події, які відстежуються:\n"
        "• Початок збору\n"
        "• Збір завершено\n"
        "• Виїзд маршруту\n"
        "• Завершення маршруту\n"
        "• Проблеми доставки\n\n"
        "В особистому меню «👥 Водії» можна додати водія, перемістити "
        "його до звільнених, повернути назад і керувати аліасами.\n\n"
        "Додай бота до групи логістики, і він автоматично розбиратиме "
        "повідомлення та зберігатиме статистику."
    )


async def backfill_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Одноразовая команда: досоздать формулы пробега всем водителям."""
    if not check_access(update):
        await access_denied(update)
        return
    await update.message.reply_text("⏳ Заповнюю формули пробігу...")
    count = sheets_manager.backfill_mileage_formulas()
    await update.message.reply_text(f"✅ Готово. Заповнено формул: {count}")


def format_stats(stats: dict, period: str) -> str:
    """Форматирует статистику для вывода."""
    text = f"📊 Статистика за {period}\n\n"
    text += f"Усього подій: {stats['total_events']}\n\n"

    if stats.get("by_type"):
        text += "За типами:\n"
        type_names = {
            "начало_сборки": "🔧 Початок збору",
            "сборка_завершена": "✅ Збір завершено",
            "выезд": "🚗 Виїзд",
            "маршрут_завершён": "🏁 Маршрут завершено",
            "все_выехали": "🎉 Усі виїхали",
            "проблема": "⚠️ Проблеми"
        }
        for event_type, count in stats["by_type"].items():
            name = type_names.get(event_type, event_type.replace('_', ' '))
            text += f"  {name}: {count}\n"
        text += "\n"

    if stats.get("by_driver"):
        text += "За водіями:\n"
        for driver, count in sorted(stats["by_driver"].items(), key=lambda x: -x[1]):
            text += f"  • {driver}: {count} подій\n"
        text += "\n"

    if stats.get("problems"):
        text += f"Проблеми ({len(stats['problems'])}):\n"
        for problem in stats["problems"][:5]:  # Максимум 5
            text += f"  — {problem[:50]}...\n"

    return text


async def mileage_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Километраж по водителям за последние 7 дней."""
    if not check_access(update):
        await access_denied(update)
        return
    rows = sheets_manager.get_weekly_mileage()

    if not rows:
        await update.message.reply_text("📏 За останні 7 днів пробіг не записано.")
        return

    today = datetime.now(pytz.timezone(config.TIMEZONE)).date()
    start = today - timedelta(days=6)
    text = f"📏 Километраж {start:%d.%m}—{today:%d.%m}\n\n"
    total = 0
    for r in rows:
        text += f"  • {r['driver']}: {r['km']} км\n"
        total += r["km"]
    text += f"\nРазом: {total} км"
    await update.message.reply_text(text)


async def show_driver_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Открывает меню водителей и завершает незаконченный диалог."""
    _clear_driver_flow(context)
    await update.message.reply_text(
        "👥 Керування водіями\n\nЩо потрібно зробити?",
        reply_markup=build_driver_menu_keyboard(),
    )


async def _stale_driver_callback(query, context: ContextTypes.DEFAULT_TYPE):
    _clear_driver_flow(context)
    await query.edit_message_text(
        "Це меню застаріло. Відкрий нове та повтори дію.",
        reply_markup=_driver_menu_return_keyboard(),
    )


def _flow_matches(
    flow: Optional[dict], action: str, step: str, token: Optional[str] = None
) -> bool:
    if not flow or flow.get("action") != action or flow.get("step") != step:
        return False
    if token is None:
        return True
    stored_token = flow.get("token")
    return (
        isinstance(stored_token, str)
        and secrets.compare_digest(stored_token, token)
    )


async def on_driver_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline-диалог добавления, архивации, возврата и undo."""
    query = update.callback_query
    if not config.is_user_allowed(query.from_user.id):
        await query.answer("⛔ Доступ заборонено", show_alert=True)
        return

    chat = update.effective_chat
    if chat is not None and getattr(chat, "type", "private") != "private":
        await query.answer("Керування водіями доступне лише в особистих повідомленнях", show_alert=True)
        return

    await query.answer()
    parts = (query.data or "").split("|")
    action = parts[1] if len(parts) > 1 else ""

    try:
        if action == "menu":
            _clear_driver_flow(context)
            await query.edit_message_text(
                "👥 Керування водіями\n\nЩо потрібно зробити?",
                reply_markup=build_driver_menu_keyboard(),
            )
            return

        if action == "cancel":
            _clear_driver_flow(context)
            await query.edit_message_text(
                "Дію скасовано.",
                reply_markup=_driver_menu_return_keyboard(),
            )
            return

        if action == "aliases":
            _clear_driver_flow(context)
            await query.edit_message_text(
                "🔤 Аліаси водіїв\n\n"
                "Аліас — це точний варіант прізвища, який бот замінює на "
                "канонічне українське прізвище.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Додати аліас", callback_data="drv|alias_add")],
                    [InlineKeyboardButton("🗑 Видалити аліас", callback_data="drv|alias_remove")],
                    [InlineKeyboardButton("👥 До водіїв", callback_data="drv|menu")],
                ]),
            )
            return

        if action == "alias_add":
            roster = await asyncio.to_thread(_get_driver_roster)
            if not roster["ok"]:
                await query.edit_message_text(
                    "Таблиця зараз недоступна. Спробуй ще раз пізніше.",
                    reply_markup=_driver_menu_return_keyboard(),
                )
                return
            drivers = sorted({
                str(driver)
                for section in (roster["active"], roster["archived"])
                for names in section.values()
                if isinstance(names, (list, tuple))
                for driver in names
                if str(driver).strip()
            })
            if not drivers:
                await query.edit_message_text(
                    "У таблиці немає водіїв.",
                    reply_markup=_driver_menu_return_keyboard(),
                )
                return
            token = _set_driver_flow(
                context, action="alias_add", step="choose_driver", drivers=drivers
            )
            await query.edit_message_text(
                "➕ Для якого водія додати аліас?",
                reply_markup=_driver_choice_keyboard(drivers, "alias_add_driver", token),
            )
            return

        if action == "alias_add_driver":
            flow = _get_driver_flow(context)
            if (len(parts) != 4
                    or not _flow_matches(flow, "alias_add", "choose_driver", parts[2])):
                await _stale_driver_callback(query, context)
                return
            try:
                driver = flow["drivers"][int(parts[3])]
            except (ValueError, IndexError, KeyError, TypeError):
                await _stale_driver_callback(query, context)
                return
            _set_driver_flow(context, action="alias_add", step="alias", driver=driver)
            await query.edit_message_text(
                f"Канонічне прізвище: {driver}\n\n"
                "Введи один точний варіант прізвища, наприклад російською.",
                reply_markup=_driver_cancel_keyboard(),
            )
            return

        if action == "alias_add_confirm":
            flow = _get_driver_flow(context)
            if (len(parts) != 3
                    or not _flow_matches(flow, "alias_add", "confirm", parts[2])):
                await _stale_driver_callback(query, context)
                return
            result = await asyncio.to_thread(
                sheets_manager.add_driver_alias, flow["driver"], flow["alias"]
            )
            code = getattr(result, "code", "")
            _clear_driver_flow(context)
            if getattr(result, "ok", False) and code == "alias_added":
                await query.edit_message_text(
                    f"✅ Аліас «{result.alias}» додано для {result.driver}.",
                    reply_markup=_driver_menu_return_keyboard(),
                )
            else:
                await query.edit_message_text(
                    f"⚠️ {_driver_result_error(code)}",
                    reply_markup=_driver_menu_return_keyboard(),
                )
            return

        if action == "alias_remove":
            data = await asyncio.to_thread(_get_driver_aliases)
            if not data["ok"]:
                await query.edit_message_text(
                    "Довідник аліасів зараз недоступний.",
                    reply_markup=_driver_menu_return_keyboard(),
                )
                return
            entries = sorted(
                ({"alias": alias, "driver": driver}
                 for alias, driver in data["aliases"].items()),
                key=lambda item: (item["driver"].casefold(), item["alias"]),
            )
            if not entries:
                await query.edit_message_text(
                    "Список аліасів порожній.",
                    reply_markup=_driver_menu_return_keyboard(),
                )
                return
            labels = [f"{item['alias']} → {item['driver']}" for item in entries]
            token = _set_driver_flow(
                context, action="alias_remove", step="choose", entries=entries
            )
            await query.edit_message_text(
                "🗑 Який аліас видалити?",
                reply_markup=_driver_choice_keyboard(labels, "alias_remove_choose", token),
            )
            return

        if action == "alias_remove_choose":
            flow = _get_driver_flow(context)
            if (len(parts) != 4
                    or not _flow_matches(flow, "alias_remove", "choose", parts[2])):
                await _stale_driver_callback(query, context)
                return
            try:
                entry = flow["entries"][int(parts[3])]
            except (ValueError, IndexError, KeyError, TypeError):
                await _stale_driver_callback(query, context)
                return
            token = _set_driver_flow(
                context, action="alias_remove", step="confirm", **entry
            )
            await query.edit_message_text(
                f"Видалити аліас «{entry['alias']}» для {entry['driver']}?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "✅ Видалити", callback_data=f"drv|alias_remove_confirm|{token}"
                    )],
                    [InlineKeyboardButton("❌ Скасувати", callback_data="drv|cancel")],
                ]),
            )
            return

        if action == "alias_remove_confirm":
            flow = _get_driver_flow(context)
            if (len(parts) != 3
                    or not _flow_matches(flow, "alias_remove", "confirm", parts[2])):
                await _stale_driver_callback(query, context)
                return
            result = await asyncio.to_thread(
                sheets_manager.remove_driver_alias, flow["driver"], flow["alias"]
            )
            code = getattr(result, "code", "")
            _clear_driver_flow(context)
            if getattr(result, "ok", False) and code == "alias_removed":
                await query.edit_message_text(
                    f"✅ Аліас «{result.alias}» видалено.",
                    reply_markup=_driver_menu_return_keyboard(),
                )
            else:
                await query.edit_message_text(
                    f"⚠️ {_driver_result_error(code)}",
                    reply_markup=_driver_menu_return_keyboard(),
                )
            return

        if action == "add":
            roster = await asyncio.to_thread(_get_driver_roster)
            if not roster["ok"]:
                _clear_driver_flow(context)
                await query.edit_message_text(
                    "Таблиця зараз недоступна. Спробуй ще раз пізніше.",
                    reply_markup=_driver_menu_return_keyboard(),
                )
                return
            cities = sorted(str(city) for city in roster["active"] if str(city).strip())
            if not cities:
                _clear_driver_flow(context)
                await query.edit_message_text(
                    "У таблиці не знайдено жодного активного міста.",
                    reply_markup=_driver_menu_return_keyboard(),
                )
                return
            token = _set_driver_flow(
                context, action="add", step="choose_city", cities=cities
            )
            await query.edit_message_text(
                "➕ До якого міста додати водія?",
                reply_markup=_driver_choice_keyboard(cities, "add_city", token),
            )
            return

        if action == "add_city":
            flow = _get_driver_flow(context)
            if (len(parts) != 4
                    or not _flow_matches(flow, "add", "choose_city", parts[2])):
                await _stale_driver_callback(query, context)
                return
            try:
                city = flow["cities"][int(parts[3])]
            except (ValueError, IndexError, KeyError, TypeError):
                await _stale_driver_callback(query, context)
                return
            _set_driver_flow(context, action="add", step="driver", city=city)
            await query.edit_message_text(
                f"➕ Місто: {city}\n\n"
                "Введи канонічне прізвище українською, без пробілів і дефісів.",
                reply_markup=_driver_cancel_keyboard(),
            )
            return

        if action == "add_confirm":
            flow = _get_driver_flow(context)
            if (len(parts) != 3
                    or not _flow_matches(flow, "add", "confirm", parts[2])):
                await _stale_driver_callback(query, context)
                return
            result = await asyncio.to_thread(
                sheets_manager.add_mileage_driver,
                flow["city"], flow["driver"], flow["fuel_rate"],
            )
            code = getattr(result, "code", "")
            if getattr(result, "ok", False) and code == "added":
                driver = getattr(result, "driver", None) or flow["driver"]
                city = getattr(result, "city", None) or flow["city"]
                rate = _format_fuel_rate(flow["fuel_rate"])
                _clear_driver_flow(context)
                await query.edit_message_text(
                    f"✅ {driver} додано до міста {city}.\n"
                    f"Норма: {rate} л/100 км.",
                    reply_markup=_driver_menu_return_keyboard(),
                )
                return
            _clear_driver_flow(context)
            await query.edit_message_text(
                f"⚠️ {_driver_result_error(code)}",
                reply_markup=_driver_menu_return_keyboard(),
            )
            return

        if action == "archive":
            roster = await asyncio.to_thread(_get_driver_roster)
            if not roster["ok"]:
                _clear_driver_flow(context)
                await query.edit_message_text(
                    "Таблиця зараз недоступна. Спробуй ще раз пізніше.",
                    reply_markup=_driver_menu_return_keyboard(),
                )
                return
            drivers_by_city = {
                str(city): sorted(str(driver) for driver in drivers if str(driver).strip())
                for city, drivers in roster["active"].items()
                if isinstance(drivers, (list, tuple)) and drivers
            }
            cities = sorted(city for city, drivers in drivers_by_city.items() if drivers)
            if not cities:
                _clear_driver_flow(context)
                await query.edit_message_text(
                    "У таблиці немає чинних водіїв.",
                    reply_markup=_driver_menu_return_keyboard(),
                )
                return
            token = _set_driver_flow(
                context, action="archive", step="choose_city",
                cities=cities, drivers_by_city=drivers_by_city,
            )
            await query.edit_message_text(
                "📦 З якого міста перемістити водія?",
                reply_markup=_driver_choice_keyboard(
                    cities, "archive_city", token
                ),
            )
            return

        if action == "archive_city":
            flow = _get_driver_flow(context)
            if (len(parts) != 4
                    or not _flow_matches(
                        flow, "archive", "choose_city", parts[2]
                    )):
                await _stale_driver_callback(query, context)
                return
            try:
                city = flow["cities"][int(parts[3])]
                drivers = flow["drivers_by_city"][city]
            except (ValueError, IndexError, KeyError, TypeError):
                await _stale_driver_callback(query, context)
                return
            token = _set_driver_flow(
                context, action="archive", step="choose_driver",
                city=city, drivers=drivers,
            )
            await query.edit_message_text(
                f"📦 {city}: кого перемістити до звільнених?",
                reply_markup=_driver_choice_keyboard(
                    drivers, "archive_driver", token
                ),
            )
            return

        if action == "archive_driver":
            flow = _get_driver_flow(context)
            if (len(parts) != 4
                    or not _flow_matches(
                        flow, "archive", "choose_driver", parts[2]
                    )):
                await _stale_driver_callback(query, context)
                return
            try:
                driver = flow["drivers"][int(parts[3])]
            except (ValueError, IndexError, KeyError, TypeError):
                await _stale_driver_callback(query, context)
                return
            token = _set_driver_flow(
                context, action="archive", step="confirm",
                city=flow["city"], driver=driver,
            )
            await query.edit_message_text(
                f"Перемістити {driver} з {flow['city']} до «Звільнених»?\n\n"
                "Історія та формули збережуться.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "✅ Перемістити",
                        callback_data=f"drv|archive_confirm|{token}",
                    )],
                    [InlineKeyboardButton("❌ Скасувати", callback_data="drv|cancel")],
                ]),
            )
            return

        if action == "archive_confirm":
            flow = _get_driver_flow(context)
            if (len(parts) != 3
                    or not _flow_matches(flow, "archive", "confirm", parts[2])):
                await _stale_driver_callback(query, context)
                return
            result = await asyncio.to_thread(
                sheets_manager.archive_mileage_driver,
                flow["city"], flow["driver"],
            )
            code = getattr(result, "code", "")
            if getattr(result, "ok", False) and code == "archived":
                driver = getattr(result, "driver", None) or flow["driver"]
                city = getattr(result, "city", None) or flow["city"]
                token = _remember_driver_undo(context, driver, city)
                _clear_driver_flow(context)
                await query.edit_message_text(
                    f"✅ {driver} переміщено до «Звільнених».\n"
                    f"Початкове місто: {city}.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("↩️ Скасувати", callback_data=f"drv|undo|{token}")],
                        [InlineKeyboardButton("👥 До водіїв", callback_data="drv|menu")],
                    ]),
                )
                return
            _clear_driver_flow(context)
            await query.edit_message_text(
                f"⚠️ {_driver_result_error(code)}",
                reply_markup=_driver_menu_return_keyboard(),
            )
            return

        if action == "restore":
            roster = await asyncio.to_thread(_get_driver_roster)
            if not roster["ok"]:
                _clear_driver_flow(context)
                await query.edit_message_text(
                    "Таблиця зараз недоступна. Спробуй ще раз пізніше.",
                    reply_markup=_driver_menu_return_keyboard(),
                )
                return
            entries = [
                {"original_city": str(city), "driver": str(driver)}
                for city, drivers in roster["archived"].items()
                if isinstance(drivers, (list, tuple))
                for driver in drivers
                if str(driver).strip()
            ]
            entries.sort(key=lambda item: (item["original_city"], item["driver"]))
            if not entries:
                _clear_driver_flow(context)
                await query.edit_message_text(
                    "Список звільнених водіїв порожній.",
                    reply_markup=_driver_menu_return_keyboard(),
                )
                return
            labels = [f"{item['driver']} — {item['original_city']}" for item in entries]
            active_cities = sorted(str(city) for city in roster["active"] if str(city).strip())
            token = _set_driver_flow(
                context, action="restore", step="choose_driver",
                entries=entries, active_cities=active_cities,
            )
            await query.edit_message_text(
                "♻️ Кого повернути до чинних?",
                reply_markup=_driver_choice_keyboard(
                    labels, "restore_driver", token
                ),
            )
            return

        if action == "restore_driver":
            flow = _get_driver_flow(context)
            if (len(parts) != 4
                    or not _flow_matches(
                        flow, "restore", "choose_driver", parts[2]
                    )):
                await _stale_driver_callback(query, context)
                return
            try:
                entry = flow["entries"][int(parts[3])]
            except (ValueError, IndexError, KeyError, TypeError):
                await _stale_driver_callback(query, context)
                return
            original_city = entry["original_city"]
            targets = [original_city]
            targets.extend(city for city in flow["active_cities"] if city != original_city)
            token = _set_driver_flow(
                context, action="restore", step="choose_city",
                driver=entry["driver"], original_city=original_city, targets=targets,
            )
            labels = [
                f"{city} (початкове)" if city == original_city else city
                for city in targets
            ]
            await query.edit_message_text(
                f"Куди повернути {entry['driver']}?",
                reply_markup=_driver_choice_keyboard(
                    labels, "restore_city", token
                ),
            )
            return

        if action == "restore_city":
            flow = _get_driver_flow(context)
            if (len(parts) != 4
                    or not _flow_matches(
                        flow, "restore", "choose_city", parts[2]
                    )):
                await _stale_driver_callback(query, context)
                return
            try:
                target_city = flow["targets"][int(parts[3])]
            except (ValueError, IndexError, KeyError, TypeError):
                await _stale_driver_callback(query, context)
                return
            token = _set_driver_flow(
                context, action="restore", step="confirm",
                driver=flow["driver"], original_city=flow["original_city"],
                target_city=target_city,
            )
            await query.edit_message_text(
                f"Повернути {flow['driver']} до чинних у місті {target_city}?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "✅ Повернути",
                        callback_data=f"drv|restore_confirm|{token}",
                    )],
                    [InlineKeyboardButton("❌ Скасувати", callback_data="drv|cancel")],
                ]),
            )
            return

        if action == "restore_confirm":
            flow = _get_driver_flow(context)
            if (len(parts) != 3
                    or not _flow_matches(flow, "restore", "confirm", parts[2])):
                await _stale_driver_callback(query, context)
                return
            result = await asyncio.to_thread(
                sheets_manager.restore_mileage_driver,
                flow["driver"], flow["target_city"],
            )
            code = getattr(result, "code", "")
            if (getattr(result, "ok", False) and code == "restored") or code == "already_active":
                driver = getattr(result, "driver", None) or flow["driver"]
                city = getattr(result, "city", None) or flow["target_city"]
                _clear_driver_flow(context)
                text = (
                    f"✅ {driver} повернуто до чинних. Місто: {city}."
                    if code == "restored"
                    else f"ℹ️ {driver} уже серед чинних. Місто: {city}."
                )
                await query.edit_message_text(
                    text,
                    reply_markup=_driver_menu_return_keyboard(),
                )
                return
            _clear_driver_flow(context)
            await query.edit_message_text(
                f"⚠️ {_driver_result_error(code)}",
                reply_markup=_driver_menu_return_keyboard(),
            )
            return

        if action == "undo":
            token = parts[2] if len(parts) == 3 else ""
            undo_actions = context.user_data.get(DRIVER_UNDO_KEY, {})
            undo = undo_actions.get(token) if isinstance(undo_actions, dict) else None
            if not isinstance(undo, dict):
                await query.edit_message_text(
                    "Ця кнопка скасування застаріла або вже недійсна.",
                    reply_markup=_driver_menu_return_keyboard(),
                )
                return
            result = await asyncio.to_thread(
                sheets_manager.restore_mileage_driver,
                undo["driver"], undo["city"],
            )
            code = getattr(result, "code", "")
            if (getattr(result, "ok", False) and code == "restored") or code == "already_active":
                driver = getattr(result, "driver", None) or undo["driver"]
                city = getattr(result, "city", None) or undo["city"]
                prefix = "↩️" if code == "restored" else "ℹ️"
                text = (
                    f"{prefix} Переміщення скасовано: {driver} повернуто до {city}."
                    if code == "restored"
                    else f"{prefix} {driver} уже серед чинних. Повторне скасування не потрібне."
                )
                await query.edit_message_text(text, reply_markup=_driver_menu_return_keyboard())
                return
            await query.edit_message_text(
                f"⚠️ {_driver_result_error(code)}",
                reply_markup=_driver_menu_return_keyboard(),
            )
            return

        await _stale_driver_callback(query, context)
    except Exception as exc:
        logger.exception("Ошибка в меню управления водителями: %s", exc)
        _clear_driver_flow(context)
        await query.edit_message_text(
            "⚠️ Не вдалося завершити дію в інтерфейсі. "
            "Відкрий меню та перевір актуальний список.",
            reply_markup=_driver_menu_return_keyboard(),
        )


async def continue_driver_text_flow(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Продолжает текстовые шаги добавления; True — текст уже обработан."""
    flow = _get_driver_flow(context)
    if not flow:
        return False

    if _flow_matches(flow, "add", "driver"):
        raw_driver = (update.message.text or "").strip()
        if not DRIVER_CANONICAL_NAME_RE.fullmatch(raw_driver):
            await update.message.reply_text(
                "⚠️ Потрібне одне канонічне прізвище українською, без "
                "пробілів і дефісів. Спробуй ще раз.",
                reply_markup=_driver_cancel_keyboard(),
            )
            return True
        driver = raw_driver[0].upper() + raw_driver[1:].lower()
        _set_driver_flow(
            context, action="add", step="fuel_rate",
            city=flow["city"], driver=driver,
        )
        await update.message.reply_text(
            f"Водій: {driver}.\n\n"
            "Введи норму витрати в л/100 км — число від 0 до 100. "
            "Можна з комою, наприклад 12,5.",
            reply_markup=_driver_cancel_keyboard(),
        )
        return True

    if _flow_matches(flow, "add", "fuel_rate"):
        raw_rate = (update.message.text or "").strip()
        if not re.fullmatch(r"\d+(?:[.,]\d+)?", raw_rate):
            rate = None
        else:
            try:
                rate = float(raw_rate.replace(",", "."))
            except ValueError:
                rate = None
        if rate is None or not 0 < rate <= 100:
            await update.message.reply_text(
                "⚠️ Введи число більше за 0 і не більше за 100, наприклад 12,5.",
                reply_markup=_driver_cancel_keyboard(),
            )
            return True
        token = _set_driver_flow(
            context, action="add", step="confirm",
            city=flow["city"], driver=flow["driver"], fuel_rate=rate,
        )
        await update.message.reply_text(
            "Перевір дані:\n\n"
            f"• Місто: {flow['city']}\n"
            f"• Водій: {flow['driver']}\n"
            f"• Норма: {_format_fuel_rate(rate)} л/100 км",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "✅ Додати",
                    callback_data=f"drv|add_confirm|{token}",
                )],
                [InlineKeyboardButton("❌ Скасувати", callback_data="drv|cancel")],
            ]),
        )
        return True

    if _flow_matches(flow, "alias_add", "alias"):
        raw_alias = (update.message.text or "").strip()
        if not DRIVER_ALIAS_RE.fullmatch(raw_alias):
            await update.message.reply_text(
                "⚠️ Потрібен один точний варіант прізвища кирилицею, без "
                "пробілів і дефісів.",
                reply_markup=_driver_cancel_keyboard(),
            )
            return True
        alias = raw_alias[0].upper() + raw_alias[1:].lower()
        token = _set_driver_flow(
            context, action="alias_add", step="confirm",
            driver=flow["driver"], alias=alias,
        )
        await update.message.reply_text(
            "Перевір відповідність:\n\n"
            f"• Аліас: {alias}\n"
            f"• Канонічне прізвище: {flow['driver']}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "✅ Додати аліас",
                    callback_data=f"drv|alias_add_confirm|{token}",
                )],
                [InlineKeyboardButton("❌ Скасувати", callback_data="drv|cancel")],
            ]),
        )
        return True

    await update.message.reply_text(
        "Продовж вибір за допомогою кнопок вище або скасуй дію.",
        reply_markup=_driver_cancel_keyboard(),
    )
    return True


async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на reply-кнопки в личке."""
    if not check_access(update):
        await access_denied(update)
        return
    text = update.message.text

    # Любая другая reply-кнопка — явный выход из незаконченного
    # кадрового диалога, чтобы следующий текст не стал фамилией/расходом.
    if text not in {"👥 Водії", "👥 Водители"}:
        _clear_driver_flow(context)

    action_by_label = {
        "📊 Статистика сьогодні": "today",
        "📈 За тиждень": "week",
        "🚗 Активні маршрути": "active",
        "📊 Статистика сегодня": "today",
        "📈 За неделю": "week",
        "🚗 Активные маршруты": "active",
    }
    if text in action_by_label:
        action = action_by_label[text]
        cities = sheets_manager.list_city_sheets()
        if not cities:
            await update.message.reply_text("Поки немає жодного міста.")
            return
        await update.message.reply_text(
            f"{ACTION_LABELS[action]} — вибери місто:",
            reply_markup=build_city_keyboard(action, page=0),
        )
    elif text in {"❓ Допомога", "❓ Помощь"}:
        await help_command(update, context)
    elif text in {"📏 Кілометраж за тиждень", "📏 Километраж за неделю"}:
        await mileage_week(update, context)
    elif text in {"🧮 Заповнити формули", "🧮 Заполнить формулы"}:
        await backfill_command(update, context)
    elif text in {"👥 Водії", "👥 Водители"}:
        await show_driver_menu(update, context)


async def on_city_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает inline-кнопки выбора города и пагинации."""
    query = update.callback_query
    await query.answer()
    if not config.is_user_allowed(query.from_user.id):
        return

    parts = query.data.split("|")
    kind = parts[0]

    if kind == "noop":
        return

    if kind == "page":
        action, page = parts[1], int(parts[2])
        await query.edit_message_reply_markup(
            reply_markup=build_city_keyboard(action, page)
        )
        return

    if kind == "city":
        action, idx = parts[1], int(parts[2])
        cities = sorted(sheets_manager.list_city_sheets())
        if idx < 0 or idx >= len(cities):
            await query.edit_message_text(
                "Список міст змінився — відкрий меню знову."
            )
            return
        text = render_city_data(action, cities[idx])
        await query.edit_message_text(text)


def render_city_data(action: str, city: str) -> str:
    """Текст ответа для действия action по городу city."""
    if action == "today":
        stats = sheets_manager.get_today_stats(city)
        if not stats or stats.get("total_events", 0) == 0:
            return f"📊 {city}: сьогодні подій поки немає."
        return f"🏙 {city}\n" + format_stats(stats, "сьогодні")
    if action == "week":
        stats = sheets_manager.get_stats_for_period(city, 7)
        if not stats or stats.get("total_events", 0) == 0:
            return f"📊 {city}: за останні 7 днів подій немає."
        return f"🏙 {city}\n" + format_stats(stats, "тиждень")
    if action == "active":
        routes = sheets_manager.get_active_routes(city)
        if not routes:
            return f"🚗 {city}: активних маршрутів немає."
        text = f"🚗 {city} — активні маршрути:\n\n"
        for r in routes:
            driver = f" ({r['driver']})" if r.get("driver") else ""
            status = (r.get("status") or "").replace("_", " ")
            text += f"• Маршрут {r.get('route') or '?'}{driver} — {status} о {r.get('time') or ''}\n"
        return text
    return "Невідома дія."


async def handle_private_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик произвольного текста в личных сообщениях."""
    if not check_access(update):
        await access_denied(update)
        return
    if await continue_driver_text_flow(update, context):
        return
    await update.message.reply_text(
        "Використовуй кнопки нижче для роботи зі статистикою 👇",
        reply_markup=MAIN_KEYBOARD
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик всех текстовых сообщений в группе (включая отредактированные)."""
    msg = update.message or update.edited_message
    if not msg:
        return

    is_edited = update.edited_message is not None

    # Получаем текст из message.text или message.caption (для фото/документов)
    text = msg.text or msg.caption
    if not text:
        return

    # DEBUG: логируем все сообщения из групп
    is_caption = msg.caption is not None
    edit_tag = "(edited)" if is_edited else ""
    caption_tag = "(caption)" if is_caption else ""
    logger.info(f"[DEBUG] Получено{edit_tag}{caption_tag}: '{text[:100]}' от {update.effective_user.first_name if update.effective_user else 'unknown'}")

    # Игнорируем кнопки в группах
    if text in BUTTON_LABELS:
        return

    known_drivers = sheets_manager.get_mileage_drivers()
    alias_data = (
        sheets_manager.get_driver_aliases()
        if "км" in text.casefold()
        else {"ok": True, "aliases": {}}
    )
    driver_aliases = (
        alias_data.get("aliases", {})
        if isinstance(alias_data, dict) and alias_data.get("ok")
        else None
    )
    events = parser.parse(text, known_drivers, driver_aliases)

    # DEBUG: логируем результат парсинга
    if events:
        logger.info(f"[DEBUG] Распознано {len(events)} событий: {[e.event_type for e in events]}")
    else:
        logger.debug(f"[DEBUG] Не распознано: '{text[:50]}'")

    if not events:
        return  # Сообщение не содержит логистических событий

    # Дедупликация: фильтруем события, которые уже были недавно
    chat_id = update.effective_chat.id if update.effective_chat else 0
    now = time_module.time()

    # Чистим старые записи (старше 5 минут)
    cutoff = now - 300
    stale_keys = [k for k, t in _recent_events.items() if t < cutoff]
    for k in stale_keys:
        del _recent_events[k]

    unique_events = []
    event_keys = []  # параллельный список ключей дедупа для rollback при ошибке
    for event in events:
        key = (chat_id, event.event_type, event.route_number, event.driver)
        if key in _recent_events and (now - _recent_events[key]) < DEDUP_WINDOW_SEC:
            logger.info(f"[DEDUP] Пропущен дубликат: {event.event_type} маршрут={event.route_number} водитель={event.driver}")
            continue
        _recent_events[key] = now
        unique_events.append(event)
        event_keys.append(key)

    events = unique_events
    if not events:
        return  # Все события — дубликаты

    # Город = имя листа (из названия чата)
    group_name = update.effective_chat.title or "" if update.effective_chat else ""
    city = city_of(update)
    full_stats = config.is_full_stats_chat(chat_id)

    # Сохраняем события по одному — чтобы откатить дедуп для тех,
    # которые не записались (тогда следующая копия сообщения сможет повторить запись)
    saved = 0
    tz = pytz.timezone(config.TIMEZONE)
    for event, key in zip(events, event_keys):
        if event.event_type == parser.EVENT_MILEAGE:
            ok = sheets_manager.upsert_mileage(
                event.driver, event.mileage_km, datetime.now(tz)
            )
        elif full_stats:
            ok = sheets_manager.add_event(event, city, group_name)
        else:
            continue  # mileage-only чат — события маршрутов не пишем

        if ok:
            saved += 1
        else:
            # Запись не удалась — убираем из дедупа, чтобы retry-копия прошла
            _recent_events.pop(key, None)
            logger.warning(f"[DEDUP] Откат кэша после ошибки записи: {event.event_type} маршрут={event.route_number} водитель={event.driver}")

    if saved > 0:
        logger.info(f"Сохранено {saved} событий из группы '{group_name}'")
        # Ставим реакцию 🏆 как подтверждение записи
        await set_saved_reaction(msg)

        if not full_stats:
            return  # mileage-only чат — статистику маршрутов не ведём

        # Проверка цепочки событий и предупреждения
        tz = pytz.timezone(config.TIMEZONE)
        today_str = datetime.now(tz).strftime("%d.%m.%Y")
        for event in events:
            try:
                # Предупреждение: выезд без номера маршрута
                if event.event_type == "выезд" and not event.route_number and event.driver:
                    warn_text = (
                        f"⚠️ {event.driver} — виїзд зафіксовано, "
                        f"але номер маршруту не вказано. Вкажіть номер маршруту."
                    )
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=warn_text
                    )
                    logger.info(f"[WARN] Выезд без номера маршрута: {event.driver}")

                # Проверка несоответствия маршрутов: водитель закрыл не тот маршрут
                route_mismatch = False
                if event.event_type == "маршрут_завершён" and event.driver and event.route_number:
                    departed_route = sheets_manager.get_driver_departure_route(city, event.driver, today_str)
                    if departed_route and departed_route != event.route_number:
                        route_mismatch = True
                        warn_text = (
                            f"⚠️ Увага: {event.driver} виїхав на маршрут {departed_route}, "
                            f"але закрив маршрут {event.route_number}. Перевірте номер маршруту."
                        )
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=warn_text
                        )
                        logger.info(f"[WARN] Несоответствие маршрутов: {event.driver} выехал {departed_route}, закрыл {event.route_number}")

                # Проверка цепочки: начало_сборки → сборка_завершена → выезд → маршрут_завершён
                # Пропускаем если уже сработало несоответствие маршрутов (более информативно)
                if event.route_number and not route_mismatch:
                    missing = sheets_manager.check_chain_violation(
                        city, event.event_type, event.route_number, today_str
                    )
                    if missing:
                        driver_info = f" ({event.driver})" if event.driver else ""
                        warn_text = (
                            f"⚠️ Маршрут {event.route_number}{driver_info}: "
                            f"зафіксовано «{event.event_type}», але не було: {missing}"
                        )
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=warn_text
                        )
                        logger.info(f"[WARN] Нарушение цепочки: маршрут {event.route_number} — {event.event_type}, не хватает: {missing}")
            except Exception as e:
                logger.warning(f"Ошибка проверки цепочки событий: {e}")

        # Закрыли последний маршрут города — уведомляем этот же чат
        has_route_completed = any(
            e.event_type == "маршрут_завершён" for e in events
        )
        if has_route_completed and update.effective_chat:
            await asyncio.sleep(1)  # дать Google API синхронизироваться
            remaining = sheets_manager.get_active_routes(city)
            logger.info(f"[DEBUG] '{city}': активных маршрутов = {len(remaining)}")
            if not remaining:
                try:
                    now = datetime.now(tz)
                    if now.hour < 19:
                        text = ("✅ Всі маршрути завершені\n\n"
                                "🎉 Сьогодні усі колеги-водії завершили "
                                "до 19:00. Дякуємо! 👏🚚")
                    else:
                        text = "✅ Всі маршрути завершені"
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id, text=text
                    )
                    logger.info(f"Уведомление о завершении: '{city}' (hour={now.hour})")
                except Exception as e:
                    logger.warning(f"Не удалось отправить уведомление: {e}")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок."""
    logger.error(f"Update {update} caused error: {context.error}")


def main():
    """Запуск бота."""
    # Проверяем конфигурацию
    if not config.validate():
        return

    # Подключаемся к Google Sheets
    if not sheets_manager.connect():
        logger.warning("Google Sheets не подключен, данные не будут сохраняться")

    # Создаём приложение с увеличенным таймаутом
    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .build()
    )

    # Регистрируем обработчик /start
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("backfill", backfill_command))
    # Специализированный drv-обработчик должен стоять перед общим city/page.
    app.add_handler(CallbackQueryHandler(on_driver_callback, pattern=r"^drv\|"))
    app.add_handler(CallbackQueryHandler(on_city_callback))

    # Обработчик кнопок в личных сообщениях
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE &
        filters.Regex(
            r"^(📊 Статистика сьогодні|📈 За тиждень|🚗 Активні маршрути|"
            r"❓ Допомога|📏 Кілометраж за тиждень|🧮 Заповнити формули|👥 Водії|"
            r"📊 Статистика сегодня|📈 За неделю|🚗 Активные маршруты|"
            r"❓ Помощь|📏 Километраж за неделю|🧮 Заполнить формулы|👥 Водители)$"
        ),
        handle_buttons
    ))

    # Обработчик произвольного текста в личных сообщениях
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_private_text
    ))

    # Обработчик всех текстовых сообщений в группах (включая подписи к фото/документам)
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.CAPTION) & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
        handle_message
    ))

    # Обработчик отредактированных сообщений в группах
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.CAPTION) & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP)
        & filters.UpdateType.EDITED_MESSAGE,
        handle_message
    ))

    # Обработчик ошибок
    app.add_error_handler(error_handler)

    # Запускаем планировщик автоотчётов (с поддержкой timezone)
    setup_scheduler(app)

    # Запуск
    logger.info("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
