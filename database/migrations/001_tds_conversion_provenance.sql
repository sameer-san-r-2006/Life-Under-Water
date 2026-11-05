-- Additive and safe to run repeatedly. Existing readings are left unchanged;
-- rows without a conversion method are not eligible for calibrated analytics.
ALTER TABLE sensor_readings
    ADD COLUMN IF NOT EXISTS tds_conversion_method TEXT;

ALTER TABLE sensor_readings
    ADD COLUMN IF NOT EXISTS tds_legacy_demo_estimate_ppm DOUBLE PRECISION;

COMMENT ON COLUMN sensor_readings.tds_conversion_method IS
    'Firmware conversion method/version that produced tds_ppm; NULL means unknown.';

COMMENT ON COLUMN sensor_readings.tds_legacy_demo_estimate_ppm IS
    'Legacy, uncalibrated sketch lookup estimate in ppm; excluded from calibrated analytics.';
