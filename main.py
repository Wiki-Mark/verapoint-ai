"""
VeraPoint.ai — Main Application Entry Point
Real-time phone call translation — multilingual

Run with: python main.py
Or: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import logging
import sys

from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import config
from app.call_controller import router as call_router
from app.inbound_handler import router as inbound_router
from app.stream_handler import handle_media_stream
from app.translator_google import translator
from app.tts_elevenlabs import tts_client

# ─── Logging Setup ─────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.log_level.upper(), logging.INFO),
    format="%(asctime)s │ %(name)-28s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)

# Reduce noise from libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

logger = logging.getLogger("verapoint")

# ─── FastAPI App ───────────────────────────────────────────────────
app = FastAPI(
    title="VeraPoint.ai",
    description="Real-time phone call translation (English ↔ Punjabi)",
    version="0.1.0",
)

# Mount routes
app.include_router(call_router)
app.include_router(inbound_router)

# Serve static IVR audio files (ElevenLabs pre-generated greetings)
import os
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.on_event("startup")
async def startup():
    """Validate configuration on startup."""
    logger.info("=" * 60)
    logger.info("  VeraPoint.ai — Translation Bridge Starting")
    logger.info("=" * 60)

    missing = config.validate()
    if missing:
        logger.warning(f"Missing config: {', '.join(missing)}")
        logger.warning("Some features may not work without these keys.")
    else:
        logger.info("✓ All configuration validated")

    logger.info(f"  Twilio Number: {config.twilio_phone_number}")
    logger.info(f"  Webhook URL:   {config.webhook_base_url}")
    logger.info(f"  WebSocket URL: {config.ws_base_url}")
    logger.info(f"  STT Provider:  {config.stt_provider}")
    logger.info(f"  TTS Model:     {config.elevenlabs_model_id}")
    logger.info(f"  Languages:     {config.default_language_pair[0]} ↔ {config.default_language_pair[1]}")
    logger.info("=" * 60)


@app.on_event("shutdown")
async def shutdown():
    """Clean up resources on shutdown."""
    await translator.close()
    await tts_client.close()
    logger.info("VeraPoint.ai shut down cleanly")


@app.get("/")
async def index():
    """Health check endpoint."""
    return {
        "service": "VeraPoint.ai",
        "status": "running",
        "version": "0.1.0",
        "description": "Real-time phone call translation (English ↔ Punjabi)",
    }


@app.get("/health")
async def health():
    """Detailed health check."""
    return {
        "status": "healthy",
        "config": {
            "twilio": bool(config.twilio_account_sid),
            "deepgram": bool(config.deepgram_api_key),
            "elevenlabs": bool(config.elevenlabs_api_key),
            "google_translate": bool(config.google_application_credentials),
            "ngrok_url": config.ngrok_url or "not set",
        },
    }


# ─── WebSocket Endpoint for Twilio Media Streams ──────────────────
# Two separate WebSocket endpoints: one for each leg of the call

@app.websocket("/media-stream/{session_id}/{leg}")
async def media_stream_endpoint(websocket: WebSocket, session_id: str, leg: str):
    """
    WebSocket endpoint for Twilio Media Streams.
    
    Twilio connects to:
      /media-stream/{session_id}/leg_a  (English speaker's audio)
      /media-stream/{session_id}/leg_b  (Punjabi speaker's audio)
    """
    await handle_media_stream(websocket, session_id, leg)




if __name__ == "__main__":
    import uvicorn

    is_dev = os.getenv("ENVIRONMENT", "production") == "development"

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=config.port,
        reload=is_dev,
        log_level=config.log_level.lower(),
    )
