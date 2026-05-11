from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from .models import BusinessRecord


FIELDNAMES = [
    "search_query",
    "name",
    "category",
    "address",
    "phone",
    "website",
    "emails",
    "scraped_at",
]


def write_records(records: Iterable[BusinessRecord], output_path: str | Path, output_format: str) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    records = list(records)

    if output_format == "json":
        path.write_text(
            json.dumps([record.to_row() for record in records], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        for record in records:
            writer.writerow(record.to_row())

    return path


class RecordBatchWriter:
    def __init__(self, output_path: str | Path, output_format: str, append: bool = True):
        self.path = Path(output_path)
        self.output_format = output_format
        self.append = append
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._json_rows: list[dict[str, str]] = []

        if append and output_format == "json" and self.path.exists() and self.path.stat().st_size > 0:
            self._json_rows = json.loads(self.path.read_text(encoding="utf-8"))

    def write_batch(self, records: Iterable[BusinessRecord]) -> int:
        records = list(records)
        if not records:
            return 0

        if self.output_format == "json":
            self._json_rows.extend(record.to_row() for record in records)
            self.path.write_text(json.dumps(self._json_rows, indent=2, ensure_ascii=False), encoding="utf-8")
            return len(records)

        if self.output_format == "jsonl":
            with self.path.open("a", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record.to_row(), ensure_ascii=False) + "\n")
            return len(records)

        file_exists = self.path.exists() and self.path.stat().st_size > 0
        mode = "a" if self.append else "w"
        with self.path.open(mode, newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
            if not file_exists or not self.append:
                writer.writeheader()
            for record in records:
                writer.writerow(record.to_row())

        self.append = True
        return len(records)
