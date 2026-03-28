"""
Envelope Explorer — safe operating envelope grid sweep.

find_safe_envelope(state, days, resolution) sweeps a grid of
load_pct × ambient_f and runs lightweight projections for each cell.

Output shape:
{
  "grid": [[{load_pct, ambient_f, status, days_to_fault, days_to_shutdown, binding}, ...]],
  "current_point": {load_pct, ambient_f},
  "binding_constraint": "thermal_valve",
  "safe_load_at_current_ambient": 78,
  "safe_ambient_at_current_load": 95,
  "margin_description": "..."
}
"""

from copy import deepcopy
from typing import Optional
from core.machine_state import MachineState
from simulation.projector import project


def find_safe_envelope(
    state: MachineState,
    days: int = 30,
    resolution: int = 5,
) -> dict:
    """
    Sweep a load_pct × ambient_f grid and classify each cell as safe/degraded/fault/shutdown.

    load_pct  : 0–100 step resolution
    ambient_f : 40–115 step resolution
    ~300 cells at resolution=5
    """
    load_values = list(range(0, 101, resolution))
    ambient_values = list(range(40, 116, resolution))

    current_load = state.load_pct
    current_ambient = state.ambient_f

    grid = []
    binding_counts: dict = {}  # component → how many cells it is binding constraint

    for load in load_values:
        row = []
        for ambient in ambient_values:
            result = project(
                state=state,
                days=days,
                load_pct=float(load),
                ambient_f=float(ambient),
            )
            dtf = result.days_to_first_fault
            dts = result.days_to_shutdown

            if dts is not None:
                status = "shutdown"
            elif dtf is not None:
                status = "fault" if dtf < days * 0.5 else "degraded"
            else:
                status = "safe"

            # Binding component: first component to cross fault threshold
            binding = _find_binding_component(result)
            if binding:
                binding_counts[binding] = binding_counts.get(binding, 0) + 1

            row.append({
                "load_pct": load,
                "ambient_f": ambient,
                "status": status,
                "days_to_fault": round(dtf, 1) if dtf is not None else None,
                "days_to_shutdown": round(dts, 1) if dts is not None else None,
                "binding": binding,
            })
        grid.append(row)

    # Overall binding constraint = component that limits the most cells
    overall_binding = (
        max(binding_counts, key=lambda k: binding_counts[k])
        if binding_counts else None
    )

    # Safe load at current ambient
    safe_load = _max_safe_load(grid, load_values, ambient_values, current_ambient, days)

    # Safe ambient at current load
    safe_ambient = _max_safe_ambient(grid, load_values, ambient_values, current_load, days)

    # Margin description
    margin_description = _build_margin_description(
        current_load, current_ambient, safe_load, safe_ambient, days
    )

    return {
        "grid": grid,
        "current_point": {
            "load_pct": round(current_load, 1),
            "ambient_f": round(current_ambient, 1),
        },
        "binding_constraint": overall_binding,
        "safe_load_at_current_ambient": safe_load,
        "safe_ambient_at_current_load": safe_ambient,
        "margin_description": margin_description,
        "sweep_days": days,
        "resolution": resolution,
    }


def _find_binding_component(result) -> Optional[str]:
    """Return the component that first crosses the fault threshold."""
    earliest_day = None
    earliest_comp = None
    for cid, ann in result.chart_annotations.items():
        if not isinstance(ann, dict):
            continue
        fcd = ann.get("fault_cross_day")
        if fcd is not None and (earliest_day is None or fcd < earliest_day):
            earliest_day = fcd
            earliest_comp = cid
    return earliest_comp


def _max_safe_load(grid, load_values, ambient_values, target_ambient, days) -> Optional[int]:
    """Find maximum safe load at the target ambient temperature."""
    # Find the ambient column closest to target_ambient
    closest_ambient_idx = min(
        range(len(ambient_values)),
        key=lambda i: abs(ambient_values[i] - target_ambient),
    )
    max_safe = None
    for row_idx, load in enumerate(load_values):
        cell = grid[row_idx][closest_ambient_idx]
        if cell["status"] in ("safe", "degraded"):
            max_safe = load
    return max_safe


def _max_safe_ambient(grid, load_values, ambient_values, target_load, days) -> Optional[int]:
    """Find maximum safe ambient at the target load."""
    closest_load_idx = min(
        range(len(load_values)),
        key=lambda i: abs(load_values[i] - target_load),
    )
    max_safe_ambient = None
    for col_idx, ambient in enumerate(ambient_values):
        cell = grid[closest_load_idx][col_idx]
        if cell["status"] in ("safe", "degraded"):
            max_safe_ambient = ambient
    return max_safe_ambient


def _build_margin_description(
    current_load: float,
    current_ambient: float,
    safe_load: Optional[int],
    safe_ambient: Optional[int],
    days: int,
) -> str:
    parts = []

    if safe_ambient is not None:
        ambient_margin = safe_ambient - current_ambient
        if ambient_margin >= 0:
            parts.append(
                f"At {current_load:.0f}% load, safe up to {safe_ambient}°F ambient. "
                f"Currently at {current_ambient:.0f}°F — {ambient_margin:.0f}°F margin."
            )
        else:
            parts.append(
                f"At {current_load:.0f}% load, safe up to {safe_ambient}°F ambient. "
                f"Currently at {current_ambient:.0f}°F — {abs(ambient_margin):.0f}°F into risk zone."
            )
    else:
        parts.append(f"No safe ambient found at {current_load:.0f}% load within {days}-day window.")

    if safe_load is not None:
        load_margin = safe_load - current_load
        if load_margin >= 0:
            parts.append(
                f"At {current_ambient:.0f}°F ambient, safe up to {safe_load}% load "
                f"({load_margin:.0f}% margin)."
            )
        else:
            parts.append(
                f"At {current_ambient:.0f}°F ambient, safe up to {safe_load}% load "
                f"(currently {abs(load_margin):.0f}% over safe threshold)."
            )

    return " ".join(parts) if parts else "Margin data unavailable."
