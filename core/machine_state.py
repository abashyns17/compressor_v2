"""
Machine State Model — Sullair LS110

Central state object. Holds operating conditions, component registry,
and computes sensor readings from physics-based models.
"""

from dataclasses import dataclass, field
from typing import Optional

from core.components import Component, build_component_registry
from core.constants import (
    DISCHARGE_TEMP_SHUTDOWN_F, DISCHARGE_TEMP_WARNING_F,
    FLUID_FILTER_DELTA_P_FAULT_PSI, SEPARATOR_DELTA_P_FAULT_PSI,
    INLET_FILTER_VACUUM_FAULT_WC,
)
from core.thermodynamics import (
    compute_T1, compute_T2, compute_PSW1,
    compute_P1, compute_P2, compute_P3, compute_P4,
    get_load_multiplier, get_ambient_multiplier,
    oil_flow_factor_from_filter_health,
    thermal_valve_correction,
)


@dataclass
class SensorReading:
    P1: float = 0.0
    P2: float = 0.0
    P3: float = 0.0
    P4: float = 0.0
    T1: float = 0.0
    T2: float = 0.0
    PSW1: float = 0.0
    load_pct: float = 0.0
    ambient_f: float = 68.0

    @property
    def P4_P3_delta(self) -> float:
        return round(self.P4 - self.P3, 2)

    @property
    def T1_T2_delta(self) -> float:
        return round(self.T1 - self.T2, 1)

    def to_dict(self) -> dict:
        return {
            "P1": round(self.P1, 2),
            "P2": round(self.P2, 2),
            "P3": round(self.P3, 2),
            "P4": round(self.P4, 2),
            "T1": round(self.T1, 1),
            "T2": round(self.T2, 1),
            "PSW1": round(self.PSW1, 2),
            "load_pct": round(self.load_pct, 1),
            "ambient_f": round(self.ambient_f, 1),
            "P4_P3_delta": self.P4_P3_delta,
            "T1_T2_delta": self.T1_T2_delta,
        }


class MachineState:

    def __init__(
        self,
        total_hours: float = 5000.0,
        load_pct: float = 75.0,
        ambient_f: float = 77.0,
        setpoint_psi: float = 110.0,
        fluid_filter_hrs: float = 0.0,
        separator_hrs: float = 0.0,
        inlet_filter_hrs: float = 0.0,
        scenario_name: str = "normal",
    ):
        self.total_hours = total_hours
        self.load_pct = load_pct
        self.ambient_f = ambient_f
        self.setpoint_psi = setpoint_psi
        self.scenario_name = scenario_name

        # Fault injection flags
        self.fault_solenoid_stuck_closed: bool = False
        self.fault_thermal_valve_stuck_open: bool = False
        self.fault_thermal_valve_stuck_closed: bool = False

        self.components = build_component_registry(
            fluid_filter_hrs=fluid_filter_hrs,
            separator_hrs=separator_hrs,
            inlet_filter_hrs=inlet_filter_hrs,
            total_machine_hrs=total_hours,
        )

    def compute_sensors(self) -> SensorReading:
        components = self.components

        filter_health = components["fluid_filter"].health_pct
        oil_flow = oil_flow_factor_from_filter_health(filter_health)

        P1 = compute_P1(self.setpoint_psi, self.load_pct, components["separator_element"].health_pct)
        P2 = compute_P2(self.setpoint_psi, self.load_pct, components["solenoid_valve"].health_pct)

        if self.fault_solenoid_stuck_closed and P2 > self.setpoint_psi + 10:
            p2_base = self.setpoint_psi + 15.0 + components["solenoid_valve"].health_pct * -0.1
            P2 = max(P2, p2_base)

        P3 = compute_P3(P1, oil_flow, components["separator_element"].health_pct)
        P4 = compute_P4(P3, filter_health)

        tv_health = components["thermal_valve"].health_pct
        if self.fault_thermal_valve_stuck_open:
            tv_health = 0.0
        elif self.fault_thermal_valve_stuck_closed:
            tv_health = 200.0   # sentinel: valve fully closed

        T1_base = compute_T1(self.ambient_f, self.load_pct, P1, P2, oil_flow,
                              components["oil_cooler"].health_pct)
        T1 = thermal_valve_correction(T1_base, self.ambient_f, tv_health, self.load_pct)
        T2 = compute_T2(T1, components["separator_element"].health_pct)
        PSW1 = compute_PSW1(components["inlet_filter"].health_pct)

        return SensorReading(
            P1=P1, P2=P2, P3=P3, P4=P4,
            T1=T1, T2=T2, PSW1=PSW1,
            load_pct=self.load_pct,
            ambient_f=self.ambient_f,
        )

    def advance(self, hours: float):
        load_mult = get_load_multiplier(self.load_pct)
        temp_mult = get_ambient_multiplier(self.ambient_f)
        for component in self.components.values():
            component.degrade(hours, load_mult, temp_mult)
        self.total_hours += hours

    def get_active_faults(self) -> list:
        reading = self.compute_sensors()
        faults = []

        if reading.T1 >= DISCHARGE_TEMP_SHUTDOWN_F:
            faults.append({"code": "HIGH_TEMP_T1", "severity": "SHUTDOWN",
                           "value": reading.T1, "threshold": DISCHARGE_TEMP_SHUTDOWN_F})

        if reading.T1 >= DISCHARGE_TEMP_WARNING_F:
            faults.append({"code": "TEMP_T1_WARNING", "severity": "WARNING",
                           "value": reading.T1, "threshold": DISCHARGE_TEMP_WARNING_F})

        if reading.P4_P3_delta >= FLUID_FILTER_DELTA_P_FAULT_PSI:
            faults.append({"code": "FILTER_MAINT_REQD", "severity": "MAINTENANCE",
                           "value": reading.P4_P3_delta, "threshold": FLUID_FILTER_DELTA_P_FAULT_PSI})

        if reading.PSW1 >= INLET_FILTER_VACUUM_FAULT_WC:
            faults.append({"code": "AIR_FILTER_MAINT_REQD", "severity": "MAINTENANCE",
                           "value": reading.PSW1, "threshold": INLET_FILTER_VACUUM_FAULT_WC})

        if self.fault_solenoid_stuck_closed and reading.P2 > self.setpoint_psi + 10:
            faults.append({"code": "HIGH_PRESS_P2", "severity": "SHUTDOWN",
                           "value": reading.P2, "threshold": self.setpoint_psi + 10})

        return faults

    def summary(self) -> dict:
        reading = self.compute_sensors()
        return {
            "machine": "Sullair LS110",
            "scenario_name": self.scenario_name,
            "total_hours": round(self.total_hours, 0),
            "operating_conditions": {
                "load_pct": self.load_pct,
                "ambient_f": self.ambient_f,
                "setpoint_psi": self.setpoint_psi,
            },
            "sensors": reading.to_dict(),
            "active_faults": self.get_active_faults(),
            "component_health": {
                cid: {
                    "health_pct": round(c.health_pct, 1),
                    "operating_hours": round(c.operating_hours, 0),
                    "hours_to_service": round(c.hours_to_service, 0) if c.hours_to_service else None,
                    "overdue_hours": round(c.overdue_hours, 0),
                    "is_fault_risk": c.is_fault_risk,
                    "last_service_hrs": round(self.total_hours - c.operating_hours, 0),
                    "service_interval_hrs": c.service_interval_hrs,
                    "hours_to_fault": round(h2f, 0) if (h2f := c.hours_until_fault()) is not None else None,
                }
                for cid, c in self.components.items()
            },
        }
