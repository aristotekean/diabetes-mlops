-- Ground truth for inference requests: the observed readmission outcome that
-- turns an inference.logs row into a supervised training example. Safe to
-- re-run.

-- One label per request. A corrected outcome replaces the previous one (the
-- API upserts), so a request never carries two contradicting labels.
CREATE TABLE IF NOT EXISTS inference.feedback (
    request_id UUID PRIMARY KEY REFERENCES inference.logs (request_id),
    readmitted TEXT NOT NULL CHECK (readmitted IN ('NO', '>30', '<30')),
    source TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_inference_feedback_recorded_at
    ON inference.feedback (recorded_at DESC);

-- Retraining input: every successful prediction that already has its label.
-- ``features`` and ``readmitted`` mirror clean.records (features, target_readmitted)
-- so the training pipeline can union both sources.
CREATE OR REPLACE VIEW inference.training_examples AS
SELECT
    l.request_id,
    l.requested_at,
    l.input_data AS features,
    l.prediction #>> '{}' AS predicted_readmitted,
    f.readmitted,
    f.source,
    f.recorded_at,
    l.model_name,
    l.model_version,
    l.model_alias
FROM inference.logs AS l
JOIN inference.feedback AS f ON f.request_id = l.request_id
WHERE l.status = 'succeeded';
