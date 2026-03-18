"""
State routes — current sensor readings and machine summary.
"""

from fastapi import APIRouter
from core.machine_state import MachineState

router = APIRouter(prefix="/state", tags=["state"])

# Machine state is injected via app.state — accessed through request
# For simplicity in single-machine demo, we use a module-level reference
# set by main.py on startup.
_state: MachineState = None

def set_state(s: MachineState):
    global _state
    _state = s


@router.get("/")
def get_full_state():
    """Complete machine state — sensors, components, faults."""
    return _state.summary()


@router.get("/sensors")
def get_sensors():
    """Current sensor readings only."""
    reading = _state.compute_sensors()
    return reading.to_dict()


@router.get("/components")
def get_components():
    """Current component health."""
    return {
        cid: {
            "name": c.name,
            "health_pct": round(c.health_pct, 1),
            "operating_hours": round(c.operating_hours, 0),
            "hours_to_service": round(c.hours_to_service, 0) if c.hours_to_service else None,
            "overdue_hours": round(c.overdue_hours, 0),
            "is_fault_risk": c.is_fault_risk,
        }
        for cid, c in _state.components.items()
    }


@router.get("/faults")
def get_faults():
    """Active fault conditions."""
    return {"faults": _state.get_active_faults()}
