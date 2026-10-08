import pytest

from app import photon


class _Response:
    status_code = 200

    def __init__(self, features):
        self._features = features

    def raise_for_status(self):
        pass

    def json(self):
        return {"features": self._features}


def _feature(name, lat, lon, **props):
    return {"geometry": {"coordinates": [lon, lat]}, "properties": {"name": name, **props}}


def test_search_drops_far_matches_labels_them_and_caches(monkeypatch):
    calls = []

    def fake_get(url, params, headers, timeout):
        calls.append(params)
        return _Response(
            [
                _feature("Orobia climbing", 45.695, 9.62, city="Curno", country="Italy"),
                _feature("Orobia", 3.11, 30.9, country="Uganda"),
            ]
        )

    monkeypatch.setattr(photon.httpx, "get", fake_get)
    photon._search.cache_clear()
    found = photon.search("orobio climbing", (45.8528, 9.4031))
    assert found == [{"name": "Orobia climbing, Curno, Italy", "lat": 45.695, "lon": 9.62}]
    # Same text from about the same spot is served from the cache: autocomplete repeats a lot.
    photon.search("orobio climbing ", (45.8531, 9.4029))
    assert len(calls) == 1


def test_geocode_passes_coordinates_through_and_reports_missing_places(monkeypatch):
    monkeypatch.setattr(photon.httpx, "get", lambda *args, **kwargs: _Response([]))
    photon._search.cache_clear()
    assert photon.geocode("45.1,9.2") == {"name": "45.1,9.2", "lat": 45.1, "lon": 9.2}
    with pytest.raises(ValueError, match="within 200 km"):
        photon.geocode("Nowhere", near=(45.0, 9.0))
