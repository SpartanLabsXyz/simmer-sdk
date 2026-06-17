"""
Region filtering logic for mil-aircraft cluster detection.

Loads regions from YAML, filters aircraft list by bounding box, and returns
per-region cluster state.
"""

import json
import os


def load_regions(yaml_path=None):
    """Load region definitions from regions.yaml."""
    if yaml_path is None:
        yaml_path = os.path.join(os.path.dirname(__file__), "regions.yaml")

    regions = []
    current_region = None
    in_regions = False

    with open(yaml_path, "r", encoding="utf-8") as handle:
        lines = handle.readlines()

    for line in lines:
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("regions:"):
            in_regions = True
            continue

        if not in_regions:
            continue

        if stripped.startswith("- name:"):
            if current_region:
                regions.append(current_region)
            current_region = {"name": stripped.split(":", 1)[1].strip().strip('"')}
            continue

        if current_region is None:
            continue

        if stripped.startswith("lat_min:"):
            current_region["lat_min"] = float(stripped.split(":", 1)[1].strip())
        elif stripped.startswith("lat_max:"):
            current_region["lat_max"] = float(stripped.split(":", 1)[1].strip())
        elif stripped.startswith("lon_min:"):
            current_region["lon_min"] = float(stripped.split(":", 1)[1].strip())
        elif stripped.startswith("lon_max:"):
            current_region["lon_max"] = float(stripped.split(":", 1)[1].strip())
        elif stripped.startswith("cluster_threshold:"):
            current_region["cluster_threshold"] = int(stripped.split(":", 1)[1].strip())
        elif stripped.startswith("keywords:"):
            kw_str = stripped.split(":", 1)[1].strip()
            current_region["keywords"] = json.loads(kw_str.replace("'", '"'))

    if current_region:
        regions.append(current_region)

    return regions


def filter_aircraft_by_regions(aircraft, regions):
    """Filter aircraft into regions by bounding box."""
    result = {}

    for region in regions:
        lat_min = region["lat_min"]
        lat_max = region["lat_max"]
        lon_min = region["lon_min"]
        lon_max = region["lon_max"]
        threshold = region["cluster_threshold"]

        matched_hexes = []
        matched_aircraft = []
        for ac in aircraft:
            lat = ac.get("lat")
            lon = ac.get("lon")
            if lat is None or lon is None:
                continue
            if lat_min <= float(lat) <= lat_max and lon_min <= float(lon) <= lon_max:
                matched_hexes.append(ac.get("hex", "unknown"))
                matched_aircraft.append(ac)

        count = len(matched_hexes)
        result[region["name"]] = {
            "count": count,
            "fired": count >= threshold,
            "cluster_threshold": threshold,
            "aircraft_hexes": matched_hexes,
            "aircraft": matched_aircraft,
            "keywords": region.get("keywords", []),
        }

    return result
