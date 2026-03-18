"""
Component Registry — Sullair LS110
Each component has a health value (100=new, 0=failed), degradation rate,
and the sensor signatures its degradation produces.
"""

from dataclasses import dataclass, field
from typing import Optional
from core.constants import DEGRADATION_RATES, DEGRADATION_ONSET_PCT, DEGRADATION_FAULT_PCT


@dataclass
class Component:
    id: str
    name: str
    health_pct: float = 100.0           # current health 0-100
    operating_hours: float = 0.0        # hours on this component since last service
    service_interval_hrs: Optional[float] = None
    base_degradation_rate: float = 0.0  # % health lost per 100 hrs at normal conditions
    is_serviceable: bool = True         # can be replaced/serviced
    notes: str = ""

    @property
    def is_degrading(self) -> bool:
        return self.health_pct < DEGRADATION_ONSET_PCT

    @property
    def is_fault_risk(self) -> bool:
        return self.health_pct < DEGRADATION_FAULT_PCT

    @property
    def hours_to_service(self) -> Optional[float]:
        if self.service_interval_hrs is None:
            return None
        return max(0.0, self.service_interval_hrs - self.operating_hours)

    @property
    def overdue_hours(self) -> float:
        if self.service_interval_hrs is None:
            return 0.0
        return max(0.0, self.operating_hours - self.service_interval_hrs)

    def degrade(self, hours: float, load_multiplier: float = 1.0, temp_multiplier: float = 1.0):
        """Advance degradation by given hours under operating conditions."""
        rate = self.base_degradation_rate * load_multiplier * temp_multiplier
        loss = (rate / 100.0) * hours
        self.health_pct = max(0.0, self.health_pct - loss)
        self.operating_hours += hours

    def service(self):
        """Reset component after service/replacement."""
        self.health_pct = 100.0
        self.operating_hours = 0.0

    def project_health_at(self, future_hours: float,
                           load_multiplier: float = 1.0,
                           temp_multiplier: float = 1.0) -> float:
        """Return projected health after future_hours of operation."""
        rate = self.base_degradation_rate * load_multiplier * temp_multiplier
        loss = (rate / 100.0) * future_hours
        return max(0.0, self.health_pct - loss)

    def hours_until_fault(self,
                           load_multiplier: float = 1.0,
                           temp_multiplier: float = 1.0) -> Optional[float]:
        """Estimate hours until health reaches fault threshold."""
        rate = self.base_degradation_rate * load_multiplier * temp_multiplier
        if rate <= 0:
            return None
        health_margin = self.health_pct - DEGRADATION_FAULT_PCT
        if health_margin <= 0:
            return 0.0
        return (health_margin / rate) * 100.0


def build_component_registry(
    fluid_filter_hrs: float = 0.0,
    separator_hrs: float = 0.0,
    inlet_filter_hrs: float = 0.0,
    total_machine_hrs: float = 5000.0,
) -> dict:
    """
    Build a component registry for the LS110.
    Accepts current operating hours per serviceable component
    so the registry reflects a real machine's state.
    """

    def health_from_hours(component_id: str, hrs: float) -> float:
        rate = DEGRADATION_RATES.get(component_id, 0.1)
        loss = (rate / 100.0) * hrs
        return max(0.0, 100.0 - loss)

    components = {

        # ── Serviceable components (tracked by maintenance interval) ──────────

        "fluid_filter": Component(
            id="fluid_filter",
            name="Fluid filter element",
            health_pct=health_from_hours("fluid_filter", fluid_filter_hrs),
            operating_hours=fluid_filter_hrs,
            service_interval_hrs=2000.0,
            base_degradation_rate=DEGRADATION_RATES["fluid_filter"],
            notes="P4-P3 delta >20psi triggers FILTER MAINT REQD. Change every 2000hrs."
        ),

        "separator_element": Component(
            id="separator_element",
            name="Air/oil separator element",
            health_pct=health_from_hours("separator_element", separator_hrs),
            operating_hours=separator_hrs,
            service_interval_hrs=8000.0,
            base_degradation_rate=DEGRADATION_RATES["separator_element"],
            notes="Separator dP >10psi triggers SEPARATOR MAINT REQD. Change every 8000hrs."
        ),

        "inlet_filter": Component(
            id="inlet_filter",
            name="Inlet air filter element",
            health_pct=health_from_hours("inlet_filter", inlet_filter_hrs),
            operating_hours=inlet_filter_hrs,
            service_interval_hrs=2000.0,
            base_degradation_rate=DEGRADATION_RATES["inlet_filter"],
            notes="PSW1 vacuum switch triggers AIR FILTER MAINT REQD at 22 in.w.c."
        ),

        # ── Long-life components (tracked by machine hours) ───────────────────

        "thermal_valve": Component(
            id="thermal_valve",
            name="Thermal mixing valve",
            health_pct=health_from_hours("thermal_valve", total_machine_hrs),
            operating_hours=total_machine_hrs,
            service_interval_hrs=None,
            base_degradation_rate=DEGRADATION_RATES["thermal_valve"],
            notes="Opens at 185°F. Failure modes: stuck-open (overcooling) or stuck-closed (overheating)."
        ),

        "oil_cooler": Component(
            id="oil_cooler",
            name="Oil cooler",
            health_pct=health_from_hours("oil_cooler", total_machine_hrs),
            operating_hours=total_machine_hrs,
            service_interval_hrs=None,
            base_degradation_rate=DEGRADATION_RATES["oil_cooler"],
            notes="Air-cooled: radiator type. Fouling reduces heat transfer efficiency."
        ),

        "shaft_seal": Component(
            id="shaft_seal",
            name="Shaft seal",
            health_pct=health_from_hours("shaft_seal", total_machine_hrs),
            operating_hours=total_machine_hrs,
            service_interval_hrs=None,
            base_degradation_rate=DEGRADATION_RATES["shaft_seal"],
            notes="Seal wear causes oil leakage. Accelerates at higher pressures and temperatures."
        ),

        "coupling_element": Component(
            id="coupling_element",
            name="Drive coupling element",
            health_pct=health_from_hours("coupling_element", total_machine_hrs),
            operating_hours=total_machine_hrs,
            service_interval_hrs=None,
            base_degradation_rate=DEGRADATION_RATES["coupling_element"],
            notes="Jaw-type elastomeric element. Misalignment accelerates wear."
        ),

        "main_motor_bearing": Component(
            id="main_motor_bearing",
            name="Main motor bearing",
            health_pct=health_from_hours("main_motor_bearing", total_machine_hrs),
            operating_hours=total_machine_hrs,
            service_interval_hrs=None,
            base_degradation_rate=DEGRADATION_RATES["main_motor_bearing"],
            notes="Grease per motor nameplate interval. Failure causes vibration and overheating."
        ),

        "solenoid_valve": Component(
            id="solenoid_valve",
            name="Solenoid valve SOL1",
            health_pct=health_from_hours("solenoid_valve", total_machine_hrs),
            operating_hours=total_machine_hrs,
            service_interval_hrs=None,
            base_degradation_rate=DEGRADATION_RATES["solenoid_valve"],
            notes="3-way normally-open. Controls load/unload pneumatics. Failure = unload failure."
        ),

        "blowdown_valve": Component(
            id="blowdown_valve",
            name="Blowdown valve",
            health_pct=health_from_hours("blowdown_valve", total_machine_hrs),
            operating_hours=total_machine_hrs,
            service_interval_hrs=None,
            base_degradation_rate=DEGRADATION_RATES["blowdown_valve"],
            notes="Vents sump to ~25psi on unload. Cycling count drives wear."
        ),
    }

    return components
