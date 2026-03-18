"""
Analysis routes — correlations, predictions, risk assessment, fault tree.
"""

from fastapi import APIRouter, Query
from typing import Optional
import api.routes.state as state_module
from analysis.correlator import analyse_to_dict
from analysis.predictor import (
    predict_filter_delta_trend, predict_t1_trend,
    component_risk_summary, generate_plain_language_assessment,
)

router = APIRouter(prefix="/analysis", tags=["analysis"])

# Track previous finding state so we only log when something actually changes
_prev_findings: dict = {}   # correlation_id → severity


@router.get("/correlations")
def get_correlations():
    """
    Run all cross-correlation checks against current machine state.
    Returns findings sorted by severity — CRITICAL first.
    Logs to event_log when new findings appear or severity changes.
    """
    global _prev_findings

    if state_module._state is None:
        return {"findings": [], "message": "No scenario loaded"}

    findings = analyse_to_dict(state_module._state)

    # Detect new or changed findings and log them
    from data.sensor_logger import log_event
    current = {f["correlation_id"]: f["severity"] for f in findings}

    for cid, sev in current.items():
        prev_sev = _prev_findings.get(cid)
        if prev_sev is None:
            # New finding appeared
            f = next(x for x in findings if x["correlation_id"] == cid)
            log_event("FAULT" if sev in ("CRITICAL","ACTION") else "INJECT",
                      f"{cid} · {sev} · {f['pattern']}")
        elif prev_sev != sev:
            # Severity escalated or de-escalated
            log_event("FAULT" if sev in ("CRITICAL","ACTION") else "INJECT",
                      f"{cid} severity changed {prev_sev} → {sev}")

    # Detect cleared findings
    for cid in list(_prev_findings.keys()):
        if cid not in current:
            log_event("CLEAR", f"{cid} cleared")

    # If all findings cleared
    if not findings and _prev_findings:
        log_event("CLEAR", "No active findings")

    _prev_findings = current

    return {
        "finding_count": len(findings),
        "findings": findings,
    }


@router.get("/fta")
def get_fault_tree():
    """
    Return the fault tree structure merged with live machine state.
    """
    active = {}
    if state_module._state is not None:
        for f in analyse_to_dict(state_module._state):
            cid = f["correlation_id"]
            if cid not in active or _sev_rank(f["severity"]) < _sev_rank(active[cid]["severity"]):
                active[cid] = f

    tree = _build_fta_tree(active)
    return {
        "device": "Sullair LS110",
        "active_finding_ids": list(active.keys()),
        "tree": tree,
    }


def _sev_rank(sev: str) -> int:
    return {"CRITICAL": 0, "ACTION": 1, "WARNING": 2, "INFO": 3}.get(sev, 99)


def _node(id, label, desc, conf, sensors, gate=None, children=None, active_map=None):
    finding = (active_map or {}).get(id)
    return {
        "id": id,
        "label": label,
        "description": desc,
        "confidence": conf,
        "gate": gate,
        "sensors": sensors,
        "active": finding is not None,
        "active_finding": finding,
        "children": children or [],
    }


def _build_fta_tree(active: dict) -> dict:
    n = lambda *a, **kw: _node(*a, active_map=active, **kw)

    return n(
        "TOP", "Compressor failure / shutdown",
        "Any condition causing STS controller trip or unplanned downtime",
        "MANUAL", [], gate="OR",
        children=[
            n("THERMAL", "Thermal shutdown (T1 ≥ 200°F)",
              "STS hard-stops when wet discharge temp exceeds safety limit. Warning at 195°F.",
              "MANUAL", ["T1"], gate="OR",
              children=[
                n("CORR_001", "Fluid filter differential pressure fault",
                  "P4–P3 delta rising as filter loads. Fault at 20 psi.",
                  "MANUAL", ["P3","P4"],
                  children=[
                    n("CD_001", "Silent filter bypass (composite)",
                      "P4–P3 < 3 psi + T1 normal + >1500 hrs runtime.",
                      "DERIVED", ["P3","P4","T1"]),
                  ]),
                n("CORR_002", "Oil flow vs discharge temperature",
                  "P3 dropping reduces oil flow → less cooling → T1 rises.",
                  "DERIVED", ["P3","T1"]),
                n("CORR_004", "T1 above thermodynamic model baseline",
                  "T1 predictable from load% and ambient. Deviation precedes threshold breach.",
                  "SYNTHETIC", ["T1","ambient_temp","load_pct"],
                  children=[
                    n("CD_002", "Thermal valve stuck open (composite)",
                      "T1 below model by 8°F+ at >50% load — overcooling risk.",
                      "DERIVED", ["T1","ambient_temp","load_pct"]),
                  ]),
              ]),
            n("OVERPRESSURE", "Overpressure — P2 above unload setpoint",
              "Machine cannot unload.",
              "MANUAL", ["P1","P2"], gate="OR",
              children=[
                n("CORR_005", "P1/P2 pressure relationship divergence",
                  "P1 rising faster than P2 = separator restriction.",
                  "DERIVED", ["P1","P2"],
                  children=[
                    n("SOLENOID", "Solenoid / blowdown valve failure",
                      "Both P1 and P2 rising above unload setpoint.",
                      "MANUAL", ["P1","P2"]),
                    n("MPV", "Minimum pressure valve fault",
                      "P2 rising, P1 flat.",
                      "DERIVED", ["P1","P2"]),
                  ]),
              ]),
            n("SEPARATOR", "Separator element failure",
              "Oil carryover to service line.",
              "DERIVED", ["T1","T2","P1"], gate="OR",
              children=[
                n("CORR_003", "T1–T2 separator efficiency delta",
                  "Gap closure precedes dP alarm. T2 > T1 = element ruptured.",
                  "DERIVED", ["T1","T2"],
                  children=[
                    n("CD_003", "Pre-alarm separator failure (composite)",
                      "T1–T2 < 8°F AND P1 elevated.",
                      "DERIVED", ["T1","T2","P1"]),
                  ]),
              ]),
            n("INLET", "Inlet restriction / motor overload",
              "PSW1 vacuum rising — inlet air filter clogged.",
              "MANUAL", ["PSW1"], gate="OR",
              children=[
                n("CORR_006", "Inlet vs fluid restriction differentiation",
                  "PSW1 and P3 hydraulically independent.",
                  "DERIVED", ["PSW1","P3"],
                  children=[
                    n("ENV_CONTAM", "Environmental contamination",
                      "Both PSW1 rising and P3 dropping simultaneously.",
                      "DERIVED", ["PSW1","P3"]),
                  ]),
              ]),
            n("REGIME", "Operating regime shift",
              "Sustained load increase reweights all failure timelines.",
              "SYNTHETIC", ["T1","load_pct","P2"]),
        ]
    )


@router.get("/trends/filter_delta")
def get_filter_delta_trend(hours_back: float = Query(48.0)):
    return predict_filter_delta_trend(hours_back)


@router.get("/trends/t1")
def get_t1_trend(hours_back: float = Query(48.0)):
    return predict_t1_trend(hours_back)


@router.get("/risk")
def get_risk_summary():
    if state_module._state is None:
        return {"risks": [], "message": "No scenario loaded"}
    return {
        "risks": component_risk_summary(state_module._state),
        "operating_conditions": {
            "load_pct": state_module._state.load_pct,
            "ambient_f": state_module._state.ambient_f,
            "setpoint_psi": state_module._state.setpoint_psi,
        },
    }


@router.get("/assessment")
def get_plain_language_assessment():
    if state_module._state is None:
        return {"assessment": "No scenario loaded"}
    return {
        "assessment": generate_plain_language_assessment(state_module._state),
        "total_hours": state_module._state.total_hours,
    }
