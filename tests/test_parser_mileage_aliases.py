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
