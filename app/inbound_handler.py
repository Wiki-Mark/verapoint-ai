"""
VeraPoint.ai — Inbound Call Handler (Tier 1: Dial-In)

When someone dials the VeraPoint number (+447777400123), this handler:
1. Greets them and asks what language they speak
2. Asks for the phone number they want to call
3. Creates a translation session
4. Keeps the caller on the line (Leg A) and dials the target (Leg B)
5. Both legs are connected through the translation bridge

No app required. Works from any phone.

IVR audio is pre-generated via ElevenLabs (British female voice,
"Alice" — clear, engaging educator). Served as static MP3 files
via <Play> instead of Twilio's robotic <Say>.
"""

import asyncio
import logging
import re

from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import Response

from app.config import config
from app.session_manager import session_manager, LegRole

logger = logging.getLogger(__name__)

router = APIRouter()

# ─── Language Menu Config ──────────────────────────────────────────
# Menu mapping matches the IVR audio:
# "Welcome to VeraPoint. For English press 1. اردو کے لیے دو دبائیں..."
LANGUAGE_OPTIONS = {
    "1": {"code": "en", "name": "English",  "audio": "english_confirm"},
    "2": {"code": "hi", "name": "Hindi",    "audio": "hindi_confirm"},
    "3": {"code": "ur", "name": "Urdu",     "audio": "urdu_confirm"},
    "4": {"code": "pa", "name": "Punjabi",  "audio": "punjabi_confirm"},
    "5": {"code": "ar", "name": "Arabic",   "audio": "arabic_confirm"},
    "6": {"code": "ro", "name": "Romanian", "audio": "romanian_confirm"},
}

# Callee greeting messages — short, in their language, no menu
CALLEE_GREETINGS = {
    "en": "You have a translated call via VeraPoint. Please speak naturally.",
    "hi": "VeraPoint ke zariye ek translated call aa rahi hai. Kripya baat karein.",
    "ur": "VeraPoint ke zariye ek translated call aa rahi hai. Baat karein.",
    "pa": "VeraPoint raaheen ik translated call aa rahi hai. Kripya gall karo.",
    "ar": "Ladayka mukalama mutarjama abra VeraPoint. Tafaddal takallam.",
    "ro": "Aveți un apel tradus prin VeraPoint. Vă rugăm să vorbiți natural.",
}


def _ivr_url(filename: str) -> str:
    """Build the full public URL for an IVR audio file."""
    base = config.webhook_base_url.rstrip("/")
    return f"{base}/static/ivr/{filename}.mp3"


def _normalize_uk_number(digits: str) -> str:
    """
    Normalize a dialled number to E.164 format.
    
    Handles:
      - 07xxx → +447xxx
      - 447xxx → +447xxx
      - +447xxx → +447xxx
      - International numbers starting with 00 → +xxx
    """
    # Strip any non-digits
    digits = re.sub(r"[^\d]", "", digits)
    
    if digits.startswith("07") and len(digits) == 11:
        # UK mobile: 07878604072 → +447878604072
        return f"+44{digits[1:]}"
    elif digits.startswith("447") and len(digits) == 12:
        # Already has country code without +
        return f"+{digits}"
    elif digits.startswith("44") and len(digits) >= 12:
        return f"+{digits}"
    elif digits.startswith("00"):
        # International: 00xxx → +xxx
        return f"+{digits[2:]}"
    elif digits.startswith("0") and len(digits) >= 10:
        # UK landline or other: 0xxx → +44xxx
        return f"+44{digits[1:]}"
    else:
        # Assume it's already E.164 without +
        return f"+{digits}"


# ─── Step 1: Greeting + Language Selection ─────────────────────────

@router.api_route("/incoming-call", methods=["GET", "POST"])
async def incoming_call(request: Request):
    """
    Twilio webhook: first thing that happens when someone dials the VeraPoint number.
    Plays a greeting and asks the caller to select their language.
    """
    # Get caller info from Twilio
    form = await request.form()
    caller = form.get("From", "unknown")
    call_sid = form.get("CallSid", "unknown")

    logger.info(f"📞 Inbound call from {caller} (SID: {call_sid})")

    # Let the phone ring ~2 times before answering (feels more natural)
    await asyncio.sleep(4)

    greeting_url = _ivr_url("greeting")
    menu_url = _ivr_url("language_menu")
    no_input_url = _ivr_url("no_input")

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{greeting_url}</Play>
    <Pause length="1"/>
    <Gather input="dtmf" numDigits="1" action="/incoming-call/your-language" method="POST" timeout="10">
        <Play>{menu_url}</Play>
    </Gather>
    <Play>{no_input_url}</Play>
    <Hangup/>
</Response>"""

    return Response(content=twiml, media_type="text/xml")


# ─── Step 2: Caller selects THEIR language ─────────────────────────

@router.api_route("/incoming-call/your-language", methods=["GET", "POST"])
async def incoming_your_language(request: Request):
    """
    Twilio webhook: called after the caller selects THEIR language.
    Confirms the selection and asks for the TARGET language.
    """
    form = await request.form()
    digit = form.get("Digits", "")
    caller = form.get("From", "unknown")

    lang_option = LANGUAGE_OPTIONS.get(digit)

    if not lang_option:
        logger.warning(f"Invalid language selection '{digit}' from {caller}")
        invalid_url = _ivr_url("invalid_selection")
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{invalid_url}</Play>
    <Redirect method="POST">/incoming-call</Redirect>
</Response>"""
        return Response(content=twiml, media_type="text/xml")

    logger.info(f"📞 {caller} speaks: {lang_option['name']} ({lang_option['code']})")

    confirm_url = _ivr_url(lang_option["audio"])
    target_menu_url = _ivr_url("target_language_menu")
    no_input_url = _ivr_url("no_input")

    # Confirm their language, then ask for the OTHER person's language
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{confirm_url}</Play>
    <Pause length="1"/>
    <Gather input="dtmf" numDigits="1" action="/incoming-call/their-language?source={lang_option['code']}" method="POST" timeout="10">
        <Play>{target_menu_url}</Play>
    </Gather>
    <Play>{no_input_url}</Play>
    <Hangup/>
</Response>"""

    return Response(content=twiml, media_type="text/xml")


# ─── Step 3: Caller selects the OTHER PERSON'S language ────────────

@router.api_route("/incoming-call/their-language", methods=["GET", "POST"])
async def incoming_their_language(request: Request, source: str = "en"):
    """
    Twilio webhook: called after the caller selects the TARGET language.
    Confirms and asks for the phone number.
    """
    form = await request.form()
    digit = form.get("Digits", "")
    caller = form.get("From", "unknown")

    target_option = LANGUAGE_OPTIONS.get(digit)

    if not target_option:
        logger.warning(f"Invalid target language '{digit}' from {caller}")
        invalid_url = _ivr_url("invalid_selection")
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{invalid_url}</Play>
    <Redirect method="POST">/incoming-call</Redirect>
</Response>"""
        return Response(content=twiml, media_type="text/xml")

    logger.info(f"📞 {caller} wants target: {target_option['name']} ({target_option['code']})")

    # Confirm target language
    confirm_url = _ivr_url(target_option["audio"])
    enter_number_url = _ivr_url("enter_number")
    no_number_url = _ivr_url("no_number")

    # Ask for the phone number — pass both source and target langs
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{confirm_url}</Play>
    <Pause length="1"/>
    <Gather input="dtmf" finishOnKey="#" action="/incoming-call/dial?lang={source}&amp;target={target_option['code']}" method="POST" timeout="15" numDigits="15">
        <Play>{enter_number_url}</Play>
    </Gather>
    <Play>{no_number_url}</Play>
    <Hangup/>
</Response>"""

    return Response(content=twiml, media_type="text/xml")


# ─── Step 4: Capture Number, Start Translation Bridge ─────────────

@router.api_route("/incoming-call/dial", methods=["GET", "POST"])
async def incoming_dial(request: Request, lang: str = "en", target: str = "ro"):
    """
    Twilio webhook: called after the caller enters the target phone number.
    Creates a session, keeps the inbound caller as Leg A,
    and initiates an outbound call to the target (Leg B).

    Both source and target languages are passed as query params.
    """
    form = await request.form()
    digits = form.get("Digits", "")
    caller = form.get("From", "unknown")
    call_sid = form.get("CallSid", "unknown")

    if not digits or len(digits) < 10:
        invalid_number_url = _ivr_url("invalid_number")
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{invalid_number_url}</Play>
    <Redirect method="POST">/incoming-call</Redirect>
</Response>"""
        return Response(content=twiml, media_type="text/xml")

    # Normalize the dialled number to E.164
    target_number = _normalize_uk_number(digits)
    source_lang = lang
    target_lang = target

    logger.info(f"📞 {caller} wants to call {target_number} ({source_lang} → {target_lang})")

    # ─── Create the session ────────────────────────────────────
    session = session_manager.create_session(
        caller_number=caller,
        callee_number=target_number,
        source_lang=source_lang,
        target_lang=target_lang,
    )

    # Register the inbound call SID as Leg A
    session.leg_a_call_sid = call_sid
    session_manager.register_call_sid(call_sid, session.session_id, LegRole.LEG_A)

    # Build the webhook and stream URLs
    base_url = config.webhook_base_url
    ws_url = f"{config.ws_base_url}/media-stream/{session.session_id}/leg_a"

    # ─── Call the target (Leg B) in the background ─────────────
    leg_b_webhook = f"{base_url}/call-webhook/{session.session_id}/leg_b"
    leg_b_status = f"{base_url}/call-status/{session.session_id}/leg_b"

    session._inbound_leg_b_target = target_number
    session._inbound_leg_b_webhook = leg_b_webhook
    session._inbound_leg_b_status = leg_b_status

    asyncio.ensure_future(_initiate_leg_b_for_inbound(session))

    # ─── Return TwiML to keep inbound caller on the line ───────
    connecting_url = _ivr_url("connecting")

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{connecting_url}</Play>
    <Pause length="1"/>
    <Connect>
        <Stream url="{ws_url}" />
    </Connect>
</Response>"""

    logger.info(f"📞 Inbound bridge starting: session={session.session_id}, "
                f"caller={caller} ({source_lang}) → target={target_number} ({target_lang})")
    logger.info(f"📞 Leg A WebSocket URL: {ws_url}")
    logger.info(f"📞 Leg B Webhook URL: {leg_b_webhook}")
    logger.info(f"📞 TwiML connecting_url: {connecting_url}")

    return Response(content=twiml, media_type="text/xml")


# ─── Background: Initiate Leg B for Inbound Calls ─────────────────

async def _initiate_leg_b_for_inbound(session):
    """
    Called in the background after the inbound caller's TwiML is returned.
    Waits for Leg A's stream to connect, then initiates the outbound
    call to the target (Leg B).
    """
    from app.call_controller import get_twilio_client

    session_id = session.session_id
    target_number = session._inbound_leg_b_target
    webhook_url = session._inbound_leg_b_webhook
    status_url = session._inbound_leg_b_status

    # Wait for Leg A stream to be ready
    logger.info(f"[{session_id}] Waiting for inbound Leg A stream before calling Leg B...")

    # Create the event if it doesn't exist
    if not hasattr(session, '_leg_a_stream_ready'):
        session._leg_a_stream_ready = asyncio.Event()

    try:
        await asyncio.wait_for(session._leg_a_stream_ready.wait(), timeout=15.0)
        logger.info(f"[{session_id}] Inbound Leg A stream connected — calling Leg B")
    except asyncio.TimeoutError:
        logger.warning(f"[{session_id}] Leg A stream timeout — calling Leg B anyway")

    # Small delay for stability
    await asyncio.sleep(1)

    try:
        twilio = get_twilio_client()
        call_b = twilio.calls.create(
            to=target_number,
            from_=config.twilio_phone_number,
            url=webhook_url,
            status_callback=status_url,
            status_callback_event=["initiated", "ringing", "answered", "completed"],
        )
        session.leg_b_call_sid = call_b.sid
        session_manager.register_call_sid(call_b.sid, session.session_id, LegRole.LEG_B)
        logger.info(f"[{session_id}] Leg B call initiated: {call_b.sid} → {target_number}")

    except Exception as e:
        logger.error(f"[{session_id}] Failed to call Leg B ({target_number}): {e}")
        # TODO: Notify the inbound caller that the target couldn't be reached


def _format_number_for_speech(number: str) -> str:
    """Format a phone number for TTS — reads out digits naturally."""
    # Strip the + and space out the digits for natural speech
    digits = number.lstrip("+")
    # Group: country code, then groups of 3-4
    if digits.startswith("44") and len(digits) == 12:
        # UK mobile: +44 7878 604 072
        return f"{digits[0:2]}, {digits[2:6]}, {digits[6:9]}, {digits[9:]}"
    return ", ".join(digits)
