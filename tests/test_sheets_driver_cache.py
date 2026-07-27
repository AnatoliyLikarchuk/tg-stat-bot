from sheets import SheetsManager


class SequencedMileageSheet:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def get_values(self, range_name):
        assert range_name == SheetsManager.MILEAGE_DRIVER_ROWS_RANGE
        self.calls += 1
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def make_manager(responses, cache=None):
    manager = object.__new__(SheetsManager)
    manager.mileage_sheet = SequencedMileageSheet(responses)
    manager._driver_rows_cache = cache
    manager._driver_rows_ts = 0
    manager._RETRY_DELAYS = (0, 0)
    return manager


def test_driver_rows_retry_transient_google_error():
    manager = make_manager([
        RuntimeError("service unavailable"),
        [["Будагов"], ["Мельников"]],
    ])

    rows = manager._get_driver_rows()

    assert rows == {"Будагов": 4, "Мельников": 5}
    assert manager.mileage_sheet.calls == 2
    assert manager._driver_rows_cache == rows


def test_driver_rows_use_stale_cache_when_retries_exhausted():
    stale_cache = {"Будагов": 27}
    manager = make_manager(
        [
            RuntimeError("service unavailable"),
            RuntimeError("service unavailable"),
            RuntimeError("service unavailable"),
        ],
        cache=stale_cache,
    )

    rows = manager._get_driver_rows()

    assert rows is stale_cache
    assert manager.mileage_sheet.calls == 3
