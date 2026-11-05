CREATE TABLE IF NOT EXISTS devices (
    id BIGSERIAL PRIMARY KEY,
    device_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    firmware_version TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sensor_readings (
    id BIGSERIAL PRIMARY KEY,
    device_id BIGINT REFERENCES devices(id),
    recorded_at TIMESTAMPTZ NOT NULL,
    temperature_c DOUBLE PRECISION,
    tds_raw_adc INTEGER,
    tds_voltage_v DOUBLE PRECISION,
    tds_ppm DOUBLE PRECISION,
    tds_calibration_status TEXT NOT NULL DEFAULT 'calibration_pending',
    tds_conversion_method TEXT,
    tds_source TEXT NOT NULL DEFAULT 'unavailable',
    tds_legacy_demo_estimate_ppm DOUBLE PRECISION,
    turbidity_raw_adc INTEGER,
    turbidity_voltage_v DOUBLE PRECISION,
    turbidity_ntu DOUBLE PRECISION,
    turbidity_calibration_status TEXT NOT NULL DEFAULT 'calibration_pending',
    cloudiness_index DOUBLE PRECISION,
    cloudiness_valid BOOLEAN NOT NULL DEFAULT FALSE,
    temperature_valid BOOLEAN NOT NULL DEFAULT FALSE,
    tds_valid BOOLEAN NOT NULL DEFAULT FALSE,
    turbidity_valid BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sensor_readings_recorded_at
    ON sensor_readings(recorded_at DESC);

CREATE INDEX IF NOT EXISTS idx_sensor_readings_device_time
    ON sensor_readings(device_id, recorded_at DESC);

CREATE TABLE IF NOT EXISTS ml_predictions (
    id BIGSERIAL PRIMARY KEY,
    reading_id BIGINT NOT NULL REFERENCES sensor_readings(id),
    predicted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sensor_recorded_at TIMESTAMPTZ NOT NULL,
    tds_ppm DOUBLE PRECISION NOT NULL CHECK (tds_ppm >= 0),
    temperature_c DOUBLE PRECISION NOT NULL CHECK (temperature_c BETWEEN -55 AND 125),
    cloudiness_index DOUBLE PRECISION NOT NULL CHECK (cloudiness_index BETWEEN 0 AND 100),
    predicted_category TEXT NOT NULL CHECK (
        predicted_category IN ('Household', 'Agricultural', 'Aquatic')
    ),
    model_name TEXT NOT NULL CHECK (model_name IN ('Random Forest', 'XGBoost')),
    model_version TEXT NOT NULL,
    model_scores JSONB,
    CONSTRAINT uq_ml_predictions_reading_model_version
        UNIQUE (reading_id, model_name, model_version)
);

CREATE INDEX IF NOT EXISTS idx_ml_predictions_predicted_at
    ON ml_predictions(predicted_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS system_events (
    id BIGSERIAL PRIMARY KEY,
    device_id BIGINT REFERENCES devices(id),
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON COLUMN sensor_readings.tds_ppm IS
    'Canonical numeric TDS ppm produced by the firmware conversion.';

COMMENT ON COLUMN sensor_readings.tds_source IS
    'Provenance for canonical TDS ppm: calibrated, demo_estimate, or unavailable.';

COMMENT ON COLUMN sensor_readings.tds_legacy_demo_estimate_ppm IS
    'Preserved copy of the legacy firmware lookup estimate for provenance and migration history.';
