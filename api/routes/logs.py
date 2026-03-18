"""
Logs routes — sensor history, fault events, and event log.
"""

from fastapi import APIRouter, Query
from typing import Optional
from data.sensor_logger import get_recent_readings, get_sensor_trend, get_fault_history, get_event_log

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("/readings")
def get_readings(
    limit: int = Query(100, ge=1, le=10000),
    scenario: Optional[str] = None,
):
    """Recent sensor readings. Primary data source for AI Studio graphs."""
    return {"readings": get_recent_readings(limit, scenario)}


@router.get("/events")
def get_events(limit: int = Query(100, ge=1, le=1000)):
    """
    Persistent event log — scenario loads, fault injections, finding changes.
    This is the backend-owned version of the frontend event journal.
    Newest entries first.

    Event types: LOAD | FAULT | INJECT | CLEAR

    Response shape:
    {
      "events": [
        {
          "id": 42,
          "timestamp": "2026-03-18T21:52:00.000000+00:00",
          "event_type": "FAULT",
          "message": "CORR_004 · ACTION · T1_above_model_action"
        },
        ...
      ]
    }
    """
    return {"events": get_event_log(limit)}


@router.get("/trend/{sensor}")
def get_trend(
    sensor: str,
    hours_back: float = Query(24.0, ge=1, le=720),
    scenario: Optional[str] = None,
):
    """
    Time series for a single sensor.
    Valid sensors: P1, P2, P3, P4, T1, T2, PSW1, load_pct, ambient_f
    """
    valid_sensors = ["P1", "P2", "P3", "P4", "T1", "T2", "PSW1", "load_pct", "ambient_f"]
    if sensor not in valid_sensors:
        return {"error": f"Unknown sensor. Valid: {valid_sensors}"}

    return {
        "sensor": sensor,
        "hours_back": hours_back,
        "data": get_sensor_trend(sensor, hours_back, scenario),
    }


@router.get("/faults")
def get_faults(limit: int = Query(50, ge=1, le=500)):
    """Fault event history."""
    return {"faults": get_fault_history(limit)}
