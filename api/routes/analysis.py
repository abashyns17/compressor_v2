"""
Analysis routes — correlations, predictions, risk assessment.
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
    """
    Component risk summary — current health, degradation rates,
    and estimated time to fault for each component.
    """
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
    """
    Plain language assessment — the 'you're fucked in X days' summary.
    Intended for the agent to surface to the engineer.
    """
    if state_module._state is None:
        return {"assessment": "No scenario loaded"}

    return {
        "assessment": generate_plain_language_assessment(state_module._state),
        "total_hours": state_module._state.total_hours,
    }
