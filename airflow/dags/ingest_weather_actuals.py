"""Daily: observed weather (ERA5) + CAMS history for the last 7 days -> bronze (both lag)."""

from datetime import UTC, datetime, timedelta

from airflow.sdk import dag, get_current_context, task

DEFAULT_ARGS = {"retries": 2, "retry_delay": timedelta(minutes=10)}


def run_time():
    # Airflow 3: manually triggered runs have no logical_date, so fall back to run_after.
    ctx = get_current_context()
    return ctx.get("logical_date") or ctx["dag_run"].run_after


@dag(
    schedule="0 2 * * *",
    start_date=datetime(2026, 9, 28, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["ingest", "weather"],
)
def ingest_weather_actuals():
    @task(execution_timeout=timedelta(minutes=10))
    def actuals() -> list[str]:
        from ingestion import jobs

        return jobs.weather_actuals_daily(run_time().date())

    actuals()


ingest_weather_actuals()
