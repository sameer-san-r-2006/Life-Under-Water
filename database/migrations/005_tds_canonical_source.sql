-- Promote existing firmware-produced TDS values to the canonical ppm field.
-- This is additive, repeatable, and does not recalculate any conversion.
ALTER TABLE sensor_readings
    ADD COLUMN IF NOT EXISTS tds_source TEXT NOT NULL DEFAULT 'unavailable';

COMMENT ON COLUMN sensor_readings.tds_ppm IS
    'Canonical numeric TDS ppm produced by the firmware conversion.';

COMMENT ON COLUMN sensor_readings.tds_source IS
    'Provenance for canonical TDS ppm: calibrated, demo_estimate, or unavailable.';

COMMENT ON COLUMN sensor_readings.tds_legacy_demo_estimate_ppm IS
    'Preserved copy of the legacy firmware lookup estimate for provenance and migration history.';

-- Keep provenance separate while making the already-computed estimate available
-- through tds_ppm. The voltage range matches the existing firmware lookup.
UPDATE sensor_readings
SET tds_ppm = COALESCE(tds_ppm, tds_legacy_demo_estimate_ppm),
    tds_valid = TRUE,
    tds_source = 'demo_estimate'
WHERE tds_conversion_method = 'legacy_demo_lookup_v1'
  AND tds_calibration_status = 'estimated'
  AND (tds_voltage_v IS NULL OR tds_voltage_v BETWEEN 0.200 AND 0.450)
  AND COALESCE(tds_ppm, tds_legacy_demo_estimate_ppm) IS NOT NULL
  AND COALESCE(tds_ppm, tds_legacy_demo_estimate_ppm) >= 0
  AND COALESCE(tds_ppm, tds_legacy_demo_estimate_ppm) < 'Infinity'::DOUBLE PRECISION
  AND COALESCE(tds_ppm, tds_legacy_demo_estimate_ppm) <> 'NaN'::DOUBLE PRECISION;

-- Older calibrated rows can have a canonical value and method but a defaulted
-- validity flag. Restore validity from that explicit provenance and numeric value.
UPDATE sensor_readings
SET tds_valid = TRUE,
    tds_source = 'calibrated'
WHERE tds_calibration_status = 'calibrated'
  AND COALESCE(tds_conversion_method, '') <> 'legacy_demo_lookup_v1'
  AND tds_ppm IS NOT NULL
  AND tds_ppm >= 0
  AND tds_ppm < 'Infinity'::DOUBLE PRECISION
  AND tds_ppm <> 'NaN'::DOUBLE PRECISION;

-- Label invalid or unavailable rows without changing their readings or validity.
UPDATE sensor_readings
SET tds_source = CASE
    WHEN tds_conversion_method = 'legacy_demo_lookup_v1' THEN 'demo_estimate'
    ELSE 'unavailable'
END
WHERE tds_source = 'unavailable';
