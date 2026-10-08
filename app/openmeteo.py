import httpx

DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max"


def forecast(lat: float, lon: float) -> list[dict]:
    r = httpx.get(
        "https://api.open-meteo.com/v1/forecast",
        params={"latitude": lat, "longitude": lon, "daily": DAILY, "timezone": "auto", "forecast_days": 7},
        timeout=10,
    )
    r.raise_for_status()
    d = r.json()["daily"]
    return [
        {
            "date": d["time"][i],
            "temp_max": round(d["temperature_2m_max"][i]),
            "temp_min": round(d["temperature_2m_min"][i]),
            "rain_mm": d["precipitation_sum"][i],
            "rain_prob": d["precipitation_probability_max"][i],
            "wind_kmh": round(d["wind_speed_10m_max"][i]),
            "weather_code": d["weather_code"][i],
        }
        for i in range(len(d["time"]))
    ]
