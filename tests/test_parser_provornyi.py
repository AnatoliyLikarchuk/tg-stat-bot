import pytest

from parser import MessageParser


@pytest.mark.parametrize("name", ["Проворный", "Проворний", "Проворній"])
@pytest.mark.parametrize("template,event_type", [
    ("10.01 начата сборка маршрут 3 {} и маршрут 4 Горбатко", "начало_сборки"),
    ("10.28 собран маршрут 3 {}", "сборка_завершена"),
    ("10.44 выехал маршрут 3 {}", "выезд"),
    ("{} маршрут 3 завершив", "маршрут_завершён"),
])
def test_route_events_use_one_canonical_surname(name, template, event_type):
    events = MessageParser().parse(template.format(name), set(), {})
    route = [event for event in events if event.route_number == "3"]
    assert len(route) == 1
    assert route[0].event_type == event_type
    assert route[0].driver == "Проворний"


@pytest.mark.parametrize("name", ["Проворный", "Проворний", "Проворній"])
def test_mileage_aliases_do_not_bypass_driver_roster(name):
    parser = MessageParser()
    aliases = {key: "Проворний" for key in ("проворный", "проворний", "проворній")}
    assert parser.parse(f"{name} 120 км", set(), aliases) == []
    events = parser.parse(f"{name} 120 км", {"Проворний"}, aliases)
    assert len(events) == 1
    assert events[0].driver == "Проворний"
    assert events[0].mileage_km == 120
