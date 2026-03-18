"""
Thermodynamic model for Sullair LS110 rotary screw compressor.

Calculates expected sensor values from operating conditions.
Deviation from expected = the signal the agent reasons against.

Confidence: SYNTHETIC — equations are physically grounded but
correction factors require validation against real machine data.
"""

import math
from core.constants import (
    GAMMA, ISENTROPIC_EFF, OIL_THERMAL_COEFF,
    THERMAL_VALVE_OPENS_F, OVERCOOLING_RISK_F,
    T1_DEVIATION_WARNING_F, T1_DEVIATION_ACTION_F,
)


def rankine(f: float) -> float:
    """Convert Fahrenheit to Rankine."""
    return f + 459.67


def fahrenheit(r: float) -> float:
    """Convert Rankine to Fahrenheit."""
    return r - 459.67


def expected_discharge_temp(
    ambient_f: float,
    p1_psi: float,
    p2_psi: float,
    isentropic_eff: float = ISENTROPIC_EFF,
) -> float:
    """
    Expected wet discharge temperature (T1) from compression ratio
    and inlet conditions.

    Based on isentropic compression with efficiency correction:
      T_out = T_in * (P_out/P_in)^((gamma-1)/gamma) / eta_isen

    Returns temperature in Fahrenheit.
    Confidence: SYNTHETIC
    """
    if p1_psi <= 0 or p2_psi <= 0:
        return ambient_f

    t_inlet_r = rankine(ambient_f)
    compression_ratio = (p2_psi + 14.696) / (p1_psi + 14.696)  # absolute pressures
    compression_ratio = max(1.0, compression_ratio)

    exponent = (GAMMA - 1.0) / GAMMA
    t_out_ideal_r = t_inlet_r * (compression_ratio ** exponent)
    t_out_actual_r = t_inlet_r + (t_out_ideal_r - t_inlet_r) / isentropic_eff

    return fahrenheit(t_out_actual_r)


def expected_t1_with_oil(
    ambient_f: float,
    p1_psi: float,
    p2_psi: float,
    oil_flow_factor: float = 1.0,
) -> float:
    """
    T1 adjusted for oil injection cooling effect.
    oil_flow_factor: 1.0 = full flow, 0.0 = no oil (catastrophic)

    Oil injection significantly reduces discharge temperature vs dry compression.
    Confidence: SYNTHETIC — OIL_THERMAL_COEFF needs real data calibration.
    """
    t_dry = expected_discharge_temp(ambient_f, p1_psi, p2_psi)
    t_inlet = ambient_f

    # Oil absorbs a fraction of the compression heat
    heat_absorbed_by_oil = (t_dry - t_inlet) * OIL_THERMAL_COEFF * oil_flow_factor
    t_wet = t_dry - heat_absorbed_by_oil

    return t_wet


def oil_flow_factor_from_filter_health(filter_health_pct: float,
                                        bypass_open: bool = False) -> float:
    """
    Derive oil flow factor from fluid filter health.
    - Healthy filter (100%): full flow = 1.0
    - Degraded filter (30%): restricted flow ~0.6
    - Bypass open: flow restored but unfiltered

    Confidence: DERIVED
    """
    if bypass_open:
        return 0.95  # near-full flow but unfiltered — sensor looks OK, risk is high

    if filter_health_pct >= 70:
        return 1.0
    elif filter_health_pct >= 30:
        # Linear restriction between onset and fault threshold
        return 0.6 + (filter_health_pct - 30) / (70 - 30) * 0.4
    else:
        # Severely restricted
        return max(0.2, filter_health_pct / 30 * 0.6)


def expected_p3(
    p1_psi: float,
    filter_health_pct: float,
    bypass_open: bool = False,
) -> float:
    """
    Expected injection fluid pressure (P3) given sump pressure and filter state.
    P3 = P1 minus filter restriction delta.

    Confidence: DERIVED
    """
    if bypass_open:
        # Bypass restores pressure — P3 close to P1, delta near zero
        return p1_psi * 0.98

    if filter_health_pct >= 70:
        # Clean filter: minimal delta
        delta = 2.0 + (100.0 - filter_health_pct) / 30.0 * 3.0
    elif filter_health_pct >= 30:
        # Degrading: delta grows toward fault threshold
        progress = (70.0 - filter_health_pct) / 40.0
        delta = 5.0 + progress * 15.0  # 5psi to 20psi
    else:
        # Severely restricted
        delta = 20.0 + (30.0 - filter_health_pct) * 0.5

    return max(0.0, p1_psi - delta)


def expected_p4(p1_psi: float) -> float:
    """
    P4 (upstream of filter) tracks sump pressure closely.
    Confidence: DERIVED
    """
    return p1_psi * 0.995


def expected_t2(t1_f: float, separator_health_pct: float) -> float:
    """
    Expected dry discharge temperature (T2) after separator.
    Healthy separator: T2 noticeably lower than T1 (oil carries heat).
    Failing separator: T2 approaches T1 as oil carryover increases.

    Confidence: DERIVED
    """
    if separator_health_pct >= 70:
        # Healthy: good oil separation, T2 meaningfully below T1
        delta = 15.0 + (separator_health_pct - 70) / 30.0 * 10.0
    elif separator_health_pct >= 30:
        # Degrading: gap closing
        progress = (70.0 - separator_health_pct) / 40.0
        delta = 15.0 * (1.0 - progress * 0.8)
    else:
        # Near failure: T2 approaching T1
        delta = max(1.0, separator_health_pct / 30.0 * 3.0)

    return t1_f - delta


def thermal_valve_correction(
    t1_f: float,
    ambient_f: float,
    thermal_valve_health: float,
    load_pct: float,
) -> float:
    """
    Apply thermal valve state correction to T1.
    Stuck open: overcooling at low load and low ambient.
    Stuck closed: overheating — oil bypasses cooler entirely.

    Returns corrected T1.
    Confidence: DERIVED
    """
    if thermal_valve_health >= 70:
        return t1_f  # normal operation

    # Determine stuck direction based on how valve fails
    # Below onset health we model partial stuck behaviour
    stuck_factor = (70.0 - thermal_valve_health) / 70.0  # 0 to 1

    if ambient_f < 75 and load_pct < 60:
        # Likely overcooling scenario
        overcooling_delta = stuck_factor * 20.0
        return max(ambient_f + 20, t1_f - overcooling_delta)
    else:
        # Likely overheating — valve not diverting to cooler
        overheat_delta = stuck_factor * 25.0
        return t1_f + overheat_delta


def get_load_multiplier(load_pct: float) -> float:
    """Degradation rate multiplier based on load percentage."""
    from core.constants import LOAD_MULTIPLIERS
    for (low, high), mult in LOAD_MULTIPLIERS.items():
        if low <= load_pct < high:
            return mult
    return 3.5  # above 100%


def get_ambient_multiplier(ambient_f: float) -> float:
    """Degradation rate multiplier based on ambient temperature."""
    from core.constants import AMBIENT_TEMP_MULTIPLIERS
    for (low, high), mult in AMBIENT_TEMP_MULTIPLIERS.items():
        if low <= ambient_f < high:
            return mult
    return 2.0


def t1_deviation(actual_t1: float, ambient_f: float,
                  p1_psi: float, p2_psi: float,
                  oil_flow_factor: float = 1.0) -> float:
    """
    Deviation of actual T1 from thermodynamic model prediction.
    Positive = running hotter than expected.
    Negative = running cooler than expected.
    """
    expected = expected_t1_with_oil(ambient_f, p1_psi, p2_psi, oil_flow_factor)
    return actual_t1 - expected
