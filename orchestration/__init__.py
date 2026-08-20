"""
Future architecture groundwork -- pure, read-only configuration-analysis
layer only (see docs/architecture.md "Future Architecture: Configuration-
Driven Multi-Group Execution").

Nothing in this package is imported by test.py, main.py, or test_control/
-- it is not on the current execution path, touches no hardware, and does
not change any current runtime behavior. It exists so the future
orchestration work (worker execution, supervisor execution, a real
cross-process resource broker, main.py integration) can begin
immediately once Charge/Discharge/Cycle hardware validation is complete,
without having to design the configuration-analysis layer from scratch
at that point.

Modules:
    topology.py        -- which groups use which physical resource
    workers.py          -- partitions enabled groups by their owning SMU
    resource_graph.py   -- combines the two into shared-dependency/conflict data
    arbiter.py          -- claim/release interface + a trivial in-process
                           implementation (NOT a real concurrency primitive)
    execution_plan.py   -- turns workers + resource graph into an ordered,
                           dependency-aware/conflict-aware plan
    worker_lifecycle.py -- formal worker state model + transition rules
    supervisor.py       -- start()/stop()/status() contract + a reference
                           implementation exercising it synchronously,
                           with no hardware/test_control coupling
    reporting.py        -- human-readable rendering of all of the above;
                           the one module main.py's read-only
                           `--show-topology` flag is allowed to call

Explicitly NOT implemented here: worker execution, supervisor execution,
multi-processing/multi-threading, a real cross-process resource broker,
any main.py integration beyond read-only reporting, or any change to
ChargeSequence/DischargeSequence/BatteryOperationSequence/CycleSequence.
"""
