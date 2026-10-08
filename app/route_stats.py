import numpy as np

# ORS extra_info codes: https://giscience.github.io/openrouteservice/api-reference/endpoints/directions/extra-info/
# Many OSM roads have no surface tag, so only surfaces known to be unpaved count against paved_pct.
UNPAVED = {2, 8, 9, 10, 11, 12, 13, 15, 16, 17}  # unpaved, gravels, dirt, ground, ice, sand, woodchips, grass
QUIET = {3, 4, 5, 6}  # street, path, track, cycleway (not state roads or main roads)
GRADE_WINDOW_M = 200


def cumulative_m(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    lat, lon = np.radians(lat), np.radians(lon)
    a = np.sin(np.diff(lat) / 2) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(np.diff(lon) / 2) ** 2
    return np.concatenate([[0.0], np.cumsum(2 * 6_371_000 * np.arcsin(np.sqrt(a)))])


def max_grade_pct(dist_m: np.ndarray, ele: np.ndarray, window_m: float = GRADE_WINDOW_M) -> float:
    # Point-to-point grades on DEM elevation are noisy, so measure over a fixed distance instead.
    j = np.searchsorted(dist_m, dist_m + window_m)
    i = np.nonzero(j < len(dist_m))[0]
    if not len(i):
        return 0.0
    j = j[i]
    return round(float(((ele[j] - ele[i]) / (dist_m[j] - dist_m[i])).max()) * 100, 1)


def share_pct(extra: dict | None, codes: set[int]) -> int:
    if not extra:
        return 0
    return round(sum(s["amount"] for s in extra["summary"] if s["value"] in codes))


def summarize(feature: dict) -> dict:
    """ORS GeoJSON feature -> route stats. Output coords are [lat, lon, ele]."""
    raw = feature["geometry"]["coordinates"]
    lon, lat, ele = np.array(raw, dtype=float).T
    dist = cumulative_m(lat, lon)
    props = feature["properties"]
    extras = props.get("extras", {})
    step = max(1, len(raw) // 300)
    return {
        "distance_km": round(props["summary"]["distance"] / 1000, 1),
        "duration_min": round(props["summary"]["duration"] / 60),
        "climb_m": round(props.get("ascent", 0)),
        "max_grade_pct": max_grade_pct(dist, ele),
        "paved_pct": 100 - share_pct(extras.get("surface"), UNPAVED),
        "quiet_pct": share_pct(extras.get("waytype"), QUIET),
        "coords": [[round(p[1], 5), round(p[0], 5), round(p[2])] for p in raw],
        "profile": [[round(float(d) / 1000, 2), round(float(e))] for d, e in zip(dist[::step], ele[::step])],
        # index = coordinate where the maneuver happens; type = ORS instruction type (0 left, 1 right, ...).
        "steps": [
            {"index": s["way_points"][0], "type": s["type"], "text": s["instruction"]}
            for segment in props.get("segments", [])
            for s in segment["steps"]
        ],
    }
