"""
In-memory stand-in for the python `niswitch` module (NI-SWITCH), for testing
hardware/relay_niswitch.py without the package, the NI driver or a card.

FakeCard is the hardware ground truth (which relays are physically closed,
stuck relays, injected errors). make_module(card) returns an object that
quacks like `niswitch` for the calls the driver uses: Session(...),
number_of_relays, get_relay_name, relay_control, get_relay_position,
wait_for_debounce, close, plus Error / RelayAction / RelayPosition.

Install it with:  mock.patch.dict(sys.modules, {"niswitch": make_module(card)})
"""

import enum
import types


class FakeError(Exception):
    """Stands in for niswitch.Error."""


class RelayAction(enum.Enum):
    OPEN = 20
    CLOSE = 21


class RelayPosition(enum.Enum):
    OPEN = 10
    CLOSED = 11


class FakeCard:
    def __init__(self, count=100):
        self.count = count
        self.closed = set()            # 0-based relay indexes physically closed
        self.stuck_closed = set()      # ignore OPEN
        self.stuck_open = set()        # ignore CLOSE
        self.fail_on = {}              # method name -> exception to raise
        self.fail_close_control = None # exception raised only for CLOSE actions
        self.session_args = None
        self.opened_sessions = 0
        self.closed_sessions = 0
        self.debounce_calls = 0
        self.position_reads = 0
        self.control_calls = []        # (relay_name, action)

    def physical_closed_channels(self):
        return {i + 1 for i in self.closed}

    def inject_stuck_closed(self, channel):
        self.stuck_closed.add(channel - 1)
        self.closed.add(channel - 1)

    def inject_stuck_open(self, channel):
        self.stuck_open.add(channel - 1)
        self.closed.discard(channel - 1)


def make_module(card):
    def maybe_fail(method):
        if method in card.fail_on:
            raise card.fail_on[method]

    class Session:
        def __init__(self, resource_name, topology="Configured Topology", simulate=False, reset_device=False):
            maybe_fail("Session")
            card.session_args = {"resource_name": resource_name, "topology": topology,
                                 "simulate": simulate, "reset_device": reset_device}
            card.opened_sessions += 1

        @property
        def number_of_relays(self):
            return card.count

        def get_relay_name(self, index):
            return f"ch{index - 1}"

        def relay_control(self, relay_name, relay_action):
            maybe_fail("relay_control")
            assert isinstance(relay_action, RelayAction)
            if relay_action is RelayAction.CLOSE and card.fail_close_control is not None:
                raise card.fail_close_control
            card.control_calls.append((relay_name, relay_action))
            index = int(relay_name[2:])
            if relay_action is RelayAction.CLOSE and index not in card.stuck_open:
                card.closed.add(index)
            elif relay_action is RelayAction.OPEN and index not in card.stuck_closed:
                card.closed.discard(index)

        def get_relay_position(self, relay_name):
            maybe_fail("get_relay_position")
            card.position_reads += 1
            return RelayPosition.CLOSED if int(relay_name[2:]) in card.closed else RelayPosition.OPEN

        def wait_for_debounce(self, maximum_time_ms=5000):
            maybe_fail("wait_for_debounce")
            card.debounce_calls += 1

        def close(self):
            card.closed_sessions += 1

    return types.SimpleNamespace(Error=FakeError, RelayAction=RelayAction,
                                 RelayPosition=RelayPosition, Session=Session)
