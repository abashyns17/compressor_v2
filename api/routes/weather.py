"""
Weather route — blended ambient temperature profile builder.

GET /weather/ambient-profile?days=90

Merges:
  - weather3 /api/forecast (days 0–15, deterministic)
  - weather3 /api/scenarios (days 16+, P10/P50/P90 climatology)

Returns central_profile, band_low (P10), band_high (P90) arrays,
each entry as {day, temp_c, temp_f}.  All temps in both °C and °F
since the projector uses °F internally.
"""

import httpx
from fastapi import APIRouter, HTTPException, Query
from core.settings import get_settings

router = APIRouter(prefix="/weather", tags=["weather"])

_F_PER_C = 9.0 / 5.0


def _c_to_f(c: float) -> float:
    return c * _F_PER_C + 32.0


def _f_to_c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def _current_percentile(current_c: float, p10_c: float, p50_c: float, p90_c: float) -> str:
    if current_c < p10_c:
        return "cold_below_p10"
    elif current_c < p50_c:
        return "cool_p10_p50"
    elif current_c < p90_c:
        return "warm_p50_p90"
    else:
        return "hot_above_p90"


@router.get("/ambient-profile")
def get_ambient_profile(days: int = Query(90, ge=1, le=365)):
    """
    Build a blended ambient temperature profile.

    Days 0–15  : deterministic forecast (all three bands identical — no uncertainty).
    Days 16+   : climatology P10 / P50 / P90 (band opens from day 16).

    Returns temp in both °C and °F.
    """
    settings = get_settings()
    base_url = settings.weather_service_url.rstrip("/")
    location = settings.weather_location

    # ── Fetch forecast (days 0–15) ─────────────────────────────────────────────
    try:
        forecast_resp = httpx.get(
            f"{base_url}/api/forecast",
            params={"days": 16, "location_name": location},
            timeout=10.0,
        )
        forecast_resp.raise_for_status()
        forecast_data = forecast_resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Weather service unreachable: {exc}")

    # ── Fetch scenarios (full projection window) ───────────────────────────────
    try:
        scenario_resp = httpx.get(
            f"{base_url}/api/scenarios",
            params={"days": days, "location_name": location, "percentiles": "10,50,90"},
            timeout=10.0,
        )
        scenario_resp.raise_for_status()
        scenario_data = scenario_resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Weather service unreachable: {exc}")

    # ── Parse forecast entries → {day_index: temp_c} ──────────────────────────
    forecast_entries = forecast_data.get("forecast", [])
    forecast_reliable_days = forecast_data.get("forecast_reliable_days", 7)
    forecast_days = len(forecast_entries)
    current_temp_c = forecast_data.get("current_temp_c", None)
    if current_temp_c is None and forecast_entries:
        current_temp_c = forecast_entries[0].get("temp_c", 15.0)

    forecast_by_day: dict = {}
    for entry in forecast_entries:
        d = entry.get("day", entry.get("day_index", 0))
        forecast_by_day[int(d)] = entry.get("temp_c", 15.0)

    # ── Parse scenario percentiles → {day_index: {p10, p50, p90}} ─────────────
    scenario_entries = scenario_data.get("scenarios", scenario_data.get("daily", []))
    scenario_by_day: dict = {}
    for entry in scenario_entries:
        d = int(entry.get("day", entry.get("day_index", 0)))
        scenario_by_day[d] = {
            "p10": entry.get("median_p10", entry.get("p10", current_temp_c or 10.0)),
            "p50": entry.get("median_p50", entry.get("p50", current_temp_c or 15.0)),
            "p90": entry.get("median_p90", entry.get("p90", current_temp_c or 20.0)),
        }

    # Determine current percentile position using day-0 climatology if available
    if 0 in scenario_by_day and current_temp_c is not None:
        s0 = scenario_by_day[0]
        current_percentile = _current_percentile(
            current_temp_c, s0["p10"], s0["p50"], s0["p90"]
        )
    else:
        current_percentile = "unknown"

    # ── Build merged profile arrays ────────────────────────────────────────────
    central_profile = []
    band_low = []
    band_high = []

    for day_idx in range(days):
        if day_idx < forecast_days and day_idx in forecast_by_day:
            # Forecast zone: all three bands identical
            tc = forecast_by_day[day_idx]
            tf = round(_c_to_f(tc), 1)
            tc = round(tc, 1)
            central_profile.append({"day": day_idx, "temp_c": tc, "temp_f": tf})
            band_low.append({"day": day_idx, "temp_c": tc, "temp_f": tf})
            band_high.append({"day": day_idx, "temp_c": tc, "temp_f": tf})
        else:
            # Climatology zone: use nearest available scenario entry
            nearest = min(
                (k for k in scenario_by_day if k <= day_idx),
                key=lambda k: day_idx - k,
                default=None,
            )
            if nearest is None:
                # Fall back to forecast last value or a default
                tc_fallback = forecast_by_day.get(forecast_days - 1, 15.0)
                p10_c = tc_fallback
                p50_c = tc_fallback
                p90_c = tc_fallback
            else:
                s = scenario_by_day[nearest]
                p10_c = s["p10"]
                p50_c = s["p50"]
                p90_c = s["p90"]

            central_profile.append({
                "day": day_idx,
                "temp_c": round(p50_c, 1),
                "temp_f": round(_c_to_f(p50_c), 1),
            })
            band_low.append({
                "day": day_idx,
                "temp_c": round(p10_c, 1),
                "temp_f": round(_c_to_f(p10_c), 1),
            })
            band_high.append({
                "day": day_idx,
                "temp_c": round(p90_c, 1),
                "temp_f": round(_c_to_f(p90_c), 1),
            })

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
