"""
Cross-Correlation Analyser — Sullair LS110

Implements the sensor correlation schema against live or logged sensor data.
Detects patterns that single-sensor monitoring misses.

Confidence levels inherited from correlation schema:
  MANUAL   — documented in 02250231-030 R11
  DERIVED  — inferred from component relationships
  SYNTHETIC — requires real machine validation
"""

from core.machine_state import MachineState, SensorReading
from core.constants import (
    FLUID_FILTER_DELTA_P_FAULT_PSI, SEPARATOR_DELTA_P_FAULT_PSI,
    OVERCOOLING_RISK_F, DISCHARGE_TEMP_WARNING_F,
    INLET_FILTER_VACUUM_FAULT_WC,
    T1_DEVIATION_WARNING_F, T1_DEVIATION_ACTION_F,
)
from core.thermodynamics import (
    t1_deviation, oil_flow_factor_from_filter_health,
)


class CorrelationFinding:
    def __init__(self, correlation_id: str, pattern: str,
                 interpretation: str, confidence: str,
                 severity: str, sensors_involved: list,
                 action: str = "", chain_ref: str = ""):
        self.correlation_id = correlation_id
        self.pattern = pattern
        self.interpretation = interpretation
        self.confidence = confidence
        self.severity = severity        # INFO / WARNING / ACTION / CRITICAL
        self.sensors_involved = sensors_involved
        self.action = action
        self.chain_ref = chain_ref

    def to_dict(self) -> dict:
        return {
            "correlation_id": self.correlation_id,
            "pattern": self.pattern,
            "interpretation": self.interpretation,
            "confidence": self.confidence,
            "severity": self.severity,
            "sensors_involved": self.sensors_involved,
            "action": self.action,
            "chain_ref": self.chain_ref,
        }


def analyse(state: MachineState) -> list:
    """
    Run all correlation checks against current machine state.
    Returns list of CorrelationFinding objects sorted by severity.
    """
    reading = state.compute_sensors()
    findings = []

    findings.extend(_check_corr_001(reading, state))
    findings.extend(_check_corr_002(reading, state))
    findings.extend(_check_corr_003(reading, state))
    findings.extend(_check_corr_004(reading, state))
    findings.extend(_check_corr_005(reading, state))
    findings.extend(_check_corr_006(reading, state))
    findings.extend(_check_composite_cd001(reading, state))
    findings.extend(_check_composite_cd002(reading, state))
    findings.extend(_check_composite_cd003(reading, state))

    # Sort: CRITICAL > ACTION > WARNING > INFO
    severity_order = {"CRITICAL": 0, "ACTION": 1, "WARNING": 2, "INFO": 3}
    findings.sort(key=lambda f: severity_order.get(f.severity, 99))

    return findings


# ── Individual correlation checks ─────────────────────────────────────────────

def _check_corr_001(reading: SensorReading, state: MachineState) -> list:
    """CORR_001 — P4-P3 delta: fluid filter differential. MANUAL."""
    findings = []
    delta = reading.P4_P3_delta

    if delta >= FLUID_FILTER_DELTA_P_FAULT_PSI:
        findings.append(CorrelationFinding(
            "CORR_001",
            "P4_P3_delta_at_fault_threshold",
            f"Fluid filter differential pressure at {delta:.1f}psi — FILTER MAINT REQD threshold reached",
            "MANUAL",
            "ACTION",
            ["P3", "P4"],
            "Replace fluid filter element (P/N 02250139-995)",
            "FC_002",
        ))
    elif delta >= FLUID_FILTER_DELTA_P_FAULT_PSI * 0.75:
        findings.append(CorrelationFinding(
            "CORR_001",
            "P4_P3_delta_approaching_fault",
            f"Filter differential at {delta:.1f}psi — approaching 20psi fault threshold",
            "MANUAL",
            "WARNING",
            ["P3", "P4"],
            "Plan filter replacement within next 200 operating hours",
            "FC_002",
        ))

    return findings


def _check_corr_002(reading: SensorReading, state: MachineState) -> list:
    """CORR_002 — P3 drop vs T1 behaviour. DERIVED."""
    findings = []
    filter_health = state.components["fluid_filter"].health_pct

    p3_low = reading.P3 < (reading.P1 * 0.85)
    t1_normal = reading.T1 < DISCHARGE_TEMP_WARNING_F

    if p3_low and t1_normal and filter_health < 40:
        findings.append(CorrelationFinding(
            "CORR_002",
            "P3_dropping_T1_flat",
            "P3 dropping while T1 remains normal — possible filter bypass valve open. "
            "Oil flow restored via bypass but unfiltered. Machine appears healthy on T1 alone.",
            "DERIVED",
            "CRITICAL",
            ["P3", "T1"],
            "Inspect fluid filter immediately — bypass valve may have opened. "
            "Unfiltered oil reaching air end bearings.",
            "CD_001",
        ))
    elif p3_low and not t1_normal:
        findings.append(CorrelationFinding(
            "CORR_002",
            "P3_dropping_T1_rising",
            "P3 dropping and T1 rising in proportion — fluid filter clogging, "
            "reduced oil flow causing thermal rise",
            "DERIVED",
            "ACTION",
            ["P3", "T1"],
            "Replace fluid filter element. Check P4-P3 delta.",
            "FC_002",
        ))

    return findings


def _check_corr_003(reading: SensorReading, state: MachineState) -> list:
    """CORR_003 — T1 vs T2 delta: separator efficiency. DERIVED."""
    findings = []
    delta = reading.T1_T2_delta

    if delta < 0:
        findings.append(CorrelationFinding(
            "CORR_003",
            "T2_exceeds_T1",
            f"T2 ({reading.T2:.0f}°F) exceeds T1 ({reading.T1:.0f}°F) — "
            "separator element ruptured, hot oil bypassing directly to discharge",
            "DERIVED",
            "CRITICAL",
            ["T1", "T2"],
            "Immediate shutdown recommended. Replace separator element.",
            "FC_004",
        ))
    elif delta < 5:
        findings.append(CorrelationFinding(
            "CORR_003",
            "T1_T2_gap_critically_narrow",
            f"T1-T2 gap only {delta:.1f}°F — separator near failure, "
            "oil carryover into service line",
            "DERIVED",
            "ACTION",
            ["T1", "T2"],
            "Plan separator replacement within 100 hours. "
            "Check downstream equipment for oil contamination.",
            "FC_004",
        ))
    elif delta < 10:
        findings.append(CorrelationFinding(
            "CORR_003",
            "T1_T2_gap_narrowing",
            f"T1-T2 gap narrowing at {delta:.1f}°F — separator efficiency reducing",
            "DERIVED",
            "WARNING",
            ["T1", "T2"],
            "Monitor separator dP. Plan replacement at next service window.",
            "FC_004",
        ))

    return findings


def _check_corr_004(reading: SensorReading, state: MachineState) -> list:
    """CORR_004 — T1 vs thermodynamic model: baseline deviation. SYNTHETIC."""
    findings = []

    filter_health = state.components["fluid_filter"].health_pct
    oil_flow = oil_flow_factor_from_filter_health(filter_health)
    deviation = t1_deviation(reading.T1, reading.ambient_f,
                              reading.P1, reading.P2, oil_flow)

    if deviation >= T1_DEVIATION_ACTION_F:
        findings.append(CorrelationFinding(
            "CORR_004",
            "T1_above_model_action",
            f"T1 running {deviation:.1f}°F above thermodynamic model prediction "
            f"at current load ({reading.load_pct:.0f}%) and ambient ({reading.ambient_f:.0f}°F). "
            "Thermal system degradation detected before threshold breach.",
            "SYNTHETIC",
            "ACTION",
            ["T1", "ambient"],
            "Check oil cooler fouling, thermal valve operation, and cooling airflow.",
            "CORR_004",
        ))
    elif deviation >= T1_DEVIATION_WARNING_F:
        findings.append(CorrelationFinding(
            "CORR_004",
            "T1_above_model_warning",
            f"T1 running {deviation:.1f}°F above model — early thermal degradation signal",
            "SYNTHETIC",
            "WARNING",
            ["T1", "ambient"],
            "Monitor trend. If deviation continues growing, investigate thermal system.",
            "CORR_004",
        ))
    elif deviation <= -T1_DEVIATION_ACTION_F:
        findings.append(CorrelationFinding(
            "CORR_004",
            "T1_below_model_overcooling",
            f"T1 running {abs(deviation):.1f}°F BELOW model — possible thermal valve stuck open. "
            f"Overcooling risk at current ambient ({reading.ambient_f:.0f}°F).",
            "DERIVED",
            "WARNING",
            ["T1", "ambient"],
            "Check thermal valve element. Risk of fluid condensation and foaming.",
            "FC_003",
        ))

    return findings


def _check_corr_005(reading: SensorReading, state: MachineState) -> list:
    """CORR_005 — P1 vs P2 relationship. DERIVED."""
    findings = []

    p1_p2_ratio = reading.P1 / reading.P2 if reading.P2 > 0 else 1.0

    # P1 should be lower than P2 in normal operation
    if reading.P1 > reading.P2 + 5:
        findings.append(CorrelationFinding(
            "CORR_005",
            "P1_exceeds_P2",
            f"Sump pressure P1 ({reading.P1:.0f}psi) exceeds line pressure P2 ({reading.P2:.0f}psi) "
            "— separator restriction building",
            "DERIVED",
            "ACTION",
            ["P1", "P2"],
            "Check separator differential pressure. Plan separator element replacement.",
            "FC_004",
        ))

    return findings


def _check_corr_006(reading: SensorReading, state: MachineState) -> list:
    """CORR_006 — PSW1 vs P3 independent restriction differentiation. DERIVED."""
    findings = []

    psw1_high = reading.PSW1 > INLET_FILTER_VACUUM_FAULT_WC * 0.7
    p3_low = reading.P3 < (reading.P1 * 0.88)

    if psw1_high and p3_low:
        findings.append(CorrelationFinding(
            "CORR_006",
            "PSW1_rising_P3_dropping_simultaneously",
            "Both inlet restriction (PSW1) and fluid restriction (P3) elevated simultaneously. "
            "High particulate environment likely affecting both air and fluid systems. "
            "Filter changes alone will not resolve — installation environment investigation needed.",
            "DERIVED",
            "ACTION",
            ["PSW1", "P3"],
            "Investigate installation environment for dust, particulate, or contamination source. "
            "Check air intake location and enclosure sealing.",
            "CORR_006",
        ))

    return findings


# ── Composite diagnostics ─────────────────────────────────────────────────────

def _check_composite_cd001(reading: SensorReading, state: MachineState) -> list:
    """CD_001 — Silent filter bypass detection. DERIVED."""
    findings = []

    filter_hrs = state.components["fluid_filter"].operating_hours
    delta = reading.P4_P3_delta
    t1_ok = reading.T1 < DISCHARGE_TEMP_WARNING_F

    # Low delta despite high operating hours and T1 looks fine
    if (filter_hrs > 1500
            and delta < 3.0
            and t1_ok):
        findings.append(CorrelationFinding(
            "CD_001",
            "silent_filter_bypass_suspected",
            f"Filter at {filter_hrs:.0f} operating hours but P4-P3 delta only {delta:.1f}psi "
            f"and T1 normal. Classic silent bypass signature — filter bypass valve likely open. "
            f"No alarms will fire but unfiltered oil is reaching the air end.",
            "DERIVED",
            "CRITICAL",
            ["P3", "P4", "T1"],
            "Immediate filter inspection required. If filter is visibly loaded but delta is low, "
            "bypass valve has opened. Replace filter AND inspect bypass valve.",
            "CD_001",
        ))

    return findings


def _check_composite_cd002(reading: SensorReading, state: MachineState) -> list:
    """CD_002 — Thermal valve stuck open early detection. DERIVED."""
    findings = []

    oil_flow = oil_flow_factor_from_filter_health(
        state.components["fluid_filter"].health_pct)
    deviation = t1_deviation(reading.T1, reading.ambient_f,
                              reading.P1, reading.P2, oil_flow)

    overcooling = (deviation <= -T1_DEVIATION_ACTION_F
                   and reading.load_pct > 50
                   and reading.ambient_f > 55)

    if overcooling:
        findings.append(CorrelationFinding(
            "CD_002",
            "thermal_valve_stuck_open_suspected",
            f"T1 running {abs(deviation):.1f}°F below model at {reading.load_pct:.0f}% load "
            f"and {reading.ambient_f:.0f}°F ambient. Overcooling at these conditions indicates "
            f"thermal valve stuck open — routing all oil through cooler regardless of temperature.",
            "DERIVED",
            "WARNING",
            ["T1", "ambient", "load_pct"],
            "Replace thermal valve element. Inspect fluid for water contamination and foaming.",
            "CD_002",
        ))

    return findings


def _check_composite_cd003(reading: SensorReading, state: MachineState) -> list:
    """CD_003 — Developing separator failure pre-alarm. DERIVED."""
    findings = []

    t1_t2_narrow = reading.T1_T2_delta < 8
    p1_elevated = reading.P1 > (state.setpoint_psi * 1.05)

    if t1_t2_narrow and p1_elevated:
        findings.append(CorrelationFinding(
            "CD_003",
            "separator_failure_developing_pre_alarm",
            f"T1-T2 gap at {reading.T1_T2_delta:.1f}°F AND P1 elevated at {reading.P1:.0f}psi "
            f"— separator restriction developing. dP alarm not yet triggered but trajectory "
            f"indicates breach within projection window.",
            "DERIVED",
            "WARNING",
            ["T1", "T2", "P1"],
            "Plan separator replacement within 500 operating hours. "
            "Do not wait for dP alarm — pre-alarm intervention is significantly cheaper.",
            "CD_003",
        ))

    return findings


def analyse_to_dict(state: MachineState) -> list:
    """Return correlation findings as list of dicts for API response."""
    findings = analyse(state)
    return [f.to_dict() for f in findings]
