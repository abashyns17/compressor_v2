"""
Sullair LS110 Physical Constants and Specification Values
Source: User Manual 02250231-030 R11

Confidence levels:
  MANUAL   - directly stated in the manual
  DERIVED  - inferred from documented component relationships
  SYNTHETIC - physically plausible, requires validation against real machine
"""

# ─── Machine Identity ──────────────────────────────────────────────────────────
MACHINE_MODEL = "Sullair LS110"
MANUAL_REF    = "02250231-030 R11"

# ─── Motor Specs (MANUAL) ─────────────────────────────────────────────────────
MOTOR_HP         = 150
MOTOR_KW         = 110
MOTOR_RPM        = 1780
MOTOR_PHASES     = 3
MOTOR_MAX_AMBIENT_F = 104.0   # max ambient for motor operation

# ─── Compressor Operating Envelope (MANUAL) ───────────────────────────────────
PRESSURE_RATED_PSI      = [110, 125, 150, 175]  # standard operating pressures
PRESSURE_MIN_SUMP_PSI   = 50.0    # minimum pressure valve floor
PRESSURE_UNLOAD_PSI     = 120.0   # default unload setpoint (110psi rated machine)
PRESSURE_RELOAD_PSI     = 110.0   # default reload setpoint
PRESSURE_RELIEF_PSI     = 175.0   # relief valve approximate set point
PRESSURE_MAX_OPERATING_PSI = 175.0

AMBIENT_TEMP_MIN_F = 40.0
AMBIENT_TEMP_MAX_F = 115.0

FLUID_CAPACITY_GAL  = 9.0    # separator/sump tank capacity
FLUID_CAPACITY_HIGH_AMBIENT_GAL = 14.5  # full system fill

# ─── Thermal Thresholds (MANUAL) ──────────────────────────────────────────────
THERMAL_VALVE_OPENS_F           = 185.0  # thermal valve starts opening
THERMAL_VALVE_OPENS_HIGH_PSI_F  = 210.0  # for machines rated > 150psi
DISCHARGE_TEMP_SHUTDOWN_F       = 200.0  # T1 shutdown threshold (standard)
DISCHARGE_TEMP_SHUTDOWN_HIGH_F  = 215.0  # T1 shutdown for water-cooled or >150psi
DISCHARGE_TEMP_WARNING_F        = 195.0  # pre-shutdown warning (DERIVED)
DISCHARGE_TEMP_MIN_NORMAL_F     = 150.0  # below this = overcooling risk (DERIVED)
OVERCOOLING_RISK_F              = 170.0  # condensation risk below this (MANUAL ref section 7.16.2)

# ─── Filter Thresholds (MANUAL) ───────────────────────────────────────────────
FLUID_FILTER_DELTA_P_FAULT_PSI  = 20.0   # P4-P3 delta triggers FILTER MAINT REQD
SEPARATOR_DELTA_P_FAULT_PSI     = 10.0   # separator dP triggers SEPARATOR MAINT REQD
INLET_FILTER_VACUUM_FAULT_WC    = 22.0   # inches water column — PSW1 fault threshold
WATER_PRESSURE_MIN_PSI          = 10.0   # PSW2 fault threshold (water-cooled only)

# ─── Maintenance Intervals (MANUAL) ───────────────────────────────────────────
FLUID_FILTER_INTERVAL_HRS   = 2000
SEPARATOR_INTERVAL_HRS      = 8000
FLUID_CHANGE_INTERVAL_HRS   = 8000
AIR_FILTER_CHECK_HRS        = 2000
MOTOR_GREASE_INTERVAL_HRS   = 2000
INITIAL_SERVICE_HRS         = 50     # first service after break-in
INTERMEDIATE_SERVICE_HRS    = 2000

# ─── Normal Operating Ranges (DERIVED) ────────────────────────────────────────
# Expected sensor value ranges during healthy steady-state operation
# at standard 110psi rated pressure, 70-80°F ambient, 60-80% load
NORMAL_T1_RANGE_F       = (160.0, 195.0)
NORMAL_T2_RANGE_F       = (130.0, 180.0)
NORMAL_P1_RANGE_PSI     = (55.0, 120.0)   # wet sump — tracks rated pressure
NORMAL_P2_RANGE_PSI     = (100.0, 125.0)  # line pressure — between reload and unload
NORMAL_P3_RANGE_PSI     = (55.0, 115.0)   # injection fluid — slightly below P1
NORMAL_P4_P3_DELTA_PSI  = (2.0, 18.0)     # filter differential — clean to near-fault
NORMAL_T1_T2_DELTA_F    = (5.0, 25.0)     # separator efficiency indicator

# ─── Thermodynamic Constants (SYNTHETIC — validate against real machine) ──────
GAMMA               = 1.4      # heat capacity ratio for air
ISENTROPIC_EFF      = 0.72     # rotary screw isentropic efficiency (typical range 0.68-0.78)
MECHANICAL_EFF      = 0.95     # mechanical transmission efficiency
OIL_THERMAL_COEFF   = 0.85     # oil heat absorption coefficient (SYNTHETIC)

# T1 expected formula:
#   T1_expected = T_inlet_R * (P2/P1)^((GAMMA-1)/GAMMA) / ISENTROPIC_EFF
#   where temperatures in Rankine (F + 459.67)
# Deviation thresholds from expected T1:
T1_DEVIATION_WATCH_F    = 5.0   # start monitoring
T1_DEVIATION_WARNING_F  = 8.0   # surface to engineer
T1_DEVIATION_ACTION_F   = 12.0  # immediate investigation

# ─── Component Degradation Rates (SYNTHETIC) ──────────────────────────────────
# Health degrades from 100% (new) to 0% (failed)
# Rates are per 100 operating hours under normal conditions
# Accelerate under stress per load/temp multipliers below

DEGRADATION_RATES = {
    "fluid_filter":       1.20,   # % per 100hrs — reaches ~75% health at 2000hr interval
    "separator_element":  0.35,   # % per 100hrs — reaches ~72% at 8000hr interval
    "inlet_filter":       0.80,   # % per 100hrs — condition-dependent
    "thermal_valve":      0.15,   # % per 100hrs — long-life component
    "oil_cooler":         0.10,   # % per 100hrs — fouling accumulates slowly
    "shaft_seal":         0.25,   # % per 100hrs — pressure and temp dependent
    "coupling_element":   0.20,   # % per 100hrs — alignment-dependent
    "main_motor_bearing": 0.08,   # % per 100hrs — greased per schedule
    "solenoid_valve":     0.12,   # % per 100hrs — cycling dependent
    "blowdown_valve":     0.18,   # % per 100hrs — cycling dependent
}

# Health threshold at which component behaviour starts degrading sensor readings
DEGRADATION_ONSET_PCT   = 70.0  # component starts affecting sensors below this
DEGRADATION_FAULT_PCT   = 30.0  # component likely to cause fault event below this
DEGRADATION_FAILURE_PCT = 10.0  # imminent failure

# ─── Load and Thermal Multipliers (SYNTHETIC) ─────────────────────────────────
# Degradation rate multipliers based on operating conditions
LOAD_MULTIPLIERS = {
    (0,   60):  1.0,
    (60,  80):  1.3,
    (80,  90):  2.0,
    (90,  100): 3.5,
}

AMBIENT_TEMP_MULTIPLIERS = {
    (40,  75):  1.0,
    (75,  90):  1.3,
    (90,  100): 1.5,
    (100, 115): 2.0,
}

# ─── Sensor Noise (SYNTHETIC) ─────────────────────────────────────────────────
# Realistic measurement noise for emulated sensor streams
SENSOR_NOISE = {
    "P1": 0.3,   # psi
    "P2": 0.3,
    "P3": 0.3,
    "P4": 0.3,
    "T1": 0.5,   # fahrenheit
    "T2": 0.5,
    "PSW1": 0.1, # inches water column
    "load_pct": 0.5,
}
