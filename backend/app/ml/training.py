"""Prepare a grouped stratified split and compare Random Forest with XGBoost."""

import argparse
import csv
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.ml.service import (
    ARTIFACT_DIR,
    CLASS_NAMES,
    CLASS_TO_CODE,
    DATA_DIR,
    FEATURE_DESCRIPTIONS,
    FEATURE_NAMES,
)


DATASET_FIELDS = ("reading_id", "sample_id", *FEATURE_NAMES, "water_category")


def load_dataset(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"Dataset file does not exist: {path}")
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != DATASET_FIELDS:
            raise ValueError(
                "Dataset CSV must have these columns in order: "
                + ",".join(DATASET_FIELDS)
            )
        rows = []
        seen_reading_ids = set()
        sample_categories: dict[str, str] = {}
        for line_number, row in enumerate(reader, start=2):
            try:
                reading_id = int(row["reading_id"])
                values = {name: float(row[name]) for name in FEATURE_NAMES}
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid feature or reading_id on dataset line {line_number}.") from exc
            sample_id = (row.get("sample_id") or "").strip()
            category = (row.get("water_category") or "").strip()
            if reading_id <= 0 or reading_id in seen_reading_ids:
                raise ValueError(f"Invalid or duplicate reading_id on dataset line {line_number}.")
            if not sample_id or category not in CLASS_TO_CODE:
                raise ValueError(f"Invalid sample_id or water_category on dataset line {line_number}.")
            if not all(math.isfinite(value) for value in values.values()):
                raise ValueError(f"Non-finite feature on dataset line {line_number}.")
            if values["tds_ppm"] < 0 or not -55 <= values["temperature_c"] <= 125:
                raise ValueError(f"Out-of-range sensor feature on dataset line {line_number}.")
            if not 0 <= values["cloudiness_index"] <= 100:
                raise ValueError(f"Out-of-range cloudiness index on dataset line {line_number}.")
            previous_category = sample_categories.setdefault(sample_id, category)
            if previous_category != category:
                raise ValueError(f"Sample {sample_id!r} has conflicting category labels.")
            seen_reading_ids.add(reading_id)
            rows.append({
                "reading_id": reading_id,
                "sample_id": sample_id,
                **values,
                "water_category": category,
            })
    return rows


def grouped_stratified_split(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if not rows:
        raise ValueError("No labeled rows are available; export verified labels before preparing a split.")
    class_counts = Counter(row["water_category"] for row in rows)
    group_categories: dict[str, str] = {}
    for row in rows:
        group_categories[row["sample_id"]] = row["water_category"]
    group_counts = Counter(group_categories.values())
    missing = [name for name in CLASS_NAMES if class_counts.get(name, 0) == 0]
    insufficient_groups = [name for name in CLASS_NAMES if group_counts.get(name, 0) < 2]
    if missing:
        raise ValueError("A stratified split needs labeled rows for all three categories: " + ", ".join(missing))
    if insufficient_groups:
        raise ValueError(
            "A leakage-safe test split needs at least two physical samples per category: "
            + ", ".join(insufficient_groups)
        )

    try:
        from sklearn.model_selection import StratifiedGroupKFold
    except ImportError as exc:
        raise RuntimeError(
            "scikit-learn is required. Install backend dependencies with: pip install -r requirements.txt"
        ) from exc

    n_splits = min(5, min(group_counts[name] for name in CLASS_NAMES))
    features = [[row[name] for name in FEATURE_NAMES] for row in rows]
    labels = [CLASS_TO_CODE[row["water_category"]] for row in rows]
    groups = [row["sample_id"] for row in rows]
    full_distribution = Counter(labels)
    candidates = []
    splitter = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=42)
    for train_indices, test_indices in splitter.split(features, labels, groups):
        train_labels = [labels[index] for index in train_indices]
        test_labels = [labels[index] for index in test_indices]
        if set(train_labels) != set(range(len(CLASS_NAMES))) or set(test_labels) != set(range(len(CLASS_NAMES))):
            continue
        target_counts = Counter(test_labels)
        test_size_error = abs(len(test_indices) / len(rows) - 0.2)
        distribution_error = sum(
            abs(target_counts.get(code, 0) / len(test_indices) - full_distribution[code] / len(rows))
            for code in range(len(CLASS_NAMES))
        )
        candidates.append(((test_size_error, distribution_error), train_indices, test_indices))
    if not candidates:
        raise ValueError(
            "Could not form a grouped stratified split that keeps all categories in both sets. "
            "Collect more independently labeled physical samples per category."
        )
    _, train_indices, test_indices = min(candidates, key=lambda item: item[0])
    train_rows = [rows[int(index)] for index in train_indices]
    test_rows = [rows[int(index)] for index in test_indices]
    train_groups = {row["sample_id"] for row in train_rows}
    test_groups = {row["sample_id"] for row in test_rows}
    if train_groups & test_groups:
        raise RuntimeError("Internal split error: a physical sample appeared in both train and test.")

    split_report = {
        "strategy": "StratifiedGroupKFold; repeated readings stay with their physical sample",
        "folds_considered": n_splits,
        "train_size": len(train_rows),
        "test_size": len(test_rows),
        "train_physical_samples": len(train_groups),
        "test_physical_samples": len(test_groups),
        "train_class_counts": dict(Counter(row["water_category"] for row in train_rows)),
        "test_class_counts": dict(Counter(row["water_category"] for row in test_rows)),
    }
    return train_rows, test_rows, split_report


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=DATASET_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def prepare_datasets(dataset_path: Path | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    dataset_path = dataset_path or DATA_DIR / "ml_dataset.csv"
    rows = load_dataset(dataset_path)
    train_rows, test_rows, split_report = grouped_stratified_split(rows)
    _write_csv(DATA_DIR / "ml_train.csv", train_rows)
    _write_csv(DATA_DIR / "ml_test.csv", test_rows)
    split_report["training_file"] = str(DATA_DIR / "ml_train.csv")
    split_report["test_file"] = str(DATA_DIR / "ml_test.csv")
    (DATA_DIR / "ml_split_report.json").write_text(
        json.dumps(split_report, indent=2) + "\n",
        encoding="utf-8",
    )
    return train_rows, test_rows, split_report


def train_models(dataset_path: Path | None = None) -> dict[str, Any]:
    rows = load_dataset(dataset_path or DATA_DIR / "ml_dataset.csv")
    train_rows, test_rows, split_report = grouped_stratified_split(rows)
    try:
        import joblib
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support
        from xgboost import XGBClassifier
    except ImportError as exc:
        raise RuntimeError(
            "scikit-learn, XGBoost, and joblib are required. Install backend dependencies with: "
            "pip install -r requirements.txt"
        ) from exc

    _write_csv(DATA_DIR / "ml_train.csv", train_rows)
    _write_csv(DATA_DIR / "ml_test.csv", test_rows)

    x_train = [[row[name] for name in FEATURE_NAMES] for row in train_rows]
    y_train = [CLASS_TO_CODE[row["water_category"]] for row in train_rows]
    x_test = [[row[name] for name in FEATURE_NAMES] for row in test_rows]
    y_test = [CLASS_TO_CODE[row["water_category"]] for row in test_rows]
    models = (
        ("Random Forest", RandomForestClassifier(
            n_estimators=300,
            class_weight="balanced_subsample",
            random_state=42,
            n_jobs=-1,
        )),
        ("XGBoost", XGBClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            objective="multi:softprob",
            num_class=len(CLASS_NAMES),
            eval_metric="mlogloss",
            random_state=42,
            n_jobs=1,
        )),
    )

    results = {}
    trained_models = {}
    for name, model in models:
        model.fit(x_train, y_train)
        predicted = [int(value) for value in model.predict(x_test)]
        precision, recall, f1, _ = precision_recall_fscore_support(
            y_test,
            predicted,
            labels=list(range(len(CLASS_NAMES))),
            average="macro",
            zero_division=0,
        )
        metrics = {
            "accuracy": float(accuracy_score(y_test, predicted)),
            "precision": float(precision),
            "recall": float(recall),
            "f1_score": float(f1),
            "averaging": "macro",
            "confusion_matrix": confusion_matrix(
                y_test,
                predicted,
                labels=list(range(len(CLASS_NAMES))),
            ).astype(int).tolist(),
            "confusion_matrix_class_order": list(CLASS_NAMES),
            "test_samples": len(test_rows),
        }
        results[name] = {"metrics": metrics}
        trained_models[name] = model

    preferred = max(
        (name for name, _ in models),
        key=lambda name: (results[name]["metrics"]["f1_score"], results[name]["metrics"]["accuracy"]),
    )
    trained_at = datetime.now(timezone.utc)
    model_version = "1.0.0-" + trained_at.strftime("%Y%m%dT%H%M%S%fZ")
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    registry_models = {}
    for name, model in trained_models.items():
        artifact_slug = "random_forest" if name == "Random Forest" else "xgboost"
        artifact_name = f"{artifact_slug}-{model_version}.joblib"
        joblib.dump(model, ARTIFACT_DIR / artifact_name)
        registry_models[name] = {
            "status": "trained",
            "artifact": artifact_name,
            "metrics": results[name]["metrics"],
        }

    class_counts = {name: sum(row["water_category"] == name for row in rows) for name in CLASS_NAMES}
    manifest = {
        "schema_version": 1,
        "model_version": model_version,
        "training_date": trained_at.isoformat(),
        "dataset_size": len(rows),
        "class_counts": class_counts,
        "physical_sample_count": len({row["sample_id"] for row in rows}),
        "training_size": len(train_rows),
        "test_size": len(test_rows),
        "feature_names": list(FEATURE_NAMES),
        "feature_descriptions": FEATURE_DESCRIPTIONS,
        "class_names": list(CLASS_NAMES),
        "class_to_code": CLASS_TO_CODE,
        "split": split_report,
        "models": registry_models,
        "preferred_model": preferred,
        "selection_basis": "Highest held-out macro F1-score; accuracy breaks ties.",
    }
    registry_path = ARTIFACT_DIR / "registry.json"
    temporary_path = ARTIFACT_DIR / "registry.json.tmp"
    temporary_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(registry_path)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare", help="create group-separated train and test CSVs")
    prepare_parser.add_argument("--dataset", type=Path, default=DATA_DIR / "ml_dataset.csv")
    train_parser = subparsers.add_parser("train", help="train and compare both classifiers")
    train_parser.add_argument("--dataset", type=Path, default=DATA_DIR / "ml_dataset.csv")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            _, _, report = prepare_datasets(args.dataset)
        else:
            report = train_models(args.dataset)
    except Exception as exc:
        print(f"ML {args.command} failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
