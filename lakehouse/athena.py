"""Minimal Athena runner for pipeline tasks (the project workgroup enforces the scan cap)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

WORKGROUP = "checkyouraqi"


class AthenaQueryError(RuntimeError):
    pass


@dataclass(frozen=True)
class QueryResult:
    query_id: str
    bytes_scanned: int
    rows: list[list[str]]


def run_query(
    client: Any,
    sql: str,
    *,
    workgroup: str = WORKGROUP,
    poll_s: float = 1.0,
    timeout_s: float = 600,
    fetch_rows: bool = True,
    sleep=time.sleep,
) -> QueryResult:
    qid = client.start_query_execution(QueryString=sql, WorkGroup=workgroup)["QueryExecutionId"]
    waited = 0.0
    while True:
        q = client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        state = q["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        if waited >= timeout_s:
            client.stop_query_execution(QueryExecutionId=qid)
            raise AthenaQueryError(f"query {qid} timed out after {timeout_s:.0f}s")
        sleep(poll_s)
        waited += poll_s
    if state != "SUCCEEDED":
        raise AthenaQueryError(f"query {qid} {state}: {q['Status'].get('StateChangeReason')}")
    scanned = q.get("Statistics", {}).get("DataScannedInBytes", 0)
    rows: list[list[str]] = []
    if fetch_rows:
        result = client.get_query_results(QueryExecutionId=qid)["ResultSet"]["Rows"]
        rows = [[c.get("VarCharValue", "") for c in r["Data"]] for r in result]
    return QueryResult(qid, scanned, rows)
