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
infra/k8s/       manifiestos kustomize por servicio
infra/monitoring observabilidad con Prometheus y Grafana (Server C)
infra/load-testing  pruebas de carga con Locust (Server B)
```

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

## Procesamiento y entrenamiento

El paquete compartido `ml/diabetes_model` valida y prepara el CSV, excluye
identificadores y variables posteriores al desenlace, separa por paciente y
entrena un pipeline reproducible. Para ejecutarlo localmente, instala el
paquete y sus dependencias; luego ejecuta:

```bash
python3 -m pip install -e ./ml/diabetes_model
python3 -m diabetes_model data/Diabetes/Diabetes.csv
```

El pipeline reporta macro-F1 de validación como métrica primaria, además de
métricas por clase y evaluación final en test. La imagen de Airflow también
instala el mismo paquete compartido para que los DAGs lo reutilicen en el
clúster; no se debe mantener una implementación distinta del preprocesamiento
en Airflow.

## DAG de entrenamiento

El DAG `diabetes_incremental_training` valida el CSV, carga RAW en lotes de
hasta 15.000 filas, valida RAW, genera CLEAN por lotes, entrena y registra
candidatos en MLflow, compara macro-F1 de validación contra el alias productivo
y solo lo mueve si mejora. El DAG usa `@weekly`, desactiva catchup, limita a una
ejecución activa y reintenta las tareas fallidas dos veces. Los IDs de lote se
derivan del checksum y número de lote; el checksum/posición de fila y
`raw_record_id`/versión de transformación hacen idempotentes RAW y CLEAN.

La tarea inicial descarga el CSV del taller al volumen persistente cuando el
archivo todavía no existe; luego valida esquema, claves y etiquetas. En Compose
se monta `data/Diabetes` en Airflow. En Kubernetes/servidor remoto, monta un
volumen persistente escribible en `DIABETES_DATASET_PATH` y permite salida HTTPS
a Google Drive. El CSV no se incluye en la imagen ni en Git. La conexión
PostgreSQL se configura con `TRAIN_DB_*` o `TRAIN_DATABASE_URL`, y MLflow con
`MLFLOW_TRACKING_URI`. Aplica antes la migración PostgreSQL descrita en
[docs/data-contract.md](docs/data-contract.md). La primera ejecución puede
promover el primer modelo al alias `production`; las siguientes solo lo
reemplazan si superan su macro-F1 de validación sobre el mismo snapshot y split.
