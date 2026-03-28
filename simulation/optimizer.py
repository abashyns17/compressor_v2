"""
Intervention Optimizer — maintenance bundle ranking.

optimize_maintenance(state, days, outage_hours) enumerates all subsets
of components currently below 60% health, resets each subset to 100%,
runs project(), and ranks bundles by days_to_first_fault.

Output shape:
{
  "constraint": {"outage_hours": 8},
  "optimal_bundle": ["fluid_filter", "separator_element"],
  "outcome": {"days_to_first_fault": 94, "days_to_shutdown": null},
  "vs_no_action": {"days_to_first_fault": 31},
  "gain_days": 63,
  "rationale": "...",
  "alternatives": [...]
}
"""

from copy import deepcopy
from itertools import combinations
from typing import Optional
from core.machine_state import MachineState
from simulation.projector import project

# Estimated service time per component (hours)
_SERVICE_HOURS: dict = {
    "fluid_filter": 1.0,
    "separator_element": 4.0,
    "inlet_filter": 1.0,
    "thermal_valve": 6.0,
    "oil_cooler": 8.0,
    "shaft_seal": 16.0,
    "coupling_element": 8.0,
    "main_motor_bearing": 8.0,
    "solenoid_valve": 2.0,
    "blowdown_valve": 2.0,
}

_HEALTH_THRESHOLD = 60.0  # only consider components below this


def optimize_maintenance(
    state: MachineState,
    days: int = 90,
    outage_hours: float = 8.0,
) -> dict:
    """
    Rank maintenance bundles by outcome improvement.

    Returns top 3 bundles plus no-action baseline.
    """
    # Baseline: no action
    baseline = project(state=state, days=days)
    baseline_fault = baseline.days_to_first_fault
    baseline_shutdown = baseline.days_to_shutdown

    # Find serviceable candidates
    candidates = [
        cid for cid, comp in state.components.items()
        if comp.health_pct < _HEALTH_THRESHOLD
    ]

    if not candidates:
        return {
            "constraint": {"outage_hours": outage_hours},
            "optimal_bundle": [],
            "outcome": {
                "days_to_first_fault": baseline_fault,
                "days_to_shutdown": baseline_shutdown,
            },
            "vs_no_action": {
                "days_to_first_fault": baseline_fault,
                "days_to_shutdown": baseline_shutdown,
            },
            "gain_days": 0,
            "rationale": "All components above 60% health — no maintenance intervention needed.",
            "alternatives": [],
        }

    # Enumerate all non-empty subsets that fit within outage_hours
    feasible_bundles = []
    for r in range(1, len(candidates) + 1):
        for subset in combinations(candidates, r):
            total_hrs = sum(_SERVICE_HOURS.get(c, 4.0) for c in subset)
            if total_hrs <= outage_hours:
                feasible_bundles.append(list(subset))

    if not feasible_bundles:
        # Even the cheapest single component doesn't fit — return single cheapest
        cheapest = min(candidates, key=lambda c: _SERVICE_HOURS.get(c, 4.0))
        feasible_bundles = [[cheapest]]

    # Score each bundle
    scored = []
    for bundle in feasible_bundles:
        sim = deepcopy(state)
        for cid in bundle:
            if cid in sim.components:
                sim.components[cid].service()
        result = project(state=sim, days=days)
        dtf = result.days_to_first_fault
        dts = result.days_to_shutdown
        # Score: shutdown_days (higher = better), then fault_days
        shutdown_score = dts if dts is not None else days + 1
        fault_score = dtf if dtf is not None else days + 1
        scored.append({
            "bundle": bundle,
            "days_to_first_fault": dtf,
            "days_to_shutdown": dts,
            "shutdown_score": shutdown_score,
            "fault_score": fault_score,
            "service_hours": sum(_SERVICE_HOURS.get(c, 4.0) for c in bundle),
        })

    # Sort best first: highest shutdown_score, then fault_score
    scored.sort(key=lambda x: (x["shutdown_score"], x["fault_score"]), reverse=True)

    top = scored[:3]
    best = top[0]

    # Gain days vs baseline
    best_fault = best["days_to_first_fault"]
    if baseline_fault is not None and best_fault is not None:
        gain_days = round(best_fault - baseline_fault, 1)
    elif baseline_fault is not None and best_fault is None:
        gain_days = round(days - baseline_fault, 1)
    else:
        gain_days = 0

    rationale = _build_rationale(best["bundle"], best, baseline_fault, baseline_shutdown, days)

    alternatives = []
    for entry in top[1:]:
        alt_gain = None
        if baseline_fault is not None:
            if entry["days_to_first_fault"] is not None:
                alt_gain = round(entry["days_to_first_fault"] - baseline_fault, 1)
            else:
                alt_gain = round(days - baseline_fault, 1)
        alternatives.append({
            "bundle": entry["bundle"],
            "outcome": {
                "days_to_first_fault": entry["days_to_first_fault"],
                "days_to_shutdown": entry["days_to_shutdown"],
            },
            "service_hours": entry["service_hours"],
            "gain_days": alt_gain,
            "rationale": _build_rationale(
                entry["bundle"], entry, baseline_fault, baseline_shutdown, days
            ),
        })

    return {
        "constraint": {"outage_hours": outage_hours},
        "optimal_bundle": best["bundle"],
        "outcome": {
            "days_to_first_fault": best["days_to_first_fault"],
            "days_to_shutdown": best["days_to_shutdown"],
        },
        "vs_no_action": {
            "days_to_first_fault": baseline_fault,
            "days_to_shutdown": baseline_shutdown,
        },
        "gain_days": gain_days,
        "rationale": rationale,
        "service_hours_required": best["service_hours"],
        "alternatives": alternatives,
    }


def _build_rationale(
    bundle: list,
    outcome: dict,
    baseline_fault: Optional[float],
    baseline_shutdown: Optional[float],
    days: int,
) -> str:
    bundle_str = " + ".join(c.replace("_", " ") for c in bundle)
    dtf = outcome["days_to_first_fault"]
    dts = outcome["days_to_shutdown"]

    if dts is None and dtf is None:
        outcome_str = f"no fault or shutdown projected within {days}-day window"
    elif dts is None and dtf is not None:
        outcome_str = f"no shutdown projected; first fault at day {dtf:.0f}"
    else:
        outcome_str = f"shutdown projected at day {dts:.0f}"

    baseline_str = ""
    if baseline_fault is not None:
        baseline_str = f" Baseline without service: first fault at day {baseline_fault:.0f}."
    elif baseline_shutdown is not None:
        baseline_str = f" Baseline without service: shutdown at day {baseline_shutdown:.0f}."

    return f"Servicing [{bundle_str}] results in {outcome_str}.{baseline_str}"
