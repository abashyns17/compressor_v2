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

FAULT_CODE_TO_CORRELATIONS = {
    "FILTER_MAINT_REQD":     ["CORR_001", "CD_001"],
    "AIR_FILTER_MAINT_REQD": ["CORR_006"],
    "HIGH_TEMP_T1":          ["CORR_004", "THERMAL"],
    "TEMP_T1_WARNING":       ["CORR_004"],
    "HIGH_PRESS_P2":         ["CORR_005", "SOLENOID"],
    "SEPARATOR_OVERPRESSURE_SHUTDOWN": ["CORR_003", "CORR_005"],
    "MOTOR_OVERLOAD_SHUTDOWN":         ["CORR_006"],
}

COMPONENT_TO_CORRELATIONS = {
    "fluid_filter":       {"ids": ["CORR_001", "CD_001"], "severity": "ACTION",   "label": "Filter loading — delta-P approaching fault threshold"},
    "separator_element":  {"ids": ["CORR_003", "CORR_005"], "severity": "ACTION", "label": "Separator degraded — T1-T2 efficiency reducing, P1/P2 divergence risk"},
    "inlet_filter":       {"ids": ["CORR_006"], "severity": "ACTION",             "label": "Inlet filter loading — PSW1 vacuum increasing"},
    "thermal_valve":      {"ids": ["CORR_004", "CD_002"], "severity": "ACTION",   "label": "Thermal valve worn — T1 model deviation developing"},
    "oil_cooler":         {"ids": ["CORR_004"], "severity": "ACTION",             "label": "Oil cooler degraded — T1 thermal rise risk"},
    "shaft_seal":         {"ids": [], "severity": "ACTION",                        "label": "Shaft seal worn — external oil leakage risk"},
    "coupling_element":   {"ids": [], "severity": "ACTION",                        "label": "Coupling element worn — vibration risk"},
    "main_motor_bearing": {"ids": [], "severity": "ACTION",                        "label": "Motor bearing worn — vibration and heat risk"},
    "solenoid_valve":     {"ids": ["CORR_005", "SOLENOID"], "severity": "ACTION", "label": "Solenoid degraded — unload circuit at risk"},
    "blowdown_valve":     {"ids": ["CORR_005"], "severity": "ACTION",             "label": "Blowdown valve degraded — unload risk"},
}

CORRELATION_LABELS = {
    "CORR_001": "Filter delta-P fault threshold",
    "CORR_002": "Oil flow vs discharge temperature",
    "CORR_003": "T1–T2 separator efficiency delta",
    "CORR_004": "T1 above thermodynamic model",
    "CORR_005": "P1/P2 pressure divergence",
    "CORR_006": "Inlet vs fluid restriction",
    "CD_001":   "Silent filter bypass (composite)",
    "CD_002":   "Thermal valve stuck open (composite)",
    "CD_003":   "Pre-alarm separator failure (composite)",
    "SOLENOID": "Solenoid / blowdown valve failure",
    "THERMAL":  "Thermal shutdown branch",
}


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
        self.active_faults_at_start: list = []
        self.already_faulted: bool = False
        self.already_shutdown: bool = False
        self.projected_findings: list = []
        self.chart_annotations: dict = {}
        self.timeline: list = []  # chronological event stream
        self.cascade_chains: list = []  # root cause + downstream sequence
        self.explanation: dict = {}   # structured plain-English explanation

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
            "already_faulted": self.already_faulted,
            "already_shutdown": self.already_shutdown,
            "active_faults_at_start": self.active_faults_at_start,
            "risk_summary": self.risk_summary,
            "mitigations": self.mitigations,
            "component_trajectories": self.component_trajectories,
            "sensor_trajectory": self.sensor_trajectory,
            "projected_findings": self.projected_findings,
            "chart_annotations": self.chart_annotations,
            "timeline": self.timeline,
            "cascade_chains": self.cascade_chains,
            "explanation": self.explanation,
        }


def project(
    state: MachineState,
    days: float = 30.0,
    load_pct: Optional[float] = None,
    ambient_f: Optional[float] = None,
    setpoint_psi: Optional[float] = None,
    defer_services: Optional[dict] = None,
    ambient_profile: Optional[list] = None,
) -> ProjectionResult:
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

    initial_faults = sim.get_active_faults()
    result.active_faults_at_start = initial_faults

    for fault in initial_faults:
        sev = fault.get("severity")
        if sev == "SHUTDOWN":
            result.already_shutdown = True
        if sev in ("SHUTDOWN", "MAINTENANCE"):
            result.already_faulted = True

    for cid in sim.components:
        result.component_trajectories[cid] = []

    total_hours = days * 24.0
    elapsed_hours = 0.0
    sample_interval = 24.0

    # load_mult is fixed — load_pct doesn't change during projection
    load_mult = get_load_multiplier(sim.load_pct)
    # temp_mult is recomputed each step from actual T1 — see loop

    next_sample = 0.0

    # Track (code, severity) pairs to detect escalations (MAINTENANCE→SHUTDOWN)
    recorded_fault_sigs: set = set(
        (f.get("code"), f.get("severity")) for f in initial_faults
    )
    recorded_correlation_ids: set = set()

    # Cascade chain tracking: detect when temp_mult spikes due to component fault
    _prev_temp_mult = get_ambient_multiplier(sim.compute_sensors().T1)
    _cascade_root: Optional[str] = None  # component that triggered the cascade
    _cascade_root_day: Optional[float] = None
    _cascade_downstream: list = []  # [{component, day, triggered_by, reason}]

    # Record day-0 fault timing for already-active faults
    for fault in initial_faults:
        sev = fault.get("severity")
        code = fault.get("code")
        if sev == "MAINTENANCE" and result.days_to_first_fault is None:
            result.days_to_first_fault = 0.0
            result.first_fault_type = code
        if sev == "SHUTDOWN" and result.days_to_shutdown is None:
            result.days_to_shutdown = 0.0

    while elapsed_hours < total_hours:
        step = min(PROJECTION_STEP_HRS, total_hours - elapsed_hours)
        elapsed_days = (elapsed_hours + step) / 24.0

        # Apply ambient profile if provided: override sim.ambient_f per day
        if ambient_profile:
            day_idx = min(int(elapsed_hours / 24.0), len(ambient_profile) - 1)
            profile_entry = ambient_profile[day_idx]
            sim.ambient_f = profile_entry.get("temp_f", sim.ambient_f)

        # Dynamic temp_mult from actual running T1 — cascades propagate:
        # filter degrades → T1 rises → other components degrade faster.
        _reading_now = sim.compute_sensors()
        temp_mult = get_ambient_multiplier(_reading_now.T1)

        # Detect cascade onset: temp_mult stepped up significantly
        if temp_mult > _prev_temp_mult + 0.15 and _cascade_root is None:
            # Identify which faulted component is the thermal root cause
            for cid in ("fluid_filter", "thermal_valve", "oil_cooler"):
                comp = sim.components.get(cid)
                if comp and comp.health_pct <= DEGRADATION_FAULT_PCT:
                    _cascade_root = cid
                    _cascade_root_day = round(elapsed_days, 1)
                    break

        # Track components being accelerated by cascade
        if _cascade_root is not None and temp_mult > _prev_temp_mult:
            for cid, component in sim.components.items():
                if cid == _cascade_root:
                    continue
                comp_key = f"CASCADE_{cid}"
                if comp_key not in recorded_fault_sigs and component.health_pct < 70.0:
                    recorded_fault_sigs.add(comp_key)
                    _cascade_downstream.append({
                        "component": cid,
                        "day": round(elapsed_days, 1),
                        "triggered_by": _cascade_root,
                        "cascade_reason": (
                            f"T1 elevated by {_cascade_root.replace('_',' ')} failure "
                            f"— degradation rate ×{temp_mult:.1f}"
                        ),
                    })

        _prev_temp_mult = temp_mult

        for cid, component in sim.components.items():
            component.degrade(step, load_mult, temp_mult)

        sim.total_hours += step
        elapsed_hours += step

        faults = sim.get_active_faults()
        for fault in faults:
            severity = fault.get("severity")
            code = fault.get("code")

            sig = (code, severity)
            if sig in recorded_fault_sigs:
                continue

            recorded_fault_sigs.add(sig)

            if severity == "WARNING" and result.days_to_first_warning is None:
                result.days_to_first_warning = elapsed_days
                result.first_warning_type = code

            if severity == "MAINTENANCE" and result.days_to_first_fault is None:
                result.days_to_first_fault = elapsed_days
                result.first_fault_type = code

            if severity == "SHUTDOWN" and result.days_to_shutdown is None:
                result.days_to_shutdown = elapsed_days

            for corr_id in FAULT_CODE_TO_CORRELATIONS.get(code, []):
                if corr_id not in recorded_correlation_ids:
                    recorded_correlation_ids.add(corr_id)
                    proj_severity = "CRITICAL" if severity == "SHUTDOWN" else "ACTION"
                    result.projected_findings.append({
                        "correlation_id": corr_id,
                        "fires_at_day": round(elapsed_days, 1),
                        "severity": proj_severity,
                        "fault_code": code,
                        "label": CORRELATION_LABELS.get(corr_id, corr_id),
                        "interpretation": f"Projected to activate at day {elapsed_days:.0f} — {CORRELATION_LABELS.get(corr_id, code)}",
                        "triggered_by": _cascade_root if _cascade_root else None,
                        "cascade_reason": (
                            f"Downstream of {_cascade_root.replace('_',' ')} thermal cascade"
                            if _cascade_root else None
                        ),
                    })

        # Component health crossings — FTA events even without sensor alarm
        for cid, component in sim.components.items():
            if component.health_pct <= DEGRADATION_FAULT_PCT:
                comp_info = COMPONENT_TO_CORRELATIONS.get(cid, {})
                comp_key = f"COMP_{cid}"
                comp_sig = (comp_key, "ACTION")
                if comp_sig not in recorded_fault_sigs and comp_info:
                    recorded_fault_sigs.add(comp_sig)
                    for corr_id in comp_info.get("ids", []):
                        if corr_id not in recorded_correlation_ids:
                            recorded_correlation_ids.add(corr_id)
                            result.projected_findings.append({
                                "correlation_id": corr_id,
                                "fires_at_day": round(elapsed_days, 1),
                                "severity": comp_info["severity"],
                                "fault_code": comp_key,
                                "label": CORRELATION_LABELS.get(corr_id, corr_id),
                                "interpretation": f"Projected at day {elapsed_days:.0f} — {comp_info['label']}",
                                "triggered_by": _cascade_root if _cascade_root else None,
                                "cascade_reason": (
                                    f"Downstream of {_cascade_root.replace('_',' ')} thermal cascade"
                                    if _cascade_root else None
                                ),
                            })

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

    # Build cascade chains output
    if _cascade_root and _cascade_downstream:
        result.cascade_chains = [{
            "root_cause": _cascade_root,
            "root_cause_day": _cascade_root_day,
            "mechanism": "thermal_cascade",
            "description": (
                f"{_cascade_root.replace('_',' ').title()} degraded to fault threshold, "
                f"causing T1 rise that accelerated downstream component wear"
            ),
            "downstream": _cascade_downstream,
        }]

    result.risk_summary = _build_risk_summary(result, sim, days)
    result.mitigations = _build_mitigations(result, sim, state)
    result.chart_annotations = _build_chart_annotations(result, sim)
    result.timeline = _build_timeline(result)
    result.explanation = _build_explanation(result, sim, state, days)

    return result


# ── Change 3: Timeline event stream ──────────────────────────────────────────

def _build_timeline(result: ProjectionResult) -> list:
    """
    Merge all projection events into a single sorted chronological list.
    Each event: { day, type, severity, code, title, description }
    Types: FAULT_START | SHUTDOWN | WARNING | COMPONENT_FAULT | SENSOR_THRESHOLD
    """
    events = []

    # Day-0 active faults
    for fault in result.active_faults_at_start:
        sev = fault.get("severity", "")
        code = fault.get("code", "")
        events.append({
            "day": 0.0,
            "type": "SHUTDOWN" if sev == "SHUTDOWN" else "FAULT_START",
            "severity": sev,
            "code": code,
            "title": _fault_title(code),
            "description": f"Active at start — {_fault_title(code)}",
        })

    # Projected findings (sensor faults and component crossings)
    for f in result.projected_findings:
        code = f.get("fault_code", "")
        sev = f["severity"]
        if code.startswith("COMP_"):
            events.append({
                "day": f["fires_at_day"],
                "type": "COMPONENT_FAULT",
                "severity": sev,
                "code": f["correlation_id"],
                "title": f["label"],
                "description": f["interpretation"],
            })
        else:
            events.append({
                "day": f["fires_at_day"],
                "type": "SHUTDOWN" if sev == "CRITICAL" else "SENSOR_THRESHOLD",
                "severity": sev,
                "code": f["correlation_id"],
                "title": f["label"],
                "description": f["interpretation"],
            })

    # Component health fault crossings from annotations
    for cid, ann in result.chart_annotations.items():
        if not isinstance(ann, dict):
            continue
        fcd = ann.get("fault_cross_day")
        if fcd is not None and cid not in ("T1", "P4_P3_delta"):
            comp_name = cid.replace("_", " ").title()
            events.append({
                "day": fcd,
                "type": "COMPONENT_FAULT",
                "severity": "ACTION",
                "code": f"COMP_{cid.upper()}",
                "title": f"{comp_name} at fault threshold",
                "description": ann.get("fault_annotation", f"{comp_name} health crossed 30%"),
            })

    # T1 sensor events
    t1_ann = result.chart_annotations.get("T1", {})
    if t1_ann.get("warn_day") is not None:
        events.append({
            "day": t1_ann["warn_day"],
            "type": "WARNING",
            "severity": "WARNING",
            "code": "TEMP_T1_WARNING",
            "title": "T1 warning threshold",
            "description": t1_ann.get("warn_annotation", "T1 approaching warning limit"),
        })
    if t1_ann.get("shutdown_day") is not None:
        events.append({
            "day": t1_ann["shutdown_day"],
            "type": "SHUTDOWN",
            "severity": "CRITICAL",
            "code": "HIGH_TEMP_T1",
            "title": "T1 thermal shutdown",
            "description": t1_ann.get("shutdown_annotation", "T1 reached shutdown threshold"),
        })

    # Filter delta-P event
    dp_ann = result.chart_annotations.get("P4_P3_delta", {})
    if dp_ann.get("fault_day") is not None:
        events.append({
            "day": dp_ann["fault_day"],
            "type": "SENSOR_THRESHOLD",
            "severity": "ACTION",
            "code": "FILTER_MAINT_REQD",
            "title": "Filter delta-P fault",
            "description": dp_ann.get("fault_annotation", "FILTER MAINT REQD threshold reached"),
        })

    # Deduplicate by (day, code) and sort
    seen = set()
    unique = []
    for e in sorted(events, key=lambda x: x["day"]):
        key = (round(e["day"], 1), e["code"])
        if key not in seen:
            seen.add(key)
            unique.append(e)

    return unique


def _fault_title(code: str) -> str:
    return {
        "FILTER_MAINT_REQD":               "Filter maintenance required",
        "AIR_FILTER_MAINT_REQD":           "Air filter maintenance required",
        "HIGH_TEMP_T1":                    "Thermal shutdown — T1 ≥ 200°F",
        "TEMP_T1_WARNING":                 "Discharge temperature warning",
        "HIGH_PRESS_P2":                   "High discharge pressure — shutdown",
        "SEPARATOR_OVERPRESSURE_SHUTDOWN": "Separator failure — overpressure shutdown",
        "MOTOR_OVERLOAD_SHUTDOWN":         "Motor overload — inlet restriction at high load",
    }.get(code, code.replace("_", " ").title())


def _build_chart_annotations(result: ProjectionResult,
                               final_state: MachineState) -> dict:
    annotations = {}

    for cid, traj in result.component_trajectories.items():
        if not traj:
            continue
        start_health = traj[0]["health_pct"]
        end_health = traj[-1]["health_pct"]
        fault_threshold = 30.0
        fault_cross_day = None
        for point in traj:
            if point["health_pct"] <= fault_threshold:
                fault_cross_day = point["day"]
                break
        comp_name = cid.replace("_", " ").title()
        annotation = {
            "component": cid,
            "label": comp_name,
            "start_health_pct": round(start_health, 1),
            "end_health_pct": round(end_health, 1),
            "fault_threshold_pct": fault_threshold,
            "fault_cross_day": fault_cross_day,
            "chart_title": f"{comp_name} — {end_health:.0f}% at day {result.projection_days:.0f}",
            "status": (
                "critical" if end_health < 30 else
                "degraded" if end_health < 70 else
                "healthy"
            ),
        }
        if fault_cross_day is not None:
            annotation["fault_annotation"] = (
                f"Fault threshold crossed at day {fault_cross_day:.0f} — service required"
            )
        annotations[cid] = annotation

    warn_f = DISCHARGE_TEMP_WARNING_F
    shutdown_f = DISCHARGE_TEMP_SHUTDOWN_F
    t1_warn_day = None
    t1_shutdown_day = None
    for point in result.sensor_trajectory:
        t1 = point.get("T1")
        if t1 is None:
            continue
        if t1_warn_day is None and t1 >= warn_f:
            t1_warn_day = point["day"]
        if t1_shutdown_day is None and t1 >= shutdown_f:
            t1_shutdown_day = point["day"]

    annotations["T1"] = {
        "sensor": "T1",
        "label": "Wet Discharge Temperature",
        "warn_threshold_f": warn_f,
        "warn_threshold_c": round((warn_f - 32) * 5/9, 1),
        "shutdown_threshold_f": shutdown_f,
        "shutdown_threshold_c": round((shutdown_f - 32) * 5/9, 1),
        "warn_day": t1_warn_day,
        "shutdown_day": t1_shutdown_day,
        "warn_annotation": (
            f"Temperature warning (195°F / 90.6°C) reached at day {t1_warn_day:.0f}"
            if t1_warn_day else None
        ),
        "shutdown_annotation": (
            f"Thermal shutdown (200°F / 93.3°C) reached at day {t1_shutdown_day:.0f}"
            if t1_shutdown_day else None
        ),
        "chart_title": "T1 Wet Discharge Temperature Forecast",
    }

    dp_fault_psi = FLUID_FILTER_DELTA_P_FAULT_PSI
    dp_fault_day = None
    for point in result.sensor_trajectory:
        dp = point.get("P4_P3_delta")
        if dp is not None and dp >= dp_fault_psi:
            dp_fault_day = point["day"]
            break

    annotations["P4_P3_delta"] = {
        "sensor": "P4_P3_delta",
        "label": "Filter Differential Pressure (P4–P3)",
        "fault_threshold_psi": dp_fault_psi,
        "fault_threshold_bar": round(dp_fault_psi * 0.0689476, 2),
        "fault_day": dp_fault_day,
        "chart_title": "Filter Differential Pressure Trajectory",
        "fault_annotation": (
            f"FILTER MAINT REQD threshold reached at day {dp_fault_day:.0f} — replace fluid filter element"
            if dp_fault_day else None
        ),
    }

    return annotations


def _build_risk_summary(result: ProjectionResult,
                         final_state: MachineState,
                         projection_days: float) -> str:

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

    sol = components.get("solenoid_valve")
    if sol and sol.health_pct < 20:
        mitigations.append({
            "action": "Replace solenoid valve SOL1",
            "urgency": "immediate",
            "reason": f"Solenoid health at {sol.health_pct:.0f}% — machine cannot unload",
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
            "impact": f"Reduces projected risk window from "
                      f"{result.days_to_shutdown or '>30'} days to estimated 30+ days post-service",
        })

    if final_state.load_pct > original_state.load_pct + 10:
        mitigations.append({
            "action": "Review cooling airflow before sustained load increase",
            "urgency": "prerequisite",
            "reason": f"Sustained {final_state.load_pct:.0f}% load increases degradation rate 2x",
            "impact": "Halves time to next maintenance event across all service intervals",
        })

    return mitigations


def _build_explanation(
    result: ProjectionResult,
    final_state: MachineState,
    original_state: MachineState,
    projection_days: float,
) -> dict:
    """
    Build a structured plain-English explanation of the projection outcome.
    Fields: headline, top_drivers, what_changes_recommendation,
            model_confidence, causal_chain.
    """
    components = final_state.components

    # ── Binding constraint (headline) ─────────────────────────────────────────
    if result.days_to_shutdown is not None:
        d = result.days_to_shutdown
        constraint_comp = result.first_fault_type or "unknown component"
        headline = (
            f"Machine projected to shut down in {d:.0f} days due to "
            f"{_fault_title(constraint_comp).lower()}."
        )
    elif result.days_to_first_fault is not None:
        d = result.days_to_first_fault
        headline = (
            f"Maintenance required in {d:.0f} days "
            f"({result.first_fault_type}). No shutdown within {projection_days:.0f}-day window."
        )
    elif result.days_to_first_warning is not None:
        headline = (
            f"Warning threshold approached in {result.days_to_first_warning:.0f} days. "
            f"No fault risk within {projection_days:.0f}-day window."
        )
    else:
        headline = f"No fault conditions projected within {projection_days:.0f} days."

    # ── Top drivers ───────────────────────────────────────────────────────────
    # Score each component by how much health it loses and its fault proximity
    drivers = []
    for cid, traj in result.component_trajectories.items():
        if not traj:
            continue
        start_h = traj[0]["health_pct"]
        end_h = traj[-1]["health_pct"]
        loss = start_h - end_h
        if loss <= 0:
            continue
        fault_cross = result.chart_annotations.get(cid, {}).get("fault_cross_day")
        # Sensitivity: HIGH if crosses fault threshold, MEDIUM if >10pt loss, LOW otherwise
        if fault_cross is not None:
            sensitivity = "HIGH"
        elif loss > 10:
            sensitivity = "MEDIUM"
        else:
            sensitivity = "LOW"
        comp = components.get(cid)
        comp_name = cid.replace("_", " ").title()
        drivers.append({
            "component": cid,
            "name": comp_name,
            "current_health_pct": round(start_h, 1),
            "projected_health_pct": round(end_h, 1),
            "health_loss_pct": round(loss, 1),
            "effect": (
                f"Drops from {start_h:.0f}% to {end_h:.0f}% over {projection_days:.0f} days"
                + (f" — fault threshold crossed at day {fault_cross:.0f}" if fault_cross else "")
            ),
            "sensitivity": sensitivity,
        })

    drivers.sort(key=lambda d: (d["sensitivity"] == "HIGH", d["health_loss_pct"]), reverse=True)
    top_drivers = drivers[:3]

    # ── What changes recommendation ───────────────────────────────────────────
    if result.days_to_shutdown is not None:
        # Find the component closest to threshold
        binding_comp = None
        min_health = 100.0
        for cid, comp in components.items():
            if comp.health_pct < min_health:
                min_health = comp.health_pct
                binding_comp = cid
        if binding_comp:
            what_changes = (
                f"Servicing {binding_comp.replace('_',' ')} (currently {min_health:.0f}% health) "
                f"would extend the shutdown window. "
                f"Reducing load or ambient temperature also extends component life."
            )
        else:
            what_changes = "Reduce operating load or ambient temperature to extend component life."
    elif result.days_to_first_fault is not None:
        what_changes = (
            f"Scheduling service before day {result.days_to_first_fault:.0f} prevents the "
            f"{result.first_fault_type} fault. Operating at lower load extends the window further."
        )
    else:
        what_changes = (
            "Continue current operating conditions. "
            "Preventive service at next scheduled interval is sufficient."
        )

    # ── Model confidence ──────────────────────────────────────────────────────
    model_confidence = {
        "thermodynamics": "SYNTHETIC — isentropic model with empirical correction factors",
        "degradation_rates": "SYNTHETIC — based on OEM service intervals, not field telemetry",
        "load_multipliers": "SYNTHETIC — stepped approximation (1x/1.3x/2x/3.5x by load band)",
        "ambient_multipliers": "SYNTHETIC — stepped approximation (1x/1.3x/1.5x/2x by temp band)",
        "fault_thresholds": "FIELD_VALIDATED — OEM fault codes and setpoints from Sullair documentation",
        "note": "All rates require validation against real machine sensor history for accuracy.",
    }

    # ── Causal chain narrative ────────────────────────────────────────────────
    if result.cascade_chains:
        chain = result.cascade_chains[0]
        root = chain["root_cause"].replace("_", " ")
        downstream_names = [d["component"].replace("_", " ") for d in chain["downstream"][:2]]
        downstream_str = " and ".join(downstream_names) if downstream_names else "downstream components"
        causal_chain = (
            f"{root.title()} degradation raises discharge temperature, "
            f"which accelerates wear on {downstream_str}, "
            f"ultimately driving the {result.first_fault_type or 'projected fault'}."
        )
    elif result.days_to_shutdown is not None:
        causal_chain = (
            f"Continued operation at current conditions leads to "
            f"{_fault_title(result.first_fault_type or '').lower()} "
            f"in {result.days_to_shutdown:.0f} days."
        )
    elif result.days_to_first_fault is not None:
        causal_chain = (
            f"Normal wear brings {result.first_fault_type} to maintenance threshold "
            f"in {result.days_to_first_fault:.0f} days under current load and ambient conditions."
        )
    else:
        causal_chain = (
            "No dominant failure mechanism active within the projection window. "
            "Machine operating within normal wear parameters."
        )

    return {
        "headline": headline,
        "top_drivers": top_drivers,
        "what_changes_recommendation": what_changes,
        "model_confidence": model_confidence,
        "causal_chain": causal_chain,
    }


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
    """
    Score on (shutdown_score, fault_score) tuples.
    Already-faulted but no shutdown risk beats a clean scenario heading for shutdown.
    """
    best = None
    best_score = (-1, -1)

    for label, result in results.items():
        proj_days = result["projection_days"]
        raw_shut = result.get("days_to_shutdown")
        shut_score = proj_days + 1 if raw_shut is None else raw_shut
        raw_fault = result.get("days_to_first_fault")
        fault_score = proj_days + 1 if raw_fault is None else raw_fault
        score = (shut_score, fault_score)
        if score > best_score:
            best_score = score
            best = label

    if best:
        shut = results[best].get("days_to_shutdown")
        fault = results[best].get("days_to_first_fault")
        if shut is None and fault is None:
            detail = f"no shutdown or new fault projected in {results[best]['projection_days']:.0f}d"
        elif shut is None:
            detail = f"no shutdown projected, first fault at day {fault:.0f}"
        else:
            detail = f"latest shutdown projection at day {shut:.0f}"
        return f"Recommended: '{best}' — {detail}"
    return "No clear recommendation — review scenarios manually"
