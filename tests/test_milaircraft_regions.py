import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "polymarket-mil-aircraft-tracker"
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


def test_filter_aircraft_by_region():
    """Aircraft within region bounds are counted correctly."""
    from regions import filter_aircraft_by_regions

    regions = [
        {
            "name": "persian-gulf",
            "lat_min": 24,
            "lat_max": 30,
            "lon_min": 50,
            "lon_max": 60,
            "cluster_threshold": 3,
            "keywords": ["Iran"],
        },
    ]

    aircraft = [
        {"hex": "a1", "lat": 26.5, "lon": 56.2},
        {"hex": "a2", "lat": 27.0, "lon": 55.8},
        {"hex": "a3", "lat": 26.0, "lon": 57.0},
        {"hex": "a4", "lat": 40.0, "lon": 10.0},
        {"hex": "a5", "lat": None, "lon": None},
    ]

    result = filter_aircraft_by_regions(aircraft, regions)
    assert result["persian-gulf"]["count"] == 3
    assert result["persian-gulf"]["fired"] is True
    assert set(result["persian-gulf"]["aircraft_hexes"]) == {"a1", "a2", "a3"}


def test_region_below_threshold_does_not_fire():
    """Region with count < threshold does not fire."""
    from regions import filter_aircraft_by_regions

    regions = [
        {
            "name": "persian-gulf",
            "lat_min": 24,
            "lat_max": 30,
            "lon_min": 50,
            "lon_max": 60,
            "cluster_threshold": 3,
            "keywords": ["Iran"],
        },
    ]

    aircraft = [
        {"hex": "a1", "lat": 26.5, "lon": 56.2},
        {"hex": "a2", "lat": 27.0, "lon": 55.8},
    ]

    result = filter_aircraft_by_regions(aircraft, regions)
    assert result["persian-gulf"]["count"] == 2
    assert result["persian-gulf"]["fired"] is False


def test_load_regions_from_yaml():
    """load_regions() parses regions.yaml correctly."""
    from regions import load_regions

    regions_path = SKILL_DIR / "regions.yaml"
    regions = load_regions(str(regions_path))
    assert len(regions) == 5
    assert regions[0]["name"] == "persian-gulf"
    assert regions[0]["cluster_threshold"] == 3


def test_market_classifier_handles_nullable_text_fields():
    """Market search responses can include nullable text fields."""
    from milaircraft_tracker import is_strike_action_market

    market = {
        "question": "Will there be a military strike on the Korean peninsula?",
        "event_name": None,
        "resolution_criteria": None,
        "description": None,
    }

    assert is_strike_action_market(market, ["Korea"]) is True
