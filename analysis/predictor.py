"""
Predictor — Sullair LS110

Trend analysis and timeline estimation from logged sensor data.
Answers: "how fast is this degrading, and when does it become a problem?"
"""

from typing import Optional
from data.sensor_logger import get_sensor_trend
from core.machine_state import MachineState
from core.constants import (
    FLUID_FILTER_DELTA_P_FAULT_PSI, DISCHARGE_TEMP_WARNING_F,
    DISCHARGE_TEMP_SHUTDOWN_F, SEPARATOR_DELTA_P_FAULT_PSI,
)


def _linear_trend(values: list) -> Optional[float]:
    """
    Simple linear regression slope over a list of numeric values.
    Returns rate of change per step (positive = rising).
    Returns None if insufficient data.
    """
    n = len(values)
    if n < 3:
        return None

    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n

    numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    denominator = sum((i - x_mean) ** 2 for i in range(n))

    if denominator == 0:
        return 0.0

    return numerator / denominator


def _hours_to_threshold(current: float, rate_per_hr: float,
                         threshold: float) -> Optional[float]:
    """Given current value, rate of change per hour, and threshold — how many hours?"""
    if rate_per_hr <= 0:
        return None
    remaining = threshold - current
    if remaining <= 0:
        return 0.0
    return remaining / rate_per_hr


def predict_filter_delta_trend(hours_back: float = 48.0,
                                scenario: str = None) -> dict:
    """
    Analyse P4-P3 delta trend from logged data.
    Predicts hours until FILTER MAINT REQD threshold.
    """
    p3_series = get_sensor_trend("P3", hours_back, scenario)
    p4_series = get_sensor_trend("P4", hours_back, scenario)

    if len(p3_series) < 3:
        return {"status": "insufficient_data", "minimum_readings": 3}

    deltas = []
    for p3, p4 in zip(p3_series, p4_series):
        p3_val = p3.get("P3", 0) or 0
        p4_val = p4.get("P4", 0) or 0
        deltas.append(p4_val - p3_val)

    current_delta = deltas[-1]
    slope = _linear_trend(deltas)

    if slope is None:
        return {"status": "insufficient_data"}

    # Slope is per reading — estimate readings per hour
    # (assumes readings ~every minute in fast mode, hourly in normal)
    slope_per_hour = slope * 60  # approximate — adjust per actual logging rate

    hours_to_fault = _hours_to_threshold(
        current_delta, slope_per_hour, FLUID_FILTER_DELTA_P_FAULT_PSI
    )

    return {
        "sensor": "P4_P3_delta",
        "current_value": round(current_delta, 2),
        "fault_threshold": FLUID_FILTER_DELTA_P_FAULT_PSI,
        "trend_direction": "rising" if slope > 0 else "stable_or_falling",
        "rate_per_hour": round(slope_per_hour, 4),
        "hours_to_fault": round(hours_to_fault, 1) if hours_to_fault else None,
        "days_to_fault": round(hours_to_fault / 24, 1) if hours_to_fault else None,
        "confidence": "SYNTHETIC",
        "note": "Rate extrapolated from logged trend — accuracy improves with more data",
    }


def predict_t1_trend(hours_back: float = 48.0, scenario: str = None) -> dict:
    """Analyse T1 trend — predict approach to warning and shutdown thresholds."""
    t1_series = get_sensor_trend("T1", hours_back, scenario)

    if len(t1_series) < 3:
        return {"status": "insufficient_data"}

    values = [r.get("T1", 0) or 0 for r in t1_series]
    current = values[-1]
    slope = _linear_trend(values)

    if slope is None:
        return {"status": "insufficient_data"}

    slope_per_hour = slope * 60

    hours_to_warning = _hours_to_threshold(
        current, slope_per_hour, DISCHARGE_TEMP_WARNING_F)
    hours_to_shutdown = _hours_to_threshold(
        current, slope_per_hour, DISCHARGE_TEMP_SHUTDOWN_F)

    return {
        "sensor": "T1",
        "current_value": round(current, 1),
        "warning_threshold": DISCHARGE_TEMP_WARNING_F,
        "shutdown_threshold": DISCHARGE_TEMP_SHUTDOWN_F,
        "trend_direction": "rising" if slope > 0.001 else ("falling" if slope < -0.001 else "stable"),
        "rate_per_hour_f": round(slope_per_hour, 3),
        "hours_to_warning": round(hours_to_warning, 1) if hours_to_warning else None,
        "hours_to_shutdown": round(hours_to_shutdown, 1) if hours_to_shutdown else None,
        "days_to_warning": round(hours_to_warning / 24, 1) if hours_to_warning else None,
        "days_to_shutdown": round(hours_to_shutdown / 24, 1) if hours_to_shutdown else None,
        "confidence": "SYNTHETIC",
    }


def component_risk_summary(state: MachineState) -> list:
    """
    For each component, return current health, degradation rate under
    current conditions, and estimated hours to fault threshold.
    This is the "what's going to break and when" overview.
    """
    from core.thermodynamics import get_load_multiplier, get_ambient_multiplier

    load_mult = get_load_multiplier(state.load_pct)
    temp_mult = get_ambient_multiplier(state.ambient_f)

    risks = []
    for cid, component in state.components.items():
        hours_to_fault = component.hours_until_fault(load_mult, temp_mult)

        risk_level = "LOW"
        if component.health_pct < 30:
            risk_level = "CRITICAL"
        elif component.health_pct < 50:
            risk_level = "HIGH"
        elif component.health_pct < 70:
            risk_level = "MEDIUM"

        risks.append({
            "component": cid,
            "name": component.name,
            "health_pct": round(component.health_pct, 1),
            "operating_hours": round(component.operating_hours, 0),
            "hours_to_service": round(component.hours_to_service, 0)
                                if component.hours_to_service else None,
            "overdue_hours": round(component.overdue_hours, 0),
            "hours_to_fault_at_current_load": round(hours_to_fault, 0)
                                               if hours_to_fault else None,
            "days_to_fault_at_current_load": round(hours_to_fault / 24, 1)
                                              if hours_to_fault else None,
            "risk_level": risk_level,
            "effective_degradation_rate": round(
                component.base_degradation_rate * load_mult * temp_mult, 3),
            "load_multiplier": load_mult,
            "temp_multiplier": temp_mult,
        })

    # Sort by risk level then health
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    risks.sort(key=lambda r: (order.get(r["risk_level"], 99), r["health_pct"]))

    return risks


def generate_plain_language_assessment(state: MachineState) -> str:
    """
    The "you're fucked in X days — here's how" summary.
    Plain language, for the agent to surface to the engineer.
    """
    risks = component_risk_summary(state)

    critical = [r for r in risks if r["risk_level"] == "CRITICAL"]
    high = [r for r in risks if r["risk_level"] == "HIGH"]
    medium = [r for r in risks if r["risk_level"] == "MEDIUM"]

    lines = []

    if critical:
        for r in critical:
            d = r.get("days_to_fault_at_current_load")
            if d is not None and d <= 0:
                lines.append(f"CRITICAL: {r['name']} is at fault threshold now ({r['health_pct']:.0f}% health). Immediate action.")
            elif d is not None:
                lines.append(f"CRITICAL: {r['name']} reaches fault threshold in ~{d:.0f} days at current load. Plan replacement now.")
            else:
                lines.append(f"CRITICAL: {r['name']} health at {r['health_pct']:.0f}% — service required.")

    if high:
        for r in high:
            d = r.get("days_to_fault_at_current_load")
            if d is not None:
                lines.append(f"HIGH: {r['name']} projected fault in ~{d:.0f} days. Schedule within next service window.")
            else:
                lines.append(f"HIGH: {r['name']} health at {r['health_pct']:.0f}% — monitor closely.")

    if medium:
        names = [r["name"] for r in medium]
        lines.append(f"MONITOR: {', '.join(names)} showing degradation — no immediate risk.")

    if not critical and not high:
        lines.append("No immediate fault risk detected at current operating conditions.")

    lines.append(
        f"Operating at {state.load_pct:.0f}% load, {state.ambient_f:.0f}°F ambient — "
        f"degradation rates {_load_context(state.load_pct)}."
    )

    return " ".join(lines)


def _load_context(load_pct: float) -> str:
    if load_pct >= 90:
        return "running at 3.5x normal rate (>90% load)"
    elif load_pct >= 80:
        return "running at 2x normal rate (>80% load)"
    elif load_pct >= 60:
        return "running at 1.3x normal rate (60-80% load)"
    else:
        return "at normal rate (<60% load)"
