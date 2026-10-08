from app import scenery

LAT0 = 45.0


def _route_north(km: float) -> list:
    """Straight route going north from (45.0, 9.0), one point every ~100 m."""
    return [[LAT0 + i * 0.0009, 9.0, 200] for i in range(int(km * 10) + 1)]


def _lake_east_of_first_km() -> dict:
    """A closed way 50-500 m east of the route, along its first ~1 km."""
    ring = [(45.0, 9.00065), (45.0, 9.0064), (45.009, 9.0064), (45.009, 9.00065), (45.0, 9.00065)]
    return {"type": "way", "tags": {"natural": "water"}, "geometry": [{"lat": a, "lon": b} for a, b in ring]}


def _shapes(elements: list) -> dict:
    shapes = {"water": [], "forest": [], "views": []}
    for el in elements:
        shapes[scenery._category(el.get("tags", {}))] += scenery._shapes(el, LAT0)
    return shapes


def test_counts_km_along_water_only_where_close():
    result = scenery.measure(_route_north(3), _shapes([_lake_east_of_first_km()]), LAT0)
    assert 0.9 <= result["water_km"] <= 1.3
    assert result["forest_km"] == 0.0


def test_route_inside_forest_counts_even_far_from_edge():
    ring = [(44.99, 8.98), (44.99, 9.02), (45.05, 9.02), (45.05, 8.98), (44.99, 8.98)]
    forest = {"type": "way", "tags": {"landuse": "forest"}, "geometry": [{"lat": a, "lon": b} for a, b in ring]}
    result = scenery.measure(_route_north(3), _shapes([forest]), LAT0)
    assert result["forest_km"] >= 2.9


def test_viewpoints_and_clipped_geometry():
    view_near = {"type": "node", "tags": {"tourism": "viewpoint"}, "lat": 45.001, "lon": 9.002}
    view_far = {"type": "node", "tags": {"tourism": "viewpoint"}, "lat": 45.001, "lon": 9.05}
    river = {
        "type": "way",
        "tags": {"waterway": "river"},
        "geometry": [None, {"lat": 45.0, "lon": 9.0005}, {"lat": 45.0045, "lon": 9.0005}, None],
    }
    result = scenery.measure(_route_north(2), _shapes([view_near, view_far, river]), LAT0)
    assert result["viewpoints"] == 1
    assert result["water_km"] > 0
