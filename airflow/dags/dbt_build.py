"""Hourly, after ingestion: refresh only the HOURLY models (dbt selector `hourly`), then export
the public snapshots. Source freshness runs alongside: it goes red when a source is stale (e.g.
OpenAQ's CPCB outage) without blocking the export, so the dashboard keeps showing last-known
data. Daily inputs and the full test suite run in `dbt_daily` (S3 request cost, decisions.md).
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
        bash_command=f"{DBT} run --selector hourly --profiles-dir . --no-use-colors",
        execution_timeout=timedelta(minutes=30),
    )

    freshness = BashOperator(
        task_id="source_freshness",
        bash_command=f"{DBT} source freshness --profiles-dir . --no-use-colors",
        execution_timeout=timedelta(minutes=10),
        retries=0,
        # Fails by design whenever a source is stale; feed_alert below reports that instead.
        on_failure_callback=None,
    )

    @task(execution_timeout=timedelta(minutes=15))
    def export_public() -> dict:
        from lakehouse import jobs

        return jobs.export_public()

    @task(execution_timeout=timedelta(minutes=5))
    def feed_alert() -> dict:
        # Telegram notice when the data source's status changes (ok / degraded / outage).
        from alerts import jobs

        return jobs.feed_status_hourly()

    build >> [freshness, export_public() >> feed_alert()]


dbt_build()
