# Sullair LS110 — ProActive Agents Backend

Physics-informed digital twin simulation for the Sullair LS110 rotary screw air compressor.

## Setup

```bash
cd C:\GIT_Repos\compressor
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000
```

Swagger UI: http://localhost:8000/docs

---

## What this does

Emulates the complete sensor and component state of a Sullair LS110 compressor.
Built from the operator manual (02250231-030 R11) with physics-derived relationships.

### Key capabilities

**Sensor emulation** — P1/P2/P3/P4 pressure, T1/T2 temperature, PSW1 inlet vacuum.
All values derived from thermodynamic relationships, not lookup tables.

**Scenario engine** — load named operating scenarios:
- `normal` — healthy machine, typical load
- `stress_filter` — fluid filter degrading
- `stress_thermal` — thermal valve failing
- `stress_separator` — separator restriction building
- `terminal_*` — cascade in progress

**Fault injection** — directly inject specific faults:
- `thermal_valve_stuck_open` / `stuck_closed`
- `filter_bypass_open` — silent fault, no alarm fires
- `solenoid_failure` — unload failure

**Forward projection** — what-if engine:
```
POST /predict/project
{"days": 30, "setpoint_psi": 125}              # what if we increase pressure?
{"days": 60, "defer_services": {"fluid_filter": 60}}  # defer filter 60 days?
{"days": 730, "defer_services": {"shaft_seal": 730}}  # defer shaft seal 2 years?
```

**Cross-correlation analysis** — detects patterns single-sensor monitoring misses:
- Silent filter bypass (no alarm fires but unfiltered oil reaching bearings)
- Thermal valve overcooling before condensation damage occurs
- Separator failure pre-alarm signature

**Plain language assessment**:
```
GET /analysis/assessment
→ "CRITICAL: fluid filter reaches fault threshold in ~8 days at current load.
   HIGH: separator element projected fault in ~22 days. Schedule within next service window."
```

---

## Confidence levels

All relationships tagged:
- `MANUAL` — directly stated in Sullair manual 02250231-030 R11
- `DERIVED` — inferred from documented component relationships
- `SYNTHETIC` — physically plausible, requires validation against real machine data

---

## API Quick Reference

| Endpoint | Method | Description |
|---|---|---|
| `/state/` | GET | Full machine state |
| `/state/sensors` | GET | Sensor readings only |
| `/scenarios/load` | POST | Load named scenario |
| `/scenarios/advance` | POST | Advance time N hours |
| `/scenarios/conditions` | POST | Change load/ambient/pressure |
| `/inject/fault` | POST | Inject named fault |
| `/inject/component/health` | POST | Set component health % |
| `/inject/component/service/{id}` | POST | Reset component to 100% |
| `/analysis/correlations` | GET | Cross-correlation findings |
| `/analysis/assessment` | GET | Plain language risk summary |
| `/analysis/risk` | GET | Component risk table |
| `/predict/project` | POST | Forward projection |
| `/predict/compare` | POST | Compare multiple scenarios |
| `/predict/component/{id}` | GET | Single component projection |
| `/logs/readings` | GET | Sensor history for graphs |
| `/logs/trend/{sensor}` | GET | Single sensor time series |
