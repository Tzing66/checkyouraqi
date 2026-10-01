"""Daily, after the daily ingestion (weather actuals 02:00, fires 03:30 UTC): refresh the DAILY
models (ERA5, previous runs, CAMS history, fires, dimensions, seeds), then run the FULL test
suite over every model, then export. Hourly runs skip tests to keep S3 requests down.
"""

from datetime import UTC, datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import dag, task

DEFAULT_ARGS = {"retries": 1, "retry_delay": timedelta(minutes=5)}

# dbt lives in its own venv; the repo's dbt/ is mounted read-only, so build output, logs and
# packages go to /tmp inside the container.
DBT = (
    "cd /opt/airflow/project/dbt && "
    # 2 threads: dbt runs inside the scheduler container (LocalExecutor) under its memory cap.
    "export DBT_THREADS=2 DBT_TARGET_PATH=/tmp/dbt/target DBT_LOG_PATH=/tmp/dbt/logs "
    "DBT_PACKAGES_INSTALL_PATH=/tmp/dbt/packages && "
    "( [ -d /tmp/dbt/packages/dbt_utils ] || /home/airflow/dbt-venv/bin/dbt deps "
    "--profiles-dir . ) && /home/airflow/dbt-venv/bin/dbt"
)


@dag(
    schedule="15 4 * * *",
    start_date=datetime(2026, 9, 29, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=90),
    default_args=DEFAULT_ARGS,
    tags=["lakehouse", "dbt", "daily"],
)
def dbt_daily():
    build = BashOperator(
        task_id="dbt_build_daily",
        bash_command=f"{DBT} build --selector daily --profiles-dir . --no-use-colors",
        execution_timeout=timedelta(minutes=30),
    )

    test_all = BashOperator(
        task_id="dbt_test_all",
        bash_command=f"{DBT} test --profiles-dir . --no-use-colors",
        execution_timeout=timedelta(minutes=30),
        retries=0,
    )

    @task(execution_timeout=timedelta(minutes=15))
    def export_public() -> dict:
        from lakehouse import jobs

        return jobs.export_public()

    build >> test_all >> export_public()


dbt_daily()
