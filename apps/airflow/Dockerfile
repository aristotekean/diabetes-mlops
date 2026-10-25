# Airflow (S6) image: official base plus the Python dependencies the DAGs need
# to read the training database, train the model and register it in MLflow.
#
# Build context is the repository root, so the shared ml/diabetes_model package
# can be installed here once it exists:
#   docker compose build airflow-scheduler
#   docker build -f apps/airflow/Dockerfile -t diabetes-airflow .

ARG AIRFLOW_VERSION=3.3.2
FROM apache/airflow:${AIRFLOW_VERSION}
ARG AIRFLOW_VERSION

COPY apps/airflow/requirements.txt /tmp/requirements.txt

# Re-stating apache-airflow pins its version so pip cannot upgrade or
# downgrade it while resolving the DAG dependencies.
RUN pip install --no-cache-dir \
    "apache-airflow==${AIRFLOW_VERSION}" \
    -r /tmp/requirements.txt
