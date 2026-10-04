"""Run an Athena query in the project workgroup and print rows + bytes scanned (dev helper).

PYTHONPATH=. uv run python scripts/athena_query.py "select 1"
"""

import sys

import boto3

from ingestion.settings import Settings
from lakehouse.athena import AthenaQueryError, run_query

if __name__ == "__main__":
    s = Settings()
    client = boto3.Session(profile_name=s.aws_profile, region_name=s.aws_region).client("athena")
    for sql in sys.argv[1:]:
        try:
            r = run_query(client, sql, poll_s=0.5)
            print(f"-- {r.bytes_scanned / 1e6:.2f} MB scanned")
            for row in r.rows[:12]:
                print("   ", " | ".join(row))
        except AthenaQueryError as e:
            print("-- FAILED:", str(e)[:400])
