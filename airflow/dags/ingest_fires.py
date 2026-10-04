"""Daily: NASA FIRMS VIIRS fire detections for the last 2 days (NRT keeps filling in)."""

from datetime import UTC, datetime, timedelta

from airflow.sdk import dag, get_current_context, task

from alerts.jobs import task_failed

DEFAULT_ARGS = {
    "retries": 2,
    "retry_delay": timedelta(minutes=10),
    "on_failure_callback": task_failed,
}


def run_time():
    # Airflow 3: manually triggered runs have no logical_date, so fall back to run_after.
    ctx = get_current_context()
    return ctx.get("logical_date") or ctx["dag_run"].run_after


@dag(
    schedule="30 3 * * *",
    start_date=datetime(2026, 9, 28, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["ingest", "fires"],
)
def ingest_fires():
    @task(execution_timeout=timedelta(minutes=15))
    def fires() -> dict:
        from ingestion import jobs

        return jobs.fires_daily(run_time().date())

    fires()


ingest_fires()
