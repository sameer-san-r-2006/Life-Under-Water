(() => {
  const API_BASE = window.LUW_API_BASE || "http://127.0.0.1:8000/api";
  const isRecord = value => value !== null && typeof value === "object" && !Array.isArray(value);

  function finiteNumber(value) {
    if (value === null || value === undefined || (typeof value === "string" && !value.trim())) return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function calibrationStatus(value) {
    return typeof value === "string" && value.trim()
      ? value.trim().toLowerCase()
      : "calibration_pending";
  }

  function rangeHours(range) {
    const ranges = { "1h": 1, "24h": 24, "7d": 168 };
    return Object.prototype.hasOwnProperty.call(ranges, range) ? ranges[range] : 24;
  }

  function validTimestamp(value) {
    return typeof value === "string" && value.trim() && Number.isFinite(Date.parse(value));
  }

  function validTdsRow(row, expectedMethod = null) {
    if (!isRecord(row)) return false;
    const value = finiteNumber(row.tds_ppm);
    const method = typeof row.tds_conversion_method === "string" ? row.tds_conversion_method.trim() : "";
    const status = calibrationStatus(row.tds_calibration_status);
    const source = row.tds_source || (method === "legacy_demo_lookup_v1" && status === "estimated"
      ? "demo_estimate" : status === "calibrated" ? "calibrated" : "unavailable");
    if (value === null || value < 0) return false;
    if (source === "demo_estimate") {
      const voltage = finiteNumber(row.tds_voltage_v);
      return method === "legacy_demo_lookup_v1" && status === "estimated" &&
        (voltage === null || (voltage >= 0.2 && voltage <= 0.45));
    }
    return source === "calibrated" && method !== "legacy_demo_lookup_v1" &&
      status === "calibrated" && row.tds_valid === true &&
      (!expectedMethod || method === expectedMethod);
  }

  function normalizeReading(payload, expectedTdsMethod = null) {
    const envelope = isRecord(payload) ? payload : {};
    const nestedReading = isRecord(envelope.reading) ? envelope.reading : null;
    const configuredMethod = typeof expectedTdsMethod === "string" && expectedTdsMethod.trim()
      ? expectedTdsMethod.trim()
      : typeof envelope.tds_working_calibration_method === "string" && envelope.tds_working_calibration_method.trim()
        ? envelope.tds_working_calibration_method.trim()
        : null;
    const hasFlatFields = [
      "recorded_at", "temperature_c", "tds_ppm", "tds_voltage_v", "turbidity_ntu", "turbidity_voltage_v", "cloudiness_index"
    ].some(key => Object.prototype.hasOwnProperty.call(envelope, key));
    const row = nestedReading || (hasFlatFields ? envelope : null);

    const emptyReading = () => ({
        timestamp: null,
        temperature: { value: null, celsius: null, timestamp: null, valid: false, status: "waiting_for_data" },
        tds: { value: null, ppm: null, raw_voltage: null, timestamp: null, valid: false, status: "waiting_for_data", calibration_status: "calibration_pending", source: "unavailable" },
        turbidity: { value: null, ntu: null, raw_voltage: null, timestamp: null, valid: false, status: "calibration_pending", calibration_status: "calibration_pending" },
        legacy_cloudiness_index: { value: null, index: null, raw_voltage: null, timestamp: null, valid: false, status: "waiting_for_data" },
        cloudiness: { value: null, index: null, raw_voltage: null, timestamp: null, valid: false, status: "waiting_for_data" }
      });

    function addLatestTds(base) {
      const latestTds = isRecord(envelope.latest_valid_tds) ? envelope.latest_valid_tds : null;
      if (!latestTds) return base;
      const ppm = finiteNumber(latestTds.tds_ppm ?? latestTds.value);
      const method = typeof latestTds.tds_conversion_method === "string" ? latestTds.tds_conversion_method.trim() : "";
      const source = latestTds.tds_source || (method === "legacy_demo_lookup_v1" ? "demo_estimate" : "calibrated");
      const calibration = calibrationStatus(latestTds.tds_calibration_status);
      const methodMatches = !configuredMethod || source === "demo_estimate" || method === configuredMethod;
      if (ppm === null || !validTdsRow({ ...latestTds, tds_ppm: ppm }, configuredMethod) || !methodMatches) return base;
      const timestamp = latestTds.recorded_at || latestTds.timestamp || null;
      return {
        ...base,
        tds: {
          ...base.tds,
          value: ppm,
          ppm,
          raw_adc: finiteNumber(latestTds.tds_raw_adc),
          raw_voltage: finiteNumber(latestTds.tds_voltage_v),
          timestamp,
          valid: true,
          status: "ok",
          calibration_status: calibration,
          conversion_method: method || null,
          source
        }
      };
    }

    if (!row) return addLatestTds(emptyReading());

    const timestamp = row.recorded_at || row.timestamp || null;
    const temperatureRaw = finiteNumber(row.temperature_c);
    const temperatureValue = row.temperature_valid === true ? temperatureRaw : null;
    const tdsCalibration = calibrationStatus(row.tds_calibration_status);
    const turbidityCalibration = calibrationStatus(row.turbidity_calibration_status);
    const tdsRaw = finiteNumber(row.tds_ppm);
    const turbidityRaw = finiteNumber(row.turbidity_ntu);
    const tdsMethod = typeof row.tds_conversion_method === "string" ? row.tds_conversion_method.trim() : "";
    const tdsVoltage = finiteNumber(row.tds_voltage_v);
    const tdsSource = row.tds_source || (tdsMethod === "legacy_demo_lookup_v1" && tdsCalibration === "estimated"
      ? "demo_estimate" : tdsCalibration === "calibrated" ? "calibrated" : "unavailable");
    const tdsMethodMatches = !configuredMethod || tdsSource === "demo_estimate" || tdsMethod === configuredMethod;
    const demoVoltageMatches = tdsVoltage === null || (tdsVoltage >= 0.2 && tdsVoltage <= 0.45);
    const validDemoTds = tdsSource === "demo_estimate" && tdsMethod === "legacy_demo_lookup_v1" &&
      tdsCalibration === "estimated" && tdsRaw !== null && tdsRaw >= 0 && demoVoltageMatches;
    const validCalibratedTds = tdsSource === "calibrated" && tdsCalibration === "calibrated" &&
      tdsMethod !== "legacy_demo_lookup_v1" && tdsMethodMatches && row.tds_valid === true &&
      tdsRaw !== null && tdsRaw >= 0;
    const tdsValue = validDemoTds || validCalibratedTds ? tdsRaw : null;
    const turbidityValue = row.turbidity_valid === true && turbidityCalibration === "calibrated" ? turbidityRaw : null;
    const cloudinessRaw = finiteNumber(row.cloudiness_index);
    const cloudinessValue = row.cloudiness_valid === true && cloudinessRaw !== null &&
      cloudinessRaw >= 0 && cloudinessRaw <= 100 ? cloudinessRaw : null;

    const result = {
      timestamp,
      temperature: {
        value: temperatureValue,
        celsius: temperatureValue,
        timestamp,
        valid: row.temperature_valid === true,
        status: temperatureValue !== null ? "ok" : (temperatureRaw !== null && row.temperature_valid === false ? "invalid" : "waiting_for_data")
      },
      tds: {
        value: tdsValue,
        ppm: tdsValue,
        raw_adc: finiteNumber(row.tds_raw_adc),
        raw_voltage: finiteNumber(row.tds_voltage_v),
        timestamp,
        valid: tdsValue !== null,
        status: tdsValue !== null ? "ok" : (tdsCalibration === "invalid" || (tdsRaw !== null && row.tds_valid === false) ? "invalid" : "waiting_for_data"),
        calibration_status: tdsValue !== null ? tdsCalibration : (tdsCalibration === "invalid" ? "invalid" : "calibration_pending"),
        conversion_method: tdsMethod || null,
        source: tdsValue !== null ? tdsSource : "unavailable"
      },
      turbidity: {
        value: turbidityValue,
        ntu: turbidityValue,
        raw_adc: finiteNumber(row.turbidity_raw_adc),
        raw_voltage: finiteNumber(row.turbidity_voltage_v),
        timestamp,
        valid: row.turbidity_valid === true,
        status: turbidityValue !== null ? "ok" : (turbidityCalibration === "calibrated" && row.turbidity_valid === false ? "invalid" : turbidityCalibration),
        calibration_status: turbidityCalibration
      },
      legacy_cloudiness_index: {
        value: cloudinessValue,
        index: cloudinessValue,
        raw_voltage: finiteNumber(row.turbidity_voltage_v),
        timestamp,
        valid: cloudinessValue !== null,
        status: cloudinessValue !== null ? "ok" : (cloudinessRaw !== null ? "invalid" : "waiting_for_data"),
        scale_minimum: 0,
        scale_maximum: 100
      }
    };
    result.cloudiness = result.legacy_cloudiness_index;
    return addLatestTds(result);
  }

  function normalizeHistory(payload) {
    const envelope = isRecord(payload) ? payload : {};
    const configuredMethod = typeof envelope.tds_working_calibration_method === "string"
      ? envelope.tds_working_calibration_method.trim()
      : "";
    const readings = Array.isArray(envelope.readings) ? envelope.readings : [];
    const points = { temperature: [], tds: [], turbidity: [], legacy_cloudiness_index: [] };

    readings.forEach(row => {
      if (!isRecord(row)) return;
      const timestamp = row.recorded_at || row.timestamp;
      if (!validTimestamp(timestamp)) return;

      const temperature = finiteNumber(row.temperature_c);
      if (row.temperature_valid === true && temperature !== null) {
        points.temperature.push({ timestamp, value: temperature });
      }

      const tds = finiteNumber(row.tds_ppm);
      if (validTdsRow(row, configuredMethod || null)) {
        points.tds.push({ timestamp, value: tds });
      }

      const turbidity = finiteNumber(row.turbidity_ntu);
      if (row.turbidity_valid === true && calibrationStatus(row.turbidity_calibration_status) === "calibrated" && turbidity !== null) {
        points.turbidity.push({ timestamp, value: turbidity });
      }

      const cloudiness = finiteNumber(row.cloudiness_index);
      if (row.cloudiness_valid === true && cloudiness !== null && cloudiness >= 0 && cloudiness <= 100) {
        points.legacy_cloudiness_index.push({ timestamp, value: cloudiness });
      }
    });

    Object.values(points).forEach(series => series.sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp)));
    points.cloudiness_index = points.legacy_cloudiness_index;
    return points;
  }

  function normalizeTemperatureSummary(value) {
    const item = isRecord(value) ? value : {};
    const samples = Number.isInteger(item.valid_samples) && item.valid_samples >= 0 ? item.valid_samples : null;
    const hasSamples = samples !== null && samples > 0;
    const latestTimestamp = hasSamples && validTimestamp(item.latest_timestamp) ? item.latest_timestamp : null;
    return {
      average: hasSamples ? finiteNumber(item.average) : null,
      minimum: hasSamples ? finiteNumber(item.minimum) : null,
      maximum: hasSamples ? finiteNumber(item.maximum) : null,
      latest: hasSamples ? finiteNumber(item.latest) : null,
      latest_timestamp: latestTimestamp,
      valid_samples: samples,
      calibration_status: "calibrated",
      unit: item.unit || "°C",
      source: item.source || "measured",
      data_quality: item.data_quality || (hasSamples ? "valid" : "unavailable"),
      time_window: isRecord(item.time_window) ? item.time_window : null
    };
  }

  function normalizeCalibratedSummary(value) {
    const item = isRecord(value) ? value : {};
    const samples = Number.isInteger(item.valid_samples) && item.valid_samples >= 0 ? item.valid_samples : null;
    const permitted = item.calibrated_readings_only === true && samples !== null && samples > 0;
    const visibleSamples = item.calibrated_readings_only === true ? samples : null;
    const latestTimestamp = permitted && validTimestamp(item.latest_timestamp) ? item.latest_timestamp : null;
    return {
      average: permitted ? finiteNumber(item.average) : null,
      minimum: permitted ? finiteNumber(item.minimum) : null,
      maximum: permitted ? finiteNumber(item.maximum) : null,
      latest: permitted ? finiteNumber(item.latest) : null,
      latest_timestamp: latestTimestamp,
      valid_samples: visibleSamples,
      calibration_status: item.calibrated_readings_only === true
        ? "calibrated"
        : "calibration_pending",
      calibration_method: item.calibration_method || null,
      unit: item.unit || null,
      source: permitted ? (item.source || "calibrated") : "unavailable",
      data_quality: permitted ? (item.data_quality || "calibrated") : "unavailable",
      time_window: isRecord(item.time_window) ? item.time_window : null
    };
  }

  function normalizeRelativeSummary(value) {
    const item = isRecord(value) ? value : {};
    const samples = Number.isInteger(item.valid_samples) && item.valid_samples >= 0 ? item.valid_samples : null;
    const permitted = item.relative_index_only === true && samples !== null && samples > 0;
    return {
      average: permitted ? finiteNumber(item.average) : null,
      minimum: permitted ? finiteNumber(item.minimum) : null,
      maximum: permitted ? finiteNumber(item.maximum) : null,
      latest: permitted ? finiteNumber(item.latest) : null,
      latest_timestamp: permitted && validTimestamp(item.latest_timestamp) ? item.latest_timestamp : null,
      valid_samples: samples,
      status: permitted ? "valid" : "waiting_for_data",
      unit: "index / 100",
      source: item.source || "firmware_relative_index",
      data_quality: item.data_quality || (permitted ? "relative_index" : "unavailable"),
      time_window: isRecord(item.time_window) ? item.time_window : null,
      scale_minimum: finiteNumber(item.scale_minimum) ?? 0,
      scale_maximum: finiteNumber(item.scale_maximum) ?? 100
    };
  }

  function normalizeTdsSummary(value, expectedMethod = null) {
    const item = isRecord(value) ? value : {};
    const samples = Number.isInteger(item.valid_samples) && item.valid_samples >= 0 ? item.valid_samples : null;
    const source = item.source || "unavailable";
    const demoEstimate = source === "demo_estimate" && item.calibration_status === "estimated" &&
      item.calibration_method === "legacy_demo_lookup_v1";
    const calibrated = source === "calibrated" && item.calibration_status === "calibrated" &&
      item.calibration_method !== "legacy_demo_lookup_v1" && (!expectedMethod || item.calibration_method === expectedMethod);
    const permitted = (demoEstimate || calibrated) && samples !== null && samples > 0;
    const latestTimestamp = permitted && validTimestamp(item.latest_timestamp) ? item.latest_timestamp : null;
    return {
      average: permitted ? finiteNumber(item.average) : null,
      minimum: permitted ? finiteNumber(item.minimum) : null,
      maximum: permitted ? finiteNumber(item.maximum) : null,
      latest: permitted ? finiteNumber(item.latest) : null,
      latest_timestamp: latestTimestamp,
      valid_samples: permitted ? samples : 0,
      calibration_status: item.calibration_status || (source === "demo_estimate" ? "estimated" : permitted ? "calibrated" : "unavailable"),
      unit: "ppm",
      source: permitted ? source : "unavailable",
      data_quality: permitted ? (item.data_quality || (source === "demo_estimate" ? "estimated" : "calibrated")) : "unavailable",
      calibration_method: item.calibration_method || null,
      time_window: isRecord(item.time_window) ? item.time_window : null
    };
  }

  function normalizeSummary(payload) {
    const envelope = isRecord(payload) ? payload : {};
    return {
      period_hours: Number.isInteger(envelope.period_hours) ? envelope.period_hours : null,
      reading_count: Number.isInteger(envelope.reading_count) && envelope.reading_count >= 0 ? envelope.reading_count : null,
      time_window: isRecord(envelope.time_window) ? envelope.time_window : null,
      temperature: normalizeTemperatureSummary(envelope.temperature_c),
      tds: normalizeTdsSummary(envelope.tds_ppm, envelope.tds_working_calibration_method),
      turbidity: normalizeCalibratedSummary(envelope.turbidity_ntu),
      legacy_cloudiness_index: normalizeRelativeSummary(envelope.legacy_cloudiness_index || envelope.cloudiness_index),
      cloudiness_index: normalizeRelativeSummary(envelope.legacy_cloudiness_index || envelope.cloudiness_index)
    };
  }

  function normalizeOverviewAverages(payload, expectedTdsMethod = null) {
    const averages = isRecord(payload) ? payload : {};
    const normalizeMetric = value => {
      const item = isRecord(value) ? value : {};
      const samples = Number.isInteger(item.valid_samples) && item.valid_samples >= 0 ? item.valid_samples : null;
      const permitted = samples !== null && samples > 0;
      const average = permitted ? finiteNumber(item.average ?? item.value) : null;
      return {
        value: average,
        average,
        minimum: permitted ? finiteNumber(item.minimum) : null,
        maximum: permitted ? finiteNumber(item.maximum) : null,
        valid_samples: samples,
        data_status: permitted && average !== null ? "valid" : "waiting_for_data",
        calibration_status: item.calibration_status || null,
        calibration_method: item.calibration_method || null,
        unit: item.unit || null,
        source: permitted && average !== null ? (item.source || "measured") : "unavailable",
        data_quality: permitted && average !== null ? (item.data_quality || "valid") : "unavailable",
        time_window: isRecord(item.time_window) ? item.time_window : null
      };
    };
    const normalizedTds = normalizeTdsSummary(averages.tds, expectedTdsMethod);
    normalizedTds.value = normalizedTds.average;
    normalizedTds.data_status = normalizedTds.valid_samples > 0 && normalizedTds.average !== null ? "valid" : "waiting_for_data";
    return {
      temperature: normalizeMetric(averages.temperature),
      tds: normalizedTds,
      turbidity: normalizeMetric(averages.turbidity),
      legacy_cloudiness_index: normalizeMetric(averages.legacy_cloudiness_index || averages.cloudiness_index),
      cloudiness_index: normalizeMetric(averages.legacy_cloudiness_index || averages.cloudiness_index),
      time_window: isRecord(averages.temperature?.time_window) ? averages.temperature.time_window : null
    };
  }

  function normalizeForecast(payload) {
    const envelope = isRecord(payload) ? payload : {};
    const target = typeof envelope.target_at === "string" && validTimestamp(envelope.target_at) ? envelope.target_at : null;
    const parameters = isRecord(envelope.parameters) ? envelope.parameters : {};
    const normalizePoints = list => Array.isArray(list) ? list.filter(point => isRecord(point) &&
      finiteNumber(point.value) !== null && validTimestamp(point.timestamp)).map(point => ({
        timestamp: point.timestamp,
        value: finiteNumber(point.value)
      })) : [];
    const normalizeModel = item => {
      const prediction = isRecord(item) ? item : {};
      const status = prediction.status === "available"
        ? finiteNumber(prediction.predicted) !== null && target ? "available" : "invalid_response"
        : (prediction.status || "unavailable");
      return {
        ...prediction,
        status,
        predicted: status === "available" ? finiteNumber(prediction.predicted) : null,
        forecast_points: status === "available" ? normalizePoints(prediction.forecast_points) : []
      };
    };
    const normalizeParameter = (key, unit) => {
      const item = isRecord(parameters[key]) ? parameters[key] : {};
      const models = isRecord(item.models) ? item.models : {};
      const normalizedModels = {
        "Random Forest": normalizeModel(models["Random Forest"]),
        "XGBoost": normalizeModel(models.XGBoost)
      };
      const available = Object.values(normalizedModels).some(model => model.status === "available");
      return {
        ...item,
        metric: key,
        unit,
        status: available ? "available" : (item.status === "available" ? "unavailable" : (item.status || "unavailable")),
        current: isRecord(item.current) && finiteNumber(item.current.value) !== null && validTimestamp(item.current.timestamp)
          ? { value: finiteNumber(item.current.value), timestamp: item.current.timestamp } : null,
        predicted: null,
        models: normalizedModels,
        historical_points: normalizePoints(item.historical_points),
        forecast_points: Object.entries(normalizedModels).flatMap(([model, output]) =>
          output.forecast_points.map(point => ({ ...point, model })))
      };
    };
    const modelStatus = Array.isArray(envelope.model_status) ? envelope.model_status : [];
    return {
      ...envelope,
      target_at: target,
      horizon_hours: Number.isInteger(envelope.horizon_hours) ? envelope.horizon_hours : null,
      model_status: ["Random Forest", "XGBoost"].map(name => {
        const item = modelStatus.find(model => model?.name === name) || {};
        return { ...item, name, status: item.status || "unavailable" };
      }),
      parameters: {
        temperature_c: normalizeParameter("temperature_c", "°C"),
        tds_ppm: normalizeParameter("tds_ppm", "ppm"),
        turbidity_ntu: normalizeParameter("turbidity_ntu", "NTU")
      }
    };
  }

  async function request(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        signal: controller.signal
      });
      if (!response.ok) throw new Error(`API returned HTTP ${response.status}`);
      return await response.json();
    } finally {
      clearTimeout(timeout);
    }
  }

  async function status() {
    const payload = await request("/status");
    return isRecord(payload) ? payload : {};
  }

  async function overview() {
    const payload = await request("/overview");
    if (!isRecord(payload)) return {};
    const latest = normalizeReading({
      reading: payload.latest_reading,
      latest_valid_tds: payload.latest_valid_tds,
      tds_working_calibration_method: payload.tds_working_calibration_method
    }, payload.tds_working_calibration_method);
    return {
      ...payload,
      latest,
      latest_valid_tds: latest.tds?.valid ? latest.tds : null,
      live_reading: isRecord(payload.live_reading)
        ? normalizeReading(payload.live_reading, payload.tds_working_calibration_method)
        : null,
      averages: normalizeOverviewAverages(payload.averages, payload.tds_working_calibration_method)
    };
  }

  async function latest() {
    const payload = await request("/readings/latest");
    return normalizeReading(payload, payload?.tds_working_calibration_method);
  }

  async function devices() {
    const payload = await request("/devices");
    if (!isRecord(payload)) return {};
    const expectedMethod = payload.tds_working_calibration_method;
    return {
      ...payload,
      devices: Array.isArray(payload.devices) ? payload.devices.map(device => {
        if (!isRecord(device) || !isRecord(device.latest_reading)) return device;
        return {
          ...device,
          latest_reading: normalizeReading({
            reading: device.latest_reading,
            tds_working_calibration_method: expectedMethod
          }, expectedMethod)
        };
      }) : []
    };
  }

  window.LUWApi = {
    base: API_BASE,
    health: () => request("/health"),
    status,
    overview,
    latest,
    history: (range = "24h") =>
      request(`/readings/history?hours=${rangeHours(range)}&limit=500`).then(normalizeHistory),
    summary: (range = "24h") =>
      request(`/analytics-summary?hours=${rangeHours(range)}`).then(normalizeSummary),
    normalizeHistory,
    normalizeSummary,
    normalizeForecast,
    devices,
    forecast: (horizonHours = 1) => {
      const horizon = Number(horizonHours);
      if (!Number.isInteger(horizon) || horizon < 1 || horizon > 72) return Promise.reject(new Error("Forecast horizon must be between 1 and 72 hours."));
      return request(`/forecasts?horizon_hours=${horizon}`).then(normalizeForecast);
    },
    pull: () => request("/readings/pull", { method: "POST" })
  };
})();
