CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS clean;
CREATE SCHEMA IF NOT EXISTS inference;

CREATE TABLE IF NOT EXISTS raw.load_batches (
    batch_id UUID PRIMARY KEY,
    source_file TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    batch_number INTEGER NOT NULL CHECK (batch_number > 0),
    expected_record_count INTEGER NOT NULL
        CHECK (expected_record_count BETWEEN 1 AND 15000),
    loaded_record_count INTEGER NOT NULL DEFAULT 0
        CHECK (loaded_record_count BETWEEN 0 AND 15000),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'loading', 'completed', 'failed')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    error_message TEXT,
    CONSTRAINT uq_load_batches_source_batch
        UNIQUE (source_sha256, batch_number),
    CONSTRAINT ck_load_batches_completion
        CHECK (
            (status = 'completed' AND completed_at IS NOT NULL
                AND loaded_record_count = expected_record_count)
            OR (status <> 'completed' AND completed_at IS NULL)
        )
);

CREATE TABLE IF NOT EXISTS raw.records (
    raw_record_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id UUID NOT NULL REFERENCES raw.load_batches (batch_id),
    source_sha256 CHAR(64) NOT NULL,
    source_row_number BIGINT NOT NULL CHECK (source_row_number > 0),
    source_record_sha256 CHAR(64) NOT NULL,
    encounter_id TEXT NOT NULL,
    record_data JSONB NOT NULL CHECK (jsonb_typeof(record_data) = 'object'),
    status TEXT NOT NULL DEFAULT 'loaded'
        CHECK (status IN ('loaded', 'validated', 'rejected')),
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_raw_source_row UNIQUE (source_sha256, source_row_number)
);

CREATE INDEX IF NOT EXISTS ix_raw_records_batch_id
    ON raw.records (batch_id);
CREATE INDEX IF NOT EXISTS ix_raw_records_status
    ON raw.records (status);

CREATE TABLE IF NOT EXISTS clean.records (
    clean_record_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    raw_record_id BIGINT NOT NULL REFERENCES raw.records (raw_record_id),
    transformation_version TEXT NOT NULL,
    features JSONB NOT NULL CHECK (jsonb_typeof(features) = 'object'),
    target_readmitted TEXT NOT NULL
        CHECK (target_readmitted IN ('NO', '>30', '<30')),
    data_split TEXT NOT NULL
        CHECK (data_split IN ('train', 'validation', 'test')),
    processed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_clean_record_transform
        UNIQUE (raw_record_id, transformation_version)
);

CREATE INDEX IF NOT EXISTS ix_clean_records_split_version
    ON clean.records (transformation_version, data_split);

CREATE TABLE IF NOT EXISTS inference.logs (
    request_id UUID PRIMARY KEY,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    input_data JSONB NOT NULL CHECK (jsonb_typeof(input_data) = 'object'),
    prediction JSONB,
    probabilities JSONB
        CHECK (probabilities IS NULL OR jsonb_typeof(probabilities) = 'object'),
    model_name TEXT,
    model_version TEXT,
    model_alias TEXT,
    processing_time_ms DOUBLE PRECISION
        CHECK (processing_time_ms IS NULL OR processing_time_ms >= 0),
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
    error_code TEXT,
    error_message TEXT,
    CONSTRAINT ck_inference_log_result
        CHECK (
            (status = 'succeeded' AND prediction IS NOT NULL
                AND model_name IS NOT NULL AND model_version IS NOT NULL
                AND processing_time_ms IS NOT NULL AND error_code IS NULL)
            OR (status = 'failed' AND error_code IS NOT NULL)
        )
);

CREATE INDEX IF NOT EXISTS ix_inference_logs_requested_at
    ON inference.logs (requested_at DESC);
CREATE INDEX IF NOT EXISTS ix_inference_logs_model
    ON inference.logs (model_name, model_version);
