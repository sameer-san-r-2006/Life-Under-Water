const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

class FakeElement {
  constructor(dataset = {}) {
    this.dataset = dataset;
    this.innerHTML = "";
    this.textContent = "";
    this.className = "";
    this.handlers = {};
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  addEventListener(name, callback) { this.handlers[name] = callback; }
}

function loadUi(overview, latest, charts = null, status = {}, apiOptions = {}) {
  const elements = new Map();
  const getElement = selector => {
    if (!elements.has(selector)) elements.set(selector, new FakeElement());
    return elements.get(selector);
  };
  const nav = ["overview", "analytics", "devices", "predictions"].map(page => new FakeElement({ page }));
  const document = {
    visibilityState: "visible",
    querySelector: getElement,
    querySelectorAll: selector => selector === ".nav-link" ? nav : [],
    addEventListener() {}
  };
  const currentOverview = typeof overview === "function" ? overview : () => overview;
  const currentLatest = typeof latest === "function" ? latest : async () => latest;
  const window = {
    innerWidth: 1280,
    LUWApi: {
      health: async () => ({ api: "ok", database: "connected" }),
      status: async () => status,
      overview: async () => {
        const payload = currentOverview();
        return payload && typeof payload === "object" ? { ...payload, latest: payload.latest || (typeof latest === "function" ? null : latest) } : payload;
      },
      latest: currentLatest,
      devices: async () => apiOptions.devices || { devices: [] },
      history: async () => {
        if (apiOptions.historyError) throw new Error("history unavailable");
        if (typeof apiOptions.history === "function") return apiOptions.history();
        return apiOptions.history || { temperature: [], tds: [], turbidity: [], cloudiness_index: [] };
      },
      forecast: async horizonHours => {
        if (apiOptions.forecastError) throw new Error("forecast unavailable");
        if (typeof apiOptions.forecastProvider === "function") return apiOptions.forecastProvider(horizonHours);
        if (apiOptions.forecast) return apiOptions.forecast;
        const generatedAt = "2026-10-24T02:00:00Z";
        const targetAt = new Date(Date.parse(generatedAt) + horizonHours * 60 * 60 * 1000).toISOString();
        const unavailable = (metric, label, unit, reason) => ({
          metric, label, unit, status: "unavailable", reason,
          current: null, sample_count: 0, historical_points: [], models: {
            "Random Forest": { status: "insufficient_training_data", reason, predicted: null, forecast_points: [] },
            "XGBoost": { status: "insufficient_training_data", reason, predicted: null, forecast_points: [] }
          }
        });
        return {
          generated_at: generatedAt, target_at: targetAt, horizon_hours: horizonHours,
          model_status: [
            { name: "Random Forest", algorithm: "sklearn.ensemble.RandomForestRegressor", status: "unavailable" },
            { name: "XGBoost", algorithm: "xgboost.XGBRegressor", status: "unavailable" }
          ],
          parameters: {
            temperature_c: unavailable("temperature_c", "Temperature", "°C", "Not enough valid hourly readings."),
            tds_ppm: unavailable("tds_ppm", "TDS", "ppm", "Not enough calibrated hourly readings."),
            turbidity_ntu: unavailable("turbidity_ntu", "Turbidity", "NTU", "No calibrated NTU readings are recorded.")
          },
        };
      },
      summary: async () => {
        if (apiOptions.summaryError) throw new Error("summary unavailable");
        if (typeof apiOptions.summary === "function") return apiOptions.summary();
        return apiOptions.summary || {
          temperature: { valid_samples: 0 },
          tds: { valid_samples: 0, source: "unavailable" },
          turbidity: { valid_samples: 0 },
          cloudiness_index: { valid_samples: 0 }
        };
      },
      pull: async () => {
        if (apiOptions.pullError) throw new Error("pull failed");
        return typeof apiOptions.pull === "function" ? apiOptions.pull() : { message: "Reading pull accepted." };
      }
    }
  };
  window.LUWCharts = charts;
  const source = fs.readFileSync(path.join(__dirname, "../js/app.js"), "utf8");
  vm.runInNewContext(source, {
    window,
    LUWApi: window.LUWApi,
    document,
    Date,
    Number,
    String,
    Array,
    Map,
    Math,
    setTimeout,
    clearTimeout,
    setInterval() {}
  }, { filename: "app.js" });
  return { content: getElement("#pageContent"), nav, elements, window };
}

const latest = {
  timestamp: "2026-10-24T02:00:00Z",
  temperature: { value: 25.5, celsius: 25.5, timestamp: "2026-10-24T02:00:00Z", valid: true, status: "ok" },
  tds: { value: 127.17, ppm: 127.17, raw_voltage: 0.378, timestamp: "2026-10-24T02:00:00Z", valid: true, status: "ok", calibration_status: "estimated", source: "demo_estimate", conversion_method: "legacy_demo_lookup_v1" },
  turbidity: { value: null, ntu: null, valid: false, status: "unavailable" },
  cloudiness: { value: 45, index: 45, raw_voltage: 0.23, timestamp: "2026-10-24T02:00:00Z", valid: true, status: "ok" }
};

const status = {
  system_status: "online",
  device_status: "connected",
  serial_reader_status: "connected",
  serial_connection_status: "connected",
  sensor_health: {
    temperature: { status: "valid", latest_at: "2026-10-24T02:00:00Z" },
    tds: { status: "valid", latest_at: "2026-10-24T02:00:00Z" },
    cloudiness: { status: "valid", latest_at: "2026-10-24T02:00:00Z" }
  }
};

const overview = {
  timestamp: "2026-10-24T02:05:00Z",
  average_window: "Last 24 hours",
  time_window: { hours: 24 },
  averages: {
    temperature: { average: 25.25, minimum: 24, maximum: 27, valid_samples: 8, unit: "°C" },
    tds: { average: 127.17, minimum: 100, maximum: 200, valid_samples: 5, source: "demo_estimate", data_quality: "estimated", unit: "ppm" },
    cloudiness_index: { average: 45, minimum: 30, maximum: 60, valid_samples: 4, unit: "index / 100", source: "firmware_relative_index" }
  },
  system_status: "online",
  serial_connection_status: "connected",
  sensor_health: status.sensor_health,
  purpose_assessments: [
    {
      key: "agriculture", label: "Agriculture", status: "met",
      reason: "A fresh, valid sensor reading is available; Agriculture has no additional thresholds.",
      conditions: ["Any fresh, valid TDS, turbidity/cloudiness, or temperature reading"],
      readings: [
        { label: "Temperature", value: 25.5, unit: "°C", status: "valid" },
        { label: "TDS", value: 127.17, unit: "ppm", status: "valid" },
        { label: "Relative cloudiness index (not NTU)", value: 45, unit: "index / 100", status: "valid" }
      ]
    },
    {
      key: "household", label: "Household", status: "met",
      reason: "TDS is below 200 ppm and turbidity/cloudiness is below 100.",
      conditions: ["TDS < 200 ppm", "Relative cloudiness index < 100", "Temperature: any value"],
      readings: [
        { label: "TDS", value: 127.17, unit: "ppm", status: "valid" },
        { label: "Relative cloudiness index (not NTU)", value: 45, unit: "index / 100", status: "valid" },
        { label: "Temperature", value: 25.5, unit: "°C", status: "valid" }
      ]
    },
    {
      key: "aquatic", label: "Aquatic", status: "not_met",
      reason: "One or more required thresholds are not met.",
      conditions: ["TDS > 500 ppm", "Relative cloudiness index < 100", "Temperature: any value"],
      readings: [
        { label: "TDS", value: 127.17, unit: "ppm", status: "valid" },
        { label: "Relative cloudiness index (not NTU)", value: 45, unit: "index / 100", status: "valid" },
        { label: "Temperature", value: 25.5, unit: "°C", status: "valid" }
      ]
    }
  ],
  live_reading: {
    timestamp: "2026-10-24T02:00:00Z",
    temperature: latest.temperature,
    tds: { value: null, ppm: null, raw_voltage: 0.378, timestamp: "2026-10-24T02:00:00Z", valid: false, status: "waiting_for_data" },
    cloudiness: latest.cloudiness
  },
  latest,
  latest_valid_tds: latest.tds,
  activity: [{ event_type: "serial_connected", message: "Serial reader connected to COM5.", timestamp: "2026-10-24T02:00:00Z" }]
};

function purposeCardHtml(ui) {
  const match = ui.content.innerHTML.match(/<article class="panel purpose-assessment-card">([\s\S]*?)<\/article>/);
  assert.ok(match, "combined Water Quality Purpose Assessment card should render");
  return match[1];
}

async function settle() { await new Promise(resolve => setTimeout(resolve, 0)); }

test("Overview renders live purpose checks in order and keeps the three average cards", async () => {
  const ui = loadUi(overview, latest, null, status);
  await settle();
  const html = ui.content.innerHTML;
  assert.equal([...html.matchAll(/class="overview-section"/g)].length, 3);
  const cards = [...html.matchAll(/<article class="metric-card average-card">([\s\S]*?)<\/article>/g)].map(match => match[1]);
  assert.equal(cards.length, 3);
  assert.match(cards[0], /25\.25<span class="unit"> °C<\/span>/);
  assert.match(cards[1], /127\.17<span class="unit"> ppm<\/span>/);
  assert.match(cards[1], /Valid samples: 5/);
  assert.match(cards[2], /Average turbidity/);
  assert.match(cards[2], /NTU/);
  assert.match(cards[2], /No valid readings yet/);
  assert.match(html, /Last successful data refresh:/);
  const purposeCard = purposeCardHtml(ui);
  assert.match(purposeCard, /Water Quality Purpose Assessment/);
  assert.match(html, /Overall purpose status/);
  assert.match(html, /Live sensor readings checked against thresholds/);
  assert.match(purposeCard, /Uses the latest valid, fresh sensor values\./);
  assert.deepEqual([...purposeCard.matchAll(/class="purpose-assessment-row" data-purpose="([^"]+)"/g)].map(match => match[1]), ["agriculture", "household", "aquatic"]);
  assert.match(purposeCard, /<h3>Agriculture<\/h3>/);
  assert.match(purposeCard, /<h3>Household<\/h3>/);
  assert.match(purposeCard, /<h3>Aquatic<\/h3>/);
  assert.match(purposeCard, /TDS &lt; 200 ppm/);
  assert.match(purposeCard, /TDS &gt; 500 ppm/);
  assert.match(purposeCard, /index \/ 100/);
  assert.match(purposeCard, /not NTU/);
  assert.match(purposeCard, /Conditions met/);
  assert.match(purposeCard, /Conditions not met/);
  assert.match(purposeCard, /These threshold-based assessments do not certify water safety\./);
  assert.doesNotMatch(purposeCard, /Agricultural use|Aquatic use|Household non-drinking use|configured range|reviewed reference/i);
  assert.equal([...html.matchAll(/class="panel purpose-assessment-card"/g)].length, 1);
  assert.match(html, /System status/);
  assert.match(html, /NodeMCU \/ USB serial/);
  assert.match(html, /TDS sensor/);
  assert.match(html, /Recent activity/);
  assert.doesNotMatch(html, /DEMO \/ ESTIMATED|Calibration pending|Legacy demo lookup|excluded from calibrated averages|calibration required for ppm|Latest sensor measurements|Raw TDS voltage/);
});

test("Overview shows missing average values as unavailable and preserves all three sections", async () => {
  const empty = {
    ...overview,
    averages: {
      temperature: { average: null, valid_samples: 0 },
      tds: { average: null, valid_samples: 0, source: "unavailable" },
      cloudiness_index: { average: null, valid_samples: 0, source: "unavailable" }
    }
  };
  const ui = loadUi(empty, latest, null, { ...status, system_status: "degraded" });
  await settle();
  assert.equal([...ui.content.innerHTML.matchAll(/class="overview-section"/g)].length, 3);
  assert.equal([...ui.content.innerHTML.matchAll(/class="metric-card average-card"/g)].length, 3);
  assert.equal((ui.content.innerHTML.match(/No valid readings yet/g) || []).length, 3);
  assert.doesNotMatch(purposeCardHtml(ui), /Cannot assess/);
});

test("Overview purpose section no longer requires configured reference ranges", async () => {
  const ui = loadUi(overview, latest, null, status);
  await settle();
  const card = purposeCardHtml(ui);
  assert.match(card, /TDS &lt; 200 ppm/);
  assert.doesNotMatch(card, /ASSESSMENT_RULES_JSON|configured range|reviewed reference/i);
  assert.match(card, /Cannot assess|Conditions met|Conditions not met/);
});

test("Overview Pull Latest Reading blocks duplicates and refreshes overview values and chart data", async () => {
  let average = 25.25;
  let pullCount = 0;
  let historyCount = 0;
  let finishPull;
  const overviewProvider = () => ({
    ...overview,
    timestamp: `2026-10-24T02:0${average === 25.25 ? "5" : "9"}:00Z`,
    averages: {
      ...overview.averages,
      temperature: { ...overview.averages.temperature, average }
    }
  });
  const ui = loadUi(overviewProvider, latest, null, status, {
    pull: () => {
      pullCount += 1;
      return new Promise(resolve => { finishPull = resolve; });
    },
    history: async () => {
      historyCount += 1;
      return { temperature: [], tds: [], turbidity: [], cloudiness_index: [] };
    }
  });
  await settle();
  assert.match(ui.content.innerHTML, /25\.25<span class="unit"> °C<\/span>/);
  const button = ui.elements.get("#pullReading");
  const pending = button.handlers.click();
  await settle();
  assert.match(ui.content.innerHTML, /id="pullReading" disabled>Requesting/);
  await button.handlers.click();
  assert.equal(pullCount, 1);
  average = 26.5;
  finishPull({ message: "Fresh reading received." });
  await pending;
  assert.match(ui.content.innerHTML, /26\.5<span class="unit"> °C<\/span>/);
  assert.doesNotMatch(ui.content.innerHTML, /id="pullReading" disabled/);
  assert.equal(historyCount, 1);
  assert.equal(ui.elements.get("#toast").textContent, "Fresh reading received.");
});

test("Overview Pull Latest Reading shows an error and returns the button to idle", async () => {
  const ui = loadUi(overview, latest, null, status, { pullError: true });
  await settle();
  await ui.elements.get("#pullReading").handlers.click();
  assert.equal(ui.elements.get("#toast").textContent, "Could not request a reading. Check the backend and USB serial connection.");
  assert.doesNotMatch(ui.content.innerHTML, /id="pullReading" disabled/);
});

test("Analytics Pull Latest Reading still uses the shared pull action and refreshes analytics", async () => {
  let pullCount = 0;
  let historyCount = 0;
  const ui = loadUi(overview, latest, null, status, {
    pull: async () => {
      pullCount += 1;
      return { message: "Analytics reading received." };
    },
    history: async () => {
      historyCount += 1;
      return { temperature: [], tds: [], turbidity: [], cloudiness_index: [] };
    }
  });
  await settle();
  ui.nav.find(button => button.dataset.page === "analytics").handlers.click();
  await settle();
  const countBeforePull = historyCount;
  await ui.elements.get("#pullReading").handlers.click();
  assert.equal(pullCount, 1);
  assert.ok(historyCount > countBeforePull);
  assert.equal(ui.elements.get("#toast").textContent, "Analytics reading received.");
});

test("Analytics displays the latest canonical ppm and same-window statistics and history", async () => {
  const chartCalls = [];
  const ui = loadUi(overview, latest, { draw(...args) { chartCalls.push(args); } }, status, {
    history: {
      temperature: [{ timestamp: "2026-10-24T01:00:00Z", value: 25 }],
      tds: [{ timestamp: "2026-10-24T01:00:00Z", value: 120 }, { timestamp: "2026-10-24T02:00:00Z", value: 127.17 }],
      turbidity: [],
      cloudiness_index: [{ timestamp: "2026-10-24T02:00:00Z", value: 45 }]
    },
    summary: {
      temperature: { average: 25, minimum: 24, maximum: 26, latest: 25.5, valid_samples: 2, source: "measured" },
      tds: { average: 123.59, minimum: 120, maximum: 127.17, latest: 127.17, valid_samples: 2, source: "demo_estimate", data_quality: "estimated" },
      cloudiness_index: { average: 45, minimum: 45, maximum: 45, latest: 45, valid_samples: 1, source: "firmware_relative_index" }
    }
  });
  await settle();
  ui.nav.find(button => button.dataset.page === "analytics").handlers.click();
  await settle();
  const html = ui.content.innerHTML;
  assert.match(html, /127\.17<span class="unit"> ppm<\/span>/);
  assert.match(html, /TDS Concentration Over Time/);
  assert.match(html, /Legacy Relative Sensor Index History/);
  assert.match(html, /Turbidity Over Time/);
  assert.match(html, /Temperature Over Time/);
  assert.match(html, /127\.17 ppm/);
  assert.match(html, /Samples/);
  assert.match(html, /Legacy relative sensor index \(not NTU\)/);
  assert.doesNotMatch(html, /Cloudiness Index Over Time|TDS demo estimate|DEMO \/ ESTIMATED|Legacy demo lookup/);
  const tdsChart = chartCalls.filter(call => call[0] === "tds").at(-1);
  assert.deepEqual(tdsChart[2], [
    { timestamp: "2026-10-24T01:00:00Z", value: 120 },
    { timestamp: "2026-10-24T02:00:00Z", value: 127.17 }
  ]);
  assert.equal(tdsChart.length, 6);
});

test("Analytics refresh updates the latest TDS card from the latest-reading endpoint", async () => {
  let value = 150;
  const latestProvider = async () => ({
    ...latest,
    tds: { ...latest.tds, value, ppm: value, timestamp: `2026-10-24T02:0${value === 150 ? "1" : "2"}:00Z` }
  });
  const ui = loadUi(overview, latestProvider, null, status);
  await settle();
  ui.nav.find(button => button.dataset.page === "analytics").handlers.click();
  await settle();
  assert.match(ui.content.innerHTML, /150<span class="unit"> ppm<\/span>/);
  value = 175;
  await ui.elements.get("#refreshAnalytics").handlers.click();
  assert.match(ui.content.innerHTML, /175<span class="unit"> ppm<\/span>/);
});

test("Analytics TDS card has unavailable, loading and error states", async () => {
  const unavailable = {
    ...latest,
    tds: { value: null, ppm: null, valid: false, status: "waiting_for_data", timestamp: null }
  };
  const ui = loadUi({ ...overview, live_reading: null, latest: unavailable }, unavailable, null, status);
  await settle();
  ui.nav.find(button => button.dataset.page === "analytics").handlers.click();
  await settle();
  assert.match(ui.content.innerHTML, /Unavailable/);
  assert.doesNotMatch(ui.content.innerHTML, /127\.17 ppm/);

  const broken = loadUi(overview, latest, null, status, { historyError: true });
  await settle();
  broken.nav.find(button => button.dataset.page === "analytics").handlers.click();
  await settle();
  assert.match(broken.content.innerHTML, /Error/);
});

test("Devices displays calibrated sensor names and canonical TDS without relabeling the legacy index", async () => {
  const ui = loadUi(overview, latest, null, status);
  await settle();
  ui.nav.find(button => button.dataset.page === "devices").handlers.click();
  await settle();
  assert.match(ui.content.innerHTML, /127\.17 ppm/);
  assert.match(ui.content.innerHTML, /Turbidity Sensor/);
  assert.match(ui.content.innerHTML, /Turbidity in NTU; calibration required/);
  assert.match(ui.content.innerHTML, /NTU/);
  assert.doesNotMatch(ui.content.innerHTML, /Cloudiness Index|DEMO \/ ESTIMATED|Legacy demo lookup/);
});

test("Predictions keeps sensor regressions while omitting the experimental purpose classifier UI", async () => {
  const ui = loadUi(overview, latest);
  await settle();
  ui.nav.find(button => button.dataset.page === "predictions").handlers.click();
  await settle();
  await settle();
  const html = ui.content.innerHTML;
  assert.equal(ui.elements.get("#pageTitle").textContent, "Predictions");
  assert.match(html, /Future Sensor Predictions/);
  assert.match(html, /Future target/);
  assert.match(html, /Predicted Temperature/);
  assert.match(html, /Predicted TDS/);
  assert.match(html, /Predicted Turbidity/);
  assert.match(html, /Random Forest/);
  assert.match(html, /XGBoost/);
  assert.match(html, /No calibrated NTU readings are recorded/);
  assert.doesNotMatch(html, /100\.0%|75\.0%|Predicted category<\/td>/);
  assert.doesNotMatch(html, /Predicted Water Quality and Use Assessment|sensor forecast used|Aquatic use|Agricultural use|Household use|Experimental sample category classifier|No trained classifier is available|Experimental use categories are unavailable|No trained verified-label classifier is registered/i);
  assert.doesNotMatch(html, /classifier|purpose assessment|predicted category|verified-label/i);
});
test("Prediction selector requests every horizon and draws Random Forest and XGBoost separately", async () => {
  const requests = [];
  const chartCalls = [];
  const forecastProvider = async horizon => {
    requests.push(horizon);
    const generated = "2026-10-24T02:00:00Z";
    const target = new Date(Date.parse(generated) + horizon * 3600000).toISOString();
    const parameter = (metric, label, unit, current, predicted) => {
      const result = value => ({
        status: "available", predicted: value,
        direction: value >= current ? "increasing" : "decreasing", change: value - current,
        training_samples: 8, forecast_points: [{ timestamp: target, value }]
      });
      return {
        metric, label, unit, status: "available", current: { value: current, timestamp: generated },
        sample_count: 12,
        historical_points: [{ timestamp: generated, value: current }],
        models: { "Random Forest": result(predicted), "XGBoost": result(predicted + 0.1) }
      };
    };
    return {
      generated_at: generated, target_at: target, horizon_hours: horizon,
      model_status: [
        { name: "Random Forest", algorithm: "sklearn.ensemble.RandomForestRegressor", status: "available" },
        { name: "XGBoost", algorithm: "xgboost.XGBRegressor", status: "available" }
      ],
      parameters: {
        temperature_c: parameter("temperature_c", "Temperature", "°C", 24, 25),
        tds_ppm: parameter("tds_ppm", "TDS", "ppm", 100, 110),
        turbidity_ntu: parameter("turbidity_ntu", "Turbidity", "NTU", 1.2, 1.3)
      },
    };
  };
  const ui = loadUi(overview, latest, { draw(...args) { chartCalls.push(args); } }, status, { forecastProvider });
  await settle();
  ui.nav.find(button => button.dataset.page === "predictions").handlers.click();
  await settle();
  await settle();
  assert.deepEqual(requests, [1]);
  assert.match(ui.content.innerHTML, /Predictions in 1 hour/);
  assert.match(ui.content.innerHTML, /25 °C/);
  assert.match(ui.content.innerHTML, /25\.1 °C/);
  assert.match(ui.content.innerHTML, /110 ppm/);
  assert.match(ui.content.innerHTML, /110\.1 ppm/);
  assert.match(ui.content.innerHTML, /1\.3 NTU/);
  const select = ui.elements.get("#forecastHorizon");
  for (const value of ["6", "12", "24", "72"]) {
    select.handlers.change({ target: { value } });
    await settle();
    await settle();
  }
  assert.deepEqual(requests, [1, 6, 12, 24, 72]);
  assert.match(ui.content.innerHTML, /72 hour horizon/);
  const turbidityChart = chartCalls.filter(call => call[0] === "forecast-turbidity_ntu").at(-1);
  assert.equal(turbidityChart[2][0].value, 1.2);
  assert.equal(turbidityChart[6][0].label, "Random Forest (72 h)");
  assert.deepEqual(turbidityChart[6][0].points[0], { timestamp: "2026-10-27T02:00:00.000Z", value: 1.3 });
  assert.equal(turbidityChart[6][0].dashed, true);
  assert.equal(turbidityChart[6][1].label, "XGBoost (72 h)");
});
