from parser import MessageParser


def test_mileage_accepts_fedorov_with_e_and_yo():
    parser = MessageParser()
    known_drivers = {"Федоров"}

    for raw_name in ("Федоров", "Фёдоров"):
        events = parser.parse(f"{raw_name} 123 км", known_drivers)

        assert len(events) == 1
        assert events[0].event_type == MessageParser.EVENT_MILEAGE
        assert events[0].driver == "Федоров"
        assert events[0].mileage_km == 123


def test_mileage_accepts_popovych_in_russian_and_ukrainian():
    parser = MessageParser()
    known_drivers = {"Попович"}

    for raw_name in ("Попович", "Поповіч"):
        events = parser.parse(f"{raw_name} 123 км", known_drivers)

        assert len(events) == 1
        assert events[0].event_type == MessageParser.EVENT_MILEAGE
        assert events[0].driver == "Попович"
        assert events[0].mileage_km == 123


def test_managed_alias_has_priority_and_still_requires_active_driver():
    parser = MessageParser()
    aliases = {"сергеев": "Сергеєв"}

    events = parser.parse("Сергеев 87 км", {"Сергеєв"}, aliases)
    archived = parser.parse("Сергеев 87 км", set(), aliases)

    assert len(events) == 1
    assert events[0].driver == "Сергеєв"
    assert archived == []


def test_empty_managed_alias_table_disables_legacy_alias():
    parser = MessageParser()

    events = parser.parse("Поповіч 42 км", {"Попович"}, {})

    assert events == []


def test_canonical_driver_with_shared_letters_needs_no_alias():
    parser = MessageParser()

    events = parser.parse("Косич 42 км", {"Косич"}, {})

    assert len(events) == 1
    assert events[0].driver == "Косич"
