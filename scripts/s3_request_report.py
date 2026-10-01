"""Count and price S3 requests against the data bucket in a UTC time window, from the S3
server access logs (separate logs bucket, see infra/terraform/logging.tf). Needs read access
to the logs bucket (admin profile).

    PYTHONPATH=. uv run python scripts/s3_request_report.py \\
        --start 2026-10-01T12:00:00 --end 2026-10-01T12:10:00 [--profile checkyouraqi]

Logs are delivered best-effort (minutes to ~1h late), so run this a while after the window.
Prices: S3 Standard, ap-south-1 — Tier 1 (PUT/COPY/POST/LIST) $0.005 per 1,000;
Tier 2 (GET/HEAD/other) $0.0004 per 1,000.
"""

from __future__ import annotations

import argparse
import collections
import re
import shlex
from datetime import UTC, datetime

import boto3

from ingestion.settings import Settings

TIER1_PRICE = 0.005 / 1000
TIER2_PRICE = 0.0004 / 1000
# Operations S3 bills as Tier 1 (PUT, COPY, POST, LIST); everything else is Tier 2.
TIER1_OPS = re.compile(r"REST\.(PUT|POST|COPY)\.|REST\.GET\.BUCKET$|REST\.GET\.BUCKETVERSIONS$")

LINE = re.compile(
    r"^\S+ \S+ \[(?P<time>[^\]]+)\] \S+ \S+ \S+ (?P<op>\S+) (?P<key>\S+) (?P<rest>.*)$"
)


def parse_line(line: str) -> dict | None:
    m = LINE.match(line)
    if not m:
        return None
    t = datetime.strptime(m["time"], "%d/%b/%Y:%H:%M:%S %z").astimezone(UTC)
    rest = shlex.split(m["rest"])
    request_uri = rest[0] if rest else ""
    return {"time": t, "op": m["op"], "key": m["key"], "uri": request_uri}


def prefix_of(entry: dict) -> str:
    """Bucket area a request touched: for LIST, the queried prefix; else the object key."""
    if entry["op"] == "REST.GET.BUCKET":
        m = re.search(r"prefix=([^&\s]+)", entry["uri"])
        path = m.group(1).replace("%2F", "/") if m else ""
    else:
        path = entry["key"]
    parts = [p for p in path.split("/") if p and p != "-"]
    return "/".join(parts[:3]) if parts else "(bucket root)"


def tier(op: str) -> int:
    return 1 if TIER1_OPS.search(op) else 2


def report(entries: list[dict]) -> dict:
    by_tier = collections.Counter(tier(e["op"]) for e in entries)
    cost = by_tier[1] * TIER1_PRICE + by_tier[2] * TIER2_PRICE
    return {
        "requests": len(entries),
        "tier1": by_tier[1],
        "tier2": by_tier[2],
        "cost_usd": cost,
        "by_op": collections.Counter(e["op"] for e in entries).most_common(8),
        "by_prefix_tier1": collections.Counter(
            prefix_of(e) for e in entries if tier(e["op"]) == 1
        ).most_common(10),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="UTC, e.g. 2026-10-01T12:00:00")
    p.add_argument("--end", required=True)
    p.add_argument("--profile", default="checkyouraqi")
    args = p.parse_args()
    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC)

    s = Settings()
    s3 = boto3.Session(profile_name=args.profile, region_name=s.aws_region).client("s3")
    account = boto3.Session(profile_name=args.profile).client("sts").get_caller_identity()
    bucket = f"checkyouraqi-logs-{account['Account']}"
    entries = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="data-bucket/"):
        for obj in page.get("Contents", []):
            body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read().decode()
            for line in body.splitlines():
                e = parse_line(line)
                if e and start <= e["time"] < end:
                    entries.append(e)
    r = report(entries)
    print(
        f"window {start:%H:%M:%S}–{end:%H:%M:%S} UTC: {r['requests']} requests "
        f"(tier1 {r['tier1']}, tier2 {r['tier2']}) ≈ ${r['cost_usd']:.4f}"
    )
    print("top operations:", r["by_op"])
    print("tier-1 requests by prefix:")
    for prefix, n in r["by_prefix_tier1"]:
        print(f"   {n:6}  {prefix}")


if __name__ == "__main__":
    main()
