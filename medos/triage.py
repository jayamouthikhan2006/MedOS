"""Rule-based symptom analysis.

Always available (no network, no model). Produces a level (critical/high/medium/low),
a protocol rank (1 = most urgent in the priority table), a base priority score and
human-readable reasons. The AI second opinion (llm.py) can only raise the level.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

LEVELS = ("critical", "high", "medium", "low")
LEVEL_ORDER = {lvl: i for i, lvl in enumerate(LEVELS)}   # lower index = more urgent
LEVEL_BASE = {"critical": 1500.0, "high": 600.0, "medium": 300.0, "low": 0.0}
LEVEL_LABEL = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}

# Red flags sit above the 15-row priority table (rank 0) and are always Critical.
# Rows 1–15 are the project's priority table, in its original order.
DEFAULT_CONDITIONS: List[Dict[str, Any]] = [
    {"name": "Chest pain / suspected heart attack", "rank": 0, "level": "critical", "red_flag": True,
     "department": "Emergency", "reason": "Possible cardiac event — minutes matter",
     "keywords": ["chest pain", "pain in chest", "chest tightness", "tight chest", "heart attack",
                  "crushing chest", "chest pressure", "cardiac", "left arm pain+sweat", "pain in my chest",
                  "chest+tight*", "chest+heavy", "chest+squeez*"]},
    {"name": "Breathing difficulty", "rank": 0, "level": "critical", "red_flag": True,
     "department": "Emergency", "reason": "Airway or breathing compromise",
     "keywords": ["difficulty breathing", "difficulty in breathing", "can't breathe", "cannot breathe",
                  "cant breathe", "unable to breathe", "shortness of breath", "short of breath",
                  "breathless", "gasping", "choking", "severe asthma", "blue lips", "not able to breathe",
                  "breathing difficulty", "breath*+difficult*", "breath*+trouble", "hard to breathe",
                  "can't catch my breath", "lips+blu*", "struggling to breathe"]},
    {"name": "Stroke signs", "rank": 0, "level": "critical", "red_flag": True,
     "department": "Emergency", "reason": "Time-critical brain injury",
     "keywords": ["stroke", "face drooping", "facial droop", "slurred speech", "one side weak",
                  "weakness on one side", "paralysis", "paralysed", "paralyzed", "sudden confusion",
                  "can't move one side", "face+droop*", "slurr*", "mouth+droop*"]},
    {"name": "Unconscious / collapsed", "rank": 0, "level": "critical", "red_flag": True,
     "department": "Emergency", "reason": "Unresponsive patient",
     "keywords": ["unconscious", "unresponsive", "fainted", "collapsed", "passed out",
                  "not responding", "not breathing", "blacked out", "not waking up", "fainting"]},
    {"name": "Severe bleeding", "rank": 0, "level": "critical", "red_flag": True,
     "department": "Emergency", "reason": "Risk of shock from blood loss",
     "keywords": ["heavy bleeding", "bleeding heavily", "severe bleeding", "profuse bleeding",
                  "won't stop bleeding", "not stopping bleeding", "vomiting blood", "coughing blood",
                  "blood in vomit", "hemorrhage", "haemorrhage", "lot of blood", "bleeding+a lot",
                  "vomited blood", "coughing up blood", "blood in sputum", "blood in cough"]},
    {"name": "Seizure", "rank": 0, "level": "critical", "red_flag": True,
     "department": "Emergency", "reason": "Active or recent seizure",
     "keywords": ["seizure", "fits", "fitting", "convulsion", "epileptic attack"]},
    {"name": "Severe allergic reaction", "rank": 0, "level": "critical", "red_flag": True,
     "department": "Emergency", "reason": "Possible anaphylaxis",
     "keywords": ["anaphylaxis", "anaphylactic", "throat swelling", "swollen throat", "tongue swelling",
                  "severe allergic", "face swelling+allergy", "lips swelling+allergy", "swell*+throat",
                  "swell*+tongue"]},
    {"name": "Major trauma", "rank": 0, "level": "critical", "red_flag": True,
     "department": "Emergency", "reason": "High-energy injury",
     "keywords": ["road accident", "accident", "head injury", "fall from height", "stab wound", "stabbed", "stabbing", "gunshot",
                  "crush injury", "hit by a", "hit by car", "hit by bike", "hit+head", "fell from height",
                  "fell from the roof", "fell from a building", "fell from a tree", "fell from the terrace"]},
    {"name": "Poisoning / bite / overdose", "rank": 0, "level": "critical", "red_flag": True,
     "department": "Emergency", "reason": "Toxic exposure",
     "keywords": ["poison", "overdose", "snake bite", "snakebite", "scorpion sting", "pesticide",
                  "swallowed chemical", "suicide attempt", "consumed poison", "rat poison", "kerosene",
                  "snake+bit*", "bitten by a snake"]},
    {"name": "Severe burns / electric shock", "rank": 0, "level": "critical", "red_flag": True,
     "department": "Emergency", "reason": "Burn or electrical injury",
     "keywords": ["severe burn", "major burn", "electric shock", "electrocuted", "burnt badly",
                  "badly burned", "burn+face", "caught fire", "acid attack", "burn*+severe"]},
    # ---- The project's priority table -------------------------------------------------
    {"name": "Severe abdominal pain", "rank": 1, "level": "high", "department": "General Surgery",
     "reason": "Could require urgent treatment",
     "keywords": ["severe+abdominal pain", "severe+stomach pain", "severe+abdomen", "severe+belly",
                  "stomach+paining a lot", "stomach+unbearable", "stomach+very severe",
                  "severe+tummy", "unbearable+stomach", "unbearable+abdominal", "intense+abdominal",
                  "acute abdomen", "appendicitis", "very bad stomach pain", "severe abdominal"]},
    {"name": "Fracture", "rank": 2, "level": "high", "department": "Orthopaedics",
     "reason": "Painful, risk of further injury",
     "keywords": ["fracture", "broken bone", "broken arm", "broken leg", "broken wrist",
                  "broken ankle", "bone broke", "dislocat*", "cannot move my arm", "cannot move my leg"]},
    {"name": "High fever (>102°F)", "rank": 3, "level": "high", "department": "General Medicine",
     "reason": "May indicate serious infection",
     "keywords": ["high fever", "very high fever", "high temperature", "burning with fever",
                  "fever+shivering"]},
    {"name": "Severe migraine/headache", "rank": 4, "level": "medium", "department": "Neurology",
     "reason": "Needs attention but usually stable",
     "keywords": ["migraine", "severe+headache", "bad headache", "splitting headache",
                  "worst headache", "severe head pain"]},
    {"name": "Persistent vomiting", "rank": 5, "level": "medium", "department": "General Medicine",
     "reason": "Risk of dehydration",
     "keywords": ["persistent vomiting", "keeps vomiting", "keep vomiting", "continuous vomiting",
                  "repeated vomiting", "can't stop vomiting", "vomiting since", "vomiting+diarrhea",
                  "vomiting+diarrhoea", "dehydrat*", "vomiting many times"]},
    {"name": "Asthma attack (mild)", "rank": 6, "level": "medium", "department": "Pulmonology",
     "reason": "Monitor closely",
     "keywords": ["asthma", "wheez*", "inhaler"]},
    {"name": "Chest infection", "rank": 7, "level": "medium", "department": "Pulmonology",
     "reason": "Moderate priority",
     "keywords": ["chest infection", "bronchitis", "pneumonia", "phlegm", "chest congestion",
                  "productive cough", "cough with sputum"]},
    {"name": "Back pain", "rank": 8, "level": "low", "department": "Orthopaedics",
     "reason": "Non-life-threatening",
     "keywords": ["back pain", "backache", "back ache", "lower back", "spine pain", "neck pain"]},
    {"name": "Stomach pain (mild)", "rank": 9, "level": "low", "department": "Gastroenterology",
     "reason": "Moderate discomfort",
     "keywords": ["stomach pain", "stomach ache", "stomachache", "abdominal pain", "tummy ache",
                  "belly pain", "stomach+pain*", "abdomen+pain*", "tummy+pain*", "belly+pain*",
                  "gastric", "acidity", "indigestion", "stomach upset", "vomiting",
                  "loose motion", "diarrhea", "diarrhoea"]},
    {"name": "Ear pain", "rank": 10, "level": "low", "department": "ENT",
     "reason": "Low urgency",
     "keywords": ["ear pain", "earache", "ear ache", "ear infection", "pain in ear", "pain in my ear"]},
    {"name": "Sore throat", "rank": 11, "level": "low", "department": "ENT",
     "reason": "Low urgency",
     "keywords": ["sore throat", "throat pain", "tonsil", "tonsillitis", "throat infection", "pain in throat",
                  "difficulty swallowing"]},
    {"name": "Skin allergy/rash", "rank": 12, "level": "low", "department": "Dermatology",
     "reason": "Usually non-urgent",
     "keywords": ["rash", "itching", "itchy", "skin allergy", "hives", "eczema", "allergy"]},
    {"name": "Cold & cough", "rank": 13, "level": "low", "department": "General Medicine",
     "reason": "Common illness",
     "keywords": ["cold", "cough", "runny nose", "sneez*", "blocked nose", "nasal congestion"]},
    {"name": "Mild fever", "rank": 14, "level": "low", "department": "General Medicine",
     "reason": "Lowest among fever cases",
     "keywords": ["fever", "feverish", "temperature", "chills", "body ache", "kaichal", "bukhar"]},
    {"name": "Routine check-up", "rank": 15, "level": "low", "department": "General Medicine",
     "reason": "Can wait the longest",
     "keywords": ["check-up", "checkup", "check up", "routine", "follow-up", "follow up",
                  "review visit", "prescription", "bp check", "sugar check", "blood pressure check",
                  "medical certificate", "vaccination"]},
]

NEGATION = re.compile(r"\b(no|not|without|denies|deny|denied|never|nil|free of|absence of)\b")
SENTENCE_SPLIT = re.compile(r"[.;!?\n]+")
TEMP_RE = re.compile(
    r"(?:fever|temperature|temp)\D{0,20}?(\d{2,3}(?:\.\d)?)\s*(?:°|degrees?|deg)?\s*(f|c|fahrenheit|celsius)?"
    r"|(\d{2,3}(?:\.\d)?)\s*(?:°|degrees?|deg)\s*(f|c|fahrenheit|celsius)?",
    re.I)


def normalise(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("’", "'").replace("‘", "'")
    return re.sub(r"[ \t]+", " ", text)


def _negated(text: str, start: int) -> bool:
    window = text[max(0, start - 40):start]
    window = re.split(r"[,.;!?\n]| but | however ", window)[-1]
    return bool(NEGATION.search(window))


def _find_phrase(text: str, phrase: str) -> Optional[int]:
    """Position of `phrase` as whole words (plural/tense endings allowed) that is not negated.

    A trailing '*' makes the phrase a prefix: 'dehydrat*' matches dehydrated, dehydration.
    """
    phrase = phrase.strip()
    if not phrase:
        return None
    if phrase.endswith("*"):
        pattern = r"(?<![a-z0-9])" + re.escape(phrase[:-1])
    else:
        pattern = r"(?<![a-z0-9])" + re.escape(phrase) + r"(?:s|es|d|ed|ing)?(?![a-z0-9])"
    for m in re.finditer(pattern, text):
        if not _negated(text, m.start()):
            return m.start()
    return None


def keyword_matches(text: str, keyword: str) -> bool:
    """A keyword is a phrase, or phrases joined by '+' that must share a sentence."""
    parts = [p.strip() for p in keyword.lower().split("+") if p.strip()]
    if not parts:
        return False
    if len(parts) == 1:
        return _find_phrase(text, parts[0]) is not None
    for sentence in SENTENCE_SPLIT.split(text):
        if all(_find_phrase(sentence, p) is not None for p in parts):
            return True
    return False


def parse_temperature_f(text: str) -> Optional[float]:
    for m in TEMP_RE.finditer(text):
        value = m.group(1) or m.group(3)
        unit = (m.group(2) or m.group(4) or "").lower()
        if not value:
            continue
        t = float(value)
        if unit.startswith("c") or (not unit and 34 <= t <= 43):
            t = t * 9 / 5 + 32
        if 93 <= t <= 110:
            return round(t, 1)
    return None


def _num(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def temp_to_f(v: Any) -> Optional[float]:
    t = _num(v)
    if t is None:
        return None
    return round(t * 9 / 5 + 32, 1) if t < 50 else t


def more_urgent(a: str, b: str) -> str:
    return a if LEVEL_ORDER.get(a, 9) <= LEVEL_ORDER.get(b, 9) else b


def base_score(level: str, rank: int, bonus: float = 0.0) -> float:
    return LEVEL_BASE.get(level, 0.0) + (16 - max(0, min(rank, 15))) * 10 + bonus


def analyse(symptoms: str, age: Any = None, sex: Optional[str] = None,
            vitals: Optional[Dict[str, Any]] = None, pregnant: bool = False,
            conditions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Return the triage decision for one patient."""
    text = normalise(symptoms)
    conditions = conditions if conditions is not None else DEFAULT_CONDITIONS
    vitals = vitals or {}
    matched: List[Dict[str, Any]] = []
    reasons: List[str] = []
    red_flags: List[str] = []

    for cond in conditions:
        if not cond.get("active", 1):
            continue
        kws = cond["keywords"]
        if isinstance(kws, str):
            kws = [k for k in kws.split(",")]
        for kw in kws:
            if kw.strip() and keyword_matches(text, kw):
                matched.append({"name": cond["name"], "rank": int(cond["rank"]), "level": cond["level"],
                                "keyword": kw.strip(), "reason": cond.get("reason"),
                                "department": cond.get("department"),
                                "red_flag": bool(cond.get("red_flag"))})
                break

    # Temperature: from the vitals box or spoken ("fever of 103")
    temp_f = temp_to_f(vitals.get("temp")) or parse_temperature_f(text)
    if temp_f is not None:
        by_name = {c["name"]: c for c in conditions}
        if temp_f >= 102:
            c = next((c for c in conditions if int(c["rank"]) == 3), by_name.get("High fever (>102°F)"))
            if c and not any(m["rank"] == int(c["rank"]) for m in matched):
                matched.append({"name": c["name"], "rank": int(c["rank"]), "level": c["level"],
                                "keyword": "%.1f°F" % temp_f, "reason": c.get("reason"),
                                "department": c.get("department"), "red_flag": False})

    level = "low"
    rank = 15
    primary = None
    department = None
    if matched:
        matched.sort(key=lambda m: (LEVEL_ORDER[m["level"]], m["rank"]))
        top = matched[0]
        level, rank, primary, department = top["level"], top["rank"], top["name"], top["department"]
        for m in matched:
            if m["red_flag"]:
                red_flags.append(m["name"])
        reasons.append("%s — %s" % (top["name"], top["reason"] or "protocol match"))
    else:
        rank = 12
        primary = "Unclassified complaint"
        department = "General Medicine"
        reasons.append("No protocol match — confirm at the desk")

    bonus = 0.0

    # Vital signs can raise the level on their own.
    spo2 = _num(vitals.get("spo2"))
    hr = _num(vitals.get("pulse"))
    sbp = _num(vitals.get("bp_sys"))
    pain = _num(vitals.get("pain"))

    def floor(new_level: str, why: str, flag: bool = False):
        nonlocal level, rank, primary
        if LEVEL_ORDER[new_level] < LEVEL_ORDER[level]:
            level = new_level
            rank = min(rank, 0 if new_level == "critical" else rank)
            primary = why if new_level == "critical" else primary
        reasons.append(why)
        if flag:
            red_flags.append(why)

    if spo2 is not None:
        if spo2 < 90:
            floor("critical", "SpO₂ %d%% — low oxygen" % spo2, True)
        elif spo2 < 94:
            floor("high", "SpO₂ %d%% — below normal" % spo2)
    if hr is not None:
        if hr >= 130 or hr <= 40:
            floor("critical", "Pulse %d bpm — unstable" % hr, True)
        elif hr > 110:
            floor("medium", "Pulse %d bpm — fast" % hr)
    if sbp is not None:
        if sbp < 90:
            floor("critical", "BP %d systolic — low" % sbp, True)
        elif sbp >= 180:
            floor("high", "BP %d systolic — very high" % sbp)
    if temp_f is not None and temp_f >= 104:
        floor("high", "Temperature %.1f°F — very high" % temp_f)
    if pain is not None and pain >= 8:
        floor("medium", "Pain score %d/10" % pain)
        bonus += 40

    # Modifiers raise the score inside a level, never across Critical.
    a = _num(age)
    if a is not None:
        if a >= 65:
            bonus += 40
            reasons.append("Age %d — elderly" % a)
        elif a <= 5:
            bonus += 40
            reasons.append("Age %d — young child" % a)
    if pregnant:
        bonus += 60
        reasons.append("Pregnant")
        if any(m["rank"] in (1, 9) for m in matched) or "bleeding" in text:
            floor("high", "Pregnancy with abdominal pain or bleeding")

    if red_flags and level != "critical":
        level = "critical"
        rank = 0

    return {
        "level": level,
        "rank": rank,
        "base_score": base_score(level, rank, bonus),
        "primary_condition": primary,
        "department": department,
        "conditions": [{"name": m["name"], "rank": m["rank"], "level": m["level"], "keyword": m["keyword"]}
                       for m in matched],
        "red_flags": list(dict.fromkeys(red_flags)),
        "reasons": list(dict.fromkeys(reasons)),
        "temp_f": temp_f,
        "bonus": bonus,
    }
