"""
State routes — current sensor readings and machine summary.
"""

from fastapi import APIRouter, Query
from core.machine_state import MachineState

router = APIRouter(prefix="/state", tags=["state"])

_state: MachineState = None

def set_state(s: MachineState):
    global _state
    _state = s


# ── Unit conversion helpers ──────────────────────────────────────────────────

def _psi_to_bar(v: float) -> float:
    return round(v * 0.0689476, 3)

def _f_to_c(v: float) -> float:
    return round((v - 32) * 5 / 9, 1)

def _inwc_to_mbar(v: float) -> float:
    return round(v * 2.48840, 1)

def _fdelta_to_cdelta(v: float) -> float:
    """Delta conversion — multiply only, no offset subtraction."""
    return round(v * 5 / 9, 1)


def _convert_sensors(sensors: dict, units: str) -> dict:
    """
    Convert sensor dict from raw imperial to requested unit system.
    Field names are always neutral (no _f or _psi suffixes) so the
    frontend never needs to guess what unit a field is in.
    """
    if units == "imperial":
        return {
            "timestamp":    sensors["timestamp"],
            "P1":           round(sensors["P1"], 2),
            "P2":           round(sensors["P2"], 2),
            "P3":           round(sensors["P3"], 2),
            "P4":           round(sensors["P4"], 2),
            "T1":           round(sensors["T1"], 1),
            "T2":           round(sensors["T2"], 1),
            "PSW1":         round(sensors["PSW1"], 2),
            "load_pct":     round(sensors["load_pct"], 1),
            "ambient":      round(sensors["ambient_f"], 1),
            "P4_P3_delta":  round(sensors["P4_P3_delta"], 2),
            "T1_T2_delta":  round(sensors["T1_T2_delta"], 1),
        }

    return {
        "timestamp":    sensors["timestamp"],
        "P1":           _psi_to_bar(sensors["P1"]),
        "P2":           _psi_to_bar(sensors["P2"]),
        "P3":           _psi_to_bar(sensors["P3"]),
        "P4":           _psi_to_bar(sensors["P4"]),
        "T1":           _f_to_c(sensors["T1"]),
        "T2":           _f_to_c(sensors["T2"]),
        "PSW1":         _inwc_to_mbar(sensors["PSW1"]),
        "load_pct":     round(sensors["load_pct"], 1),
        "ambient":      _f_to_c(sensors["ambient_f"]),
        "P4_P3_delta":  _psi_to_bar(sensors["P4_P3_delta"]),
        "T1_T2_delta":  _fdelta_to_cdelta(sensors["T1_T2_delta"]),
    }


def _convert_operating_conditions(ops: dict, units: str) -> dict:
    """
    Convert operating conditions. Field names are unit-neutral:
      ambient   (not ambient_f)
      setpoint  (not setpoint_psi)
    """
    if units == "imperial":
        return {
            "load_pct":  ops["load_pct"],
            "ambient":   round(ops["ambient_f"], 1),
            "setpoint":  round(ops["setpoint_psi"], 1),
        }
    return {
        "load_pct":  ops["load_pct"],
        "ambient":   _f_to_c(ops["ambient_f"]),
        "setpoint":  _psi_to_bar(ops["setpoint_psi"]),
    }


def _convert_faults(faults: list, units: str) -> list:
    """Convert threshold/value fields in fault list."""
    if units == "imperial":
        return faults
    result = []
    for f in faults:
        fc = dict(f)
        code = f.get("code", "")
        if "TEMP" in code or "T1" in code:
            fc["value"]     = _f_to_c(f["value"])
            fc["threshold"] = _f_to_c(f["threshold"])
        elif "PRESS" in code or "FILTER_MAINT" in code:
            fc["value"]     = _psi_to_bar(f["value"])
            fc["threshold"] = _psi_to_bar(f["threshold"])
        elif "AIR_FILTER" in code:
            fc["value"]     = _inwc_to_mbar(f["value"])
            fc["threshold"] = _inwc_to_mbar(f["threshold"])
        result.append(fc)
    return result


def _unit_metadata(units: str) -> dict:
    """Unit label strings so the frontend never has to hardcode them."""
    if units == "imperial":
        return {
            "pressure":          "psi",
            "temperature":       "°F",
            "temperature_delta": "°F",
            "vacuum":            "in.wc",
            "ambient":           "°F",
            "setpoint":          "psi",
            "system":            "imperial",
        }
    return {
        "pressure":          "bar",
        "temperature":       "°C",
        "temperature_delta": "°C",
        "vacuum":            "mbar",
        "ambient":           "°C",
        "setpoint":          "bar",
        "system":            "metric",
    }


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/")
def get_full_state(
    units: str = Query(
        default="metric",
        enum=["metric", "imperial"],
        description="Unit system. metric = bar/°C/mbar. imperial = psi/°F/in.wc",
    )
):
    """
    Complete machine state.
    All field names are unit-neutral (no _f or _psi suffixes).
    Check response.units for the label to display next to each value.
    """
    raw = _state.summary()
    return {
        **raw,
        "sensors":              _convert_sensors(raw["sensors"], units),
        "operating_conditions": _convert_operating_conditions(raw["operating_conditions"], units),
        "active_faults":        _convert_faults(raw["active_faults"], units),
        "units":                _unit_metadata(units),
    }


@router.get("/sensors")
def get_sensors(
    units: str = Query(default="metric", enum=["metric", "imperial"])
):
    """Current sensor readings only."""
    reading = _state.compute_sensors()
    raw = reading.to_dict()
    return {**_convert_sensors(raw, units), "units": _unit_metadata(units)}


@router.get("/components")
def get_components():
    """Component health — no unit conversion needed."""
    return {
        cid: {
            "name": c.name,
            "health_pct": round(c.health_pct, 1),
            "operating_hours": round(c.operating_hours, 0),
            "hours_to_service": round(c.hours_to_service, 0) if c.hours_to_service else None,
            "overdue_hours": round(c.overdue_hours, 0),
            "is_fault_risk": c.is_fault_risk,
        }
        for cid, c in _state.components.items()
    }


@router.get("/faults")
def get_faults(
    units: str = Query(default="metric", enum=["metric", "imperial"])
):
    """Active fault conditions."""
    raw_faults = _state.get_active_faults()
    return {
        "faults": _convert_faults(raw_faults, units),
        "units":  _unit_metadata(units),
    }
