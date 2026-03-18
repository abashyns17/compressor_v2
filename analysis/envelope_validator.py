"""
Envelope Validator — Sullair LS110

Checks whether proposed operating parameters are physically possible
before any scenario is run. Prevents nonsense inputs producing
nonsense outputs.
"""

from core.constants import (
    PRESSURE_MAX_OPERATING_PSI, PRESSURE_RATED_PSI,
    AMBIENT_TEMP_MIN_F, AMBIENT_TEMP_MAX_F,
    MOTOR_MAX_AMBIENT_F,
)


def validate_pressure(psi: float) -> dict:
    if psi <= 0:
        return {"valid": False, "reason": "Pressure must be positive"}
    if psi > PRESSURE_MAX_OPERATING_PSI:
        return {
            "valid": False,
            "reason": f"Requested {psi:.0f}psi exceeds LS110 maximum operating pressure "
                      f"of {PRESSURE_MAX_OPERATING_PSI:.0f}psi"
        }
    if psi < 60:
        return {
            "valid": False,
            "reason": f"Requested {psi:.0f}psi is below minimum viable operating pressure (60psi)"
        }

    # Warn if not a standard rated pressure
    warnings = []
    if psi not in PRESSURE_RATED_PSI and psi > max(PRESSURE_RATED_PSI):
        warnings.append(f"Note: {psi:.0f}psi is not a standard LS110 rated pressure "
                        f"{PRESSURE_RATED_PSI}. Consult Sullair for non-standard setpoints.")

    return {"valid": True, "warnings": warnings}


def validate_ambient(ambient_f: float) -> dict:
    if ambient_f < AMBIENT_TEMP_MIN_F:
        return {
            "valid": False,
            "reason": f"Ambient {ambient_f:.0f}°F below minimum operating temperature "
                      f"of {AMBIENT_TEMP_MIN_F:.0f}°F — risk of fluid freezing"
        }
    if ambient_f > AMBIENT_TEMP_MAX_F:
        return {
            "valid": False,
            "reason": f"Ambient {ambient_f:.0f}°F exceeds LS110 maximum ambient "
                      f"of {AMBIENT_TEMP_MAX_F:.0f}°F — thermal shutdown risk"
        }
    if ambient_f > MOTOR_MAX_AMBIENT_F:
        return {
            "valid": True,
            "warnings": [
                f"Ambient {ambient_f:.0f}°F exceeds motor maximum ambient of "
                f"{MOTOR_MAX_AMBIENT_F:.0f}°F — motor derating required, "
                f"consult factory for high-ambient option"
            ]
        }
    return {"valid": True, "warnings": []}


def validate_load(load_pct: float) -> dict:
    if load_pct < 0 or load_pct > 100:
        return {"valid": False, "reason": f"Load must be 0-100%, got {load_pct:.0f}%"}
    warnings = []
    if load_pct > 95:
        warnings.append("Sustained >95% load significantly accelerates component degradation")
    return {"valid": True, "warnings": warnings}


def validate_scenario(
    setpoint_psi: float = None,
    ambient_f: float = None,
    load_pct: float = None,
) -> dict:
    """
    Validate a full set of proposed operating parameters.
    Returns combined validation result with all warnings.
    """
    errors = []
    warnings = []

    if setpoint_psi is not None:
        v = validate_pressure(setpoint_psi)
        if not v["valid"]:
            errors.append(v["reason"])
        else:
            warnings.extend(v.get("warnings", []))

    if ambient_f is not None:
        v = validate_ambient(ambient_f)
        if not v["valid"]:
            errors.append(v["reason"])
        else:
            warnings.extend(v.get("warnings", []))

    if load_pct is not None:
        v = validate_load(load_pct)
        if not v["valid"]:
            errors.append(v["reason"])
        else:
            warnings.extend(v.get("warnings", []))

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }
