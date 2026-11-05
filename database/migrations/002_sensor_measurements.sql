-- Additive and safe to run repeatedly. Existing readings are retained.
-- Keep the deployed table aligned with the columns used by the reader and API.
ALTER TABLE sensor_readings
    ADD COLUMN IF NOT EXISTS temperature_c DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS temperature_valid BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS tds_raw_adc INTEGER,
    ADD COLUMN IF NOT EXISTS tds_voltage_v DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS tds_ppm DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS tds_valid BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS tds_calibration_status TEXT NOT NULL DEFAULT 'calibration_pending',
    ADD COLUMN IF NOT EXISTS tds_conversion_method TEXT,
    ADD COLUMN IF NOT EXISTS turbidity_raw_adc INTEGER,
    ADD COLUMN IF NOT EXISTS turbidity_voltage_v DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS turbidity_ntu DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS turbidity_valid BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS turbidity_calibration_status TEXT NOT NULL DEFAULT 'calibration_pending',
    ADD COLUMN IF NOT EXISTS cloudiness_index DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS cloudiness_valid BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN sensor_readings.cloudiness_index IS
    'Firmware relative cloudiness index on a 0–100 scale; this is not NTU.';
