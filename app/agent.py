"""Call-center agent status, worked out from the PBX's agent lamp.

No Qt or PJSIP here. FusionPBX lights a BLF lamp on "agent+<id>" while the
agent is Available, and turns it off for both Logged Out and On Break. The
lamp alone can't tell those two apart, so AgentTracker remembers whether we
just asked for a break.
"""

from enum import Enum

from app.sip.blf import BlfState


class AgentState(Enum):
    UNKNOWN = "Unknown"         # no lamp report yet
    AVAILABLE = "Available"
    ON_BREAK = "On Break"
    LOGGED_OUT = "Logged Out"


class AgentAction(Enum):
    """What a button press dials: the login code or the break code."""
    LOGIN = "login"             # *22: Logged Out/On Break -> Available, Available -> Logged Out
    BREAK = "break"             # *24: Available -> On Break


class AgentTracker:
    def __init__(self):
        self.state = AgentState.UNKNOWN
        self._break_requested = False

    def requested(self, action: AgentAction):
        """We dialed a code; remember a break so a dark lamp means On Break."""
        self._break_requested = action is AgentAction.BREAK

    def on_lamp(self, lamp: BlfState) -> AgentState:
        if lamp is BlfState.UNKNOWN:
            self.state = AgentState.UNKNOWN
        elif lamp in (BlfState.BUSY, BlfState.RINGING):   # lit
            self.state = AgentState.AVAILABLE
            self._break_requested = False
        else:                                             # dark
            self.state = AgentState.ON_BREAK if self._break_requested else AgentState.LOGGED_OUT
        return self.state


def actions_for(state: AgentState) -> list[tuple[str, AgentAction]]:
    """(button label, action) for the agent box, in display order."""
    if state is AgentState.AVAILABLE:
        return [("Break", AgentAction.BREAK), ("Leave queue", AgentAction.LOGIN)]
    if state is AgentState.ON_BREAK:
        return [("Back", AgentAction.LOGIN)]
    if state is AgentState.UNKNOWN:
        # No lamp report yet (the PBX's blf_subscribe.lua agent service isn't
        # running), and *22 is a toggle: we can't promise which way it goes.
        return [("Join / leave queue", AgentAction.LOGIN)]
    return [("Join queue", AgentAction.LOGIN)]
