# diabetes-mlops

Plataforma MLOps de extremo a extremo para predecir el riesgo de diabetes. Cubre el ciclo completo: orquestación del entrenamiento, registro y versionado de modelos, servicio de inferencia, interfaz para el usuario final, monitoreo y pruebas de carga.

## Arquitectura

![Arquitectura de la plataforma](docs/images/1-arquitectura.png)

La plataforma se despliega sobre tres servidores conectados por una red Tailscale:

| Servidor | Rol | Componentes |
|----------|-----|-------------|
| Server A | Clúster Kubernetes | S1 PostgreSQL (metadatos de MLflow), S2 MLflow (gestor de modelos), S3 MinIO (almacén de artefactos), S4 FastAPI (API de inferencia), S5 PostgreSQL (datos de entrenamiento), S6 Airflow (orquestador de entrenamiento), S7 Streamlit (frontend), Ingress controller |
| Server B | Pruebas de carga | Locust |
| Server C | Monitoreo | S8 Prometheus (métricas), S9 Grafana (dashboards) |

Flujo principal: Airflow toma los datos de entrenamiento desde PostgreSQL, entrena y registra el modelo en MLflow, que persiste los artefactos en MinIO. La API de inferencia carga el modelo desde MLflow y expone las predicciones al frontend de Streamlit a través del Ingress. Prometheus recolecta las métricas del clúster y Grafana las visualiza.

## Estructura del repositorio

```
apps/            servicios propios: api-inference (FastAPI), frontend (Streamlit), airflow (DAGs)
ml/              paquete python compartido con la lógica del modelo
notebooks/       análisis exploratorio que justifica el procesamiento
infra/database/  migraciones SQL de las capas RAW, CLEAN e INFERENCE
infra/k8s/       manifiestos kustomize de Server A (ver "Despliegue en Kubernetes")
infra/monitoring observabilidad con Prometheus y Grafana (Server C)
infra/load-testing  pruebas de carga con Locust (Server B)
```

## Imágenes y ejecución en Mac o Linux

Las imágenes propias (`diabetes-api-inference`, `diabetes-frontend` y
`diabetes-airflow`) se publican en DockerHub para `linux/amd64` y `linux/arm64`
bajo un mismo tag. Cada máquina descarga la variante de su procesador, así que
las mismas imágenes corren nativas en un Mac con Apple Silicon y en un servidor,
VM o nodo de Kubernetes Linux, sin emulación.

Para construirlas y publicarlas (desde la raíz, con el árbol de Git limpio):

```bash
docker login                                   # con la cuenta de DOCKERHUB_USER
scripts/publish_images.sh                      # construye ambas arquitecturas y publica
scripts/publish_images.sh --check              # solo construye, sin publicar
IMAGE_TAG=0.1.1 scripts/publish_images.sh api-inference   # un componente, otro tag
```

El script usa un builder `docker-container` de buildx (lo crea si falta); la
arquitectura ajena se compila emulada, por lo que esa parte tarda más. Publica un
tag nuevo en cada cambio: los nodos guardan en caché la imagen de un tag ya
descargado.

Para levantar el stack en un **servidor o VM Linux**, copia `.env.example` a
`.env` y ajusta:

- `AIRFLOW_UID` con el resultado de `id -u`, para que Airflow pueda escribir
  `apps/airflow/logs` y descargar el dataset en `data/Diabetes`.
- `MLFLOW_ALLOWED_HOSTS` con la IP o el nombre del servidor (p. ej.
  `192.168.1.50:*`) para abrir la UI de MLflow desde otra máquina; sin eso
  responde 403.
- `POSTGRES_TRAIN_PORT` u otros puertos si el host ya los usa.

Luego `docker compose up -d` descarga las imágenes publicadas
(`docker compose up -d --build` las construye en el propio servidor).

## Despliegue en Kubernetes (OrbStack)

`infra/k8s` despliega Server A completo (S1–S7 e Ingress) con kustomize sobre
el Kubernetes integrado de OrbStack. Usa las mismas imágenes publicadas en
Docker Hub (`aristotekean/diabetes-*:0.1.1`, el tag fijado en el bloque
`images:` de `infra/k8s/kustomization.yaml`): no hace falta construir nada en
local.

### Requisitos

| Requisito | Detalle |
|-----------|---------|
| OrbStack con Kubernetes habilitado | Settings → Kubernetes. Trae `kubectl` y la StorageClass `local-path`. |
| Ingress controller | OrbStack no incluye ninguno. Instala ingress-nginx desde el manifiesto oficial (abajo). |
| Salida a Internet desde el clúster | Docker Hub para las imágenes y Google Drive para el dataset. |

Instalación del Ingress controller (una sola vez por clúster, versión fijada):

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.15.1/deploy/static/provider/cloud/deploy.yaml
kubectl -n ingress-nginx rollout status deploy/ingress-nginx-controller --timeout=180s
```

El Service `ingress-nginx-controller` es de tipo `LoadBalancer`; OrbStack le
asigna una IP y resuelve cualquier `*.k8s.orb.local` hacia ella desde el Mac.

### Despliegue

```bash
kubectl apply -k infra/k8s          # crea o actualiza todo
kubectl -n diabetes get pods        # espera a ver Running y Completed
kubectl -n diabetes get ingress     # el ADDRESS tarda unos segundos en aparecer
```

Todo queda en el namespace `diabetes`:

| Recurso | Qué es |
|---------|--------|
| `mlflow-db`, `minio`, `mlflow` | S1 PostgreSQL de MLflow, S3 MinIO (bucket `mlflow`), S2 MLflow tracking server |
| `postgres-train` | S5 PostgreSQL con los esquemas `raw`, `clean` e `inference` |
| `api-inference`, `frontend` | S4 FastAPI y S7 Streamlit |
| `airflow-db`, `airflow` | PostgreSQL de metadatos y S6 Airflow en un solo pod (api-server, scheduler, dag-processor, triggerer) |
| Jobs `minio-init`, `train-db-migrate`, `airflow-init` | Crean el bucket, aplican las migraciones SQL y migran la base de Airflow (equivalen a los one-shot de compose) |
| Ingress `diabetes` | Enrutamiento por host hacia los cuatro servicios HTTP |

Los pods dependientes esperan con `initContainers` a que su base de datos y su
Job estén listos, así que el primer arranque tarda unos minutos.

### Acceso

| URL | Servicio | Credenciales de desarrollo |
|-----|----------|----------------------------|
| http://frontend.k8s.orb.local | Streamlit | — |
| http://api.k8s.orb.local | API de inferencia (`/docs`, `/health`, `/model-info`, `/predict`) | — |
| http://mlflow.k8s.orb.local | MLflow UI | — |
| http://airflow.k8s.orb.local | Airflow UI | `airflow` / `airflow` |

Cualquier Service también responde desde el Mac por su nombre interno, p. ej.
`http://mlflow.diabetes.svc.cluster.local:5000` o
`http://minio.diabetes.svc.cluster.local:9001` (consola de MinIO,
`minioadmin` / `minioadmin`).

### Primer entrenamiento

El DAG `diabetes_incremental_training` se crea **pausado**. Para obtener el
primer modelo basta con activarlo: el scheduler lanza enseguida la corrida del
intervalo anterior (sin catchup del resto). Si no quieres esperar, puedes
además dispararlo a mano; ten en cuenta que cada corrida carga un lote nuevo.

```bash
kubectl -n diabetes exec deploy/airflow -c scheduler -- airflow dags unpause diabetes_incremental_training
kubectl -n diabetes exec deploy/airflow -c scheduler -- airflow dags trigger diabetes_incremental_training   # opcional
kubectl -n diabetes exec deploy/airflow -c scheduler -- airflow dags list-runs -d diabetes_incremental_training
```

La ejecución descarga el dataset al volumen persistente, carga el primer lote,
entrena y registra `models:/diabetes@production` en MLflow (alrededor de un
minuto por corrida con 15.000 filas por lote). La
API recarga el modelo sola cada 60 s (`MODEL_REFRESH_SECONDS`); para forzarlo,
`curl -X POST http://api.k8s.orb.local/model/reload`. Comprueba:

```bash
curl http://api.k8s.orb.local/model-info            # "status": "ready"
curl -X POST http://api.k8s.orb.local/predict \
  -H 'content-type: application/json' -d @payload.json   # ejemplo en apps/api-inference/README.md
```

Mientras el DAG esté activo, el scheduler lo ejecuta cada 10 minutos
(`DIABETES_TRAINING_SCHEDULE`) y cada corrida carga un lote nuevo y reentrena.
En un portátil conviene volver a pausarlo tras la primera corrida y
reactivarlo solo cuando quieras seguir con la carga incremental:

```bash
kubectl -n diabetes exec deploy/airflow -c scheduler -- airflow dags pause diabetes_incremental_training
```

### Operación

| Tarea | Cómo |
|-------|------|
| Cambiar una migración SQL | Edita `infra/database/migrations`, ejecuta `bash infra/k8s/scripts/render-migrations.sh` y commitea el ConfigMap generado. `--check` falla si está desactualizado (pensado para CI). |
| Comprobar los manifiestos antes de commitear | `bash infra/k8s/scripts/check.sh`: construye el kustomize y verifica que el ConfigMap de migraciones esté al día (no hay CI en el repo; sirve como pre-commit). |
| Redesplegar una versión nueva | Publica las imágenes con un tag nuevo, cambia `newTag` en `infra/k8s/kustomization.yaml` y vuelve a aplicar (pasos abajo). |
| Reaplicar un Job | Los Jobs terminados se borran solos a los 10 minutos (`ttlSecondsAfterFinished`), así que un `kubectl apply -k infra/k8s` posterior los vuelve a crear y ejecutar (son idempotentes). Solo hace falta `kubectl -n diabetes delete job <nombre>` si quieres repetirlo dentro de esos 10 minutos, p. ej. para aplicar migraciones nuevas en el momento. |
| Borrar todo | `kubectl delete -k infra/k8s` (los PVC y sus datos se eliminan con el namespace). |

> **Credenciales de desarrollo.** Los Secrets llevan los mismos valores por
> defecto que compose (`minioadmin`, `airflow`, contraseñas `app`/`mlflow`).
> Son para un clúster local; cámbialos antes de exponer el clúster a otras
> máquinas.

#### Redespliegue de una versión nueva

Cada cambio de código se publica con un **tag nuevo**; nunca se reutiliza uno
existente. Los manifiestos usan `imagePullPolicy: IfNotPresent`, y OrbStack
comparte el almacén de imágenes con el Docker local: si un tag ya existe en la
máquina (por ejemplo, un build local anterior), Kubernetes lo usa tal cual y no
descarga el publicado en Docker Hub, aunque su contenido sea distinto.

1. Publica las tres imágenes para amd64 y arm64 (requiere `docker login` y el
   árbol de trabajo sin cambios pendientes):

   ```bash
   IMAGE_TAG=0.1.2 scripts/publish_images.sh                       # las tres
   IMAGE_TAG=0.1.2 scripts/publish_images.sh frontend              # solo una
   ```

2. Apunta los manifiestos al tag nuevo: los tres `newTag` del bloque `images:`
   en `infra/k8s/kustomization.yaml`. Mantén el mismo valor en `.env.example` y
   en el valor por defecto de `docker-compose.yml`.

3. Aplica y espera a que los Deployments terminen de rotar:

   ```bash
   kubectl apply -k infra/k8s
   kubectl -n diabetes rollout status deploy/frontend deploy/api-inference deploy/airflow --timeout=600s
   ```

   Los datos (modelo registrado, bases de datos, dataset) viven en los PVC y se
   conservan. Si `apply` responde que `airflow-init` es inmutable, el Job aún no
   venció su TTL: `kubectl -n diabetes delete job airflow-init` y vuelve a
   aplicar.

Para comprobar qué imagen corre cada pod y si coincide con Docker Hub:

```bash
kubectl -n diabetes get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].image}{"\n"}{end}'
docker buildx imagetools inspect aristotekean/diabetes-frontend:0.1.2   # digest publicado
```

### En un servidor Linux

- Los hosts `*.k8s.orb.local` solo existen en OrbStack. En
  `infra/k8s/base/ingress/ingress.yaml` sustitúyelos por el nombre Tailscale
  (o la IP) del nodo, uno por servicio o con prefijos de ruta.
- Añade ese host a `MLFLOW_ALLOWED_HOSTS` en
  `infra/k8s/base/mlflow/deployment.yaml` (p. ej. `mlflow.<nodo>.ts.net:*`);
  sin eso MLflow responde 403 desde otra máquina.
- La opción "Expose services to local network devices" es exclusiva de OrbStack:
  sin ella los LoadBalancer y NodePort solo se alcanzan desde el propio Mac. En
  un servidor, expón el controller con el LoadBalancer de tu clúster o un
  NodePort y apunta el DNS de Tailscale a él.

## Dataset

Descarga el dataset Diabetes 130-US hospitals indicado en el taller desde la raíz
del repositorio:

```bash
python3 -m pip install -r scripts/requirements.txt
python3 scripts/download_diabetes_dataset.py
```

El archivo se guarda en `data/Diabetes/Diabetes.csv`. El script valida que sea
un CSV legible con las columnas `encounter_id` y `readmitted`, cuenta los
registros y no vuelve a descargar un archivo válido que ya exista. Para
reemplazarlo, ejecuta `python3 scripts/download_diabetes_dataset.py --force`.
El dataset descargado está excluido de Git.

El diseño de las tablas RAW, CLEAN e INFERENCE, las reglas de trazabilidad y
el contrato de entrada/salida de inferencia están documentados en
[docs/data-contract.md](docs/data-contract.md). La migración PostgreSQL inicial
está en
[infra/database/migrations/001_create_data_contract.sql](infra/database/migrations/001_create_data_contract.sql).

## Análisis y procesamiento

[notebooks/01_eda.ipynb](notebooks/01_eda.ipynb) contiene el análisis
exploratorio que justifica la limpieza, la ingeniería de características
(`readmission-features-v2`), la partición por paciente y la métrica de
selección. Para ejecutarlo localmente:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ./ml/diabetes_model jupyter matplotlib
.venv/bin/jupyter lab notebooks/01_eda.ipynb
```

El paquete compartido `ml/diabetes_model` implementa esas reglas una sola vez y
lo reutilizan el DAG y la API:

- `contract.py`: esquema de la fuente, validación por registro, exclusión de
  fallecidos/hospicio y el contrato de 44 campos de entrada.
- `features.py`: 37 variables del modelo (diagnósticos ICD-9 agrupados,
  especialidad, edad ordinal, conteos de visitas y medicamentos, …).
- `splits.py`: partición 70/15/15 por hash de paciente, estable entre lotes.
- `models.py`: candidatos (regresión logística, gradient boosting, random
  forest), métricas y el pipeline que se sirve.

Para entrenar los candidatos sobre el CSV completo y ejecutar las pruebas:

```bash
.venv/bin/pip install -e ./ml/diabetes_model pytest
.venv/bin/python -m diabetes_model data/Diabetes/Diabetes.csv
.venv/bin/python -m pytest ml/diabetes_model/tests
```

La métrica principal es el **macro-F1 de validación**: las clases están
desbalanceadas (`<30` es el 11%) y el macro-F1 pondera por igual las tres. Con
el dataset completo el mejor candidato es gradient boosting (macro-F1 de
validación 0,441, test 0,441), frente a 0,425 de la regresión logística v1.

## DAG de entrenamiento

El DAG `diabetes_incremental_training` simula la llegada incremental de datos:
**cada ejecución carga un lote nuevo** (máximo 15.000 filas) y reentrena con
todo lo acumulado. Tareas:

1. `validate_source_file`: descarga el CSV si falta y valida esquema y forma.
2. `ingest_next_batch`: carga a `raw.records` el siguiente lote pendiente.
3. `validate_raw_quality`: marca cada fila `validated`/`rejected` con su motivo;
   falla si un lote supera 1% de rechazos.
4. `preprocess_raw_to_clean`: lee RAW (no el CSV), aplica la transformación y
   deja el resultado en `clean.records_staging`.
5. `store_clean_records`: fusiona staging en `clean.records` de forma atómica.
6. `split_dataset`: asigna train/validation/test por paciente y verifica que
   no haya fuga entre particiones.
7. `check_training_needed`: omite el entrenamiento si no hay datos nuevos.
8. `train_candidates`: entrena los 3 candidatos como runs anidados de MLflow
   (parámetros, métricas, matrices de confusión, reportes y modelo).
9. `register_best_model`: registra el mejor por macro-F1 de validación.
10. `compare_with_champion`: evalúa la nueva versión y la versión productiva
    sobre la misma validación, cargándolas desde el registro.
11. `promote_model`: mueve el alias `production` solo si la nueva versión es
    mejor.

Por defecto se ejecuta cada 10 minutos (`DIABETES_TRAINING_SCHEDULE`), sin
catchup y con una sola ejecución activa; las tareas se reintentan dos veces.
Es reejecutable: los lotes tienen IDs deterministas, RAW es único por
checksum y fila, CLEAN por registro y versión de transformación, y cada
versión registrada lleva la huella del snapshot de CLEAN con el que se
entrenó. Una vez cargados los 7 lotes, las ejecuciones siguientes no
entrenan.

Compose aplica las migraciones de `infra/database/migrations` con el servicio
`train-db-migrate` antes de iniciar Airflow. La conexión a PostgreSQL se
configura con `TRAIN_DB_*` o `TRAIN_DATABASE_URL`, y MLflow con
`MLFLOW_TRACKING_URI`. En Kubernetes, monta un volumen persistente escribible
en `DIABETES_DATASET_PATH` y permite salida HTTPS a Google Drive. El detalle de
tablas, reglas y contrato de inferencia está en
[docs/data-contract.md](docs/data-contract.md).

Si ya habías ejecutado la versión anterior del DAG, los datos en
`postgres-train` y el modelo `diabetes` corresponden a la carga completa v1.
Para repetir la carga incremental desde cero, elimina los volúmenes de datos:
`docker compose down && docker volume rm diabetes_postgres_train diabetes_mlflow_db diabetes_minio`.
