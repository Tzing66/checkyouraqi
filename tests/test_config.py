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


def test_dbt_freshness_vars_match_config():
    """dbt can't read config/freshness.yaml, so it mirrors it as vars; they must not drift."""
    fresh = yaml.safe_load((CONFIG_DIR / "freshness.yaml").read_text())
    dbt_vars = yaml.safe_load((CONFIG_DIR.parent / "dbt" / "dbt_project.yml").read_text())["vars"]
    assert dbt_vars["freshness_live_max_hours"] == fresh["station"]["live_max_hours"]
    assert dbt_vars["freshness_delayed_max_hours"] == fresh["station"]["delayed_max_hours"]
    assert dbt_vars["freshness_inactive_max_days"] == fresh["station"]["inactive_max_days"]
    assert dbt_vars["feed_outage_min_share_not_live"] == fresh["feed"]["outage_min_share_not_live"]
    assert (
        dbt_vars["feed_degraded_min_share_not_live"] == fresh["feed"]["degraded_min_share_not_live"]
    )


def test_dbt_seeds_are_in_sync_with_config():
    from scripts.export_seeds import SEEDS_DIR, build

    for name, text in build().items():
        assert (SEEDS_DIR / name).read_text() == text, f"{name} is stale: run export_seeds.py"


def test_every_dbt_model_has_exactly_one_cadence_tag():
    """Each model must belong to one schedule (hourly/daily/weekly/predict): dbt tags are
    additive, so a missing or doubled tag silently changes what runs (and what it costs)."""
    project = yaml.safe_load((CONFIG_DIR.parent / "dbt" / "dbt_project.yml").read_text())
    models_cfg = project["models"]["checkyouraqi"]
    cadences = {"hourly", "daily", "weekly", "predict"}
    for folder in ("staging", "intermediate", "marts"):
        assert "+tags" not in models_cfg[folder], f"no folder-wide tags in {folder}"
        for sql in (CONFIG_DIR.parent / "dbt" / "models" / folder).glob("*.sql"):
            tags = models_cfg[folder].get(sql.stem, {}).get("+tags", [])
            assert len(set(tags) & cadences) == 1, f"{sql.stem} needs exactly one cadence tag"
