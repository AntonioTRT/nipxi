"""
Post-run diagnostic classification for Charge Battery / Discharge Battery
-- Test Mode informational analysis ONLY -- PLUS the battery-presence
voltage classifier used by the pre-test Battery Presence Check (see
test_control/battery_presence_precheck.py and docs/architecture.md
"Battery Presence + NTC Presence Diagnostics").

Why the post-run classification exists: before the pre-test Battery
Presence Check was added, NTC validation was the only thing checked
before a relay ever closed -- an operator could still validate relay
routing, SMU behavior, DMM readings, SafetyMonitor integration, and
logging infrastructure against partially assembled hardware, but an empty
position could look deceptively like a real result: ChargeSequence's CV
compliance into an open circuit satisfies its own EOC condition (voltage
at/above target, current near zero) almost immediately, indistinguishable
at a glance from a battery that was simply already fully charged;
DischargeSequence sinking current from an open circuit drives voltage
toward the SMU's compliance floor almost immediately, tripping
SafetyMonitor's undervoltage check. classify_charge_behavior()/
classify_discharge_behavior() below give a human that distinction as an
ADDITIVE label -- they never gate, never raise, never change stop_reason/
result, and are computed entirely from data the sampling loop already
acquired (see test_control/battery_operation_sequence.py::
_ChargeDischargeStats) -- no new hardware reads happen there. This
remains useful even now that a real pre-test presence gate exists: the
pre-test check only samples voltage once, before the relay closes for the
real run; these post-run classifiers see the whole sampling loop's
behavior and catch cases the single pre-test reading could miss (e.g. a
connection that reads plausible at rest but never delivers real current).

classify_battery_presence() (below) is a DIFFERENT kind of function from
the two classifiers above -- a pure voltage->classification mapping with
no gating/raising of its own either (mirrors hardware/temperature.py::
classify_ntc_presence()'s own relationship to its caller: the caller
decides what to do with the classification, this function only produces
it). It is reused by test_control/battery_presence_precheck.py to
determine whether a battery is physically present at all, BEFORE a test
starts -- a distinct question from "is this position's charge/discharge
behavior normal," answered independently.

Thresholds below are best-effort STARTING POINTS, not calibrated against
real battery/rig behavior -- same "unconfirmed placeholder" convention
already used throughout config/devices.py's BATTERY_CONFIGS. Tune once
real hardware validation data is available (see docs/TODO.md).
"""

from config.settings import Settings


class ChargeDiagnosis:
    ALREADY_CHARGED         = "ALREADY_CHARGED"
    POSSIBLY_EMPTY_POSITION = "POSSIBLY_EMPTY_POSITION"
    NORMAL_CHARGE_BEHAVIOR  = "NORMAL_CHARGE_BEHAVIOR"


class DischargeDiagnosis:
    NORMAL_DISCHARGE_BEHAVIOR = "NORMAL_DISCHARGE_BEHAVIOR"
    # Deliberately the SAME literal as ChargeDiagnosis.POSSIBLY_EMPTY_POSITION
    # (matches the spec, which uses this one result name for both charge and
    # discharge) -- run_summary.test_type ("charge_battery"/"discharge_battery")
    # is what disambiguates which message applies; see message_for()'s `mode`
    # parameter below, never a second/renamed constant here.
    POSSIBLY_EMPTY_POSITION = "POSSIBLY_EMPTY_POSITION"


# Message text is owned HERE only -- classify_*() and any later reader of a
# stored analysis_result (e.g. run_summary_report.py) both resolve through
# message_for() below, never a second copy of this wording. Split by mode
# because ChargeDiagnosis/DischargeDiagnosis's POSSIBLY_EMPTY_POSITION share
# one string value (see the comment on DischargeDiagnosis above) -- a single
# result-keyed dict would silently let one mode's wording clobber the
# other's, so `mode` ("charge"/"discharge") picks the right table instead.
_CHARGE_MESSAGES = {
    ChargeDiagnosis.ALREADY_CHARGED:
        "Battery appears already charged before the test started.",
    ChargeDiagnosis.POSSIBLY_EMPTY_POSITION:
        "No meaningful charging current detected. Position may be empty, disconnected, or improperly wired.",
    ChargeDiagnosis.NORMAL_CHARGE_BEHAVIOR: "",
}
_DISCHARGE_MESSAGES = {
    DischargeDiagnosis.NORMAL_DISCHARGE_BEHAVIOR: "",
    DischargeDiagnosis.POSSIBLY_EMPTY_POSITION:
        "No meaningful discharge current detected. Position may be empty, disconnected, or improperly wired.",
}


def message_for(result: str, mode: str) -> str:
    """
    Human-readable message for a stored analysis_result -- "" for the
    normal-behavior cases, which have nothing noteworthy to say.
    `mode` ("charge"/"discharge") selects which table to resolve against,
    since ChargeDiagnosis/DischargeDiagnosis's POSSIBLY_EMPTY_POSITION share
    one string value -- see the module-level comment above.
    """
    table = _CHARGE_MESSAGES if mode == "charge" else _DISCHARGE_MESSAGES
    return table.get(result, "")


# -- Tunable thresholds (see module docstring) -------------------------------

# Within this many volts of battery_cfg["voltage_max_v"] counts as "already
# at the CV target" -- a margin, not an absolute voltage, so it scales
# correctly across battery types with different voltage windows (SB/HUB).
NEAR_FULL_MARGIN_V = 0.05

# At/below this ABSOLUTE voltage looks like an open/disconnected circuit,
# not a real (even deeply depleted) cell -- deliberately NOT
# battery_cfg["voltage_min_v"] (a real cell's floor, e.g. 3.0 V for SB/HUB):
# an absent position floats near 0 V, far below any real cell's practical
# minimum, so this is a different concept than "needs charging." Same
# order of magnitude as Settings.REVERSE_POLARITY_VOLTAGE_THRESHOLD_V
# (-0.5 V) already used in this codebase for an analogous "this reading
# doesn't look like a real intact cell" check.
EMPTY_POSITION_VOLTAGE_V = 0.5

# Average |current| below this fraction of the COMMANDED setpoint current
# (test_setpoints' charge/discharge current, not an absolute amp value)
# counts as "no meaningful current" -- scales correctly across groups with
# different commanded currents.
MIN_MEANINGFUL_CURRENT_FRACTION = 0.05

# Duration at/below this counts as "very short" -- comfortably above the
# unavoidable minimum time to ANY sample (Settings.STABILIZATION_S = 5.0 s
# plus at least one Settings.SAMPLE_RATE_HZ = 1.0 Hz sample period), and
# far below a realistic full charge/discharge (hours, per
# Settings.CHARGE_TIMEOUT_S/DISCHARGE_TIMEOUT_S = 7200 s) -- so this only
# ever fires on a near-instant EOC/EOD, never a genuinely slow real run.
SHORT_DURATION_S = 15.0


def classify_charge_behavior(*, initial_voltage_v, avg_current_a, duration_s,
                              commanded_current_a, battery_cfg) -> str:
    """
    Returns one of ChargeDiagnosis's three values. `initial_voltage_v`/
    `avg_current_a`/`duration_s` come from _ChargeDischargeStats, already
    accumulated by ChargeSequence's own sampling loop -- no new read here.
    `initial_voltage_v` is None if the sampling loop never took a single
    sample (e.g. cancelled/failed before the first reading) -- reported as
    NORMAL_CHARGE_BEHAVIOR, since there is nothing to classify.
    """
    if initial_voltage_v is None:
        return ChargeDiagnosis.NORMAL_CHARGE_BEHAVIOR

    small_current = avg_current_a < commanded_current_a * MIN_MEANINGFUL_CURRENT_FRACTION
    short_duration = duration_s <= SHORT_DURATION_S
    if small_current and short_duration:
        if initial_voltage_v >= battery_cfg["voltage_max_v"] - NEAR_FULL_MARGIN_V:
            return ChargeDiagnosis.ALREADY_CHARGED
        if initial_voltage_v <= EMPTY_POSITION_VOLTAGE_V:
            return ChargeDiagnosis.POSSIBLY_EMPTY_POSITION
    return ChargeDiagnosis.NORMAL_CHARGE_BEHAVIOR


def classify_discharge_behavior(*, initial_voltage_v, final_voltage_v, avg_current_a,
                                 duration_s, commanded_current_a, battery_cfg) -> str:
    """
    Returns one of DischargeDiagnosis's two values. Same data-reuse
    contract as classify_charge_behavior(). `final_voltage_v` at/below the
    battery's own voltage_min_v combined with near-zero current and a
    short duration is what an empty position sinking into SMU compliance
    looks like -- see module docstring.
    """
    if initial_voltage_v is None:
        return DischargeDiagnosis.NORMAL_DISCHARGE_BEHAVIOR

    small_current = avg_current_a < commanded_current_a * MIN_MEANINGFUL_CURRENT_FRACTION
    short_duration = duration_s <= SHORT_DURATION_S
    collapsed = final_voltage_v is not None and final_voltage_v <= battery_cfg["voltage_min_v"]
    if small_current and short_duration and collapsed:
        return DischargeDiagnosis.POSSIBLY_EMPTY_POSITION
    return DischargeDiagnosis.NORMAL_DISCHARGE_BEHAVIOR


class BatteryPresence:
    """
    Battery-presence classification for a single pre-test DMM voltage
    reading -- see classify_battery_presence() and
    test_control/battery_presence_precheck.py. Plain string constants,
    same convention as hardware/temperature.py::NTCPresence.
    """
    PRESENT  = "present"
    ABSENT   = "absent"
    REVERSED = "reversed"


def classify_battery_presence(voltage_v: float) -> str:
    """
    Classify a single pre-test (SMU output still disabled) DMM voltage
    reading as PRESENT (a real, plausible cell voltage), ABSENT (reads
    near 0 V -- an open/disconnected position, no battery there), or
    REVERSED (reads sharply negative -- a real cell is present, but
    installed backwards).

    Reuses two thresholds ALREADY established and reasoned about
    elsewhere in this codebase, rather than introducing a new number:
    Settings.REVERSE_POLARITY_VOLTAGE_THRESHOLD_V (-0.5 V -- the existing
    reverse-polarity boundary, see
    BatteryOperationSequence._check_battery_polarity()) and
    EMPTY_POSITION_VOLTAGE_V (0.5 V, above -- "at/below this ABSOLUTE
    voltage looks like an open/disconnected circuit, not a real cell").
    Together they partition the number line into three zones:

        <= -0.5 V           -> REVERSED
        (-0.5 V, 0.5 V]     -> ABSENT
        > 0.5 V             -> PRESENT

    Deliberately does NOT gate or raise -- mirrors
    hardware/temperature.py::classify_ntc_presence()'s relationship to its
    caller: this function only classifies, the caller
    (test_control/battery_presence_precheck.py) decides what to do with
    the result. A REVERSED classification is intentionally NOT treated as
    "missing" by that caller -- a reversed cell is a physically present
    battery, just backwards; the existing, unchanged
    BatteryOperationSequence._check_battery_polarity() (raising
    ReversePolarityError once the real sequence starts) remains the sole
    safety-relevant handling for that condition, never duplicated here.
    """
    if voltage_v <= Settings.REVERSE_POLARITY_VOLTAGE_THRESHOLD_V:
        return BatteryPresence.REVERSED
    if voltage_v <= EMPTY_POSITION_VOLTAGE_V:
        return BatteryPresence.ABSENT
    return BatteryPresence.PRESENT
