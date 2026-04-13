"""
VeraPoint.ai — Turn Manager
Manages speaker turns, prevents audio overlap, and handles conversation flow.
"""

import time
import asyncio
import logging
from enum import Enum
from typing import Optional, Callable, Awaitable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class TurnState(str, Enum):
    """State machine for each call leg's turn."""
    IDLE = "idle"                    # Waiting for speech
    LISTENING = "listening"          # Speech detected, accumulating
    PROCESSING = "processing"       # STT complete, translating
    SPEAKING = "speaking"           # TTS audio being played to the other party
    COOLDOWN = "cooldown"           # Brief pause after speaking before next turn


@dataclass
class LegTurnState:
    """Turn state for a single call leg."""
    state: TurnState = TurnState.IDLE
    speech_started_at: float = 0.0
    speech_ended_at: float = 0.0
    last_tts_ended_at: float = 0.0
    is_speaking_tts: bool = False  # Is TTS audio currently being played TO this leg?


class TurnManager:
    """
    Manages turn-taking between two call legs.
    
    Key responsibilities:
    1. Detect when a speaker's turn starts and ends
    2. Prevent translated audio from playing while the other person is speaking
    3. Queue translations during overlap
    4. Manage cooldown periods to prevent echo/feedback
    """

    def __init__(
        self,
        silence_threshold_ms: int = 500,
        cooldown_ms: int = 200,
    ):
        """
        Args:
            silence_threshold_ms: Silence duration before treating speech as ended
            cooldown_ms: Minimum pause between TTS playback ending and allowing new input
        """
        self.silence_threshold_ms = silence_threshold_ms
        self.cooldown_ms = cooldown_ms

        self.leg_a = LegTurnState()
        self.leg_b = LegTurnState()

        # Queue for translations that arrived during overlap
        self._pending_a_to_b: list[str] = []  # Translations waiting to be played to B
        self._pending_b_to_a: list[str] = []  # Translations waiting to be played to A

    def get_leg_state(self, leg_id: str) -> LegTurnState:
        """Get turn state for a leg ("leg_a" or "leg_b")."""
        return self.leg_a if leg_id == "leg_a" else self.leg_b

    def get_other_leg_state(self, leg_id: str) -> LegTurnState:
        """Get the OTHER leg's turn state."""
        return self.leg_b if leg_id == "leg_a" else self.leg_a

    def on_speech_started(self, leg_id: str):
        """Called when Deepgram detects speech on a leg."""
        state = self.get_leg_state(leg_id)
        state.state = TurnState.LISTENING
        state.speech_started_at = time.time()
        logger.debug(f"Turn [{leg_id}]: Speech started")

    def on_speech_ended(self, leg_id: str):
        """Called when Deepgram detects end of speech on a leg."""
        state = self.get_leg_state(leg_id)
        state.speech_ended_at = time.time()
        state.state = TurnState.PROCESSING
        logger.debug(f"Turn [{leg_id}]: Speech ended → processing")

    def should_play_tts(self, target_leg_id: str) -> bool:
        """
        Check if it's safe to play TTS audio to the target leg.
        
        MVP MODE: Always allow TTS playback. The turn management was
        blocking ALL translations from being delivered because ambient
        noise kept legs in LISTENING state. We'll re-enable turn
        management once the basic bridge is proven working.
        """
        target_state = self.get_leg_state(target_leg_id)
        
        # Only block if we're already playing TTS to this leg (prevent overlap)
        if target_state.is_speaking_tts:
            logger.info(f"Turn [{target_leg_id}]: TTS blocked — already playing TTS")
            return False

        logger.info(f"Turn [{target_leg_id}]: TTS allowed (state={target_state.state.value})")
        return True

    def mark_tts_started(self, target_leg_id: str):
        """Mark that TTS is now playing to a leg."""
        state = self.get_leg_state(target_leg_id)
        state.is_speaking_tts = True
        state.state = TurnState.SPEAKING
        logger.debug(f"Turn [{target_leg_id}]: TTS playback started")

    def mark_tts_ended(self, target_leg_id: str):
        """Mark that TTS has finished playing to a leg."""
        state = self.get_leg_state(target_leg_id)
        state.is_speaking_tts = False
        state.last_tts_ended_at = time.time()
        state.state = TurnState.COOLDOWN
        logger.debug(f"Turn [{target_leg_id}]: TTS playback ended → cooldown")

        # After cooldown, transition to IDLE
        asyncio.get_event_loop().call_later(
            self.cooldown_ms / 1000,
            self._end_cooldown,
            target_leg_id,
        )

    def _end_cooldown(self, leg_id: str):
        """Transition from COOLDOWN to IDLE after delay."""
        state = self.get_leg_state(leg_id)
        if state.state == TurnState.COOLDOWN:
            state.state = TurnState.IDLE
            logger.debug(f"Turn [{leg_id}]: Cooldown ended → idle")

    def queue_translation(self, source_leg_id: str, text: str):
        """Queue a translation for later delivery if target leg is busy."""
        if source_leg_id == "leg_a":
            self._pending_a_to_b.append(text)
            logger.debug(f"Turn: Queued translation A→B: '{text[:50]}'")
        else:
            self._pending_b_to_a.append(text)
            logger.debug(f"Turn: Queued translation B→A: '{text[:50]}'")

    def get_pending_translation(self, target_leg_id: str) -> Optional[str]:
        """Get the next pending translation for a target leg, if any."""
        if target_leg_id == "leg_a" and self._pending_b_to_a:
            return self._pending_b_to_a.pop(0)
        elif target_leg_id == "leg_b" and self._pending_a_to_b:
            return self._pending_a_to_b.pop(0)
        return None

    def flush_pending(self, target_leg_id: str) -> list[str]:
        """Get and clear all pending translations for a target leg."""
        if target_leg_id == "leg_a":
            pending = self._pending_b_to_a[:]
            self._pending_b_to_a.clear()
            return pending
        else:
            pending = self._pending_a_to_b[:]
            self._pending_a_to_b.clear()
            return pending

    @property
    def status_summary(self) -> dict:
        """Return a summary of turn states for logging."""
        return {
            "leg_a": self.leg_a.state.value,
            "leg_b": self.leg_b.state.value,
            "pending_a_to_b": len(self._pending_a_to_b),
            "pending_b_to_a": len(self._pending_b_to_a),
        }
