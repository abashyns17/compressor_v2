"""
Weather route — blended ambient temperature profile builder.

GET /weather/ambient-profile?days=90

Merges:
  - weather3 /api/forecast (days 0–15, deterministic)
  - weather3 /api/scenarios (days 0–N, climatology P10/P50/P90)

Returns central_profile, band_low (P10), band_high (P90) arrays,
each entry as {day, temp_c, temp_f}.
"""

import httpx
from datetime import date, timedelta
from fastapi import APIRouter, HTTPException, Query
from core.settings import get_settings

router = APIRouter(prefix="/weather", tags=["weather"])

_F_PER_C = 9.0 / 5.0


def _c_to_f(c: float) -> float:
    return c * _F_PER_C + 32.0


def _current_percentile(current_c: float, p10_c: float, p50_c: float, p90_c: float) -> str:
    if current_c < p10_c:
        return "cold_below_p10"
    elif current_c < p50_c:
        return "cool_p10_p50"
    elif current_c < p90_c:
        return "warm_p50_p90"
    else:
        return "hot_above_p90"


def _build_day_map(readings: list, start: date) -> dict:
    """Convert {date, temperature_c} readings to {day_offset: temp_c}."""
    result = {}
    for r in readings:
        try:
            d = date.fromisoformat(r["date"])
            offset = (d - start).days
            result[offset] = r["temperature_c"]
        except (KeyError, ValueError):
            pass
    return result


@router.get("/ambient-profile")
def get_ambient_profile(days: int = Query(90, ge=1, le=365)):
    """
    Build a blended ambient temperature profile for the next N days.

    Days 0–forecast_days  : deterministic forecast (all three bands identical).
    Days after            : climatology P10 / P50 / P90 band opens up.
    """
    settings = get_settings()
    base_url = settings.weather_service_url.rstrip("/")
    location = settings.weather_location

    today = date.today()
    end_date = today + timedelta(days=days)

    # ── Fetch forecast (days 0–15) ─────────────────────────────────────────────
    try:
        forecast_resp = httpx.get(
            f"{base_url}/api/forecast",
            params={"days": 16, "location_name": location},
            timeout=15.0,
        )
        forecast_resp.raise_for_status()
        forecast_data = forecast_resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Weather service unreachable: {exc}")

    # ── Fetch climatology scenarios ────────────────────────────────────────────
    try:
        scenario_resp = httpx.get(
            f"{base_url}/api/scenarios",
            params={
                "start_date": today.isoformat(),
                "end_date": end_date.isoformat(),
                "location_name": location,
            },
            timeout=15.0,
        )
        scenario_resp.raise_for_status()
        scenario_data = scenario_resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Weather service unreachable: {exc}")

    # ── Parse forecast ─────────────────────────────────────────────────────────
    # /api/forecast returns {readings: [{date, temperature_c}], forecast_reliable_days, ...}
    forecast_readings = forecast_data.get("readings", [])
    forecast_reliable_days = forecast_data.get("forecast_reliable_days", 7)
    forecast_days = len(forecast_readings)

    forecast_map = _build_day_map(forecast_readings, today)

    # Current temp — first reading or explicit field
    current_temp_c = forecast_data.get("current_temp_c") or (
        forecast_readings[0]["temperature_c"] if forecast_readings else 15.0
    )

    # ── Parse climatology scenarios ────────────────────────────────────────────
    # /api/scenarios returns {scenarios: [{name, percentile, readings: [{date, temperature_c}]}]}
    scenarios_list = scenario_data.get("scenarios", [])

    # Find P10, P50, P90 by percentile field
    p10_map: dict = {}
    p50_map: dict = {}
    p90_map: dict = {}

    for sc in scenarios_list:
        pct = sc.get("percentile")
        rdgs = sc.get("readings", [])
        if pct == 10:
            p10_map = _build_day_map(rdgs, today)
        elif pct == 50:
            p50_map = _build_day_map(rdgs, today)
        elif pct == 90:
            p90_map = _build_day_map(rdgs, today)

    # Fall back to baseline_mean if no P50
    if not p50_map:
        for sc in scenarios_list:
            if sc.get("name") == "baseline_mean":
                p50_map = _build_day_map(sc.get("readings", []), today)
                break

    # Determine current percentile
    p10_day0 = p10_map.get(0, current_temp_c)
    p50_day0 = p50_map.get(0, current_temp_c)
    p90_day0 = p90_map.get(0, current_temp_c)
    current_percentile = _current_percentile(current_temp_c, p10_day0, p50_day0, p90_day0)

    # ── Build merged profile arrays ────────────────────────────────────────────
    central_profile = []
    band_low = []
    band_high = []

    for day_idx in range(days):
        if day_idx < forecast_days and day_idx in forecast_map:
            # Deterministic forecast zone — all three bands identical
            tc = forecast_map[day_idx]
            tf = round(_c_to_f(tc), 1)
            tc = round(tc, 1)
            central_profile.append({"day": day_idx, "temp_c": tc, "temp_f": tf})
            band_low.append({"day": day_idx, "temp_c": tc, "temp_f": tf})
            band_high.append({"day": day_idx, "temp_c": tc, "temp_f": tf})
        else:
            # Climatology zone — band opens up
            # Find nearest available day in scenario maps
            def nearest(m: dict, idx: int) -> float:
                if idx in m:
                    return m[idx]
                # Try nearest available key
                keys = [k for k in m if k <= idx]
                if keys:
                    return m[max(keys)]
                keys = list(m.keys())
                return m[min(keys)] if keys else current_temp_c

            p10_c = nearest(p10_map, day_idx)
            p50_c = nearest(p50_map, day_idx)
            p90_c = nearest(p90_map, day_idx)

            central_profile.append({"day": day_idx, "temp_c": round(p50_c, 1), "temp_f": round(_c_to_f(p50_c), 1)})
            band_low.append({"day": day_idx,         "temp_c": round(p10_c, 1), "temp_f": round(_c_to_f(p10_c), 1)})
            band_high.append({"day": day_idx,        "temp_c": round(p90_c, 1), "temp_f": round(_c_to_f(p90_c), 1)})

    return {
        "location": location,
        "days": days,
        "forecast_reliable_days": forecast_reliable_days,
        "forecast_days": forecast_days,
        "central_profile": central_profile,
        "band_low": band_low,
        "band_high": band_high,
        "current_temp_c": round(current_temp_c, 1) if current_temp_c is not None else None,
        "current_temp_f": round(_c_to_f(current_temp_c), 1) if current_temp_c is not None else None,
        "current_percentile": current_percentile,
        "profile_source": f"forecast_{forecast_days}d+climatology_p10_p90",
    }
