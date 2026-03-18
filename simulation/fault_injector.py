"""
Fault Injector — Sullair LS110

Direct control over component health and fault flags.
Makes the demo controllable — show any failure chain on demand.
"""

from core.machine_state import MachineState
from core.constants import DEGRADATION_ONSET_PCT, DEGRADATION_FAULT_PCT


class FaultInjector:

    def __init__(self, state: MachineState):
        self.state = state

    # ── Component health control ───────────────────────────────────────────────

    def set_component_health(self, component_id: str, health_pct: float) -> dict:
        """Directly set a component's health percentage."""
        if component_id not in self.state.components:
            return {"error": f"Unknown component: {component_id}",
                    "valid": list(self.state.components.keys())}

        health_pct = max(0.0, min(100.0, health_pct))
        self.state.components[component_id].health_pct = health_pct

        return {
            "component": component_id,
            "health_pct": health_pct,
            "is_degrading": health_pct < DEGRADATION_ONSET_PCT,
            "is_fault_risk": health_pct < DEGRADATION_FAULT_PCT,
            "message": self._health_message(component_id, health_pct),
        }

    def degrade_component(self, component_id: str, by_pct: float) -> dict:
        """Reduce a component's health by a given percentage."""
        if component_id not in self.state.components:
            return {"error": f"Unknown component: {component_id}"}

        current = self.state.components[component_id].health_pct
        new_health = max(0.0, current - by_pct)
        return self.set_component_health(component_id, new_health)

    def service_component(self, component_id: str) -> dict:
        """Reset component to new — simulates replacement/service."""
        if component_id not in self.state.components:
            return {"error": f"Unknown component: {component_id}"}

        self.state.components[component_id].service()
        return {
            "component": component_id,
            "health_pct": 100.0,
            "message": f"{component_id} serviced — reset to 100% health",
        }

    # ── Fault flag control ─────────────────────────────────────────────────────

    def inject_thermal_valve_stuck_open(self) -> dict:
        """
        Thermal valve stuck open — fluid routed through cooler at all times.
        Causes overcooling → condensation → foaming → separator damage.
        """
        self.state.fault_thermal_valve_stuck_open = True
        self.state.fault_thermal_valve_stuck_closed = False
        self.state.components["thermal_valve"].health_pct = 5.0
        return {
            "fault": "thermal_valve_stuck_open",
            "active": True,
            "expected_signature": {
                "T1": "drops below 170°F at low load",
                "P3": "erratic — oil foaming",
                "T1_T2_delta": "narrows as separator loads with foam",
            },
            "chain": "FC_003",
        }

    def inject_thermal_valve_stuck_closed(self) -> dict:
        """
        Thermal valve stuck closed — fluid bypasses cooler entirely.
        Causes rapid overheating → T1 climbs toward shutdown threshold.
        """
        self.state.fault_thermal_valve_stuck_open = False
        self.state.fault_thermal_valve_stuck_closed = True
        self.state.components["thermal_valve"].health_pct = 5.0
        return {
            "fault": "thermal_valve_stuck_closed",
            "active": True,
            "expected_signature": {
                "T1": "climbs rapidly, approaching 200°F",
                "P3": "initially normal, drops as oil thins at high temp",
            },
            "chain": "FC_002_thermal_variant",
        }

    def inject_filter_bypass_open(self) -> dict:
        """
        Filter bypass valve opened — unfiltered oil reaching air end.
        Dangerous: T1 looks normal, P4-P3 delta collapses to near zero.
        """
        self.state.fault_filter_bypass_open = True
        self.state.components["fluid_filter"].health_pct = 20.0
        return {
            "fault": "filter_bypass_open",
            "active": True,
            "expected_signature": {
                "P3": "rises back toward P4 — bypass restores flow",
                "P4_P3_delta": "collapses to <3psi despite degraded filter",
                "T1": "appears normal — dangerous silent fault",
            },
            "chain": "CD_001_silent_bypass",
            "danger": "HIGH — no alarm fires, unfiltered oil reaching bearings",
        }

    def inject_solenoid_failure(self) -> dict:
        """
        Solenoid valve SOL1 stuck closed — cannot signal unload.
        P2 climbs above unload setpoint. Relief valve or shutdown imminent.
        """
        self.state.fault_solenoid_stuck_closed = True
        self.state.components["solenoid_valve"].health_pct = 5.0
        return {
            "fault": "solenoid_stuck_closed",
            "active": True,
            "expected_signature": {
                "P2": "rises above unload setpoint, controller cannot shed load",
                "P1": "tracks upward with P2",
            },
            "chain": "FC_005",
            "warning_window": "seconds to minutes",
        }

    def clear_all_faults(self) -> dict:
        """Remove all injected fault flags."""
        self.state.fault_thermal_valve_stuck_open = False
        self.state.fault_thermal_valve_stuck_closed = False
        self.state.fault_solenoid_stuck_closed = False
        self.state.fault_blowdown_stuck = False
        self.state.fault_filter_bypass_open = False
        return {"message": "All fault flags cleared"}

    # ── Operating condition control ────────────────────────────────────────────

    def set_load(self, load_pct: float) -> dict:
        """Change operating load percentage."""
        load_pct = max(0.0, min(100.0, load_pct))
        old = self.state.load_pct
        self.state.load_pct = load_pct
        return {
            "load_pct": load_pct,
            "previous": old,
            "regime_change": abs(load_pct - old) > 15,
            "message": f"Load changed from {old:.0f}% to {load_pct:.0f}%",
        }

    def set_ambient(self, ambient_f: float) -> dict:
        """Change ambient temperature."""
        old = self.state.ambient_f
        self.state.ambient_f = ambient_f
        return {
            "ambient_f": ambient_f,
            "previous": old,
            "message": f"Ambient changed from {old:.0f}°F to {ambient_f:.0f}°F",
        }

    def set_pressure_setpoint(self, psi: float) -> dict:
        """Change operating pressure setpoint."""
        from analysis.envelope_validator import validate_pressure
        validation = validate_pressure(psi)
        if not validation["valid"]:
            return {"error": validation["reason"]}

        old = self.state.setpoint_psi
        self.state.setpoint_psi = psi
        return {
            "setpoint_psi": psi,
            "previous": old,
            "message": f"Pressure setpoint changed from {old:.0f} to {psi:.0f} psi",
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _health_message(self, component_id: str, health_pct: float) -> str:
        if health_pct >= 70:
            return f"{component_id} healthy at {health_pct:.0f}%"
        elif health_pct >= 30:
            return f"{component_id} degrading at {health_pct:.0f}% — monitoring recommended"
        else:
            return f"{component_id} fault risk at {health_pct:.0f}% — action required"

    def status(self) -> dict:
        return {
            "fault_flags": {
                "thermal_valve_stuck_open": self.state.fault_thermal_valve_stuck_open,
                "thermal_valve_stuck_closed": self.state.fault_thermal_valve_stuck_closed,
                "solenoid_stuck_closed": self.state.fault_solenoid_stuck_closed,
                "blowdown_stuck": self.state.fault_blowdown_stuck,
                "filter_bypass_open": self.state.fault_filter_bypass_open,
            },
            "operating_conditions": {
                "load_pct": self.state.load_pct,
                "ambient_f": self.state.ambient_f,
                "setpoint_psi": self.state.setpoint_psi,
            },
        }
