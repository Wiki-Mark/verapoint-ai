"""
VeraPoint.ai — Twilio Media Stream Handler
WebSocket endpoint that receives/sends audio from/to Twilio Media Streams.
Bridges two call legs through the translation pipeline.
"""

import json
import asyncio
import logging
import math
import struct
from typing import Optional

from fastapi import WebSocket
from fastapi.websockets import WebSocketDisconnect

from app.config import config
from app.session_manager import session_manager, LegRole, CallStatus
from app.translation_pipeline import TranslationPipeline
from app.turn_manager import TurnManager
from app.audio_utils import base64_to_mulaw, mulaw_to_base64, chunk_audio, pcm16_to_mulaw

logger = logging.getLogger(__name__)

# ─── Active pipelines per session ──────────────────────────────────
# Maps session_id → { "pipeline_a": TranslationPipeline, "pipeline_b": TranslationPipeline }
_active_pipelines: dict[str, dict] = {}

# Maps session_id → { "leg_a": WebSocket, "leg_b": WebSocket }
_active_websockets: dict[str, dict[str, WebSocket]] = {}


def _generate_beep_mulaw(freq_hz: int = 440, duration_ms: int = 500, volume: float = 0.8) -> bytes:
    """Generate a sine wave beep tone as raw µ-law bytes for Twilio."""
    sample_rate = 8000
    num_samples = int(sample_rate * duration_ms / 1000)
    # Generate PCM16 sine wave
    pcm_samples = []
    for i in range(num_samples):
        t = i / sample_rate
        sample = int(volume * 32767 * math.sin(2 * math.pi * freq_hz * t))
        sample = max(-32768, min(32767, sample))
        pcm_samples.append(sample)
    pcm_data = struct.pack(f"<{len(pcm_samples)}h", *pcm_samples)
    # Convert to µ-law
    mulaw_data = bytearray()
    for s in pcm_samples:
        mulaw_data.append(pcm16_to_mulaw(s))
    return bytes(mulaw_data)


async def _inject_audio_to_leg(target_leg_id: str, mulaw_data: bytes, session_id: str):
    """
    Inject translated audio into a Twilio Media Stream WebSocket.
    This is the callback used by TranslationPipeline.on_audio_ready.
    """
    ws_map = _active_websockets.get(session_id, {})
    ws = ws_map.get(target_leg_id)
    if not ws:
        logger.warning(f"No WebSocket for {target_leg_id} in session {session_id}")
        return

    session = session_manager.get_session(session_id)
    if not session:
        return

    leg = session.get_leg(
        LegRole.LEG_A if target_leg_id == "leg_a" else LegRole.LEG_B
    )
    if not leg or not leg.stream_sid:
        logger.warning(f"No stream SID for {target_leg_id}")
        return

    # Split audio into Twilio-sized chunks (640 bytes = 80ms at 8kHz µ-law)
    chunks = chunk_audio(mulaw_data, chunk_size=640)

    for chunk in chunks:
        media_message = {
            "event": "media",
            "streamSid": leg.stream_sid,
            "media": {
                "payload": mulaw_to_base64(chunk)
            }
        }
        try:
            await ws.send_text(json.dumps(media_message))
        except Exception as e:
            logger.error(f"Error sending audio to {target_leg_id}: {e}")
            return


async def handle_media_stream(
    websocket: WebSocket,
    session_id: str,
    leg: str,
):
    """
    Handle a Twilio Media Stream WebSocket connection for one call leg.
    
    This is called for each leg of the call:
      - /media-stream/{session_id}/leg_a  (source language speaker)
      - /media-stream/{session_id}/leg_b  (target language speaker)
    """
    await websocket.accept()
    logger.info(f"Media stream connected: session={session_id}, leg={leg}")

    session = session_manager.get_session(session_id)
    if not session:
        logger.error(f"Session {session_id} not found")
        await websocket.close()
        return

    leg_role = LegRole.LEG_A if leg == "leg_a" else LegRole.LEG_B
    call_leg = session.get_leg(leg_role)
    if not call_leg:
        logger.error(f"Leg {leg} not found in session {session_id}")
        await websocket.close()
        return

    # Register the WebSocket
    if session_id not in _active_websockets:
        _active_websockets[session_id] = {}
    _active_websockets[session_id][leg] = websocket

    # Initialize pipelines when both legs are connected
    stream_sid = None

    try:
        async for message in websocket.iter_text():
            data = json.loads(message)
            event = data.get("event")

            if event == "connected":
                logger.info(f"[{session_id}/{leg}] Twilio stream: connected")

            elif event == "start":
                stream_sid = data["start"]["streamSid"]
                call_leg.mark_connected(stream_sid=stream_sid)
                session_manager.register_stream_sid(stream_sid, session_id, leg_role)

                logger.info(f"[{session_id}/{leg}] Stream started: {stream_sid}")

                # ─── DIAGNOSTIC: Send a beep to prove bidirectional audio works ───
                beep_data = _generate_beep_mulaw(freq_hz=440, duration_ms=500)
                beep_chunks = chunk_audio(beep_data, chunk_size=640)
                logger.info(f"[{session_id}/{leg}] Sending diagnostic beep: {len(beep_data)} bytes, {len(beep_chunks)} chunks")
                for bc in beep_chunks:
                    beep_msg = {
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {
                            "payload": mulaw_to_base64(bc)
                        }
                    }
                    await websocket.send_text(json.dumps(beep_msg))
                logger.info(f"[{session_id}/{leg}] Diagnostic beep sent ✓")
                # ─── END DIAGNOSTIC ───

                # Signal that this leg's stream is ready
                # (used by call_controller to sequence Leg B after Leg A)
                if leg == "leg_a" and hasattr(session, '_leg_a_stream_ready'):
                    session._leg_a_stream_ready.set()
                    logger.info(f"[{session_id}] Leg A stream ready — signalling call_controller")

                # Update session status
                session.update_status()

                # If both legs are now connected, start the translation pipelines
                if session.both_connected():
                    await _start_pipelines(session_id, session)

            elif event == "media":
                # Forward audio to the translation pipeline
                audio_payload = data["media"]["payload"]
                mulaw_bytes = base64_to_mulaw(audio_payload)

                pipeline_key = f"pipeline_{leg[-1]}"  # "pipeline_a" or "pipeline_b"
                pipelines = _active_pipelines.get(session_id, {})
                pipeline = pipelines.get(pipeline_key)

                if pipeline:
                    await pipeline.process_audio(mulaw_bytes)

            elif event == "mark":
                # Twilio mark event — can be used for playback synchronization
                mark_name = data.get("mark", {}).get("name", "")
                logger.debug(f"[{session_id}/{leg}] Mark: {mark_name}")

            elif event == "stop":
                logger.info(f"[{session_id}/{leg}] Stream stopped")
                break

    except WebSocketDisconnect:
        logger.info(f"[{session_id}/{leg}] WebSocket disconnected")
    except Exception as e:
        logger.error(f"[{session_id}/{leg}] Error: {e}", exc_info=True)
    finally:
        # Clean up
        await _cleanup_leg(session_id, leg)


async def _start_pipelines(session_id: str, session):
    """Start translation pipelines when both legs are connected."""
    if session_id in _active_pipelines:
        return  # Already started

    logger.info(f"[{session_id}] Both legs connected — starting translation pipelines")

    turn_manager = TurnManager(
        silence_threshold_ms=config.silence_threshold_ms,
        cooldown_ms=200,
    )

    # Pipeline A: source_lang (Leg A) → target_lang (Leg B)
    pipeline_a = TranslationPipeline(
        pipeline_id=f"{session.source_lang}→{session.target_lang}",
        source_lang=session.source_lang,
        target_lang=session.target_lang,
        turn_manager=turn_manager,
        source_leg_id="leg_a",
        target_leg_id="leg_b",
        on_audio_ready=lambda target, audio: _inject_audio_to_leg(target, audio, session_id),
    )

    # Pipeline B: target_lang (Leg B) → source_lang (Leg A)
    pipeline_b = TranslationPipeline(
        pipeline_id=f"{session.target_lang}→{session.source_lang}",
        source_lang=session.target_lang,
        target_lang=session.source_lang,
        turn_manager=turn_manager,
        source_leg_id="leg_b",
        target_leg_id="leg_a",
        on_audio_ready=lambda target, audio: _inject_audio_to_leg(target, audio, session_id),
    )

    _active_pipelines[session_id] = {
        "pipeline_a": pipeline_a,
        "pipeline_b": pipeline_b,
        "turn_manager": turn_manager,
    }

    # Start both pipelines
    await pipeline_a.start()
    await pipeline_b.start()

    logger.info(f"[{session_id}] Translation pipelines active — bridge is live!")


async def _cleanup_leg(session_id: str, leg: str):
    """Clean up when a call leg disconnects."""
    # Remove WebSocket
    if session_id in _active_websockets:
        _active_websockets[session_id].pop(leg, None)
        if not _active_websockets[session_id]:
            del _active_websockets[session_id]

    # If both legs are gone, stop pipelines
    remaining = _active_websockets.get(session_id, {})
    if not remaining:
        await _stop_pipelines(session_id)


async def _stop_pipelines(session_id: str):
    """Stop and clean up translation pipelines for a session."""
    pipelines = _active_pipelines.pop(session_id, {})
    if "pipeline_a" in pipelines:
        await pipelines["pipeline_a"].stop()
    if "pipeline_b" in pipelines:
        await pipelines["pipeline_b"].stop()

    session_manager.end_session(session_id)
    logger.info(f"[{session_id}] Pipelines stopped, session ended")
