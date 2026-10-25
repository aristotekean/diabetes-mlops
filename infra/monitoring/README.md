# Observabilidad · Server C (Prometheus + Grafana)

Stack de monitoreo de la API de inferencia. Prometheus recolecta las métricas
que la API expone en `/metrics` y Grafana las visualiza en el dashboard
**Diabetes · Inference API**.

```
api-inference:8000/metrics  ──scrape──▶  S8 Prometheus  ──query──▶  S9 Grafana
```

## Qué se mide

La API (`apps/api-inference`) está instrumentada con
[`prometheus-fastapi-instrumentator`](https://github.com/trallnag/prometheus-fastapi-instrumentator).
Cada ruta se mide automáticamente a través de la etiqueta `handler`:

| Métrica | Tipo | Etiquetas | Uso en el dashboard |
|---------|------|-----------|---------------------|
| `http_requests_total` | Counter | `method`, `handler`, `status` | total, req/s, errores, tasa de error |
| `http_request_duration_seconds` | Histogram | `method`, `handler`, `status` | latencia promedio y percentiles p50/p95/p99 |
| `process_cpu_seconds_total` | Counter | — | CPU del pod de la API |
| `process_resident_memory_bytes` | Gauge | — | memoria del pod de la API |

El `status` se agrupa como `2xx` / `4xx` / `5xx`.

## Paneles del dashboard

Cubre los mínimos exigidos por el enunciado:

- Solicitudes totales · solicitudes por segundo
- Latencia promedio · latencia p95 (KPI) y serie p50/p95/p99
- Número de errores (4xx/5xx) · tasa de error (KPI y en el tiempo)
- Solicitudes por estado · solicitudes por endpoint
- CPU y memoria del pod de la API

## Uso local (stack completo)

Desde la raíz del repositorio, el compose de monitoreo se incluye en el
`docker-compose.yml` principal, así que sube junto con el resto:

```bash
docker compose up -d --build
```

| Servicio | URL | Credenciales |
|----------|-----|--------------|
| Prometheus | <http://localhost:9090> | — |
| Grafana | <http://localhost:3000> | `admin` / `admin` |

Grafana abre directamente en el dashboard **Diabetes · Inference API** (datasource
y dashboard se aprovisionan solos desde `grafana/provisioning`).

Para ver datos: generar tráfico contra la API (`/health`, `/predict`) o lanzar
Locust (`docker compose --profile loadtest up -d`, ver `infra/load-testing`) y
observar cómo cambian los paneles durante la prueba de carga.

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
├── docker-compose.yml                    S8 Prometheus + S9 Grafana (red Docker)
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
