"""
Global configuration for NIPXI Battery Test System.
Edit this file before running tests.
"""

import os


class Settings:
    # -------------------------------------------------------------------------
    # Project
    # -------------------------------------------------------------------------
    PROJECT_NAME = "NIPXI Battery Test System"
    VERSION = "0.1.0"

    # -------------------------------------------------------------------------
    # System mode -- see config/system_mode.py for the full policy each mode
    # implies (hardware startup strictness, database location, recovery,
    # simulated devices) and docs/architecture.md "System Modes".
    #   DEVELOPMENT -- laptop/software work, hardware optional, missing
    #                  devices warn and startup continues.
    #   VALIDATION  -- hardware integration/driver validation, missing
    #                  devices reported as failures but framework still launches.
    #   PRODUCTION  -- real battery cycling, any missing device aborts startup.
    # -------------------------------------------------------------------------
    SYSTEM_MODE = "DEVELOPMENT"

    # Recovery hook -- NOT implemented yet (see docs/DATABASE_ROADMAP.md).
    # None = use the active mode's default (see config/system_mode.py
    # MODE_POLICIES); set True/False to override regardless of mode (e.g.
    # to try VALIDATION with recovery on, since VALIDATION's default is
    # "optionally enabled").
    RECOVERY_ENABLED_OVERRIDE = None

    # -------------------------------------------------------------------------
    # Battery positions -- the system is organized around battery positions
    # grouped into blocks of GROUP_SIZE (see config/devices.py::BATTERY_GROUPS
    # -- each group of GROUP_SIZE positions corresponds to a distinct relay
    # matrix; Group B1/positions 1-8 is the only group with real hardware
    # today). Renamed from NUM_CHANNELS -- "channel" reads as a generic DAQ/
    # electrical term; this constant has always meant "how many battery
    # positions exist". This is the legacy Proto Test Execution numbering
    # (utils/validators.py's range validation) -- separate from, and not
    # touched by, config/devices.py::BATTERY_GROUPS' per-group "positions".
    # -------------------------------------------------------------------------
    GROUP_SIZE = 8
    BATTERY_POSITIONS = 8
    ACTIVE_CHANNELS = list(range(1, 9))  # [1, 2, 3, 4, 5, 6, 7, 8]
    # NOTE: ACTIVE_CHANNELS is the list every real sequence (BatteryTestSequence,
    # ProtoTestSequence, TestExecutor) actually iterates -- a "positions"
    # rename for this one is recommended but deliberately deferred to avoid
    # touching test_control/ files outside this change's scope (see
    # docs/architecture.md "Battery Group / Position Architecture").

    # -------------------------------------------------------------------------
    # Battery limits (Li-ion, per BLOSS Hub spec) -- these are the GLOBAL
    # fallback limits used only when no battery_cfg (config/devices.py
    # BATTERY_CONFIGS[...]) is supplied. Real charge/discharge work always
    # selects an explicit battery type, so BATTERY_CONFIGS[...]["voltage_min_v"]
    # is what actually governs in practice -- see docs/architecture.md
    # "Discharge Cutoff Policy" (Section 30).
    # -------------------------------------------------------------------------
    BAT_VOLTAGE_MAX = 4.7       # V  - absolute upper limit (safety ceiling)
    BAT_VOLTAGE_MIN = 3.5       # V  - absolute lower limit (safety floor -- SafetyMonitor
                                 #      enforces this independently of any cycle-level
                                 #      discharge target; the system must never discharge
                                 #      below this value, or the per-battery equivalent)
    BAT_CURRENT_MAX = 1.0       # A  - max charge/discharge current
    BAT_TEMP_MAX_C  = 45.0      # °C - safety cutoff temperature
    BAT_TEMP_MIN_C  = 20.0      # °C - minimum operating temperature

    # -------------------------------------------------------------------------
    # Charge parameters (CC-CV)
    # -------------------------------------------------------------------------
    CHARGE_CURRENT_A   = 0.5    # A  - constant current during CC phase
    CHARGE_VOLTAGE_V   = 4.2    # V  - target CV voltage
    # end-of-charge current threshold (CV taper) -- confirmed 2026-08-24
    # against the real HUB cell datasheet's stated "Termination threshold:
    # 150 mA" (see docs/architecture.md "HUB Battery Configuration Update:
    # Real Datasheet Values"). Was 0.05 A (50 mA), an unconfirmed
    # placeholder. This is a single GLOBAL constant with no per-
    # battery-type equivalent (see test_control/charge_cycle.py/
    # charge_sequence.py's own comments) -- HUB is the only real battery
    # type in active use today, so this value is effectively HUB's own
    # termination threshold applied globally. Revisit if/when a second
    # real battery type with a materially different termination spec goes
    # into active use. Raising this (0.05 -> 0.15 A) means EOC is now
    # detected at a higher tapering current than before -- charge
    # terminates slightly earlier/less fully than the old placeholder
    # would have allowed, matching the manufacturer's own specified
    # termination point rather than an arbitrary lower placeholder.
    CHARGE_CUTOFF_A    = 0.15   # A  - end-of-charge current threshold (CV taper)
    CHARGE_TIMEOUT_S   = 7200   # s  - max charge time (2 h)

    # -------------------------------------------------------------------------
    # Discharge parameters (CC)
    # -------------------------------------------------------------------------
    DISCHARGE_CURRENT_A = 0.5   # A  - constant discharge current
    DISCHARGE_CUTOFF_V  = 3.0   # V  - discharge TARGET (cycle objective -- where a
                                 #      discharge cycle intends to stop). This is a
                                 #      global fallback default, NOT the safety floor --
                                 #      it is deliberately allowed to differ from
                                 #      BAT_VOLTAGE_MIN above. DischargeCycle clamps the
                                 #      effective cutoff to never go below whichever
                                 #      voltage floor is active (BAT_VOLTAGE_MIN, or a
                                 #      selected battery's own voltage_min_v), so the
                                 #      safety limit always takes priority regardless of
                                 #      this target. See docs/architecture.md Section 30
                                 #      "Discharge Cutoff Policy" -- this value and
                                 #      BAT_VOLTAGE_MIN are NOT in conflict; they answer
                                 #      two different questions (objective vs. floor).
    DISCHARGE_TIMEOUT_S = 7200  # s  - max discharge time

    # -------------------------------------------------------------------------
    # Cycle parameters (test_control/cycle_sequence.py::CycleSequence -- see
    # docs/architecture.md Section 67 "CycleSequence -- Final Design")
    # -------------------------------------------------------------------------
    # Rest period between the charge phase and the discharge phase within one
    # Cycle Battery repetition -- passive dwell only (SMU already off, relay
    # already open by the time this runs; see CycleSequence.run()). A
    # per-group override is supported via test_setpoints["cycle_rest_s"];
    # this is only the fallback when a group doesn't set one.
    CYCLE_REST_S = 60.0  # s -- rest between charge and discharge phases

    # Hard ceiling for a per-group test_setpoints["charge_timeout_s"]/
    # ["discharge_timeout_s"] validation override (see docs/architecture.md
    # "Configurable Validation Timeout"). Deliberately NOT a way to disable
    # the timeout entirely -- "unknown state = unsafe state" means a charge/
    # discharge must always have *some* finite wall-clock ceiling, even
    # during validation. 86400s (24h) is generously larger than any
    # legitimate single Charge/Discharge Battery validation session should
    # need while still guaranteeing a run can never be effectively
    # unattended-indefinite. utils/validators.py::validate_group_test_config()
    # rejects any override above this value, and rejects the override
    # existing at all while Settings.SYSTEM_MODE is PRODUCTION.
    MAX_TIMEOUT_OVERRIDE_S = 86400  # s - 24h hard ceiling, validation only

    # -------------------------------------------------------------------------
    # Stabilization and sampling
    # -------------------------------------------------------------------------
    # Wait after SMU output enable before measuring (electrical/output
    # stabilization only). This is NOT relay-related -- the mandatory
    # relay contact settling/dead-time delay is RELAY_SETTLE_TIME_S below,
    # enforced centrally by RelayBase.open()/close() (hardware/relay.py),
    # never here.
    STABILIZATION_S     = 5.0   # s  - wait after SMU output enable before measuring
    SAMPLE_RATE_HZ      = 1.0   # Hz - DAQ acquisition rate during cycle

    # -------------------------------------------------------------------------
    # Safety
    # -------------------------------------------------------------------------
    ZERO_CURRENT_THRESHOLD_A = 0.01  # A - "current is zero" threshold for relay safety

    # hardware/smu.py::SMU.emergency_output_off() bounded retry (see
    # docs/architecture.md "Shutdown Safety -- Bounded Retry + Distinct
    # Failure Modes"). Only affects the FAILURE path -- a first-attempt
    # success returns immediately with zero added latency. The delay
    # exists purely to give a transient verification-communication
    # failure a real chance to resolve before the caller
    # (hardware_manager.py::disconnect_all(), safety_monitor.py) gives up.
    EMERGENCY_OUTPUT_OFF_MAX_ATTEMPTS = 3     # attempts before giving up
    EMERGENCY_OUTPUT_OFF_RETRY_DELAY_S = 0.3  # s - delay between retry attempts

    # ChargeSequence/DischargeSequence sampling-loop DMM-read bounded
    # tolerance (see docs/architecture.md "Standardized Hardware Event
    # Logging" -- DMM_MEASUREMENT_FAILED/_RECOVERED). DMM is the
    # authoritative voltage source for EOC/EOD and safety.check(), so a
    # read failure cannot be tolerated indefinitely (unlike an NTC read
    # failure, which only degrades temperature monitoring) -- but a single
    # transient comms glitch should get a bounded chance to resolve before
    # the run aborts, mirroring EMERGENCY_OUTPUT_OFF_MAX_ATTEMPTS's own
    # bounded-retry philosophy above. Consecutive (not cumulative) count:
    # any successful read resets it to zero and logs
    # DMM_MEASUREMENT_RECOVERED if it had previously failed at least once.
    DMM_MEASUREMENT_MAX_CONSECUTIVE_FAILURES = 3

    # -------------------------------------------------------------------------
    # Hardware Audit Trail (see docs/architecture.md "Hardware Audit Trail" --
    # hardware/audit_proxy.py + data/raw_hardware_log.py). Fully additive and
    # independent of event_log/measurements/EventType -- disabling this never
    # changes ChargeSequence/DischargeSequence/MonitorBatterySequence/
    # SafetyMonitor behavior or existing event logging in any way.
    # -------------------------------------------------------------------------

    # Master switch. False = HardwareManager returns every device
    # completely unwrapped (zero overhead, not just "logs nothing").
    ENABLE_RAW_HARDWARE_LOGGING = True

    # Whether "repetitive measurement read" methods (SMU.measure(),
    # DMM.measure_dc_voltage(), DAQ.read_channel() -- see
    # hardware/audit_proxy.py::MEASUREMENT_METHOD_NAMES) are logged at
    # all. State-changing commands (output enable/disable, setpoint
    # commands, relay/matrix open/close, connect/disconnect) and every
    # FAILURE are always logged regardless of this flag -- it only gates
    # successful, routine measurement polling, which is by far the
    # highest-frequency hardware traffic in any sampling loop. Left True
    # (not disabled) as the production default -- the storage-growth
    # review's finding was that FULL-RATE measurement logging is the
    # actual cost driver, not that measurement logging itself should be
    # dropped; RAW_HW_MEASUREMENT_SAMPLE_RATE below is the correct lever.
    RAW_HW_LOG_MEASUREMENTS = True

    # When RAW_HW_LOG_MEASUREMENTS is True, log only 1 in every N
    # successful measurement-method calls (always including the very
    # first). 1 = log every call (no sampling). NEVER applies to
    # failures (always logged, every time, regardless of this value) or
    # to state-changing commands (always logged in full) -- only
    # successful measurement polling is sampled.
    #
    # Production default is 10, not 1 -- see docs/architecture.md
    # "Hardware Audit Trail: Storage Growth Review". At the confirmed
    # real sampling cadence (Settings.SAMPLE_RATE_HZ = 1.0, i.e. one
    # ChargeSequence/DischargeSequence sampling-loop iteration per
    # second, each iteration calling SMU.measure() + DMM.
    # measure_dc_voltage() + DAQ.read_channel() -- see
    # charge_sequence.py's sampling loop), a rate of 1 logs every one of
    # those three measurement calls every second for the full duration
    # of every run: ~86,400 measurement rows (~23 MB) for one position's
    # 8-hour B1 validation-timeout run alone, before Group -> ALL
    # multiplies that by every enabled position. A rate of 10 cuts that
    # dominant term by ~90% (one measurement row every ~10 seconds per
    # device instead of every second) while leaving command traceability
    # and failure traceability at 100% fidelity, unaffected -- both are
    # comparatively rare events (a handful of commands per run, and
    # failures are the exceptional case this audit trail exists to
    # catch), so neither is meaningfully improved by full-rate
    # measurement sampling and both are completely unaffected by this
    # value. This is the production-safe default; a shorter validation-
    # only run can still set this to 1 (or use DEVELOPMENT/VALIDATION
    # mode) for full-fidelity measurement capture when specifically
    # investigating a measurement-level issue.
    RAW_HW_MEASUREMENT_SAMPLE_RATE = 10

    # Pre-output-enable reverse-polarity sanity check (ChargeSequence/
    # DischargeSequence -- see docs/architecture.md "Reverse Polarity
    # Protection"). A correctly-connected, intact Li-ion cell never reads
    # negative. A small negative reading can still occur from ADC/DMM
    # offset noise on a near-zero (deeply discharged or disconnected)
    # cell, so the threshold is set safely below that noise floor rather
    # than at 0.0 V -- anything at or below it is treated as physically
    # implausible for a correctly-connected cell, not noise.
    REVERSE_POLARITY_VOLTAGE_THRESHOLD_V = -0.5  # V - at/below this -> ReversePolarityError

    # -------------------------------------------------------------------------
    # SMU configuration-readback verification (hardware/smu.py)
    #
    # These bound session.voltage_level/session.current_limit readback
    # against the commanded value after commit(). NI-DCPower echoes these
    # as stored IVI attribute properties (an IEEE-754 double round-tripped
    # through the driver), NOT a new ADC measurement -- there is no analog
    # conversion in this path. The only legitimate error sources are
    # floating-point round-trip and instrument coercion to its nearest
    # programmable step, both far smaller than any electrical accuracy
    # spec. These are deliberately tight (attribute round-trip tolerance,
    # NOT a measurement-accuracy/percent-of-range figure) -- a wider value
    # here would risk masking a real failure (wrong channel, stale
    # attribute, a command silently rejected).
    # -------------------------------------------------------------------------
    SMU_VOLTAGE_READBACK_TOLERANCE_V = 1e-4   # V
    SMU_CURRENT_READBACK_TOLERANCE_A = 1e-4   # A

    # -------------------------------------------------------------------------
    # Proto Test Execution (Milestone 2) -- infrastructure validation, no
    # battery connected. Reuses CHARGE_VOLTAGE_V/CHARGE_CURRENT_A/
    # BAT_VOLTAGE_MAX above as the bench source point/current-limit/voltage-
    # range (same constants SMU Functional Validation already sources from
    # -- no new duplicate voltage/current constant). ACTIVE_CHANNELS above
    # is reused as the relay sequence (relay numbering matches battery
    # channel numbering on the BLOSS Hub PCB).
    #
    # PROTO_TEST_SMU_NAME -- which config/devices.py::SMU_ASSIGNMENTS entry
    # Proto Test Execution sources from. Named explicitly rather than
    # falling back to next(iter(SMU_ASSIGNMENTS.items())) (the "whichever
    # SMU is listed first" default HardwareManager/run_main_test() use) --
    # that positional default always resolves to PRIMARY_SMU regardless of
    # which physical unit is actually wired up for this bench workflow, and
    # is shared with the real battery-test default, so changing it would
    # have changed main.py's behavior too. This setting is consulted ONLY
    # by test.py::run_proto_test_execution() -- HardwareManager's own
    # default and the real battery-test path are untouched.
    # -------------------------------------------------------------------------
    PROTO_TEST_SMU_NAME = "AUX_SMU_1"   # PXI-4130, Slot 7, channel "1"

    # TEMPORARY -- shortened for the first physical rack validation run.
    # Restore to 120.0 (~2 min) once the quick end-to-end check passes.
    PROTO_TEST_DWELL_S = 5   # s -- per-relay dwell time

    # -------------------------------------------------------------------------
    # Relay settling / dead-time -- SINGLE GLOBAL CONSTANT for every relay
    # switching operation in the entire application (Monitor Battery,
    # Monitor Battery Scan, Proto Test, ChargeSequence, DischargeSequence,
    # relay validation/hardware-validation workflows, and any future
    # caller). Enforced centrally in RelayBase.open()/close()
    # (hardware/relay.py) immediately after every relay action completes
    # and is verified -- callers never need (and must never add) their own
    # relay-settle sleep; do not hardcode a second timing value anywhere
    # else for this purpose. Must never be 0 -- a 0 s value would allow a
    # subsequent relay action before the previous one has mechanically
    # settled, which this constant exists specifically to prevent.
    # -------------------------------------------------------------------------
    RELAY_SETTLE_TIME_S    = 2.0  # s -- mandatory dead-time after every relay open()/close(), never 0
    MONITOR_SCAN_SAMPLES   = 3    # count -- DMM samples averaged per voltage reading

    # -------------------------------------------------------------------------
    # Relay Matrix Scan (test.py::_run_relay_matrix_scan(), "[2] Matrix Scan
    # (ON -> READ -> OFF, scoped by group)") -- how long each relay is held
    # ON, after it has been activated/read/verified and before it is turned
    # back OFF. This is a Matrix-Scan-ONLY dwell for real-rack hardware
    # inspection (observe activation, verify LEDs, verify physical routing/
    # measurements/wiring, confirm relay selection) -- it is NOT a relay
    # settling/dead-time value and is completely independent of
    # RELAY_SETTLE_TIME_S above, which still applies unchanged on top of
    # this dwell (via RelayBase.open()/close()). Do not reuse this constant
    # for any other workflow, and do not let RELAY_SETTLE_TIME_S be reused
    # for this purpose either -- the two concerns are deliberately separate.
    # -------------------------------------------------------------------------
    RELAY_MATRIX_SCAN_DWELL_S = 5.0  # s -- ON-state dwell, Matrix Scan only

    # How long a position's relay is held closed AFTER the initial settled
    # voltage/DAQ reading, to observe relay/measurement stability over time
    # before moving to the next position -- see the CLOSED-state monitoring
    # dwell in test_control/monitor_battery_scan_sequence.py. Sampled every
    # MONITOR_SCAN_DWELL_SAMPLE_INTERVAL_S via interruptible_sleep(), never a
    # blocking sleep() -- cancellation is checked every interval.
    MONITOR_SCAN_DWELL_TIME_S            = 30   # s -- total monitoring dwell per position
    MONITOR_SCAN_DWELL_SAMPLE_INTERVAL_S = 5    # s -- DMM/DAQ sample period during the dwell

    # -------------------------------------------------------------------------
    # PXI rack -- simulation mode only. VISA resource strings (slot numbers)
    # live in config/devices.py (SMU_ASSIGNMENTS / DAQ_CONFIG / DMM_CONFIG)
    # ONLY -- that is their single source of truth. They used to be
    # duplicated here as PXI_RESOURCE_DAQ/DMM/SMU1/SMU2; those were removed
    # because HardwareManager was reading them instead of config/devices.py,
    # silently diverging from it. Edit config/devices.py to change a slot.
    # -------------------------------------------------------------------------
    PXI_SIMULATE       = False          # True = NI VISA simulation mode (no hardware)

    # -------------------------------------------------------------------------
    # Numato Relay Matrix (Numato Lab 32 Channel Ethernet Relay Module) -- PRODUCTION
    # Single source of truth for relay count. config/devices.py
    # NUMATO_RELAY_MATRIX_CONFIG reads this value (never hardcode 32
    # elsewhere -- e.g. in RelayEthernetTest).
    # -------------------------------------------------------------------------
    RELAY_COUNT = 32

    # -------------------------------------------------------------------------
    # Relay matrix  (COM port - not NI, custom controller) -- diagnostic only,
    # NOT the production relay path (see RELAY_COUNT / NUMATO_RELAY_MATRIX_CONFIG above).
    # -------------------------------------------------------------------------
    RELAY_COM_PORT    = "COM3"          # Serial port of relay matrix controller
    RELAY_BAUD_RATE   = 9600
    RELAY_TIMEOUT_S   = 2.0
    RELAY_NUM_CHANNELS = 8

    # -------------------------------------------------------------------------
    # DAQ channels  (NI 6363 physical channel names)
    # -------------------------------------------------------------------------
    DAQ_VOLTAGE_CHANNELS = [f"Dev1/ai{i}" for i in range(8)]   # ai0..ai7
    DAQ_CURRENT_CHANNELS = [f"Dev1/ai{i}" for i in range(8, 16)]  # ai8..ai15
    DAQ_NTC_CHANNELS     = [f"Dev1/ai{i}" for i in range(16, 24)] # ai16..ai23

    # -------------------------------------------------------------------------
    # Logging
    # -------------------------------------------------------------------------
    LOG_LEVEL = "INFO"   # DEBUG | INFO | WARNING | ERROR
    LOG_FILE  = os.path.join("logs", "nipxi.log")

    # -------------------------------------------------------------------------
    # Data storage -- mode-separated (see docs/DATABASE_ROADMAP.md). Each
    # mode gets its own subdirectory so DEVELOPMENT experiments can never
    # collide with VALIDATION or PRODUCTION data.
    #
    # NOTE: the roadmap's illustrative paths were "data/dev/nipxi_dev.db"
    # etc., but this project already has a "data/" PACKAGE directory
    # (data/storage.py, data/logger.py, data/report.py) -- reusing "data/"
    # as the output root too would put runtime database files inside the
    # same directory as source code, which is confusing and risks an
    # accidental import-path collision. This uses the existing
    # "data_output/" root (already gitignored as generated output) with a
    # mode subdirectory instead: data_output/development|validation/production/.
    #
    # There is deliberately no single DATABASE_FILE constant anymore (see
    # docs/architecture.md "Telemetry / Index Database Split"): two
    # database files now live under DATA_DIR --
    #   - the permanent index database (run_summary/station_state/
    #     run_sequence), resolved by data/rotation.py::index_database_file()
    #   - the current month's telemetry database (measurements/event_log/
    #     raw_hardware_log), resolved by data/rotation.py::
    #     telemetry_database_file() -- "nipxi_<YYYY>_<MM>.db"
    # Both resolvers read DATA_DIR live (a plain attribute lookup, not
    # baked in at class-definition time like the old DATABASE_FILE was),
    # and both accept a settings-level override (a DATABASE_FILE/
    # INDEX_DATABASE_FILE attribute, if a caller sets one) for tests/
    # legacy single-file callers -- see that module's docstring.
    # -------------------------------------------------------------------------
    _MODE_DB_SUBDIR = {
        "DEVELOPMENT": "development",
        "VALIDATION":  "validation",
        "PRODUCTION":  "production",
    }

    DATA_DIR         = os.path.join("data_output", _MODE_DB_SUBDIR.get(SYSTEM_MODE, "development"))
    CSV_DIR          = os.path.join(DATA_DIR, "csv")
    REPORT_DIR       = os.path.join(DATA_DIR, "reports")
