"""
Logs routes — sensor history and fault events for graph generation.
"""

from fastapi import APIRouter, Query
from typing import Optional
from data.sensor_logger import get_recent_readings, get_sensor_trend, get_fault_history

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("/readings")
def get_readings(
    limit: int = Query(100, ge=1, le=10000),
    scenario: Optional[str] = None,
):
    """Recent sensor readings. Primary data source for AI Studio graphs."""
    return {"readings": get_recent_readings(limit, scenario)}


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
