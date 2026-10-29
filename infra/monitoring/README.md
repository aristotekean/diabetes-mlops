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

## Despliegue en un Server C dedicado

Cuando el monitoreo corre en una máquina aparte del clúster, apunta el scrape a
la dirección de la API en `prometheus/targets/api-targets.yml` (host:puerto
alcanzable desde Server C) y levanta el stack con `docker-compose.yml`. Prometheus
recarga el target sin reiniciar (`file_sd`).

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
   docker compose -f docker-compose.yml up -d
   ```

Así Server C alcanza la API de Server A **de forma privada por la tailnet**, sin
abrir puertos públicos y sin cambiar el stack de monitoreo.

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
├── .env.example                          variables (puertos, admin de Grafana)
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
