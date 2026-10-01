"""Hourly: OpenAQ trailing-6h PM2.5 + latest readings for every station, and a raw snapshot
of CPCB's live feed (backup source, no history upstream) -> bronze."""

from datetime import UTC, datetime, timedelta

from airflow.sdk import dag, get_current_context, task

DEFAULT_ARGS = {"retries": 2, "retry_delay": timedelta(minutes=5)}


def run_time():
    # Airflow 3: manually triggered runs have no logical_date, so fall back to run_after.
    ctx = get_current_context()
    return ctx.get("logical_date") or ctx["dag_run"].run_after


@dag(
    schedule="5 * * * *",
    start_date=datetime(2026, 9, 28, tzinfo=UTC),
    # The 6h lookback window re-covers short gaps; long gaps go through scripts/backfill.py.
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=50),
    default_args=DEFAULT_ARGS,
    tags=["ingest", "openaq"],
)
def ingest_openaq():
    @task(execution_timeout=timedelta(minutes=30))
    def extract() -> dict:
        from ingestion import jobs

        return jobs.openaq_hourly(run_time())

    @task(execution_timeout=timedelta(minutes=5), retries=3)
    def capture_cpcb() -> dict:
        # Independent of extract(): the backup source must not block, or be blocked by, OpenAQ.
        from ingestion import jobs

        return jobs.cpcb_snapshot_hourly(run_time())

    extract()
    capture_cpcb()


ingest_openaq()
