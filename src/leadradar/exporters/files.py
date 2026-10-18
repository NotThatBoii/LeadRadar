import json
import os
import tempfile
from pathlib import Path

import pandas as pd

COLUMNS = [
    "id",
    "business_name",
    "website",
    "domain",
    "category",
    "city",
    "region",
    "country",
    "phone",
    "address",
    "source",
    "source_record_id",
    "source_url",
    "source_kind",
    "first_collected_at",
    "last_updated_at",
    "last_seen_at",
    "validation_status",
    "warnings",
    "attribution",
    "license_url",
    "original_values",
    "field_provenance",
    "observations",
]


def safe_cell(value):
    if isinstance(value, str):
        # Check after leading whitespace/control chars; protects Excel/Sheets imports.
        candidate = value.lstrip(" \t\r\n\x00\x0b\x0c\ufeff")
        if candidate.startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
            return "'" + value
    return value


def safe_value(value):
    if isinstance(value, dict):
        return {key: safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_value(item) for item in value]
    return safe_cell(value)


def export_records(records: list[dict], destination: Path, format: str) -> int:
    if format not in {"csv", "json"}:
        raise ValueError("Export format must be csv or json")
    if destination.exists():
        raise FileExistsError(f"Output exists: {destination}; choose a new filename")
    destination.parent.mkdir(parents=True, exist_ok=True)
    sanitized = [safe_value({key: record.get(key) for key in COLUMNS}) for record in records]
    fd, temporary = tempfile.mkstemp(prefix=".leadradar-", dir=destination.parent)
    try:
        with os.fdopen(
            fd, "w", encoding="utf-8-sig" if format == "csv" else "utf-8", newline=""
        ) as file:
            if format == "json":
                json.dump(sanitized, file, ensure_ascii=False, indent=2, allow_nan=False)
                file.write("\n")
            else:
                flattened = [
                    {
                        key: json.dumps(value, ensure_ascii=False)
                        if isinstance(value, (dict, list))
                        else value
                        for key, value in record.items()
                    }
                    for record in sanitized
                ]
                pd.DataFrame(flattened, columns=COLUMNS).to_csv(
                    file, index=False, lineterminator="\n"
                )
        # Reserve exclusively, then replace our own placeholder with the completed file.
        # Works on Windows/OneDrive without requiring hard-link privileges.
        with destination.open("xb"):
            pass
        try:
            os.replace(temporary, destination)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    finally:
        Path(temporary).unlink(missing_ok=True)
    return len(records)
