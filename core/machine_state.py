"""
Machine State Engine — Sullair LS110

Maintains the live state of all sensors and components.
Advances time, applies thermodynamics, adds realistic noise.
This is the digital twin's beating heart.
"""

import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from core.constants import (
    PRESSURE_UNLOAD_PSI, PRESSURE_RELOAD_PSI, PRESSURE_MIN_SUMP_PSI,
    NORMAL_T1_RANGE_F, SENSOR_NOISE,
    DISCHARGE_TEMP_SHUTDOWN_F, DISCHARGE_TEMP_WARNING_F,
    FLUID_FILTER_DELTA_P_FAULT_PSI, SEPARATOR_DELTA_P_FAULT_PSI,
    INLET_FILTER_VACUUM_FAULT_WC,
)
from core.components import Component, build_component_registry
from core.thermodynamics import (
    expected_t1_with_oil, expected_p3, expected_p4, expected_t2,
    oil_flow_factor_from_filter_health, thermal_valve_correction,
    get_load_multiplier, get_ambient_multiplier,
)


@dataclass
class SensorReading:
    timestamp: datetime
    P1: float   # wet sump pressure (psi)
    P2: float   # line/discharge pressure (psi)
    P3: float   # injection fluid pressure (psi)
    P4: float   # filter upstream pressure (psi)
    T1: float   # wet discharge temperature (°F)
    T2: float   # dry discharge temperature (°F)
    PSW1: float # inlet filter vacuum (inches w.c.)
    load_pct: float
    ambient_f: float

    # Derived signals
    @property
    def P4_P3_delta(self) -> float:
        return self.P4 - self.P3

    @property
    def T1_T2_delta(self) -> float:
        return self.T1 - self.T2

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "P1": round(self.P1, 2),
            "P2": round(self.P2, 2),
            "P3": round(self.P3, 2),
            "P4": round(self.P4, 2),
            "T1": round(self.T1, 1),
            "T2": round(self.T2, 1),
            "PSW1": round(self.PSW1, 2),
            "load_pct": round(self.load_pct, 1),
            "ambient_f": round(self.ambient_f, 1),
            "P4_P3_delta": round(self.P4_P3_delta, 2),
            "T1_T2_delta": round(self.T1_T2_delta, 1),
        }


@dataclass
class MachineState:
    """
    Complete state of the LS110 at a point in time.
    """
    # Operating conditions
    load_pct: float = 75.0
    ambient_f: float = 77.0
    setpoint_psi: float = 110.0
    total_hours: float = 5000.0

    # Fault injection flags (override normal physics)
    fault_thermal_valve_stuck_open: bool = False
    fault_thermal_valve_stuck_closed: bool = False
    fault_solenoid_stuck_closed: bool = False
    fault_blowdown_stuck: bool = False
    fault_filter_bypass_open: bool = False

    # Components — initialised via factory
    components: dict = field(default_factory=dict)

    # Internal state
    _running: bool = True
    _simulation_speed: float = 1.0

    def __post_init__(self):
        if not self.components:
            self.components = build_component_registry(
                total_machine_hrs=self.total_hours
            )

    # ── Sensor computation ────────────────────────────────────────────────────

    def compute_sensors(self) -> SensorReading:
        components = self.components

        filter_health = components["fluid_filter"].health_pct
        bypass_open = self.fault_filter_bypass_open
        oil_flow = oil_flow_factor_from_filter_health(filter_health, bypass_open)

        p1_base = self.setpoint_psi + PRESSURE_MIN_SUMP_PSI * 0.1
        p2_base = self.setpoint_psi + random.uniform(-2.0, 4.0)
        if self.fault_solenoid_stuck_closed:
            p2_base = self.setpoint_psi + 15.0 + components["solenoid_valve"].health_pct * -0.1

        p4_base = expected_p4(p1_base)
        p3_base = expected_p3(p1_base, filter_health, bypass_open)

        t1_base = expected_t1_with_oil(self.ambient_f, p1_base, p2_base, oil_flow)

        tv_health = components["thermal_valve"].health_pct
        if self.fault_thermal_valve_stuck_open:
            tv_health = 0.0
        elif self.fault_thermal_valve_stuck_closed:
            tv_health = 5.0
            t1_base += 30.0

        t1_base = thermal_valve_correction(t1_base, self.ambient_f, tv_health, self.load_pct)

        cooler_health = components["oil_cooler"].health_pct
        if cooler_health < 70:
            cooler_penalty = (70.0 - cooler_health) / 70.0 * 20.0
            t1_base += cooler_penalty

        sep_health = components["separator_element"].health_pct
        t2_base = expected_t2(t1_base, sep_health)

        inlet_health = components["inlet_filter"].health_pct
        inlet_restriction = max(0, (100.0 - inlet_health) / 100.0)
        psw1_base = inlet_restriction * INLET_FILTER_VACUUM_FAULT_WC * \
                    (0.5 + self.load_pct / 100.0 * 0.5)

        load_factor = self.load_pct / 100.0
        p1_base += load_factor * 8.0
        t1_base += load_factor * 5.0

        def noise(sensor_id: float, base: float) -> float:
            n = SENSOR_NOISE.get(sensor_id, 0.3)
            return base + random.gauss(0, n)

        return SensorReading(
            timestamp=datetime.now(timezone.utc),
            P1=max(0, noise("P1", p1_base)),
            P2=max(0, noise("P2", p2_base)),
            P3=max(0, noise("P3", p3_base)),
            P4=max(0, noise("P4", p4_base)),
            T1=noise("T1", t1_base),
            T2=noise("T2", t2_base),
            PSW1=max(0, noise("PSW1", psw1_base)),
            load_pct=max(0, min(100, noise("load_pct", self.load_pct))),
            ambient_f=self.ambient_f,
        )

    # ── Time advancement ──────────────────────────────────────────────────────

    def advance(self, hours: float):
        load_mult = get_load_multiplier(self.load_pct)
        temp_mult = get_ambient_multiplier(self.ambient_f)
        for component in self.components.values():
            component.degrade(hours, load_mult, temp_mult)
        self.total_hours += hours

    # ── Status ────────────────────────────────────────────────────────────────

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
                }
                for cid, c in self.components.items()
            },
        }
