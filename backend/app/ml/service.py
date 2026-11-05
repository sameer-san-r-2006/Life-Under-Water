"""Model metadata, validated live inputs, and prediction persistence."""

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.database import get_connection


FEATURE_NAMES = ("tds_ppm", "temperature_c", "cloudiness_index")
FEATURE_DESCRIPTIONS = {
    "tds_ppm": "Calibrated TDS measurement in ppm.",
    "temperature_c": "Water temperature in degrees Celsius.",
    "cloudiness_index": "Legacy relative 0-100 sensor index; not calibrated NTU.",
}
CLASS_NAMES = ("Household", "Agricultural", "Aquatic")
CLASS_TO_CODE = {name: index for index, name in enumerate(CLASS_NAMES)}
MODEL_NAMES = ("Random Forest", "XGBoost")
BACKEND_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BACKEND_DIR / "data"
ARTIFACT_DIR = Path(os.getenv("ML_ARTIFACTS_DIR", str(BACKEND_DIR / "ml_artifacts")))
REGISTRY_PATH = ARTIFACT_DIR / "registry.json"
DATASET_REPORT_PATH = DATA_DIR / "ml_dataset_report.json"
_LOADED_MODEL_CACHE: tuple[str, Any] | None = None


def finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_registry() -> dict[str, Any] | None:
    registry = _read_json(REGISTRY_PATH)
    if not registry or not isinstance(registry.get("models"), dict):
        return None
    return registry


def model_comparison() -> dict[str, Any]:
    registry = load_registry()
    if not registry:
        return {
            "status": "untrained",
            "models": [{"name": name, "status": "untrained", "metrics": None} for name in MODEL_NAMES],
            "preferred_model": None,
            "selection_basis": None,
        }

    registered = registry["models"]
    models = []
    for name in MODEL_NAMES:
        entry = registered.get(name)
        entry = entry if isinstance(entry, dict) else {}
        metrics = entry.get("metrics") if isinstance(entry.get("metrics"), dict) else None
        models.append({
            "name": name,
            "status": "trained" if metrics else "untrained",
            "metrics": metrics,
        })
    return {
        "status": "trained" if any(item["metrics"] for item in models) else "untrained",
        "models": models,
        "preferred_model": registry.get("preferred_model"),
        "selection_basis": registry.get("selection_basis"),
    }


def model_information() -> dict[str, Any]:
    registry = load_registry()
    dataset_report = _read_json(DATASET_REPORT_PATH) or {}
    if registry:
        preferred = registry.get("preferred_model")
        selected = registry["models"].get(preferred, {}) if isinstance(preferred, str) else {}
        artifact_name = selected.get("artifact") if isinstance(selected, dict) else None
        artifact_exists = isinstance(artifact_name, str) and (ARTIFACT_DIR / Path(artifact_name).name).is_file()
        status = "ready" if artifact_exists else "artifact_unavailable"
        return {
            "status": status,
            "model_version": registry.get("model_version"),
            "training_date": registry.get("training_date"),
            "dataset_size": registry.get("dataset_size"),
            "class_counts": registry.get("class_counts"),
            "physical_sample_count": registry.get("physical_sample_count"),
            "feature_names": list(FEATURE_NAMES),
            "feature_descriptions": FEATURE_DESCRIPTIONS,
            "class_names": list(CLASS_NAMES),
            "preferred_model": preferred,
        }

    has_dataset_report = bool(dataset_report)
    return {
        "status": "untrained",
        "model_version": None,
        "training_date": None,
        "dataset_size": dataset_report.get("dataset_size") if has_dataset_report else None,
        "class_counts": dataset_report.get("class_counts") if has_dataset_report else None,
        "physical_sample_count": dataset_report.get("physical_sample_count") if has_dataset_report else None,
        "feature_names": list(FEATURE_NAMES),
        "feature_descriptions": FEATURE_DESCRIPTIONS,
        "class_names": list(CLASS_NAMES),
        "preferred_model": None,
        "valid_calibrated_candidates": dataset_report.get("valid_calibrated_candidates") if has_dataset_report else None,
        "label_file_present": dataset_report.get("label_file_present") if has_dataset_report else None,
    }


def _input_status(row: dict[str, Any] | None) -> tuple[dict[str, dict[str, Any]], dict[str, float] | None]:
    if not row:
        return {
            "tds_ppm": {"value": None, "valid": False, "status": "unavailable"},
            "temperature_c": {"value": None, "valid": False, "status": "unavailable"},
            "cloudiness_index": {"value": None, "valid": False, "status": "unavailable"},
        }, None

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

    tds_status = "valid" if tds_valid else (
        "not_calibrated" if row.get("tds_source") == "demo_estimate" else "invalid" if tds is not None else "unavailable"
    )
    inputs = {
        "tds_ppm": {"value": tds, "valid": tds_valid, "status": tds_status},
        "temperature_c": {
            "value": temperature,
            "valid": temperature_valid,
            "status": "valid" if temperature_valid else "invalid" if temperature is not None else "unavailable",
        },
        "cloudiness_index": {
            "value": cloudiness,
            "valid": cloudiness_valid,
            "status": "valid" if cloudiness_valid else "invalid" if cloudiness is not None else "unavailable",
        },
    }
    if not all(item["valid"] for item in inputs.values()):
        return inputs, None
    return inputs, {
        "tds_ppm": tds,
        "temperature_c": temperature,
        "cloudiness_index": cloudiness,
    }


def classify_values(
    *, temperature_c: Any, tds_ppm: Any, cloudiness_index: Any
) -> dict[str, Any]:
    """Run the existing verified-label classifier without persisting a future result."""
    temperature = finite_number(temperature_c)
    tds = finite_number(tds_ppm)
    cloudiness = finite_number(cloudiness_index)
    if (
        temperature is None or not -55 <= temperature <= 125
        or tds is None or tds < 0
        or cloudiness is None or not 0 <= cloudiness <= 100
    ):
        return {
            "available": False,
            "reason": "Predicted temperature, calibrated TDS, and a valid legacy relative index are required.",
        }

    registry = load_registry()
    if not registry:
        return {"available": False, "reason": "No trained verified-label classifier is registered."}
    model, model_error = _load_preferred_model(registry)
    if model is None:
        return {"available": False, "reason": model_error or "The trained classifier is unavailable."}

    feature_vector = [[tds, temperature, cloudiness]]
    try:
        code = int(model.predict(feature_vector)[0])
    except Exception as exc:
        return {"available": False, "reason": f"The classifier could not assess the forecast ({type(exc).__name__})."}
    if not 0 <= code < len(CLASS_NAMES):
        return {"available": False, "reason": "The classifier returned an unsupported experimental category."}

    confidence = None
    if callable(getattr(model, "predict_proba", None)):
        try:
            probabilities = model.predict_proba(feature_vector)[0]
            model_classes = list(getattr(model, "classes_", range(len(probabilities))))
            for model_class, probability in zip(model_classes, probabilities):
                if int(model_class) == code:
                    confidence = finite_number(probability)
                    break
        except Exception:
            confidence = None
    return {
        "available": True,
        "predicted_category": CLASS_NAMES[code],
        "confidence": confidence,
        "model_name": registry.get("preferred_model"),
        "model_version": registry.get("model_version"),
    }


def _latest_sensor_row() -> dict[str, Any] | None:
    with get_connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id, recorded_at, temperature_c, temperature_valid,
                       tds_ppm, tds_valid, tds_source, tds_calibration_status,
                       tds_conversion_method, cloudiness_index, cloudiness_valid
                FROM sensor_readings
                ORDER BY recorded_at DESC, id DESC
                LIMIT 1
                """
            )
            return cursor.fetchone()


def _prediction_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "prediction_timestamp": row.get("predicted_at").isoformat() if row.get("predicted_at") else None,
        "sensor_recorded_at": row.get("sensor_recorded_at").isoformat() if row.get("sensor_recorded_at") else None,
        "inputs": {
            "tds_ppm": {"value": row.get("tds_ppm"), "valid": True, "status": "valid"},
            "temperature_c": {"value": row.get("temperature_c"), "valid": True, "status": "valid"},
            "cloudiness_index": {"value": row.get("cloudiness_index"), "valid": True, "status": "valid"},
        },
        "predicted_category": row.get("predicted_category"),
        "model_name": row.get("model_name"),
        "model_version": row.get("model_version"),
        "model_scores": row.get("model_scores"),
    }


def latest_prediction() -> dict[str, Any]:
    with get_connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id, predicted_at, sensor_recorded_at, tds_ppm,
                       temperature_c, cloudiness_index, predicted_category,
                       model_name, model_version, model_scores
                FROM ml_predictions
                ORDER BY predicted_at DESC, id DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
    return {"available": bool(row), "prediction": _prediction_from_row(row) if row else None}


def prediction_history(limit: int = 50) -> dict[str, Any]:
    with get_connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id, predicted_at, sensor_recorded_at, tds_ppm,
                       temperature_c, cloudiness_index, predicted_category,
                       model_name, model_version, model_scores
                FROM ml_predictions
                ORDER BY predicted_at DESC, id DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()
    return {"predictions": [_prediction_from_row(row) for row in rows], "count": len(rows)}


def _load_preferred_model(registry: dict[str, Any]) -> tuple[Any | None, str | None]:
    global _LOADED_MODEL_CACHE
    preferred = registry.get("preferred_model")
    models = registry.get("models")
    entry = models.get(preferred) if isinstance(preferred, str) and isinstance(models, dict) else None
    artifact_name = entry.get("artifact") if isinstance(entry, dict) else None
    if not isinstance(artifact_name, str) or Path(artifact_name).name != artifact_name:
        return None, "The selected model artifact is unavailable."
    artifact_path = ARTIFACT_DIR / artifact_name
    if not artifact_path.is_file():
        return None, "The selected model artifact is unavailable."
    cache_key = f"{registry.get('model_version')}:{preferred}:{artifact_path.resolve()}"
    if _LOADED_MODEL_CACHE and _LOADED_MODEL_CACHE[0] == cache_key:
        return _LOADED_MODEL_CACHE[1], None
    try:
        import joblib

        model = joblib.load(artifact_path)
    except Exception as exc:
        return None, f"The trained model could not be loaded ({type(exc).__name__})."
    _LOADED_MODEL_CACHE = (cache_key, model)
    return model, None


def _persist_prediction(
    sensor_row: dict[str, Any],
    inputs: dict[str, float],
    category: str,
    model_name: str,
    model_version: str,
    model_scores: dict[str, float] | None,
) -> dict[str, Any]:
    with get_connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO ml_predictions (
                    reading_id, sensor_recorded_at, tds_ppm, temperature_c,
                    cloudiness_index, predicted_category, model_name,
                    model_version, model_scores
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (reading_id, model_name, model_version) DO NOTHING
                RETURNING id, predicted_at, sensor_recorded_at, tds_ppm,
                          temperature_c, cloudiness_index, predicted_category,
                          model_name, model_version, model_scores
                """,
                (
                    sensor_row["id"],
                    sensor_row["recorded_at"],
                    inputs["tds_ppm"],
                    inputs["temperature_c"],
                    inputs["cloudiness_index"],
                    category,
                    model_name,
                    model_version,
                    Jsonb(model_scores) if model_scores is not None else None,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    """
                    SELECT id, predicted_at, sensor_recorded_at, tds_ppm,
                           temperature_c, cloudiness_index, predicted_category,
                           model_name, model_version, model_scores
                    FROM ml_predictions
                    WHERE reading_id = %s AND model_name = %s AND model_version = %s
                    """,
                    (sensor_row["id"], model_name, model_version),
                )
                row = cursor.fetchone()
    if not row:
        raise RuntimeError("Could not save or retrieve the prediction record.")
    return _prediction_from_row(row)


def run_latest_prediction() -> dict[str, Any]:
    sensor_row = _latest_sensor_row()
    inputs, valid_values = _input_status(sensor_row)
    registry = load_registry()
    if not registry:
        return {
            "available": False,
            "status": "untrained",
            "message": "Prediction unavailable: no trained model is registered.",
            "inputs": inputs,
        }
    if sensor_row is None or valid_values is None:
        return {
            "available": False,
            "status": "invalid_inputs",
            "message": "Prediction unavailable: missing or invalid sensor reading. A calibrated TDS value and valid temperature and cloudiness readings are required.",
            "inputs": inputs,
        }

    model, model_error = _load_preferred_model(registry)
    if model is None:
        return {
            "available": False,
            "status": "model_unavailable",
            "message": f"Prediction unavailable: {model_error}",
            "inputs": inputs,
        }

    feature_vector = [[valid_values[name] for name in FEATURE_NAMES]]
    code = int(model.predict(feature_vector)[0])
    if not 0 <= code < len(CLASS_NAMES):
        return {
            "available": False,
            "status": "model_output_invalid",
            "message": "Prediction unavailable: the model returned an unsupported category.",
            "inputs": inputs,
        }
    category = CLASS_NAMES[code]
    scores = None
    if callable(getattr(model, "predict_proba", None)):
        probabilities = model.predict_proba(feature_vector)[0]
        model_classes = list(getattr(model, "classes_", range(len(probabilities))))
        parsed_scores: dict[str, float] = {}
        for model_class, probability in zip(model_classes, probabilities):
            try:
                class_code = int(model_class)
            except (TypeError, ValueError, OverflowError):
                continue
            score = finite_number(probability)
            if 0 <= class_code < len(CLASS_NAMES) and score is not None:
                parsed_scores[CLASS_NAMES[class_code]] = score
        scores = parsed_scores or None

    selected = registry.get("preferred_model")
    model_version = registry.get("model_version")
    if selected not in MODEL_NAMES or not isinstance(model_version, str):
        return {
            "available": False,
            "status": "model_unavailable",
            "message": "Prediction unavailable: model registration metadata is incomplete.",
            "inputs": inputs,
        }

    prediction = _persist_prediction(
        sensor_row,
        valid_values,
        category,
        selected,
        model_version,
        scores,
    )
    return {
        "available": True,
        "status": "ready",
        "message": None,
        "prediction": prediction,
        "inputs": prediction["inputs"],
    }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
