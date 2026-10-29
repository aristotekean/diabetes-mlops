# api-inference

Servicio FastAPI que expone el modelo de readmisión diabética registrado en
MLflow (`models:/diabetes@production`), registra cada inferencia en la tabla
`inference.logs` y publica métricas Prometheus.

## Endpoints

| Método | Ruta            | Descripción |
| ------ | --------------- | ----------- |
| GET    | `/health`       | Liveness. Siempre `200`; informa `model_loaded` y el estado de la base de datos (`ok`/`error`). |
| GET    | `/model-info`   | Nombre, alias, versión, `run_id`, estado (`ready`/`unavailable`), `loaded_at`, última comprobación del alias y último error. |
| POST   | `/model/reload` | Fuerza la recarga de la versión apuntada por el alias. Devuelve `previous_version`, `current_version` y `changed`. `503` si la carga falla (se conserva la versión anterior). |
| POST   | `/predict`      | Una observación por petición. Contrato en `docs/data-contract.md`. |
| GET    | `/metrics`      | Exposición Prometheus (`text/plain; version=0.0.4`). |

### `POST /predict`

```json
{
  "request_id": "UUID opcional; se genera si falta",
  "features": { "race": "Caucasian", "time_in_hospital": 6, "...": "44 campos" }
}
```

- Los 44 campos del contrato son obligatorios (admiten `null`). Los ocho
  numéricos deben ser números JSON; el resto, cadenas (incluidos los códigos
  como `"admission_type_id": "1"`). Los tipos son estrictos: `"6"` en un campo
  numérico o `1` en un código se rechazan.
- `encounter_id`, `patient_nbr`, `readmitted`, `weight`, `examide`,
  `citoglipton` y cualquier clave desconocida producen `422`.
- El esquema se construye en tiempo de importación a partir de
  `diabetes_model.contract.INPUT_COLUMNS`/`NUMERIC_INPUTS`, de modo que la API
  y el pipeline de entrenamiento comparten la misma definición.

Respuesta `200`:

```json
{
  "request_id": "UUID",
  "prediction": "NO",
  "probabilities": { "NO": 0.8, ">30": 0.15, "<30": 0.05 },
  "model": { "name": "diabetes", "version": "7", "alias": "production" },
  "processing_time_ms": 4.2
}
```

Errores: `422` (`validation_error`), `503` (`model_unavailable`), `500`
(`inference_error`). El cuerpo incluye `request_id` y
`detail.error_code`/`detail.message`.

## Estrategia de carga del modelo

El modelo se carga con `mlflow.sklearn.load_model` (formato `skops`) para
disponer de `predict_proba` y `classes_`. La estrategia combina tres
mecanismos:

1. **Carga en el arranque.** El `lifespan` resuelve el alias con
   `MlflowClient().get_model_version_by_alias` y carga la versión concreta
   (`models:/<name>/<version>`). Hay reintentos acotados
   (`MODEL_LOAD_RETRIES`, backoff lineal `MODEL_LOAD_BACKOFF_SECONDS`). Si se
   agotan, el proceso arranca igualmente en estado `unavailable`: `/health`
   responde `200` con `model_loaded=false` y `/predict` responde `503` mientras
   tanto. El contenedor no entra en bucle de reinicio.
2. **Refresco en segundo plano.** Una tarea `asyncio` consulta el alias cada
   `MODEL_REFRESH_SECONDS` (60 por defecto; `0` la desactiva). Si la versión
   cambia, se carga la nueva y se sustituye la referencia bajo un
   `asyncio.Lock`; las peticiones en vuelo terminan con la versión que
   tomaron. Una versión nueva promovida por el DAG entra en servicio sin
   redeploy ni reinicio.
3. **Recarga bajo demanda.** `POST /model/reload` fuerza la carga aunque la
   versión no haya cambiado (útil tras una promoción manual o para validar el
   despliegue).

Las llamadas a MLflow y a scikit-learn son bloqueantes; todas se ejecutan en
`asyncio.to_thread` para no detener el event loop.

## Registro en `inference.logs`

Se persiste una fila por petición a `/predict`, incluidas las fallidas, en la
base de datos de entrenamiento (`postgres-train`, esquema creado por
`infra/database/migrations/001_create_data_contract.sql`):

- `input_data`: el objeto `features` tal como se envió (en errores de parseo
  JSON se guarda `{"raw": "<cuerpo truncado>"}`).
- `prediction` (cadena JSON), `probabilities`, `model_name`, `model_version`,
  `model_alias` y `processing_time_ms` en éxito.
- `status = failed` con `error_code` ∈ {`validation_error`,
  `model_unavailable`, `inference_error`} y `error_message` en fallo.

`processing_time_ms` mide normalización más inferencia; no incluye la
escritura en base de datos.

**Decisión:** la escritura se espera dentro de la petición, pero si falla
(base de datos caída, `request_id` duplicado, etc.) se registra la excepción,
se incrementa `inference_log_failures_total` y la predicción se devuelve igual.
Se prioriza servir la inferencia sobre la trazabilidad estricta; la métrica
permite alertar sobre la pérdida de filas.

## Métricas

Middleware ASGI propio sobre `prometheus_client`, etiquetado por plantilla de
ruta (no por URL cruda) para mantener acotada la cardinalidad; las rutas no
reconocidas se agrupan bajo `path="unmatched"` y `/metrics` no se contabiliza.

| Métrica | Etiquetas | Significado |
| ------- | --------- | ----------- |
| `http_requests_total` | `method`, `path`, `status` | Peticiones HTTP. |
| `http_request_duration_seconds` | `method`, `path` | Histograma de latencia (buckets de 1 ms a 10 s). |
| `http_requests_in_progress` | — | Peticiones en curso. |
| `predictions_total` | `status` | Resultado de `/predict`: `succeeded`, `validation_error`, `model_unavailable`, `inference_error`. |
| `prediction_duration_seconds` | — | Latencia de normalización + inferencia. |
| `inference_log_failures_total` | — | Filas de `inference.logs` no escritas. |
| `model_info` | `name`, `version`, `alias` | Vale 1 para la versión servida; se reinicia en cada recarga. |
| `model_reloads_total` | `result` | Intentos de carga (`success`/`failure`). |

Además se exportan los colectores por defecto (`process_*` en Linux,
`python_info`).

## Configuración

| Variable | Por defecto | Descripción |
| -------- | ----------- | ----------- |
| `MLFLOW_TRACKING_URI` | — | Servidor MLflow (el cliente también la lee de forma nativa). |
| `MODEL_NAME` / `MODEL_ALIAS` | `diabetes` / `production` | Modelo registrado y alias servido. |
| `MODEL_REFRESH_SECONDS` | `60` | Periodo del refresco en segundo plano; `0` lo desactiva. |
| `MODEL_LOAD_RETRIES` / `MODEL_LOAD_BACKOFF_SECONDS` | `5` / `2` | Reintentos de la carga inicial. |
| `TRAIN_DATABASE_URL` | — | DSN completo; tiene prioridad sobre las partes. |
| `TRAIN_DB_HOST` / `TRAIN_DB_PORT` / `TRAIN_DB_NAME` / `TRAIN_DB_USER` / `TRAIN_DB_PASSWORD` | `—/5432/—/—/—` | Partes del DSN de `inference.logs`. |
| `DB_POOL_MIN` / `DB_POOL_MAX` | `1` / `4` | Tamaño del pool async de psycopg. |
| `LOG_LEVEL` | `INFO` | Nivel de logging estándar. |

## Desarrollo local

```bash
cd apps/api-inference
uv sync
uv run pytest -q
TRAIN_DATABASE_URL=postgresql://app:app@localhost:5432/diabetes \
MLFLOW_TRACKING_URI=http://localhost:5000 \
uv run uvicorn api_inference.main:app --reload
```

Las pruebas no necesitan red, MLflow ni PostgreSQL: inyectan un registro falso,
un modelo falso y un repositorio en memoria a través de `create_app(...)`.

## Docker y compose

El `Dockerfile` usa la raíz del repositorio como contexto para copiar el
paquete compartido `ml/diabetes_model` (dependencia de ruta en `uv.lock`). En
`docker-compose.yml` el servicio `api-inference` depende de `mlflow`,
`postgres-train` (healthcheck) y `train-db-migrate` (migraciones aplicadas).

```bash
docker compose build api-inference
docker compose up -d api-inference
```

## Versión de Python

El servicio se ejecuta sobre CPython 3.14. Se intentó 3.15, pero
`pyarrow` (dependencia de MLflow) y `pandas 2.3.x` (fijado `<3` por
`diabetes_model`) no publican ruedas `cp315` y la compilación desde fuente de
`pyarrow` falla; 3.14 instala todo desde ruedas, incluido
`scikit-learn==1.9.1`, la misma versión con la que se entrena el modelo.
