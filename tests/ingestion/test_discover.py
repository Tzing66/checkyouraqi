import json
import shutil
from datetime import UTC, datetime

import yaml

from ingestion.openaq import discover
from ingestion.openaq.client import Fetched
from ingestion.openaq.models import LatestReading, Location
from ingestion.settings import CONFIG_DIR
from ingestion.writers import LocalWriter

NOW = datetime(2026, 9, 28, 6, tzinfo=UTC)


class FakeClient:
    def __init__(self, load_fixture):
        self.fx = load_fixture
        self.latest_calls = []

    def locations(self, bbox):
        page = self.fx("openaq/locations_bbox.json")
        return Fetched([Location.model_validate(r) for r in page["results"]], [page])

    def latest(self, location_id):
        self.latest_calls.append(location_id)
        page = self.fx("openaq/location_235_latest.json")
        return Fetched([LatestReading.model_validate(r) for r in page["results"]], [page])


def _config(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    shutil.copy(CONFIG_DIR / "cities.yaml", cfg / "cities.yaml")
    return cfg


def test_run_lands_bronze_and_writes_config(tmp_path, load_fixture):
    cfg = _config(tmp_path)
    client = FakeClient(load_fixture)
    stations = discover.run(client, LocalWriter(tmp_path / "lake"), now=NOW, config_dir=cfg)

    # Only the active candidate costs a /latest call; the 2022 legacy location doesn't.
    assert client.latest_calls == [235]
    assert [s.location_id for s in stations] == [235]

    bronze = tmp_path / "lake/bronze/openaq/locations/dt=2026-09-28"
    assert json.loads((bronze / "locations.json").read_text())[0]["results"][0]["id"] == 235
    assert "235" in json.loads((bronze / "latest.json").read_text())

    doc = yaml.safe_load((cfg / "stations.yaml").read_text())
    assert doc["city_id"] == "delhi_ncr"
    assert doc["stations"][0]["pm25_sensor_id"] == 12235610
    assert "zone" not in doc["stations"][0]

    zones = yaml.safe_load((cfg / "zones.yaml").read_text())
    assert zones["zones"] == [{"id": "delhi_east", "name": "East Delhi", "stations": [235]}]


def test_run_never_overwrites_reviewed_zones(tmp_path, load_fixture):
    cfg = _config(tmp_path)
    (cfg / "zones.yaml").write_text("# hand-reviewed\n")
    discover.run(FakeClient(load_fixture), LocalWriter(tmp_path / "lake"), now=NOW, config_dir=cfg)
    assert (cfg / "zones.yaml").read_text() == "# hand-reviewed\n"
    assert (cfg / "zones.draft.yaml").exists()
