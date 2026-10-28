# Feature: inference-api

Branch: `feat/inference-api` (from `main` @ c307ea9). Locator: `odd/tasks/inference-api.md`. Engram mirror: `odd/inference-api/tasks`.

## Objective
Implement the FastAPI inference service in `apps/api-inference` that serves the
production diabetes readmission model from MLflow, logs every request to
`inference.logs`, and exposes Prometheus metrics.

## Problem / why
`apps/api-inference` is a bare scaffold (`/health` only). The course phase
requires `/health`, `/predict`, `/model-info`, `/metrics`, mandatory DB logging
of every inference, and a controlled model-loading strategy that picks up a new
production version without code changes.

## Scope
- In: API code, tests, dependency pins, Dockerfile/compose env needed by the API,
  API README (Spanish, documents the loading strategy).
- Out: Streamlit form, Prometheus/Grafana services and dashboards, k8s manifests,
  locustfile update.

## Design decisions (accepted)
- Model: `mlflow.sklearn.load_model("models:/diabetes@production")` so
  `predict_proba`/`classes_` are available. Loader strategy = **load at startup +
  background refresh task polling the alias version every `MODEL_REFRESH_SECONDS`
  (default 60) + `POST /model/reload` on demand**. Swap is atomic (replace a
  reference under an `asyncio.Lock`); inference runs in a thread
  (`asyncio.to_thread`) so the event loop stays free.
- Input normalisation reuses `diabetes_model.contract.input_frame` (path dependency
  on `ml/diabetes_model`); feature engineering lives inside the served pipeline.
- DB: `inference.logs` on `postgres-train` (db `diabetes`) via async psycopg3 pool.
  One row per request, including validation/model failures. Write is awaited in the
  request path; DB failure is counted and logged, prediction still returned.
- Metrics: hand-rolled ASGI middleware with `prometheus_client`
  (`http_requests_total`, `http_request_duration_seconds`, `predictions_total`,
  `model_info`, inference log failures) + default process collector (CPU/RSS).
- Settings: `pydantic-settings` (`MLFLOW_*`, `MODEL_NAME`, `MODEL_ALIAS`,
  `MODEL_REFRESH_SECONDS`, `TRAIN_DATABASE_URL` or `TRAIN_DB_*`).
- Python: stay on 3.15 if `scikit-learn==1.9.1`, pandas, numpy, psycopg[binary]
  ship cp315 wheels; otherwise fall back to the newest CPython that does and
  update `.python-version`, `pyproject`, Dockerfile images (record the reason).

## Tasks
- [x] T1 — deps + settings + model loader (`model.py`), `/health`, `/model-info`,
  `POST /model/reload`; lifespan with startup load and background refresh. Tests
  with a fake registry/loader. Route: delegated (writer trigger, 2+ files).
- [ ] T2 — `/predict`: pydantic schemas (44 fields, identifiers rejected),
  `asyncio.to_thread` inference, `inference.logs` repository (async pool), row for
  success and failure, `request_id` echo. Tests with fake repository. Route: delegated.
- [ ] T3 — metrics middleware + `/metrics`; counters/histogram/gauge; tests. Route: delegated.
- [ ] T4 — compose wiring for the API (train DB env, `depends_on` postgres-train /
  train-db-migrate), Dockerfile install of `ml/diabetes_model`, API README (es)
  explaining the loading strategy. Route: delegated (same writer).

## Verification
- `uv lock --check` and `uv run pytest -q` in `apps/api-inference`.
- `docker compose config -q` at repo root.
- `docker compose build api-inference` (image builds).
- Test-first: RED observed before each endpoint is implemented.

## Delivery
Strategy: `ask-on-risk`. Forecast ~700 authored changed lines (excluding `uv.lock`).

## Progress / evidence

### Python version
Python 3.15 was tried first (`uv lock` resolved) but `uv sync` had to build
`pyarrow==25.0.1` (pulled by mlflow) and `pandas==2.3.3` (pinned `<3` by
`ml/diabetes_model`) from source; the pyarrow CMake build failed. Per the design
decision the app moved to **CPython 3.14** (`.python-version` = `3.14`,
`requires-python >=3.14`, Dockerfile images `uv:python3.14-trixie-slim` /
`python:3.14-slim-trixie`). On 3.14.7 every dependency installs from wheels
(sklearn 1.9.1, numpy 2.5.4, pandas 2.3.3, mlflow 3.16.1, skops 0.16.0,
psycopg 3.3.6, pyarrow 25.0.1).

### T1 — model service + ops routes (route: delegated writer)
- Re-slice: `repository.py` (protocol + `PostgresInferenceLogRepository`) landed
  in T1 instead of T2 because the lifespan/`/health` already need the pool; T2
  wires `/predict` to it.
- RED: `uv run pytest -q` → `ImportError: cannot import name 'create_app'`.
- GREEN: `uv run pytest -q` → `11 passed`.
- `uv lock --check` → `Resolved 106 packages` (lock up to date).
- `uv run python -c "import api_inference.main"` → import ok (DSN resolved lazily
  in `open()`, so import needs no DB env).
- Commit: see `git log` entry `feat(api-inference): add model service, settings
  and health/model-info/reload routes`.
