"""Database-backed Random Forest and XGBoost sensor forecasts."""

import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from psycopg.rows import dict_row

from app.database import get_connection


FORECAST_LOOKBACK_DAYS = 30
FORECAST_CHART_DAYS = 7
MAX_LAGS = 3
TARGET_MATCH_TOLERANCE_MINUTES = 30
TDS_CALIBRATION_METHOD = os.getenv("TDS_CALIBRATION_METHOD", "").strip() or None

PARAMETERS = {
    "temperature_c": {"label": "Temperature", "unit": "°C", "minimum": -55.0, "maximum": 125.0},
    "tds_ppm": {"label": "TDS", "unit": "ppm", "minimum": 0.0, "maximum": None},
    "turbidity_ntu": {"label": "Turbidity", "unit": "NTU", "minimum": 0.0, "maximum": None},
}
MODEL_NAMES = ("Random Forest", "XGBoost")
MODEL_ALGORITHMS = {
    "Random Forest": "sklearn.ensemble.RandomForestRegressor",
    "XGBoost": "xgboost.XGBRegressor",
}


def _to_utc(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _tds_valid_predicate(alias: str = "r") -> str:
    return f"""{alias}.tds_valid IS TRUE
        AND {alias}.tds_source = 'calibrated'
        AND {alias}.tds_calibration_status = 'calibrated'
        AND COALESCE({alias}.tds_conversion_method, '') <> 'legacy_demo_lookup_v1'
        AND (%s::TEXT IS NULL OR {alias}.tds_conversion_method = %s)
        AND {alias}.tds_ppm IS NOT NULL
        AND {alias}.tds_ppm >= 0
        AND {alias}.tds_ppm < 'Infinity'::DOUBLE PRECISION
        AND {alias}.tds_ppm <> 'NaN'::DOUBLE PRECISION"""


def _fetch_hourly_history(cutoff: datetime, now: datetime) -> list[dict[str, Any]]:
    tds_filter = _tds_valid_predicate("r")
    tds_parameters = (TDS_CALIBRATION_METHOD, TDS_CALIBRATION_METHOD)
    parameters = (cutoff, now) + tds_parameters * 3
    with get_connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"""
                WITH readings AS (
                    SELECT
                        date_trunc('hour', r.recorded_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' AS bucket,
                        r.temperature_c, r.temperature_valid,
                        r.tds_ppm, r.tds_valid, r.tds_source, r.tds_calibration_status, r.tds_conversion_method,
                        r.turbidity_ntu, r.turbidity_valid, r.turbidity_calibration_status
                    FROM sensor_readings r
                    WHERE r.recorded_at >= %s AND r.recorded_at <= %s
                )
                SELECT bucket AS recorded_at,
                    AVG(temperature_c) FILTER (
                        WHERE temperature_valid IS TRUE AND temperature_c BETWEEN -55 AND 125
                    ) AS temperature_c,
                    COUNT(*) FILTER (
                        WHERE temperature_valid IS TRUE AND temperature_c BETWEEN -55 AND 125
                    ) AS temperature_samples,
                    AVG(tds_ppm) FILTER (WHERE {tds_filter}) AS tds_ppm,
                    COUNT(*) FILTER (WHERE {tds_filter}) AS tds_samples,
                    AVG(turbidity_ntu) FILTER (
                        WHERE turbidity_valid IS TRUE AND turbidity_calibration_status = 'calibrated'
                          AND turbidity_ntu IS NOT NULL AND turbidity_ntu >= 0
                          AND turbidity_ntu < 'Infinity'::DOUBLE PRECISION
                          AND turbidity_ntu <> 'NaN'::DOUBLE PRECISION
                    ) AS turbidity_ntu,
                    COUNT(*) FILTER (
                        WHERE turbidity_valid IS TRUE AND turbidity_calibration_status = 'calibrated'
                          AND turbidity_ntu IS NOT NULL AND turbidity_ntu >= 0
                          AND turbidity_ntu < 'Infinity'::DOUBLE PRECISION
                          AND turbidity_ntu <> 'NaN'::DOUBLE PRECISION
                    ) AS turbidity_samples
                FROM readings r
                GROUP BY r.bucket
                HAVING COUNT(*) FILTER (
                    WHERE temperature_valid IS TRUE AND temperature_c BETWEEN -55 AND 125
                ) > 0 OR COUNT(*) FILTER (WHERE {tds_filter}) > 0 OR COUNT(*) FILTER (
                    WHERE turbidity_valid IS TRUE AND turbidity_calibration_status = 'calibrated'
                      AND turbidity_ntu IS NOT NULL AND turbidity_ntu >= 0
                      AND turbidity_ntu < 'Infinity'::DOUBLE PRECISION
                      AND turbidity_ntu <> 'NaN'::DOUBLE PRECISION
                ) > 0
                ORDER BY r.bucket
                """,
                parameters,
            )
            return cursor.fetchall()


def _fetch_latest_valid() -> dict[str, Any]:
    tds_filter = _tds_valid_predicate("r")
    parameters = (TDS_CALIBRATION_METHOD, TDS_CALIBRATION_METHOD)
    with get_connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"""
                SELECT
                    temperature.recorded_at AS temperature_at, temperature.temperature_c,
                    tds.recorded_at AS tds_at, tds.tds_ppm,
                    turbidity.recorded_at AS turbidity_at, turbidity.turbidity_ntu
                FROM (VALUES (1)) AS seed(value)
                LEFT JOIN LATERAL (
                    SELECT recorded_at, temperature_c FROM sensor_readings
                    WHERE temperature_valid IS TRUE AND temperature_c BETWEEN -55 AND 125
                    ORDER BY recorded_at DESC, id DESC LIMIT 1
                ) temperature ON TRUE
                LEFT JOIN LATERAL (
                    SELECT r.recorded_at, r.tds_ppm FROM sensor_readings r
                    WHERE {tds_filter}
                    ORDER BY r.recorded_at DESC, r.id DESC LIMIT 1
                ) tds ON TRUE
                LEFT JOIN LATERAL (
                    SELECT recorded_at, turbidity_ntu FROM sensor_readings
                    WHERE turbidity_valid IS TRUE AND turbidity_calibration_status = 'calibrated'
                      AND turbidity_ntu IS NOT NULL AND turbidity_ntu >= 0
                      AND turbidity_ntu < 'Infinity'::DOUBLE PRECISION
                      AND turbidity_ntu <> 'NaN'::DOUBLE PRECISION
                    ORDER BY recorded_at DESC, id DESC LIMIT 1
                ) turbidity ON TRUE
                """,
                parameters,
            )
            return cursor.fetchone() or {}


def _valid_hourly_points(rows: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    value_key = "turbidity_ntu" if metric == "turbidity_ntu" else metric
    samples_key = {
        "temperature_c": "temperature_samples",
        "tds_ppm": "tds_samples",
        "turbidity_ntu": "turbidity_samples",
    }[metric]
    points = []
    for row in rows:
        timestamp = _to_utc(row.get("recorded_at"))
        try:
            value = float(row.get(value_key))
            samples = int(row.get(samples_key) or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if timestamp is None or not math.isfinite(value) or samples <= 0:
            continue
        points.append({"timestamp": timestamp, "value": value, "samples": samples})
    return points


def _model_factory(name: str) -> tuple[Callable[[], Any] | None, str | None]:
    try:
        if name == "Random Forest":
            from sklearn.ensemble import RandomForestRegressor

            return lambda: RandomForestRegressor(
                n_estimators=100,
                min_samples_leaf=1,
                random_state=42,
                n_jobs=1,
            ), None
        if name == "XGBoost":
            from xgboost import XGBRegressor

            return lambda: XGBRegressor(
                n_estimators=100,
                max_depth=3,
                learning_rate=0.05,
                objective="reg:squarederror",
                random_state=42,
                n_jobs=1,
                verbosity=0,
            ), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    return None, "Unknown regression model."


def _training_examples(
    points: list[dict[str, Any]], horizon_hours: int, lag_count: int
) -> tuple[list[list[float]], list[float]]:
    features: list[list[float]] = []
    targets: list[float] = []
    tolerance = timedelta(minutes=TARGET_MATCH_TOLERANCE_MINUTES)
    for origin_index, origin in enumerate(points):
        if origin_index + 1 < lag_count:
            continue
        target_time = origin["timestamp"] + timedelta(hours=horizon_hours)
        future = [
            point for point in points[origin_index + 1:]
            if abs(point["timestamp"] - target_time) <= tolerance
        ]
        if not future:
            continue
        target = min(future, key=lambda point: abs(point["timestamp"] - target_time))
        history = points[origin_index - lag_count + 1:origin_index + 1]
        features.append([float(point["value"]) for point in reversed(history)])
        targets.append(float(target["value"]))
    return features, targets


def _unavailable_model(status: str, reason: str, training_samples: int = 0) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "predicted": None,
        "direction": None,
        "change": None,
        "training_samples": training_samples,
        "forecast_points": [],
    }


def _fit_model(
    name: str,
    points: list[dict[str, Any]],
    current: dict[str, Any] | None,
    target_at: datetime,
    horizon_hours: int,
    minimum: float,
    maximum: float | None,
) -> dict[str, Any]:
    factory, factory_error = _model_factory(name)
    if factory is None:
        return _unavailable_model("model_unavailable", f"{name} is unavailable: {factory_error}")

    usable_points = list(points)
    if current and current.get("timestamp") and current.get("value") is not None:
        timestamp = _to_utc(current["timestamp"])
        value = _finite_number(current.get("value"))
        if timestamp is not None and value is not None and all(p["timestamp"] != timestamp for p in usable_points):
            usable_points.append({"timestamp": timestamp, "value": value, "samples": 1})
    usable_points.sort(key=lambda point: point["timestamp"])
    if not usable_points:
        return _unavailable_model("no_valid_data", "No valid historical readings are available.")

    candidate: tuple[int, list[list[float]], list[float]] | None = None
    for lag_count in range(min(MAX_LAGS, len(usable_points)), 0, -1):
        features, targets = _training_examples(usable_points, horizon_hours, lag_count)
        if features:
            candidate = (lag_count, features, targets)
            break
    if candidate is None:
        return _unavailable_model(
            "insufficient_training_data",
            f"Not enough historical readings are separated by the selected {horizon_hours}-hour horizon to train {name}.",
        )

    lag_count, features, targets = candidate
    if len(usable_points) < lag_count:
        return _unavailable_model("no_valid_data", "Not enough valid readings are available for model input.", len(targets))
    latest_features = [float(point["value"]) for point in reversed(usable_points[-lag_count:])]
    try:
        model = factory()
        model.fit(features, targets)
        predicted = float(model.predict([latest_features])[0])
    except Exception as exc:
        return _unavailable_model(
            "model_unavailable",
            f"{name} could not be trained for this parameter and horizon ({type(exc).__name__}).",
            len(targets),
        )

    if not math.isfinite(predicted) or predicted < minimum or (maximum is not None and predicted > maximum):
        return _unavailable_model(
            "invalid_prediction",
            f"{name} returned a value outside the parameter's valid sensor range.",
            len(targets),
        )
    current_value = _finite_number(current.get("value")) if current else None
    difference = predicted - current_value if current_value is not None else None
    direction = "increasing" if difference is not None and difference > 0 else (
        "decreasing" if difference is not None and difference < 0 else "unchanged" if difference == 0 else None
    )
    return {
        "status": "available",
        "reason": None,
        "predicted": predicted,
        "direction": direction,
        "change": difference,
        "training_samples": len(targets),
        "lag_count": lag_count,
        "forecast_points": [{"timestamp": target_at.isoformat(), "value": predicted}],
    }


def _finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def forecast_series(
    metric: str,
    history: list[dict[str, Any]],
    current: dict[str, Any] | None,
    target_at: datetime,
    horizon_hours: int,
) -> dict[str, Any]:
    """Train separate direct-horizon regressors using only valid values for this sensor."""
    metadata = PARAMETERS[metric]
    points = sorted(
        [
            {"timestamp": timestamp, "value": value, "samples": point.get("samples", 1)}
            for point in history
            if (timestamp := _to_utc(point.get("timestamp"))) is not None
            and (value := _finite_number(point.get("value"))) is not None
        ],
        key=lambda point: point["timestamp"],
    )
    normalized_current = None
    if current:
        timestamp = _to_utc(current.get("timestamp"))
        value = _finite_number(current.get("value"))
        if timestamp is not None and value is not None:
            normalized_current = {"timestamp": timestamp, "value": value}
    if normalized_current is None and points:
        normalized_current = {"timestamp": points[-1]["timestamp"], "value": points[-1]["value"]}

    reason = None
    if not points and normalized_current is None:
        reason = (
            "No calibrated turbidity (NTU) readings are available. The legacy relative sensor index is not used or converted."
            if metric == "turbidity_ntu"
            else f"No valid {metadata['label'].lower()} readings are available."
        )
    model_outputs = {}
    for name in MODEL_NAMES:
        if reason:
            model_outputs[name] = _unavailable_model("no_valid_data", reason)
        else:
            model_outputs[name] = _fit_model(
                name,
                points,
                normalized_current,
                target_at,
                horizon_hours,
                metadata["minimum"],
                metadata["maximum"],
            )

    available_models = [name for name, item in model_outputs.items() if item["status"] == "available"]
    status = "available" if available_models else "unavailable"
    if not reason and not available_models:
        reason = "; ".join(f"{name}: {item['reason']}" for name, item in model_outputs.items())
    return {
        "metric": metric,
        "label": metadata["label"],
        "unit": metadata["unit"],
        "status": status,
        "reason": reason,
        "current": (
            {"value": normalized_current["value"], "timestamp": normalized_current["timestamp"].isoformat()}
            if normalized_current else None
        ),
        "sample_count": len(points),
        "historical_points": [
            {"timestamp": point["timestamp"].isoformat(), "value": point["value"]}
            for point in points[-FORECAST_CHART_DAYS * 24:]
        ],
        "models": model_outputs,
    }


def forecast_readings(horizon_hours: int, *, now: datetime | None = None) -> dict[str, Any]:
    if not isinstance(horizon_hours, int) or not 1 <= horizon_hours <= 72:
        raise ValueError("Forecast horizon must be between 1 and 72 hours.")
    generated_at = now or datetime.now(timezone.utc)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    else:
        generated_at = generated_at.astimezone(timezone.utc)
    target_at = generated_at + timedelta(hours=horizon_hours)
    rows = _fetch_hourly_history(generated_at - timedelta(days=FORECAST_LOOKBACK_DAYS), generated_at)
    latest = _fetch_latest_valid()

    history_by_metric = {metric: _valid_hourly_points(rows, metric) for metric in PARAMETERS}
    current_fields = {
        "temperature_c": ("temperature_c", "temperature_at"),
        "tds_ppm": ("tds_ppm", "tds_at"),
        "turbidity_ntu": ("turbidity_ntu", "turbidity_at"),
    }
    parameters: dict[str, dict[str, Any]] = {}
    for metric, (value_key, timestamp_key) in current_fields.items():
        current = {"value": latest.get(value_key), "timestamp": latest.get(timestamp_key)}
        parameters[metric] = forecast_series(
            metric,
            history_by_metric[metric],
            current,
            target_at,
            horizon_hours,
        )

    model_status = []
    for name in MODEL_NAMES:
        available_parameters = [
            metric for metric, item in parameters.items()
            if item["models"][name]["status"] == "available"
        ]
        model_status.append({
            "name": name,
            "algorithm": MODEL_ALGORITHMS[name],
            "status": "available" if available_parameters else "unavailable",
            "available_parameters": available_parameters,
            "reason": None if available_parameters else "No parameter and horizon produced a usable model prediction.",
        })

    return {
        "generated_at": generated_at.isoformat(),
        "target_at": target_at.isoformat(),
        "horizon_hours": horizon_hours,
        "forecast_method": "direct-horizon Random Forest and XGBoost regression over historical sensor readings",
        "history_lookback_days": FORECAST_LOOKBACK_DAYS,
        "model_status": model_status,
        "parameters": parameters,
    }
