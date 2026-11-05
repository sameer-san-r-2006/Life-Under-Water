-- Keep the legacy estimate column for values already written by prior firmware
-- or an earlier application of this migration. Never derive ppm from voltage
-- here; the conversion result belongs to the firmware payload.
ALTER TABLE sensor_readings
    ADD COLUMN IF NOT EXISTS tds_legacy_demo_estimate_ppm DOUBLE PRECISION;

COMMENT ON COLUMN sensor_readings.tds_legacy_demo_estimate_ppm IS
    'Preserved legacy firmware lookup estimate in ppm; provenance is retained separately.';
