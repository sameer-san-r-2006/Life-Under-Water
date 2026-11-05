import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main
from app.ml import dataset, service, training


class MlFeatureValidationTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            "temperature_c": 24.5,
            "temperature_valid": True,
            "tds_ppm": 140.0,
            "tds_valid": True,
            "tds_source": "calibrated",
            "tds_calibration_status": "calibrated",
            "tds_conversion_method": "calibrated_curve_v1",
            "cloudiness_index": 0,
            "cloudiness_valid": True,
        }

    def test_valid_zero_cloudiness_is_preserved_as_a_real_value(self):
        values = dataset.valid_feature_values(self.row)
        self.assertEqual(values, {
            "tds_ppm": 140.0,
            "temperature_c": 24.5,
            "cloudiness_index": 0.0,
        })

    def test_missing_or_uncalibrated_inputs_make_features_unavailable(self):
        missing_cloudiness = {**self.row, "cloudiness_index": None, "cloudiness_valid": False}
        estimate_only_tds = {
            **self.row,
            "tds_source": "demo_estimate",
            "tds_calibration_status": "estimated",
            "tds_conversion_method": "legacy_demo_lookup_v1",
        }
        self.assertIsNone(dataset.valid_feature_values(missing_cloudiness))
        self.assertIsNone(dataset.valid_feature_values(estimate_only_tds))

    def test_prediction_input_status_keeps_invalid_value_visible_but_unusable(self):
        row = {
            "id": 1,
            **self.row,
            "tds_source": "demo_estimate",
            "tds_calibration_status": "estimated",
            "tds_conversion_method": "legacy_demo_lookup_v1",
        }
        inputs, features = service._input_status(row)
        self.assertEqual(inputs["tds_ppm"]["value"], 140.0)
        self.assertEqual(inputs["tds_ppm"]["status"], "not_calibrated")
        self.assertIsNone(features)

    def test_out_of_range_cloudiness_is_not_substituted(self):
        invalid = {**self.row, "cloudiness_index": 101}
        self.assertIsNone(dataset.valid_feature_values(invalid))


class MlDatasetPipelineTests(unittest.TestCase):
    def test_export_includes_only_verified_valid_rows_and_reports_zero_correctly(self):
        valid = {
            "id": 11,
            "temperature_c": 24.5,
            "temperature_valid": True,
            "tds_ppm": 140.0,
            "tds_valid": True,
            "tds_source": "calibrated",
            "tds_calibration_status": "calibrated",
            "tds_conversion_method": "calibrated_curve_v1",
            "cloudiness_index": 0,
            "cloudiness_valid": True,
        }
        invalid = {**valid, "id": 12, "cloudiness_index": None, "cloudiness_valid": False}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            labels_path = root / "verified_labels.csv"
            with labels_path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=dataset.LABEL_FIELDS)
                writer.writeheader()
                writer.writerow({
                    "reading_id": 11, "sample_id": "sample-a",
                    "water_category": "Household", "label_verified": "true",
                })
                writer.writerow({
                    "reading_id": 12, "sample_id": "sample-b",
                    "water_category": "Aquatic", "label_verified": "true",
                })
                writer.writerow({
                    "reading_id": 99, "sample_id": "",
                    "water_category": "", "label_verified": "false",
                })
            with patch.object(dataset, "fetch_readings", return_value=[valid, invalid]):
                report = dataset.export_dataset(
                    labels_path,
                    root / "ml_dataset.csv",
                    root / "report.json",
                )
            with (root / "ml_dataset.csv").open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["cloudiness_index"], "0.0")
            self.assertEqual(report["dataset_size"], 1)
            self.assertEqual(report["class_counts"]["Household"], 1)
            self.assertEqual(report["class_counts"]["Aquatic"], 0)
            self.assertEqual(report["unverified_label_rows"], 1)
            self.assertEqual(report["labels_for_invalid_readings"], 1)
            self.assertIn("not calibrated NTU", report["feature_descriptions"]["cloudiness_index"])

    def test_no_verified_labels_produces_no_rows_and_no_class_balance_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            labels_path = root / "missing_labels.csv"
            with patch.object(dataset, "fetch_readings", return_value=[]):
                report = dataset.export_dataset(
                    labels_path,
                    root / "ml_dataset.csv",
                    root / "report.json",
                )
            self.assertEqual(report["dataset_size"], 0)
            self.assertEqual(report["class_counts"], {name: 0 for name in service.CLASS_NAMES})
            self.assertEqual(report["class_balance_status"], "not_assessable_no_verified_labels")
            self.assertEqual(training.load_dataset(root / "ml_dataset.csv"), [])


class MlStatusAndApiTests(unittest.TestCase):
    def test_untrained_models_have_no_metrics_or_preferred_model(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(service, "REGISTRY_PATH", Path(directory) / "missing-registry.json"):
                result = service.model_comparison()
        self.assertEqual(result["status"], "untrained")
        self.assertIsNone(result["preferred_model"])
        self.assertEqual([item["metrics"] for item in result["models"]], [None, None])

    def test_legacy_classifier_feature_metadata_distinguishes_relative_index_from_ntu(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(service, "REGISTRY_PATH", Path(directory) / "missing-registry.json"),
                patch.object(service, "DATASET_REPORT_PATH", Path(directory) / "missing-report.json"),
            ):
                result = service.model_information()
        self.assertIn("cloudiness_index", result["feature_names"])
        self.assertIn("not calibrated NTU", result["feature_descriptions"]["cloudiness_index"])

    def test_classifier_api_routes_are_disconnected_and_sensor_forecasts_remain_registered(self):
        paths = {route.path for route in main.app.routes if hasattr(route, "path")}
        self.assertTrue({
            "/api/health", "/api/status", "/api/overview",
            "/api/readings/latest", "/api/readings/history", "/api/devices",
            "/api/forecasts",
        }.issubset(paths))
        self.assertFalse(any(path.startswith("/api/ml/") for path in paths))


if __name__ == "__main__":
    unittest.main()
