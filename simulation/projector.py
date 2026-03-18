"""
Forward Projection Engine — Sullair LS110

Answers "what if" questions by simulating machine state forward in time.
"""

from copy import deepcopy
from typing import Optional

from core.machine_state import MachineState
from core.thermodynamics import get_load_multiplier, get_ambient_multiplier
from core.constants import (
    DEGRADATION_FAULT_PCT, DEGRADATION_FAILURE_PCT,
    DISCHARGE_TEMP_SHUTDOWN_F, DISCHARGE_TEMP_WARNING_F,
    FLUID_FILTER_DELTA_P_FAULT_PSI, SEPARATOR_DELTA_P_FAULT_PSI,
)

PROJECTION_STEP_HRS = 1.0


class ProjectionResult:

    def __init__(self):
        self.days_to_first_warning: Optional[float] = None
        self.days_to_first_fault: Optional[float] = None
        self.days_to_shutdown: Optional[float] = None
        self.first_warning_type: Optional[str] = None
        self.first_fault_type: Optional[str] = None
        self.component_trajectories: dict = {}
        self.sensor_trajectory: list = []
        self.risk_summary: str = ""
        self.mitigations: list = []
        self.projection_days: float = 0
        # Pre-existing faults at projection start — not "predictions"
        self.active_faults_at_start: list = []
        self.already_faulted: bool = False
        self.already_shutdown: bool = False

    def to_dict(self) -> dict:
        return {
            "projection_days": self.projection_days,
            "days_to_first_warning": round(self.days_to_first_warning, 1)
                                     if self.days_to_first_warning else None,
            "days_to_first_fault": round(self.days_to_first_fault, 1)
                                   if self.days_to_first_fault else None,
            "days_to_shutdown": round(self.days_to_shutdown, 1)
                                if self.days_to_shutdown else None,
            "first_warning_type": self.first_warning_type,
            "first_fault_type": self.first_fault_type,
            # These distinguish "already broken" from "will break in N days"
            "already_faulted": self.already_faulted,
            "already_shutdown": self.already_shutdown,
            "active_faults_at_start": self.active_faults_at_start,
            "risk_summary": self.risk_summary,
            "mitigations": self.mitigations,
            "component_trajectories": self.component_trajectories,
            "sensor_trajectory": self.sensor_trajectory,
        }


def project(
    state: MachineState,
    days: float = 30.0,
    load_pct: Optional[float] = None,
    ambient_f: Optional[float] = None,
    setpoint_psi: Optional[float] = None,
    defer_services: Optional[dict] = None,
) -> ProjectionResult:
    # Deep copy — never modify the live state
    sim = deepcopy(state)

    if load_pct is not None:
        sim.load_pct = load_pct
    if ambient_f is not None:
        sim.ambient_f = ambient_f
    if setpoint_psi is not None:
        from analysis.envelope_validator import validate_pressure
        v = validate_pressure(setpoint_psi)
        if not v["valid"]:
            result = ProjectionResult()
            result.risk_summary = f"Invalid pressure setpoint: {v['reason']}"
            return result
        sim.setpoint_psi = setpoint_psi

    result = ProjectionResult()
    result.projection_days = days

    # ── Check for pre-existing faults BEFORE the projection starts ──────────
    # This is the critical distinction: faults present NOW vs faults that develop.
    initial_faults = sim.get_active_faults()
    result.active_faults_at_start = initial_faults

    for fault in initial_faults:
        sev = fault.get("severity")
        if sev == "SHUTDOWN":
            result.already_shutdown = True
        if sev in ("SHUTDOWN", "MAINTENANCE"):
            result.already_faulted = True

    # Initialise component trajectories
    for cid in sim.components:
        result.component_trajectories[cid] = []

    total_hours = days * 24.0
    elapsed_hours = 0.0
    sample_interval = 24.0

    load_mult = get_load_multiplier(sim.load_pct)
    temp_mult = get_ambient_multiplier(sim.ambient_f)

    next_sample = 0.0

    while elapsed_hours < total_hours:
        step = min(PROJECTION_STEP_HRS, total_hours - elapsed_hours)

        for cid, component in sim.components.items():
            if defer_services and cid in defer_services:
                defer_hrs = defer_services[cid] * 24.0
                if component.operating_hours < defer_hrs:
                    component.degrade(step, load_mult, temp_mult)
                else:
                    component.degrade(step, load_mult, temp_mult)
            else:
                component.degrade(step, load_mult, temp_mult)

        sim.total_hours += step
        elapsed_hours += step
        elapsed_days = elapsed_hours / 24.0

        faults = sim.get_active_faults()
        for fault in faults:
            severity = fault.get("severity")
            code = fault.get("code")

            # Only record as a "new" development if it wasn't already present
            already = any(f.get("code") == code for f in initial_faults)

            if severity == "WARNING" and result.days_to_first_warning is None and not already:
                result.days_to_first_warning = elapsed_days
                result.first_warning_type = code

            if severity == "MAINTENANCE" and result.days_to_first_fault is None and not already:
                result.days_to_first_fault = elapsed_days
                result.first_fault_type = code

            if severity == "SHUTDOWN" and result.days_to_shutdown is None and not already:
                result.days_to_shutdown = elapsed_days

        if elapsed_hours >= next_sample:
            reading = sim.compute_sensors()
            result.sensor_trajectory.append({
                "day": round(elapsed_days, 1),
                **reading.to_dict(),
            })
            for cid, component in sim.components.items():
                result.component_trajectories[cid].append({
                    "day": round(elapsed_days, 1),
                    "health_pct": round(component.health_pct, 1),
                })
            next_sample += sample_interval

    result.risk_summary = _build_risk_summary(result, sim, days)
    result.mitigations = _build_mitigations(result, sim, state)

    return result


def _build_risk_summary(result: ProjectionResult,
                         final_state: MachineState,
                         projection_days: float) -> str:

    # Already-broken case — separate messaging
    if result.already_shutdown:
        codes = [f.get("code") for f in result.active_faults_at_start
                 if f.get("severity") == "SHUTDOWN"]
        code_str = ", ".join(codes) if codes else "multiple faults"
        base = (f"ACTIVE SHUTDOWN FAULT — machine is currently in fault condition "
                f"({code_str}). Projection shows what develops IF this is not resolved.")
        if result.days_to_shutdown:
            base += f" Additional shutdown fault develops in {result.days_to_shutdown:.0f} days."
        return base

    if result.already_faulted:
        codes = [f.get("code") for f in result.active_faults_at_start
                 if f.get("severity") == "MAINTENANCE"]
        code_str = ", ".join(codes) if codes else "maintenance fault"
        base = (f"ACTIVE MAINTENANCE FAULT — machine currently requires service "
                f"({code_str}). Projection shows degradation over next {projection_days:.0f} days.")
        return base

    if result.days_to_shutdown:
        d = result.days_to_shutdown
        if d < 3:
            return (f"CRITICAL — shutdown event projected within {d:.0f} days "
                    f"({result.first_fault_type or 'multiple faults'}). Immediate action required.")
        elif d < 14:
            return (f"HIGH RISK — shutdown projected in {d:.0f} days. "
                    f"First fault: {result.first_fault_type}. Plan maintenance now.")
        else:
            return (f"ELEVATED RISK — shutdown projected in {d:.0f} days. "
                    f"Schedule maintenance within {d*0.6:.0f} days.")

    elif result.days_to_first_fault:
        d = result.days_to_first_fault
        return (f"Maintenance required in {d:.0f} days "
                f"({result.first_fault_type}). No shutdown risk within "
                f"{projection_days:.0f}-day window if serviced on schedule.")

    elif result.days_to_first_warning:
        d = result.days_to_first_warning
        return (f"Warning threshold approached in {d:.0f} days. "
                f"Monitor closely. No immediate fault risk.")

    else:
        return (f"No fault conditions projected within {projection_days:.0f} days "
                f"at current operating conditions.")


def _build_mitigations(result: ProjectionResult,
                        final_state: MachineState,
                        original_state: MachineState) -> list:
    mitigations = []
    components = final_state.components

    # Solenoid valve — add explicit mitigation if at fault
    sol = components.get("solenoid_valve")
    if sol and sol.health_pct < 20:
        mitigations.append({
            "action": "Replace solenoid valve SOL1",
            "urgency": "immediate",
            "reason": f"Solenoid health at {sol.health_pct:.0f}% — machine cannot unload, P2 exceeds setpoint",
            "impact": "Clears HIGH_PRESS_P2 shutdown condition immediately",
        })

    ff = components.get("fluid_filter")
    if ff and ff.health_pct < 40:
        mitigations.append({
            "action": "Replace fluid filter element",
            "part": "02250139-995",
            "urgency": "immediate" if ff.health_pct < 20 else "within_7_days",
            "reason": f"Filter health at {ff.health_pct:.0f}% — P4-P3 delta approaching fault threshold",
            "impact": "Prevents T1 rise and potential thermal shutdown",
        })

    sep = components.get("separator_element")
    if sep and sep.health_pct < 40:
        mitigations.append({
            "action": "Replace separator element",
            "part": "02250242-636",
            "urgency": "immediate" if sep.health_pct < 15 else "within_14_days",
            "reason": f"Separator health at {sep.health_pct:.0f}% — sump overpressure risk",
            "impact": "Prevents relief valve cycling and potential cascade",
        })

    inf = components.get("inlet_filter")
    if inf and inf.health_pct < 40:
        mitigations.append({
            "action": "Inspect and replace inlet filter element",
            "urgency": "within_7_days",
            "reason": f"Inlet filter health at {inf.health_pct:.0f}% — PSW1 approaching fault threshold",
            "impact": "Prevents motor overload from restricted airflow",
        })

    tv = components.get("thermal_valve")
    if tv and tv.health_pct < 40:
        mitigations.append({
            "action": "Replace thermal valve element",
            "urgency": "within_14_days",
            "reason": f"Thermal valve health at {tv.health_pct:.0f}% — overcooling or overheating risk",
            "impact": "Prevents fluid foaming or thermal cascade",
        })

    if final_state.setpoint_psi > original_state.setpoint_psi:
        delta_psi = final_state.setpoint_psi - original_state.setpoint_psi
        mitigations.append({
            "action": f"Before increasing pressure by {delta_psi:.0f}psi — service fluid filter first",
            "urgency": "prerequisite",
            "reason": "Higher pressure increases thermal load — degraded filter compounds risk",
            "impact": f"Reduces projected risk window from {result.days_to_shutdown or '>30'} days "
                      f"to estimated 30+ days post-service",
        })

    if final_state.load_pct > original_state.load_pct + 10:
        mitigations.append({
            "action": "Review cooling airflow before sustained load increase",
            "urgency": "prerequisite",
            "reason": f"Sustained {final_state.load_pct:.0f}% load increases degradation rate 2x",
            "impact": "Halves time to next maintenance event across all service intervals",
        })

    return mitigations


def compare_scenarios(
    state: MachineState,
    scenarios: list,
    days: float = 30.0,
) -> dict:
    results = {}
    for scenario in scenarios:
        label = scenario.pop("label", f"scenario_{len(results)}")
        proj = project(state, days=days, **scenario)
        results[label] = proj.to_dict()

    return {
        "projection_days": days,
        "scenarios": results,
        "recommendation": _pick_best_scenario(results),
    }


def _pick_best_scenario(results: dict) -> str:
    best = None
    best_days = -1

    for label, result in results.items():
        fault_day = result.get("days_to_first_fault") or result["projection_days"]
        if fault_day > best_days:
            best_days = fault_day
            best = label

    if best:
        return f"Recommended: '{best}' — latest fault projection at day {best_days:.0f}"
    return "No clear recommendation — review scenarios manually"
