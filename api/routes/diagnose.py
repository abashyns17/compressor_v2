"""
Diagnose routes — symptom-driven diagnostic engine.

Fully deterministic — no LLM. Engineer picks a symptom, backend
cross-references live sensor data against the correlation schema,
returns the most likely hypothesis and one confirmatory question.
Engineer answers → diagnosis locked.

This is intentionally 'rigged' — the symptom→hypothesis mapping is
a curated lookup, not inference. That's fine for a demo and
actually correct: the correlation schema IS the domain knowledge.
"""

from fastapi import APIRouter
import api.routes.state as state_module

router = APIRouter(prefix="/diagnose", tags=["diagnose"])


# ── Symptom catalogue ─────────────────────────────────────────────────────────

SYMPTOMS = [
    {"id":"S01","label":"Machine running hotter than usual","description":"Discharge temperature feels elevated. Controller may show temperature warning.","category":"thermal"},
    {"id":"S02","label":"Output pressure lower than expected","description":"Line pressure not reaching setpoint, or dropping under load.","category":"pressure"},
    {"id":"S03","label":"Oil in the discharge air","description":"Oil mist, smell, or visible oil contamination at the service outlet.","category":"separator"},
    {"id":"S04","label":"Machine won't unload / runs continuously loaded","description":"Pressure keeps climbing, machine never enters unloaded state.","category":"pressure"},
    {"id":"S05","label":"Unusual noise or vibration","description":"Knocking, grinding, or vibration not present before.","category":"mechanical"},
    {"id":"S06","label":"Oil level dropping faster than expected","description":"Needing to top up oil more frequently than the service schedule.","category":"oil"},
    {"id":"S07","label":"Machine running colder than usual","description":"Discharge temperature noticeably lower than normal operating range.","category":"thermal"},
    {"id":"S08","label":"FILTER MAINT REQD message on controller","description":"Controller is displaying a filter maintenance required message.","category":"filter"},
]


def get_symptoms():
    return SYMPTOMS


# ── Hypothesis engine ─────────────────────────────────────────────────────────

def build_hypotheses(symptom_id: str, state) -> dict:
    if state is None:
        return {"error": "No scenario loaded — load a scenario first"}

    reading = state.compute_sensors()
    s = {
        "P1": reading.P1, "P2": reading.P2, "P3": reading.P3, "P4": reading.P4,
        "T1": reading.T1, "T2": reading.T2, "PSW1": reading.PSW1,
        "load_pct": reading.load_pct, "ambient_f": reading.ambient_f,
        "P4_P3_delta": reading.P4_P3_delta, "T1_T2_delta": reading.T1_T2_delta,
    }
    comp_health = {cid: c.health_pct for cid, c in state.components.items()}

    handlers = {
        "S01": _s01_running_hot, "S02": _s02_low_pressure,
        "S03": _s03_oil_in_air,  "S04": _s04_wont_unload,
        "S05": _s05_noise_vibration, "S06": _s06_oil_loss,
        "S07": _s07_running_cold, "S08": _s08_filter_message,
    }
    handler = handlers.get(symptom_id)
    if not handler:
        return {"error": f"Unknown symptom: {symptom_id}"}
    return handler(s, comp_health, state)


def _evidence(s, comp_health):
    return {
        "T1_c": round((s["T1"] - 32) * 5/9, 1),
        "T2_c": round((s["T2"] - 32) * 5/9, 1),
        "T1_T2_delta_c": round(s["T1_T2_delta"] * 5/9, 1),
        "P1_bar": round(s["P1"] * 0.0689476, 2),
        "P2_bar": round(s["P2"] * 0.0689476, 2),
        "P3_bar": round(s["P3"] * 0.0689476, 2),
        "P4_bar": round(s["P4"] * 0.0689476, 2),
        "P4_P3_delta_bar": round(s["P4_P3_delta"] * 0.0689476, 2),
        "PSW1_mbar": round(s["PSW1"] * 2.4884, 1),
        "load_pct": round(s["load_pct"], 0),
        "thermal_valve_health": round(comp_health.get("thermal_valve", 100), 0),
        "fluid_filter_health": round(comp_health.get("fluid_filter", 100), 0),
        "separator_health": round(comp_health.get("separator_element", 100), 0),
        "oil_cooler_health": round(comp_health.get("oil_cooler", 100), 0),
    }


def _s01_running_hot(s, comp_health, state):
    ev = _evidence(s, comp_health)
    p3_dropping = s["P3"] < s["P1"] * 0.90
    t1_high = s["T1"] > 160
    filter_degraded = comp_health.get("fluid_filter", 100) < 60
    cooler_degraded = comp_health.get("oil_cooler", 100) < 60
    tv_degraded = comp_health.get("thermal_valve", 100) < 50

    if filter_degraded and p3_dropping:
        primary = "CORR_002"
        primary_label = "Fluid filter clogging — reduced oil flow reducing cooling capacity"
        question = "Is the P4–P3 pressure delta higher than normal on the controller display?"
        answers = [
            {"label":"Yes, delta is elevated","diagnosis_id":"CORR_001","diagnosis":"Fluid filter requires replacement. Reduced oil flow causing thermal rise.","action":"Replace fluid filter element (P/N 02250139-995). Check P4–P3 delta after replacement.","urgency":"within_7_days"},
            {"label":"No, delta looks normal","diagnosis_id":"CD_001","diagnosis":"Possible silent filter bypass — filter bypass valve may have opened.","action":"Inspect filter physically. If filter is loaded but delta is low, bypass valve has opened — replace filter AND inspect bypass valve.","urgency":"immediate"},
        ]
    elif cooler_degraded or (t1_high and not p3_dropping):
        primary = "CORR_004"
        primary_label = "Thermal system degradation — oil cooler fouling or thermal valve restriction"
        question = "Is the area around the oil cooler free of dust and obstructions?"
        answers = [
            {"label":"No, cooler looks dirty or blocked","diagnosis_id":"CORR_004a","diagnosis":"Oil cooler fouling restricting heat dissipation.","action":"Clean oil cooler fins. Ensure cooling airflow is unrestricted.","urgency":"within_14_days"},
            {"label":"Yes, cooler looks clean","diagnosis_id":"CORR_004b","diagnosis":"Thermal valve element degraded — not diverting oil through cooler correctly.","action":f"Replace thermal valve element (P/N 02250100-374). Current health: {ev['thermal_valve_health']}%.","urgency":"within_7_days"},
        ]
    elif tv_degraded:
        primary = "CH_THERMAL_VALVE"
        primary_label = "Thermal valve element worn — not regulating oil temperature correctly"
        question = "Has the machine been running at high load (>80%) for extended periods?"
        answers = [
            {"label":"Yes, high sustained load","diagnosis_id":"CH_THERMAL_VALVE_LOAD","diagnosis":"Thermal valve under load stress, degraded element failing.","action":f"Replace thermal valve element immediately. Current health: {ev['thermal_valve_health']}%.","urgency":"immediate"},
            {"label":"No, load has been normal","diagnosis_id":"CH_THERMAL_VALVE_AGE","diagnosis":"Thermal valve element aged and worn.","action":f"Replace thermal valve element (P/N 02250100-374). Current health: {ev['thermal_valve_health']}%.","urgency":"within_7_days"},
        ]
    else:
        primary = "CORR_004"
        primary_label = "T1 above expected for current load and ambient conditions"
        question = "Has ambient temperature in the compressor room increased recently?"
        answers = [
            {"label":"Yes, room is hotter than usual","diagnosis_id":"AMBIENT","diagnosis":"Elevated ambient temperature reducing cooling margin.","action":"Improve ventilation in compressor room. Max inlet temp 46°C / 115°F.","urgency":"monitor"},
            {"label":"No, ambient is normal","diagnosis_id":"CORR_004c","diagnosis":"Thermal system degradation not explained by ambient.","action":"Inspect oil cooler for fouling. Check thermal valve operation.","urgency":"within_14_days"},
        ]

    return {"symptom_id":"S01","sensor_evidence":ev,"primary_hypothesis":primary,"primary_hypothesis_label":primary_label,
            "confidence":"HIGH" if (filter_degraded or cooler_degraded or tv_degraded) else "MEDIUM",
            "confirmatory_question":question,"question_context":"Your answer will confirm or rule out the most likely cause.","answers":answers}


def _s02_low_pressure(s, comp_health, state):
    ev = _evidence(s, comp_health)
    psw1_high = s["PSW1"] > 15
    p1_high_vs_p2 = s["P1"] > s["P2"] + 5
    sep_degraded = comp_health.get("separator_element", 100) < 60

    if psw1_high:
        primary, primary_label = "CORR_006", f"Inlet restriction detected — PSW1 vacuum at {ev['PSW1_mbar']} mbar"
        question = "Is the inlet air filter visually dirty or clogged?"
        answers = [
            {"label":"Yes, filter looks dirty","diagnosis_id":"CORR_006a","diagnosis":"Inlet filter clogged — airflow restricted.","action":"Replace inlet air filter. PSW1 should drop below 10 in.wc after replacement.","urgency":"immediate"},
            {"label":"No, filter looks clean","diagnosis_id":"CORR_006b","diagnosis":"Inlet restriction not from filter — check piping or intake vent.","action":"Inspect inlet piping and intake vent for obstruction.","urgency":"within_7_days"},
        ]
        conf = "HIGH"
    elif p1_high_vs_p2 or sep_degraded:
        primary, primary_label = "CORR_005", "Sump-to-line pressure divergence — separator or minimum pressure valve"
        question = "Is the sump pressure (P1) reading higher than line pressure (P2)?"
        answers = [
            {"label":"Yes, P1 appears higher than P2","diagnosis_id":"CORR_005a","diagnosis":"Separator restriction — pressure building in sump faster than line.","action":"Replace separator element. Check separator dP.","urgency":"within_7_days"},
            {"label":"No, pressures look equal","diagnosis_id":"CORR_005b","diagnosis":"Minimum pressure valve issue.","action":"Inspect minimum pressure valve seat.","urgency":"within_14_days"},
        ]
        conf = "MEDIUM"
    else:
        primary, primary_label = "GENERAL_PRESSURE", "Output pressure below setpoint — cause unclear from sensors"
        question = "Did the pressure drop suddenly or gradually over time?"
        answers = [
            {"label":"Suddenly — within hours/days","diagnosis_id":"CORR_005c","diagnosis":"Possible solenoid or blowdown valve fault.","action":"Check solenoid valve and blowdown valve.","urgency":"within_7_days"},
            {"label":"Gradually — over weeks/months","diagnosis_id":"WEAR","diagnosis":"Progressive air end wear.","action":"Schedule air end inspection and performance test.","urgency":"within_14_days"},
        ]
        conf = "MEDIUM"

    return {"symptom_id":"S02","sensor_evidence":ev,"primary_hypothesis":primary,"primary_hypothesis_label":primary_label,
            "confidence":conf,"confirmatory_question":question,"question_context":"Distinguishes inlet restriction from internal flow restriction.","answers":answers}


def _s03_oil_in_air(s, comp_health, state):
    ev = _evidence(s, comp_health)
    t1_t2_narrow = s["T1_T2_delta"] < 10
    sep_degraded = comp_health.get("separator_element", 100) < 50

    if t1_t2_narrow or sep_degraded:
        primary = "CORR_003"
        primary_label = f"Separator efficiency reduced — T1–T2 delta only {ev['T1_T2_delta_c']}°C"
        question = "Is there visible oil mist at the service outlet, or just an oil smell?"
        answers = [
            {"label":"Visible mist or oil droplets","diagnosis_id":"CORR_003b","diagnosis":"Separator element failure — oil bypassing to discharge.","action":"Replace separator element immediately. Check downstream for contamination.","urgency":"immediate"},
            {"label":"Just a smell, no visible mist","diagnosis_id":"CORR_003a","diagnosis":f"Separator degrading. Health: {ev['separator_health']}%. T1–T2 gap narrowing.","action":f"Plan separator replacement. Act immediately if T1–T2 drops below 5°C.","urgency":"within_7_days"},
        ]
        conf = "HIGH"
    else:
        primary = "CORR_003_EARLY"
        primary_label = "Oil carryover — separator suspect but T1–T2 delta still within range"
        question = "When did the oil in air start — after a recent service or gradually?"
        answers = [
            {"label":"After a recent service","diagnosis_id":"SERVICE_ERROR","diagnosis":"Possible incorrect separator installation or over-filled oil.","action":"Check oil level at mid-sight-glass. Inspect separator installation.","urgency":"immediate"},
            {"label":"Gradually over time","diagnosis_id":"CORR_003c","diagnosis":f"Progressive separator wear. Health: {ev['separator_health']}%.","action":"Schedule separator replacement.","urgency":"within_14_days"},
        ]
        conf = "MEDIUM"

    return {"symptom_id":"S03","sensor_evidence":ev,"primary_hypothesis":primary,"primary_hypothesis_label":primary_label,
            "confidence":conf,"confirmatory_question":question,"question_context":"Severity indicates urgency of separator replacement.","answers":answers}


def _s04_wont_unload(s, comp_health, state):
    ev = _evidence(s, comp_health)
    sol_degraded = comp_health.get("solenoid_valve", 100) < 50
    question = "Does the controller show any solenoid or valve fault codes?"
    answers = [
        {"label":"Yes, solenoid fault shown","diagnosis_id":"CORR_005_SOL","diagnosis":"Solenoid valve failure — cannot signal unload.","action":"Replace solenoid valve. Check wiring before replacing.","urgency":"immediate"},
        {"label":"No fault codes, just won't unload","diagnosis_id":"CORR_005_BLOWDOWN","diagnosis":"Blowdown valve stuck closed.","action":"Inspect blowdown valve for debris. Check pilot line.","urgency":"immediate"},
    ]
    return {"symptom_id":"S04","sensor_evidence":ev,"primary_hypothesis":"CORR_005",
            "primary_hypothesis_label":f"Unload circuit fault — solenoid health {comp_health.get('solenoid_valve',100):.0f}%",
            "confidence":"HIGH" if sol_degraded else "MEDIUM","confirmatory_question":question,
            "question_context":"Distinguishes solenoid electrical fault from blowdown valve mechanical fault.","answers":answers}


def _s05_noise_vibration(s, comp_health, state):
    ev = _evidence(s, comp_health)
    bearing_degraded = comp_health.get("main_motor_bearing", 100) < 50
    coupling_degraded = comp_health.get("coupling_element", 100) < 50
    question = "Where does the noise seem to come from — motor end or compressor end?"
    answers = [
        {"label":"Motor end — near the drive","diagnosis_id":"CH_MOTOR_BEARING","diagnosis":f"Motor bearing wear. Health: {comp_health.get('main_motor_bearing',100):.0f}%.","action":"Replace main motor bearing. Schedule during planned downtime.","urgency":"within_7_days" if bearing_degraded else "within_14_days"},
        {"label":"Compressor end or coupling","diagnosis_id":"CH_COUPLING","diagnosis":f"Coupling element wear. Health: {comp_health.get('coupling_element',100):.0f}%.","action":"Inspect and replace coupling element. Check alignment.","urgency":"within_7_days" if coupling_degraded else "within_14_days"},
    ]
    return {"symptom_id":"S05","sensor_evidence":ev,"primary_hypothesis":"CH_BEARING_OR_COUPLING",
            "primary_hypothesis_label":f"Mechanical wear — bearing {comp_health.get('main_motor_bearing',100):.0f}%, coupling {comp_health.get('coupling_element',100):.0f}%",
            "confidence":"HIGH" if (bearing_degraded or coupling_degraded) else "LOW",
            "confirmatory_question":question,"question_context":"Location identifies motor bearing vs coupling/air end.","answers":answers}


def _s06_oil_loss(s, comp_health, state):
    ev = _evidence(s, comp_health)
    seal_degraded = comp_health.get("shaft_seal", 100) < 50
    sep_degraded = comp_health.get("separator_element", 100) < 50
    question = "Is there oil visible on the outside of the machine, or is it disappearing without visible leakage?"
    answers = [
        {"label":"Visible external leak","diagnosis_id":"CH_SHAFT_SEAL","diagnosis":f"Shaft seal failure. Health: {comp_health.get('shaft_seal',100):.0f}%.","action":"Replace shaft seal assembly. Clean leaked oil before restart.","urgency":"immediate" if seal_degraded else "within_7_days"},
        {"label":"No visible leak, oil disappearing","diagnosis_id":"CORR_003_OIL","diagnosis":f"Oil via separator carryover. Separator health: {ev['separator_health']}%.","action":"Replace separator element. Check discharge air for mist.","urgency":"within_7_days"},
    ]
    return {"symptom_id":"S06","sensor_evidence":ev,"primary_hypothesis":"CH_SHAFT_SEAL_OR_SEPARATOR",
            "primary_hypothesis_label":f"Oil loss — seal {comp_health.get('shaft_seal',100):.0f}%, separator {ev['separator_health']}%",
            "confidence":"HIGH" if (seal_degraded or sep_degraded) else "MEDIUM",
            "confirmatory_question":question,"question_context":"External vs internal loss points to different components.","answers":answers}


def _s07_running_cold(s, comp_health, state):
    ev = _evidence(s, comp_health)
    tv_degraded = comp_health.get("thermal_valve", 100) < 50
    question = "Is the fluid in the sump sight glass looking milky, foamy, or lighter than normal?"
    answers = [
        {"label":"Yes — fluid looks abnormal","diagnosis_id":"CD_002_CONFIRM","diagnosis":"Thermal valve stuck open — overcooling, condensation forming in sump.","action":"Replace thermal valve immediately. Drain and replace fluid — contaminated with water.","urgency":"immediate"},
        {"label":"No — fluid looks normal","diagnosis_id":"CD_002_EARLY","diagnosis":"Thermal valve stuck open — overcooling, condensation not yet visible.","action":f"Replace thermal valve (P/N 02250100-374). Health: {ev['thermal_valve_health']}%. Monitor fluid.","urgency":"within_7_days"},
    ]
    return {"symptom_id":"S07","sensor_evidence":ev,"primary_hypothesis":"CD_002",
            "primary_hypothesis_label":f"Thermal valve stuck open — T1 below model. Valve: {ev['thermal_valve_health']}%",
            "confidence":"HIGH" if tv_degraded else "MEDIUM","confirmatory_question":question,
            "question_context":"Fluid condition shows whether condensation damage has already begun.","answers":answers}


def _s08_filter_message(s, comp_health, state):
    ev = _evidence(s, comp_health)
    delta = s["P4_P3_delta"]
    filter_hrs = state.components.get("fluid_filter")
    hrs = filter_hrs.operating_hours if filter_hrs else 0

    if delta < 3.0 and hrs > 1500:
        primary = "CD_001"
        primary_label = f"Silent bypass suspected — P4–P3 delta only {ev['P4_P3_delta_bar']} bar despite {hrs:.0f}h"
        question = "Has the fluid filter recently been changed or serviced?"
        answers = [
            {"label":"No, not recently changed","diagnosis_id":"CD_001_CONFIRM","diagnosis":"Silent filter bypass — bypass valve opened. Filter message is correct but situation is worse than it appears.","action":"Replace filter AND inspect bypass valve. Unfiltered oil has been reaching air end bearings.","urgency":"immediate"},
            {"label":"Yes, just changed","diagnosis_id":"CD_001_POST_SVC","diagnosis":"Possible reset issue or new filter already bypassing.","action":"Verify filter installation. Reset service counter. Monitor P4–P3 delta.","urgency":"within_7_days"},
        ]
        conf = "HIGH"
    else:
        primary = "CORR_001"
        primary_label = f"Fluid filter differential pressure elevated — {ev['P4_P3_delta_bar']} bar"
        question = "What is the P4–P3 pressure delta showing on the controller?"
        answers = [
            {"label":"Delta is at or above fault threshold","diagnosis_id":"CORR_001_FAULT","diagnosis":f"Fluid filter loaded — P4–P3 at fault threshold: {ev['P4_P3_delta_bar']} bar.","action":"Replace fluid filter element (P/N 02250139-995).","urgency":"immediate"},
            {"label":"Delta is elevated but below fault","diagnosis_id":"CORR_001_WARN","diagnosis":f"Filter loading — approaching threshold. Current: {ev['P4_P3_delta_bar']} bar.","action":"Schedule filter replacement within next 200 operating hours.","urgency":"within_14_days"},
        ]
        conf = "HIGH"

    return {"symptom_id":"S08","sensor_evidence":ev,"primary_hypothesis":primary,"primary_hypothesis_label":primary_label,
            "confidence":conf,"confirmatory_question":question,
            "question_context":"P4–P3 pattern distinguishes normal filter load from dangerous bypass.","answers":answers}


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/symptoms")
def list_symptoms():
    return {"symptoms": SYMPTOMS}


@router.get("/symptoms/{symptom_id}")
def get_symptom_analysis(symptom_id: str):
    if state_module._state is None:
        return {"error": "No scenario loaded — load a scenario on the monitor first"}
    return build_hypotheses(symptom_id, state_module._state)


@router.post("/symptoms/{symptom_id}/answer")
def submit_answer(symptom_id: str, body: dict):
    if state_module._state is None:
        return {"error": "No scenario loaded"}
    analysis = build_hypotheses(symptom_id, state_module._state)
    if "error" in analysis:
        return analysis
    answer_index = body.get("answer_index", 0)
    answers = analysis.get("answers", [])
    if answer_index < 0 or answer_index >= len(answers):
        return {"error": f"answer_index must be 0–{len(answers)-1}"}
    chosen = answers[answer_index]
    return {
        "symptom_id": symptom_id,
        "diagnosis_id": chosen["diagnosis_id"],
        "diagnosis": chosen["diagnosis"],
        "action": chosen["action"],
        "urgency": chosen["urgency"],
        "sensor_evidence": analysis["sensor_evidence"],
        "confidence": analysis["confidence"],
        "fta_node": _map_to_fta(chosen["diagnosis_id"]),
    }


# ── Finding → symptom mapping ─────────────────────────────────────────────────
# Maps active correlation finding IDs to the symptoms they suggest.
# The /pending endpoint uses this to tell the frontend which symptoms
# are relevant RIGHT NOW based on live machine state.

FINDING_TO_SYMPTOMS = {
    "CORR_001":              [{"symptom_id":"S08","reason":"FILTER MAINT threshold reached — P4–P3 Δ at fault level","urgency":"immediate"}],
    "CD_001":                [{"symptom_id":"S08","reason":"Silent bypass suspected — filter loaded but delta collapsed","urgency":"immediate"}],
    "CORR_004":              [{"symptom_id":"S01","reason":"T1 above thermodynamic model — thermal system degrading","urgency":"action"}],
    "CH_THERMAL_VALVE":      [{"symptom_id":"S01","reason":"Thermal valve health critical — temperature regulation failing","urgency":"action"},
                              {"symptom_id":"S07","reason":"Degraded valve may cause overcooling — check if T1 is lower than normal","urgency":"monitor"}],
    "CD_002":                [{"symptom_id":"S07","reason":"Thermal valve stuck open detected — overcooling confirmed","urgency":"immediate"}],
    "CORR_003":              [{"symptom_id":"S03","reason":"T1–T2 gap narrow — separator efficiency reduced, oil carryover likely","urgency":"action"},
                              {"symptom_id":"S06","reason":"Separator degraded — oil passing through to discharge","urgency":"action"}],
    "CORR_005":              [{"symptom_id":"S02","reason":"P1 exceeding P2 — separator restriction reducing line pressure","urgency":"action"}],
    "CORR_002":              [{"symptom_id":"S08","reason":"P3 dropping with T1 flat — possible bypass open","urgency":"action"}],
    "CORR_006":              [{"symptom_id":"S02","reason":"Both inlet and fluid restriction elevated — dual contamination source","urgency":"action"}],
    "CH_MAIN_MOTOR_BEARING": [{"symptom_id":"S05","reason":"Motor bearing health critical — vibration or noise may be present","urgency":"action"}],
    "CH_COUPLING_ELEMENT":   [{"symptom_id":"S05","reason":"Coupling element degraded — vibration at motor-compressor interface","urgency":"action"}],
    "CH_SHAFT_SEAL":         [{"symptom_id":"S06","reason":"Shaft seal health critical — external oil leakage risk","urgency":"action"}],
    "CH_OIL_COOLER":         [{"symptom_id":"S01","reason":"Oil cooler degraded — heat dissipation reduced","urgency":"action"}],
    "CH_SEPARATOR_ELEMENT":  [{"symptom_id":"S03","reason":"Separator element health critical — oil carryover into air line","urgency":"action"},
                              {"symptom_id":"S06","reason":"Separator degraded — elevated oil consumption expected","urgency":"monitor"}],
    "CH_SOLENOID_VALVE":     [{"symptom_id":"S04","reason":"Solenoid valve health critical — unload failure risk","urgency":"action"}],
    "CD_003":                [{"symptom_id":"S03","reason":"Separator failure developing — pre-alarm signature detected","urgency":"action"}],
}

URGENCY_ORDER = {"immediate": 0, "action": 1, "monitor": 2}


@router.get("/pending")
def get_pending_suggestions():
    """
    Return symptom suggestions driven by currently active correlation findings.
    Frontend uses this to highlight relevant symptoms on the diagnose page —
    only symptoms tied to what the machine is actually showing right now.
    Polls every 8 seconds alongside the FTA refresh.
    """
    if state_module._state is None:
        return {"suggestions": [], "context": "No scenario loaded"}

    from analysis.correlator import analyse_to_dict
    findings = analyse_to_dict(state_module._state)
    active_finding_ids = {f["correlation_id"] for f in findings}

    seen: dict = {}
    for finding_id in active_finding_ids:
        for sug in FINDING_TO_SYMPTOMS.get(finding_id, []):
            sid = sug["symptom_id"]
            existing = seen.get(sid)
            if existing is None or URGENCY_ORDER[sug["urgency"]] < URGENCY_ORDER[existing["urgency"]]:
                label = next((s["label"] for s in SYMPTOMS if s["id"] == sid), sid)
                seen[sid] = {
                    "symptom_id": sid,
                    "label": label,
                    "reason": sug["reason"],
                    "urgency": sug["urgency"],
                    "triggered_by": finding_id,
                }

    suggestions = sorted(seen.values(), key=lambda x: (URGENCY_ORDER[x["urgency"]], x["symptom_id"]))
    return {
        "suggestions": suggestions,
        "active_finding_count": len(active_finding_ids),
        "context": f"{len(suggestions)} symptom(s) suggested from {len(active_finding_ids)} active finding(s)",
    }


# ── FTA cross-link map ────────────────────────────────────────────────────────

def _map_to_fta(diagnosis_id: str) -> str | None:
    mapping = {
        "CORR_001":"CORR_001","CORR_001_FAULT":"CORR_001","CORR_001_WARN":"CORR_001",
        "CD_001":"CD_001","CD_001_CONFIRM":"CD_001",
        "CORR_002":"CORR_002",
        "CORR_003a":"CORR_003","CORR_003b":"CORR_003","CORR_003c":"CORR_003","CORR_003_OIL":"CORR_003",
        "CORR_004a":"CORR_004","CORR_004b":"CORR_004","CORR_004c":"CORR_004",
        "CORR_005a":"CORR_005","CORR_005b":"CORR_005","CORR_005c":"CORR_005",
        "CORR_005_SOL":"SOLENOID","CORR_005_BLOWDOWN":"SOLENOID",
        "CORR_006a":"CORR_006","CORR_006b":"CORR_006",
        "CD_002":"CD_002","CD_002_CONFIRM":"CD_002","CD_002_EARLY":"CD_002",
        "CH_THERMAL_VALVE":"THERMAL","CH_MOTOR_BEARING":"TOP",
        "CH_COUPLING":"TOP","CH_SHAFT_SEAL":"TOP",
    }
    return mapping.get(diagnosis_id)
