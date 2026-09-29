import json
from datetime import UTC, datetime

import pytest

from lakehouse.athena import AthenaQueryError, QueryResult, run_query
from lakehouse.export_public import (
    MANIFEST_KEY,
    Dataset,
    export,
    run_id_for,
    select_sql,
    unload_sql,
)

NOW = datetime(2026, 9, 29, 10, 30, tzinfo=UTC)
DATASETS = (Dataset("a", "g.a", "A"), Dataset("b", "g.b", "B", "where x > 1"))
COLUMNS = [
    ["t", "c", "d"],
    ["g.a", "id", "bigint"],
    ["g.a", "ts", "timestamp(6)"],
    ["g.b", "x", "double"],
]


class FakeS3:
    def __init__(self, keys=()):
        self.keys = set(keys)
        self.events = []

    def list_objects_v2(self, Bucket, Prefix, Delimiter=None):
        hits = sorted(k for k in self.keys if k.startswith(Prefix))
        if Delimiter:
            prefixes = sorted(
                {
                    Prefix + k[len(Prefix) :].split("/")[0] + "/"
                    for k in hits
                    if "/" in k[len(Prefix) :]
                }
            )
            return {"CommonPrefixes": [{"Prefix": p} for p in prefixes]}
        return {"Contents": [{"Key": k} for k in hits]}

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.keys.discard(o["Key"])
            self.events.append(("delete", o["Key"]))

    def put_object(self, Bucket, Key, Body, **kw):
        self.keys.add(Key)
        self.events.append(("put", Key, Body))


def fake_query(s3):
    def q(athena, sql, fetch_rows=True):
        if sql.startswith("select table_schema"):
            s3.events.append(("columns", sql))
            return QueryResult("c", 10, COLUMNS)
        # Simulate UNLOAD writing a file under the target prefix.
        target = sql.split("to 's3://bkt/")[1].split("'")[0]
        s3.keys.add(target + "part-0.parquet")
        s3.events.append(("unload", target))
        return QueryResult("q", 1000, [])

    return q


def test_unload_sql_shape():
    sql = unload_sql(" select * from t ", "s3://b/public/x/run=1/")
    assert sql == (
        "unload (select * from t) to 's3://b/public/x/run=1/' "
        "with (format = 'PARQUET', compression = 'SNAPPY')"
    )


def test_manifest_written_after_all_unloads_and_points_at_run():
    s3 = FakeS3()
    manifest = export(None, s3, "bkt", now=NOW, datasets=DATASETS, query=fake_query(s3))
    kinds = [e[0] for e in s3.events]
    assert kinds.index("put") > max(i for i, k in enumerate(kinds) if k == "unload")
    run = run_id_for(NOW)
    assert manifest["datasets"]["a"]["path"] == f"s3://bkt/public/a/run={run}/"
    body = json.loads(next(e[2] for e in s3.events if e[0] == "put" and e[1] == MANIFEST_KEY))
    assert body["exported_at"] == "2026-09-29T10:30:00Z"
    assert set(body["datasets"]) == {"a", "b"}
    assert manifest["bytes_scanned"] == 2010
    unloads = [e[1] for e in s3.events if e[0] == "unload"]
    assert len(unloads) == 2


def test_prunes_old_runs_but_keeps_previous():
    old = ["public/a/run=20260101T000000Z/p.parquet", "public/a/run=20260201T000000Z/p.parquet"]
    s3 = FakeS3(old)
    export(None, s3, "bkt", now=NOW, datasets=DATASETS[:1], query=fake_query(s3))
    remaining = sorted(k for k in s3.keys if k.startswith("public/a/"))
    assert remaining == [
        "public/a/run=20260201T000000Z/p.parquet",
        f"public/a/run={run_id_for(NOW)}/part-0.parquet",
    ]


def test_failed_unload_leaves_old_manifest_untouched():
    s3 = FakeS3([MANIFEST_KEY])

    def failing(athena, sql, fetch_rows=True):
        raise AthenaQueryError("boom")

    with pytest.raises(AthenaQueryError):
        export(None, s3, "bkt", now=NOW, datasets=DATASETS, query=failing)
    assert not any(e[0] == "put" for e in s3.events)


class FakeAthena:
    def __init__(self, states, reason=None):
        self.states = list(states)
        self.reason = reason
        self.stopped = False

    def start_query_execution(self, QueryString, WorkGroup):
        assert WorkGroup == "checkyouraqi"
        return {"QueryExecutionId": "qid"}

    def get_query_execution(self, QueryExecutionId):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {
            "QueryExecution": {
                "Status": {"State": state, "StateChangeReason": self.reason},
                "Statistics": {"DataScannedInBytes": 42},
            }
        }

    def get_query_results(self, QueryExecutionId):
        return {"ResultSet": {"Rows": [{"Data": [{"VarCharValue": "x"}]}]}}

    def stop_query_execution(self, QueryExecutionId):
        self.stopped = True


def test_run_query_polls_until_done():
    r = run_query(FakeAthena(["QUEUED", "RUNNING", "SUCCEEDED"]), "select 1", sleep=lambda s: None)
    assert r.bytes_scanned == 42
    assert r.rows == [["x"]]


def test_run_query_raises_with_reason_and_times_out():
    with pytest.raises(AthenaQueryError, match="scan limit"):
        run_query(FakeAthena(["FAILED"], "scan limit exceeded"), "q", sleep=lambda s: None)
    athena = FakeAthena(["RUNNING"])
    with pytest.raises(AthenaQueryError, match="timed out"):
        run_query(athena, "q", timeout_s=3, poll_s=1, sleep=lambda s: None)
    assert athena.stopped


def test_select_sql_casts_timestamps_to_millis():
    sql = select_sql(DATASETS[0], [("id", "bigint"), ("ts", "timestamp(6)")])
    assert sql == 'select "id", cast("ts" as timestamp(3)) as "ts" from g.a'
    assert select_sql(DATASETS[1], [("x", "double")]) == 'select "x" from g.b where x > 1'
    with pytest.raises(ValueError):
        select_sql(DATASETS[0], [])
