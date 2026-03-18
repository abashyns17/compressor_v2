"""
Projection routes — forward simulation and what-if scenarios.
"""

from fastapi import APIRouter, HTTPException
from api.models import ProjectionRequest, CompareRequest
from simulation.projector import project, compare_scenarios
from analysis.envelope_validator import validate_scenario
import api.routes.state as state_module

router = APIRouter(prefix="/predict", tags=["predict"])


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

    result = project(
        state=state_module._state,
        days=req.days,
        load_pct=req.load_pct,
        ambient_f=req.ambient_f,
        setpoint_psi=req.setpoint_psi,
        defer_services=req.defer_services,
    )

    response = result.to_dict()
    response["envelope_warnings"] = v.get("warnings", [])
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
