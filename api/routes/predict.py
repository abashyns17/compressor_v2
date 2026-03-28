"""
Projection routes — forward simulation and what-if scenarios.
"""

import httpx
from fastapi import APIRouter, HTTPException
from api.models import ProjectionRequest, CompareRequest
from simulation.projector import project, compare_scenarios
from analysis.envelope_validator import validate_scenario
import api.routes.state as state_module

router = APIRouter(prefix="/predict", tags=["predict"])


def _try_fetch_ambient_profile(days: int) -> tuple:
    """
    Attempt to auto-fetch the blended ambient profile from the weather service.
    Returns (profile_list, source_label) or (None, 'manual') on failure.
    """
    from core.settings import get_settings
    settings = get_settings()
    if settings.ambient_source == "manual":
        return None, "manual"
    base_url = settings.weather_service_url.rstrip("/")
    try:
        resp = httpx.get(
            f"http://127.0.0.1:8000/weather/ambient-profile",
            params={"days": days},
            timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()
        profile = data.get("central_profile", [])
        source = data.get("profile_source", "weather_service")
        return profile, source
    except Exception:
        return None, "manual_fallback"


@router.post("/project")
def run_projection(req: ProjectionRequest):
    """
    Project machine state forward N days under given conditions.

    This is the core what-if engine.
    Examples:
      - {"days": 30} — project current state forward 30 days unchanged
      - {"days": 30, "setpoint_psi": 125} — what if we increase pressure?
      - {"days": 30, "load_pct": 90} — what if load increases to 90%?
      - {"days": 60, "defer_services": {"fluid_filter": 60}} — defer filter 60 days?
      - {"days": 730, "defer_services": {"shaft_seal": 730}} — defer shaft seal 2 years?
    """
    if state_module._state is None:
        raise HTTPException(status_code=400, detail="No scenario loaded")

    # Validate proposed conditions before projecting
    v = validate_scenario(
        setpoint_psi=req.setpoint_psi,
        ambient_f=req.ambient_f,
        load_pct=req.load_pct,
    )
    if not v["valid"]:
        raise HTTPException(status_code=400, detail={"errors": v["errors"]})

    # Ambient profile: use caller-supplied, or auto-fetch if ambient_source != manual
    ambient_profile = req.ambient_profile
    ambient_source_used = "request"
    if ambient_profile is None:
        ambient_profile, ambient_source_used = _try_fetch_ambient_profile(int(req.days))

    result = project(
        state=state_module._state,
        days=req.days,
        load_pct=req.load_pct,
        ambient_f=req.ambient_f,
        setpoint_psi=req.setpoint_psi,
        defer_services=req.defer_services,
        ambient_profile=ambient_profile,
    )

    response = result.to_dict()
    response["envelope_warnings"] = v.get("warnings", [])
    response["ambient_source_used"] = ambient_source_used
    return response


@router.post("/compare")
def compare_projections(req: CompareRequest):
    """
    Run multiple projection scenarios side by side and compare outcomes.

    Example — OEM vs aftermarket part, vs no action:
    {
      "days": 14,
      "scenarios": [
        {"label": "no_action", "setpoint_psi": 235},
        {"label": "reduced_pressure", "setpoint_psi": 210},
        {"label": "defer_service", "defer_services": {"shaft_seal": 14}}
      ]
    }
    """
    if state_module._state is None:
        raise HTTPException(status_code=400, detail="No scenario loaded")

    return compare_scenarios(
        state=state_module._state,
        scenarios=req.scenarios,
        days=req.days,
    )


@router.get("/envelope")
def get_envelope(days: int = 30, resolution: int = 5):
    """
    Sweep load_pct × ambient_f grid and return safe operating envelope.

    ~300 lightweight projections at resolution=5. Identifies the binding
    constraint and how far current operating point is from the risk zone.
    """
    if state_module._state is None:
        raise HTTPException(status_code=400, detail="No scenario loaded")

    from simulation.envelope_explorer import find_safe_envelope
    return find_safe_envelope(state=state_module._state, days=days, resolution=resolution)


@router.post("/optimize")
def optimize_maintenance(body: dict):
    """
    Find the optimal maintenance bundle given an outage window.

    Body: {"outage_hours": 8, "days": 90}

    Enumerates all subsets of degraded components (<60% health),
    resets each subset to 100%, runs projections, and ranks by
    days_to_first_fault improvement. Returns top 3 bundles.
    """
    if state_module._state is None:
        raise HTTPException(status_code=400, detail="No scenario loaded")

    outage_hours = float(body.get("outage_hours", 8.0))
    days = int(body.get("days", 90))

    from simulation.optimizer import optimize_maintenance as _optimize
    return _optimize(state=state_module._state, days=days, outage_hours=outage_hours)


@router.get("/component/{component_id}")
def project_component(
    component_id: str,
    days: float = 30.0,
):
    """
    Single component forward projection.
    'What happens to this specific component over the next N days?'
    """
    if state_module._state is None:
        raise HTTPException(status_code=400, detail="No scenario loaded")

    if component_id not in state_module._state.components:
        raise HTTPException(status_code=404, detail={
            "error": f"Unknown component: {component_id}",
            "valid": list(state_module._state.components.keys()),
        })

    component = state_module._state.components[component_id]
    from core.thermodynamics import get_load_multiplier, get_ambient_multiplier
    from core.constants import DEGRADATION_FAULT_PCT

    load_mult = get_load_multiplier(state_module._state.load_pct)
    temp_mult = get_ambient_multiplier(state_module._state.ambient_f)

    trajectory = []
    health = component.health_pct
    rate = component.base_degradation_rate * load_mult * temp_mult

    for day in range(int(days) + 1):
        trajectory.append({
            "day": day,
            "health_pct": round(max(0, health - (rate / 100) * day * 24), 1),
        })

    fault_day = component.hours_until_fault(load_mult, temp_mult)

    return {
        "component": component_id,
        "name": component.name,
        "current_health_pct": round(component.health_pct, 1),
        "fault_threshold_pct": DEGRADATION_FAULT_PCT,
        "days_to_fault": round(fault_day / 24, 1) if fault_day else None,
        "effective_rate_pct_per_100hrs": round(rate, 3),
        "load_multiplier": load_mult,
        "temp_multiplier": temp_mult,
        "trajectory": trajectory,
    }
