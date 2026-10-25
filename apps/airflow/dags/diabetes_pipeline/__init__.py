"""Task logic for the diabetes_incremental_training DAG.

Kept out of the DAG file so parsing stays fast and each task stays small;
``.airflowignore`` stops Airflow from scanning this package for DAGs.
"""
