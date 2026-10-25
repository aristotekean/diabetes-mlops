# Data contract

This contract defines the PostgreSQL boundaries between dataset ingestion,
training, and inference. The source is the Diabetes 130-US hospitals CSV
linked by the workshop: 101,766 rows and 50 columns. `encounter_id` is unique
in the file; `patient_nbr` is not (71,518 patients).

The analysis behind every decision below is in
[`notebooks/01_eda.ipynb`](../notebooks/01_eda.ipynb). The rules are implemented
once, in the shared package [`ml/diabetes_model`](../ml/diabetes_model), and
reused by the Airflow DAG and, later, the inference API.

## Modeling decisions

- **Target:** `readmitted`, three classes: `NO`, `>30` (readmitted after 30
  days), `<30` (readmitted within 30 days). The prediction is made at
  discharge.
- **Primary selection metric:** validation **macro-F1**. Classes are
  imbalanced (`NO` 53.9%, `>30` 34.9%, `<30` 11.2%), so accuracy rewards
  ignoring the minority classes; macro-F1 weights the three classes equally.
  Per-class recall (especially `<30`), per-class F1, macro precision/recall,
  balanced accuracy, ROC-AUC one-vs-rest, and log loss are logged as
  supporting metrics. Every candidate uses balanced class weights.
- **Production alias:** `production` on the registered model `diabetes`.

## PostgreSQL schemas and migrations

The migrations in [`infra/database/migrations`](../infra/database/migrations)
create three schemas in the training database:

- `raw`: source batches and the original CSV records, with their validation
  status.
- `clean`: versioned, model-ready features, label, exclusion reason, and split.
- `inference`: one auditable event per inference request.

[`infra/database/migrate.sh`](../infra/database/migrate.sh) applies pending
files in name order, each in its own transaction, and records them in
`public.schema_migrations`. Docker Compose runs it as the one-shot
`train-db-migrate` service before Airflow starts; it uses the standard libpq
variables (`PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`), so the same script
can run as a Kubernetes Job. Every migration is also written to be safe to
re-run. Credentials come from the deployment environment or a Kubernetes
Secret and must not be committed.

## RAW layer

Each DAG run loads **one** batch of at most 15,000 rows: the first batch of the
current source snapshot that is not `completed`. The seven batches of the
workshop file therefore arrive over seven runs, simulating incremental data.

`raw.load_batches` (one row per batch):

- `batch_id`: deterministic UUIDv5 of the file checksum and batch number.
- `source_file`, `source_uri`, `source_sha256`: origin of the data. A changed
  file checksum is a new source snapshot.
- `batch_number`, `expected_record_count`, `loaded_record_count`.
- `status` (`pending`, `loading`, `completed`, `failed`), `started_at`,
  `completed_at`, `error_message`, and `dag_run_id`.

`raw.records` (one row per CSV data row):

- `raw_record_id`: surrogate key used as CLEAN lineage.
- `batch_id`, `source_sha256`, `source_row_number` (one-based data row), and
  `source_record_sha256` (row hash).
- `encounter_id` and `record_data`: the complete, unmodified row as strings.
- `status`: `loaded` on insert, then `validated` or `rejected` by the quality
  task; `status_reason` lists the violated rules; `validated_at`.

Idempotency: `(source_sha256, source_row_number)` is unique. A batch commits
its `loading` state, then inserts its records and marks itself `completed` in a
single transaction; on failure the records roll back and the batch is set to
`failed` in a separate transaction. The next run resumes that batch before
loading a new one.

### RAW quality rules

`validate_source_record` checks every `loaded` record: all 50 columns present;
`encounter_id` and `patient_nbr` are digits; `readmitted` is a known class; the
eight numeric fields are non-negative integers and `time_in_hospital` is 1-14;
the three admission/discharge codes are digits; `age`, `gender`, lab results,
medications, `change`, and `diabetesMed` belong to their known domains. An
encounter already validated earlier is rejected as `duplicate_encounter_id`.
The DAG fails when any batch has more than 1% rejected rows
(`RAW_MAX_REJECTED_FRACTION`); because statuses persist, the gate keeps
failing until the data is investigated. The workshop file has no rejections.

## CLEAN layer (`readmission-features-v2`)

Validated RAW records not yet in CLEAN for the current transformation version
are transformed in chunks of at most 15,000 into `clean.records_staging`, then
merged atomically into `clean.records` and removed from staging.

`clean.records`:

- `raw_record_id` (lineage) and `transformation_version`; the pair is unique,
  so reprocessing is idempotent and a future version can coexist.
- `features`: JSON object with the 37 model features below.
- `target_readmitted`: `NO`, `>30`, or `<30`.
- `exclusion_reason`: set for encounters that cannot be used for training;
  such rows never get a split.
- `data_split`: `train`, `validation`, or `test`, assigned by the split task.
- `processed_at`.

### Excluded encounters

Discharge dispositions 11, 19, 20, 21 (expired) and 13, 14 (hospice) are kept
in CLEAN with `exclusion_reason = 'discharged_expired_or_hospice'` and are not
used for training: a readmission cannot be observed, and every expired
encounter is labelled `NO`. This removes 2,423 encounters (99,343 remain).

### Inputs and features

The **input contract** has 44 fields: the 50 source columns minus
`encounter_id`, `patient_nbr`, `readmitted`, `weight` (96.9% missing), and
`examide` / `citoglipton` (constant). Missing tokens `?` and empty strings
become null. `None` in `max_glu_serum` and `A1Cresult` means "test not
performed" and stays as its own category.

`engineer_features` turns those inputs into **37 model features**:

| Feature | Rule |
|---|---|
| 8 numeric inputs | `time_in_hospital`, `num_lab_procedures`, `num_procedures`, `num_medications`, `number_outpatient`, `number_emergency`, `number_inpatient`, `number_diagnoses` |
| `age_years` | midpoint of the age bucket (5 … 95) |
| `total_visits` | outpatient + emergency + inpatient visits in the previous year |
| `num_active_medications` | medications (of 21) not equal to `No` |
| `num_medication_changes` | medications with dosage `Up` or `Down` |
| `race`, `payer_code` | unchanged categories |
| `gender` | `Unknown/Invalid` becomes null |
| `medical_specialty_group` | 72 specialties grouped (internal medicine, emergency, family practice, cardiology, surgery, orthopedics, radiology, …, other) |
| `admission_type` | emergency, urgent, elective, other, unknown |
| `admission_source` | referral, emergency_room, transfer, other, unknown |
| `discharge_group` | home, home_health, facility_transfer, left_against_advice, other, unknown |
| `diag_1_group`, `diag_2_group`, `diag_3_group` | ICD-9 chapter (Strack et al., 2014): circulatory, respiratory, digestive, diabetes (250.xx), injury, musculoskeletal, genitourinary, neoplasms, other |
| `max_glu_serum`, `A1Cresult` | categories, null becomes `None` (not measured) |
| 11 medications | `metformin`, `repaglinide`, `nateglinide`, `glimepiride`, `glipizide`, `glyburide`, `pioglitazone`, `rosiglitazone`, `acarbose`, `insulin`, `glyburide-metformin` |
| `change`, `diabetesMed` | categories |

The other 10 medications are used by fewer than 0.1% of encounters and only
feed the two medication counts. Fitted transformations happen inside the model
pipeline, using the training partition only: numeric median imputation (plus
standard scaling for logistic regression), categorical nulls as
`__missing__`, and one-hot encoding (ordinal encoding with native categorical
support for gradient boosting). Unseen categories are ignored.

### Splits

`assign_split` hashes `patient_nbr` (SHA-256 with the salt
`diabetes-patient-split-v1`): 70% train, 15% validation, 15% test. The split
depends only on the patient, so all encounters of a patient share a partition
and earlier assignments never change when new batches arrive. The split task
fails if any patient spans two partitions. On the full file the partitions hold
69,586 / 14,986 / 14,771 rows with near-identical class distributions.

## Training, registration, and promotion

Every run with new CLEAN data trains three candidates on all accumulated
training rows: class-balanced logistic regression, histogram gradient
boosting, and random forest. Each candidate is a nested MLflow run under a
parent run named `training-<dag_run_id>` and logs parameters, train /
validation / test metrics, fit time, normalized confusion matrices,
classification reports, the CLEAN snapshot, and the model.

The logged model is the **serving pipeline**: the stateless `FeatureEngineer`
step followed by the fitted preprocessing and classifier. It accepts the 44
input fields, has an explicit signature (numeric fields `double`, the rest
`string`), bundles the `diabetes_model` package through `code_paths`, and is
serialized with skops, trusting only sklearn, numpy, scipy, and
`diabetes_model` types.

The candidate with the best validation macro-F1 is registered as a new version
of `diabetes`, tagged with its CLEAN snapshot fingerprint so a retry or a run
without new data never registers the same data twice. The registered version
and the current `production` version are then loaded from the registry and
scored on the same validation rows, rebuilt from RAW exactly as the API will
send them. The new version must reproduce its training-time score (a
training/serving skew check), and it takes the `production` alias only if its
macro-F1 is higher than the champion's (`PROMOTION_MIN_IMPROVEMENT`, default
0). The test partition is reported, never used to select.

When a run brings no new CLEAN data, `check_training_needed` skips the training
branch.

## Inference API and INFERENCE LOGS

The API accepts one observation as JSON. `features` contains the 44 input
fields: the eight numeric fields are JSON numbers or `null`; the other fields
are strings or `null`, including numeric-looking codes. Identifiers, the target,
and the excluded columns are never accepted. The API loads
`models:/diabetes@production`, whose signature is authoritative.

Request:

```json
{
  "request_id": "optional UUID; the API generates one if omitted",
  "features": {
    "race": "Caucasian",
    "gender": "Male",
    "age": "[70-80)",
    "admission_type_id": "1",
    "discharge_disposition_id": "1",
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
    "max_glu_serum": "None",
    "A1Cresult": "None",
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
caller. Do not store patient identifiers in inference inputs.
