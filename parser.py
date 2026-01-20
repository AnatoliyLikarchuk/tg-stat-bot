"""
Парсер сообщений логистики.
Извлекает события: начало сборки, завершение сборки, выезд, завершение маршрута, проблемы.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List


@dataclass
class ParsedEvent:
    """Распознанное событие из сообщения."""
    event_type: str          # Тип события
    time: Optional[str]      # Время события (HH:MM)
    route_number: Optional[str]  # Номер маршрута
    driver: Optional[str]    # Имя водителя
    raw_text: str            # Исходный текст


class MessageParser:
    """Парсер сообщений из Telegram-группы логистики."""

    # Словарь синонимов имён водителей (варианты -> каноническое имя)
    # Добавляй сюда новые варианты по мере появления
    DRIVER_ALIASES = {
        "роговский": "Роговський",
        "роговській": "Роговський",
        "роговський": "Роговський",
        "бельченко": "Бєльченко",
        "бєльченко": "Бєльченко",
        "качаенко": "Качаєнко",
        "качаєнко": "Качаєнко",
        # Добавляй новых водителей здесь:
        # "иванов": "Іванов",
    }

    # Транслитерация рус → укр (для автоматической нормализации)
    RU_TO_UA = {
        "и": "і",
        "ы": "и",
        "э": "е",
        "ъ": "",
        "ё": "ьо",
    }

    # Типичные окончания фамилий рус → укр
    ENDINGS_RU_TO_UA = [
        ("ский", "ський"),
        ("ская", "ська"),
        ("цкий", "цький"),
        ("цкая", "цька"),
        ("ий", "ій"),
        ("ая", "а"),
        ("ое", "е"),
        ("ев", "єв"),
        ("ёв", "йов"),
    ]

    # Типы событий
    EVENT_ASSEMBLY_START = "начало_сборки"
    EVENT_ASSEMBLY_DONE = "сборка_завершена"
    EVENT_DEPARTURE = "выезд"
    EVENT_ROUTE_COMPLETE = "маршрут_завершён"
    EVENT_ALL_DEPARTED = "все_выехали"
    EVENT_PROBLEM = "проблема"
    EVENT_LATE_DELIVERY = "поздняя_доставка"

    # Regex паттерны
    PATTERNS = {
        # Время в формате HH.MM или HH:MM
        "time": r"(\d{1,2})[.:](\d{2})",

        # Начало сборки (рус/укр/микс)
        # "начата сборка", "початок зборки", "начало зборки", "початок сборки"
        "assembly_start": r"начат[аоы]?\s+сборк[аи]|початок\s+зборки|начало\s+(?:сборки|зборки)|початок\s+сборки",

        # Сборка завершена
        # "собран маршрут", "сборка завершена"
        "assembly_done": r"собран[аоы]?\s+(?:мар[шщ]рут)?|сборка\s+завершена",

        # Выезд (рус/укр)
        # "выехал", "виїхав"
        "departure": r"(?:выехал[аио]?|виїхав|виїхала|виїхали)(?:\s+мар[шщ]рут)?",
        "departure_alt": r"([А-ЯІЇЄҐа-яіїєґ]+)\s+(?:выехал|виїхав)",  # Горбатко выехал/виїхав

        # Маршрут завершён (рус/укр)
        # Поддерживает: "маршрут 1 завершен", "закончил", "завершив", "закрыт" и т.д.
        "route_complete": r"мар[шщ]рут\s+\d*\s*(?:заверш|закінч|закри)|(?:все\s+)?развез|(?:всё\s+)?развёз|мар[шщ]рут\s+закінчи|закінчив|закончил|завершив|завершен[оа]?|завершён|мар[шщ]рут\s*\d*\s*закрит[оа]?|закрыт",

        # Все маршруты выехали
        "all_departed": r"все\s+мар[шщ]рут[ыи]\s+выехал",

        # Проблемы
        "problem": r"не\s+доставив|не\s+вказано|завтра\s+(?:с\s+)?утра|час\s+прийому\s+не",

        # Поздняя доставка: "5 точек после 19"
        "late_delivery": r"(\d+)\s+точ[е|о]к\s+после\s+(\d+)",

        # Номер маршрута (с учётом опечаток: марщрут, маршру)
        "route": r"мар[шщ]рут?\s*(\d+)",

        # Имя водителя (после номера маршрута)
        "driver": r"мар[шщ]рут?\s*\d+\s+([А-ЯІЇЄҐа-яіїєґA-Za-z]+)",
    }

    def __init__(self):
        # Компилируем регулярки
        self.compiled = {
            key: re.compile(pattern, re.IGNORECASE | re.UNICODE)
            for key, pattern in self.PATTERNS.items()
        }

    def normalize_driver_name(self, name: str) -> str:
        """
        Нормализует имя водителя к единому формату (украинский).
        1. Проверяет словарь синонимов
        2. Применяет автотранслитерацию рус → укр
        """
        if not name:
            return name

        name_lower = name.lower()

        # 1. Проверяем словарь синонимов
        if name_lower in self.DRIVER_ALIASES:
            return self.DRIVER_ALIASES[name_lower]

        # 2. Автотранслитерация рус → укр
        result = name

        # Сначала меняем окончания (более специфичные правила)
        result_lower = result.lower()
        for ru_ending, ua_ending in self.ENDINGS_RU_TO_UA:
            if result_lower.endswith(ru_ending):
                # Сохраняем регистр первой буквы
                base = result[:-len(ru_ending)]
                result = base + ua_ending
                break

        # Затем посимвольная замена
        for ru_char, ua_char in self.RU_TO_UA.items():
            # Заменяем с учётом регистра
            result = result.replace(ru_char, ua_char)
            result = result.replace(ru_char.upper(), ua_char.upper() if ua_char else "")

        # Первая буква — заглавная
        if result:
            result = result[0].upper() + result[1:]

        return result

    def parse(self, text: str) -> List[ParsedEvent]:
        """
        Парсит сообщение и возвращает список событий.
        Одно сообщение может содержать несколько событий.
        """
        events = []
        text_lower = text.lower()

        # Извлекаем время
        time_match = self.compiled["time"].search(text)
        time_str = None
        if time_match:
            hours, minutes = time_match.groups()
            time_str = f"{int(hours):02d}:{minutes}"

        # Проверяем типы событий
        if self.compiled["all_departed"].search(text_lower):
            events.append(ParsedEvent(
                event_type=self.EVENT_ALL_DEPARTED,
                time=time_str,
                route_number=None,
                driver=None,
                raw_text=text
            ))
            return events

        if self.compiled["assembly_start"].search(text_lower):
            # Извлекаем все маршруты и водителей
            routes_drivers = self._extract_routes_drivers(text)
            if routes_drivers:
                for route, driver in routes_drivers:
                    events.append(ParsedEvent(
                        event_type=self.EVENT_ASSEMBLY_START,
                        time=time_str,
                        route_number=route,
                        driver=driver,
                        raw_text=text
                    ))
            else:
                events.append(ParsedEvent(
                    event_type=self.EVENT_ASSEMBLY_START,
                    time=time_str,
                    route_number=None,
                    driver=None,
                    raw_text=text
                ))

        if self.compiled["assembly_done"].search(text_lower):
            routes_drivers = self._extract_routes_drivers(text)
            if routes_drivers:
                for route, driver in routes_drivers:
                    events.append(ParsedEvent(
                        event_type=self.EVENT_ASSEMBLY_DONE,
                        time=time_str,
                        route_number=route,
                        driver=driver,
                        raw_text=text
                    ))

        if self.compiled["departure"].search(text_lower):
            routes_drivers = self._extract_routes_drivers(text)
            if routes_drivers:
                for route, driver in routes_drivers:
                    events.append(ParsedEvent(
                        event_type=self.EVENT_DEPARTURE,
                        time=time_str,
                        route_number=route,
                        driver=driver,
                        raw_text=text
                    ))
            else:
                # Альтернативный формат: "Водитель выехал" (без номера маршрута)
                alt_match = self.compiled["departure_alt"].search(text)
                if alt_match:
                    driver_name = alt_match.group(1)
                    events.append(ParsedEvent(
                        event_type=self.EVENT_DEPARTURE,
                        time=time_str,
                        route_number=None,
                        driver=driver_name,
                        raw_text=text
                    ))

        if self.compiled["route_complete"].search(text_lower):
            routes_drivers = self._extract_routes_drivers(text)
            if routes_drivers:
                for route, driver in routes_drivers:
                    events.append(ParsedEvent(
                        event_type=self.EVENT_ROUTE_COMPLETE,
                        time=time_str,
                        route_number=route,
                        driver=driver,
                        raw_text=text
                    ))
            # Без номера маршрута — не записываем (чтобы не ломать логику активных маршрутов)

        if self.compiled["problem"].search(text_lower):
            events.append(ParsedEvent(
                event_type=self.EVENT_PROBLEM,
                time=time_str,
                route_number=None,
                driver=None,
                raw_text=text
            ))

        # Поздняя доставка: "5 точек после 19"
        late_match = self.compiled["late_delivery"].search(text_lower)
        if late_match:
            routes_drivers = self._extract_routes_drivers(text)
            route_num = routes_drivers[0][0] if routes_drivers else None
            driver_name = routes_drivers[0][1] if routes_drivers else None
            events.append(ParsedEvent(
                event_type=self.EVENT_LATE_DELIVERY,
                time=time_str,
                route_number=route_num,
                driver=driver_name,
                raw_text=text
            ))

        return events

    # Служебные слова, которые НЕ являются именами водителей
    SKIP_WORDS = {
        # Союзы и предлоги
        "и", "та", "в", "на", "з", "с",
        # Служебные слова
        "ходка", "ходки", "общего", "сборочного",
        # Слова сборки (рус/укр)
        "початок", "начало", "сборка", "зборки", "сборки", "зборка",
        "собран", "собрана", "собрано",
        # Глаголы завершения (рус)
        "закончил", "закончила", "закончили", "закончив",
        "завершил", "завершила", "завершили", "завершив",
        "завершен", "завершена", "завершено", "завершён",
        "закрыл", "закрыла", "закрыли",
        "развез", "развезла", "развезли", "развёз",
        # Глаголы завершения (укр)
        "закінчив", "закінчила", "закінчили",
        "завершив", "завершила", "завершили",
        "закрив", "закрила", "закрили",
        "закрито",
        # Глаголы завершения (рус)
        "закрыт", "закрыта", "закрыто",
        # Глаголы выезда
        "выехал", "выехала", "выехали",
        "виїхав", "виїхала", "виїхали",
        # Прочее
        "тест", "тестов", "точка", "точку", "точек",
    }

    def _extract_routes_drivers(self, text: str) -> List[tuple]:
        """
        Извлекает пары (номер_маршрута, водитель) из текста.
        Например: "маршрут 1 Кияниця" -> [("1", "Кияниця")]
        Также: "Бельченко маршрут 50 закончил" -> [("50", "Бельченко")]
        """
        results = []

        # Паттерн 1: маршрут N ИмяВодителя (стандартный, с учётом опечаток: марщрут, маршру)
        # Добавлено ёЁ в класс символов (не входит в диапазон а-я в Unicode)
        pattern1 = r"мар[шщ]рут?\s*(\d+)\s*([А-ЯІЇЄҐЁа-яіїєґё]+)?"
        matches1 = re.findall(pattern1, text, re.IGNORECASE | re.UNICODE)

        # Паттерн 2: ИмяВодителя маршрут N (водитель перед маршрутом, включая перенос строки)
        pattern2 = r"([А-ЯІЇЄҐЁ][а-яіїєґё']+)\s+мар[шщ]рут?\s*(\d+)"
        matches2 = re.findall(pattern2, text, re.IGNORECASE | re.UNICODE)
        drivers_before = {route_num: driver_name for driver_name, route_num in matches2
                         if driver_name.lower() not in self.SKIP_WORDS}

        # Паттерн 3: ИмяВодителя N маршрут (номер между именем и словом "маршрут")
        # Пример: "Довгаль 6 маршрут закрив"
        pattern3 = r"([А-ЯІЇЄҐЁ][а-яіїєґё']+)\s+(\d+)\s+мар[шщ]рут"
        matches3 = re.findall(pattern3, text, re.IGNORECASE | re.UNICODE)
        for driver_name, route_num in matches3:
            if driver_name.lower() not in self.SKIP_WORDS and route_num not in drivers_before:
                drivers_before[route_num] = driver_name

        for route_num, driver_after in matches1:
            driver_name = None

            # Сначала проверяем слово после маршрута
            if driver_after and driver_after.lower() not in self.SKIP_WORDS:
                driver_name = driver_after
            # Если слово после — служебное, берём водителя перед маршрутом
            elif route_num in drivers_before:
                driver_name = drivers_before[route_num]

            # Нормализуем имя водителя (рус → укр)
            if driver_name:
                driver_name = self.normalize_driver_name(driver_name)

            if driver_name or route_num:
                results.append((route_num, driver_name))

        # Если ничего не нашли через паттерн 1, пробуем только паттерн 2
        if not results and drivers_before:
            for route_num, driver_name in drivers_before.items():
                # Нормализуем имя водителя (рус → укр)
                driver_name = self.normalize_driver_name(driver_name)
                results.append((route_num, driver_name))

        return results


# Для тестирования
if __name__ == "__main__":
    parser = MessageParser()

    test_messages = [
        # Старые тесты
        "6.00 начата сборка общего сборочного листа",
        "7.34 начата сборка маршрут 1 Кияниця 3 ходка, маршрут 2 Косич 3 ходка и маршрут 3 Сергеев",
        "7.40 собран маршрут 1 Кияниця 3 ходка и маршрут 2 Косич 3 ходка",
        "8.30 выехал маршрут 2 Косич 3 ходка",
        "Все маршруты выехали",
        "Маршрут завершив",
        "Точку не доставив",
        "Завтра с утра завезу",
        # Новые тесты (2026-01-15)
        "Бєльченко маршрут 66 початок зборки",
        "Бєльченко маршрут 66начало сборки",
        "Бєльченко маршрут 66 начало зборки",
        "маршрут 66 Бєльченко сборка завершена",
        "12:55 выехал маршрут 66 Бєльченко",
        "12:55 виїхав маршрут 66 Бєльченко",
        "маршрут 66 Бельченко 5 точек после 19",
        "Бєльченко маршрут 66 закончил",
        # Опечатка марщрут
        "Роговський марщрут 8 закінчив",
        # Нормализация имён (рус → укр)
        "10.15 собран маршрут 8 Роговский",
        # Закрито (укр)
        "Качаєнко\nМаршрут 9 закрито.",
        # Формат "Имя N маршрут глагол" (номер перед словом маршрут)
        "Довгаль 6 маршрут закрив",
    ]

    for msg in test_messages:
        print(f"\n--- {msg} ---")
        events = parser.parse(msg)
        for e in events:
            print(f"  {e.event_type}: маршрут={e.route_number}, водитель={e.driver}, время={e.time}")
