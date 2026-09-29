"""Hourly, after ingestion: dbt build (bronze -> silver -> gold + tests), then export the public
snapshots. Source freshness runs alongside: it goes red when a source is stale (e.g. OpenAQ's
CPCB outage) without blocking the export, so the dashboard keeps showing last-known data.
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
    # Ingestion runs at :05 (OpenAQ) and :10 (weather); build after both have landed.
    schedule="25 * * * *",
    start_date=datetime(2026, 9, 29, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=45),
    default_args=DEFAULT_ARGS,
    tags=["lakehouse", "dbt"],
)
def dbt_build():
    build = BashOperator(
        task_id="dbt_build",
        bash_command=f"{DBT} build --profiles-dir . --no-use-colors",
        execution_timeout=timedelta(minutes=30),
    )

    freshness = BashOperator(
        task_id="source_freshness",
        bash_command=f"{DBT} source freshness --profiles-dir . --no-use-colors",
        execution_timeout=timedelta(minutes=10),
        retries=0,
    )

    @task(execution_timeout=timedelta(minutes=15))
    def export_public() -> dict:
        from lakehouse import jobs

        return jobs.export_public()

    build >> [freshness, export_public()]


dbt_build()
