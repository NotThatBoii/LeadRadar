import csv
import json

import pytest

from leadradar.exporters.files import COLUMNS, export_records, safe_cell


@pytest.mark.parametrize(
    "value", ['=HYPERLINK("bad")', "+1234", "-123", "@SUM(A1)", "  =1+1", "\t=1+1", "\ntext"]
)
def test_formula_cells_are_escaped(value):
    assert safe_cell(value) == "'" + value


def test_csv_utf8_nulls_headers_and_formula_protection(tmp_path):
    output = tmp_path / "leads.csv"
    export_records(
        [
            {
                "business_name": "Café",
                "phone": "+00 555",
                "website": None,
                "original_values": {"phone": "+00 555"},
            }
        ],
        output,
        "csv",
    )
    assert output.read_bytes().startswith(b"\xef\xbb\xbf")
    with output.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        assert reader.fieldnames == COLUMNS
        row = next(reader)
    assert row["business_name"] == "Café" and row["website"] == ""
    assert row["phone"] == "'+00 555"


def test_json_preserves_null_and_attribution_and_sanitizes_nested_values(tmp_path):
    output = tmp_path / "leads.json"
    export_records(
        [
            {
                "business_name": "@danger",
                "website": None,
                "attribution": "© OpenStreetMap contributors",
                "observations": [{"phone": "+00 123"}],
            }
        ],
        output,
        "json",
    )
    record = json.loads(output.read_text(encoding="utf-8"))[0]
    assert record["website"] is None
    assert record["business_name"] == "'@danger"
    assert record["observations"][0]["phone"] == "'+00 123"
    assert record["attribution"] == "© OpenStreetMap contributors"


def test_empty_exports_are_valid(tmp_path):
    csv_path, json_path = tmp_path / "empty.csv", tmp_path / "empty.json"
    assert export_records([], csv_path, "csv") == 0
    assert export_records([], json_path, "json") == 0
    assert json.loads(json_path.read_text()) == []
    assert csv_path.read_text(encoding="utf-8-sig").strip().split(",") == COLUMNS


def test_existing_export_is_never_overwritten(tmp_path):
    output = tmp_path / "protected.csv"
    output.write_text("keep me")
    with pytest.raises(FileExistsError):
        export_records([], output, "csv")
    assert output.read_text() == "keep me"


def test_unsupported_format_leaves_no_file(tmp_path):
    output = tmp_path / "leads.xlsx"
    with pytest.raises(ValueError):
        export_records([], output, "xlsx")
    assert not output.exists()
