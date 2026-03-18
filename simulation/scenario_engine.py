"""
Scenario Engine — Sullair LS110

Three operating modes:
  normal   — healthy machine, realistic load variation
  stress   — one or more components degrading toward fault
  terminal — cascade in progress, shutdown imminent
  demo_*   — curated narratives for demonstrations

Each mode returns a configured MachineState ready to run.
"""

import random
from core.machine_state import MachineState
from core.components import build_component_registry


# ── Helpers ───────────────────────────────────────────────────────────────────

def _health_from_hours(component_id: str, hrs: float) -> float:
    from core.constants import DEGRADATION_RATES
    rate = DEGRADATION_RATES.get(component_id, 0.1)
    return max(0.0, 100.0 - (rate / 100.0) * hrs)


def _set_component(state: MachineState, cid: str, health_pct: float, op_hrs: float = None):
    """Directly set a component's health and operating hours."""
    c = state.components.get(cid)
    if c:
        c.health_pct = max(0.0, min(100.0, health_pct))
        if op_hrs is not None:
            c.operating_hours = op_hrs


# ── Base scenarios ────────────────────────────────────────────────────────────

def _normal_state() -> MachineState:
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
    if fault_type == "fluid_filter":
        _set_component(state, "fluid_filter", random.uniform(35, 50), 1900.0)
    elif fault_type == "inlet_filter":
        _set_component(state, "inlet_filter", random.uniform(30, 45), 1800.0)
    elif fault_type == "thermal_valve":
        _set_component(state, "thermal_valve", random.uniform(25, 45))
    elif fault_type == "separator":
        _set_component(state, "separator_element", random.uniform(30, 45), 7500.0)
    elif fault_type == "solenoid":
        _set_component(state, "solenoid_valve", random.uniform(25, 40))
    return state


def _terminal_state(fault_type: str = "fluid_filter") -> MachineState:
    state = _stress_state(fault_type)
    if fault_type == "fluid_filter":
        _set_component(state, "fluid_filter", random.uniform(10, 25))
        _set_component(state, "oil_cooler", random.uniform(40, 60))
        state.load_pct = random.uniform(85, 95)
        state.ambient_f = random.uniform(88, 100)
    elif fault_type == "separator":
        _set_component(state, "separator_element", random.uniform(5, 20), 8500.0)
        state.load_pct = random.uniform(85, 95)
    elif fault_type == "thermal_valve":
        _set_component(state, "thermal_valve", random.uniform(5, 15))
        state.fault_thermal_valve_stuck_open = True
    elif fault_type == "solenoid":
        _set_component(state, "solenoid_valve", random.uniform(5, 15))
        state.fault_solenoid_stuck_closed = True
    return state


# ── Demo narratives ───────────────────────────────────────────────────────────
#
# Each demo_* function builds a machine state that:
#   1. Has a clear backstory (hours, last service, conditions)
#   2. Produces active sensor anomalies visible on Monitor
#   3. Triggers specific correlation findings on FTA
#   4. Maps directly to one or two Diagnose symptoms
#   5. Has a natural "what happens next" arc via Advance Time
#
# DEMO MATRIX:
#   demo_overdue_service   → S08 filter msg   → CD_001 silent bypass  → filter chain FTA
#   demo_summer_thermal    → S01 running hot  → CORR_004 T1 deviation → thermal chain FTA
#   demo_silent_separator  → S03 oil in air   → CORR_003 T1-T2 gap    → separator FTA
#   demo_overcooling       → S07 running cold → CD_002 valve stuck    → thermal chain FTA
#   demo_cascade           → S01+S06 multi    → CORR_002+003+004      → multi-branch FTA


def _demo_overdue_service() -> MachineState:
    """
    NARRATIVE: "The Overdue Service"
    ─────────────────────────────────
    A plant running at peak summer demand. The fluid filter hit its 2000hr
    service interval 3 weeks ago but the service team was busy. The filter
    bypass valve has now opened — machine looks fine on T1, but unfiltered
    oil is reaching the air end bearings.

    What you see:
      - P4-P3 delta collapsed to <0.3 bar (bypass open)
      - T1 normal — this is the dangerous signature
      - FILTER MAINT REQD message has been active for 300hrs
      - CD_001 fires in correlator (silent bypass)

    Diagnose trigger: S08 (filter message) → engineer says delta is LOW
    → diagnosis: silent bypass, IMMEDIATE action
    → FTA: CD_001 node highlighted

    Advance time: 200hrs → bearing wear starts accumulating from abrasive particles
    """
    state = MachineState(
        load_pct=87.0,
        ambient_f=96.0,        # hot summer plant floor
        setpoint_psi=110.0,
        total_hours=9340.0,    # high-hours machine
    )
    state.components = build_component_registry(
        fluid_filter_hrs=2300.0,   # 300hrs OVERDUE
        separator_hrs=5800.0,
        inlet_filter_hrs=1100.0,
        total_machine_hrs=state.total_hours,
    )
    # Bypass is open — filter loaded but delta collapsed
    state.fault_filter_bypass_open = True
    _set_component(state, "fluid_filter", 8.0, 2300.0)
    # Minor bearing wear starting from unfiltered oil
    _set_component(state, "main_motor_bearing", 72.0)
    return state


def _demo_summer_thermal() -> MachineState:
    """
    NARRATIVE: "The Summer Thermal"
    ─────────────────────────────────
    Midsummer. Plant floor ambient has crept up to 42°C (108°F).
    The machine is running 90%+ load to meet production demand.
    T1 is 18°F above the thermodynamic model — thermal system struggling.
    Oil cooler fouling + thermal valve at 38% health = compound thermal stress.
    No alarm yet. But trajectory leads to shutdown in ~3 weeks at this rate.

    What you see:
      - CORR_004 fires: T1 above model
      - CH_THERMAL_VALVE fires: health at 38%
      - T1 elevated but below 195°F warning
      - Assessment: "HIGH: thermal valve approaching fault"

    Diagnose trigger: S01 (running hot) → engineer says cooler looks clean
    → diagnosis: thermal valve degraded, replace within 7 days
    → FTA: CORR_004 + THERMAL nodes

    Advance time: 500hrs → T1 breaks 195°F warning, CORR_004 escalates to CRITICAL
    """
    state = MachineState(
        load_pct=91.0,
        ambient_f=108.0,       # 42°C — peak summer
        setpoint_psi=110.0,
        total_hours=11200.0,
    )
    state.components = build_component_registry(
        fluid_filter_hrs=620.0,    # recently changed — not the issue
        separator_hrs=4200.0,
        inlet_filter_hrs=480.0,
        total_machine_hrs=state.total_hours,
    )
    _set_component(state, "thermal_valve", 38.0)
    _set_component(state, "oil_cooler", 52.0)
    return state


def _demo_silent_separator() -> MachineState:
    """
    NARRATIVE: "The Silent Separator"
    ─────────────────────────────────
    Machine has 11,500 total hours. Separator is 3,500hrs overdue for
    replacement (last changed at ~8000hrs combined, now at 11,500hrs).
    Oil carryover into the service line has been increasing for months.
    T1-T2 delta has collapsed to 6.8°F — separator near failure.
    Production team noticed oil smell at pneumatic tools last week.

    What you see:
      - CORR_003 fires: T1-T2 gap critically narrow
      - CORR_005 fires: P1 exceeding P2 (separator restriction)
      - CH_SEPARATOR fires at ACTION level
      - PSW1 slightly elevated from inlet partially loaded

    Diagnose trigger: S03 (oil in air) → engineer confirms smell not mist
    → diagnosis: separator degrading, plan replacement within 7 days
    → FTA: CORR_003 + CORR_005 nodes both lit

    Advance time: 500hrs → T1-T2 delta drops below 5°F, CORR_003 → CRITICAL
    """
    state = MachineState(
        load_pct=83.0,
        ambient_f=84.0,
        setpoint_psi=110.0,
        total_hours=11500.0,
    )
    state.components = build_component_registry(
        fluid_filter_hrs=800.0,
        separator_hrs=8400.0,      # 400hrs OVERDUE on separator
        inlet_filter_hrs=950.0,
        total_machine_hrs=state.total_hours,
    )
    # Push separator to near-critical manually
    _set_component(state, "separator_element", 18.0, 8400.0)
    _set_component(state, "thermal_valve", 55.0)
    return state


def _demo_overcooling() -> MachineState:
    """
    NARRATIVE: "The Overcooling Mystery"
    ─────────────────────────────────────
    Night shift noticed the machine "running cold" — T1 well below normal.
    No alarms firing. Controller shows all green. But the thermal valve
    element has failed open — routing all oil through the cooler regardless
    of temperature. In the UK winter conditions (ambient 8°C / 46°F),
    condensation is forming in the sump. The fluid is starting to foam.

    What you see:
      - CD_002 fires: T1 below model (overcooling)
      - CH_THERMAL_VALVE fires: health at 7%
      - T1 looks "low and safe" — counterintuitive dangerous state
      - Assessment: "CRITICAL: thermal valve — overcooling risk"

    Diagnose trigger: S07 (running cold) → engineer checks fluid = milky
    → diagnosis: immediate — thermal valve stuck open + fluid contaminated
    → FTA: CD_002 node highlighted

    Advance time: 200hrs → separator starts accumulating water damage
    """
    state = MachineState(
        load_pct=74.0,
        ambient_f=46.0,        # 8°C — winter conditions
        setpoint_psi=110.0,
        total_hours=7800.0,
    )
    state.components = build_component_registry(
        fluid_filter_hrs=340.0,
        separator_hrs=3200.0,
        inlet_filter_hrs=290.0,
        total_machine_hrs=state.total_hours,
    )
    # Thermal valve failed open
    state.fault_thermal_valve_stuck_open = True
    _set_component(state, "thermal_valve", 7.0)
    # Separator starting to suffer from fluid foaming
    _set_component(state, "separator_element", 61.0)
    return state


def _demo_cascade() -> MachineState:
    """
    NARRATIVE: "The 3am Cascade"
    ─────────────────────────────
    End-of-life machine. 14,000+ hours. Running continuously at 93% load
    to cover for another compressor that's down for maintenance. Multiple
    systems degrading simultaneously. This is the "everything is wrong"
    scenario — shows compound failure and multi-branch FTA activation.

    Active failures:
      - Fluid filter: 15% health — at fault threshold, bypass likely imminent
      - Separator: 22% health — T1-T2 gap at 7°F, oil carryover confirmed
      - Thermal valve: 24% health — T1 deviating above model
      - Oil cooler: 41% health — compounding thermal stress
      - Shaft seal: 35% health — oil consumption elevated

    What you see:
      - 5+ active findings in correlator
      - CORR_001, CORR_002, CORR_003, CORR_004, CORR_005 all lit
      - Assessment: multiple CRITICAL/ACTION items
      - FTA: 4+ nodes active across thermal AND separator branches

    Diagnose trigger: S01 (hot) + S03 (oil in air) + S06 (oil loss)
    → three separate diagnosis paths, all confirm cascade
    → FTA shows full compound failure picture

    Advance time: 100hrs → first SHUTDOWN fault triggers
    """
    state = MachineState(
        load_pct=93.0,
        ambient_f=101.0,       # hot, overworked plant
        setpoint_psi=110.0,
        total_hours=14200.0,
    )
    state.components = build_component_registry(
        fluid_filter_hrs=1950.0,   # near fault
        separator_hrs=8200.0,      # overdue
        inlet_filter_hrs=1600.0,
        total_machine_hrs=state.total_hours,
    )
    _set_component(state, "fluid_filter",     15.0, 1950.0)
    _set_component(state, "separator_element",22.0, 8200.0)
    _set_component(state, "thermal_valve",    24.0)
    _set_component(state, "oil_cooler",       41.0)
    _set_component(state, "shaft_seal",       35.0)
    _set_component(state, "main_motor_bearing",48.0)
    return state


# ── Demo metadata ─────────────────────────────────────────────────────────────
# Used by /scenarios/demo endpoint to return structured narrative context

DEMO_NARRATIVES = {
    "demo_overdue_service": {
        "title": "The Overdue Service",
        "subtitle": "Silent filter bypass — no alarm, full danger",
        "hours": "9,340 hrs · Filter 300hrs overdue",
        "conditions": "87% load · 36°C ambient · Peak demand",
        "active_findings": ["CD_001", "CORR_001"],
        "primary_symptom": "S08",
        "secondary_symptom": None,
        "key_sensor": "P4_P3_delta",
        "narrative": (
            "The fluid filter crossed its 2,000hr service interval 300 hours ago. "
            "The bypass valve has opened — oil flow is restored but unfiltered. "
            "T1 looks normal. No alarms. The air end bearings are receiving "
            "abrasive particles with every cycle."
        ),
        "demo_flow": [
            "Load demo → see P4–P3 delta collapsed on Monitor",
            "Intel tab → CD_001 CRITICAL finding active",
            "Go to Diagnose → S08 → answer 'delta is low' → IMMEDIATE action",
            "FTA → CD_001 node glowing red",
            "Advance 200hrs → bearing health starts dropping",
        ],
        "fta_highlight": "CD_001",
    },
    "demo_summer_thermal": {
        "title": "The Summer Thermal",
        "subtitle": "Pre-fault thermal detection — T1 above model before any alarm",
        "hours": "11,200 hrs · Filter recently changed",
        "conditions": "91% load · 42°C ambient · Summer peak",
        "active_findings": ["CORR_004", "CH_THERMAL_VALVE"],
        "primary_symptom": "S01",
        "secondary_symptom": None,
        "key_sensor": "T1",
        "narrative": (
            "Peak summer. Ambient has reached 42°C on the plant floor. "
            "The thermal valve element is at 38% health and struggling to "
            "regulate oil temperature. T1 is 18°F above the thermodynamic "
            "model. No alarm has fired yet — but trajectory leads to "
            "shutdown in 3 weeks at this rate."
        ),
        "demo_flow": [
            "Load demo → see T1 elevated on Monitor, T1 model Δ positive",
            "Intel tab → CORR_004 ACTION + CH_THERMAL_VALVE ACTION",
            "Go to Diagnose → S01 → 'cooler looks clean' → thermal valve diagnosis",
            "FTA → CORR_004 and THERMAL nodes both active",
            "Advance 500hrs → T1 breaks 195°F warning threshold",
        ],
        "fta_highlight": "CORR_004",
    },
    "demo_silent_separator": {
        "title": "The Silent Separator",
        "subtitle": "Oil carryover developing before separator dP alarm fires",
        "hours": "11,500 hrs · Separator 400hrs overdue",
        "conditions": "83% load · 29°C ambient",
        "active_findings": ["CORR_003", "CORR_005", "CH_SEPARATOR_ELEMENT"],
        "primary_symptom": "S03",
        "secondary_symptom": "S06",
        "key_sensor": "T1_T2_delta",
        "narrative": (
            "The separator element is 400 hours overdue. T1–T2 delta has "
            "collapsed to 6.8°C — separator efficiency critically low. "
            "Production noticed oil smell at pneumatic tools. No dP alarm "
            "yet, but oil carryover is confirmed and increasing."
        ),
        "demo_flow": [
            "Load demo → see T1-T2 Δ narrow on Monitor (T2delta chart)",
            "Intel tab → CORR_003 ACTION + CORR_005 ACTION",
            "Go to Diagnose → S03 → 'oil smell, no visible mist' → plan replacement",
            "FTA → CORR_003 + CORR_005 nodes both active",
            "Advance 500hrs → T1-T2 drops below 5°C → CRITICAL",
        ],
        "fta_highlight": "CORR_003",
    },
    "demo_overcooling": {
        "title": "The Overcooling Mystery",
        "subtitle": "Thermal valve stuck open — cold machine, no alarm, real danger",
        "hours": "7,800 hrs · Winter operation",
        "conditions": "74% load · 8°C ambient · Fault injected",
        "active_findings": ["CD_002", "CH_THERMAL_VALVE"],
        "primary_symptom": "S07",
        "secondary_symptom": None,
        "key_sensor": "T1",
        "narrative": (
            "Night shift reported the machine running unusually cold. "
            "The thermal valve element has failed open — all oil is routing "
            "through the cooler regardless of temperature. At 8°C ambient, "
            "condensation is forming in the sump. Fluid is beginning to foam. "
            "Controller shows all green. This is a counterintuitive danger."
        ),
        "demo_flow": [
            "Load demo → see T1 lower than expected on Monitor",
            "Intel tab → CD_002 WARNING + CH_THERMAL_VALVE CRITICAL",
            "Go to Diagnose → S07 → 'fluid looks milky' → IMMEDIATE action",
            "FTA → CD_002 node active",
            "Advance 200hrs → separator health drops from fluid foaming",
        ],
        "fta_highlight": "CD_002",
    },
    "demo_cascade": {
        "title": "The 3am Cascade",
        "subtitle": "Compound failure — five systems degrading simultaneously",
        "hours": "14,200 hrs · End of life",
        "conditions": "93% load · 38°C ambient · Covering downed unit",
        "active_findings": ["CORR_001", "CORR_002", "CORR_003", "CORR_004", "CORR_005",
                            "CH_THERMAL_VALVE", "CH_SEPARATOR_ELEMENT"],
        "primary_symptom": "S01",
        "secondary_symptom": "S03",
        "key_sensor": "T1",
        "narrative": (
            "End-of-life machine covering for a downed unit. Running 93% load "
            "continuously. Five components in the fault zone simultaneously. "
            "Filter near bypass, separator near rupture, thermal valve degraded, "
            "oil cooler fouled, shaft seal leaking. One hundred more hours and "
            "the first shutdown fault triggers."
        ),
        "demo_flow": [
            "Load demo → Monitor shows 5+ active findings",
            "Intel tab → multiple CRITICAL and ACTION findings",
            "Go to Diagnose → S01 (hot) then S03 (oil) → two diagnosis paths",
            "FTA → 4+ nodes active across both thermal and separator branches",
            "Advance 100hrs → first SHUTDOWN fault fires",
        ],
        "fta_highlight": "CORR_004",
    },
}


# ── Scenario factory ──────────────────────────────────────────────────────────

SCENARIO_BUILDERS = {
    "normal":             _normal_state,
    "stress_filter":      lambda: _stress_state("fluid_filter"),
    "stress_inlet":       lambda: _stress_state("inlet_filter"),
    "stress_thermal":     lambda: _stress_state("thermal_valve"),
    "stress_separator":   lambda: _stress_state("separator"),
    "stress_solenoid":    lambda: _stress_state("solenoid"),
    "terminal_filter":    lambda: _terminal_state("fluid_filter"),
    "terminal_separator": lambda: _terminal_state("separator"),
    "terminal_thermal":   lambda: _terminal_state("thermal_valve"),
    "terminal_solenoid":  lambda: _terminal_state("solenoid"),
    # Demo narratives
    "demo_overdue_service":  _demo_overdue_service,
    "demo_summer_thermal":   _demo_summer_thermal,
    "demo_silent_separator": _demo_silent_separator,
    "demo_overcooling":      _demo_overcooling,
    "demo_cascade":          _demo_cascade,
}


def build_scenario(name: str) -> MachineState:
    builder = SCENARIO_BUILDERS.get(name)
    if not builder:
        raise ValueError(
            f"Unknown scenario '{name}'. "
            f"Valid options: {list(SCENARIO_BUILDERS.keys())}"
        )
    return builder()


def list_scenarios() -> list:
    return list(SCENARIO_BUILDERS.keys())
