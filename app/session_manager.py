"""
VeraPoint.ai — Session Manager
Tracks call sessions, maps call legs, and maintains translation state.
"""

import time
import uuid
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class CallStatus(str, Enum):
    INITIATING = "initiating"
    LEG_A_CONNECTED = "leg_a_connected"
    LEG_B_CONNECTED = "leg_b_connected"
    BOTH_CONNECTED = "both_connected"
    TRANSLATING = "translating"
    ENDED = "ended"
    ERROR = "error"


class LegRole(str, Enum):
    LEG_A = "leg_a"  # The initiator (source language speaker)
    LEG_B = "leg_b"  # The callee (target language speaker)


@dataclass
class CallLeg:
    """Represents one side of the call."""
    role: LegRole
    phone_number: str
    language: str  # ISO 639-1 code: "en" or "pa"
    call_sid: str = ""
    stream_sid: str = ""
    websocket: Optional[object] = None  # WebSocket connection reference
    connected: bool = False
    connected_at: float = 0.0

    def mark_connected(self, call_sid: str = "", stream_sid: str = ""):
        self.connected = True
        self.connected_at = time.time()
        if call_sid:
            self.call_sid = call_sid
        if stream_sid:
            self.stream_sid = stream_sid


@dataclass
class Session:
    """A translation session bridging two call legs."""
    session_id: str
    status: CallStatus = CallStatus.INITIATING
    created_at: float = field(default_factory=time.time)
    ended_at: float = 0.0

    # Language pair
    source_lang: str = "ur"  # Leg A's language (user speaks Urdu)
    target_lang: str = "en"  # Leg B's language (callee speaks English)

    # Call legs
    leg_a: Optional[CallLeg] = None
    leg_b: Optional[CallLeg] = None

    # Twilio call SIDs for management
    leg_a_call_sid: str = ""
    leg_b_call_sid: str = ""

    # Stats
    turns_translated: int = 0
    total_audio_seconds: float = 0.0

    def get_other_leg(self, leg_role: LegRole) -> Optional[CallLeg]:
        """Given a leg role, return the other leg."""
        if leg_role == LegRole.LEG_A:
            return self.leg_b
        return self.leg_a

    def get_leg(self, leg_role: LegRole) -> Optional[CallLeg]:
        """Get a specific leg."""
        if leg_role == LegRole.LEG_A:
            return self.leg_a
        return self.leg_b

    def both_connected(self) -> bool:
        """Check if both legs are connected."""
        return (
            self.leg_a is not None and self.leg_a.connected and
            self.leg_b is not None and self.leg_b.connected
        )

    def update_status(self):
        """Update session status based on leg connection state."""
        if self.status == CallStatus.ENDED:
            return

        a_connected = self.leg_a and self.leg_a.connected
        b_connected = self.leg_b and self.leg_b.connected

        if a_connected and b_connected:
            self.status = CallStatus.TRANSLATING
        elif a_connected:
            self.status = CallStatus.LEG_A_CONNECTED
        elif b_connected:
            self.status = CallStatus.LEG_B_CONNECTED
        else:
            self.status = CallStatus.INITIATING

    def end(self):
        """Mark session as ended."""
        self.status = CallStatus.ENDED
        self.ended_at = time.time()

    @property
    def duration_seconds(self) -> float:
        """Session duration in seconds."""
        end = self.ended_at if self.ended_at else time.time()
        return end - self.created_at


class SessionManager:
    """
    In-memory session store.
    Maps session IDs to Sessions and provides lookup by call SID / stream SID.
    """

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._call_sid_to_session: dict[str, tuple[str, LegRole]] = {}
        self._stream_sid_to_session: dict[str, tuple[str, LegRole]] = {}

    def create_session(
        self,
        caller_number: str,
        callee_number: str,
        source_lang: str = "ur",
        target_lang: str = "en",
    ) -> Session:
        """Create a new translation session."""
        session_id = str(uuid.uuid4())[:8]

        session = Session(
            session_id=session_id,
            source_lang=source_lang,
            target_lang=target_lang,
            leg_a=CallLeg(
                role=LegRole.LEG_A,
                phone_number=caller_number,
                language=source_lang,
            ),
            leg_b=CallLeg(
                role=LegRole.LEG_B,
                phone_number=callee_number,
                language=target_lang,
            ),
        )

        self._sessions[session_id] = session
        logger.info(
            f"Session {session_id} created: "
            f"{caller_number} ({source_lang}) ↔ {callee_number} ({target_lang})"
        )
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by its ID."""
        return self._sessions.get(session_id)

    def register_call_sid(self, call_sid: str, session_id: str, leg_role: LegRole):
        """Map a Twilio call SID to a session and leg."""
        self._call_sid_to_session[call_sid] = (session_id, leg_role)
        session = self._sessions.get(session_id)
        if session:
            if leg_role == LegRole.LEG_A:
                session.leg_a_call_sid = call_sid
            else:
                session.leg_b_call_sid = call_sid
        logger.info(f"Registered call SID {call_sid} → session {session_id} ({leg_role.value})")

    def register_stream_sid(self, stream_sid: str, session_id: str, leg_role: LegRole):
        """Map a Twilio stream SID to a session and leg."""
        self._stream_sid_to_session[stream_sid] = (session_id, leg_role)
        session = self._sessions.get(session_id)
        if session:
            leg = session.get_leg(leg_role)
            if leg:
                leg.stream_sid = stream_sid
        logger.info(f"Registered stream SID {stream_sid} → session {session_id} ({leg_role.value})")

    def lookup_by_call_sid(self, call_sid: str) -> Optional[tuple[Session, LegRole]]:
        """Find session and leg role by Twilio call SID."""
        mapping = self._call_sid_to_session.get(call_sid)
        if mapping:
            session_id, leg_role = mapping
            session = self._sessions.get(session_id)
            if session:
                return session, leg_role
        return None

    def lookup_by_stream_sid(self, stream_sid: str) -> Optional[tuple[Session, LegRole]]:
        """Find session and leg role by Twilio stream SID."""
        mapping = self._stream_sid_to_session.get(stream_sid)
        if mapping:
            session_id, leg_role = mapping
            session = self._sessions.get(session_id)
            if session:
                return session, leg_role
        return None

    def end_session(self, session_id: str):
        """End a session and clean up mappings."""
        session = self._sessions.get(session_id)
        if not session:
            return

        session.end()

        # Clean up lookup tables
        for call_sid, (sid, _) in list(self._call_sid_to_session.items()):
            if sid == session_id:
                del self._call_sid_to_session[call_sid]
        for stream_sid, (sid, _) in list(self._stream_sid_to_session.items()):
            if sid == session_id:
                del self._stream_sid_to_session[stream_sid]

        logger.info(
            f"Session {session_id} ended. Duration: {session.duration_seconds:.1f}s, "
            f"Turns: {session.turns_translated}"
        )

    def list_active(self) -> list[Session]:
        """List all active (non-ended) sessions."""
        return [s for s in self._sessions.values() if s.status != CallStatus.ENDED]

    @property
    def active_count(self) -> int:
        return len(self.list_active())


# Global session manager instance
session_manager = SessionManager()
