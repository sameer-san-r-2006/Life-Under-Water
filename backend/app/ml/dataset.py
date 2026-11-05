"""Export only valid sensor rows with explicit, verified human category labels."""

import argparse
import csv
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg.rows import dict_row

from app.database import get_connection
from app.ml.service import CLASS_NAMES, DATA_DIR, FEATURE_DESCRIPTIONS, FEATURE_NAMES


LABEL_FIELDS = ("reading_id", "sample_id", "water_category", "label_verified")
DATASET_FIELDS = ("reading_id", "sample_id", *FEATURE_NAMES, "water_category")


def finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def valid_feature_values(row: dict[str, Any]) -> dict[str, float] | None:
    temperature = finite_number(row.get("temperature_c"))
    tds = finite_number(row.get("tds_ppm"))
    cloudiness = finite_number(row.get("cloudiness_index"))
    temperature_valid = (
        row.get("temperature_valid") is True
        and temperature is not None
        and -55 <= temperature <= 125
    )
    tds_valid = (
        row.get("tds_valid") is True
        and row.get("tds_source") == "calibrated"
        and row.get("tds_calibration_status") == "calibrated"
        and row.get("tds_conversion_method") != "legacy_demo_lookup_v1"
        and tds is not None
        and tds >= 0
    )
    cloudiness_valid = (
        row.get("cloudiness_valid") is True
        and cloudiness is not None
        and 0 <= cloudiness <= 100
    )
    if not (temperature_valid and tds_valid and cloudiness_valid):
        return None
    return {
        "tds_ppm": tds,
        "temperature_c": temperature,
        "cloudiness_index": cloudiness,
    }


def fetch_readings() -> list[dict[str, Any]]:
    with get_connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id, recorded_at, temperature_c, temperature_valid,
                       tds_ppm, tds_valid, tds_source, tds_calibration_status,
                       tds_conversion_method, cloudiness_index, cloudiness_valid
                FROM sensor_readings
                ORDER BY recorded_at, id
                """
            )
            return cursor.fetchall()


def read_verified_labels(path: Path) -> tuple[dict[int, dict[str, str]], int]:
    if not path.is_file():
        return {}, 0
    labels: dict[int, dict[str, str]] = {}
    seen_reading_ids = set()
    unverified_count = 0
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != LABEL_FIELDS:
            raise ValueError(
                "Verified label CSV must have these columns in order: "
                + ",".join(LABEL_FIELDS)
            )
        for line_number, row in enumerate(reader, start=2):
            raw_id = (row.get("reading_id") or "").strip()
            try:
                reading_id = int(raw_id)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid reading_id on label CSV line {line_number}.") from exc
            if reading_id <= 0 or reading_id in seen_reading_ids:
                raise ValueError(f"Invalid or duplicate reading_id on label CSV line {line_number}.")
            seen_reading_ids.add(reading_id)
            verified = (row.get("label_verified") or "").strip().lower()
            if verified not in {"true", "false"}:
                raise ValueError(
                    f"label_verified must be true or false on label CSV line {line_number}."
                )
            if verified == "false":
                unverified_count += 1
                continue
            sample_id = (row.get("sample_id") or "").strip()
            category = (row.get("water_category") or "").strip()
            if not sample_id:
                raise ValueError(f"Missing sample_id on label CSV line {line_number}.")
            if category not in CLASS_NAMES:
                raise ValueError(
                    f"Unsupported water_category on label CSV line {line_number}; "
                    f"use one of {', '.join(CLASS_NAMES)}."
                )
            labels[reading_id] = {"sample_id": sample_id, "water_category": category}
    return labels, unverified_count


def class_balance_status(counts: dict[str, int]) -> tuple[str, float | None]:
    values = [counts.get(name, 0) for name in CLASS_NAMES]
    if not any(values):
        return "not_assessable_no_verified_labels", None
    if any(value == 0 for value in values):
        return "missing_category", None
    ratio = max(values) / min(values)
    return ("imbalanced_over_2_to_1" if ratio > 2 else "no_class_exceeds_2_to_1"), ratio


def export_dataset(
    labels_path: Path | None = None,
    output_path: Path | None = None,
    report_path: Path | None = None,
) -> dict[str, Any]:
    labels_path = labels_path or DATA_DIR / "verified_labels.csv"
    output_path = output_path or DATA_DIR / "ml_dataset.csv"
    report_path = report_path or DATA_DIR / "ml_dataset_report.json"
    readings = fetch_readings()
    labels, unverified_count = read_verified_labels(labels_path)
    by_id = {int(row["id"]): row for row in readings}

    candidates: dict[int, dict[str, float]] = {}
    for row in readings:
        values = valid_feature_values(row)
        if values is not None:
            candidates[int(row["id"])] = values

    rows = []
    unmatched_labels = 0
    labels_for_invalid_readings = 0
    sample_category: dict[str, str] = {}
    for reading_id, label in labels.items():
        if reading_id not in by_id:
            unmatched_labels += 1
            continue
        values = candidates.get(reading_id)
        if values is None:
            labels_for_invalid_readings += 1
            continue
        sample_id = label["sample_id"]
        category = label["water_category"]
        previous_category = sample_category.setdefault(sample_id, category)
        if previous_category != category:
            raise ValueError(
                f"Verified labels disagree for physical sample_id {sample_id!r}."
            )
        rows.append({
            "reading_id": reading_id,
            "sample_id": sample_id,
            **values,
            "water_category": category,
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=DATASET_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    class_counts = Counter(row["water_category"] for row in rows)
    physical_counts = {
        name: len({row["sample_id"] for row in rows if row["water_category"] == name})
        for name in CLASS_NAMES
    }
    complete_counts = {name: class_counts.get(name, 0) for name in CLASS_NAMES}
    balance_status, imbalance_ratio = class_balance_status(complete_counts)
    report = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_table": "sensor_readings",
        "label_file": str(labels_path),
        "label_file_present": labels_path.is_file(),
        "dataset_file": str(output_path),
        "dataset_size": len(rows),
        "source_reading_count": len(readings),
        "valid_calibrated_candidates": len(candidates),
        "unlabeled_candidate_readings": sum(reading_id not in labels for reading_id in candidates),
        "unverified_label_rows": unverified_count,
        "unmatched_label_rows": unmatched_labels,
        "labels_for_invalid_readings": labels_for_invalid_readings,
        "class_counts": complete_counts,
        "physical_sample_counts": physical_counts,
        "physical_sample_count": len({row["sample_id"] for row in rows}),
        "class_balance_status": balance_status,
        "class_imbalance_ratio_max_to_min": imbalance_ratio,
        "feature_names": list(FEATURE_NAMES),
        "feature_descriptions": FEATURE_DESCRIPTIONS,
        "target_name": "water_category",
        "dataset_columns": list(DATASET_FIELDS),
        "training_test_split": "not prepared; run python -m app.ml.training prepare after labels are verified",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export", help="export valid readings with verified labels")
    export_parser.add_argument("--labels", type=Path, default=DATA_DIR / "verified_labels.csv")
    export_parser.add_argument("--output", type=Path, default=DATA_DIR / "ml_dataset.csv")
    export_parser.add_argument("--report", type=Path, default=DATA_DIR / "ml_dataset_report.json")
    args = parser.parse_args(argv)
    try:
        report = export_dataset(args.labels, args.output, args.report)
    except Exception as exc:
        print(f"Dataset export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
