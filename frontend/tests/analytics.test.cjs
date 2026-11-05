const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadApi(fetchImpl = async () => ({ ok: true, json: async () => ({}) })) {
  const source = fs.readFileSync(path.join(__dirname, "../js/api.js"), "utf8");
  const context = { window: {}, fetch: fetchImpl, AbortController, setTimeout, clearTimeout };
  vm.runInNewContext(source, context, { filename: "api.js" });
  return context.window.LUWApi;
}

function plain(value) { return JSON.parse(JSON.stringify(value)); }

const demoRow = {
  recorded_at: "2026-10-24T02:00:00Z",
  tds_ppm: 127.17,
  tds_valid: true,
  tds_source: "demo_estimate",
  tds_calibration_status: "estimated",
  tds_conversion_method: "legacy_demo_lookup_v1",
  tds_voltage_v: 0.378
};

test("history uses canonical tds_ppm and retains temperature and relative cloudiness points", () => {
  const api = loadApi();
  const result = api.normalizeHistory({
    tds_working_calibration_method: "tds_curve_v2",
    readings: [
      { ...demoRow, temperature_c: 25.5, temperature_valid: true, cloudiness_index: 0, cloudiness_valid: true },
      { recorded_at: "2026-10-24T02:01:00Z", tds_ppm: null, tds_valid: false, tds_source: "unavailable", tds_conversion_method: "legacy_demo_lookup_v1", temperature_c: 30, temperature_valid: false }
    ]
  });
  assert.deepEqual(plain(result.tds), [{ timestamp: demoRow.recorded_at, value: 127.17 }]);
  assert.deepEqual(plain(result.temperature), [{ timestamp: demoRow.recorded_at, value: 25.5 }]);
  assert.deepEqual(plain(result.cloudiness_index), [{ timestamp: demoRow.recorded_at, value: 0 }]);
  assert.equal(Object.hasOwn(result, "tds_estimate"), false);
});

test("summary uses one canonical TDS set for every statistic and sample count", () => {
  const api = loadApi();
  const result = api.normalizeSummary({
    period_hours: 24,
    reading_count: 10,
    tds_working_calibration_method: "tds_curve_v2",
    temperature_c: { average: 25.13, minimum: 24.8, maximum: 25.6, valid_samples: 8, latest: 25.4, latest_timestamp: "2026-10-24T02:00:00Z" },
    tds_ppm: {
      average: 127.17, minimum: 100, maximum: 200, valid_samples: 5, latest: 150,
      latest_timestamp: "2026-10-24T02:00:00Z", source: "demo_estimate", data_quality: "estimated",
      calibration_status: "estimated", calibration_method: "legacy_demo_lookup_v1", unit: "ppm"
    },
    turbidity_ntu: { average: null, valid_samples: 0, calibrated_readings_only: true },
    cloudiness_index: { average: 35, minimum: 20, maximum: 50, latest: 50, valid_samples: 3, relative_index_only: true }
  });
  assert.equal(result.tds.average, 127.17);
  assert.equal(result.tds.minimum, 100);
  assert.equal(result.tds.maximum, 200);
  assert.equal(result.tds.latest, 150);
  assert.equal(result.tds.valid_samples, 5);
  assert.equal(result.tds.source, "demo_estimate");
  assert.equal(result.temperature.valid_samples, 8);
  assert.equal(result.cloudiness_index.unit, "index / 100");
  assert.equal(result.turbidity.average, null);
});

test("selected time ranges call the history and summary routes while preserving the legacy alias", async () => {
  const urls = [];
  const api = loadApi(async url => {
    urls.push(url);
    return { ok: true, json: async () => ({ readings: [] }) };
  });
  const history = await api.history("7d");
  await api.summary("1h");
  assert.deepEqual(plain(history), { temperature: [], tds: [], turbidity: [], legacy_cloudiness_index: [], cloudiness_index: [] });
  assert.match(urls[0], /\/readings\/history\?hours=168&limit=500$/);
  assert.match(urls[1], /\/analytics-summary\?hours=1$/);
});

test("missing TDS values and zero samples stay unavailable rather than becoming zero", () => {
  const api = loadApi();
  const history = api.normalizeHistory({ readings: [{ recorded_at: demoRow.recorded_at, tds_ppm: null, tds_valid: false }] });
  const summary = api.normalizeSummary({
    tds_ppm: { average: 0, minimum: 0, maximum: 0, latest: 0, valid_samples: 0, source: "unavailable", data_quality: "unavailable" }
  });
  assert.deepEqual(plain(history.tds), []);
  assert.equal(summary.tds.average, null);
  assert.equal(summary.tds.latest, null);
  assert.equal(summary.tds.valid_samples, 0);
});

test("latest API maps latest valid TDS ppm even when the newest report has no TDS result", async () => {
  const api = loadApi(async () => ({ ok: true, json: async () => ({
    reading: { recorded_at: "2026-10-24T02:01:00Z", tds_ppm: null, tds_valid: false },
    latest_valid_tds: { ...demoRow, tds_valid: false }
  }) }));
  const result = await api.latest();
  assert.equal(result.tds.value, 127.17);
  assert.equal(result.tds.ppm, 127.17);
  assert.equal(result.tds.valid, true);
  assert.equal(result.tds.source, "demo_estimate");
  assert.equal(result.tds.timestamp, demoRow.recorded_at);
});

test("latest API rejects demo ppm without matching provenance or voltage range", async () => {
  const api = loadApi(async () => ({ ok: true, json: async () => ({
    latest_valid_tds: { ...demoRow, tds_conversion_method: "other_lookup" }
  }) }));
  const mismatchedMethod = await api.latest();
  assert.equal(mismatchedMethod.tds.value, null);

  const outOfRangeApi = loadApi(async () => ({ ok: true, json: async () => ({
    latest_valid_tds: { ...demoRow, tds_voltage_v: 0.008 }
  }) }));
  const outOfRange = await outOfRangeApi.latest();
  assert.equal(outOfRange.tds.value, null);
});

test("latest API keeps calibrated ppm when the backend's method filter matches", async () => {
  const calibrated = {
    tds_ppm: 142,
    tds_valid: true,
    tds_source: "calibrated",
    tds_calibration_status: "calibrated",
    tds_conversion_method: "tds_curve_v2"
  };
  const api = loadApi(async () => ({ ok: true, json: async () => ({
    tds_working_calibration_method: "tds_curve_v2",
    latest_valid_tds: calibrated
  }) }));
  const result = await api.latest();
  assert.equal(result.tds.value, 142);
  assert.equal(result.tds.source, "calibrated");
});

test("configured method filters calibrated values but does not exclude the tagged legacy source", async () => {
  const api = loadApi(async () => ({ ok: true, json: async () => ({
    tds_working_calibration_method: "tds_curve_v2",
    averages: { tds: { average: 127.17, minimum: 100, maximum: 150, valid_samples: 4,
      source: "demo_estimate", data_quality: "estimated", calibration_status: "estimated",
      calibration_method: "legacy_demo_lookup_v1" } }
  }) }));
  const overview = await api.overview();
  const summary = api.normalizeSummary({
    tds_working_calibration_method: "tds_curve_v2",
    tds_ppm: { average: 127.17, minimum: 100, maximum: 150, latest: 150, valid_samples: 4,
      source: "demo_estimate", data_quality: "estimated", calibration_status: "estimated",
      calibration_method: "legacy_demo_lookup_v1" }
  });
  assert.equal(overview.averages.tds.average, 127.17);
  assert.equal(summary.tds.average, 127.17);
});

test("invalid legacy estimate voltage and invalid calibrated method are rejected", () => {
  const api = loadApi();
  const result = api.normalizeHistory({
    tds_working_calibration_method: "tds_curve_v2",
    readings: [
      { ...demoRow, tds_voltage_v: 0.008 },
      { recorded_at: demoRow.recorded_at, tds_ppm: 100, tds_valid: true, tds_source: "calibrated", tds_calibration_status: "calibrated", tds_conversion_method: "tds_curve_v1" }
    ]
  });
  assert.deepEqual(plain(result.tds), []);
});

test("summary requires source, calibration state and method provenance to agree", () => {
  const api = loadApi();
  const result = api.normalizeSummary({
    tds_ppm: { average: 120, minimum: 100, maximum: 150, latest: 140, valid_samples: 4,
      source: "demo_estimate", data_quality: "estimated", calibration_status: "calibrated",
      calibration_method: "tds_curve_v2" }
  });
  assert.equal(result.tds.average, null);
  assert.equal(result.tds.valid_samples, 0);
});

test("device readings normalize canonical TDS and zero cloudiness", async () => {
  const api = loadApi(async () => ({ ok: true, json: async () => ({
    tds_working_calibration_method: "tds_curve_v2",
    devices: [{ device_key: "node-1", latest_reading: {
      recorded_at: demoRow.recorded_at, ...demoRow, cloudiness_index: 0, cloudiness_valid: true
    } }]
  }) }));
  const result = await api.devices();
  assert.equal(result.devices[0].latest_reading.tds.value, 127.17);
  assert.equal(result.devices[0].latest_reading.cloudiness.index, 0);
});

test("legacy cloudiness field stays separate from calibrated NTU", async () => {
  const api = loadApi(async () => ({ ok: true, json: async () => ({
    latest_reading: { recorded_at: demoRow.recorded_at, cloudiness_index: 0, cloudiness_valid: true, turbidity_ntu: null, turbidity_valid: false }
  }) }));
  const result = await api.overview();
  assert.equal(result.latest.cloudiness.value, 0);
  assert.equal(result.latest.legacy_cloudiness_index.value, 0);
  assert.equal(result.latest.turbidity.value, null);
});

test("forecast API requests the selected horizon and normalizes the two model outputs", async () => {
  const urls = [];
  const api = loadApi(async url => {
    urls.push(url);
    return { ok: true, json: async () => ({
      generated_at: "2026-10-24T02:00:00Z",
      target_at: "2026-10-24T08:00:00Z",
      horizon_hours: 6,
      parameters: {
        temperature_c: { status: "unavailable", historical_points: [], models: {
          "Random Forest": { status: "insufficient_training_data", predicted: null, forecast_points: [] },
          "XGBoost": { status: "insufficient_training_data", predicted: null, forecast_points: [] }
        } },
        tds_ppm: { status: "unavailable", historical_points: [], models: {
          "Random Forest": { status: "insufficient_training_data", predicted: null, forecast_points: [] },
          "XGBoost": { status: "insufficient_training_data", predicted: null, forecast_points: [] }
        } },
        turbidity_ntu: { status: "unavailable", historical_points: [], models: {
          "Random Forest": { status: "no_valid_data", predicted: null, forecast_points: [] },
          "XGBoost": { status: "no_valid_data", predicted: null, forecast_points: [] }
        } }
      }
    }) };
  });
  const result = await api.forecast(6);
  assert.match(urls[0], /\/forecasts\?horizon_hours=6$/);
  assert.equal(result.horizon_hours, 6);
  assert.equal(result.target_at, "2026-10-24T08:00:00Z");
  assert.equal(result.parameters.turbidity_ntu.unit, "NTU");
  assert.equal(result.parameters.turbidity_ntu.status, "unavailable");
  assert.equal(result.parameters.turbidity_ntu.predicted, null);
  assert.equal(Object.hasOwn(result, "overall_assessment"), false);
  assert.deepEqual(plain(result.parameters.turbidity_ntu.forecast_points), []);
  assert.deepEqual(Object.keys(plain(result.parameters.turbidity_ntu.models)), ["Random Forest", "XGBoost"]);
});

test("forecast API preserves separate Random Forest and XGBoost regression values", async () => {
  const target = "2026-10-24T08:00:00Z";
  const result = await loadApi(async () => ({ ok: true, json: async () => ({
    generated_at: "2026-10-24T02:00:00Z",
    target_at: target,
    horizon_hours: 6,
    model_status: [
      { name: "Random Forest", algorithm: "sklearn.ensemble.RandomForestRegressor", status: "available" },
      { name: "XGBoost", algorithm: "xgboost.XGBRegressor", status: "available" }
    ],
    parameters: {
      temperature_c: {
        status: "available",
        current: { value: 24, timestamp: "2026-10-24T02:00:00Z" },
        historical_points: [{ timestamp: "2026-10-24T02:00:00Z", value: 24 }],
        models: {
          "Random Forest": { status: "available", predicted: 25, forecast_points: [{ timestamp: target, value: 25 }] },
          "XGBoost": { status: "available", predicted: 26, forecast_points: [{ timestamp: target, value: 26 }] }
        }
      }
    }
  }) })).forecast(6);
  const temperature = result.parameters.temperature_c;
  assert.equal(temperature.status, "available");
  assert.equal(temperature.predicted, null);
  assert.equal(temperature.models["Random Forest"].predicted, 25);
  assert.equal(temperature.models.XGBoost.predicted, 26);
  assert.deepEqual(plain(temperature.forecast_points), [
    { timestamp: target, value: 25, model: "Random Forest" },
    { timestamp: target, value: 26, model: "XGBoost" }
  ]);
  assert.equal(result.model_status[0].status, "available");
  assert.equal(result.model_status[1].algorithm, "xgboost.XGBRegressor");
});

test("forecast API validates horizons before issuing a request", async () => {
  let requested = false;
  const api = loadApi(async () => { requested = true; return { ok: true, json: async () => ({}) }; });
  await assert.rejects(api.forecast(0), /between 1 and 72 hours/);
  assert.equal(requested, false);
});

test("forecast API does not accept a legacy scalar prediction without named RF and XGBoost outputs", async () => {
  const api = loadApi(async () => ({ ok: true, json: async () => ({
    generated_at: "2026-10-24T02:00:00Z",
    target_at: "2026-10-24T08:00:00Z",
    horizon_hours: 6,
    parameters: {
      temperature_c: {
        status: "available", predicted: 28,
        historical_points: [{ timestamp: "2026-10-24T02:00:00Z", value: 25 }],
        forecast_points: [{ timestamp: "2026-10-24T08:00:00Z", value: 28 }]
      }
    }
  }) }));
  const result = await api.forecast(6);
  assert.equal(result.parameters.temperature_c.status, "unavailable");
  assert.equal(result.parameters.temperature_c.predicted, null);
  assert.equal(result.parameters.temperature_c.models["Random Forest"].status, "unavailable");
  assert.equal(result.parameters.temperature_c.models.XGBoost.status, "unavailable");
  assert.deepEqual(plain(result.parameters.temperature_c.forecast_points), []);
});

test("TDS chart labels and tooltip units use ppm with one canonical line", () => {
  const source = fs.readFileSync(path.join(__dirname, "../js/charts.js"), "utf8");
  let chartConfig;
  const canvas = { style: {}, parentElement: { querySelector: () => null, append() {} } };
  class FakeChart { constructor(_canvas, config) { chartConfig = config; } destroy() {} }
  const context = { window: { Chart: FakeChart }, Chart: FakeChart, document: { getElementById: () => canvas } };
  vm.runInNewContext(source, context, { filename: "charts.js" });
  context.window.LUWCharts.draw("tds", "tdsChart", [{ timestamp: demoRow.recorded_at, value: 127.17 }], "tds", "ppm");
  assert.equal(chartConfig.data.datasets.length, 1);
  assert.equal(chartConfig.data.datasets[0].label, "TDS (ppm)");
  const tooltip = chartConfig.options.plugins.tooltip.callbacks.label({ dataset: { label: "TDS (ppm)" }, parsed: { y: 127.17 } });
  assert.equal(tooltip, "TDS (ppm): 127.17");
});

test("forecast charts keep measured history separate from each model prediction", () => {
  const source = fs.readFileSync(path.join(__dirname, "../js/charts.js"), "utf8");
  let chartConfig;
  const canvas = { style: {}, parentElement: { querySelector: () => null, append() {} } };
  class FakeChart { constructor(_canvas, config) { chartConfig = config; } destroy() {} }
  const context = { window: { Chart: FakeChart }, Chart: FakeChart, document: { getElementById: () => canvas } };
  vm.runInNewContext(source, context, { filename: "charts.js" });
  context.window.LUWCharts.draw(
    "forecast-turbidity", "turbidityForecastChart",
    [{ timestamp: "2026-10-24T02:00:00Z", value: 1.2 }], "turbidity", "NTU",
    "No historical points", [{
      label: "Random Forest (6 h)", color: "#43d3a7", dashed: true,
      points: [{ timestamp: "2026-10-24T08:00:00Z", value: 1.3 }]
    }, {
      label: "XGBoost (6 h)", color: "#c48cff", dashed: true,
      points: [{ timestamp: "2026-10-24T08:00:00Z", value: 1.4 }]
    }], "Historical hourly means"
  );
  assert.equal(chartConfig.data.datasets.length, 3);
  assert.equal(chartConfig.data.datasets[0].label, "Historical hourly means");
  assert.equal(chartConfig.data.datasets[1].label, "Random Forest (6 h)");
  assert.equal(chartConfig.data.datasets[2].label, "XGBoost (6 h)");
  assert.deepEqual(Array.from(chartConfig.data.datasets[1].borderDash), [6, 4]);
});
