from threading import Lock

from sheets import SheetsManager


class FakeMileageSheet:
    id = 123

    def __init__(self, grid, driver_cells):
        self.grid = grid
        self.driver_cells = driver_cells

    def get_values(self, range_name, **kwargs):
        if range_name == "A1:ZZ300":
            return self.grid
        if range_name == SheetsManager.MILEAGE_DRIVER_ROWS_RANGE:
            return self.driver_cells
        raise AssertionError(f"Unexpected range: {range_name}")


class FakeSpreadsheet:
    def __init__(self):
        self.payload = None

    def batch_update(self, payload):
        self.payload = payload


def make_manager(grid, driver_cells):
    manager = object.__new__(SheetsManager)
    manager.mileage_sheet = FakeMileageSheet(grid, driver_cells)
    manager.mileage_sheet_id = 123
    manager.spreadsheet = FakeSpreadsheet()
    manager._mileage_lock = Lock()
    manager._driver_rows_cache = None
    manager._driver_rows_ts = 0
    return manager


def test_backfill_fills_missing_fuel_formula_when_mileage_formula_exists():
    manager = make_manager(
        [
            ["", "", "", "Розрахунок", "Розрахунок", "M2026-05"],
            [],
            ["№", "Водій", "Планова витрата", "Пробіг км", "Витрата палива", "16.05.26"],
            ["", "Іван", "10", '=SUMIFS($4:$4;$1:$1;"M2026-05")', "", "120"],
        ],
        [["Іван"]],
    )

    count = SheetsManager.backfill_mileage_formulas(manager)

    assert count == 1
    requests = manager.spreadsheet.payload["requests"]
    assert len(requests) == 1
    update = requests[0]["updateCells"]
    assert update["range"] == {
        "sheetId": 123,
        "startRowIndex": 3,
        "endRowIndex": 4,
        "startColumnIndex": 4,
        "endColumnIndex": 5,
    }
    assert update["rows"][0]["values"] == [
        {"userEnteredValue": {"formulaValue": "=D4/100*C4"}},
    ]
