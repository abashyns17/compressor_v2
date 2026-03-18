"""
Inject routes — fault injection and component health control.
"""

from fastapi import APIRouter, HTTPException
from api.models import FaultInjectRequest, ComponentHealthRequest, DegradeRequest
from simulation.fault_injector import FaultInjector
import api.routes.state as state_module
from data.sensor_logger import log_event

router = APIRouter(prefix="/inject", tags=["inject"])


def _get_injector() -> FaultInjector:
    if state_module._state is None:
        raise HTTPException(status_code=400, detail="No scenario loaded. POST /scenarios/load first.")
    return FaultInjector(state_module._state)


@router.post("/fault")
def inject_fault(req: FaultInjectRequest):
    """Inject a named fault condition."""
    injector = _get_injector()
    fault_map = {
        "thermal_valve_stuck_open":   injector.inject_thermal_valve_stuck_open,
        "thermal_valve_stuck_closed": injector.inject_thermal_valve_stuck_closed,
        "filter_bypass_open":         injector.inject_filter_bypass_open,
        "solenoid_failure":           injector.inject_solenoid_failure,
        "clear_all":                  injector.clear_all_faults,
    }
    handler = fault_map.get(req.fault)
    if not handler:
        raise HTTPException(status_code=400, detail={
            "error": f"Unknown fault: {req.fault}",
            "valid_faults": list(fault_map.keys()),
        })
    result = handler()
    event_type = "CLEAR" if req.fault == "clear_all" else "INJECT"
    msg = "All faults cleared" if req.fault == "clear_all" else f"Injected: {req.fault}"
    log_event(event_type, msg)
    return result


@router.post("/component/health")
def set_component_health(req: ComponentHealthRequest):
    """Directly set a component's health percentage."""
    result = _get_injector().set_component_health(req.component_id, req.health_pct)
    log_event("INJECT", f"Component health set: {req.component_id} → {req.health_pct:.0f}%")
    return result


@router.post("/component/degrade")
def degrade_component(req: DegradeRequest):
    """Reduce a component's health by a given amount."""
    result = _get_injector().degrade_component(req.component_id, req.by_pct)
    log_event("INJECT", f"Component degraded: {req.component_id} by {req.by_pct:.0f}%")
    return result


@router.post("/component/service/{component_id}")
def service_component(component_id: str):
    """Reset a component to 100% health (simulates replacement)."""
    result = _get_injector().service_component(component_id)
    log_event("LOAD", f"Component serviced: {component_id} reset to 100%")
    return result


@router.get("/status")
def get_inject_status():
    """Current fault flags and operating conditions."""
    return _get_injector().status()
