"""
Scenario Engine — Sullair LS110

Three operating modes:
  normal   — healthy machine, realistic load variation
  stress   — one or more components degrading toward fault
  terminal — cascade in progress, shutdown imminent

Each mode returns a configured MachineState ready to run.
"""

import random
from copy import deepcopy
from core.machine_state import MachineState
from core.components import build_component_registry


def _normal_state() -> MachineState:
    """
    Healthy machine. Typical industrial operation.
    All components within service intervals, normal load variation.
    """
    state = MachineState(
        load_pct=random.uniform(65, 80),
        ambient_f=random.uniform(68, 82),
        setpoint_psi=110.0,
        total_hours=random.uniform(3000, 6000),
    )
    state.components = build_component_registry(
        fluid_filter_hrs=random.uniform(200, 800),
        separator_hrs=random.uniform(1000, 4000),
        inlet_filter_hrs=random.uniform(300, 1000),
        total_machine_hrs=state.total_hours,
    )
    return state


def _stress_state(fault_type: str = "fluid_filter") -> MachineState:
    """
    Machine under stress — component degrading, approaching fault threshold.
    Default stress scenario is fluid filter restriction.

    fault_type options:
      fluid_filter     — filter clogging, P3 dropping, T1 rising
      inlet_filter     — inlet restriction, PSW1 rising
      thermal_valve    — overcooling or overheating depending on mode
      separator        — separator restriction, P1 rising
      solenoid         — unload failure, P2 climbing
    """
    state = MachineState(
        load_pct=random.uniform(75, 90),
        ambient_f=random.uniform(78, 92),
        setpoint_psi=110.0,
        total_hours=random.uniform(6000, 9000),
    )

    state.components = build_component_registry(
        fluid_filter_hrs=1800.0,
        separator_hrs=6000.0,
        inlet_filter_hrs=1500.0,
        total_machine_hrs=state.total_hours,
    )

    # Degrade the target component to stress zone
    if fault_type == "fluid_filter":
        state.components["fluid_filter"].health_pct = random.uniform(35, 50)
        state.components["fluid_filter"].operating_hours = 1900.0

    elif fault_type == "inlet_filter":
        state.components["inlet_filter"].health_pct = random.uniform(30, 45)
        state.components["inlet_filter"].operating_hours = 1800.0

    elif fault_type == "thermal_valve":
        state.components["thermal_valve"].health_pct = random.uniform(25, 45)

    elif fault_type == "separator":
        state.components["separator_element"].health_pct = random.uniform(30, 45)
        state.components["separator_element"].operating_hours = 7500.0

    elif fault_type == "solenoid":
        state.components["solenoid_valve"].health_pct = random.uniform(25, 40)

    return state


def _terminal_state(fault_type: str = "fluid_filter") -> MachineState:
    """
    Cascade in progress. Fault threshold breached or imminent.
    Multiple components typically affected.
    """
    state = _stress_state(fault_type)

    # Push the primary fault component deeper into failure zone
    if fault_type == "fluid_filter":
        state.components["fluid_filter"].health_pct = random.uniform(10, 25)
        # Secondary: thermal effects start hitting air end
        state.components["oil_cooler"].health_pct = random.uniform(40, 60)
        state.load_pct = random.uniform(85, 95)
        state.ambient_f = random.uniform(88, 100)

    elif fault_type == "separator":
        state.components["separator_element"].health_pct = random.uniform(5, 20)
        state.components["separator_element"].operating_hours = 8500.0
        state.load_pct = random.uniform(85, 95)

    elif fault_type == "thermal_valve":
        state.components["thermal_valve"].health_pct = random.uniform(5, 15)
        state.fault_thermal_valve_stuck_open = True

    elif fault_type == "solenoid":
        state.components["solenoid_valve"].health_pct = random.uniform(5, 15)
        state.fault_solenoid_stuck_closed = True

    return state


# Public scenario factory
SCENARIO_BUILDERS = {
    "normal":            _normal_state,
    "stress_filter":     lambda: _stress_state("fluid_filter"),
    "stress_inlet":      lambda: _stress_state("inlet_filter"),
    "stress_thermal":    lambda: _stress_state("thermal_valve"),
    "stress_separator":  lambda: _stress_state("separator"),
    "stress_solenoid":   lambda: _stress_state("solenoid"),
    "terminal_filter":   lambda: _terminal_state("fluid_filter"),
    "terminal_separator":lambda: _terminal_state("separator"),
    "terminal_thermal":  lambda: _terminal_state("thermal_valve"),
    "terminal_solenoid": lambda: _terminal_state("solenoid"),
}


def build_scenario(name: str) -> MachineState:
    """
    Factory function. Returns a MachineState configured for the named scenario.
    """
    builder = SCENARIO_BUILDERS.get(name)
    if not builder:
        raise ValueError(
            f"Unknown scenario '{name}'. "
            f"Valid options: {list(SCENARIO_BUILDERS.keys())}"
        )
    return builder()


def list_scenarios() -> list:
    return list(SCENARIO_BUILDERS.keys())
