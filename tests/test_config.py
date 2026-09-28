"""Integrity checks on the hand-maintained config files."""

import yaml

from ingestion.settings import CONFIG_DIR


def test_every_station_is_in_exactly_one_zone():
    stations = {
        s["location_id"]
        for s in yaml.safe_load((CONFIG_DIR / "stations.yaml").read_text())["stations"]
    }
    zoned = [
        sid
        for z in yaml.safe_load((CONFIG_DIR / "zones.yaml").read_text())["zones"]
        for sid in z["stations"]
    ]
    assert len(zoned) == len(set(zoned)), "a station is in more than one zone"
    assert set(zoned) == stations
