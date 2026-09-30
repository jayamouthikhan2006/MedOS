from medos import triage
from medos.voice import parse_checkin, words_to_digits


def level(text, **kw):
    return triage.analyse(text, **kw)["level"]


def test_priority_table_order():
    ranks = {c["name"]: c["rank"] for c in triage.DEFAULT_CONDITIONS if not c.get("red_flag")}
    assert ranks["Severe abdominal pain"] == 1
    assert ranks["Fracture"] == 2
    assert ranks["Routine check-up"] == 15
    assert len(ranks) == 15


def test_red_flags_are_critical():
    assert level("I have chest pain and sweating") == "critical"
    assert level("my father can't breathe properly") == "critical"
    assert level("snake bite on the leg") == "critical"
    assert level("he collapsed at home") == "critical"


def test_table_levels():
    assert level("severe abdominal pain since morning") == "high"
    assert level("I think I have a fractured wrist") == "high"
    assert level("migraine since two days") == "medium"
    assert level("stomach ache") == "low"
    assert level("routine check-up") == "low"


def test_most_urgent_match_wins():
    r = triage.analyse("severe stomach pain and a cold")
    assert r["primary_condition"] == "Severe abdominal pain"
    assert r["rank"] == 1


def test_negation():
    assert level("no chest pain, just a cough") == "low"
    assert level("not breathless, mild cold") == "low"


def test_word_boundaries():
    # "stable" must not trigger the stab-wound red flag
    assert level("patient is stable, came for a routine check up") == "low"


def test_fever_by_temperature():
    assert triage.analyse("fever", vitals={"temp": 103})["primary_condition"] == "High fever (>102°F)"
    assert triage.analyse("fever of 39.5 degrees celsius")["level"] == "high"
    assert triage.analyse("mild fever 99")["level"] == "low"


def test_vitals_raise_level():
    assert level("cough", vitals={"spo2": 86}) == "critical"
    assert level("cough", vitals={"spo2": 92}) == "high"
    assert level("headache", vitals={"bp_sys": 85}) == "critical"


def test_modifiers_raise_score_within_level():
    young = triage.analyse("cold and cough", age=30)
    old = triage.analyse("cold and cough", age=80)
    assert old["level"] == young["level"] == "low"
    assert old["base_score"] > young["base_score"]


def test_levels_never_overlap():
    best_high = triage.base_score("high", 1, 140)       # top High with every modifier
    worst_critical = triage.base_score("critical", 15)
    assert worst_critical > best_high + 450              # the default aging cap can't lift High above Critical


def test_words_to_digits():
    assert words_to_digits("I am forty five years old") == "I am 45 years old"
    assert words_to_digits("fever of one hundred and two") == "fever of 102"
    assert "9876543210" in words_to_digits("nine eight seven six five four three two one zero")


def test_parse_checkin():
    f = parse_checkin("my name is ravi kumar and I am forty two years old, I have severe stomach pain")
    assert f["name"] == "Ravi Kumar"
    assert f["age"] == 42
    assert "stomach pain" in f["symptoms"].lower()
    g = parse_checkin("I am having fever")
    assert g["name"] is None


def test_red_flag_phrasings():
    assert level("breathing difficulty since an hour") == "critical"
    assert level("his lips look bluish") == "critical"
    assert level("my chest feels very tight") == "critical"
    assert level("her face is drooping on one side") == "critical"
    assert level("vomiting blood since morning") == "critical"
    assert level("a snake bit him in the field") == "critical"


def test_no_false_alarm_from_blood_tests():
    assert level("mild cough, came to collect blood test report") == "low"


def test_parse_unpunctuated_speech():
    # Offline speech recognition gives no punctuation at all.
    f = parse_checkin("my name is kavitha ramesh i am thirty four years old i have a high fever since two days")
    assert f["name"] == "Kavitha Ramesh"
    assert f["age"] == 34
    assert f["symptoms"].lower().startswith("i have a high fever")


def test_minor_fall_is_not_major_trauma():
    assert level("fell from bike, swelling and suspected fracture in left wrist") == "high"
    assert level("fell from the terrace, not moving legs") == "critical"
