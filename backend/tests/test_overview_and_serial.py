import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main, migrate
from app.serial_reader import update_reading_from_line


class FakeCursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, query, parameters=()):
        self.calls.append((query, parameters))

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, row=None, rows=None):
        self.fake_cursor = FakeCursor(row, rows)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def cursor(self, **_kwargs):
        return self.fake_cursor


class TdsValidationAndSerialTests(unittest.TestCase):
    def test_existing_firmware_estimate_maps_once_to_canonical_ppm_even_with_legacy_false_flag(self):
        reading = {
            "tds_voltage_v": 0.378,
            "tds_ppm": 500,
            "tds_valid": False,
            "tds_calibration_status": "estimated",
            "tds_source": "demo_estimate",
            "tds_conversion_method": "legacy_demo_lookup_v1",
        }
        with patch.object(main, "TDS_CALIBRATION_METHOD", "tds_curve_v2"):
            fields = main.tds_sample_fields(reading)

        self.assertEqual(fields["ppm"], 500)
        self.assertEqual(fields["legacy_demo_estimate_ppm"], 500)
        self.assertTrue(fields["valid"])
        self.assertEqual(fields["source"], "demo_estimate")
        self.assertEqual(fields["calibration_status"], "estimated")
        self.assertEqual(fields["conversion_method"], "legacy_demo_lookup_v1")

    def test_missing_invalid_and_out_of_range_tds_values_stay_unavailable(self):
        base = {
            "tds_voltage_v": 0.378,
            "tds_ppm": 500,
            "tds_valid": False,
            "tds_calibration_status": "estimated",
            "tds_source": "demo_estimate",
            "tds_conversion_method": "legacy_demo_lookup_v1",
        }
        for invalid in (
            {key: value for key, value in base.items() if key != "tds_ppm"},
            {**base, "tds_ppm": None},
            {**base, "tds_ppm": float("nan")},
            {**base, "tds_ppm": -1},
            {**base, "tds_voltage_v": 0.008},
            {**base, "tds_source": "unavailable"},
            {**base, "tds_calibration_status": "invalid"},
        ):
            fields = main.tds_sample_fields(invalid)
            self.assertIsNone(fields["ppm"], invalid)
            self.assertFalse(fields["valid"], invalid)

    def test_calibrated_values_respect_optional_method_pin(self):
        reading = {
            "tds_ppm": 123.4,
            "tds_valid": True,
            "tds_calibration_status": "calibrated",
            "tds_source": "calibrated",
            "tds_conversion_method": "tds_curve_v2",
        }
        with patch.object(main, "TDS_CALIBRATION_METHOD", "tds_curve_v2"):
            self.assertEqual(main.tds_sample_fields(reading)["ppm"], 123.4)
        with patch.object(main, "TDS_CALIBRATION_METHOD", "tds_curve_v3"):
            self.assertIsNone(main.tds_sample_fields(reading)["ppm"])

    def test_json_serial_parser_preserves_firmware_ppm_and_never_converts_voltage(self):
        reading = {}
        update_reading_from_line(
            '{"tds_voltage_v":0.378,"tds_ppm":500,"tds_valid":false,'
            '"tds_calibration_status":"estimated","tds_source":"demo_estimate",'
            '"tds_conversion_method":"legacy_demo_lookup_v1","cloudiness_index":0}',
            reading,
        )
        self.assertEqual(reading["tds_ppm"], 500)
        self.assertEqual(reading["tds_voltage_v"], 0.378)
        self.assertEqual(reading["tds_source"], "demo_estimate")
        self.assertEqual(reading["cloudiness_index"], 0)
        self.assertEqual(main.tds_sample_fields(reading)["ppm"], 500)

    def test_json_serial_parser_preserves_explicit_sensor_error_flags(self):
        reading = {}
        update_reading_from_line(
            '{"temperature_c":25,"temperature_valid":false,'
            '"cloudiness_index":45,"cloudiness_valid":false}',
            reading,
        )
        self.assertFalse(reading["temperature_valid"])
        self.assertFalse(reading["cloudiness_valid"])
        self.assertFalse(main.cloudiness_sample_fields(reading)["valid"])

    def test_voltage_without_conversion_result_does_not_invent_ppm(self):
        reading = {}
        update_reading_from_line('{"tds_voltage_v":0.378}', reading)
        self.assertNotIn("tds_ppm", reading)
        self.assertIsNone(main.tds_sample_fields(reading)["ppm"])

    def test_text_conversion_result_and_temperature_failure_parse_independently(self):
        reading = {}
        update_reading_from_line("TDS demo estimate: 500 ppm (not calibrated)", reading)
        update_reading_from_line("TDS voltage (A3): 0.3780 V", reading)
        update_reading_from_line("Temperature: Sensor error", reading)
        update_reading_from_line("Cloudiness index: 42.5 / 100 (demo index)", reading)
        self.assertEqual(main.tds_sample_fields(reading)["ppm"], 500)
        self.assertIsNone(reading["temperature_c"])
        self.assertEqual(main.cloudiness_sample_fields(reading)["index"], 42.5)

    def test_current_firmware_keeps_relative_cloudiness_separate_from_ntu_field(self):
        sketch = (Path(__file__).resolve().parents[2] / "arduino" / "Arduino_Code.ino").read_text(encoding="utf-8")
        self.assertIn("float calculateTDS(float voltage, float tempC)", sketch)
        self.assertIn("float calculateCloudiness(float voltage)", sketch)
        self.assertIn("return constrain(index, 0.0, 100.0)", sketch)
        self.assertIn("The resulting NTU-like values are not validated against", sketch)
        self.assertIn("cloudiness_index", sketch)
        self.assertIn("turbidity_ntu", sketch)

class TdsPersistenceAndApiTests(unittest.TestCase):
    def test_database_insert_stores_one_canonical_ppm_and_retains_source_metadata(self):
        connection = FakeConnection(row=(7,))
        reading = {
            "recorded_at": "2026-10-24T02:00:00+00:00",
            "tds_voltage_v": 0.378,
            "tds_ppm": 500,
            "tds_valid": False,
            "tds_calibration_status": "estimated",
            "tds_source": "demo_estimate",
            "tds_conversion_method": "legacy_demo_lookup_v1",
            "cloudiness_index": 0,
        }
        with patch.object(main, "get_connection", return_value=connection):
            main.save_serial_reading(reading)

        query, parameters = next(
            (query, parameters)
            for query, parameters in connection.fake_cursor.calls
            if "INSERT INTO sensor_readings" in query
        )
        self.assertEqual(query.count("%s"), len(parameters))
        self.assertIn("tds_ppm", query)
        self.assertIn("tds_source", query)
        self.assertEqual(parameters[6:12], (500.0, 500.0, True, "estimated", "legacy_demo_lookup_v1", "demo_estimate"))
        self.assertEqual(parameters[17:19], (0.0, True))

    def test_database_persists_tds_when_unrelated_sensors_are_missing(self):
        connection = FakeConnection(row=(4,))
        reading = {
            "recorded_at": "2026-10-24T02:00:00+00:00",
            "tds_voltage_v": 0.378,
            "tds_ppm": 500,
            "tds_valid": False,
            "tds_calibration_status": "estimated",
            "tds_source": "demo_estimate",
            "tds_conversion_method": "legacy_demo_lookup_v1",
        }
        with patch.object(main, "get_connection", return_value=connection):
            main.save_serial_reading(reading)
        params = next(params for query, params in connection.fake_cursor.calls if "INSERT INTO sensor_readings" in query)
        self.assertEqual(params[6], 500.0)
        self.assertTrue(params[8])
        self.assertIsNone(params[2])
        self.assertIsNone(params[17])

    def test_latest_reading_api_returns_canonical_tds_and_separate_latest_sensor_value(self):
        latest_row = {
            "id": 2,
            "recorded_at": datetime(2026, 10, 24, 2, tzinfo=timezone.utc),
            "tds_ppm": None,
            "tds_valid": False,
            "tds_source": "unavailable",
        }
        latest_tds = {
            "tds_ppm": 500,
            "recorded_at": datetime(2026, 10, 24, 1, 59, tzinfo=timezone.utc),
            "tds_source": "demo_estimate",
            "tds_valid": True,
            "tds_calibration_status": "estimated",
            "tds_conversion_method": "legacy_demo_lookup_v1",
        }
        with (
            patch.object(main, "latest_row", return_value=latest_row),
            patch.object(main, "latest_valid_tds", return_value=main.row_to_dict(latest_tds)),
            patch.object(main, "latest_sensor_snapshot", return_value={}),
        ):
            result = main.readings_latest()
        self.assertIsNone(result["reading"]["tds_ppm"])
        self.assertEqual(result["latest_valid_tds"]["tds_ppm"], 500)
        self.assertEqual(result["latest_valid_tds"]["tds_source"], "demo_estimate")
        self.assertNotIn("tds_estimate_ppm", result["reading"])

    def test_latest_row_query_reads_canonical_field_without_legacy_lookup_case(self):
        connection = FakeConnection(row={"tds_ppm": 500, "tds_source": "demo_estimate"})
        with patch.object(main, "get_connection", return_value=connection):
            result = main.latest_row()
        query, _ = connection.fake_cursor.calls[-1]
        self.assertIn("r.tds_ppm", query)
        self.assertIn("r.tds_source", query)
        self.assertNotIn("CASE WHEN r.tds_conversion_method", query)
        self.assertEqual(result["tds_ppm"], 500)


class AnalyticsAndOverviewTests(unittest.TestCase):
    def summary_row(self):
        return {
            "reading_count": 20,
            "avg_temp": 25.5,
            "min_temp": 24.0,
            "max_temp": 27.0,
            "valid_temperature_samples": 18,
            "avg_tds": 127.17,
            "min_tds": 100.0,
            "max_tds": 200.0,
            "valid_tds_samples": 5,
            "latest_tds": 150.0,
            "latest_tds_at": datetime(2026, 10, 24, 2, tzinfo=timezone.utc),
            "tds_source": "demo_estimate",
            "latest_tds_method": "legacy_demo_lookup_v1",
            "avg_turbidity": None,
            "min_turbidity": None,
            "max_turbidity": None,
            "valid_turbidity_samples": 0,
            "avg_cloudiness_index": 45.0,
            "min_cloudiness_index": 30.0,
            "max_cloudiness_index": 60.0,
            "valid_cloudiness_samples": 4,
            "latest_temp": 25.8,
            "latest_temp_at": datetime(2026, 10, 24, 2, tzinfo=timezone.utc),
            "latest_turbidity": None,
            "latest_turbidity_at": None,
            "latest_cloudiness_index": 30.0,
            "latest_cloudiness_at": datetime(2026, 10, 24, 2, tzinfo=timezone.utc),
        }

    def test_analytics_summary_uses_one_valid_tds_cohort_and_one_time_window(self):
        connection = FakeConnection(row=self.summary_row())
        with (
            patch.object(main, "TDS_CALIBRATION_METHOD", "tds_curve_v2"),
            patch.object(main, "get_connection", return_value=connection),
        ):
            result = main.analytics_summary(hours=1)

        query, parameters = connection.fake_cursor.calls[-1]
        candidates = query.split("tds_candidates AS", 1)[1].split("selected_tds_source", 1)[0]
        self.assertEqual(query.count("%s"), len(parameters))
        self.assertEqual(len(parameters), 2)
        self.assertEqual(parameters[1], "tds_curve_v2")
        self.assertIn("WHERE recorded_at >= %s", query)
        self.assertIn("tds_source = 'demo_estimate'", candidates)
        self.assertIn("tds_source = 'calibrated'", candidates)
        self.assertIn("COALESCE(tds_conversion_method, '')", query)
        self.assertNotIn("temperature_valid", candidates)
        self.assertNotIn("cloudiness_valid", candidates)
        self.assertIn("selected_tds AS", query)
        self.assertEqual(result["period_hours"], 1)
        self.assertEqual(result["tds_ppm"]["average"], 127.17)
        self.assertEqual(result["tds_ppm"]["minimum"], 100.0)
        self.assertEqual(result["tds_ppm"]["maximum"], 200.0)
        self.assertEqual(result["tds_ppm"]["latest"], 150.0)
        self.assertEqual(result["tds_ppm"]["valid_samples"], 5)
        self.assertEqual(result["tds_ppm"]["source"], "demo_estimate")
        self.assertEqual(result["tds_ppm"]["unit"], "ppm")
        self.assertNotIn("tds_estimate_ppm", result)

    def test_overview_average_uses_canonical_tds_average_and_minimum_maximum(self):
        summary = {
            "temperature_c": {"average": 25.5, "minimum": 24, "maximum": 27, "valid_samples": 18},
            "tds_ppm": {"average": 127.17, "minimum": 100, "maximum": 200, "valid_samples": 5,
                        "source": "demo_estimate", "data_quality": "estimated", "calibration_status": "estimated"},
            "turbidity_ntu": {"average": None, "minimum": None, "maximum": None, "valid_samples": 0,
                               "calibrated_readings_only": True},
            "cloudiness_index": {"average": 45, "minimum": 30, "maximum": 60, "valid_samples": 4},
            "time_window": {"hours": 24},
        }
        result = main.overview_averages(summary)
        self.assertEqual(result["tds"]["value"], 127.17)
        self.assertEqual(result["tds"]["minimum"], 100)
        self.assertEqual(result["tds"]["maximum"], 200)
        self.assertEqual(result["tds"]["valid_samples"], 5)
        self.assertEqual(result["tds"]["source"], "demo_estimate")
        self.assertNotIn("demo_estimate", result["tds"])

    def test_overview_endpoint_exposes_average_purpose_status_and_refresh_timestamp(self):
        average_summary = {
            "temperature_c": {"average": 25, "minimum": 24, "maximum": 26, "valid_samples": 3},
            "tds_ppm": {"average": 127.17, "minimum": 120, "maximum": 130, "valid_samples": 3,
                        "source": "demo_estimate", "data_quality": "estimated", "calibration_status": "estimated"},
            "turbidity_ntu": {"average": None, "minimum": None, "maximum": None, "valid_samples": 0,
                               "calibrated_readings_only": True},
            "cloudiness_index": {"average": 40, "minimum": 35, "maximum": 45, "valid_samples": 3},
            "time_window": {"hours": 24, "start": "2026-10-23T02:00:00+00:00", "end": "2026-10-24T02:00:00+00:00"},
        }
        sensor_snapshot = {
            "temperature_at": datetime.now(timezone.utc), "temperature_c": 25,
            "tds_at": datetime.now(timezone.utc), "tds_ppm": 127.17, "tds_source": "demo_estimate",
            "tds_conversion_method": "legacy_demo_lookup_v1", "tds_voltage_v": 0.378, "tds_raw_adc": None,
            "cloudiness_at": datetime.now(timezone.utc), "cloudiness_index": 40,
        }
        serial_status = {
            "status": "connected", "port": "COM5", "baudrate": 115200, "last_error": None,
            "latest_reading": {"recorded_at": datetime.now(timezone.utc).isoformat()},
        }
        with (
            patch.object(main, "latest_row", return_value=None),
            patch.object(main, "analytics_summary", return_value={**average_summary, "time_window": average_summary["time_window"]}),
            patch.object(main.reader, "status", return_value=serial_status),
            patch.object(main, "recent_system_events", return_value=[]),
            patch.object(main, "latest_sensor_snapshot", return_value=sensor_snapshot),
        ):
            result = main.overview()

        self.assertEqual(result["averages"]["tds"]["average"], 127.17)
        self.assertEqual(result["latest_valid_tds"]["tds_ppm"], 127.17)
        self.assertEqual(result["system_status"], "degraded")
        self.assertEqual(result["sensor_health"]["turbidity"]["status"], "unavailable")
        self.assertEqual([item["label"] for item in result["purpose_assessments"]], ["Agriculture", "Household", "Aquatic"])
        self.assertEqual([item["status"] for item in result["purpose_assessments"]], ["met", "met", "not_met"])
        self.assertTrue(result["timestamp"])

    def test_no_tds_samples_remain_null_and_have_zero_samples(self):
        row = self.summary_row()
        row.update({"avg_tds": None, "min_tds": None, "max_tds": None, "valid_tds_samples": 0,
                    "latest_tds": None, "latest_tds_at": None, "tds_source": None, "latest_tds_method": None})
        with (
            patch.object(main, "TDS_CALIBRATION_METHOD", None),
            patch.object(main, "get_connection", return_value=FakeConnection(row=row)),
        ):
            result = main.analytics_summary(hours=24)
        self.assertIsNone(result["tds_ppm"]["average"])
        self.assertIsNone(result["tds_ppm"]["latest"])
        self.assertEqual(result["tds_ppm"]["valid_samples"], 0)
        self.assertEqual(result["tds_ppm"]["source"], "unavailable")


class PurposeDetectionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)

    def reading(self, **changes):
        result = {
            "recorded_at": self.now,
            "temperature_c": 25.0,
            "temperature_valid": True,
            "tds_ppm": 150.0,
            "tds_valid": True,
            "tds_source": "calibrated",
            "tds_calibration_status": "calibrated",
            "tds_conversion_method": "curve-v1",
            "cloudiness_index": 40.0,
            "cloudiness_valid": True,
            "turbidity_ntu": None,
            "turbidity_valid": False,
            "turbidity_calibration_status": "calibration_pending",
        }
        result.update(changes)
        return result

    def assess(self, reading):
        with (
            patch.object(main, "TDS_CALIBRATION_METHOD", None),
            patch.object(main, "SENSOR_FRESHNESS_SECONDS", 30),
        ):
            return {
                item["key"]: item
                for item in main.assess_latest_purposes(reading, now=self.now)
            }

    def test_strict_tds_boundaries_and_matching_values(self):
        household = self.assess(self.reading(tds_ppm=199.999))
        at_200 = self.assess(self.reading(tds_ppm=200))
        at_500 = self.assess(self.reading(tds_ppm=500))
        aquatic = self.assess(self.reading(tds_ppm=500.001))

        self.assertEqual(household["household"]["status"], "met")
        self.assertEqual(at_200["household"]["status"], "not_met")
        self.assertEqual(at_500["aquatic"]["status"], "not_met")
        self.assertEqual(aquatic["aquatic"]["status"], "met")

    def test_turbidity_boundary_is_strict_for_both_thresholds(self):
        for tds in (150, 600):
            with self.subTest(tds=tds):
                result = self.assess(self.reading(tds_ppm=tds, cloudiness_index=100))
                self.assertEqual(result["household"]["status"], "not_met")
                self.assertEqual(result["aquatic"]["status"], "not_met")

    def test_missing_or_invalid_required_readings_cannot_be_assessed(self):
        missing_tds = self.assess(self.reading(tds_ppm=None, tds_valid=False))
        missing_cloudiness = self.assess(self.reading(cloudiness_index=None, cloudiness_valid=False))
        invalid_tds = self.assess(self.reading(tds_ppm=float("nan")))
        invalid_cloudiness = self.assess(self.reading(cloudiness_index=float("inf")))

        for result in (missing_tds, invalid_tds):
            self.assertEqual(result["household"]["status"], "cannot_assess")
            self.assertEqual(result["aquatic"]["status"], "cannot_assess")
            self.assertEqual(result["agriculture"]["status"], "met")
        for result in (missing_cloudiness, invalid_cloudiness):
            self.assertEqual(result["household"]["status"], "cannot_assess")
            self.assertEqual(result["aquatic"]["status"], "cannot_assess")

    def test_explicit_error_flags_make_finite_sensor_values_unusable(self):
        invalid = self.reading(
            temperature_valid=False,
            tds_valid=False,
            cloudiness_valid=False,
        )
        result = self.assess(invalid)
        self.assertEqual([result[key]["status"] for key in ("agriculture", "household", "aquatic")], ["cannot_assess"] * 3)

    def test_stale_reading_cannot_assess_any_purpose(self):
        stale = self.reading(recorded_at=self.now - timedelta(seconds=31))
        result = self.assess(stale)
        self.assertEqual([result[key]["status"] for key in ("agriculture", "household", "aquatic")], ["cannot_assess"] * 3)
        self.assertTrue(all("stale" in result[key]["reason"] for key in result))

    def test_latest_sensor_snapshot_checks_freshness_per_sensor(self):
        latest_valid_values = self.reading(
            recorded_at=self.now - timedelta(seconds=60),
            temperature_at=self.now - timedelta(seconds=60),
            tds_at=self.now,
            legacy_cloudiness_at=self.now,
        )
        result = self.assess(latest_valid_values)
        self.assertEqual(result["agriculture"]["status"], "met")
        self.assertEqual(result["household"]["status"], "met")
        self.assertEqual(result["aquatic"]["status"], "not_met")

        stale_tds = self.reading(tds_at=self.now - timedelta(seconds=31))
        result = self.assess(stale_tds)
        self.assertEqual(result["household"]["status"], "cannot_assess")
        self.assertEqual(result["aquatic"]["status"], "cannot_assess")
        self.assertEqual(result["agriculture"]["status"], "met")

    def test_temperature_never_changes_any_purpose_result(self):
        valid_temperature = self.assess(self.reading(tds_ppm=501))
        invalid_temperature = self.assess(self.reading(tds_ppm=501, temperature_c=float("nan"), temperature_valid=False))
        self.assertEqual(
            [valid_temperature[key]["status"] for key in ("agriculture", "household", "aquatic")],
            [invalid_temperature[key]["status"] for key in ("agriculture", "household", "aquatic")],
        )

    def test_agriculture_matches_when_any_fresh_valid_sensor_reading_exists(self):
        for reading in (
            self.reading(),
            self.reading(tds_ppm=None, tds_valid=False),
            self.reading(temperature_c=None, temperature_valid=False, tds_ppm=None, tds_valid=False),
        ):
            with self.subTest(reading=reading):
                self.assertEqual(self.assess(reading)["agriculture"]["status"], "met")

    def test_cloudiness_label_and_calibrated_turbidity_fallback_keep_their_units(self):
        relative = self.assess(self.reading())["household"]["readings"][1]
        ntu = self.assess(self.reading(
            cloudiness_index=None,
            cloudiness_valid=False,
            turbidity_ntu=1.5,
            turbidity_valid=True,
            turbidity_calibration_status="calibrated",
        ))["household"]["readings"][1]
        self.assertEqual((relative["label"], relative["unit"]), ("Relative cloudiness index (not NTU)", "index / 100"))
        self.assertEqual((ntu["label"], ntu["unit"]), ("Turbidity", "NTU"))

    def test_saving_a_new_reading_records_one_alert_for_each_matched_purpose(self):
        for tds, matched in ((150, {"Agriculture", "Household"}), (600, {"Agriculture", "Aquatic"})):
            with self.subTest(tds=tds):
                connection = FakeConnection(row=(7,))
                reading = {
                    "recorded_at": datetime.now(timezone.utc),
                    "temperature_c": 25,
                    "tds_ppm": tds,
                    "tds_valid": True,
                    "tds_source": "calibrated",
                    "tds_calibration_status": "calibrated",
                    "tds_conversion_method": "curve-v1",
                    "cloudiness_index": 40,
                }
                with (
                    patch.object(main, "TDS_CALIBRATION_METHOD", None),
                    patch.object(main, "get_connection", return_value=connection),
                ):
                    main.save_serial_reading(reading)

                events = [
                    parameters
                    for query, parameters in connection.fake_cursor.calls
                    if "INSERT INTO system_events" in query
                ]
                purpose_alerts = [event for event in events if event[1] == "purpose_alert"]
                self.assertEqual(len(purpose_alerts), 2)
                messages = [event[2] for event in purpose_alerts]
                self.assertEqual({message.split(" conditions met", 1)[0] for message in messages}, matched)
                self.assertTrue(all(f"TDS: {float(tds)} ppm" in message and "index / 100" in message for message in messages))

    def test_history_uses_same_source_selection_and_keeps_window_cutoff(self):
        timestamp = datetime.now(timezone.utc)
        row = {
            "id": 1, "recorded_at": timestamp, "temperature_c": None, "temperature_valid": False,
            "tds_raw_adc": None, "tds_voltage_v": 0.378, "tds_ppm": 500, "tds_source": "demo_estimate",
            "tds_valid": True, "tds_calibration_status": "estimated", "tds_conversion_method": "legacy_demo_lookup_v1",
            "turbidity_raw_adc": None, "turbidity_voltage_v": None, "turbidity_ntu": None,
            "turbidity_valid": False, "turbidity_calibration_status": "calibration_pending",
            "cloudiness_index": None, "cloudiness_valid": False,
        }
        connection = FakeConnection(rows=[row])
        with (
            patch.object(main, "TDS_CALIBRATION_METHOD", "tds_curve_v2"),
            patch.object(main, "get_connection", return_value=connection),
        ):
            result = main.readings_history(hours=1, limit=100)

        query, parameters = connection.fake_cursor.calls[-1]
        self.assertEqual(query.count("%s"), len(parameters))
        self.assertIn("WHERE recorded_at >= %s", query)
        self.assertIn("selected_tds_source", query)
        self.assertIn("tds_source = (SELECT tds_source FROM selected_tds_source)", query)
        self.assertNotIn("tds_legacy_demo_estimate_ppm", query)
        self.assertEqual(result["readings"][0]["tds_ppm"], 500)
        self.assertTrue(result["readings"][0]["tds_valid"])
        self.assertEqual(result["readings"][0]["tds_source"], "demo_estimate")


class SystemHealthTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.serial = {
            "status": "connected",
            "last_error": None,
            "latest_reading": {"recorded_at": self.now.isoformat()},
        }
        self.snapshot = {
            "temperature_at": self.now, "temperature_c": 25,
            "tds_at": self.now, "tds_ppm": 120, "tds_source": "calibrated",
            "turbidity_at": self.now, "turbidity_ntu": 1.2,
        }

    def test_all_recent_valid_sensors_and_live_serial_connection_are_online(self):
        with (
            patch.object(main, "DEVICE_FRESHNESS_SECONDS", 30),
            patch.object(main, "SENSOR_FRESHNESS_SECONDS", 30),
        ):
            result = main.system_health(self.serial, self.snapshot)
        self.assertEqual(result["system_status"], "online")
        self.assertEqual(result["serial_connection_status"], "connected")
        self.assertTrue(all(sensor["status"] == "valid" for sensor in result["sensor_health"].values()))

    def test_missing_sensor_value_degrades_system_for_each_sensor(self):
        for name, time_key, value_key in (
            ("temperature", "temperature_at", "temperature_c"),
            ("tds", "tds_at", "tds_ppm"),
            ("turbidity", "turbidity_at", "turbidity_ntu"),
        ):
            snapshot = {**self.snapshot, time_key: None, value_key: None}
            result = main.system_health(self.serial, snapshot)
            self.assertEqual(result["system_status"], "degraded", name)
            self.assertEqual(result["sensor_health"][name]["status"], "unavailable", name)

    def test_stale_sensor_reading_degrades_system_for_each_sensor(self):
        old = self.now - timedelta(minutes=5)
        for name, time_key in (("temperature", "temperature_at"), ("tds", "tds_at"), ("turbidity", "turbidity_at")):
            snapshot = {**self.snapshot, time_key: old}
            with patch.object(main, "SENSOR_FRESHNESS_SECONDS", 30):
                result = main.system_health(self.serial, snapshot)
            self.assertEqual(result["system_status"], "degraded", name)
            self.assertEqual(result["sensor_health"][name]["status"], "stale", name)

    def test_stale_or_disconnected_serial_connection_is_offline(self):
        old_serial = {**self.serial, "latest_reading": {"recorded_at": (self.now - timedelta(minutes=5)).isoformat()}}
        with patch.object(main, "DEVICE_FRESHNESS_SECONDS", 30):
            stale = main.system_health(old_serial, self.snapshot)
        disconnected = main.system_health({**self.serial, "status": "disconnected"}, self.snapshot)
        self.assertEqual(stale["system_status"], "offline")
        self.assertEqual(stale["serial_connection_status"], "stale")
        self.assertEqual(disconnected["system_status"], "offline")
        self.assertEqual(disconnected["serial_connection_status"], "disconnected")


class EventsAndMigrationTests(unittest.TestCase):
    def test_event_insert_uses_parameterized_values(self):
        connection = FakeConnection()
        with patch.object(main, "get_connection", return_value=connection):
            main.record_system_event("serial_connected", "Reader connected.", 12)
        query, parameters = connection.fake_cursor.calls[-1]
        self.assertIn("INSERT INTO system_events", query)
        self.assertEqual(parameters, (12, "serial_connected", "Reader connected."))

    def test_recent_events_deduplicate_repeated_messages_and_keep_newest_first(self):
        timestamp = datetime.now(timezone.utc)
        connection = FakeConnection(rows=[{"id": 2, "event_type": "reading_saved", "message": "Saved", "timestamp": timestamp}])
        with patch.object(main, "get_connection", return_value=connection):
            result = main.recent_system_events()
        query, parameters = connection.fake_cursor.calls[-1]
        self.assertIn("ROW_NUMBER() OVER", query)
        self.assertIn("PARTITION BY event_type, message", query)
        self.assertIn("ORDER BY recorded_at DESC, id DESC", query)
        self.assertEqual(parameters, (8,))
        self.assertEqual(len(result), 1)

    def test_migration_promotes_estimates_without_recalculating_or_deleting_history(self):
        path = Path(__file__).resolve().parents[2] / "database" / "migrations" / "005_tds_canonical_source.sql"
        migration = path.read_text(encoding="utf-8").lower()
        self.assertIn("add column if not exists tds_source", migration)
        self.assertIn("tds_ppm = coalesce(tds_ppm, tds_legacy_demo_estimate_ppm)", migration)
        self.assertIn("tds_source = 'demo_estimate'", migration)
        self.assertIn("tds_valid = true", migration)
        self.assertNotIn("delete from", migration)
        self.assertNotIn("drop table", migration)
        self.assertNotIn("0.270", migration)
        self.assertIn("tds_source", migrate.REQUIRED_SENSOR_READING_COLUMNS)

    def test_legacy_column_migration_never_recalculates_missing_firmware_results(self):
        path = Path(__file__).resolve().parents[2] / "database" / "migrations" / "004_tds_demo_estimates.sql"
        migration = path.read_text(encoding="utf-8").lower()
        self.assertIn("add column if not exists tds_legacy_demo_estimate_ppm", migration)
        self.assertNotIn("update sensor_readings", migration)
        self.assertNotIn("tds_voltage_v", migration)
        self.assertNotIn("case", migration)
        self.assertNotIn("round(", migration)

    def test_migration_runner_verifies_columns_and_splits_sql_safely(self):
        existing = {(column,) for column in migrate.REQUIRED_SENSOR_READING_COLUMNS}
        cursor = FakeCursor(rows=list(existing))
        migrate.verify_sensor_readings_schema(cursor)
        query, parameters = cursor.calls[-1]
        self.assertIn("pg_attribute", query)
        self.assertEqual(parameters, ("sensor_readings",))
        statements = migrate.split_sql_statements(
            "-- delimiter; comment\nCOMMENT ON COLUMN t.note IS 'text; with ''quote'''; "
            "/* block; comment */ SELECT $$body; text$$; SELECT 1;"
        )
        self.assertEqual(len(statements), 3)
        self.assertIn("'text; with ''quote'''", statements[0])
        self.assertIn("$$body; text$$", statements[1])
        self.assertEqual(statements[2], "SELECT 1")


if __name__ == "__main__":
    unittest.main()
