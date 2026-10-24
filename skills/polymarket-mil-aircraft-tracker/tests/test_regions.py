"""Offline coverage for region loading and aircraft cluster detection."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from regions import filter_aircraft_by_regions, load_regions  # noqa: E402


def test_load_shipped_regions():
    regions = load_regions()

    assert len(regions) == 5
    assert {region["name"] for region in regions} == {
        "persian-gulf", "diego-garcia", "eastern-mediterranean",
        "korean-peninsula", "south-china-sea",
    }
    for region in regions:
        for bound in ("lat_min", "lat_max", "lon_min", "lon_max"):
            assert isinstance(region[bound], float)
        assert isinstance(region["cluster_threshold"], int)
        assert isinstance(region["keywords"], list)

    gulf = next(region for region in regions if region["name"] == "persian-gulf")
    assert gulf == {
        "name": "persian-gulf",
        "lat_min": 24.0, "lat_max": 30.0,
        "lon_min": 50.0, "lon_max": 60.0,
        "cluster_threshold": 3,
        "keywords": ["Iran", "Hormuz", "Persian Gulf", "IRGC"],
    }


def test_load_custom_regions_with_comments_and_optional_keywords(tmp_path):
    path = tmp_path / "x.yaml"
    path.write_text('''# Custom regions
version: "1.0.0"

regions:
  # A quoted name and single-quoted keywords
  - name: "first"
    lat_min: -2
    lat_max: 2.5
    lon_min: -4.5
    lon_max: 4
    cluster_threshold: 2
    keywords: ['one', 'two']

  # Keywords are optional
  - name: second
    lat_min: 10
    lat_max: 20
    lon_min: 30
    lon_max: 40
    cluster_threshold: 1
''', encoding="utf-8")

    regions = load_regions(path)
    assert regions == [
        {"name": "first", "lat_min": -2.0, "lat_max": 2.5,
         "lon_min": -4.5, "lon_max": 4.0, "cluster_threshold": 2,
         "keywords": ["one", "two"]},
        {"name": "second", "lat_min": 10.0, "lat_max": 20.0,
         "lon_min": 30.0, "lon_max": 40.0, "cluster_threshold": 1},
    ]
    assert filter_aircraft_by_regions([], regions)["second"]["keywords"] == []


@pytest.fixture
def region():
    return {"name": "test", "lat_min": 10.0, "lat_max": 20.0,
            "lon_min": 30.0, "lon_max": 40.0, "cluster_threshold": 2,
            "keywords": ["test market"]}


@pytest.mark.parametrize("lat,lon", [
    (15, 35), (10, 35), (20, 35), (15, 30), (15, 40),
    (10, 30), (20, 40),
])
def test_inside_and_boundary_aircraft_are_included(region, lat, lon):
    aircraft = {"hex": "abc123", "lat": lat, "lon": lon}
    assert filter_aircraft_by_regions([aircraft], [region]) == {
        "test": {"count": 1, "fired": False, "cluster_threshold": 2,
                 "aircraft_hexes": ["abc123"], "aircraft": [aircraft],
                 "keywords": ["test market"]},
    }


@pytest.mark.parametrize("aircraft", [
    {"lat": 9.9, "lon": 35}, {"lat": 20.1, "lon": 35},
    {"lat": 15, "lon": 29.9}, {"lat": 15, "lon": 40.1},
    {"lon": 35}, {"lat": 15}, {},
    {"lat": None, "lon": 35}, {"lat": 15, "lon": None},
])
def test_outside_or_missing_coordinates_are_skipped(region, aircraft):
    result = filter_aircraft_by_regions([aircraft], [region])["test"]
    assert result["count"] == 0
    assert result["aircraft_hexes"] == []
    assert result["aircraft"] == []
    assert result["fired"] is False


def test_missing_hex_uses_unknown(region):
    aircraft = {"lat": 15, "lon": 35}
    result = filter_aircraft_by_regions([aircraft], [region])["test"]
    assert result["aircraft_hexes"] == ["unknown"]
    assert result["aircraft"] == [aircraft]
    assert result["count"] == 1


@pytest.mark.parametrize("count,fired", [(0, False), (1, False), (2, True), (3, True)])
def test_fired_threshold_is_evaluated_per_region(region, count, fired):
    aircraft = [{"hex": f"ac{i}", "lat": 15, "lon": 35} for i in range(count)]
    higher_threshold = dict(region, name="higher", cluster_threshold=4)
    result = filter_aircraft_by_regions(aircraft, [region, higher_threshold])

    assert result["test"]["count"] == count
    assert result["test"]["fired"] is fired
    assert result["test"]["cluster_threshold"] == 2
    assert result["higher"]["count"] == count
    assert result["higher"]["fired"] is False
    assert result["higher"]["cluster_threshold"] == 4
