import sys
import math
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import forecasting


class StubRegressor:
    predictions = {"Random Forest": 24.0, "XGBoost": 25.0}

    def __init__(self, name):
        self.name = name
        self.fit_rows = None
        self.target_rows = None

    def fit(self, features, targets):
        self.fit_rows = features
        self.target_rows = targets
        return self

    def predict(self, features):
        return [self.predictions[self.name] for _ in features]


class BrokenRegressor(StubRegressor):
    def fit(self, features, targets):
        raise RuntimeError("fit failed")


class ForecastingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 24, 2, tzinfo=timezone.utc)

    def series(self, count=16):
        return [
            {
                "timestamp": self.now - timedelta(hours=count - index - 1),
                "value": 20.0 + (index % 4) * 0.5,
                "samples": 2,
            }
            for index in range(count)
        ]

    @staticmethod
    def factory(name):
        return lambda: StubRegressor(name), None

    def test_forecast_runs_random_forest_and_xgboost_as_separate_regressors(self):
        history = self.series()
        current = {"timestamp": self.now, "value": history[-1]["value"]}
        factory_calls = []

        def factory(name):
            factory_calls.append(name)
            return self.factory(name)

        with patch.object(forecasting, "_model_factory", side_effect=factory):
            result = forecasting.forecast_series(
                "temperature_c", history, current, self.now + timedelta(hours=6), 6
            )

        self.assertEqual(factory_calls, ["Random Forest", "XGBoost"])
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["models"]["Random Forest"]["predicted"], 24.0)
        self.assertEqual(result["models"]["XGBoost"]["predicted"], 25.0)
        self.assertEqual(result["models"]["Random Forest"]["forecast_points"][0]["timestamp"], (self.now + timedelta(hours=6)).isoformat())
        self.assertIsNone(result.get("predicted"))

    def test_limited_history_trains_when_a_horizon_pair_is_available(self):
        history = self.series(count=3)
        current = {"timestamp": self.now, "value": history[-1]["value"]}
        with patch.object(forecasting, "_model_factory", side_effect=lambda name: self.factory(name)):
            result = forecasting.forecast_series(
                "temperature_c", history, current, self.now + timedelta(hours=1), 1
            )
        self.assertEqual(result["status"], "available")
        self.assertGreaterEqual(result["models"]["Random Forest"]["training_samples"], 1)

    def test_missing_calibrated_turbidity_is_unavailable_without_index_conversion(self):
        with patch.object(forecasting, "_model_factory") as factory:
            result = forecasting.forecast_series(
                "turbidity_ntu", [], None, self.now + timedelta(hours=1), 1
            )
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["models"]["Random Forest"]["predicted"])
        self.assertIn("legacy relative sensor index is not used or converted", result["reason"])
        factory.assert_not_called()

    def test_one_model_failure_does_not_silently_substitute_the_other(self):
        history = self.series()
        current = {"timestamp": self.now, "value": history[-1]["value"]}

        def factory(name):
            if name == "Random Forest":
                return lambda: BrokenRegressor(name), None
            return lambda: StubRegressor(name), None

        with patch.object(forecasting, "_model_factory", side_effect=factory):
            result = forecasting.forecast_series(
                "temperature_c", history, current, self.now + timedelta(hours=1), 1
            )
        self.assertEqual(result["models"]["Random Forest"]["status"], "model_unavailable")
        self.assertEqual(result["models"]["XGBoost"]["status"], "available")
        self.assertEqual(result["models"]["XGBoost"]["predicted"], 25.0)

    def test_out_of_range_model_output_is_unavailable(self):
        history = self.series()
        current = {"timestamp": self.now, "value": history[-1]["value"]}
        original = StubRegressor.predictions
        StubRegressor.predictions = {"Random Forest": 200.0, "XGBoost": 200.0}
        try:
            with patch.object(forecasting, "_model_factory", side_effect=lambda name: self.factory(name)):
                result = forecasting.forecast_series(
                    "temperature_c", history, current, self.now + timedelta(hours=1), 1
                )
        finally:
            StubRegressor.predictions = original
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["models"]["XGBoost"]["status"], "invalid_prediction")
        self.assertIsNone(result["models"]["XGBoost"]["predicted"])

    def test_all_dashboard_horizons_use_the_model_forecaster(self):
        rows = []
        for point in self.series(count=100):
            index = int((point["timestamp"] - (self.now - timedelta(hours=99))).total_seconds() / 3600)
            rows.append({
                "recorded_at": point["timestamp"],
                "temperature_c": 20 + index % 4,
                "temperature_samples": 2,
                "tds_ppm": 100 + index % 9,
                "tds_samples": 2,
                "turbidity_ntu": 1 + (index % 5) / 10,
                "turbidity_samples": 2,
            })
        latest = {
            "temperature_at": self.now, "temperature_c": rows[-1]["temperature_c"],
            "tds_at": self.now, "tds_ppm": rows[-1]["tds_ppm"],
            "turbidity_at": self.now, "turbidity_ntu": rows[-1]["turbidity_ntu"],
            "cloudiness_at": self.now, "cloudiness_index": 40,
        }
        with (
            patch.object(forecasting, "_fetch_hourly_history", return_value=rows),
            patch.object(forecasting, "_fetch_latest_valid", return_value=latest),
            patch.object(forecasting, "_model_factory", side_effect=lambda name: self.factory(name)),
            patch.object(forecasting, "TDS_CALIBRATION_METHOD", None),
        ):
            for horizon in (1, 6, 12, 24, 72):
                with self.subTest(horizon=horizon):
                    result = forecasting.forecast_readings(horizon, now=self.now)
                    self.assertEqual(result["target_at"], (self.now + timedelta(hours=horizon)).isoformat())
                    self.assertEqual(result["horizon_hours"], horizon)
                    self.assertNotIn("overall_assessment", result)
                    for metric in forecasting.PARAMETERS:
                        self.assertEqual(result["parameters"][metric]["status"], "available")
                        self.assertEqual(set(result["parameters"][metric]["models"]), {"Random Forest", "XGBoost"})

    def test_real_regressors_train_and_predict_from_historical_values(self):
        for metric, base in (("temperature_c", 20.0), ("tds_ppm", 100.0), ("turbidity_ntu", 1.0)):
            history = self.series(count=12)
            for index, point in enumerate(history):
                point["value"] = base + (index % 4) * 0.1
            current = {"timestamp": self.now, "value": history[-1]["value"]}
            result = forecasting.forecast_series(
                metric, history, current, self.now + timedelta(hours=1), 1
            )
            with self.subTest(metric=metric):
                self.assertEqual(result["status"], "available")
                for name in forecasting.MODEL_NAMES:
                    with self.subTest(model=name):
                        model = result["models"][name]
                        self.assertEqual(model["status"], "available")
                        self.assertTrue(math.isfinite(model["predicted"]))
                        self.assertGreater(model["training_samples"], 0)

    def test_regressor_factory_creates_the_required_algorithms(self):
        from sklearn.ensemble import RandomForestRegressor
        from xgboost import XGBRegressor

        random_forest_factory, random_forest_error = forecasting._model_factory("Random Forest")
        xgboost_factory, xgboost_error = forecasting._model_factory("XGBoost")
        self.assertIsNone(random_forest_error)
        self.assertIsNone(xgboost_error)
        self.assertIsInstance(random_forest_factory(), RandomForestRegressor)
        self.assertIsInstance(xgboost_factory(), XGBRegressor)

    def test_invalid_horizon_is_rejected(self):
        with self.assertRaises(ValueError):
            forecasting.forecast_readings(0, now=self.now)


if __name__ == "__main__":
    unittest.main()
