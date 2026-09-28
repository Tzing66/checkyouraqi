"""Land FIRMS fire detections in bronze: bronze/firms/dt=YYYY-MM-DD/<source>.csv

One file per (day, source) so reruns overwrite cleanly. When a day later moves from NRT to
the SP archive, both files can exist for it; silver must prefer `_SP` over `_NRT` per day
and sensor to avoid double counting.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from ingestion.firms.client import SENSORS, Availability, FirmsClient, source_for
from ingestion.writers import Writer


def extract_fires(
    client: FirmsClient,
    writer: Writer,
    day: date,
    bbox: Sequence[float],
    availability: dict[str, Availability],
    sensors: Sequence[str] = SENSORS,
) -> dict[str, int]:
    """Returns {source: detections} for the files written (empty days still get a file)."""
    written = {}
    for sensor in sensors:
        source = source_for(sensor, day, availability)
        if source is None:
            continue
        got = client.fires(source, bbox, day)
        writer.put_text(f"bronze/firms/dt={day:%Y-%m-%d}/{source}.csv", got.text)
        written[source] = len(got.points)
    return written
