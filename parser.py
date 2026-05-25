"""
Парсер сообщений логистики.
Извлекает события: начало сборки, завершение сборки, выезд, завершение маршрута, проблемы.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Collection


@dataclass
class ParsedEvent:
    """Распознанное событие из сообщения."""
    event_type: str          # Тип события
    time: Optional[str]      # Время события (HH:MM)
    route_number: Optional[str]  # Номер маршрута
    driver: Optional[str]    # Имя водителя
    raw_text: str            # Исходный текст
    mileage_km: Optional[int] = None  # Пробег в км (только для EVENT_MILEAGE)


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
        "горобец": "Горобець",
        # Украинские фамилии без уникальных букв (і/ї/є/ґ) —
        # защита от автотранслитерации и→і
        "кияниця": "Кияниця",
        "косич": "Косич",
        "косіч": "Косич",
        "черник": "Черник",
        "чернік": "Черник",
        "абрамович": "Абрамович",
        "абрамовіч": "Абрамович",
        "клименко": "Клименко",
        "кліменко": "Клименко",
        "черних": "Черних",      # Николаев
        "черніх": "Черних",
        "митченко": "Митченко",  # Ужгород
        "мітченко": "Митченко",
        "мельников": "Мельников",  # Днепр
        "мельніков": "Мельников",
        "вовчановский": "Вовчановський",  # Черкассы
        "вовчановський": "Вовчановський",
        "вовчановській": "Вовчановський",
        "кривий": "Кривий",      # Хмельницкий
        "крівій": "Кривий",
        "грошев": "Грошев",      # Полтава
        "грошєв": "Грошев",
        # Водители для учёта километража
        "буркало": "Буркало",
        "галунько": "Галунько",
        "горбатко": "Горбатко",
        "грабиченко": "Грабіченко",
        "грабіченко": "Грабіченко",
        "карпенко": "Карпенко",
        "овчаренко": "Овчаренко",
        "сергеев": "Сергеєв",
        "сергєєв": "Сергеєв",
        "сергеєв": "Сергеєв",
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
    EVENT_MILEAGE = "пробег"

    # Regex паттерны
    PATTERNS = {
        # Время в формате HH.MM или HH:MM
        "time": r"(\d{1,2})[.:](\d{2})",

        # Начало сборки (рус/укр/микс)
        # "начата сборка", "початок зборки", "начало зборки", "початок сборки"
        "assembly_start": r"начат[аоы]?\s+со?борк[аи]|початок\s+зборки|начало\s+(?:сборки|зборки)|початок\s+сборки",

        # Сборка завершена
        # "собран маршрут", "сборка завершена"
        "assembly_done": r"собран[аоы]?\s+(?:мар?[шщ]рут|мршт)?|сборка\s+завершена",

        # Выезд (рус/укр)
        # "выехал", "виїхав"
        "departure": r"(?:выехал[аио]?|виїхав|виїхала|виїхали)(?:\s+(?:мар?[шщ]рут|мршт))?",
        "departure_alt": r"([А-ЯІЇЄҐа-яіїєґ]+)\s+(?:выехал|виїхав)",  # Горбатко выехал/виїхав
        "departure_name_after": r"(?:выехал[аио]?|виїхав|виїхала|виїхали)\s+([А-ЯІЇЄҐЁ][а-яіїєґё']+)",  # выехал Роговский

        # Маршрут завершён (рус/укр)
        # Поддерживает: "маршрут 1 завершен", "закончил", "завершив", "закрыт", "закрыл" и т.д.
        "route_complete": r"(?:мар?[шщ]рут|мршт)\s+\d*\s*(?:заверш|закінч|закри)|(?:все\s+)?развез|(?:всё\s+)?развёз|(?:мар?[шщ]рут|мршт)\s+закінчи|закінчив|закончил|завершив|завершен[оа]?|завершён|(?:мар?[шщ]рут|мршт)\s*\d*\s*закрит[оа]?|закрит[оа]?|закрыт|закрыл[аои]?",

        # Все маршруты выехали
        "all_departed": r"вс[еі]\s+(?:(?:мар?[шщ]рут[ыи]|мршт[ыи]?)\s+)?(?:выехал|виїхал)",

        # Проблемы
        "problem": r"не\s+доставив|не\s+вказано|завтра\s+(?:с\s+)?утра|час\s+прийому\s+не",

        # Поздняя доставка: "5 точек после 19"
        "late_delivery": r"(\d+)\s+точ[ео]к\s+после\s+(\d+)",

        # Номер маршрута (с учётом опечаток: марщрут, маршру)
        "route": r"(?:мар?[шщ]рут?|мршт)\s*(\d+)",

        # Имя водителя (после номера маршрута)
        "driver": r"(?:мар?[шщ]рут?|мршт)\s*\d+\s+([А-ЯІЇЄҐа-яіїєґA-Za-z]+)",

        # Пробег: "Косич 120 км", "Косич за сегодня 120 км", "Косич - 120км",
        # "Косич проехав 120 км". Граница слова после "км" отсекает "километров".
        # Допускает 0-2 слов между именем и числом (под "за сегодня", "проехав", "сегодня").
        "mileage": r"([А-ЯІЇЄҐЁ][а-яіїєґё']+)[\s,\-—–]*(?:[а-яіїєґё]+\s+){0,2}(\d{1,4})\s*км\b",
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

        # 2. Если имя уже содержит уникальные украинские буквы — не трогаем
        if any(ch in name_lower for ch in "іїєґ"):
            return name[0].upper() + name[1:]

        # 3. Автотранслитерация рус → укр
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

    def parse(self, text: str, known_drivers: Collection[str] = frozenset()) -> List[ParsedEvent]:
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

        # Пробег: "Косич 120 км" и варианты. Если матч есть и имя из списка
        # известных водителей — это пробег, не пускаем дальше (слово "км"
        # не должно ложно срабатывать на других паттернах).
        # Если имя не из списка — игнорируем молча.
        mileage_match = self.compiled["mileage"].search(text)
        if mileage_match:
            raw_name, km_str = mileage_match.groups()
            name = self.normalize_driver_name(raw_name)
            if name in known_drivers:
                events.append(ParsedEvent(
                    event_type=self.EVENT_MILEAGE,
                    time=None,
                    route_number=None,
                    driver=name,
                    raw_text=text,
                    mileage_km=int(km_str),
                ))
                return events

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
                # Сначала пробуем взять имя из начала строки
                first_word_match = re.match(r"(?:\d{1,2}[.:]\d{2}\s+)?([А-ЯІЇЄҐЁ][а-яіїєґё']+)", text.strip())
                driver_name = None
                if first_word_match:
                    candidate = first_word_match.group(1)
                    if candidate.lower() not in self.SKIP_WORDS:
                        driver_name = candidate
                # Если не нашли в начале, пробуем слово перед "выехал/виїхав"
                if not driver_name:
                    alt_match = self.compiled["departure_alt"].search(text)
                    if alt_match:
                        candidate = alt_match.group(1)
                        if candidate.lower() not in self.SKIP_WORDS:
                            driver_name = candidate
                # Если не нашли перед, пробуем слово после "выехал/виїхав"
                if not driver_name:
                    after_match = self.compiled["departure_name_after"].search(text)
                    if after_match:
                        candidate = after_match.group(1)
                        if candidate.lower() not in self.SKIP_WORDS:
                            driver_name = candidate
                if driver_name:
                    driver_name = self.normalize_driver_name(driver_name)
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
                # Исключаем маршруты, уже записанные как сборка_завершена —
                # слово "завершена" в "сборка завершена" ложно триггерит route_complete
                assembly_done_routes = {
                    e.route_number for e in events
                    if e.event_type == self.EVENT_ASSEMBLY_DONE
                }
                for route, driver in routes_drivers:
                    if route in assembly_done_routes:
                        continue
                    events.append(ParsedEvent(
                        event_type=self.EVENT_ROUTE_COMPLETE,
                        time=time_str,
                        route_number=route,
                        driver=driver,
                        raw_text=text
                    ))
            # Без номера маршрута — не записываем (чтобы не ломать логику активных маршрутов)

        if self.compiled["problem"].search(text_lower):
            # Проблема засчитывается только если есть контекст маршрута
            # или в сообщении уже найдены другие логистические события.
            # Это отсекает нерелевантные сообщения вроде
            # "Суши Швілі точку не доставив клієнт не було розрахунку"
            routes_drivers = self._extract_routes_drivers(text)
            route_num = routes_drivers[0][0] if routes_drivers else None
            driver_name = routes_drivers[0][1] if routes_drivers else None
            if route_num or events:
                if driver_name:
                    driver_name = self.normalize_driver_name(driver_name)
                events.append(ParsedEvent(
                    event_type=self.EVENT_PROBLEM,
                    time=time_str,
                    route_number=route_num,
                    driver=driver_name,
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
        # Местоимения-собирательные (не водители)
        "все", "всі", "всё",
        # Служебные слова
        "ходка", "ходки", "общего", "сборочного",
        # Слова сборки (рус/укр)
        "початок", "начало", "сборка", "зборки", "сборки", "зборка",
        "собран", "собрана", "собрано",
        # Глаголы завершения (рус)
        "закончил", "закончила", "закончили", "закончив",
        "завершил", "завершила", "завершили",
        "завершен", "завершена", "завершено", "завершён",
        "закрыл", "закрыла", "закрыли",
        "развез", "развезла", "развезли", "развёз",
        # Глаголы завершения (укр)
        "закінчив", "закінчила", "закінчили",
        "завершив", "завершила", "завершили",
        "закрив", "закрила", "закрили",
        "закрито",
        "закрит", "закрита",
        # Глаголы завершения (рус)
        "закрыт", "закрыта", "закрыто",
        # Глаголы выезда
        "выехал", "выехала", "выехали",
        "виїхав", "виїхала", "виїхали",
        # Слова загрузки (рус/укр)
        "завантажено", "завантажений", "завантажена",
        "загружен", "загружена", "загружено",
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
        pattern1 = r"(?:мар?[шщ]рут?|мршт)\s*(\d+)\s*([А-ЯІЇЄҐЁа-яіїєґё]+)?"
        matches1 = re.findall(pattern1, text, re.IGNORECASE | re.UNICODE)

        # Паттерн 2: ИмяВодителя маршрут N (водитель перед маршрутом, включая перенос строки)
        pattern2 = r"([А-ЯІЇЄҐЁ][а-яіїєґё']+)\s+(?:мар?[шщ]рут?|мршт)\s*(\d+)"
        matches2 = re.findall(pattern2, text, re.IGNORECASE | re.UNICODE)
        drivers_before = {route_num: driver_name for driver_name, route_num in matches2
                         if driver_name.lower() not in self.SKIP_WORDS}

        # Паттерн 3: ИмяВодителя N маршрут (номер между именем и словом "маршрут")
        # Пример: "Довгаль 6 маршрут закрив"
        pattern3 = r"([А-ЯІЇЄҐЁ][а-яіїєґё']+)\s+(\d+)\s+(?:мар?[шщ]рут|мршт)"
        matches3 = re.findall(pattern3, text, re.IGNORECASE | re.UNICODE)
        for driver_name, route_num in matches3:
            if driver_name.lower() not in self.SKIP_WORDS and route_num not in drivers_before:
                drivers_before[route_num] = driver_name

        # Паттерн 4: N маршрут (номер перед словом "маршрут", без имени рядом)
        # Пример: "Карпенко завершив 3 маршрут" — имя в начале строки
        pattern4 = r"(\d+)\s+(?:мар?[шщ]рут|мршт)"
        matches4 = re.findall(pattern4, text, re.IGNORECASE | re.UNICODE)
        routes_from_pattern4 = set(matches4)  # номера маршрутов из этого паттерна

        # Паттерн 5: Имя+Цифра+маршрут (слитное написание без пробелов)
        # Пример: "Карпенко7маршрут завершив"
        pattern5 = r"([А-ЯІЇЄҐЁ][а-яіїєґё']+)(\d+)(?:мар?[шщ]рут|мршт)"
        matches5 = re.findall(pattern5, text, re.IGNORECASE | re.UNICODE)
        for driver_name, route_num in matches5:
            if driver_name.lower() not in self.SKIP_WORDS and route_num not in drivers_before:
                drivers_before[route_num] = driver_name

        # Паттерн 6: Имя+Цифра+глагол завершения (без слова "маршрут")
        # Пример: "Карпенко5завершив", "Косич3закрив"
        pattern6 = r"([А-ЯІЇЄҐЁ][а-яіїєґё']+)(\d+)(?:заверш|закінч|закри[втл]|закрыл|закончил|развез|завершив|закінчив)"
        matches6 = re.findall(pattern6, text, re.IGNORECASE | re.UNICODE)

        # Ищем имя водителя в начале строки (первое слово с заглавной буквы)
        first_word_match = re.match(r"([А-ЯІЇЄҐЁ][а-яіїєґё']+)", text.strip())
        first_word_driver = None
        if first_word_match:
            candidate = first_word_match.group(1)
            if candidate.lower() not in self.SKIP_WORDS:
                first_word_driver = candidate

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

        # Если всё ещё ничего — пробуем паттерн 4 (N маршрут) с именем из начала строки
        if not results and routes_from_pattern4:
            for route_num in routes_from_pattern4:
                driver_name = first_word_driver
                if driver_name:
                    driver_name = self.normalize_driver_name(driver_name)
                results.append((route_num, driver_name))

        # Последний fallback: паттерн 6 (Имя+Цифра+глагол без "маршрут")
        if not results and matches6:
            for driver_name, route_num in matches6:
                if driver_name.lower() not in self.SKIP_WORDS:
                    driver_name = self.normalize_driver_name(driver_name)
                    results.append((route_num, driver_name))

        return results


# Для тестирования
if __name__ == "__main__":
    parser = MessageParser()

    test_drivers = {"Косич"}

    test_messages = [
        # Старые тесты
        "6.00 начата сборка общего сборочного листа",
        "7.34 начата сборка маршрут 1 Кияниця 3 ходка, маршрут 2 Косич 3 ходка и маршрут 3 Сергеев",
        "7.40 собран маршрут 1 Кияниця 3 ходка и маршрут 2 Косич 3 ходка",
        "8.30 выехал маршрут 2 Косич 3 ходка",
        "Все маршруты выехали",
        "Маршрут завершив",
        "Точку не доставив",  # без маршрута — НЕ должно распознаваться
        "Маршрут 5 Карпенко точку не доставив",  # с маршрутом — проблема
        "Святопетрівське Суши Швілі точку не доставив клієнт не було розрахунку",  # НЕ логистика — должно игнорироваться
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
        # Формат "Имя глагол N маршрут" (глагол между именем и номером)
        "Карпенко завершив 3 маршрут",
        # Формат слитный "Имя+Цифра+маршрут" (без пробелов)
        "Карпенко7маршрут завершив",
        # Формат "Имя маршрут N закрыл" (глагол закрыл в конце)
        "Косич маршрут 1 закрыл",
        # Закрит (опечатка ы→и)
        "Маршрут 5 Карпенко закрит 17:42",
        # Имя+Цифра+глагол без "маршрут"
        "Карпенко5завершив",
        # Опечатка: "Машрут" вместо "Маршрут" (пропущена р)
        "Машрут 3 Грабиченко закончил",
        # Формат "выехал ИМЯ мршт" (без номера маршрута)
        "11:47 выехал Роговский мршт ",
        # Пробег: известный водитель → событие, неизвестный → молча игнорируется
        "Косич 120 км",
        "Сегодня проехали 200 км",
    ]

    for msg in test_messages:
        print(f"\n--- {msg} ---")
        events = parser.parse(msg, test_drivers)
        for e in events:
            print(f"  {e.event_type}: маршрут={e.route_number}, водитель={e.driver}, время={e.time}")
