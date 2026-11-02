# Observabilidad · Server C (Prometheus + Grafana)

Stack de monitoreo de la API de inferencia y de su almacén de artefactos.
Prometheus recolecta las métricas que la API expone en `/metrics` y las que
MinIO expone en `/minio/v2/metrics/cluster`; Grafana las visualiza en el
dashboard **Diabetes · Inference API**.

```
api-inference:8000/metrics                ──scrape──▶  S8 Prometheus  ──query──▶  S9 Grafana
minio:9000/minio/v2/metrics/cluster       ──scrape──▶       ┘
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

**MinIO (job `minio`, métricas nativas v2 del servidor)** — MinIO las publica
en su puerto S3 bajo `/minio/v2/metrics/cluster`; el servidor corre con
`MINIO_PROMETHEUS_AUTH_TYPE=public` (compose y k8s) para que Prometheus no
necesite un bearer token. Solo expone contadores de uso y tráfico, nunca
objetos ni credenciales:

| Métrica | Tipo | Etiquetas | Uso en el dashboard |
|---------|------|-----------|---------------------|
| `minio_cluster_health_status` | Gauge | `server` | estado (1 = OK) |
| `minio_cluster_bucket_total` | Gauge | `server` | nº de buckets |
| `minio_cluster_usage_object_total` | Gauge | `server` | objetos almacenados |
| `minio_cluster_usage_total_bytes` | Gauge | `server` | bytes almacenados |
| `minio_cluster_capacity_usable_{free,total}_bytes` | Gauge | `server` | % de capacidad usada |
| `minio_s3_requests_total` | Counter | `api`, `server` | solicitudes S3 por operación |
| `minio_s3_requests_errors_total` | Counter | `api`, `server` | errores S3 (total y por operación) |
| `minio_s3_traffic_{received,sent}_bytes` | Counter | `server` | tráfico S3 |
| `minio_s3_requests_ttfb_seconds_distribution` | Histogram | `api`, `le`, `server` | latencia TTFB p50/p95/p99 |
| `minio_node_process_cpu_total_seconds` | Counter | `server` | CPU de MinIO |
| `minio_node_process_resident_memory_bytes` | Gauge | `server` | memoria de MinIO |

Las series de buckets/objetos/bytes las calcula el scanner de MinIO, así que
aparecen unos minutos después del arranque; las de solicitudes aparecen con la
primera operación S3 (por ejemplo, al registrar un modelo en MLflow).

## Paneles del dashboard

Cubre los mínimos exigidos por el enunciado y añade observabilidad de dominio:

**Tráfico HTTP** — solicitudes totales · req/s · latencia promedio · p95 (KPIs);
serie p50/p95/p99; solicitudes por código de estado; por endpoint; nº de errores
y tasa de error (KPI y en el tiempo).
**Recursos del pod** — CPU y memoria de la API.
**MLOps** — predicciones por resultado; latencia de predicción p50/p95/p99;
recargas de modelo; solicitudes en curso; fallos de log; tabla del modelo servido.
**MinIO · almacén de artefactos (S3)** — estado · buckets · objetos · datos
almacenados · capacidad usada · errores S3 (KPIs); solicitudes y errores S3 por
operación; tráfico recibido/enviado; latencia TTFB p50/p95/p99; CPU y memoria
de MinIO.

## Uso local

El compose raíz incluye este stack bajo el perfil `monitoring`, así que un
`docker compose up` normal no lo arranca. Desde la raíz del repo:

```bash
docker compose up -d prometheus grafana        # solo monitoreo (nombrar el servicio activa su perfil)
docker compose --profile monitoring up -d      # stack completo + monitoreo
```

También es un stack autónomo (Server C): se levanta por su cuenta desde esta
carpeta, sin depender del resto. El perfil es obligatorio también aquí, sin él
compose no arranca nada, y el `.env` de la raíz se pasa con `--env-file` porque
el directorio del proyecto pasa a ser esta carpeta:

```bash
cd infra/monitoring
docker compose --env-file ../../.env -f docker-compose.yml --profile monitoring up -d
```

| Servicio | URL | Credenciales |
|----------|-----|--------------|
| Prometheus | <http://localhost:9090> | — |
| Grafana | <http://localhost:3000> | `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` del `.env` (`admin` / `admin` en `.env.example`) |

Grafana abre directamente en el dashboard **Diabetes · Inference API** (datasource
y dashboard se aprovisionan solos desde `grafana/provisioning`).

Prometheus hace scrape de los targets definidos en
[`prometheus/targets/api-targets.yml`](prometheus/targets/api-targets.yml)
(`api-inference.diabetes.svc.cluster.local:8000`) y
[`prometheus/targets/minio-targets.yml`](prometheus/targets/minio-targets.yml)
(`minio.diabetes.svc.cluster.local:9000`). Por defecto apuntan a la API y al
MinIO del clúster Kubernetes: OrbStack resuelve los Services del clúster desde
los contenedores. Con el stack de compose usar `api-inference:8000` y
`minio:9000`. Si la API o MinIO no son alcanzables, el target
aparece como **DOWN** (Status → Targets) y sus paneles muestran "No data".
Si MinIO responde `403`, al pod le falta `MINIO_PROMETHEUS_AUTH_TYPE=public`
(`kubectl apply -k infra/k8s` o `kubectl -n diabetes set env deployment/minio
MINIO_PROMETHEUS_AUTH_TYPE=public`).

### Conectar a la API (misma máquina)

Desde la raíz del repo no hay nada que conectar: `docker compose up -d prometheus grafana`
ya comparte la red con la API. Si en cambio levantas el monitoreo desde esta
carpeta, la API corre en el compose raíz (`name: diabetes` → red `diabetes_default`)
y, para que Prometheus la alcance **sin tocar el compose raíz**, el overlay
[`docker-compose.integration.yml`](docker-compose.integration.yml) une Prometheus
a esa red:

```bash
# 1) la API arriba (desde la raíz del repo; crea la red diabetes_default)
docker compose up -d

# 2) el monitoreo conectado a la red de la API
cd infra/monitoring
docker compose --env-file ../../.env -f docker-compose.yml -f docker-compose.integration.yml --profile monitoring up -d
```

Ahora los targets `diabetes-api` y `minio` pasan a **UP** y los paneles se
llenan. `file_sd` recarga los targets sin reiniciar, así que para apuntar a otra
dirección basta editar `prometheus/targets/api-targets.yml` o
`prometheus/targets/minio-targets.yml`.

## Despliegue en un Server C dedicado

Cuando el monitoreo corre en una máquina aparte del clúster, apunta el scrape a
la dirección de la API en `prometheus/targets/api-targets.yml` y a la del puerto
S3 de MinIO en `prometheus/targets/minio-targets.yml` (host:puerto alcanzables
desde Server C) y levanta el stack con `docker-compose.yml`. Prometheus recarga
los targets sin reiniciar (`file_sd`). Con la API en Kubernetes (OrbStack), el
host resuelve los Services del clúster: `minio.diabetes.svc.cluster.local:9000`.

### Conectar Server C a la red Tailscale (opcional, instalado en el host)

En la arquitectura del equipo los tres servidores se conectan por una **tailnet**
de Tailscale. Esta es la forma **instalando Tailscale en el host** de Server C
(no requiere contenedor ni auth key; es un extra del equipo, no del taller):

1. **Instalar Tailscale** en Server C: <https://tailscale.com/download>.
2. **Iniciar sesión** (login en el navegador):
   ```bash
   sudo tailscale up
   ```
   Al autenticarte, Server C queda como un nodo de la tailnet.
3. **Verificar** que está conectado y ver las IPs/nombres de los nodos:
   ```bash
   tailscale status
   ```
4. **Apuntar Prometheus a la API por la tailnet** en
   `prometheus/targets/api-targets.yml`, usando la **IP de tailnet** de Server A
   (las IPs `100.x.y.z` se enrutan desde los contenedores a través del host):
   ```yaml
   - targets: ["100.x.y.z:8000"]   # IP de Server A en la tailnet
   ```
   > Si prefieres el **nombre** (`server-a:8000`) en vez de la IP, corre Prometheus
   > con `network_mode: host` para que use el MagicDNS del host.
5. **Levantar el monitoreo** normal:
   ```bash
   docker compose --env-file ../../.env -f docker-compose.yml --profile monitoring up -d
   ```

Así Server C alcanza la API de Server A **de forma privada por la tailnet**, sin
abrir puertos públicos y sin cambiar el stack de monitoreo.

## Variables de entorno

Se definen en el `.env` de la raíz del repositorio (plantilla en
[`.env.example`](../../.env.example)); compose no trae valores por defecto y se
detiene si falta alguna. En un Server C dedicado basta copiar ese `.env` con
estas cinco variables.

| Variable | Valor en `.env.example` | Descripción |
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
├── prometheus/
│   ├── prometheus.yml                    configuración de scrape (file_sd)
│   └── targets/
│       ├── api-targets.yml               target de la API (editable por entorno)
│       └── minio-targets.yml             target de MinIO (k8s por defecto; editable por entorno)
└── grafana/
    ├── provisioning/
    │   ├── datasources/datasource.yml    Prometheus (uid: prometheus, URL por env)
    │   └── dashboards/dashboards.yml      provider de dashboards
    └── dashboards/
        └── diabetes-api.json              dashboard principal
```
