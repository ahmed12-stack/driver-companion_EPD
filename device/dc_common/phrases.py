"""Everything the device says that is known in advance.

Kept in one place so the speech module can make these clips ahead of time
(a road-sign alert must start within 1 s, REQ-02), and so the wording stays
the same across modules.
"""

# Road signs (the 20 IRC:67 classes of the vision CNN) -> what the driver hears.
SIGN_PHRASES = {
    "speed_limit_20": "Speed limit 20.",
    "speed_limit_30": "Speed limit 30.",
    "speed_limit_40": "Speed limit 40.",
    "speed_limit_50": "Speed limit 50.",
    "speed_limit_60": "Speed limit 60.",
    "speed_limit_80": "Speed limit 80.",
    "speed_limit_100": "Speed limit 100.",
    "stop": "Stop sign ahead.",
    "give_way": "Give way.",
    "no_entry": "No entry.",
    "no_overtaking": "No overtaking.",
    "no_u_turn": "No U-turn.",
    "no_horn": "No honking.",
    "no_parking": "No parking.",
    "school_ahead": "School ahead.",
    "pedestrian_crossing": "Pedestrian crossing ahead.",
    "speed_breaker": "Speed breaker ahead.",
    "left_curve": "Left curve ahead.",
    "right_curve": "Right curve ahead.",
    "men_at_work": "Men at work ahead.",
}

# Friendly module names, for spoken warnings.
MODULE_NAMES = {
    "dc_speech": "speech",
    "dc_phone": "phone link",
    "dc_trip": "trip logger",
    "dc_ignition": "ignition sensor",
    "dc_health": "health monitor",
    "dc_vision": "sign reader",
    "dc_speed": "speed check",
}

SIGNS_PAUSED = "Sign alerts paused."
SIGNS_RESUMED = "Sign alerts resumed."
CAMERA_MISSING = "Camera not found. Sign alerts are off."
CAMERA_FOUND = "Camera found. Sign alerts are on."
SIGNS_FAILED = "The sign reader could not start."
SELFTEST_OK = "Self test passed. System ready."
PAIRING_ON = "Pairing mode on."
PAIRING_OFF = "Pairing mode off."
PHONE_PAIRED = "Phone paired."

MODULE_PROBLEMS = ("stopped working", "is not responding")


SPEED_LIMITS = (20, 30, 40, 50, 60, 80, 100)


def overspeed_phrase(limit_kmh: int) -> str:
    return f"Slow down. Speed limit {int(limit_kmh)}."


def sign_phrase(label: str) -> str:
    return SIGN_PHRASES.get(label) or label.replace("_", " ").capitalize() + "."


def module_warning(module: str, problem: str) -> str:
    name = MODULE_NAMES.get(module, module.removeprefix("dc_"))
    return f"Warning: the {name} {problem}."


def fixed_phrases() -> list:
    """Every phrase worth making in advance, most urgent first."""
    out = [overspeed_phrase(k) for k in SPEED_LIMITS]
    out += list(SIGN_PHRASES.values())
    out += [SIGNS_PAUSED, SIGNS_RESUMED, CAMERA_MISSING, CAMERA_FOUND, SIGNS_FAILED,
            SELFTEST_OK, PAIRING_ON, PAIRING_OFF, PHONE_PAIRED]
    out += [module_warning(m, p) for m in MODULE_NAMES for p in MODULE_PROBLEMS]
    return list(dict.fromkeys(out))
