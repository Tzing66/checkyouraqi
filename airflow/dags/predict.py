"""Hourly, after dbt_build (:25) has refreshed fct_features_live: forecast every station for
24/48/72h with the production model, build fct_forecasts / fct_forecast_accuracy, and re-export
the public snapshots. Stations with stale inputs are still forecast and flagged (owner
decision 2026-09-29).
"""

from datetime import UTC, datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import dag, task

from alerts.jobs import task_failed

DEFAULT_ARGS = {
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "on_failure_callback": task_failed,
}

DBT = (
    "cd /opt/airflow/project/dbt && "
    "export DBT_THREADS=2 DBT_TARGET_PATH=/tmp/dbt/target DBT_LOG_PATH=/tmp/dbt/logs "
    "DBT_PACKAGES_INSTALL_PATH=/tmp/dbt/packages && "
    "( [ -d /tmp/dbt/packages/dbt_utils ] || /home/airflow/dbt-venv/bin/dbt deps "
    "--profiles-dir . ) && /home/airflow/dbt-venv/bin/dbt"
)


@dag(
    schedule="45 * * * *",
    start_date=datetime(2026, 9, 29, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=30),
    default_args=DEFAULT_ARGS,
    tags=["ml", "serving"],
)
def predict():
    @task(execution_timeout=timedelta(minutes=10))
    def predict_live() -> dict:
        from ml import jobs

        return jobs.predict_live()

    build_forecasts = BashOperator(
        task_id="dbt_build_forecasts",
        bash_command=f"{DBT} build -s stg_predictions+ --profiles-dir . --no-use-colors",
        execution_timeout=timedelta(minutes=15),
    )

    @task(execution_timeout=timedelta(minutes=15))
    def export_public() -> dict:
        from lakehouse import jobs

        return jobs.export_public()

    predict_live() >> build_forecasts >> export_public()


predict()
