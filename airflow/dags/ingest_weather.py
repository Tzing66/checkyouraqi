"""Hourly: Open-Meteo weather + CAMS air-quality forecasts per zone, with issue time -> bronze."""

from datetime import UTC, datetime, timedelta

from airflow.sdk import dag, get_current_context, task

from alerts.jobs import task_failed

DEFAULT_ARGS = {
    "retries": 2,
    "retry_delay": timedelta(minutes=3),
    "on_failure_callback": task_failed,
}


def run_time():
    # Airflow 3: manually triggered runs have no logical_date, so fall back to run_after.
    ctx = get_current_context()
    return ctx.get("logical_date") or ctx["dag_run"].run_after


@dag(
    schedule="10 * * * *",
    start_date=datetime(2026, 9, 28, tzinfo=UTC),
    # Must never catch up: a late run would store today's forecast under a past issue time.
    # The extractor also refuses runs more than 2h from now as a second guard.
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=30),
    default_args=DEFAULT_ARGS,
    tags=["ingest", "weather"],
)
def ingest_weather():
    @task(execution_timeout=timedelta(minutes=10))
    def forecast() -> list[str]:
        from ingestion import jobs

        return jobs.weather_forecast_hourly(run_time())

    forecast()


ingest_weather()
