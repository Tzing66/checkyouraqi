"""Run an Athena query in the project workgroup and print rows + bytes scanned (dev helper).

PYTHONPATH=. uv run python scripts/athena_query.py "select 1"
"""

import sys
import time

import boto3

from ingestion.settings import Settings


def run(sql: str) -> tuple[list[list[str]], int]:
    s = Settings()
    athena = boto3.Session(profile_name=s.aws_profile, region_name=s.aws_region).client("athena")
    qid = athena.start_query_execution(QueryString=sql, WorkGroup="checkyouraqi")[
        "QueryExecutionId"
    ]
    while True:
        q = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        state = q["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(0.5)
    scanned = q.get("Statistics", {}).get("DataScannedInBytes", 0)
    if state != "SUCCEEDED":
        raise RuntimeError(f"{state}: {q['Status'].get('StateChangeReason')}")
    rows = athena.get_query_results(QueryExecutionId=qid, MaxResults=50)["ResultSet"]["Rows"]
    return [[c.get("VarCharValue", "") for c in r["Data"]] for r in rows], scanned


if __name__ == "__main__":
    for sql in sys.argv[1:]:
        try:
            rows, scanned = run(sql)
            print(f"-- {scanned / 1e6:.2f} MB scanned")
            for r in rows[:12]:
                print("   ", " | ".join(r))
        except RuntimeError as e:
            print("-- FAILED:", str(e)[:400])
