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


@router.get("/correlations")
def get_correlations():
    """
    Run all cross-correlation checks against current machine state.
    Returns findings sorted by severity — CRITICAL first.
    """
    if state_module._state is None:
        return {"findings": [], "message": "No scenario loaded"}

    findings = analyse_to_dict(state_module._state)
    return {
        "finding_count": len(findings),
        "findings": findings,
    }


@router.get("/fta")
def get_fault_tree():
    """
    Return the fault tree structure for the LS110, merged with live machine state.

    Each node carries:
      - id, label, description, confidence, gate (AND/OR)
      - sensors: list of sensor IDs involved
      - active: true if this node's correlation is currently firing
      - active_finding: the live finding dict if active (includes severity, interpretation)
      - children: nested list

    The 'active' flag is what the FTA visualiser uses to highlight live paths.
    Frontend can open with ?highlight=CORR_001 to pre-select a node.
    """
    # Get live findings so we can annotate the tree
    active = {}
    if state_module._state is not None:
        for f in analyse_to_dict(state_module._state):
            cid = f["correlation_id"]
            # Keep highest-severity finding per correlation_id
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
                  "P4–P3 delta rising as filter loads. Fault at 20 psi. Earlier: bypass opens silently.",
                  "MANUAL", ["P3","P4"],
                  children=[
                    n("CD_001", "Silent filter bypass (composite)",
                      "P4–P3 < 3 psi + T1 normal + >1500 hrs runtime. No alarm fires — only composite logic detects it.",
                      "DERIVED", ["P3","P4","T1"]),
                  ]),

                n("CORR_002", "Oil flow vs discharge temperature",
                  "P3 dropping reduces oil flow → less cooling → T1 rises. Inverse correlation.",
                  "DERIVED", ["P3","T1"]),

                n("CORR_004", "T1 above thermodynamic model baseline",
                  "T1 predictable from load% and ambient. Deviation precedes threshold breach.",
                  "SYNTHETIC", ["T1","ambient_temp","load_pct"],
                  children=[
                    n("CD_002", "Thermal valve stuck open (composite)",
                      "T1 below model by 8°F+ at >50% load — overcooling, condensation risk.",
                      "DERIVED", ["T1","ambient_temp","load_pct"]),
                  ]),
              ]),

            n("OVERPRESSURE", "Overpressure — P2 above unload setpoint",
              "Machine cannot unload. STS trips on high pressure.",
              "MANUAL", ["P1","P2"], gate="OR",
              children=[

                n("CORR_005", "P1/P2 pressure relationship divergence",
                  "P1 rising faster than P2 = separator restriction. Both rising = solenoid/blowdown failure.",
                  "DERIVED", ["P1","P2"],
                  children=[
                    n("SOLENOID", "Solenoid / blowdown valve failure",
                      "P1 and P2 both rising above unload setpoint — unload logic not functioning.",
                      "MANUAL", ["P1","P2"]),
                    n("MPV", "Minimum pressure valve fault",
                      "P2 rising, P1 flat — sump not tracking line pressure.",
                      "DERIVED", ["P1","P2"]),
                  ]),
              ]),

            n("SEPARATOR", "Separator element failure",
              "Oil carryover to service line / sump overpressure.",
              "DERIVED", ["T1","T2","P1"], gate="OR",
              children=[

                n("CORR_003", "T1–T2 separator efficiency delta",
                  "Normal delta 5–25°F. Gap closure precedes dP alarm. T2 > T1 = element ruptured.",
                  "DERIVED", ["T1","T2"],
                  children=[
                    n("CD_003", "Pre-alarm separator failure (composite)",
                      "T1–T2 < 8°F AND P1 elevated. dP alarm not yet fired but failure imminent.",
                      "DERIVED", ["T1","T2","P1"]),
                  ]),
              ]),

            n("INLET", "Inlet restriction / motor overload",
              "PSW1 vacuum rising — inlet air filter clogged. Motor works harder.",
              "MANUAL", ["PSW1"], gate="OR",
              children=[

                n("CORR_006", "Inlet vs fluid restriction differentiation",
                  "PSW1 and P3 are hydraulically independent. Both rising = environment; one alone = filter.",
                  "DERIVED", ["PSW1","P3"],
                  children=[
                    n("ENV_CONTAM", "Environmental contamination",
                      "Both PSW1 rising and P3 dropping simultaneously — installation problem.",
                      "DERIVED", ["PSW1","P3"]),
                  ]),
              ]),

            n("REGIME", "Operating regime shift — all chains accelerate",
              "Sustained load increase >15% for 4+ hrs reweights all failure timelines.",
              "SYNTHETIC", ["T1","load_pct","P2"]),
        ]
    )


@router.get("/trends/filter_delta")
def get_filter_delta_trend(
    hours_back: float = Query(48.0, description="Hours of history to analyse")
):
    """P4-P3 delta trend — predict time to FILTER MAINT REQD."""
    return predict_filter_delta_trend(hours_back)


@router.get("/trends/t1")
def get_t1_trend(
    hours_back: float = Query(48.0, description="Hours of history to analyse")
):
    """T1 trend — predict approach to warning and shutdown thresholds."""
    return predict_t1_trend(hours_back)


@router.get("/risk")
def get_risk_summary():
    """Component risk summary."""
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
    """Plain language assessment."""
    if state_module._state is None:
        return {"assessment": "No scenario loaded"}

    return {
        "assessment": generate_plain_language_assessment(state_module._state),
        "total_hours": state_module._state.total_hours,
    }
