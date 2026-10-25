-- Incremental pipeline: per-row RAW validation, CLEAN exclusions, split
-- assignment after storage, and a staging table between preprocessing and
-- the CLEAN merge. Safe to re-run.

ALTER TABLE raw.load_batches ADD COLUMN IF NOT EXISTS dag_run_id TEXT;

ALTER TABLE raw.records ADD COLUMN IF NOT EXISTS status_reason TEXT;
ALTER TABLE raw.records ADD COLUMN IF NOT EXISTS validated_at TIMESTAMPTZ;
ALTER TABLE raw.records DROP CONSTRAINT IF EXISTS ck_raw_records_rejection_reason;
ALTER TABLE raw.records ADD CONSTRAINT ck_raw_records_rejection_reason
    CHECK (status <> 'rejected' OR status_reason IS NOT NULL);
CREATE INDEX IF NOT EXISTS ix_raw_records_encounter_id
    ON raw.records (encounter_id);

-- A CLEAN row is either excluded from training (with a reason and no split)
-- or eligible; eligible rows get their split from the split task.
ALTER TABLE clean.records ADD COLUMN IF NOT EXISTS exclusion_reason TEXT;
ALTER TABLE clean.records ALTER COLUMN data_split DROP NOT NULL;
ALTER TABLE clean.records DROP CONSTRAINT IF EXISTS ck_clean_records_excluded_split;
ALTER TABLE clean.records ADD CONSTRAINT ck_clean_records_excluded_split
    CHECK (exclusion_reason IS NULL OR data_split IS NULL);

CREATE TABLE IF NOT EXISTS clean.records_staging (
    dag_run_id TEXT NOT NULL,
    raw_record_id BIGINT NOT NULL REFERENCES raw.records (raw_record_id),
    transformation_version TEXT NOT NULL,
    features JSONB NOT NULL CHECK (jsonb_typeof(features) = 'object'),
    target_readmitted TEXT NOT NULL
        CHECK (target_readmitted IN ('NO', '>30', '<30')),
    exclusion_reason TEXT,
    staged_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (dag_run_id, raw_record_id, transformation_version)
);
