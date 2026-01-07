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

    # Типы событий
    EVENT_ASSEMBLY_START = "начало_сборки"
    EVENT_ASSEMBLY_DONE = "сборка_завершена"
    EVENT_DEPARTURE = "выезд"
    EVENT_ROUTE_COMPLETE = "маршрут_завершён"
    EVENT_ALL_DEPARTED = "все_выехали"
    EVENT_PROBLEM = "проблема"

    # Regex паттерны
    PATTERNS = {
        # Время в формате HH.MM или HH:MM
        "time": r"(\d{1,2})[.:](\d{2})",

        # Начало сборки
        "assembly_start": r"начат[аоы]?\s+сборк[аи]",

        # Сборка завершена
        "assembly_done": r"собран[аоы]?\s+(?:маршрут)?",

        # Выезд
        "departure": r"выехал[аио]?\s+(?:маршрут)?",

        # Маршрут завершён (рус/укр)
        "route_complete": r"маршрут\s+(?:заверш|закінч|закри)|все\s+развез|всё\s+развёз|маршрут\s+закінчи|закінчив",

        # Все маршруты выехали
        "all_departed": r"все\s+маршрут[ыи]\s+выехал",

        # Проблемы
        "problem": r"не\s+доставив|не\s+вказано|завтра\s+(?:с\s+)?утра|час\s+прийому\s+не",

        # Номер маршрута
        "route": r"маршрут\s*(\d+)",

        # Имя водителя (после номера маршрута)
        "driver": r"маршрут\s*\d+\s+([А-ЯІЇЄҐа-яіїєґA-Za-z]+)",
    }

    def __init__(self):
        # Компилируем регулярки
        self.compiled = {
            key: re.compile(pattern, re.IGNORECASE | re.UNICODE)
            for key, pattern in self.PATTERNS.items()
        }

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
            else:
                events.append(ParsedEvent(
                    event_type=self.EVENT_ROUTE_COMPLETE,
                    time=time_str,
                    route_number=None,
                    driver=None,
                    raw_text=text
                ))

        if self.compiled["problem"].search(text_lower):
            events.append(ParsedEvent(
                event_type=self.EVENT_PROBLEM,
                time=time_str,
                route_number=None,
                driver=None,
                raw_text=text
            ))

        return events

    def _extract_routes_drivers(self, text: str) -> List[tuple]:
        """
        Извлекает пары (номер_маршрута, водитель) из текста.
        Например: "маршрут 1 Кияниця" -> [("1", "Кияниця")]
        """
        results = []

        # Ищем паттерн: маршрут N ИмяВодителя
        pattern = r"маршрут\s*(\d+)\s+([А-ЯІЇЄҐа-яіїєґ]+)"
        matches = re.findall(pattern, text, re.IGNORECASE | re.UNICODE)

        for route_num, driver_name in matches:
            # Фильтруем служебные слова
            if driver_name.lower() not in ["и", "та", "ходка", "общего", "сборочного"]:
                results.append((route_num, driver_name))

        return results


# Для тестирования
if __name__ == "__main__":
    parser = MessageParser()

    test_messages = [
        "6.00 начата сборка общего сборочного листа",
        "7.34 начата сборка маршрут 1 Кияниця 3 ходка, маршрут 2 Косич 3 ходка и маршрут 3 Сергеев",
        "7.40 собран маршрут 1 Кияниця 3 ходка и маршрут 2 Косич 3 ходка",
        "8.30 выехал маршрут 2 Косич 3 ходка",
        "Все маршруты выехали",
        "Маршрут завершив",
        "Точку не доставив",
        "Завтра с утра завезу",
    ]

    for msg in test_messages:
        print(f"\n--- {msg} ---")
        events = parser.parse(msg)
        for e in events:
            print(f"  {e.event_type}: маршрут={e.route_number}, водитель={e.driver}, время={e.time}")
