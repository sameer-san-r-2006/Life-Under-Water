(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const content = $("#pageContent");
  const state = { page: "overview", range: "24h", status: null, overview: null, latest: null, devices: null, history: null, summary: null, forecast: null, forecastHorizonHours: 1, forecastLoading: false, forecastError: null, pullLoading: false, connected: false, loading: true, analyticsLoading: false, analyticsError: false };
  const pageNames = { overview: "Overview", analytics: "Analytics", devices: "Devices", predictions: "Predictions" };
  let toastTimer;

  function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }
  function hasReading(reading) {
    if (!isRecord(reading)) return false;
    return Boolean(reading.timestamp) || reading.temperature?.value != null || reading.tds?.value != null ||
      reading.tds?.raw_voltage != null || reading.turbidity?.value != null || reading.turbidity?.raw_voltage != null || reading.legacy_cloudiness_index?.value != null || reading.cloudiness?.value != null;
  }
  function mergeMeasurement(current, previous) {
    const live = isRecord(current) ? current : {};
    const saved = isRecord(previous) ? previous : {};
    const liveValue = live.value ?? live.celsius ?? live.ppm ?? live.ntu;
    const savedValue = saved.value ?? saved.celsius ?? saved.ppm ?? saved.ntu;
    const useLive = liveValue !== null && liveValue !== undefined;
    const source = useLive ? live : saved;
    return {
      ...saved,
      ...live,
      value: useLive ? liveValue : savedValue,
      timestamp: source.timestamp || saved.timestamp || live.timestamp,
      status: source.status || live.status || saved.status,
      valid: source.valid ?? live.valid ?? saved.valid,
      calibration_status: source.calibration_status || live.calibration_status || saved.calibration_status,
      raw_voltage: source.raw_voltage ?? null,
      raw_adc: source.raw_adc ?? null
    };
  }
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
  }
  function fmtTime(value) {
    if (!value) return "Not available";
    try {
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? "Not available" : date.toLocaleString();
    } catch (_) {
      return "Not available";
    }
  }
  function liveValueText(value, unit = "", digits = 2) {
    if (value === null || value === undefined || value === "" || !Number.isFinite(Number(value))) return "Unavailable";
    const formatted = Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
    return `${formatted}${unit ? ` ${unit}` : ""}`;
  }
  function statusTag(status) {
    const s = String(status || "unavailable").toLowerCase();
    const cls = ["online","ok","valid","connected","available","calibrated","matches_configured_ranges","within_reference_range","matched"].includes(s) ? "ok" :
      ["degraded","warning","stale","pending","insufficient_data","insufficient_history","stale_current","calibration_required","criteria_unavailable","insufficient_training_data","model_unavailable"].includes(s) ? "warn" :
      ["offline","error","invalid","disconnected","critical","outside_reference_range","outside_configured_ranges","invalid_projection"].includes(s) ? "bad" : "neutral";
    const label = ({ online: "Online", degraded: "Degraded", offline: "Offline", connected: "Connected", valid: "Valid", stale: "Stale", error: "Error", calibration_pending: "Unavailable", estimated: "Available", available: "Available", unavailable: "Unavailable", waiting_for_data: "Unavailable", loading: "Loading", insufficient_data: "Insufficient data", insufficient_history: "Not enough history", stale_current: "Current reading is stale", calibration_required: "Calibration required", criteria_unavailable: "Criteria unavailable", matches_configured_ranges: "Matches configured ranges", outside_configured_ranges: "Outside configured ranges", within_reference_range: "Within configured range", outside_reference_range: "Outside configured range", invalid_projection: "Projection unavailable", insufficient_training_data: "Not enough training data", model_unavailable: "Model unavailable", no_valid_data: "No valid data", invalid_prediction: "Invalid model output", matched: "Model matched", not_selected: "Not selected" })[s] || s.replaceAll("_"," ");
    return `<span class="tag ${cls}">${escapeHtml(label)}</span>`;
  }
  function valueText(value, unit, calibrationState) {
    if (value === null || value === undefined || value === "" || !Number.isFinite(Number(value))) {
      return `<span class="muted">—</span><span class="unit"> ${escapeHtml(unit)}</span>`;
    }
    if (calibrationState && !["calibrated","valid","ok","estimated"].includes(String(calibrationState).toLowerCase())) {
      return `<span class="muted">—</span><span class="unit"> ${escapeHtml(unit)}</span>`;
    }
    return `${escapeHtml(Number(value).toLocaleString(undefined,{maximumFractionDigits:2}))}<span class="unit"> ${escapeHtml(unit)}</span>`;
  }
  function metricCard(title, icon, value, unit, updatedAt, sensorState, calibrationState, detail = "", loading = false) {
    const showValue = loading
      ? `<span class="loading-value"><span class="spinner" aria-hidden="true"></span>Loading</span>`
      : valueText(value, unit, calibrationState);
    const stateLabel = loading ? "loading"
      : ["disconnected", "stale", "error"].includes(String(sensorState || "").toLowerCase()) ? sensorState
      : String(calibrationState || "").toLowerCase() === "invalid" ? "invalid"
      : calibrationState && !["calibrated","valid","ok","estimated"].includes(String(calibrationState).toLowerCase())
        ? "calibration_pending" : (sensorState || "waiting_for_data");
    return `<article class="metric-card">
      <div class="metric-top"><span class="metric-label">${title}</span><span class="metric-icon">${icon}</span></div>
      <div class="metric-value">${showValue}</div>
      ${detail ? `<small class="muted" style="display:block;margin:0 0 9px;line-height:1.45">${escapeHtml(detail)}</small>` : ""}
      <div class="metric-meta"><span>Updated ${escapeHtml(fmtTime(updatedAt))}</span>${statusTag(stateLabel)}</div>
    </article>`;
  }
  function emptyState(title, message) {
    return `<div class="empty-state"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p></div>`;
  }
  function activityMarkup(events) {
    if (!Array.isArray(events)) return emptyState("Activity unavailable", "The backend did not return system activity.");
    if (!events.length) return emptyState("No activity recorded", "No system events have been recorded yet.");
    const labels = {
      backend_started: ["Backend started", "ok"],
      backend_stopped: ["Backend stopped", "neutral"],
      serial_connected: ["Device connected", "ok"],
      serial_disconnected: ["Device disconnected", "warn"],
      serial_error: ["Serial error", "bad"],
      database_error: ["Database error", "bad"],
      reading_saved: ["Reading saved", "ok"],
      purpose_alert: ["Purpose conditions met", "ok"]
    };
    const items = events.filter(isRecord).slice(0, 8);
    if (!items.length) return emptyState("Activity unavailable", "System event records could not be displayed.");
    return `<div class="activity-list">${items.map(e => {
      const [label, state] = labels[e.event_type] || ["System event", "neutral"];
      const timestamp = e.timestamp || e.recorded_at;
      return `<div class="activity-item"><div class="activity-message"><span class="activity-bullet ${state}"></span><strong>${escapeHtml(e.message || e.event || label)}</strong></div><small class="activity-meta"><span class="tag ${state}">${escapeHtml(label)}</span><span class="activity-separator" aria-hidden="true">·</span><span>${escapeHtml(fmtTime(timestamp))}</span></small></div>`;
    }).join("")}</div>`;
  }
  function sensorRows(status, latest) {
    const data = isRecord(status) ? status : {};
    const health = isRecord(data.sensor_health) ? data.sensor_health : {};
    const entries = [
      ["NodeMCU / USB serial", data.serial_connection_status || data.device_status || "unavailable", "Serial data connection"],
      ["Temperature sensor", health.temperature?.status || latest?.temperature?.status || "unavailable", "DS18B20 probe"],
      ["TDS sensor", health.tds?.status || latest?.tds?.status || "unavailable", "TDS concentration in ppm"],
      ["Turbidity sensor", health.turbidity?.status || latest?.turbidity?.status || "unavailable", "Calibrated turbidity in NTU"]
    ];
    return `<div class="status-list">${entries.map(([name,state,detail]) => `<div class="status-row"><div class="status-name"><span class="status-dot ${["online","ok","connected","valid"].includes(String(state).toLowerCase())?"ok":(["offline","error","disconnected"].includes(String(state).toLowerCase())?"bad":"warn")}"></span><div>${escapeHtml(name)}<small>${escapeHtml(detail)}</small></div></div>${statusTag(state)}</div>`).join("")}</div>`;
  }
  function purposeAssessmentMarkup(results) {
    const entries = [
      ["agriculture", "Agriculture"],
      ["household", "Household"],
      ["aquatic", "Aquatic"]
    ];
    return entries.map(([key, label]) => {
      const item = Array.isArray(results) ? results.find(result => result?.key === key) : null;
      const status = item?.status === "met" ? ["met", "Conditions met"]
        : item?.status === "not_met" ? ["not-met", "Conditions not met"]
        : ["cannot-assess", "Cannot assess"];
      const readings = Array.isArray(item?.readings) ? item.readings : [];
      const readingMarkup = readings.map(reading => {
        const state = reading?.status;
        const value = state === "stale" ? "Stale"
          : state !== "valid" ? "Unavailable"
          : liveValueText(reading.value, reading.unit);
        return `<span><strong>${escapeHtml(reading?.label || "Sensor")}</strong> ${escapeHtml(value)}</span>`;
      }).join("");
      const conditions = Array.isArray(item?.conditions) ? item.conditions : [];
      return `<div class="purpose-assessment-row" data-purpose="${key}">
        <div class="purpose-assessment-copy"><h3>${label}</h3>
          <div class="purpose-reading-list">${readingMarkup || '<span class="muted">Sensor readings unavailable</span>'}</div>
          <small class="purpose-conditions"><strong>Conditions:</strong> ${escapeHtml(conditions.join(" · ") || "Fresh, valid sensor readings are required")}</small>
          <p>${escapeHtml(item?.reason || "Cannot assess because required valid, fresh sensor readings are unavailable.")}</p>
        </div>
        <span class="purpose-status-badge ${status[0]}">${status[1]}</span>
      </div>`;
    }).join("");
  }
  function renderOverview() {
    const overview = isRecord(state.overview) ? state.overview : {};
    const averages = isRecord(overview.averages) ? overview.averages : {};
    const timeWindow = isRecord(overview.time_window) ? overview.time_window : averages.time_window;
    const windowLabel = overview.average_window || (timeWindow?.hours === 24 ? "Last 24 hours" : "Selected time window");
    const purposes = Array.isArray(overview.purpose_assessments) ? overview.purpose_assessments : [];
    content.innerHTML = `
      <div class="page-intro"><div><h2>Water Quality Overview</h2><p>Live readings, threshold based purpose results and system health.</p></div><div class="controls"><button class="btn primary" id="pullReading" ${state.pullLoading ? "disabled" : ""}>${state.pullLoading ? "Requesting…" : "Pull Latest Reading"}</button></div></div>
      <section class="overview-section" aria-label="Average sensor values">
        <div class="section-heading overview-section-heading"><div><h2>Average sensor values</h2><p>${escapeHtml(windowLabel)}</p></div><small>Last successful data refresh: ${escapeHtml(fmtTime(overview.timestamp))}</small></div>
        <div class="grid overview-average-grid">
          ${averageCard("Average temperature", "&#x2668;", averages.temperature, "°C", windowLabel)}
          ${averageCard("Average TDS", "&#x25C9;", averages.tds, "ppm", windowLabel)}
          ${averageCard("Average turbidity", "&#x2727;", averages.turbidity, "NTU", windowLabel, averages.turbidity?.calibration_status || "Calibration required")}
        </div>
      </section>
      <section class="overview-section" aria-label="Overall purpose status">
        <div class="section-heading"><div><h2>Overall purpose status</h2><p>Live sensor readings checked against thresholds</p></div></div>
        <article class="panel purpose-assessment-card">
          <div class="panel-header"><div><h2>Water Quality Purpose Assessment</h2><p>Uses the latest valid, fresh sensor values.</p></div></div>
          <div class="purpose-assessment-list">${purposeAssessmentMarkup(purposes)}</div>
          <small class="purpose-assessment-note">These threshold-based assessments do not certify water safety.</small>
        </article>
      </section>
      <section class="overview-section" aria-label="System status and activity">
        <div class="overview-system-grid">
          <article class="panel"><div class="panel-header"><div><h2>System status</h2><p>Connection and recent sensor health</p></div>${statusTag(overview.system_status || "unavailable")}</div>${sensorRows(overview, overview.live_reading)}</article>
          <article class="panel"><div class="panel-header"><div><h2>Recent activity</h2><p>Latest system and device events</p></div></div>${activityMarkup(overview.activity)}</article>
        </div>
      </section>`;
    $("#pullReading").addEventListener("click", pullLatest);
  }  function averageCard(name, icon, item, unit, windowLabel, detail = "") {
    const value = item && (item.value ?? item.average);
    const samples = Number.isInteger(item?.valid_samples) && item.valid_samples >= 0 ? item.valid_samples : 0;
    const available = samples > 0 && value !== null && value !== undefined && Number.isFinite(Number(value));
    const format = number => Number(number).toLocaleString(undefined, { maximumFractionDigits: 2 });
    const details = available ? [
      item.minimum !== null && item.minimum !== undefined ? `Minimum: ${format(item.minimum)} ${unit}` : null,
      item.maximum !== null && item.maximum !== undefined ? `Maximum: ${format(item.maximum)} ${unit}` : null,
      `Valid samples: ${samples}`,
      detail || null
    ].filter(Boolean).join(" · ") : "No valid readings yet";
    const display = available
      ? `${escapeHtml(format(value))}<span class="unit"> ${escapeHtml(unit)}</span>`
      : `<span class="muted">&mdash;</span><span class="unit"> ${escapeHtml(unit)}</span>`;
    const tag = available ? '<span class="tag ok">AVAILABLE</span>' : '<span class="tag neutral">NO DATA</span>';
    return `<article class="metric-card average-card"><div class="metric-top"><span class="metric-label">${escapeHtml(name)}</span><span class="metric-icon">${icon}</span></div><div class="metric-value">${display}</div><div class="average-detail">${escapeHtml(details)}</div><div class="metric-meta"><span>${escapeHtml(windowLabel)}</span>${tag}</div></article>`;
  }
  function renderAnalytics() {
    const latest = isRecord(state.latest) ? state.latest : {};
    const overview = isRecord(state.overview) ? state.overview : {};
    const liveReading = isRecord(overview.live_reading) ? overview.live_reading : {};
    const hasLiveReading = Boolean(liveReading.timestamp || liveReading.tds?.raw_voltage != null || liveReading.turbidity?.raw_voltage != null);
    const current = hasLiveReading ? liveReading : latest;
    const currentTemperature = mergeMeasurement(current.temperature, latest.temperature);
    const currentTds = mergeMeasurement(current.tds, latest.tds);
    const currentTurbidity = mergeMeasurement(current.turbidity, latest.turbidity);
    const summary = isRecord(state.summary) ? state.summary : {};
    const tdsDetail = `Raw voltage: ${liveValueText(currentTds.raw_voltage, "V", 4)}`;
    const turbidityDetail = currentTurbidity.valid === true
      ? "Calibrated NTU reading"
      : `Calibration required · Raw voltage: ${liveValueText(currentTurbidity.raw_voltage, "V", 4)}`;
    const tdsCardState = state.analyticsError ? "error" : currentTds.valid ? "valid" : "unavailable";
    const rows = [
      ["Temperature", "°C", summary.temperature],
      ["TDS", "ppm", summary.tds],
      ["Turbidity", "NTU", summary.turbidity],
      ["Legacy relative sensor index (not NTU)", "index / 100", summary.legacy_cloudiness_index]
    ];
    const rowStatus = stat => Number.isInteger(stat?.valid_samples) && stat.valid_samples > 0 ? "valid" : state.analyticsError ? "error" : "unavailable";
    content.innerHTML = `
      <div class="page-intro"><div><h2>Water Quality Analytics</h2><p>Latest sensor readings, trends and statistical summary.</p></div>
        <div class="controls"><select class="select" id="rangeSelect" aria-label="Time range"><option value="1h" ${state.range==="1h"?"selected":""}>Last hour</option><option value="24h" ${state.range==="24h"?"selected":""}>Last 24 hours</option><option value="7d" ${state.range==="7d"?"selected":""}>Last 7 days</option></select><button class="btn" id="refreshAnalytics">Refresh Analytics</button><button class="btn primary" id="pullReading" ${state.pullLoading ? "disabled" : ""}>${state.pullLoading ? "Requesting…" : "Pull Latest Reading"}</button></div>
      </div>
      <section class="grid metric-grid">
        ${metricCard("Water temperature","♨",currentTemperature.value ?? currentTemperature.celsius,"°C",currentTemperature.timestamp || latest.timestamp,currentTemperature.status,currentTemperature.value != null ? "valid" : "unavailable")}
        ${metricCard("Total dissolved solids","◉",currentTds.value ?? currentTds.ppm,"ppm",currentTds.timestamp || latest.timestamp,tdsCardState,currentTds.value != null ? "valid" : "unavailable",tdsDetail,state.analyticsLoading && currentTds.value == null)}
        ${metricCard("Turbidity","✧",currentTurbidity.value ?? currentTurbidity.ntu,"NTU",currentTurbidity.timestamp || latest.timestamp,currentTurbidity.status,currentTurbidity.calibration_status || "calibration_pending",turbidityDetail)}
      </section>
      <section class="grid chart-grid">
        ${chartCard("TDS Concentration Over Time","tdsChart","ppm","chart-panel--tds")}
        ${chartCard("Turbidity Over Time","turbidityChart","NTU","chart-panel--turbidity")}
        ${chartCard("Temperature Over Time","temperatureChart","°C","chart-panel--temperature")}
        ${chartCard("Legacy Relative Sensor Index History","legacyCloudinessChart","Relative index (0-100; not NTU)","chart-panel--legacy")}
      </section>
      <section class="panel"><div class="panel-header"><div><h2>Statistical Summary</h2><p>For the selected time range. Legacy index values are not NTU.</p></div></div>
        <div class="table-wrap"><table><thead><tr><th>Sensor / series</th><th>Average</th><th>Minimum</th><th>Maximum</th><th>Latest</th><th>Samples</th><th>Status</th></tr></thead>
        <tbody>${rows.map(([name,unit,stat]) => {
          const valid = Number.isInteger(stat?.valid_samples) && stat.valid_samples > 0 && stat.source !== "unavailable";
          const show = value => valid && value !== null && value !== undefined && Number.isFinite(Number(value))
            ? `${escapeHtml(Number(value).toLocaleString(undefined,{maximumFractionDigits:2}))} ${unit}` : "&mdash;";
          return `<tr><td>${name}</td><td>${show(stat?.average)}</td><td>${show(stat?.minimum)}</td><td>${show(stat?.maximum)}</td><td>${show(stat?.latest)}</td><td>${escapeHtml(stat?.valid_samples ?? "—")}</td><td>${statusTag(rowStatus(stat))}</td></tr>`;
        }).join("")}</tbody></table></div>
      </section>`;
    $("#rangeSelect").addEventListener("change", e => { state.range = e.target.value; refreshAnalytics(); });
    $("#refreshAnalytics").addEventListener("click", refreshAnalytics);
    $("#pullReading").addEventListener("click", pullLatest);
    drawCharts();
  }  function chartCard(title, canvasId, unit, layoutClass) {
    return `<article class="panel chart-panel ${layoutClass}"><div class="panel-header"><div><h2>${title}</h2><p>Selected range &middot; ${unit}</p></div></div><div class="chart-wrap"><canvas id="${canvasId}" aria-label="${title}" role="img"></canvas></div></article>`;
  }
  function drawCharts() {
    if (!window.LUWCharts) return;
    const history = isRecord(state.history) ? state.history : {};
    const summary = isRecord(state.summary) ? state.summary : {};
    const missingMessage = (name, samples) => state.analyticsLoading
      ? `Loading ${name} history...`
      : state.analyticsError
        ? `${name} history could not be loaded. Check the backend connection.`
        : Number.isInteger(samples) && samples > 0
          ? `${name} readings exist in this range, but no chart points were returned.`
          : Number.isInteger(samples)
            ? `No ${name} history is available for the selected range.`
            : `${name} history could not be confirmed for the selected range.`;
    window.LUWCharts.draw("temperature","temperatureChart",Array.isArray(history.temperature) ? history.temperature : [],"temperature","°C",missingMessage("temperature",summary.temperature?.valid_samples));
    window.LUWCharts.draw("tds","tdsChart",Array.isArray(history.tds) ? history.tds : [],"tds","ppm",missingMessage("TDS",summary.tds?.valid_samples));
    window.LUWCharts.draw("turbidity","turbidityChart",Array.isArray(history.turbidity) ? history.turbidity : [],"turbidity","NTU",missingMessage("calibrated turbidity",summary.turbidity?.valid_samples));
    window.LUWCharts.draw("legacy-cloudiness","legacyCloudinessChart",Array.isArray(history.legacy_cloudiness_index) ? history.legacy_cloudiness_index : [],"legacy_cloudiness","index / 100",missingMessage("legacy relative sensor index",summary.legacy_cloudiness_index?.valid_samples));
  }  function renderDevices() {
    const data = isRecord(state.devices) ? state.devices : {};
    const device = isRecord(data.device) ? data.device : (Array.isArray(data.devices) ? data.devices.find(isRecord) || {} : {});
    const overview = isRecord(state.overview) ? state.overview : {};
    const sensors = isRecord(overview.live_reading)
      ? overview.live_reading
      : (isRecord(state.latest) ? state.latest : (device.latest_reading || {}));
    const sensorHealth = isRecord(overview.sensor_health) ? overview.sensor_health : {};
    const list = [
      {key:"temperature",name:"DS18B20 Temperature Sensor",icon:"♨",detail:"Water temperature",unit:"°C"},
      {key:"tds",name:"TDS Sensor",icon:"◉",detail:"Total dissolved solids",unit:"ppm"},
      {key:"turbidity",name:"Turbidity Sensor",icon:"✧",detail:"Turbidity in NTU; calibration required",unit:"NTU"}
    ];
    content.innerHTML = `
      <div class="page-intro"><div><h2>Devices</h2><p>Hardware connection, sensor health and recent communication.</p></div><button class="btn" id="refreshDevices">Refresh Status</button></div>
      <section class="grid device-grid">
        <article class="device-card"><div class="device-top"><span class="device-symbol">▣</span>${statusTag(overview.serial_connection_status || state.status?.serial_connection_status || "unavailable")}</div><h3>NodeMCU ESP8266</h3><p>USB serial monitoring device</p><div class="device-details">
          ${detailRow("Device ID",device.id || device.device_key || "Not reported")}
          ${detailRow("Connection",device.connection_type || "USB serial")}
          ${detailRow("Serial port",device.serial_port || state.status?.serial_port || "Not detected")}
          ${detailRow("Device uptime",device.uptime_seconds != null ? formatUptime(device.uptime_seconds) : "Unavailable")}
          ${detailRow("Last message",fmtTime(device.last_seen || device.last_message_at || state.latest?.timestamp || state.status?.last_data_at))}
          ${detailRow("Firmware",device.firmware_version || "Not reported")}
        </div></article>
        ${list.map(sensor => {
          const item = sensor.key === "tds" ? (overview.latest_valid_tds || sensors.tds || {})
            : sensor.key === "turbidity" ? (sensors.turbidity || {}) : (sensors.temperature || {});
          const healthKey = sensor.key;
          const health = sensorHealth[healthKey] || {};
          const value = item.value ?? item.celsius ?? item.ppm ?? item.ntu;
          const sensorState = health.status || (item.valid ? "valid" : item.status || "unavailable");
          return `<article class="device-card"><div class="device-top"><span class="device-symbol">${sensor.icon}</span>${statusTag(sensorState)}</div><h3>${sensor.name}</h3><p>${sensor.detail}</p><div class="device-details">
            ${detailRow("Latest reading",value != null && item.valid === true ? `${value} ${sensor.unit}` : "Unavailable")}
            ${detailRow("Last sensor report",fmtTime(item.timestamp || health.latest_at))}
            ${sensor.key === "tds" && item.raw_voltage != null ? detailRow("Raw TDS voltage",`${Number(item.raw_voltage).toLocaleString(undefined,{maximumFractionDigits:4})} V`) : ""}
            ${sensor.key === "turbidity" && item.raw_voltage != null ? detailRow("Raw sensor voltage",`${Number(item.raw_voltage).toLocaleString(undefined,{maximumFractionDigits:4})} V`) : ""}
          </div></article>`;
        }).join("")}
        <article class="device-card"><div class="device-top"><span class="device-symbol">⌘</span>${statusTag(state.status?.system_status || "unavailable")}</div><h3>Backend &amp; Serial Reader</h3><p>Local data collection service</p><div class="device-details">
          ${detailRow("API",state.connected ? "Reachable" : "Disconnected")}
          ${detailRow("Serial reader",state.status?.serial_reader_status || "Unavailable")}
          ${detailRow("Last data received",fmtTime(state.status?.last_data_at))}
        </div></article>
      </section>`;
    $("#refreshDevices").addEventListener("click", loadAll);
  }
  function predictionCard(metric, label, unit, item, targetAt, horizonHours) {
    const current = item?.current ? liveValueText(item.current.value, unit) : "Unavailable";
    const modelRows = ["Random Forest", "XGBoost"].map(name => {
      const model = isRecord(item?.models?.[name]) ? item.models[name] : {};
      const available = model.status === "available" && Number.isFinite(Number(model.predicted));
      const direction = available ? model.direction : null;
      const arrow = direction === "increasing" ? "&uarr;" : direction === "decreasing" ? "&darr;" : direction === "unchanged" ? "&rarr;" : "&middot;";
      const change = available && model.change !== null && model.change !== undefined
        ? `${direction || "unchanged"} by ${liveValueText(Math.abs(model.change), unit)}`
        : model.reason || "Prediction unavailable";
      return `<div class="forecast-model-result"><div><span>${escapeHtml(name)}</span>${statusTag(model.status || "unavailable")}</div><strong>${escapeHtml(available ? liveValueText(model.predicted, unit) : "Unavailable")}</strong><small>${available ? arrow : ""} ${escapeHtml(change)}</small></div>`;
    }).join("");
    const horizonUnit = Number(horizonHours) === 1 ? "hour" : "hours";
    return `<article class="panel forecast-card" data-metric="${escapeHtml(metric)}">
      <div class="forecast-card-heading"><div><span class="metric-label">${escapeHtml(label)}</span><small>${escapeHtml(unit === "NTU" ? "Calibrated NTU readings only" : "Historical sensor readings")}</small></div>${statusTag(item?.status || "unavailable")}</div>
      <div class="forecast-current"><span>Latest measured</span><strong>${escapeHtml(current)}</strong><small>${escapeHtml(fmtTime(item?.current?.timestamp))}</small></div>
      <div class="forecast-result"><span>Predictions in ${escapeHtml(horizonHours)} ${horizonUnit} &middot; ${escapeHtml(fmtTime(targetAt))}</span><div class="forecast-model-results">${modelRows}</div></div>
      <small class="muted forecast-reliability">Each result is produced by its named regressor. Unavailable models are shown without substituting another algorithm.</small>
    </article>`;
  }
  function drawForecastCharts(forecast) {
    if (!window.LUWCharts) return;
    const parameters = isRecord(forecast?.parameters) ? forecast.parameters : {};
    const horizon = forecast?.horizon_hours || state.forecastHorizonHours;
    const charts = [
      ["temperature_c", "temperatureForecastChart", "temperature", "°C"],
      ["tds_ppm", "tdsForecastChart", "tds", "ppm"],
      ["turbidity_ntu", "turbidityForecastChart", "turbidity", "NTU"]
    ];
    charts.forEach(([key, canvas, sensor, unit]) => {
      const item = parameters[key] || {};
      const modelOutputs = isRecord(item.models) ? item.models : {};
      const extra = [
        ["Random Forest", "#43d3a7"],
        ["XGBoost", "#c48cff"]
      ].map(([name, color]) => {
        const model = modelOutputs[name] || {};
        return model.forecast_points?.length
          ? { label: `${name} (${horizon} h)`, points: model.forecast_points, color, dashed: true }
          : null;
      }).filter(Boolean);
      window.LUWCharts.draw(`forecast-${key}`, canvas, item.historical_points || [], sensor, unit,
        item.reason || "No valid historical readings are available for this parameter.", extra, "Historical hourly means");
    });
  }
  function renderPredictions() {
    const forecast = isRecord(state.forecast) ? state.forecast : null;
    const parameters = isRecord(forecast?.parameters) ? forecast.parameters : {};
    const targetAt = forecast?.target_at || null;
    const horizons = [[1, "1 hour"], [6, "6 hours"], [12, "12 hours"], [24, "24 hours"], [72, "3 days"]];
    const modelStatuses = Array.isArray(forecast?.model_status) ? forecast.model_status : [];
    const modelStatusMarkup = ["Random Forest", "XGBoost"].map(name => {
      const model = modelStatuses.find(item => item?.name === name) || {};
      return `<div class="forecast-model-status"><strong>${escapeHtml(name)}</strong>${statusTag(model.status || "unavailable")}<small>${escapeHtml(model.algorithm || (name === "Random Forest" ? "Random Forest regressor" : "XGBoost regressor"))}</small></div>`;
    }).join("");
    const error = state.forecastError
      ? `<div class="ml-error">Future predictions could not be loaded. Check the backend and PostgreSQL connection, then refresh.</div>` : "";
    content.innerHTML = `
      <div class="page-intro forecast-intro"><div><h2>Future Sensor Predictions</h2><p>Separate Random Forest and XGBoost regressors use recorded sensor readings for each selected horizon.</p></div>
        <div class="controls"><label for="forecastHorizon">Prediction horizon</label><select class="select" id="forecastHorizon" ${state.forecastLoading ? "disabled" : ""}>${horizons.map(([hours,label]) => `<option value="${hours}" ${state.forecastHorizonHours === hours ? "selected" : ""}>${label}</option>`).join("")}</select><button class="btn primary" id="refreshPredictions" ${state.forecastLoading ? "disabled" : ""}>${state.forecastLoading ? "Loading..." : "Refresh predictions"}</button></div>
      </div>
      ${error}
      ${!forecast && !state.forecastLoading ? emptyState("Predictions unavailable", "The backend did not return a prediction response. No projected values are being displayed.") : ""}
      <div class="forecast-target"><span>Future target</span><strong>${escapeHtml(targetAt ? fmtTime(targetAt) : state.forecastLoading ? "Loading from database..." : "Unavailable")}</strong><small>${forecast ? `${escapeHtml(forecast.horizon_hours)} hour horizon · generated ${escapeHtml(fmtTime(forecast.generated_at))}` : "Target time is returned by the backend with the selected horizon."}</small></div>
      <section class="forecast-model-status-list" aria-label="Regression model availability">${modelStatusMarkup}</section>
      <section class="grid forecast-card-grid">
        ${predictionCard("temperature_c", "Predicted Temperature", "°C", parameters.temperature_c, targetAt, forecast?.horizon_hours || state.forecastHorizonHours)}
        ${predictionCard("tds_ppm", "Predicted TDS", "ppm", parameters.tds_ppm, targetAt, forecast?.horizon_hours || state.forecastHorizonHours)}
        ${predictionCard("turbidity_ntu", "Predicted Turbidity", "NTU", parameters.turbidity_ntu, targetAt, forecast?.horizon_hours || state.forecastHorizonHours)}
      </section>
      <section class="grid chart-grid forecast-chart-grid">
        ${chartCard("Temperature · history and model predictions", "temperatureForecastChart", "°C", "chart-panel--temperature")}
        ${chartCard("TDS · history and model predictions", "tdsForecastChart", "ppm", "chart-panel--tds")}
        ${chartCard("Turbidity · calibrated NTU history and model predictions", "turbidityForecastChart", "NTU", "chart-panel--turbidity")}
      </section>
      `;
    $("#forecastHorizon").addEventListener("change", event => {
      state.forecastHorizonHours = Number(event.target.value);
      refreshPredictions(false);
    });
    $("#refreshPredictions").addEventListener("click", () => refreshPredictions(true));
    drawForecastCharts(forecast);
  }
  async function refreshPredictions(showToast = false) {
    if (state.forecastLoading) return;
    state.forecastLoading = true;
    state.forecastError = null;
    state.forecast = null;
    if (state.page === "predictions") renderPredictions();
    try {
      state.forecast = await LUWApi.forecast(state.forecastHorizonHours);
    } catch (_) {
      state.forecast = null;
      state.forecastError = true;
    }
    state.forecastLoading = false;
    if (state.page === "predictions") renderPredictions();
    if (showToast) showToastMessage(state.forecast ? "Future forecast refreshed from stored readings." : "Forecast unavailable. Check the backend and database connection.");
  }  function detailRow(label, value) {
    return `<div class="status-row"><span class="muted">${escapeHtml(label)}</span><span style="text-align:right;font-size:13px">${escapeHtml(value)}</span></div>`;
  }
  function formatUptime(seconds) {
    const n = Math.max(0, Number(seconds) || 0), days = Math.floor(n/86400), hours = Math.floor(n%86400/3600), minutes = Math.floor(n%3600/60);
    return `${days ? `${days}d ` : ""}${hours}h ${minutes}m`;
  }
  function render() {
    $("#pageTitle").textContent = pageNames[state.page];
    document.querySelectorAll(".nav-link").forEach(b => b.classList.toggle("active", b.dataset.page === state.page));
    if (state.page === "overview") renderOverview();
    if (state.page === "analytics") renderAnalytics();
    if (state.page === "devices") renderDevices();
    if (state.page === "predictions") renderPredictions();
  }
  function setConnection(connected, status) {
    state.connected = connected;
    if (isRecord(status)) state.status = status;
    const text = connected ? String(state.status?.system_status || "Connected") : "Backend disconnected";
    $("#headerStatus").textContent = text.replaceAll("_"," ");
    $("#sidebarConnection").textContent = connected ? "Backend connected" : "Backend disconnected";
    const cls = !connected ? "bad" : ["degraded", "offline"].includes(String(state.status?.system_status).toLowerCase()) ? "warn" : "ok";
    $("#headerDot").className = `status-dot ${cls}`;
    $("#sidebarDot").className = `status-dot ${cls}`;
  }
  async function loadAll() {
    try {
      const [health, status, overview, devices] = await Promise.all([
        LUWApi.health(), LUWApi.status(), LUWApi.overview(), LUWApi.devices()
      ]);
      state.overview = isRecord(overview) ? overview : {};
      state.latest = hasReading(state.overview.latest) ? state.overview.latest : null;
      state.devices = isRecord(devices) ? devices : {};
      const healthy = isRecord(health) && health.api === "ok" && health.database === "connected";
      setConnection(healthy, status);
      state.loading = false;
      if (state.page === "analytics") await refreshAnalytics(false);
      else if (state.page === "predictions") await refreshPredictions(false);
      else render();
      $("#footerUpdated").textContent = healthy ? `Updated ${new Date().toLocaleTimeString()}` : "Backend unavailable";
    } catch (err) {
      state.loading = false;
      setConnection(false, {
        ...(isRecord(state.status) ? state.status : {}),
        system_status: "offline",
        device_status: "offline",
        serial_reader_status: "unavailable"
      });
      if (!state.overview) state.overview = {};
      if (!isRecord(state.latest)) state.latest = null;
      if (!state.devices) state.devices = {};
      render();
      $("#footerUpdated").textContent = "Waiting for backend connection";
    }
  }
  async function refreshAnalytics(showToast = true) {
    state.analyticsLoading = true;
    state.analyticsError = false;
    if (state.page === "analytics") renderAnalytics();
    try {
      const [history, summary, latest] = await Promise.all([
        LUWApi.history(state.range), LUWApi.summary(state.range), LUWApi.latest()
      ]);
      state.history = history;
      state.summary = summary;
      if (hasReading(latest)) state.latest = latest;
      state.analyticsError = false;
      state.analyticsLoading = false;
      if (state.page === "analytics") renderAnalytics();
      if (showToast) showToastMessage("Analytics refreshed.");
    } catch (err) {
      state.history = {}; state.summary = {};
      state.analyticsError = true;
      state.analyticsLoading = false;
      if (state.page === "analytics") renderAnalytics();
      if (showToast) showToastMessage("Backend unavailable. Analytics could not be refreshed.");
    }
  }
  async function pullLatest() {
    if (state.pullLoading) return;
    state.pullLoading = true;
    render();
    try {
      const result = await LUWApi.pull();
      const message = (isRecord(result) && result.message) || "Reading request accepted.";
      await loadAll();
      if (state.page === "overview") await refreshAnalytics(false);
      showToastMessage(message);
    } catch (err) {
      showToastMessage("Could not request a reading. Check the backend and USB serial connection.");
    } finally {
      state.pullLoading = false;
      render();
    }
  }
  function showToastMessage(message) {
    const toast = $("#toast"); toast.textContent = message; toast.classList.add("show");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.classList.remove("show"), 3200);
  }
  document.querySelectorAll(".nav-link").forEach(button => button.addEventListener("click", () => {
    state.page = button.dataset.page;
    $("#sidebar").classList.remove("open");
    render();
    if (state.page === "analytics") refreshAnalytics(false);
    if (state.page === "devices") loadAll();
    if (state.page === "predictions") refreshPredictions(false);
  }));
  $("#mobileMenu").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  document.addEventListener("click", e => {
    if (window.innerWidth <= 760 && !e.target.closest("#sidebar") && !e.target.closest("#mobileMenu")) $("#sidebar").classList.remove("open");
  });
  render();
  loadAll();
  setInterval(() => { if (document.visibilityState === "visible") loadAll(); }, 10000);
})();
