-- Additive prediction history storage. Sensor readings and existing APIs are unchanged.
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

COMMENT ON TABLE ml_predictions IS
    'Predictions for experimental sample categories; not a water-safety assessment.';

COMMENT ON COLUMN ml_predictions.cloudiness_index IS
    'Relative 0-100 cloudiness index; this is not NTU.';
