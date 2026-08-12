from threading import Lock

from sheets import DriverAliasResult, DriverChangeResult, SheetsManager


def _grid(*data_rows):
    return [
        ["", "", "", "Розрахунок", "Розрахунок", "M2026-08"],
        ["", "", "", "Серпень 2026"],
        ["№", "Водій", "Планова витрата", "Пробіг км", "Витрата палива", "10.08.26"],
        *[list(row) for row in data_rows],
    ]


class FakeMileageSheet:
    id = 321

    def __init__(self, grid):
        self.grid = grid

    def get_values(self, range_name, **kwargs):
        if range_name == SheetsManager.MILEAGE_DRIVER_ROWS_RANGE:
            return [list(row[:2]) for row in self.grid[3:]]
        if range_name == SheetsManager.MILEAGE_MANAGEMENT_GRID_RANGE:
            assert kwargs == {"value_render_option": "FORMULA"}
            return self.grid
        raise AssertionError(f"Unexpected range: {range_name}")


class FakeSpreadsheet:
    def __init__(self, error=None, on_batch=None):
        self.payloads = []
        self.error = error
        self.on_batch = on_batch

    def batch_update(self, payload):
        if self.on_batch is not None:
            self.on_batch()
        if self.error is not None:
            raise self.error
        self.payloads.append(payload)


def make_manager(grid, *, error=None, on_batch=None):
    manager = object.__new__(SheetsManager)
    manager.mileage_sheet = FakeMileageSheet(grid)
    manager.mileage_sheet_id = 321
    manager.spreadsheet = FakeSpreadsheet(error=error, on_batch=on_batch)
    manager._mileage_lock = Lock()
    manager._driver_rows_cache = {"Старый кэш": 99}
    manager._driver_rows_ts = 1
    manager._driver_aliases_cache = None
    manager._driver_aliases_ts = 0
    manager._RETRY_DELAYS = ()
    manager.driver_aliases_sheet = None
    return manager


class FakeAliasSheet:
    def __init__(self, rows=None):
        self.rows = [list(row) for row in (rows or [])]

    def get_values(self, range_name):
        assert range_name == SheetsManager.DRIVER_ALIASES_RANGE
        return [list(row) for row in self.rows]

    def append_row(self, row, value_input_option=None):
        assert value_input_option == "RAW"
        self.rows.append(list(row))

    def delete_rows(self, row):
        self.rows.pop(row - 2)


def _move_requests(manager):
    return [
        request["moveDimension"]
        for request in manager.spreadsheet.payloads[0]["requests"]
        if "moveDimension" in request
    ]


def test_get_driver_roster_preserves_empty_cities_and_excludes_archive_from_active():
    manager = make_manager(_grid(
        ["Киев", ""],
        ["1", "Иван", "10"],
        ["Львов", ""],
        ["Уволенные", ""],
        ["Киев", ""],
        ["1", "Петр", "11"],
        ["Одесса", ""],
    ))

    roster = manager.get_driver_roster()

    assert roster == {
        "ok": True,
        "active": {"Киев": ["Иван"], "Львов": []},
        "archived": {"Киев": ["Петр"], "Одесса": []},
    }
    assert manager.get_mileage_drivers() == {"Иван"}


def test_add_driver_inserts_at_city_end_and_creates_all_calculation_formulas():
    manager = make_manager(_grid(
        ["Киев", ""],
        ["=ROW()-4", "Иван", "10"],
        ["Львов", ""],
        ["Уволенные", ""],
        ["Киев", ""],
        ["", "Петр", "11"],
    ))

    result = manager.add_mileage_driver("киев", "  Олег  ", "12,5")

    assert result == DriverChangeResult(True, "added", "Олег", "Киев")
    requests = manager.spreadsheet.payloads[0]["requests"]
    assert requests[0]["insertDimension"]["range"] == {
        "sheetId": 321,
        "dimension": "ROWS",
        "startIndex": 5,
        "endIndex": 6,
    }
    assert any(
        request.get("copyPaste", {}).get("pasteType") == "PASTE_FORMAT"
        for request in requests
    )
    assert any(
        request.get("copyPaste", {}).get("pasteType") == "PASTE_DATA_VALIDATION"
        for request in requests
    )
    assert any(
        request.get("copyPaste", {}).get("pasteType") == "PASTE_FORMULA"
        for request in requests
    )

    values_update = next(
        request["updateCells"]
        for request in requests
        if request.get("updateCells", {}).get("range", {}).get("startColumnIndex") == 1
    )
    assert values_update["range"]["startRowIndex"] == 5
    assert values_update["rows"][0]["values"] == [
        {"userEnteredValue": {"stringValue": "Олег"}},
        {"userEnteredValue": {"numberValue": 12.5}},
    ]

    formula_update = next(
        request["updateCells"]
        for request in requests
        if request.get("updateCells", {}).get("range", {}).get("startColumnIndex") == 3
    )
    assert formula_update["range"]["startRowIndex"] == 5
    assert formula_update["rows"][0]["values"] == [
        {"userEnteredValue": {
            "formulaValue": '=SUMIFS($6:$6;$1:$1;"M2026-08")',
        }},
        {"userEnteredValue": {"formulaValue": "=D6/100*C6"}},
    ]
    assert manager._driver_rows_cache is None
    assert manager._driver_rows_ts == 0


def test_add_rejects_casefold_duplicate_across_active_and_archive_without_write():
    manager = make_manager(_grid(
        ["Киев", ""],
        ["1", "Иван", "10"],
        ["Уволенные", ""],
        ["Киев", ""],
        ["1", "Петр", "11"],
    ))

    result = manager.add_mileage_driver("Киев", "пЕТР", 12)

    assert result == DriverChangeResult(False, "duplicate_driver", "Петр", "Киев")
    assert manager.spreadsheet.payloads == []


def test_add_rejects_out_of_range_fuel_rate_without_read_or_write():
    manager = make_manager(_grid(["Киев", ""]))

    result = manager.add_mileage_driver("Киев", "Олег", 101)

    assert result == DriverChangeResult(
        False, "invalid_fuel_rate", "Олег", "Киев",
    )
    assert manager.spreadsheet.payloads == []


def test_add_does_not_copy_unknown_manual_numbering_in_column_a():
    manager = make_manager(_grid(
        ["Киев", ""],
        ["водитель №1", "Иван", "10"],
    ))

    result = manager.add_mileage_driver("Киев", "Олег", 12)

    assert result.ok is True
    requests = manager.spreadsheet.payloads[0]["requests"]
    assert not any(
        request.get("copyPaste", {}).get("pasteType") == "PASTE_FORMULA"
        for request in requests
    )
    assert not any(
        request.get("updateCells", {}).get("range", {}).get("startColumnIndex") == 0
        for request in requests
    )


def test_add_to_empty_city_uses_shifted_safe_exemplar():
    manager = make_manager(_grid(
        ["Киев", ""],
        ["Львов", ""],
        ["=ROW()-4", "Анна", "9"],
    ))

    result = manager.add_mileage_driver("Киев", "Олег", 12)

    assert result == DriverChangeResult(True, "added", "Олег", "Киев")
    requests = manager.spreadsheet.payloads[0]["requests"]
    format_copy = next(
        request["copyPaste"]
        for request in requests
        if request.get("copyPaste", {}).get("pasteType") == "PASTE_FORMAT"
    )
    assert format_copy["source"]["startRowIndex"] == 6
    assert format_copy["destination"]["startRowIndex"] == 4


def test_add_uses_archived_exemplar_when_all_active_blocks_are_empty():
    manager = make_manager(_grid(
        ["Киев", ""],
        ["Уволенные", ""],
        ["Киев", ""],
        ["=ROW()-4", "Иван", "10"],
    ))

    result = manager.add_mileage_driver("Киев", "Олег", 12)

    assert result == DriverChangeResult(True, "added", "Олег", "Киев")
    requests = manager.spreadsheet.payloads[0]["requests"]
    assert any(
        request.get("copyPaste", {}).get("pasteType") == "PASTE_FORMAT"
        for request in requests
    )
    assert any(
        request.get("copyPaste", {}).get("pasteType") == "PASTE_DATA_VALIDATION"
        for request in requests
    )


def test_archive_moves_down_using_pre_removal_destination_index():
    manager = make_manager(_grid(
        ["Киев", ""],       # row 4
        ["1", "Иван", "10"],  # row 5, source index 4
        ["2", "Олег", "12"],
        ["Львов", ""],
        ["1", "Анна", "9"],
        ["Уволенные", ""],
        ["Киев", ""],
        ["1", "Петр", "11"],
        ["Львов", ""],      # row 12, destination index 11
    ))

    result = manager.archive_mileage_driver("КИЕВ", "иван")

    assert result == DriverChangeResult(True, "archived", "Иван", "Киев")
    assert _move_requests(manager) == [{
        "source": {
            "sheetId": 321,
            "dimension": "ROWS",
            "startIndex": 4,
            "endIndex": 5,
        },
        "destinationIndex": 11,
    }]
    assert manager._driver_rows_cache is None


def test_archive_creates_archive_headers_atomically_before_moving_row():
    manager = make_manager(_grid(
        ["Киев", ""],
        ["1", "Иван", "10"],
        ["Львов", ""],
        ["1", "Анна", "9"],
    ))

    result = manager.archive_mileage_driver("Киев", "Иван")

    assert result.code == "archived"
    requests = manager.spreadsheet.payloads[0]["requests"]
    insert = requests[0]["insertDimension"]["range"]
    assert insert["startIndex"] == 7
    assert insert["endIndex"] == 9
    assert _move_requests(manager)[0]["destinationIndex"] == 9
    header_values = [
        request["updateCells"]["rows"][0]["values"][0]["userEnteredValue"]["stringValue"]
        for request in requests
        if "updateCells" in request
        and "stringValue" in request["updateCells"]["rows"][0]["values"][0]["userEnteredValue"]
    ]
    assert header_values == ["Уволенные", "Киев"]


def test_archive_adds_missing_city_subsection_to_existing_archive():
    manager = make_manager(_grid(
        ["Киев", ""],
        ["1", "Иван", "10"],
        ["Львов", ""],
        ["1", "Анна", "9"],
        ["Уволенные", ""],
        ["Киев", ""],
        ["2", "Петр", "11"],
    ))

    result = manager.archive_mileage_driver("Львов", "Анна")

    assert result == DriverChangeResult(True, "archived", "Анна", "Львов")
    requests = manager.spreadsheet.payloads[0]["requests"]
    insert = requests[0]["insertDimension"]["range"]
    assert insert["startIndex"] == 10
    assert insert["endIndex"] == 11
    assert _move_requests(manager)[0]["destinationIndex"] == 11
    assert any(
        request.get("updateCells", {}).get("rows", [{}])[0]
        .get("values", [{}])[0]
        .get("userEnteredValue", {})
        .get("stringValue") == "Львов"
        for request in requests
    )


def test_repeated_archive_is_idempotent_and_does_not_write():
    manager = make_manager(_grid(
        ["Киев", ""],
        ["Уволенные", ""],
        ["Киев", ""],
        ["1", "Иван", "10"],
    ))

    result = manager.archive_mileage_driver("Киев", "ИВАН")

    assert result == DriverChangeResult(False, "already_archived", "Иван", "Киев")
    assert manager.spreadsheet.payloads == []


def test_restore_moves_up_and_rewrites_formulas_for_final_row():
    manager = make_manager(_grid(
        ["Киев", ""],       # row 4
        ["1", "Иван", "10"],
        ["Львов", ""],      # row 6, destination index 5
        ["1", "Анна", "9"],
        ["Уволенные", ""],
        ["Киев", ""],
        ["2", "Петр", "11"],  # row 10, source index 9
    ))

    result = manager.restore_mileage_driver("петр", "КИЕВ")

    assert result == DriverChangeResult(True, "restored", "Петр", "Киев")
    assert _move_requests(manager) == [{
        "source": {
            "sheetId": 321,
            "dimension": "ROWS",
            "startIndex": 9,
            "endIndex": 10,
        },
        "destinationIndex": 5,
    }]
    formula_update = manager.spreadsheet.payloads[0]["requests"][1]["updateCells"]
    assert formula_update["range"]["startRowIndex"] == 5
    assert formula_update["rows"][0]["values"] == [
        {"userEnteredValue": {
            "formulaValue": '=SUMIFS($6:$6;$1:$1;"M2026-08")',
        }},
        {"userEnteredValue": {"formulaValue": "=D6/100*C6"}},
    ]


def test_repeated_restore_is_idempotent_and_does_not_write():
    manager = make_manager(_grid(
        ["Киев", ""],
        ["1", "Иван", "10"],
        ["Уволенные", ""],
        ["Киев", ""],
    ))

    result = manager.restore_mileage_driver("ИВАН", "Киев")

    assert result == DriverChangeResult(False, "already_active", "Иван", "Киев")
    assert manager.spreadsheet.payloads == []


def test_batch_error_returns_sheets_error_and_invalidates_row_cache():
    manager = make_manager(_grid(
        ["Киев", ""],
        ["1", "Иван", "10"],
    ), error=RuntimeError("batch failed"))

    result = manager.add_mileage_driver("Киев", "Олег", 12)

    assert result == DriverChangeResult(False, "sheets_error", "Олег", "Киев")
    assert manager._driver_rows_cache is None
    assert manager._driver_rows_ts == 0


def test_add_reconciles_timeout_after_batch_was_applied():
    grid = _grid(
        ["Киев", ""],
        ["1", "Иван", "10"],
    )
    manager = make_manager(
        grid,
        error=TimeoutError("response lost"),
        on_batch=lambda: grid.append(["", "Олег", "12"]),
    )

    result = manager.add_mileage_driver("Киев", "Олег", 12)

    assert result == DriverChangeResult(True, "added", "Олег", "Киев")
    assert manager._driver_rows_cache is None


def test_archive_reconciles_timeout_after_move_was_applied():
    grid = _grid(
        ["Киев", ""],
        ["1", "Иван", "10"],
        ["Уволенные", ""],
        ["Киев", ""],
    )

    def apply_move():
        driver_row = grid.pop(4)
        grid.append(driver_row)

    manager = make_manager(
        grid,
        error=TimeoutError("response lost"),
        on_batch=apply_move,
    )

    result = manager.archive_mileage_driver("Киев", "Иван")

    assert result == DriverChangeResult(True, "archived", "Иван", "Киев")
    assert manager._driver_rows_cache is None


def test_restore_reconciles_timeout_after_move_was_applied():
    grid = _grid(
        ["Киев", ""],
        ["Уволенные", ""],
        ["Киев", ""],
        ["1", "Иван", "10"],
    )

    def apply_move():
        driver_row = grid.pop(6)
        grid.insert(4, driver_row)

    manager = make_manager(
        grid,
        error=TimeoutError("response lost"),
        on_batch=apply_move,
    )

    result = manager.restore_mileage_driver("Иван", "Киев")

    assert result == DriverChangeResult(True, "restored", "Иван", "Киев")
    assert manager._driver_rows_cache is None


def test_managed_alias_add_collision_and_remove():
    manager = make_manager(_grid(
        ["Київ", ""],
        ["1", "Сергеєв", "10"],
        ["2", "Косич", "11"],
    ))
    manager.driver_aliases_sheet = FakeAliasSheet([
        ["Сергеев", "Сергеєв"],
    ])

    same = manager.add_driver_alias("Сергеєв", "сергеев")
    conflict = manager.add_driver_alias("Косич", "Сергеев")
    added = manager.add_driver_alias("Косич", "Косіч")
    removed = manager.remove_driver_alias("Косич", "косіч")

    assert same == DriverAliasResult(False, "alias_exists", "сергеев", "Сергеєв")
    assert conflict == DriverAliasResult(False, "alias_conflict", "Сергеев", "Сергеєв")
    assert added == DriverAliasResult(True, "alias_added", "Косіч", "Косич")
    assert removed == DriverAliasResult(True, "alias_removed", "Косіч", "Косич")
    assert manager.driver_aliases_sheet.rows == [["Сергеев", "Сергеєв"]]


def test_alias_cannot_shadow_another_canonical_driver():
    manager = make_manager(_grid(
        ["Київ", ""],
        ["1", "Сергеєв", "10"],
        ["2", "Косич", "11"],
    ))
    manager.driver_aliases_sheet = FakeAliasSheet()

    result = manager.add_driver_alias("Сергеєв", "Косич")

    assert result == DriverAliasResult(False, "alias_is_driver", "Косич", "Сергеєв")
    assert manager.driver_aliases_sheet.rows == []
