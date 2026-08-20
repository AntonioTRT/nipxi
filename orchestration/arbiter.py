"""
Shared-Resource Arbitration -- interface only, plus a trivial in-process
implementation (see docs/architecture.md "Future Architecture:
Shared-Resource Arbitration Strategy"). NOT wired into any real hardware
or runtime path -- nothing in test.py/main.py/test_control/ constructs or
calls an Arbiter today.

This module exists so the CALLING SHAPE (claim a resource before touching
it, release it when done) can be designed and unit-tested now, with a
real cross-process broker implementation swapped in later without
changing any caller -- worker_runtime.py (not yet built; explicitly out
of this task's approved scope) would depend only on the Arbiter interface
below, never on which implementation is active.

Explicitly NOT implemented here (approved scope boundary): worker
execution, supervisor execution, multi-processing/multi-threading, a real
cross-process broker. InProcessArbiter below is correct ONLY because
there is exactly one worker in the current execution path today -- it is
a placeholder that proves the interface shape, not a concurrency
primitive, and must not be mistaken for one.
"""

from __future__ import annotations

from dataclasses import dataclass


class ResourceBusyError(Exception):
    """
    Raised by Arbiter.claim() when the requested resource is already
    claimed by a DIFFERENT owner. Never raised for a repeat claim by the
    SAME owner (idempotent re-claim) -- the same "same caller, same
    request, no-op" convention already used elsewhere in this codebase
    (e.g. utils/cancellation.py::CancellationToken.request_cancel()).
    """


@dataclass(frozen=True)
class ClaimHandle:
    """
    Opaque token returned by Arbiter.claim() -- callers pass this back to
    release(). Carries resource_name/owner only for logging/debugging;
    callers must not infer anything else from its fields, and must not
    construct one directly.
    """
    resource_name: str
    owner: str


class Arbiter:
    """
    Abstract claim/release interface for a shared physical resource (a
    relay-matrix, DMM, or DAQ nickname from config/devices.py). A real
    implementation (a future cross-process broker) must preserve this
    exact contract so a future worker_runtime.py can be written against
    it without caring which implementation is active underneath.
    """

    def claim(self, resource_name: str, owner: str) -> ClaimHandle:
        """
        Claim exclusive use of `resource_name` on behalf of `owner`.
        Raises ResourceBusyError if a DIFFERENT owner currently holds it.
        Idempotent for the same owner (re-claiming your own already-held
        resource is a no-op, not an error).
        """
        raise NotImplementedError

    def release(self, handle: ClaimHandle) -> None:
        """
        Release a previously-claimed resource. Releasing a resource this
        owner does not actually hold must be a silent no-op, never an
        error -- callers' cleanup paths must be safe to call unconditionally
        (the same "never let cleanup itself become a new failure mode"
        principle already followed throughout hardware/*.py's shutdown
        methods).
        """
        raise NotImplementedError


class InProcessArbiter(Arbiter):
    """
    Trivial single-process implementation backed by a plain dict. Correct
    today because there is exactly one worker in the current execution
    path -- this class exists to let the Arbiter INTERFACE be exercised
    and unit-tested now, not to solve real concurrent contention.

    A real broker -- coordinating claims ACROSS separate OS processes,
    per the process-per-worker recommendation in docs/architecture.md --
    must replace this before any second concurrent worker is ever allowed
    to run against real hardware. See docs/architecture.md "What must
    wait until hardware validation is finished."
    """

    def __init__(self):
        self._claims: dict = {}  # resource_name -> owner

    def claim(self, resource_name: str, owner: str) -> ClaimHandle:
        current_owner = self._claims.get(resource_name)
        if current_owner is not None and current_owner != owner:
            raise ResourceBusyError(
                f"resource {resource_name!r} is already claimed by {current_owner!r} "
                f"(requested by {owner!r})"
            )
        self._claims[resource_name] = owner
        return ClaimHandle(resource_name=resource_name, owner=owner)

    def release(self, handle: ClaimHandle) -> None:
        current_owner = self._claims.get(handle.resource_name)
        if current_owner == handle.owner:
            del self._claims[handle.resource_name]

    def is_claimed(self, resource_name: str) -> bool:
        return resource_name in self._claims
