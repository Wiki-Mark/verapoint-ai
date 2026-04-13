"""
VeraPoint.ai — Call Controller
Twilio webhook handling and outbound call initiation.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import Response

from twilio.rest import Client as TwilioClient

from app.config import config
from app.session_manager import session_manager, LegRole

logger = logging.getLogger(__name__)

router = APIRouter()

# Lazily initialized Twilio client
_twilio_client: Optional[TwilioClient] = None


def get_twilio_client() -> TwilioClient:
    """Get or create the Twilio REST client."""
    global _twilio_client
    if _twilio_client is None:
        _twilio_client = TwilioClient(
            config.twilio_account_sid,
            config.twilio_auth_token,
        )
    return _twilio_client


@router.post("/initiate-call")
async def initiate_call(request: Request):
    """
    Initiate a translated call between two parties.
    
    Request body:
    {
        "caller_number": "+447561161161",   # Person A (English speaker)
        "callee_number": "+44XXXXXXXXXX",   # Person B (Punjabi speaker)  
        "source_lang": "en",                # Optional, default "en"
        "target_lang": "pa"                 # Optional, default "pa"
    }
    
    Response:
    {
        "session_id": "abc12345",
        "status": "initiating",
        "leg_a_call_sid": "CAxxxx",
        "leg_b_call_sid": "CAxxxx"
    }
    """
    body = await request.json()
    caller_number = body.get("caller_number")
    callee_number = body.get("callee_number")
    source_lang = body.get("source_lang", "en")
    target_lang = body.get("target_lang", "pa")

    if not caller_number or not callee_number:
        return Response(
            content='{"error": "caller_number and callee_number are required"}',
            status_code=400,
            media_type="application/json",
        )

    # Create session
    session = session_manager.create_session(
        caller_number=caller_number,
        callee_number=callee_number,
        source_lang=source_lang,
        target_lang=target_lang,
    )

    # Build webhook URLs
    base_url = config.webhook_base_url
    leg_a_webhook = f"{base_url}/call-webhook/{session.session_id}/leg_a"
    leg_b_webhook = f"{base_url}/call-webhook/{session.session_id}/leg_b"

    twilio = get_twilio_client()

    try:
        # ─── SEQUENTIAL CALL INITIATION ───────────────────────────
        # Twilio trial accounts drop the second webhook when two
        # outbound calls are made concurrently. Fix: only start
        # Leg B AFTER Leg A's media stream is fully connected.
        # ──────────────────────────────────────────────────────────

        # Create an event that stream_handler will set when Leg A connects
        import asyncio
        session._leg_a_stream_ready = asyncio.Event()

        # Step 1: Initiate call to Person A (English speaker)
        call_a = twilio.calls.create(
            to=caller_number,
            from_=config.twilio_phone_number,
            url=leg_a_webhook,
            status_callback=f"{base_url}/call-status/{session.session_id}/leg_a",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
        )
        session.leg_a_call_sid = call_a.sid
        session_manager.register_call_sid(call_a.sid, session.session_id, LegRole.LEG_A)
        logger.info(f"Leg A call initiated: {call_a.sid} → {caller_number}")

        # Step 2: Wait for Leg A's stream to connect (up to 30s)
        logger.info("Waiting for Leg A stream to connect before initiating Leg B...")
        try:
            await asyncio.wait_for(session._leg_a_stream_ready.wait(), timeout=30.0)
            logger.info("Leg A stream connected — now initiating Leg B")
        except asyncio.TimeoutError:
            logger.warning("Leg A stream timeout after 30s — initiating Leg B anyway")

        # Step 3: Small extra delay to ensure Twilio has fully released the webhook slot
        await asyncio.sleep(1)

        # Step 4: Initiate call to Person B (Urdu speaker)
        call_b = twilio.calls.create(
            to=callee_number,
            from_=config.twilio_phone_number,
            url=leg_b_webhook,
            status_callback=f"{base_url}/call-status/{session.session_id}/leg_b",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
        )
        session.leg_b_call_sid = call_b.sid
        session_manager.register_call_sid(call_b.sid, session.session_id, LegRole.LEG_B)
        logger.info(f"Leg B call initiated: {call_b.sid} → {callee_number}")

    except Exception as e:
        logger.error(f"Failed to initiate calls: {e}")
        session_manager.end_session(session.session_id)
        return Response(
            content=f'{{"error": "Failed to initiate calls: {str(e)}"}}',
            status_code=500,
            media_type="application/json",
        )

    return {
        "session_id": session.session_id,
        "status": session.status.value,
        "leg_a_call_sid": call_a.sid,
        "leg_b_call_sid": call_b.sid,
    }


@router.api_route("/call-webhook/{session_id}/{leg}", methods=["GET", "POST"])
async def call_webhook(request: Request, session_id: str, leg: str):
    """
    Twilio webhook called when a call leg is answered.
    Returns TwiML that:
      1. Plays a brief greeting
      2. Opens a bidirectional Media Stream WebSocket
    """
    session = session_manager.get_session(session_id)
    if not session:
        twiml = '<?xml version="1.0" encoding="UTF-8"?><Response><Say>Session not found.</Say><Hangup/></Response>'
        return Response(content=twiml, media_type="text/xml")

    # Determine greeting based on language
    call_leg = session.leg_a if leg == "leg_a" else session.leg_b
    language = call_leg.language if call_leg else "en"

    if language == "ur":
        greeting = "VeraPoint Translation Bridge se jure hue hain. Baat karein."
        say_lang = "en-GB"  # Twilio doesn't have ur for <Say>
    elif language == "pa":
        greeting = "VeraPoint Translation Bridge nal jud rahe ho. Kripya bol'o."
        say_lang = "en-GB"
    else:
        greeting = "Connecting to VeraPoint Translation Bridge. Please speak."
        say_lang = "en-GB"

    # Build WebSocket URL for this leg's media stream
    ws_url = f"{config.ws_base_url}/media-stream/{session_id}/{leg}"

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice" language="{say_lang}">{greeting}</Say>
    <Pause length="1"/>
    <Connect>
        <Stream url="{ws_url}" />
    </Connect>
</Response>"""

    logger.info(f"Call webhook [{session_id}/{leg}]: streaming to {ws_url}")
    return Response(content=twiml, media_type="text/xml")


@router.api_route("/call-status/{session_id}/{leg}", methods=["GET", "POST"])
async def call_status_callback(request: Request, session_id: str, leg: str):
    """
    Twilio status callback for call lifecycle events.
    """
    form = await request.form()
    call_status = form.get("CallStatus", "unknown")
    call_sid = form.get("CallSid", "unknown")

    logger.info(f"Call status [{session_id}/{leg}]: {call_status} (SID: {call_sid})")

    if call_status in ("completed", "failed", "busy", "no-answer", "canceled"):
        session = session_manager.get_session(session_id)
        if session:
            leg_role = LegRole.LEG_A if leg == "leg_a" else LegRole.LEG_B
            call_leg = session.get_leg(leg_role)
            if call_leg:
                call_leg.connected = False

            # If both legs are disconnected, end session
            if not session.both_connected():
                # Try to hang up the other leg
                other_leg = "leg_b" if leg == "leg_a" else "leg_a"
                other_call_sid = (
                    session.leg_b_call_sid if leg == "leg_a" else session.leg_a_call_sid
                )
                if other_call_sid:
                    try:
                        twilio = get_twilio_client()
                        twilio.calls(other_call_sid).update(status="completed")
                        logger.info(f"Hung up other leg: {other_call_sid}")
                    except Exception as e:
                        logger.warning(f"Failed to hang up other leg: {e}")

                session_manager.end_session(session_id)

    return Response(content="", status_code=200)


@router.get("/sessions")
async def list_sessions():
    """List all active translation sessions."""
    active = session_manager.list_active()
    return {
        "active_sessions": len(active),
        "sessions": [
            {
                "session_id": s.session_id,
                "status": s.status.value,
                "languages": f"{s.source_lang} ↔ {s.target_lang}",
                "duration_s": round(s.duration_seconds, 1),
                "turns": s.turns_translated,
            }
            for s in active
        ],
    }


@router.get("/sessions/{session_id}")
async def get_session_detail(session_id: str):
    """Get details of a specific session."""
    session = session_manager.get_session(session_id)
    if not session:
        return Response(
            content='{"error": "Session not found"}',
            status_code=404,
            media_type="application/json",
        )

    return {
        "session_id": session.session_id,
        "status": session.status.value,
        "source_lang": session.source_lang,
        "target_lang": session.target_lang,
        "duration_s": round(session.duration_seconds, 1),
        "turns_translated": session.turns_translated,
        "leg_a": {
            "phone": session.leg_a.phone_number if session.leg_a else None,
            "connected": session.leg_a.connected if session.leg_a else False,
            "language": session.leg_a.language if session.leg_a else None,
        },
        "leg_b": {
            "phone": session.leg_b.phone_number if session.leg_b else None,
            "connected": session.leg_b.connected if session.leg_b else False,
            "language": session.leg_b.language if session.leg_b else None,
        },
    }
