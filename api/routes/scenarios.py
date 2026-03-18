"""
Scenario routes — load operating scenarios, advance time.
"""

from fastapi import APIRouter, HTTPException
from api.models import ScenarioRequest, AdvanceTimeRequest, OperatingConditionsRequest
from simulation.scenario_engine import build_scenario, list_scenarios, DEMO_NARRATIVES
from api.routes.state import set_state, _state
import api.routes.state as state_module

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


@router.get("/")
def get_available_scenarios():
    return {"scenarios": list_scenarios()}


@router.post("/load")
def load_scenario(req: ScenarioRequest):
    """Load a named scenario as the active machine state."""
    try:
        new_state = build_scenario(req.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    state_module._state = new_state
    set_state(new_state)

    result = {
        "loaded": req.name,
        "summary": new_state.summary(),
    }

    # Attach narrative context if this is a demo scenario
    if req.name in DEMO_NARRATIVES:
        result["narrative"] = DEMO_NARRATIVES[req.name]

    return result


@router.post("/advance")
def advance_time(req: AdvanceTimeRequest):
    """Advance simulation time by N hours, degrading components accordingly."""
    if state_module._state is None:
        raise HTTPException(status_code=400, detail="No scenario loaded")

    state_module._state.advance(req.hours)

    from data.sensor_logger import log_reading, log_components
    reading = state_module._state.compute_sensors()
    summary = state_module._state.summary()
    log_reading(reading.to_dict(), scenario="active")
    log_components(summary["component_health"], scenario="active")

    return {
        "advanced_hours": req.hours,
        "total_hours": round(state_module._state.total_hours, 0),
        "sensors": reading.to_dict(),
        "active_faults": state_module._state.get_active_faults(),
    }


@router.post("/conditions")
def set_conditions(req: OperatingConditionsRequest):
    """Change operating conditions on the active scenario."""
    if state_module._state is None:
        raise HTTPException(status_code=400, detail="No scenario loaded")

    from analysis.envelope_validator import validate_scenario
    v = validate_scenario(
        setpoint_psi=req.setpoint_psi,
        ambient_f=req.ambient_f,
        load_pct=req.load_pct,
    )
    if not v["valid"]:
        raise HTTPException(status_code=400, detail={"errors": v["errors"]})

    if req.load_pct is not None:
        state_module._state.load_pct = req.load_pct
    if req.ambient_f is not None:
        state_module._state.ambient_f = req.ambient_f
    if req.setpoint_psi is not None:
        state_module._state.setpoint_psi = req.setpoint_psi

    return {
        "conditions_updated": True,
        "warnings": v.get("warnings", []),
        "current_conditions": {
            "load_pct": state_module._state.load_pct,
            "ambient_f": state_module._state.ambient_f,
            "setpoint_psi": state_module._state.setpoint_psi,
        },
    }


@router.get("/demos")
def list_demos():
    """List all demo narratives with their metadata."""
    return {
        "demos": [
            {
                "id": k,
                "title": v["title"],
                "subtitle": v["subtitle"],
                "hours": v["hours"],
                "conditions": v["conditions"],
                "primary_symptom": v["primary_symptom"],
                "active_findings": v["active_findings"],
                "fta_highlight": v["fta_highlight"],
            }
            for k, v in DEMO_NARRATIVES.items()
        ]
    }


@router.post("/demo")
def load_demo(body: dict):
    """
    Load a demo narrative scenario.
    Body: { "demo": "demo_overdue_service" }
    Returns full narrative context + machine state.
    """
    demo_id = body.get("demo")
    if not demo_id:
        raise HTTPException(status_code=400, detail="Provide 'demo' field")

    if demo_id not in DEMO_NARRATIVES:
        raise HTTPException(status_code=400, detail={
            "error": f"Unknown demo: {demo_id}",
            "available": list(DEMO_NARRATIVES.keys()),
        })

    try:
        new_state = build_scenario(demo_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    state_module._state = new_state
    set_state(new_state)

    narrative = DEMO_NARRATIVES[demo_id]

    return {
        "loaded": demo_id,
        "narrative": narrative,
        "summary": new_state.summary(),
        "suggested_next_steps": narrative["demo_flow"],
        "diagnose_with": {
            "primary": narrative["primary_symptom"],
            "secondary": narrative["secondary_symptom"],
            "url": f"/diagnose/symptoms/{narrative['primary_symptom']}",
        },
        "fta_url": f"/analysis/fta?highlight={narrative['fta_highlight']}",
    }
