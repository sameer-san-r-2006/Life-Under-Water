
import os
import json
import math
import re
import threading
import time
from datetime import datetime, timezone

import serial
from serial.tools import list_ports


NUMBER_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
SENSOR_VALUE_FIELDS = (
    "temperature_c",
    "tds_ppm",
    "tds_raw_adc",
    "tds_voltage_v",
    "turbidity_ntu",
    "turbidity_raw_adc",
    "turbidity_voltage_v",
    "cloudiness_index",
)


def finite_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def update_reading_from_payload(payload, current: dict) -> None:
    """Copy supported sensor fields from a JSON report without converting them."""
    for field in SENSOR_VALUE_FIELDS:
        if field not in payload:
            continue
        value = finite_number(payload[field])
        if field.endswith("_raw_adc") and value is not None and not value.is_integer():
            value = None
        current[field] = int(value) if value is not None and field.endswith("_raw_adc") else value

    method = payload.get("tds_conversion_method")
    if isinstance(method, str) and method.strip():
        current["tds_conversion_method"] = method.strip()

    source = payload.get("tds_source")
    if isinstance(source, str) and source.strip():
        current["tds_source"] = source.strip().lower()

    for field in ("tds_calibration_status", "turbidity_calibration_status"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            current[field] = value.strip().lower()

    for field in ("temperature_valid", "tds_valid", "turbidity_valid", "cloudiness_valid"):
        if isinstance(payload.get(field), bool):
            current[field] = payload[field]


def update_reading_from_line(line: str, current: dict) -> None:
    """Add supported fields from one line of the sketch's serial report."""
    stripped = line.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            return
        if isinstance(payload, dict):
            update_reading_from_payload(payload, current)
        return

    calibrated_tds = re.search(
        rf"TDS calibrated value:\s*({NUMBER_PATTERN})\s*ppm\s*"
        r"\(method=([A-Za-z0-9_.:-]+)\)",
        stripped,
        re.IGNORECASE,
    )
    if calibrated_tds:
        value = finite_number(calibrated_tds.group(1))
        if value is not None:
            current["tds_ppm"] = value
        current["tds_conversion_method"] = calibrated_tds.group(2)
        current["tds_calibration_status"] = "calibrated"
        current["tds_valid"] = True
        current["tds_source"] = "calibrated"

    demo_tds = re.search(
        rf"TDS demo estimate:\s*({NUMBER_PATTERN})\s*ppm\s*\(not calibrated\)",
        stripped,
        re.IGNORECASE,
    )
    if demo_tds:
        value = finite_number(demo_tds.group(1))
        if value is not None and value >= 0:
            current["tds_ppm"] = value
            current["tds_conversion_method"] = "legacy_demo_lookup_v1"
            current["tds_calibration_status"] = "estimated"
            current["tds_valid"] = False
            current["tds_source"] = "demo_estimate"

    if re.search(r"TDS demo estimate:\s*unavailable", stripped, re.IGNORECASE):
        current["tds_ppm"] = None
        current["tds_conversion_method"] = "legacy_demo_lookup_v1"
        current["tds_calibration_status"] = "invalid"
        current["tds_valid"] = False
        current["tds_source"] = "unavailable"

    patterns = (
        (
            "temperature_c",
            rf"Temperature:\s*({NUMBER_PATTERN})\s*C",
        ),
        (
            "tds_voltage_v",
            rf"TDS voltage \(A3\):\s*({NUMBER_PATTERN})\s*V",
        ),
        (
            "turbidity_voltage_v",
            rf"Turbidity voltage \(A1\):\s*({NUMBER_PATTERN})\s*V",
        ),
        (
            "tds_raw_adc",
            r"TDS raw ADC \(A3\):\s*(-?\d+)",
        ),
        (
            "turbidity_raw_adc",
            r"Turbidity raw ADC \(A1\):\s*(-?\d+)",
        ),
        (
            "cloudiness_index",
            rf"(?:Legacy relative sensor index|(?:Legacy relative )?Cloudiness index)(?: \(0-100; not NTU\))?:\s*({NUMBER_PATTERN})(?:\s*/\s*100)?(?:\s*\([^)]*\))?",
        ),
        (
            "turbidity_ntu",
            rf"Turbidity calibrated value:\s*({NUMBER_PATTERN})\s*NTU",
        ),
    )

    for field, pattern in patterns:
        match = re.search(pattern, stripped, re.IGNORECASE)
        if match:
            value = finite_number(match.group(1))
            if value is not None:
                current[field] = int(value) if field.endswith("_raw_adc") else value
                if field == "turbidity_ntu":
                    current["turbidity_calibration_status"] = "calibrated"

    if re.search(r"Temperature:\s*(?:sensor error|disconnected|unavailable)", stripped, re.IGNORECASE):
        current["temperature_c"] = None


class SerialSensorReader:
    def __init__(self):
        self.port = os.getenv("SERIAL_PORT", "COM5")
        self.baudrate = int(os.getenv("SERIAL_BAUDRATE", "115200"))

        self.running = False
        self.thread = None
        self.serial_connection = None
        self.lock = threading.Lock()

        self.latest_reading = None
        self.last_error = None
        self.on_reading = None
        self.on_event = None
        self._last_notified_status = "disconnected"

    def _notify_status(self, status, event_type, message):
        with self.lock:
            if self._last_notified_status == status:
                return
            self._last_notified_status = status
            callback = self.on_event

        if callable(callback):
            try:
                callback(event_type, message)
            except Exception as exc:
                print("[Serial reader] Could not record status event:", exc)

    def status(self):
        with self.lock:
            connection = self.serial_connection
            connected = (
                connection is not None
                and connection.is_open
            )

            return {
                "status": (
                    "connected"
                    if connected
                    else "error"
                    if self.last_error
                    else "disconnected"
                ),
                "port": self.port,
                "baudrate": self.baudrate,
                "last_error": self.last_error,
                "latest_reading": (
                    dict(self.latest_reading)
                    if self.latest_reading
                    else None
                ),
            }

    def start(self):
        if self.running:
            return

        self.running = True
        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="serial-sensor-reader",
        )
        self.thread.start()

    def stop(self):
        self.running = False

        connection = self.serial_connection
        if connection is not None:
            try:
                connection.close()
            except (serial.SerialException, OSError):
                pass

    def _run(self):
        while self.running:
            connection = None

            try:
                available_ports = {
                    port.device for port in list_ports.comports()
                }

                if self.port not in available_ports:
                    error_message = (
                        f"{self.port} not detected. "
                        "Check USB cable and COM port."
                    )
                    with self.lock:
                        self.last_error = error_message
                    self._notify_status(
                        "error",
                        "serial_error",
                        f"Serial reader error: {error_message}",
                    )
                    time.sleep(3)
                    continue

                connection = serial.Serial(
                    self.port,
                    self.baudrate,
                    timeout=1,
                )
                with self.lock:
                    self.serial_connection = connection
                    self.last_error = None

                self._notify_status(
                    "connected",
                    "serial_connected",
                    f"Serial reader connected to {self.port} at {self.baudrate} baud.",
                )

                print(
                    f"[Serial reader] Connected to "
                    f"{self.port} at {self.baudrate} baud."
                )

                current = {}

                while self.running and connection.is_open:
                    raw = connection.readline()

                    if not raw:
                        continue

                    line = raw.decode(
                        "utf-8",
                        errors="ignore",
                    ).strip()

                    if not line:
                        continue

                    update_reading_from_line(line, current)

                    # A separator ends a report after values are read. A failed
                    # temperature probe must not discard other sensor values.
                    if line.startswith("---"):
                        has_sensor_data = any(current.get(field) is not None for field in SENSOR_VALUE_FIELDS)
                        if has_sensor_data:
                            reading = {
                                **current,
                                "recorded_at": datetime.now(
                                    timezone.utc
                                ).isoformat(),
                            }

                            with self.lock:
                                self.latest_reading = dict(reading)
                                self.last_error = None

                            print(
                                "[Serial reader] Reading received:",
                                reading,
                            )

                            callback = self.on_reading

                            if callable(callback):
                                try:
                                    callback(reading)
                                except Exception as exc:
                                    with self.lock:
                                        self.last_error = (
                                            f"Database save failed: {exc}"
                                        )
                                    print(
                                        "[Serial reader] Save failed:",
                                        exc,
                                    )
                                else:
                                    with self.lock:
                                        self.last_error = None

                        current = {}

            except (serial.SerialException, OSError) as exc:
                with self.lock:
                    self.last_error = str(exc)

                self._notify_status(
                    "error",
                    "serial_error",
                    f"Serial reader error: {exc}",
                )

                print("[Serial reader] Connection error:", exc)
                time.sleep(3)

            except Exception as exc:
                with self.lock:
                    self.last_error = str(exc)

                self._notify_status(
                    "error",
                    "serial_error",
                    f"Serial reader error: {exc}",
                )

                print("[Serial reader] Unexpected error:", exc)
                time.sleep(3)

            finally:
                if connection is not None:
                    try:
                        if connection.is_open:
                            connection.close()
                    except (serial.SerialException, OSError):
                        pass

                with self.lock:
                    if self.serial_connection is connection:
                        self.serial_connection = None

                with self.lock:
                    was_connected = self._last_notified_status == "connected"

                if was_connected:
                    self._notify_status(
                        "disconnected",
                        "serial_disconnected",
                        f"Serial reader disconnected from {self.port}.",
                    )

                if self.running:
                    time.sleep(1)


reader = SerialSensorReader()
