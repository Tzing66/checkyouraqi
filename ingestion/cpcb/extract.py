"""Land the CPCB CAAQMS snapshot in bronze, raw and gzipped, once per hour:

    bronze/cpcb/caaqms/dt=YYYY-MM-DD/hour=HH/feed.xml.gz      (UTC fetch hour)

~43 KB per snapshot for all of India (~0.36 GB/year). Idempotent per hour: a rerun overwrites.
"""

from __future__ import annotations

import gzip
from datetime import UTC, datetime

from ingestion.cpcb.client import CpcbClient
from ingestion.writers import Writer


def snapshot_key(hour: datetime) -> str:
    h = hour.astimezone(UTC)
    return f"bronze/cpcb/caaqms/dt={h:%Y-%m-%d}/hour={h:%H}/feed.xml.gz"


def capture_snapshot(client: CpcbClient, writer: Writer, *, logical_hour: datetime) -> dict:
    snap = client.snapshot()
    # mtime=0: identical feed bytes give identical objects on rerun.
    uri = writer.put_bytes(
        snapshot_key(logical_hour), gzip.compress(snap.xml, mtime=0), "application/gzip"
    )
    return {"uri": uri, "stations": snap.stations, "last_updates": list(snap.last_updates)}
