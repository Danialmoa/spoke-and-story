from datetime import UTC, datetime, timedelta

import httpx

URL = "https://api.open-meteo.com/v1/forecast"
DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max"
HOURLY = "temperature_2m,precipitation_probability,precipitation,weather_code,wind_speed_10m"
# WMO weather codes up to each number, in words the guide can say.
SKY = [
    (0, "clear"),
    (2, "partly cloudy"),
    (3, "cloudy"),
    (48, "foggy"),
    (57, "drizzle"),
    (67, "rain"),
    (77, "snow"),
    (82, "rain showers"),
    (86, "snow showers"),
]


def forecast(lat: float, lon: float) -> list[dict]:
    r = httpx.get(
        URL,
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


def sky(code: int) -> str:
    return next((words for top, words in SKY if code <= top), "thunderstorms")


def at(lat: float, lon: float, when: datetime) -> dict:
    """The weather in the hour of `when` (an aware datetime) and that day's sunset, in the place's local time."""
    r = httpx.get(
        URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "hourly": HOURLY,
            "daily": "sunset",
            "timezone": "auto",
            "forecast_days": 3,
        },
        timeout=5,
    )
    r.raise_for_status()
    d = r.json()
    local = when.astimezone(UTC).replace(tzinfo=None) + timedelta(seconds=d["utc_offset_seconds"])
    hourly = d["hourly"]
    i = hourly["time"].index(local.strftime("%Y-%m-%dT%H:00"))
    sunset = d["daily"]["sunset"][d["daily"]["time"].index(local.strftime("%Y-%m-%d"))]
    return {
        "day": local.strftime("%A"),
        "time": local.strftime("%H:%M"),
        "temp_c": round(hourly["temperature_2m"][i]),
        "rain_chance_pct": hourly["precipitation_probability"][i],
        "rain_mm": hourly["precipitation"][i],
        "wind_kmh": round(hourly["wind_speed_10m"][i]),
        "sky": sky(hourly["weather_code"][i]),
        "sunset": sunset[11:],
        "after_sunset": local.strftime("%Y-%m-%dT%H:%M") > sunset,
    }
