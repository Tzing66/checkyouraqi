"""Daily: feature drift report (Evidently HTML -> reports/) and live accuracy summary
(-> public/). Accuracy stays empty while the air-quality source is down."""

from datetime import UTC, datetime, timedelta

from airflow.sdk import dag, get_current_context, task

from alerts.jobs import task_failed

DEFAULT_ARGS = {
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
    "on_failure_callback": task_failed,
}


def run_time():
    # Airflow 3: manually triggered runs have no logical_date, so fall back to run_after.
    ctx = get_current_context()
    return ctx.get("logical_date") or ctx["dag_run"].run_after


@dag(
    schedule="30 4 * * *",
    start_date=datetime(2026, 9, 29, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["ml", "monitoring"],
)
def monitor():
    @task(execution_timeout=timedelta(minutes=20))
    def drift_and_accuracy() -> dict:
        from ml import jobs

        return jobs.monitor_daily(run_time().date())

    drift_and_accuracy()


monitor()
