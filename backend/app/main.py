
import math
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row

from app.database import get_connection
from app.forecasting import forecast_readings
from app.serial_reader import reader


DEVICE_KEY = os.getenv("DEVICE_KEY", "life-under-water-node-01")
TDS_CALIBRATION_METHOD = os.getenv("TDS_CALIBRATION_METHOD", "").strip() or None
LEGACY_TDS_DEMO_METHOD = "legacy_demo_lookup_v1"
TDS_DEMO_LOOKUP_MIN_VOLTAGE = 0.200
TDS_DEMO_LOOKUP_MAX_VOLTAGE = 0.450


def positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


DEVICE_FRESHNESS_SECONDS = positive_env_int("DEVICE_FRESHNESS_SECONDS", 30)
SENSOR_FRESHNESS_SECONDS = positive_env_int("SENSOR_FRESHNESS_SECONDS", 30)


def finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def tds_sample_fields(reading: dict[str, Any]) -> dict[str, Any]:
    """Validate the firmware-produced ppm value while retaining its provenance."""
    voltage = finite_number(reading.get("tds_voltage_v"))
    voltage = voltage if voltage is not None and voltage >= 0 else None
    ppm = finite_number(reading.get("tds_ppm"))
    method = reading.get("tds_conversion_method")
    method = method.strip() if isinstance(method, str) and method.strip() else None
    attempted_ppm = "tds_ppm" in reading
    supplied_status = str(reading.get("tds_calibration_status") or "").strip().lower()
    supplied_source = str(reading.get("tds_source") or "").strip().lower()
    invalid_sample = attempted_ppm and (ppm is None or ppm < 0)
    legacy_method = method == LEGACY_TDS_DEMO_METHOD
    demo_provenance = legacy_method and supplied_source in {"", "demo_estimate"} and supplied_status in {"", "estimated"}
    demo_voltage_valid = voltage is None or TDS_DEMO_LOOKUP_MIN_VOLTAGE <= voltage <= TDS_DEMO_LOOKUP_MAX_VOLTAGE
    demo_estimate = demo_provenance and demo_voltage_valid
    calibrated = supplied_source == "calibrated" or supplied_status == "calibrated"
    method_matches = TDS_CALIBRATION_METHOD is None or method == TDS_CALIBRATION_METHOD
    valid_number = attempted_ppm and not invalid_sample and ppm is not None and ppm >= 0
    valid = valid_number and (
        demo_estimate
        or (
            calibrated
            and not legacy_method
            and method_matches
            and reading.get("tds_valid") is not False
        )
        or (
            not legacy_method
            and not calibrated
            and method_matches
            and reading.get("tds_valid") is True
            and supplied_status not in {"invalid", "unverified", "unavailable"}
        )
    )
    source = "demo_estimate" if valid and demo_estimate else "calibrated" if valid else "unavailable"

    return {
        "voltage": voltage,
        "ppm": ppm if valid else None,
        "legacy_demo_estimate_ppm": ppm if valid and demo_estimate else None,
        "valid": valid,
        "source": source,
        "calibration_status": (
            "estimated" if valid and demo_estimate
            else "calibrated" if valid
            else "invalid" if invalid_sample or (demo_provenance and not demo_voltage_valid) else supplied_status or "calibration_pending"
        ),
        "conversion_method": method,
    }


def tds_valid_sql(alias: str = "") -> str:
    """SQL predicate for canonical, valid TDS values, including tagged estimates."""
    prefix = f"{alias}." if alias else ""
    method_filter = "AND COALESCE(tds_conversion_method, '') = %s" if TDS_CALIBRATION_METHOD else ""
    return f"""
        {prefix}tds_valid IS TRUE
        AND {prefix}tds_ppm IS NOT NULL
        AND {prefix}tds_ppm >= 0
        AND {prefix}tds_ppm < 'Infinity'::DOUBLE PRECISION
        AND {prefix}tds_ppm <> 'NaN'::DOUBLE PRECISION
        AND (
            (
                {prefix}tds_source = 'demo_estimate'
                AND {prefix}tds_calibration_status = 'estimated'
                AND {prefix}tds_conversion_method = '{LEGACY_TDS_DEMO_METHOD}'
                AND (
                    {prefix}tds_voltage_v IS NULL
                    OR {prefix}tds_voltage_v BETWEEN {TDS_DEMO_LOOKUP_MIN_VOLTAGE} AND {TDS_DEMO_LOOKUP_MAX_VOLTAGE}
                )
            )
            OR (
                {prefix}tds_source = 'calibrated'
                AND {prefix}tds_calibration_status = 'calibrated'
                AND COALESCE({prefix}tds_conversion_method, '') <> '{LEGACY_TDS_DEMO_METHOD}'
                {method_filter.replace('tds_conversion_method', prefix + 'tds_conversion_method')}
            )
        )
    """


def raw_adc_number(value: Any) -> int | None:
    number = finite_number(value)
    if number is None or not number.is_integer() or not 0 <= number <= 32767:
        return None
    return int(number)


def cloudiness_sample_fields(reading: dict[str, Any]) -> dict[str, Any]:
    """Validate the firmware's legacy relative 0–100 sensor index as an index."""
    value = finite_number(reading.get("cloudiness_index"))
    valid = value is not None and 0 <= value <= 100 and reading.get("cloudiness_valid") is not False
    return {
        "index": value,
        "valid": valid,
        "status": "valid" if valid else "invalid" if "cloudiness_index" in reading else "waiting_for_data",
    }


def turbidity_sample_fields(reading: dict[str, Any]) -> dict[str, Any]:
    """Accept NTU only when the payload identifies it as a calibrated reading."""
    voltage = finite_number(reading.get("turbidity_voltage_v"))
    voltage = voltage if voltage is not None and voltage >= 0 else None
    ntu = finite_number(reading.get("turbidity_ntu"))
    calibration_status = str(reading.get("turbidity_calibration_status") or "").strip().lower()
    attempted = "turbidity_ntu" in reading
    valid = (
        attempted and ntu is not None and ntu >= 0
        and calibration_status == "calibrated"
        and reading.get("turbidity_valid") is not False
    )
    invalid = attempted and (
        (reading.get("turbidity_ntu") is not None and (ntu is None or ntu < 0))
        or (calibration_status == "calibrated" and ntu is None)
    )
    return {
        "voltage": voltage,
        "ntu": ntu if valid else None,
        "valid": valid,
        "calibration_status": "calibrated" if valid else "invalid" if invalid else "calibration_pending",
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    reader.on_reading = save_serial_reading
    reader.on_event = record_system_event
    try:
        record_system_event("backend_started", "Backend started; serial reader is starting.")
    except Exception as exc:
        print("[Activity] Could not record backend startup:", exc)
    reader.start()

    try:
        yield
    finally:
        reader.stop()
        try:
            record_system_event("backend_stopped", "Backend stopped; serial reader was stopped.")
        except Exception as exc:
            print("[Activity] Could not record backend shutdown:", exc)


app = FastAPI(
    title="Life Under Water API",
    version="0.3.0",
    lifespan=lifespan,
)

cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://127.0.0.1:5500,http://localhost:5500",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/forecasts")
def future_forecasts(horizon_hours: int = Query(default=1, ge=1, le=72)) -> dict[str, Any]:
    """Return database-backed Random Forest and XGBoost sensor forecasts."""
    try:
        return forecast_readings(horizon_hours)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not build sensor forecasts: {type(exc).__name__}.",
        ) from exc


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_system_event(cur, event_type: str, message: str, device_id=None) -> None:
    cur.execute(
        """
        INSERT INTO system_events (device_id, event_type, message)
        VALUES (%s, %s, %s)
        """,
        (device_id, event_type, message),
    )


def record_system_event(event_type: str, message: str, device_id=None) -> None:
    """Persist an event caused by a real backend or serial-reader change."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            insert_system_event(cur, event_type, message, device_id)


def save_serial_reading(reading: dict[str, Any]) -> None:
    """Persist one complete serial report atomically, keeping indices distinct from NTU."""
    temperature = finite_number(reading.get("temperature_c"))
    temperature = temperature if temperature is not None and -55 <= temperature <= 125 else None
    temperature_valid = temperature is not None and reading.get("temperature_valid") is not False
    temperature = temperature if temperature_valid else None
    tds = tds_sample_fields(reading)
    tds_raw_adc = raw_adc_number(reading.get("tds_raw_adc"))
    turbidity_raw_adc = raw_adc_number(reading.get("turbidity_raw_adc"))
    turbidity = turbidity_sample_fields(reading)
    cloudiness = cloudiness_sample_fields(reading)
    purpose_reading = {
        **reading,
        "temperature_c": temperature,
        "temperature_valid": temperature_valid,
        "tds_ppm": tds["ppm"],
        "tds_valid": tds["valid"],
        "tds_calibration_status": tds["calibration_status"],
        "tds_source": tds["source"],
        "tds_conversion_method": tds["conversion_method"],
        "turbidity_ntu": turbidity["ntu"],
        "turbidity_valid": turbidity["valid"],
        "turbidity_calibration_status": turbidity["calibration_status"],
        "cloudiness_index": cloudiness["index"],
        "cloudiness_valid": cloudiness["valid"],
    }
    purpose_results = assess_latest_purposes(purpose_reading)

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO devices (
                        device_key,
                        display_name,
                        firmware_version
                    )
                    VALUES (%s, %s, %s)
                    ON CONFLICT (device_key) DO NOTHING
                    """,
                    (
                        DEVICE_KEY,
                        "Life Under Water NodeMCU",
                        None,
                    ),
                )

                cur.execute(
                    """
                    SELECT id
                    FROM devices
                    WHERE device_key = %s
                    """,
                    (DEVICE_KEY,),
                )
                device = cur.fetchone()

                if not device:
                    raise RuntimeError(
                        "Could not find or create the device record."
                    )

                device_id = device[0]

                cur.execute(
                    """
                    INSERT INTO sensor_readings (
                        device_id,
                        recorded_at,
                        temperature_c,
                        temperature_valid,
                        tds_raw_adc,
                        tds_voltage_v,
                        tds_ppm,
                        tds_legacy_demo_estimate_ppm,
                        tds_valid,
                        tds_calibration_status,
                        tds_conversion_method,
                        tds_source,
                        turbidity_raw_adc,
                        turbidity_voltage_v,
                        turbidity_ntu,
                        turbidity_valid,
                        turbidity_calibration_status,
                        cloudiness_index,
                        cloudiness_valid
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        device_id,
                        reading["recorded_at"],
                        temperature if temperature_valid else None,
                        temperature_valid,
                        tds_raw_adc,
                        tds["voltage"],
                        tds["ppm"],
                        tds["legacy_demo_estimate_ppm"],
                        tds["valid"],
                        tds["calibration_status"],
                        tds["conversion_method"],
                        tds["source"],
                        turbidity_raw_adc,
                        turbidity["voltage"],
                        turbidity["ntu"],
                        turbidity["valid"],
                        turbidity["calibration_status"],
                        cloudiness["index"],
                        cloudiness["valid"],
                    ),
                )

                insert_system_event(
                    cur,
                    "reading_saved",
                    "Sensor reading received and saved.",
                    device_id,
                )
                for purpose in purpose_results:
                    if purpose["status"] == "met":
                        insert_system_event(
                            cur,
                            "purpose_alert",
                            purpose_alert_message(purpose),
                            device_id,
                        )

        print("[Database] Sensor reading saved.")

    except Exception as exc:
        print("[Database] Failed to save reading:", exc)
        try:
            record_system_event(
                "database_error",
                "A sensor reading could not be saved to the database.",
            )
        except Exception as event_exc:
            print("[Activity] Could not record database error:", event_exc)
        raise


def latest_row():
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                    r.id,
                    r.device_id,
                    d.device_key,
                    d.display_name,
                    r.recorded_at,
                    r.temperature_c,
                    r.temperature_valid,
                    r.tds_raw_adc,
                    r.tds_voltage_v,
                    r.tds_ppm,
                    r.tds_valid,
                    r.tds_calibration_status,
                    r.tds_conversion_method,
                    r.tds_source,
                    r.turbidity_raw_adc,
                    r.turbidity_voltage_v,
                    r.turbidity_ntu,
                    r.turbidity_valid,
                    r.turbidity_calibration_status,
                    r.cloudiness_index,
                    r.cloudiness_valid
                FROM sensor_readings r
                JOIN devices d ON d.id = r.device_id
                ORDER BY r.recorded_at DESC, r.id DESC
                LIMIT 1
                """
            )
            return cur.fetchone()


def latest_valid_tds():
    predicate = tds_valid_sql("r")
    parameters = (TDS_CALIBRATION_METHOD,) if TDS_CALIBRATION_METHOD else ()
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT r.tds_ppm, r.tds_valid, r.recorded_at, r.tds_source,
                       r.tds_calibration_status, r.tds_conversion_method,
                       r.tds_voltage_v, r.tds_raw_adc
                FROM sensor_readings r
                WHERE {predicate}
                ORDER BY r.recorded_at DESC, r.id DESC
                LIMIT 1
                """,
                parameters,
            )
            return row_to_dict(cur.fetchone())


def latest_sensor_snapshot():
    predicate = tds_valid_sql("r")
    parameters = (TDS_CALIBRATION_METHOD,) if TDS_CALIBRATION_METHOD else ()
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT temperature.recorded_at AS temperature_at,
                       temperature.temperature_c,
                       temperature.temperature_valid,
                       tds.recorded_at AS tds_at,
                       tds.tds_ppm,
                       tds.tds_valid,
                       tds.tds_source,
                       tds.tds_calibration_status,
                       tds.tds_conversion_method,
                       tds.tds_raw_adc,
                       tds.tds_voltage_v,
                       turbidity.recorded_at AS turbidity_at,
                       turbidity.turbidity_ntu,
                       turbidity.turbidity_valid,
                       turbidity.turbidity_calibration_status,
                       legacy_cloudiness.recorded_at AS legacy_cloudiness_at,
                       legacy_cloudiness.cloudiness_index,
                       legacy_cloudiness.cloudiness_valid
                FROM (VALUES (1)) AS seed(value)
                LEFT JOIN LATERAL (
                    SELECT recorded_at, temperature_c, temperature_valid
                    FROM sensor_readings
                    WHERE temperature_valid IS TRUE
                      AND temperature_c BETWEEN -55 AND 125
                    ORDER BY recorded_at DESC, id DESC
                    LIMIT 1
                ) temperature ON TRUE
                LEFT JOIN LATERAL (
                    SELECT r.recorded_at, r.tds_ppm, r.tds_valid, r.tds_source,
                           r.tds_calibration_status,
                           r.tds_conversion_method, r.tds_raw_adc, r.tds_voltage_v
                    FROM sensor_readings r
                    WHERE {predicate}
                    ORDER BY r.recorded_at DESC, r.id DESC
                    LIMIT 1
                ) tds ON TRUE
                LEFT JOIN LATERAL (
                    SELECT recorded_at, turbidity_ntu, turbidity_valid,
                           turbidity_calibration_status
                    FROM sensor_readings
                    WHERE turbidity_valid IS TRUE
                      AND turbidity_calibration_status = 'calibrated'
                      AND turbidity_ntu IS NOT NULL AND turbidity_ntu >= 0
                      AND turbidity_ntu < 'Infinity'::DOUBLE PRECISION
                      AND turbidity_ntu <> 'NaN'::DOUBLE PRECISION
                    ORDER BY recorded_at DESC, id DESC
                    LIMIT 1
                ) turbidity ON TRUE
                LEFT JOIN LATERAL (
                    SELECT recorded_at, cloudiness_index, cloudiness_valid
                    FROM sensor_readings
                    WHERE cloudiness_valid IS TRUE
                      AND cloudiness_index BETWEEN 0 AND 100
                    ORDER BY recorded_at DESC, id DESC
                    LIMIT 1
                ) legacy_cloudiness ON TRUE
                """,
                parameters,
            )
            return row_to_dict(cur.fetchone()) or {}


def utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def timestamp_is_fresh(value: Any, seconds: int, now: datetime | None = None) -> bool:
    timestamp = utc_datetime(value)
    if timestamp is None:
        return False
    current = now or datetime.now(timezone.utc)
    age = (current - timestamp).total_seconds()
    return -5 <= age <= seconds


def assess_latest_purposes(
    reading: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Apply the live, rule-based checks to the newest valid sensor values."""
    source = reading if isinstance(reading, dict) else {}
    recorded_at = source.get("recorded_at")
    temperature_at = source.get("temperature_at") or recorded_at
    tds_at = source.get("tds_at") or recorded_at
    cloudiness_at = source.get("legacy_cloudiness_at") or source.get("cloudiness_at") or recorded_at
    turbidity_at = source.get("turbidity_at") or recorded_at

    temperature = finite_number(source.get("temperature_c"))
    temperature_valid = (
        temperature is not None
        and -55 <= temperature <= 125
        and source.get("temperature_valid") is not False
    )
    tds = tds_sample_fields(source)
    tds_valid = tds["valid"]
    cloudiness = cloudiness_sample_fields(source)
    cloudiness_valid = cloudiness["valid"] and source.get("cloudiness_valid") is not False
    turbidity = turbidity_sample_fields(source)

    def reading_item(
        label: str,
        value: float | None,
        unit: str,
        valid: bool,
        timestamp: Any,
    ) -> dict[str, Any]:
        has_timestamp = utc_datetime(timestamp) is not None
        fresh = timestamp_is_fresh(timestamp, SENSOR_FRESHNESS_SECONDS, now)
        status = "valid" if valid and fresh else "stale" if valid and has_timestamp else "unavailable"
        return {
            "label": label,
            "value": value if status == "valid" else None,
            "unit": unit,
            "status": status,
            "timestamp": timestamp,
        }

    temperature_reading = reading_item("Temperature", temperature, "°C", temperature_valid, temperature_at)
    tds_reading = reading_item("TDS", tds["ppm"], "ppm", tds_valid, tds_at)
    cloudiness_reading = reading_item(
        "Relative cloudiness index (not NTU)", cloudiness["index"], "index / 100",
        cloudiness_valid, cloudiness_at,
    )
    ntu_reading = reading_item("Turbidity", turbidity["ntu"], "NTU", turbidity["valid"], turbidity_at)
    fresh_turbidity = [
        item for item in (cloudiness_reading, ntu_reading)
        if item["status"] == "valid"
    ]
    stale_turbidity = [
        item for item in (cloudiness_reading, ntu_reading)
        if item["status"] == "stale"
    ]
    if fresh_turbidity:
        turbidity_reading = max(
            fresh_turbidity,
            key=lambda item: utc_datetime(item["timestamp"]) or datetime.min.replace(tzinfo=timezone.utc),
        )
    elif stale_turbidity:
        turbidity_reading = max(
            stale_turbidity,
            key=lambda item: utc_datetime(item["timestamp"]) or datetime.min.replace(tzinfo=timezone.utc),
        )
    elif "turbidity_ntu" in source and str(source.get("turbidity_calibration_status") or "").lower() == "calibrated":
        turbidity_reading = ntu_reading
    else:
        turbidity_reading = cloudiness_reading

    base_readings = [temperature_reading, tds_reading, turbidity_reading]
    valid_reading_exists = any(item["status"] == "valid" for item in base_readings)
    def unavailable_reason(required: list[dict[str, Any]]) -> str:
        if any(item["status"] == "stale" for item in required):
            return "Cannot assess because one or more required sensor readings are stale."
        return "Cannot assess because required valid, fresh sensor readings are unavailable."

    agriculture_status = "met" if valid_reading_exists else "cannot_assess"
    agriculture_reason = (
        "A fresh, valid sensor reading is available; Agriculture has no additional thresholds."
        if valid_reading_exists else unavailable_reason(base_readings)
    )

    household_available = tds_reading["status"] == "valid" and turbidity_reading["status"] == "valid"
    household_met = household_available and tds_reading["value"] < 200 and turbidity_reading["value"] < 100
    household_status = "cannot_assess" if not household_available else "met" if household_met else "not_met"
    household_reason = (
        unavailable_reason([tds_reading, turbidity_reading]) if not household_available
        else "TDS is below 200 ppm and turbidity/cloudiness is below 100."
        if household_met else "One or more required thresholds are not met."
    )

    aquatic_available = tds_reading["status"] == "valid" and turbidity_reading["status"] == "valid"
    aquatic_met = aquatic_available and tds_reading["value"] > 500 and turbidity_reading["value"] < 100
    aquatic_status = "cannot_assess" if not aquatic_available else "met" if aquatic_met else "not_met"
    aquatic_reason = (
        unavailable_reason([tds_reading, turbidity_reading]) if not aquatic_available
        else "TDS is above 500 ppm and turbidity/cloudiness is below 100."
        if aquatic_met else "One or more required thresholds are not met."
    )

    turbidity_condition = (
        "Relative cloudiness index < 100"
        if turbidity_reading["unit"] == "index / 100"
        else "Turbidity < 100 NTU"
    )
    return [
        {
            "key": "agriculture",
            "label": "Agriculture",
            "status": agriculture_status,
            "reason": agriculture_reason,
            "conditions": ["Any fresh, valid TDS, turbidity/cloudiness, or temperature reading"],
            "readings": base_readings,
            "recorded_at": recorded_at,
        },
        {
            "key": "household",
            "label": "Household",
            "status": household_status,
            "reason": household_reason,
            "conditions": ["TDS < 200 ppm", turbidity_condition, "Temperature: any value"],
            "readings": [tds_reading, turbidity_reading, temperature_reading],
            "recorded_at": recorded_at,
        },
        {
            "key": "aquatic",
            "label": "Aquatic",
            "status": aquatic_status,
            "reason": aquatic_reason,
            "conditions": ["TDS > 500 ppm", turbidity_condition, "Temperature: any value"],
            "readings": [tds_reading, turbidity_reading, temperature_reading],
            "recorded_at": recorded_at,
        },
    ]


def purpose_alert_message(purpose: dict[str, Any]) -> str:
    """Format one matched purpose as a persistent dashboard activity alert."""
    values = [
        f"{item['label']}: {item['value']} {item['unit']}"
        for item in purpose.get("readings", [])
        if item.get("status") == "valid" and item.get("value") is not None
    ]
    message = f"{purpose['label']} conditions met based on the latest sensor reading."
    return f"{message} {'; '.join(values)}." if values else message


def system_health(serial_status: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    report = serial_status.get("latest_reading")
    report_at = report.get("recorded_at") if isinstance(report, dict) else None
    port_connected = serial_status.get("status") == "connected"
    report_fresh = timestamp_is_fresh(report_at, DEVICE_FRESHNESS_SECONDS)
    connection_status = (
        "connected" if port_connected and report_fresh
        else "stale" if port_connected
        else "disconnected"
    )

    sensors = {}
    for name, timestamp_key, value_key, minimum, maximum in (
        ("temperature", "temperature_at", "temperature_c", -55, 125),
        ("tds", "tds_at", "tds_ppm", 0, None),
        ("turbidity", "turbidity_at", "turbidity_ntu", 0, None),
    ):
        timestamp = snapshot.get(timestamp_key)
        value = finite_number(snapshot.get(value_key))
        has_value = value is not None and value >= minimum and (maximum is None or value <= maximum)
        fresh = has_value and timestamp_is_fresh(timestamp, SENSOR_FRESHNESS_SECONDS)
        sensors[name] = {
            "status": "valid" if fresh else "stale" if has_value else "unavailable",
            "latest_at": timestamp.isoformat() if isinstance(timestamp, datetime) else timestamp,
        }

    if connection_status != "connected":
        overall = "offline"
    elif serial_status.get("last_error") or any(sensor["status"] != "valid" for sensor in sensors.values()):
        overall = "degraded"
    else:
        overall = "online"

    return {
        "system_status": overall,
        "device_status": "connected" if connection_status == "connected" else "offline",
        "serial_connection_status": connection_status,
        "sensor_health": sensors,
        "freshness_seconds": {
            "device": DEVICE_FRESHNESS_SECONDS,
            "sensors": SENSOR_FRESHNESS_SECONDS,
        },
    }


def row_to_dict(row):
    if not row:
        return None

    result = dict(row)

    for key, value in result.items():
        if isinstance(value, datetime):
            result[key] = value.isoformat()

    return result


def device_to_dict(row):
    result = row_to_dict(row)
    if not result:
        return None
    latest_fields = (
        "recorded_at",
        "temperature_c",
        "temperature_valid",
        "tds_raw_adc",
        "tds_voltage_v",
        "tds_ppm",
        "tds_valid",
        "tds_calibration_status",
        "tds_conversion_method",
        "tds_source",
        "turbidity_raw_adc",
        "turbidity_voltage_v",
        "turbidity_ntu",
        "turbidity_valid",
        "turbidity_calibration_status",
        "cloudiness_index",
        "cloudiness_valid",
    )
    latest = {
        field: result.pop(f"latest_{field}", None)
        for field in latest_fields
    }
    result["latest_reading"] = latest if latest["recorded_at"] is not None else None
    return result


def recent_system_events(limit: int = 8) -> list[dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                WITH ranked_events AS (
                    SELECT id, event_type, message, recorded_at,
                           ROW_NUMBER() OVER (
                               PARTITION BY event_type, message
                               ORDER BY recorded_at DESC, id DESC
                           ) AS event_rank
                    FROM system_events
                )
                SELECT id, event_type, message, recorded_at AS timestamp
                FROM ranked_events
                WHERE event_rank = 1
                ORDER BY recorded_at DESC, id DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()

    return [row_to_dict(row) for row in rows]


def overview_averages(summary: dict[str, Any]) -> dict[str, Any]:
    temperature = summary["temperature_c"]
    tds = summary["tds_ppm"]
    turbidity = summary["turbidity_ntu"]
    time_window = summary.get("time_window")
    tds_available = tds["valid_samples"] > 0 and tds["average"] is not None
    turbidity_calibrated = (
        turbidity["calibrated_readings_only"] is True
        and turbidity["valid_samples"] > 0
    )

    return {
        "temperature": {
            "value": temperature["average"],
            "average": temperature["average"],
            "unit": "°C",
            "minimum": temperature["minimum"],
            "maximum": temperature["maximum"],
            "valid_samples": temperature["valid_samples"],
            "data_status": "valid" if temperature["valid_samples"] > 0 else "waiting_for_data",
            "source": "measured",
            "data_quality": "valid" if temperature["valid_samples"] > 0 else "unavailable",
            "time_window": time_window,
        },
        "tds": {
            "value": tds["average"] if tds_available else None,
            "average": tds["average"] if tds_available else None,
            "unit": "ppm",
            "minimum": tds["minimum"] if tds_available else None,
            "maximum": tds["maximum"] if tds_available else None,
            "valid_samples": tds["valid_samples"],
            "calibration_status": tds.get("calibration_status", "calibration_pending"),
            "calibration_method": tds.get("calibration_method"),
            "source": tds.get("source", "unavailable") if tds_available else "unavailable",
            "data_quality": tds.get("data_quality", "unavailable") if tds_available else "unavailable",
            "time_window": time_window,
        },
        "turbidity": {
            "value": turbidity["average"] if turbidity_calibrated else None,
            "average": turbidity["average"] if turbidity_calibrated else None,
            "unit": "NTU",
            "minimum": turbidity["minimum"] if turbidity_calibrated else None,
            "maximum": turbidity["maximum"] if turbidity_calibrated else None,
            "valid_samples": turbidity["valid_samples"],
            "calibration_status": "calibrated" if turbidity_calibrated else "calibration_pending",
            "source": "calibrated" if turbidity_calibrated else "unavailable",
            "data_quality": "calibrated" if turbidity_calibrated else "unavailable",
            "time_window": time_window,
        },
        "cloudiness_index": {
            "value": summary["cloudiness_index"]["average"],
            "average": summary["cloudiness_index"]["average"],
            "unit": "index / 100",
            "minimum": summary["cloudiness_index"]["minimum"],
            "maximum": summary["cloudiness_index"]["maximum"],
            "valid_samples": summary["cloudiness_index"]["valid_samples"],
            "data_status": "valid" if summary["cloudiness_index"]["valid_samples"] > 0 else "waiting_for_data",
            "source": "firmware_relative_index",
            "data_quality": "relative_index" if summary["cloudiness_index"]["valid_samples"] > 0 else "unavailable",
            "time_window": time_window,
        },
        "legacy_cloudiness_index": {
            "value": summary["cloudiness_index"]["average"],
            "average": summary["cloudiness_index"]["average"],
            "unit": "index / 100",
            "minimum": summary["cloudiness_index"]["minimum"],
            "maximum": summary["cloudiness_index"]["maximum"],
            "valid_samples": summary["cloudiness_index"]["valid_samples"],
            "data_status": "valid" if summary["cloudiness_index"]["valid_samples"] > 0 else "waiting_for_data",
            "source": "firmware_relative_index",
            "data_quality": "relative_index" if summary["cloudiness_index"]["valid_samples"] > 0 else "unavailable",
            "time_window": time_window,
        },
    }


def live_sensor_reading(serial_status: dict[str, Any]) -> dict[str, Any] | None:
    """Expose reported measurements, legacy relative index data, and diagnostic voltages."""
    if serial_status.get("status") != "connected" or serial_status.get("last_error"):
        return None

    reading = serial_status.get("latest_reading")
    if not isinstance(reading, dict):
        return None

    temperature = finite_number(reading.get("temperature_c"))
    temperature_valid = (
        temperature is not None and -55 <= temperature <= 125
        and reading.get("temperature_valid") is not False
    )
    tds = tds_sample_fields(reading)
    turbidity = turbidity_sample_fields(reading)
    cloudiness = cloudiness_sample_fields(reading)

    return {
        "recorded_at": reading.get("recorded_at"),
        "temperature_c": temperature if temperature_valid else None,
        "temperature_valid": temperature_valid,
        "tds_raw_adc": raw_adc_number(reading.get("tds_raw_adc")),
        "tds_voltage_v": tds["voltage"],
        "tds_ppm": tds["ppm"],
        "tds_valid": tds["valid"],
        "tds_calibration_status": tds["calibration_status"],
        "tds_conversion_method": tds["conversion_method"],
        "tds_source": tds["source"],
        "turbidity_raw_adc": raw_adc_number(reading.get("turbidity_raw_adc")),
        "turbidity_voltage_v": turbidity["voltage"],
        "turbidity_ntu": turbidity["ntu"],
        "turbidity_valid": turbidity["valid"],
        "turbidity_calibration_status": turbidity["calibration_status"],
        "cloudiness_index": cloudiness["index"],
        "cloudiness_valid": cloudiness["valid"],
        "legacy_cloudiness_index": cloudiness["index"],
        "legacy_cloudiness_valid": cloudiness["valid"],
        "legacy_cloudiness_status": cloudiness["status"],
    }


@app.get("/")
def root():
    return {
        "name": "Life Under Water API",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/api/health")
def health():
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()

        return {
            "api": "ok",
            "database": "connected",
            "timestamp": now_iso(),
        }

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database unavailable: {exc}",
        ) from exc


@app.get("/api/status")
def status() -> dict[str, Any]:
    try:
        row = latest_row()
        last_data = row["recorded_at"] if row else None
        sensor_snapshot = latest_sensor_snapshot()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load system status: {exc}",
        ) from exc

    serial_status = reader.status()
    health = system_health(serial_status, sensor_snapshot)

    return {
        **health,
        "serial_reader_status": serial_status["status"],
        "serial_port": serial_status["port"],
        "serial_baudrate": serial_status["baudrate"],
        "last_error": serial_status["last_error"],
        "last_data_at": (
            last_data.isoformat()
            if isinstance(last_data, datetime)
            else last_data
        ),
        "latest_reading": row_to_dict(row),
        "timestamp": now_iso(),
    }


@app.get("/api/overview")
def overview() -> dict[str, Any]:
    try:
        row = latest_row()
        summary = analytics_summary(hours=24)
        serial_status = reader.status()
        activity = recent_system_events()
        sensor_snapshot = latest_sensor_snapshot()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load overview: {exc}",
        ) from exc

    averages = overview_averages(summary)
    tds_health = sensor_snapshot.get("tds_at")
    latest_valid_tds_reading = None
    if tds_health is not None and sensor_snapshot.get("tds_ppm") is not None:
        latest_valid_tds_reading = {
            "tds_ppm": sensor_snapshot["tds_ppm"],
            "recorded_at": tds_health.isoformat() if isinstance(tds_health, datetime) else tds_health,
            "tds_source": sensor_snapshot.get("tds_source"),
            "tds_conversion_method": sensor_snapshot.get("tds_conversion_method"),
            "tds_raw_adc": sensor_snapshot.get("tds_raw_adc"),
            "tds_voltage_v": sensor_snapshot.get("tds_voltage_v"),
            "tds_valid": True,
            "tds_calibration_status": "estimated" if sensor_snapshot.get("tds_source") == "demo_estimate" else "calibrated",
        }
    result = {
        **system_health(serial_status, sensor_snapshot),
        "last_error": serial_status.get("last_error"),
        "averages": averages,
        "average_window": "Last 24 hours",
        "time_window": summary["time_window"],
        "live_reading": live_sensor_reading(serial_status),
        "tds_working_calibration_method": TDS_CALIBRATION_METHOD,
        "latest_valid_tds": latest_valid_tds_reading,
        "purpose_assessments": assess_latest_purposes(sensor_snapshot),
        "activity": activity,
        "timestamp": now_iso(),
    }

    if not row:
        result["latest_reading"] = None
        return result

    result["latest_reading"] = row_to_dict(row)
    return result


@app.get("/api/readings/latest")
def readings_latest() -> dict[str, Any]:
    try:
        row = latest_row()
        latest_tds = latest_valid_tds()
        sensor_snapshot = latest_sensor_snapshot()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load latest reading: {exc}",
        ) from exc

    if not row:
        return {
            "reading": None,
            "latest_valid_tds": latest_tds,
            "purpose_assessments": assess_latest_purposes(sensor_snapshot),
            "tds_working_calibration_method": TDS_CALIBRATION_METHOD,
            "message": "No readings have been saved yet.",
            "timestamp": now_iso(),
        }

    return {
        "reading": row_to_dict(row),
        "latest_valid_tds": latest_tds,
        "purpose_assessments": assess_latest_purposes(sensor_snapshot),
        "tds_working_calibration_method": TDS_CALIBRATION_METHOD,
        "timestamp": now_iso(),
    }


@app.post("/api/readings/pull")
def readings_pull() -> dict[str, Any]:
    serial_status = reader.status()

    if serial_status["status"] != "connected":
        raise HTTPException(
            status_code=503,
            detail=(
                "Serial reader is not connected. "
                f"Status: {serial_status['status']}; "
                f"error: {serial_status['last_error'] or 'none'}"
            ),
        )

    if serial_status.get("last_error"):
        raise HTTPException(
            status_code=503,
            detail="The latest serial report could not be saved. Check the database connection and retry.",
        )

    if not serial_status["latest_reading"]:
        raise HTTPException(
            status_code=503,
            detail="Waiting for the next complete sensor report.",
        )

    return {
        "message": (
            "Latest serial reading received. "
            "Readings are also saved automatically."
        ),
        "reading": serial_status["latest_reading"],
        "purpose_assessments": assess_latest_purposes(serial_status["latest_reading"]),
        "timestamp": now_iso(),
    }


@app.get("/api/readings/history")
def readings_history(
    limit: int = Query(default=50, ge=1, le=500),
    hours: int = Query(default=24, ge=1, le=720),
) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    parameters = [cutoff]
    if TDS_CALIBRATION_METHOD:
        parameters.append(TDS_CALIBRATION_METHOD)
    parameters.extend((limit, limit, limit, limit))
    tds_predicate = tds_valid_sql("r")

    try:
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    f"""
                    WITH window_readings AS (
                        SELECT *
                        FROM sensor_readings
                        WHERE recorded_at >= %s
                    ), tds_candidates AS (
                        SELECT r.*
                        FROM window_readings r
                        WHERE {tds_predicate}
                    ), selected_tds_source AS (
                        SELECT tds_source, COALESCE(tds_conversion_method, '') AS conversion_method
                        FROM tds_candidates
                        ORDER BY recorded_at DESC, id DESC
                        LIMIT 1
                    ), temperature_ranked AS (
                        SELECT
                            id,
                            recorded_at,
                            temperature_c,
                            NTILE(%s) OVER (ORDER BY recorded_at, id) AS bucket
                        FROM window_readings
                        WHERE temperature_valid IS TRUE
                          AND temperature_c IS NOT NULL
                    ), temperature_points AS (
                        SELECT DISTINCT ON (bucket)
                            id, recorded_at, temperature_c, TRUE AS temperature_valid,
                            NULL::INTEGER AS tds_raw_adc,
                            NULL::DOUBLE PRECISION AS tds_voltage_v,
                            NULL::DOUBLE PRECISION AS tds_ppm,
                            NULL::TEXT AS tds_source,
                            FALSE AS tds_valid,
                            'calibration_pending'::TEXT AS tds_calibration_status,
                            NULL::TEXT AS tds_conversion_method,
                            NULL::INTEGER AS turbidity_raw_adc,
                            NULL::DOUBLE PRECISION AS turbidity_voltage_v,
                            NULL::DOUBLE PRECISION AS turbidity_ntu,
                            FALSE AS turbidity_valid,
                            'calibration_pending'::TEXT AS turbidity_calibration_status,
                            NULL::DOUBLE PRECISION AS cloudiness_index,
                            FALSE AS cloudiness_valid
                        FROM temperature_ranked
                        ORDER BY bucket, recorded_at DESC, id DESC
                    ), tds_ranked AS (
                        SELECT
                            id,
                            recorded_at,
                            tds_raw_adc,
                            tds_voltage_v,
                            tds_ppm,
                            tds_source,
                            tds_calibration_status,
                            tds_conversion_method,
                            NTILE(%s) OVER (ORDER BY recorded_at, id) AS bucket
                        FROM tds_candidates
                        WHERE tds_source = (SELECT tds_source FROM selected_tds_source)
                          AND COALESCE(tds_conversion_method, '') = (
                              SELECT conversion_method FROM selected_tds_source
                          )
                    ), tds_points AS (
                        SELECT DISTINCT ON (bucket)
                            id, recorded_at,
                            NULL::DOUBLE PRECISION AS temperature_c,
                            FALSE AS temperature_valid,
                            tds_raw_adc,
                            tds_voltage_v,
                            tds_ppm,
                            tds_source,
                            TRUE AS tds_valid,
                            tds_calibration_status,
                            tds_conversion_method,
                            NULL::INTEGER AS turbidity_raw_adc,
                            NULL::DOUBLE PRECISION AS turbidity_voltage_v,
                            NULL::DOUBLE PRECISION AS turbidity_ntu,
                            FALSE AS turbidity_valid,
                            'calibration_pending'::TEXT AS turbidity_calibration_status,
                            NULL::DOUBLE PRECISION AS cloudiness_index,
                            FALSE AS cloudiness_valid
                        FROM tds_ranked
                        ORDER BY bucket, recorded_at DESC, id DESC
                    ), turbidity_ranked AS (
                        SELECT
                            id,
                            recorded_at,
                            turbidity_raw_adc,
                            turbidity_voltage_v,
                            turbidity_ntu,
                            turbidity_calibration_status,
                            NTILE(%s) OVER (ORDER BY recorded_at, id) AS bucket
                        FROM window_readings
                        WHERE turbidity_valid IS TRUE
                          AND turbidity_calibration_status = 'calibrated'
                          AND turbidity_ntu IS NOT NULL
                    ), turbidity_points AS (
                        SELECT DISTINCT ON (bucket)
                            id, recorded_at,
                            NULL::DOUBLE PRECISION AS temperature_c,
                            FALSE AS temperature_valid,
                            NULL::INTEGER AS tds_raw_adc,
                            NULL::DOUBLE PRECISION AS tds_voltage_v,
                            NULL::DOUBLE PRECISION AS tds_ppm,
                            NULL::TEXT AS tds_source,
                            FALSE AS tds_valid,
                            'calibration_pending'::TEXT AS tds_calibration_status,
                            NULL::TEXT AS tds_conversion_method,
                            turbidity_raw_adc,
                            turbidity_voltage_v,
                            turbidity_ntu,
                            TRUE AS turbidity_valid,
                            turbidity_calibration_status,
                            NULL::DOUBLE PRECISION AS cloudiness_index,
                            FALSE AS cloudiness_valid
                        FROM turbidity_ranked
                        ORDER BY bucket, recorded_at DESC, id DESC
                    ), cloudiness_ranked AS (
                        SELECT
                            id,
                            recorded_at,
                            cloudiness_index,
                            NTILE(%s) OVER (ORDER BY recorded_at, id) AS bucket
                        FROM window_readings
                        WHERE cloudiness_valid IS TRUE
                          AND cloudiness_index IS NOT NULL
                    ), cloudiness_points AS (
                        SELECT DISTINCT ON (bucket)
                            id, recorded_at,
                            NULL::DOUBLE PRECISION AS temperature_c,
                            FALSE AS temperature_valid,
                            NULL::INTEGER AS tds_raw_adc,
                            NULL::DOUBLE PRECISION AS tds_voltage_v,
                            NULL::DOUBLE PRECISION AS tds_ppm,
                            NULL::TEXT AS tds_source,
                            FALSE AS tds_valid,
                            'calibration_pending'::TEXT AS tds_calibration_status,
                            NULL::TEXT AS tds_conversion_method,
                            NULL::INTEGER AS turbidity_raw_adc,
                            NULL::DOUBLE PRECISION AS turbidity_voltage_v,
                            NULL::DOUBLE PRECISION AS turbidity_ntu,
                            FALSE AS turbidity_valid,
                            'calibration_pending'::TEXT AS turbidity_calibration_status,
                            cloudiness_index,
                            TRUE AS cloudiness_valid
                        FROM cloudiness_ranked
                        ORDER BY bucket, recorded_at DESC, id DESC
                    )
                    SELECT * FROM temperature_points
                    UNION ALL
                    SELECT * FROM tds_points
                    UNION ALL
                    SELECT * FROM turbidity_points
                    UNION ALL
                    SELECT * FROM cloudiness_points
                    ORDER BY recorded_at, id
                    """,
                    tuple(parameters),
                )
                rows = cur.fetchall()

        return {
            "readings": [row_to_dict(row) for row in rows],
            "count": len(rows),
            "tds_working_calibration_method": TDS_CALIBRATION_METHOD,
            "timestamp": now_iso(),
        }

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load reading history: {exc}",
        ) from exc


@app.get("/api/analytics-summary")
def analytics_summary(
    hours: int = Query(default=24, ge=1, le=720),
) -> dict[str, Any]:
    window_end = datetime.now(timezone.utc)
    cutoff = window_end - timedelta(hours=hours)
    tds_predicate = tds_valid_sql("r")
    parameters = [cutoff]
    if TDS_CALIBRATION_METHOD:
        parameters.append(TDS_CALIBRATION_METHOD)

    try:
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    f"""
                    WITH window_readings AS (
                        SELECT * FROM sensor_readings WHERE recorded_at >= %s
                    ), tds_candidates AS (
                        SELECT r.* FROM window_readings r WHERE {tds_predicate}
                    ), selected_tds_source AS (
                        SELECT tds_source, COALESCE(tds_conversion_method, '') AS conversion_method
                        FROM tds_candidates
                        ORDER BY recorded_at DESC, id DESC LIMIT 1
                    ), selected_tds AS (
                        SELECT * FROM tds_candidates
                        WHERE tds_source = (SELECT tds_source FROM selected_tds_source)
                          AND COALESCE(tds_conversion_method, '') = (
                              SELECT conversion_method FROM selected_tds_source
                          )
                    )
                    SELECT
                        (SELECT COUNT(*) FROM window_readings) AS reading_count,
                        (SELECT AVG(temperature_c) FROM window_readings
                         WHERE temperature_valid IS TRUE AND temperature_c BETWEEN -55 AND 125) AS avg_temp,
                        (SELECT MIN(temperature_c) FROM window_readings
                         WHERE temperature_valid IS TRUE AND temperature_c BETWEEN -55 AND 125) AS min_temp,
                        (SELECT MAX(temperature_c) FROM window_readings
                         WHERE temperature_valid IS TRUE AND temperature_c BETWEEN -55 AND 125) AS max_temp,
                        (SELECT COUNT(*) FROM window_readings
                         WHERE temperature_valid IS TRUE AND temperature_c BETWEEN -55 AND 125) AS valid_temperature_samples,
                        (SELECT AVG(tds_ppm) FROM selected_tds) AS avg_tds,
                        (SELECT MIN(tds_ppm) FROM selected_tds) AS min_tds,
                        (SELECT MAX(tds_ppm) FROM selected_tds) AS max_tds,
                        (SELECT COUNT(*) FROM selected_tds) AS valid_tds_samples,
                        (SELECT tds_ppm FROM selected_tds ORDER BY recorded_at DESC, id DESC LIMIT 1) AS latest_tds,
                        (SELECT recorded_at FROM selected_tds ORDER BY recorded_at DESC, id DESC LIMIT 1) AS latest_tds_at,
                        (SELECT tds_source FROM selected_tds ORDER BY recorded_at DESC, id DESC LIMIT 1) AS tds_source,
                        (SELECT tds_conversion_method FROM selected_tds ORDER BY recorded_at DESC, id DESC LIMIT 1) AS latest_tds_method,
                        (SELECT AVG(turbidity_ntu) FROM window_readings
                         WHERE turbidity_valid IS TRUE AND turbidity_calibration_status = 'calibrated'
                           AND turbidity_ntu IS NOT NULL AND turbidity_ntu >= 0
                           AND turbidity_ntu < 'Infinity'::DOUBLE PRECISION AND turbidity_ntu <> 'NaN'::DOUBLE PRECISION) AS avg_turbidity,
                        (SELECT MIN(turbidity_ntu) FROM window_readings
                         WHERE turbidity_valid IS TRUE AND turbidity_calibration_status = 'calibrated'
                           AND turbidity_ntu IS NOT NULL AND turbidity_ntu >= 0
                           AND turbidity_ntu < 'Infinity'::DOUBLE PRECISION AND turbidity_ntu <> 'NaN'::DOUBLE PRECISION) AS min_turbidity,
                        (SELECT MAX(turbidity_ntu) FROM window_readings
                         WHERE turbidity_valid IS TRUE AND turbidity_calibration_status = 'calibrated'
                           AND turbidity_ntu IS NOT NULL AND turbidity_ntu >= 0
                           AND turbidity_ntu < 'Infinity'::DOUBLE PRECISION AND turbidity_ntu <> 'NaN'::DOUBLE PRECISION) AS max_turbidity,
                        (SELECT COUNT(*) FROM window_readings
                         WHERE turbidity_valid IS TRUE AND turbidity_calibration_status = 'calibrated'
                           AND turbidity_ntu IS NOT NULL AND turbidity_ntu >= 0
                           AND turbidity_ntu < 'Infinity'::DOUBLE PRECISION AND turbidity_ntu <> 'NaN'::DOUBLE PRECISION) AS valid_turbidity_samples,
                        (SELECT AVG(cloudiness_index) FROM window_readings
                         WHERE cloudiness_valid IS TRUE AND cloudiness_index BETWEEN 0 AND 100) AS avg_cloudiness_index,
                        (SELECT MIN(cloudiness_index) FROM window_readings
                         WHERE cloudiness_valid IS TRUE AND cloudiness_index BETWEEN 0 AND 100) AS min_cloudiness_index,
                        (SELECT MAX(cloudiness_index) FROM window_readings
                         WHERE cloudiness_valid IS TRUE AND cloudiness_index BETWEEN 0 AND 100) AS max_cloudiness_index,
                        (SELECT COUNT(*) FROM window_readings
                         WHERE cloudiness_valid IS TRUE AND cloudiness_index BETWEEN 0 AND 100) AS valid_cloudiness_samples,
                        (SELECT temperature_c FROM window_readings
                         WHERE temperature_valid IS TRUE AND temperature_c BETWEEN -55 AND 125
                         ORDER BY recorded_at DESC, id DESC LIMIT 1) AS latest_temp,
                        (SELECT recorded_at FROM window_readings
                         WHERE temperature_valid IS TRUE AND temperature_c BETWEEN -55 AND 125
                         ORDER BY recorded_at DESC, id DESC LIMIT 1) AS latest_temp_at,
                        (SELECT turbidity_ntu FROM window_readings
                         WHERE turbidity_valid IS TRUE AND turbidity_calibration_status = 'calibrated'
                           AND turbidity_ntu IS NOT NULL AND turbidity_ntu >= 0
                           AND turbidity_ntu < 'Infinity'::DOUBLE PRECISION AND turbidity_ntu <> 'NaN'::DOUBLE PRECISION
                         ORDER BY recorded_at DESC, id DESC LIMIT 1) AS latest_turbidity,
                        (SELECT recorded_at FROM window_readings
                         WHERE turbidity_valid IS TRUE AND turbidity_calibration_status = 'calibrated'
                           AND turbidity_ntu IS NOT NULL AND turbidity_ntu >= 0
                           AND turbidity_ntu < 'Infinity'::DOUBLE PRECISION AND turbidity_ntu <> 'NaN'::DOUBLE PRECISION
                         ORDER BY recorded_at DESC, id DESC LIMIT 1) AS latest_turbidity_at,
                        (SELECT cloudiness_index FROM window_readings
                         WHERE cloudiness_valid IS TRUE AND cloudiness_index BETWEEN 0 AND 100
                         ORDER BY recorded_at DESC, id DESC LIMIT 1) AS latest_cloudiness_index,
                        (SELECT recorded_at FROM window_readings
                         WHERE cloudiness_valid IS TRUE AND cloudiness_index BETWEEN 0 AND 100
                         ORDER BY recorded_at DESC, id DESC LIMIT 1) AS latest_cloudiness_at
                    """,
                    tuple(parameters),
                )
                row = cur.fetchone()

        summary = row_to_dict(row)
        time_window = {
            "hours": hours,
            "start": cutoff.isoformat(),
            "end": window_end.isoformat(),
        }

        return {
            "period_hours": hours,
            "time_window": time_window,
            "reading_count": summary["reading_count"],
            "tds_working_calibration_method": TDS_CALIBRATION_METHOD,
            "temperature_c": {
                "average": summary["avg_temp"],
                "minimum": summary["min_temp"],
                "maximum": summary["max_temp"],
                "valid_samples": summary["valid_temperature_samples"],
                "latest": summary["latest_temp"],
                "latest_timestamp": summary["latest_temp_at"],
                "unit": "°C",
                "source": "measured",
                "data_quality": "valid" if summary["valid_temperature_samples"] else "unavailable",
                "time_window": time_window,
            },
            "tds_ppm": {
                "average": summary["avg_tds"],
                "minimum": summary["min_tds"],
                "maximum": summary["max_tds"],
                "valid_samples": summary["valid_tds_samples"],
                "latest": summary["latest_tds"],
                "latest_timestamp": summary["latest_tds_at"],
                "calibration_method": summary["latest_tds_method"],
                "calibration_status": "estimated" if summary["tds_source"] == "demo_estimate" else "calibrated" if summary["tds_source"] == "calibrated" else "unavailable",
                "unit": "ppm",
                "source": summary["tds_source"] or "unavailable",
                "data_quality": "estimated" if summary["tds_source"] == "demo_estimate" else "calibrated" if summary["tds_source"] == "calibrated" else "unavailable",
                "time_window": time_window,
            },
            "turbidity_ntu": {
                "average": summary["avg_turbidity"],
                "minimum": summary["min_turbidity"],
                "maximum": summary["max_turbidity"],
                "valid_samples": summary["valid_turbidity_samples"],
                "latest": summary["latest_turbidity"],
                "latest_timestamp": summary["latest_turbidity_at"],
                "calibrated_readings_only": True,
                "unit": "NTU",
                "source": "calibrated",
                "data_quality": "calibrated" if summary["valid_turbidity_samples"] else "unavailable",
                "time_window": time_window,
            },
            "cloudiness_index": {
                "average": summary["avg_cloudiness_index"],
                "minimum": summary["min_cloudiness_index"],
                "maximum": summary["max_cloudiness_index"],
                "valid_samples": summary["valid_cloudiness_samples"],
                "latest": summary["latest_cloudiness_index"],
                "latest_timestamp": summary["latest_cloudiness_at"],
                "relative_index_only": True,
                "scale_minimum": 0,
                "scale_maximum": 100,
                "unit": "index / 100",
                "source": "firmware_relative_index",
                "data_quality": "relative_index" if summary["valid_cloudiness_samples"] else "unavailable",
                "time_window": time_window,
            },
            "legacy_cloudiness_index": {
                "average": summary["avg_cloudiness_index"],
                "minimum": summary["min_cloudiness_index"],
                "maximum": summary["max_cloudiness_index"],
                "valid_samples": summary["valid_cloudiness_samples"],
                "latest": summary["latest_cloudiness_index"],
                "latest_timestamp": summary["latest_cloudiness_at"],
                "relative_index_only": True,
                "scale_minimum": 0,
                "scale_maximum": 100,
                "unit": "index / 100",
                "source": "firmware_relative_index",
                "data_quality": "relative_index" if summary["valid_cloudiness_samples"] else "unavailable",
                "time_window": time_window,
            },
            "timestamp": now_iso(),
        }

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load analytics: {exc}",
        ) from exc


@app.get("/api/devices")
def devices() -> dict[str, Any]:
    try:
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        d.id,
                        d.device_key,
                        d.display_name,
                        d.firmware_version,
                        d.created_at,
                        latest.recorded_at AS last_data_at,
                        latest.recorded_at AS latest_recorded_at,
                        latest.temperature_c AS latest_temperature_c,
                        latest.temperature_valid AS latest_temperature_valid,
                        latest.tds_raw_adc AS latest_tds_raw_adc,
                        latest.tds_voltage_v AS latest_tds_voltage_v,
                        latest.tds_ppm AS latest_tds_ppm,
                        latest.tds_valid AS latest_tds_valid,
                        latest.tds_calibration_status AS latest_tds_calibration_status,
                        latest.tds_conversion_method AS latest_tds_conversion_method,
                        latest.tds_source AS latest_tds_source,
                        latest.turbidity_raw_adc AS latest_turbidity_raw_adc,
                        latest.turbidity_voltage_v AS latest_turbidity_voltage_v,
                        latest.turbidity_ntu AS latest_turbidity_ntu,
                        latest.turbidity_valid AS latest_turbidity_valid,
                        latest.turbidity_calibration_status AS latest_turbidity_calibration_status,
                        latest.cloudiness_index AS latest_cloudiness_index,
                        latest.cloudiness_valid AS latest_cloudiness_valid
                    FROM devices d
                    LEFT JOIN LATERAL (
                        SELECT recorded_at, temperature_c, temperature_valid,
                               tds_raw_adc, tds_voltage_v, tds_ppm, tds_valid,
                               tds_calibration_status, tds_conversion_method, tds_source,
                               turbidity_raw_adc, turbidity_voltage_v, turbidity_ntu,
                               turbidity_valid, turbidity_calibration_status,
                               cloudiness_index, cloudiness_valid
                        FROM sensor_readings
                        WHERE device_id = d.id
                        ORDER BY recorded_at DESC, id DESC
                        LIMIT 1
                    ) latest ON TRUE
                    ORDER BY d.id
                    """
                )
                rows = cur.fetchall()

        return {
            "devices": [device_to_dict(row) for row in rows],
            "count": len(rows),
            "tds_working_calibration_method": TDS_CALIBRATION_METHOD,
            "timestamp": now_iso(),
        }

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load devices: {exc}",
        ) from exc


@app.get("/api/devices/{device_key}")
def device_detail(device_key: str) -> dict[str, Any]:
    try:
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        d.id,
                        d.device_key,
                        d.display_name,
                        d.firmware_version,
                        d.created_at,
                        latest.recorded_at AS last_data_at,
                        latest.recorded_at AS latest_recorded_at,
                        latest.temperature_c AS latest_temperature_c,
                        latest.temperature_valid AS latest_temperature_valid,
                        latest.tds_raw_adc AS latest_tds_raw_adc,
                        latest.tds_voltage_v AS latest_tds_voltage_v,
                        latest.tds_ppm AS latest_tds_ppm,
                        latest.tds_valid AS latest_tds_valid,
                        latest.tds_calibration_status AS latest_tds_calibration_status,
                        latest.tds_conversion_method AS latest_tds_conversion_method,
                        latest.tds_source AS latest_tds_source,
                        latest.turbidity_raw_adc AS latest_turbidity_raw_adc,
                        latest.turbidity_voltage_v AS latest_turbidity_voltage_v,
                        latest.turbidity_ntu AS latest_turbidity_ntu,
                        latest.turbidity_valid AS latest_turbidity_valid,
                        latest.turbidity_calibration_status AS latest_turbidity_calibration_status,
                        latest.cloudiness_index AS latest_cloudiness_index,
                        latest.cloudiness_valid AS latest_cloudiness_valid
                    FROM devices d
                    LEFT JOIN LATERAL (
                        SELECT recorded_at, temperature_c, temperature_valid,
                               tds_raw_adc, tds_voltage_v, tds_ppm, tds_valid,
                               tds_calibration_status, tds_conversion_method, tds_source,
                               turbidity_raw_adc, turbidity_voltage_v, turbidity_ntu,
                               turbidity_valid, turbidity_calibration_status,
                               cloudiness_index, cloudiness_valid
                        FROM sensor_readings
                        WHERE device_id = d.id
                        ORDER BY recorded_at DESC, id DESC
                        LIMIT 1
                    ) latest ON TRUE
                    WHERE d.device_key = %s
                    """,
                    (device_key,),
                )
                row = cur.fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail="Device not found.",
            )

        return {
            "device": device_to_dict(row),
            "tds_working_calibration_method": TDS_CALIBRATION_METHOD,
            "timestamp": now_iso(),
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load device: {exc}",
        ) from exc
