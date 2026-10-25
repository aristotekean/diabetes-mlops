# Data contract

This contract defines the PostgreSQL boundaries between dataset ingestion,
training, and inference. The initial source is the Diabetes 130-US hospitals
CSV linked by the workshop. Its observed header has 50 columns; `encounter_id`
is unique in the downloaded 101,766-row file, while `patient_nbr` is not.

## PostgreSQL schemas

The migration
[`001_create_data_contract.sql`](../infra/database/migrations/001_create_data_contract.sql)
creates three schemas in the training PostgreSQL database:

- `raw`: source batches and the original CSV records.
- `clean`: versioned, model-ready features, target, and data split.
- `inference`: one auditable event per inference request.

Apply it with a PostgreSQL client that can reach the training database:

```bash
psql "$TRAIN_DATABASE_URL" \
  -v ON_ERROR_STOP=1 \
  -f infra/database/migrations/001_create_data_contract.sql
```

`TRAIN_DATABASE_URL` must come from the deployment environment or a Kubernetes
Secret; credentials must not be committed to the repository. This migration is
safe to re-run and does not delete existing data.

## Source file and RAW ingestion

The source is a UTF-8 CSV. The download currently produces 101,766 rows and
these 50 columns:

```text
encounter_id, patient_nbr, race, gender, age, weight, admission_type_id,
discharge_disposition_id, admission_source_id, time_in_hospital, payer_code,
medical_specialty, num_lab_procedures, num_procedures, num_medications,
number_outpatient, number_emergency, number_inpatient, diag_1, diag_2, diag_3,
number_diagnoses, max_glu_serum, A1Cresult, metformin, repaglinide,
nateglinide, chlorpropamide, glimepiride, acetohexamide, glipizide,
glyburide, tolbutamide, pioglitazone, rosiglitazone, acarbose, miglitol,
troglitazone, tolazamide, examide, citoglipton, insulin,
glyburide-metformin, glipizide-metformin, glimepiride-pioglitazone,
metformin-rosiglitazone, metformin-pioglitazone, change, diabetesMed,
readmitted
```

`encounter_id` is the encounter key in the source, and `readmitted` is the
three-class target (`NO`, `>30`, `<30`). `patient_nbr` identifies repeated
patients and is not an encounter key. Raw CSV values are retained as strings
inside `raw.records.record_data`; tokens such as `?` and `None` are not
silently converted in RAW.

Each ingestion creates a row in `raw.load_batches` with:

- `batch_id`: UUID used to associate inserted records with the load.
- `source_file` and `source_uri`: original file name and configured source.
- `source_sha256`: SHA-256 of the complete downloaded file.
- `batch_number`: one-based, stable chunk number for that file checksum.
- `expected_record_count` and `loaded_record_count`: batch counts; a batch may
  contain at most 15,000 records.
- `status`, `started_at`, `completed_at`, and `error_message`: execution state.

Each CSV data record becomes a row in `raw.records`. `source_row_number` is the
one-based CSV data row number (the header is not a record);
`source_record_sha256` hashes that row's canonical source representation; and
`record_data` contains the complete, unmodified row keyed by the CSV header.
`encounter_id` is also copied to its own text column for lookup.

The idempotency key is `(source_sha256, source_row_number)`, not the batch UUID.
The ingestor must create or resume the batch and commit its `loading` state,
then insert that batch's records and set it `completed` in one transaction. A
retry must reuse the same file checksum and batch number, insert with conflict
detection, verify the inserted/existing count, and only then mark the batch
`completed`. On failure, roll back the record transaction and set the batch to
`failed` in a separate transaction. Do not mark a partially committed batch
complete. A changed source file checksum is a new source snapshot and must be
tracked as such.

## CLEAN training records

Each successfully validated raw record can produce a row in
`clean.records`. The first transformation is `readmission-features-v1` and uses
the following 43 model input columns:

```text
race, gender, age, admission_type_id, admission_source_id, time_in_hospital,
payer_code, medical_specialty, num_lab_procedures, num_procedures,
num_medications, number_outpatient, number_emergency, number_inpatient,
diag_1, diag_2, diag_3, number_diagnoses, max_glu_serum, A1Cresult, metformin,
repaglinide, nateglinide, chlorpropamide, glimepiride, acetohexamide,
glipizide, glyburide, tolbutamide, pioglitazone, rosiglitazone, acarbose,
miglitol, troglitazone, tolazamide, insulin, glyburide-metformin,
glipizide-metformin, glimepiride-pioglitazone, metformin-rosiglitazone,
metformin-pioglitazone, change, diabetesMed
```

The eight numeric features are `time_in_hospital`, `num_lab_procedures`,
`num_procedures`, `num_medications`, `number_outpatient`, `number_emergency`,
`number_inpatient`, and `number_diagnoses`. Other listed features are
categorical strings, including numeric-looking source codes. Null tokens
`?`, `None`, and empty strings become null values in CLEAN. `weight` is omitted
because 96.86% of its values are missing in the downloaded dataset.
`examide` and `citoglipton` are omitted because each has only the value `No`.
`discharge_disposition_id` is omitted because disposition is determined after
the encounter and would leak future information into a readmission prediction.
`encounter_id` and `patient_nbr` are retained only as lineage/split metadata,
not as model features.

During model fitting, the shared pipeline imputes numeric nulls using the
training-partition median and scales numeric features. It imputes categorical
nulls with `__missing__` and one-hot encodes categories; unseen inference
categories are ignored by the encoder. These fitted transformations are part
of the model pipeline, so inference must use that complete pipeline.

- `raw_record_id`: lineage back to the source record.
- `transformation_version`: `readmission-features-v1` for the rules above.
- `features`: JSON object containing only model input features after cleaning
  and feature engineering.
- `target_readmitted`: source label, restricted to `NO`, `>30`, or `<30`.
- `data_split`: `train`, `validation`, or `test`.
- `processed_at`: processing timestamp.

The unique key `(raw_record_id, transformation_version)` makes processing
idempotent while allowing a new transformation version to coexist with the
previous one. CLEAN must not contain `encounter_id` or `patient_nbr` inside the
model features. The shared `build_clean_records` function yields batches of at
most 15,000 rows and emits `encounter_id` as temporary lineage metadata; the
Airflow/database writer must resolve that encounter to
`raw.records.raw_record_id` and insert the foreign key, without placing the
encounter ID in `features`. Splitting groups records by patient so one
patient's encounters cannot leak across train, validation, and test.

## Inference API and INFERENCE LOGS

The API accepts one observation as JSON. `features` contains exactly the 43
model input columns listed above: numeric fields are JSON numbers or `null`;
categorical fields are strings or `null`. `encounter_id`, `patient_nbr`,
`readmitted`, and the excluded fields are never accepted as features. The API
must validate names and types against the logged MLflow model signature; that
signature is authoritative, so the API does not hard-code a model version.

Request:

```json
{
  "request_id": "optional UUID; the API generates one if omitted",
  "features": {
    "race": "Caucasian",
    "gender": "Male",
    "age": "[70-80)",
    "admission_type_id": "1",
    "admission_source_id": "7",
    "time_in_hospital": 6,
    "payer_code": null,
    "medical_specialty": null,
    "num_lab_procedures": 13,
    "num_procedures": 3,
    "num_medications": 3,
    "number_outpatient": 0,
    "number_emergency": 0,
    "number_inpatient": 0,
    "diag_1": "530",
    "diag_2": "530",
    "diag_3": "787",
    "number_diagnoses": 9,
    "max_glu_serum": null,
    "A1Cresult": null,
    "metformin": "No",
    "repaglinide": "No",
    "nateglinide": "No",
    "chlorpropamide": "No",
    "glimepiride": "No",
    "acetohexamide": "No",
    "glipizide": "No",
    "glyburide": "No",
    "tolbutamide": "No",
    "pioglitazone": "No",
    "rosiglitazone": "No",
    "acarbose": "No",
    "miglitol": "No",
    "troglitazone": "No",
    "tolazamide": "No",
    "insulin": "No",
    "glyburide-metformin": "No",
    "glipizide-metformin": "No",
    "glimepiride-pioglitazone": "No",
    "metformin-rosiglitazone": "No",
    "metformin-pioglitazone": "No",
    "change": "No",
    "diabetesMed": "No"
  }
}
```

Successful response:

```json
{
  "request_id": "UUID",
  "prediction": "NO",
  "probabilities": {
    "NO": 0.8,
    ">30": 0.15,
    "<30": 0.05
  },
  "model": {
    "name": "diabetes",
    "version": "registered model version",
    "alias": "production"
  },
  "processing_time_ms": 12.5
}
```

`probabilities` is nullable if the model does not provide class
probabilities. The API persists one `inference.logs` row for every request,
including failed requests. `input_data` is the exact submitted feature object;
`prediction`, `probabilities`, model identity, and duration are populated on
success. `status`, `error_code`, and `error_message` describe failures. The
`request_id` is the idempotency and audit key and must be returned to the
caller. Do not store patient identifiers or other unnecessary identifying
information in inference inputs.

## Contract dependencies to settle during implementation

The shared training implementation is in
[`ml/diabetes_model`](../ml/diabetes_model). Its training input uses the 43
columns above, with eight numeric and 35 categorical features. The complete
preprocessing-plus-classifier pipeline should be logged to MLflow with a model
signature; API request validation and frontend fields must be checked against
that signature when the serving integration is implemented.

The Airflow DAG in
[`apps/airflow/dags/diabetes_training.py`](../apps/airflow/dags/diabetes_training.py)
implements the source validation, RAW batches, quality gate, CLEAN upserts,
training, model comparison, and production alias promotion. It expects the
PostgreSQL migration to be applied before the first run. The source file must be
available at `DIABETES_DATASET_PATH`; the development Compose mounts
`data/Diabetes`, while server/Kubernetes deployments must mount an equivalent
writable persistent volume and permit HTTPS egress to the source when the file
is absent. The task downloads the source into this mount if necessary.
`TRAIN_DB_*` (or `TRAIN_DATABASE_URL`) and `MLFLOW_TRACKING_URI` must be
supplied by the deployment.
Runs are scheduled weekly, do not backfill, and only one DAG run is active at a
time; individual tasks retry twice. RAW batches use deterministic UUIDv5 IDs
derived from the source checksum and batch number, while record uniqueness uses
checksum plus original data-row number. Each batch is committed atomically.
CLEAN is upserted by raw-record ID and transformation version in batches of at
most 15,000. A failed CLEAN batch rolls back without corrupting earlier
completed batches and can safely be retried.
MLflow runs carry an idempotency tag derived from the Airflow run ID, source
checksum, and transformation version. A retry reuses an already registered
model for that job rather than creating another version.

The fixed split seed is 42. The implementation searches deterministic
`GroupShuffleSplit` candidates to assign approximately 70% of rows to train,
15% to validation, and 15% to test, keeping every `patient_nbr` in exactly one
partition while preferring class distributions close to the full dataset.
Training fails if any partition lacks one of the three target classes or if
patients overlap across partitions. The classifier is class-balanced logistic
regression. Validation macro-F1 is the primary selection metric because it
gives equal weight to early, later, and no readmission despite the unequal
class frequencies. Per-class recall, macro-precision, macro-recall, and
balanced accuracy are also reported. Per-class recall metric names use the
MLflow-safe tokens `no_readmission`, `readmitted_after_30_days`, and
`readmitted_within_30_days`, corresponding to source labels `NO`, `>30`, and
`<30`. The test partition is evaluated separately and is not used to fit the
model.
The baseline logistic regression uses `C=0.1`, `tol=0.001`, and
`max_iter=1000`; this configuration converged on the full downloaded dataset.
On the checked-in workshop CSV, the baseline produced validation macro-F1
`0.4254` and test macro-F1 `0.4311`. These are reproducibility reference
metrics, not a claim of production readiness; future model candidates should
be compared against them using validation metrics before test evaluation.
