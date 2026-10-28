# Observabilidad · Server C (Prometheus + Grafana)

Stack de monitoreo de la API de inferencia. Prometheus recolecta las métricas
que la API expone en `/metrics` y Grafana las visualiza en el dashboard
**Diabetes · Inference API**.

```
api-inference:8000/metrics  ──scrape──▶  S8 Prometheus  ──query──▶  S9 Grafana
```

## Qué se mide

Este módulo **solo consume** las métricas: la API las expone en `/metrics`
(instrumentada con `prometheus-client`, componente de la API, fuera de esta
carpeta). El dashboard usa exactamente estas series:

**HTTP (middleware de la API)** — la ruta se identifica por la etiqueta `path`
(plantilla de ruta; las no reconocidas van como `unmatched`), y `status` es el
**código HTTP** (`200`, `404`, `503`, …):

| Métrica | Tipo | Etiquetas | Uso en el dashboard |
|---------|------|-----------|---------------------|
| `http_requests_total` | Counter | `method`, `path`, `status` | total, req/s, por endpoint, por estado, errores, tasa de error |
| `http_request_duration_seconds` | Histogram | `method`, `path` | latencia promedio y percentiles p50/p95/p99 |
| `http_requests_in_progress` | Gauge | — | solicitudes en curso |

**Dominio (MLOps)**:

| Métrica | Tipo | Etiquetas | Uso en el dashboard |
|---------|------|-----------|---------------------|
| `predictions_total` | Counter | `status` | predicciones por resultado (succeeded / validation_error / model_unavailable / inference_error) |
| `prediction_duration_seconds` | Histogram | — | latencia de inferencia p50/p95/p99 |
| `model_reloads_total` | Counter | `result` | recargas de modelo (success / failure) |
| `model_info` | Gauge | `name`, `version`, `alias` | modelo productivo servido (tabla) |
| `inference_log_failures_total` | Counter | — | fallos al escribir `inference.logs` |

**Proceso (colectores por defecto de `prometheus_client`)**:

| Métrica | Tipo | Uso en el dashboard |
|---------|------|---------------------|
| `process_cpu_seconds_total` | Counter | CPU del pod de la API |
| `process_resident_memory_bytes` | Gauge | memoria del pod de la API |

Los errores se cuentan como `status=~"4..\|5.."`.

## Paneles del dashboard

Cubre los mínimos exigidos por el enunciado y añade observabilidad de dominio:

**Tráfico HTTP** — solicitudes totales · req/s · latencia promedio · p95 (KPIs);
serie p50/p95/p99; solicitudes por código de estado; por endpoint; nº de errores
y tasa de error (KPI y en el tiempo).
**Recursos del pod** — CPU y memoria de la API.
**MLOps** — predicciones por resultado; latencia de predicción p50/p95/p99;
recargas de modelo; solicitudes en curso; fallos de log; tabla del modelo servido.

## Uso local (independiente)

El monitoreo es un stack autónomo (Server C); se levanta por su cuenta desde
esta carpeta, sin depender del resto:

```bash
cd infra/monitoring
docker compose -f docker-compose.yml up -d
```

| Servicio | URL | Credenciales |
|----------|-----|--------------|
| Prometheus | <http://localhost:9090> | — |
| Grafana | <http://localhost:3000> | `admin` / `admin` |

Grafana abre directamente en el dashboard **Diabetes · Inference API** (datasource
y dashboard se aprovisionan solos desde `grafana/provisioning`).

Prometheus hace scrape del target definido en
[`prometheus/targets/api-targets.yml`](prometheus/targets/api-targets.yml)
(`api-inference:8000`). Si la API no está en la misma red, el target aparece como
**DOWN** (Status → Targets) y los paneles muestran "No data".

### Conectar a la API (misma máquina)

La API corre en el compose raíz (`name: diabetes` → red `diabetes_default`). Para
que Prometheus la alcance **sin tocar el compose raíz**, el overlay
[`docker-compose.integration.yml`](docker-compose.integration.yml) une Prometheus
a esa red:

```bash
# 1) la API arriba (desde la raíz del repo; crea la red diabetes_default)
docker compose up -d

# 2) el monitoreo conectado a la red de la API
cd infra/monitoring
docker compose -f docker-compose.yml -f docker-compose.integration.yml up -d
```

Ahora el target `diabetes-api` pasa a **UP** y los paneles se llenan. `file_sd`
recarga el target sin reiniciar, así que para apuntar a otra dirección basta
editar `prometheus/targets/api-targets.yml`.

## Despliegue en un Server C dedicado (Tailscale)

En la arquitectura de la plataforma los tres servidores se conectan por una red
**Tailscale** (tailnet `aristotekean.github`). El monitoreo vive en **Server C**
y alcanza la API de Server A **a través de la tailnet**, no por la red local de
Docker.

El archivo [`docker-compose.tailscale.yml`](docker-compose.tailscale.yml) añade
un *sidecar* de Tailscale. Prometheus y Grafana comparten su namespace de red
(`network_mode: service:tailscale`), así que Server C aparece en la tailnet como
un único nodo (`monitoring`):

```
Server A (API) ─┐
                ├─ Tailscale ─► [ Server C: ts sidecar ⇄ Prometheus + Grafana ]
Server B ───────┘
```

Pasos en Server C:

```bash
cd infra/monitoring
cp .env.example .env            # y poner TS_AUTHKEY (admin de Tailscale → Keys)
# apuntar el scrape a Server A en la tailnet:
#   prometheus/targets/api-targets.yml  ->  - targets: ["server-a:8000"]
docker compose -f docker-compose.tailscale.yml up -d
```

Acceso desde cualquier equipo de la tailnet:

| Servicio | URL en la tailnet |
|----------|-------------------|
| Grafana | <http://monitoring:3000> (`admin` / `admin`) |
| Prometheus | <http://monitoring:9090> |

Los puertos también se publican en el host (en el sidecar) para depuración
local. El scrape target se cambia sin reiniciar: Prometheus recarga
`prometheus/targets/api-targets.yml` automáticamente (`file_sd`).

> Variables de Tailscale (en `infra/monitoring/.env`): `TS_AUTHKEY` (requerida),
> `TS_HOSTNAME` (nombre del nodo, default `monitoring`), `TS_EXTRA_ARGS`
> (p. ej. `--advertise-tags=tag:monitoring`).

## Variables de entorno (opcionales)

| Variable | Default | Descripción |
|----------|---------|-------------|
| `PROMETHEUS_PORT` | `9090` | puerto publicado de Prometheus |
| `PROMETHEUS_RETENTION` | `15d` | retención de la TSDB |
| `GRAFANA_PORT` | `3000` | puerto publicado de Grafana |
| `GRAFANA_ADMIN_USER` | `admin` | usuario admin de Grafana |
| `GRAFANA_ADMIN_PASSWORD` | `admin` | contraseña admin de Grafana |

## Estructura

```
infra/monitoring/
├── docker-compose.yml                    S8 Prometheus + S9 Grafana (independiente)
├── docker-compose.integration.yml        overlay: conecta Prometheus a la red de la API
├── docker-compose.tailscale.yml          Server C: sidecar Tailscale + S8 + S9
├── .env.example                          variables (TS_AUTHKEY, puertos, admin)
├── prometheus/
│   ├── prometheus.yml                    configuración de scrape (file_sd)
│   └── targets/
│       └── api-targets.yml               target de la API (editable por entorno)
└── grafana/
    ├── provisioning/
    │   ├── datasources/datasource.yml    Prometheus (uid: prometheus, URL por env)
    │   └── dashboards/dashboards.yml      provider de dashboards
    └── dashboards/
        └── diabetes-api.json              dashboard principal
```
