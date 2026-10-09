import numpy as np

from app import route_stats

# 0.0009 deg of latitude is ~100 m
STEP_DEG = 0.0009


def test_max_grade_on_steady_climb():
    lat = 45.0 + np.arange(11) * STEP_DEG
    ele = np.arange(11) * 5.0
    dist = route_stats.cumulative_m(lat, np.full(11, 9.0))
    assert abs(route_stats.max_grade_pct(dist, ele) - 5.0) < 0.2


def test_max_grade_finds_short_steep_ramp():
    lat = 45.0 + np.arange(21) * STEP_DEG
    ele = np.zeros(21)
    ele[10:13] = [0, 10, 20]
    ele[13:] = 20
    dist = route_stats.cumulative_m(lat, np.full(21, 9.0))
    assert abs(route_stats.max_grade_pct(dist, ele) - 10.0) < 0.5


def test_summarize_converts_ors_feature():
    feature = {
        "geometry": {"coordinates": [[9.0, 45.0, 200.0], [9.0, 45.0009, 210.0], [9.0, 45.0018, 215.0]]},
        "properties": {
            "summary": {"distance": 200.0, "duration": 60.0},
            "ascent": 15.0,
            "extras": {
                "surface": {
                    "summary": [
                        {"value": 3, "amount": 70.0},
                        {"value": 0, "amount": 10.0},
                        {"value": 10, "amount": 20.0},
                    ]
                },
                "waytype": {"summary": [{"value": 1, "amount": 40.0}, {"value": 6, "amount": 60.0}]},
            },
            "segments": [
                {"steps": [{"type": 11, "instruction": "Head north", "way_points": [0, 1]}]},
                {"steps": [{"type": 0, "instruction": "Turn left onto Via Roma", "way_points": [1, 2]}]},
            ],
        },
    }
    route = route_stats.summarize(feature)
    assert route["coords"][1] == [45.0009, 9.0, 210]
    assert (route["distance_km"], route["duration_min"], route["climb_m"]) == (0.2, 1, 15)
    assert (route["paved_pct"], route["quiet_pct"]) == (80, 60)
    assert route["profile"][-1][0] == 0.2
    assert route["steps"][1] == {"index": 1, "type": 0, "text": "Turn left onto Via Roma"}


def test_bike_path_share_counts_only_cycleways():
    feature = {
        "geometry": {"coordinates": [[9.0, 45.0, 200.0], [9.0, 45.0009, 200.0]]},
        "properties": {
            "summary": {"distance": 100.0, "duration": 30.0},
            "extras": {
                "waytype": {
                    "summary": [
                        {"value": 3, "amount": 30.0},
                        {"value": 6, "amount": 25.4},
                        {"value": 2, "amount": 44.6},
                    ]
                }
            },
        },
    }
    route = route_stats.summarize(feature)
    assert (route["bike_path_pct"], route["quiet_pct"]) == (25, 55)
