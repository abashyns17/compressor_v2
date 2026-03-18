"""
Pydantic models for API request/response validation.
"""

from pydantic import BaseModel, Field
from typing import Optional


class ScenarioRequest(BaseModel):
    name: str = Field(..., description="Scenario name from list_scenarios()")


class ProjectionRequest(BaseModel):
    days: float = Field(30.0, ge=1, le=365)
    load_pct: Optional[float] = Field(None, ge=0, le=100)
    ambient_f: Optional[float] = Field(None, ge=40, le=115)
    setpoint_psi: Optional[float] = Field(None, ge=60, le=175)
    defer_services: Optional[dict] = Field(
        None,
        description="Component IDs mapped to deferral in days. "
                    "e.g. {'fluid_filter': 60, 'shaft_seal': 730}"
    )


class CompareRequest(BaseModel):
    days: float = Field(30.0, ge=1, le=365)
    scenarios: list = Field(..., description="List of scenario dicts with optional label, "
                                              "load_pct, ambient_f, setpoint_psi, defer_services")


class FaultInjectRequest(BaseModel):
    fault: str = Field(..., description=(
        "One of: thermal_valve_stuck_open, thermal_valve_stuck_closed, "
        "filter_bypass_open, solenoid_failure, clear_all"
    ))


class ComponentHealthRequest(BaseModel):
    component_id: str
    health_pct: float = Field(..., ge=0, le=100)


class DegradeRequest(BaseModel):
    component_id: str
    by_pct: float = Field(..., ge=0, le=100)


class OperatingConditionsRequest(BaseModel):
    load_pct: Optional[float] = Field(None, ge=0, le=100)
    ambient_f: Optional[float] = Field(None, ge=40, le=115)
    setpoint_psi: Optional[float] = Field(None, ge=60, le=175)


class AdvanceTimeRequest(BaseModel):
    hours: float = Field(..., ge=0.1, le=8760,
                         description="Advance simulation by this many hours")
