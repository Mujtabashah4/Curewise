"""Find nearby hospitals via the public OpenStreetMap Overpass API.

This is a user-facing convenience for emergencies. It does NOT read our own
database (the hospital-management tables were removed); it queries live OSM.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

# Public Overpass instances, tried in order. The main one is frequently
# saturated (504s), so a mirror is a meaningful availability win for a feature
# someone may be using in an emergency.
_OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

# OSM's usage policy requires a descriptive User-Agent, and overpass-api.de
# answers the default python-requests one with 406 Not Acceptable.
_HEADERS = {
    "User-Agent": "CureWise/2.0 (patient hospital finder; contact: ops@curewise.example)"
}

# Per-endpoint. Two endpoints means the worst case a user waits is 2x this;
# keep it tight — someone using this is in a hurry by definition.
_TIMEOUT = 15


def _address(tags: dict) -> str:
    street = " ".join(
        p for p in (tags.get("addr:housenumber"), tags.get("addr:street")) if p
    )
    parts = [street, tags.get("addr:city")]
    return ", ".join(p for p in parts if p) or "Address not available"


def _parse(payload: dict, limit: int) -> list[dict]:
    hospitals: list[dict] = []
    for element in payload.get("elements", []):
        tags = element.get("tags") or {}
        # Nodes carry lat/lon directly; ways and relations carry a computed
        # `center` because of `out center`.
        center = element.get("center") or {}
        lat = element.get("lat", center.get("lat"))
        lon = element.get("lon", center.get("lon"))
        if lat is None or lon is None:
            continue
        hospitals.append(
            {
                "name": tags.get("name") or "Unnamed hospital",
                "address": _address(tags),
                "lat": lat,
                "lng": lon,
            }
        )
        if len(hospitals) >= limit:
            break
    return hospitals


def find_nearby_hospitals(
    lat: float, lng: float, radius_m: int = 10000, limit: int = 15
) -> list[dict]:
    # `nwr` = nodes + ways + relations. Most large hospitals are mapped as
    # polygons, so the old node-only query silently missed them — in many
    # cities it returned almost nothing.
    query = (
        "[out:json][timeout:25];"
        f'nwr["amenity"="hospital"](around:{radius_m},{lat},{lng});'
        "out tags center 60;"
    )

    last_exc: Exception | None = None
    for url in _OVERPASS_ENDPOINTS:
        try:
            response = requests.post(
                url, data={"data": query}, headers=_HEADERS, timeout=_TIMEOUT
            )
            response.raise_for_status()
            return _parse(response.json(), limit)
        except requests.RequestException as exc:
            logger.warning("Overpass endpoint %s failed: %s", url, exc)
            last_exc = exc

    assert last_exc is not None
    raise last_exc
