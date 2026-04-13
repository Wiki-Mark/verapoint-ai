"""
VeraPoint.ai — Configuration
Central config loader with validation. All API keys and settings are loaded from .env.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv(override=True)


@dataclass
class Config:
    """Application configuration loaded from environment variables."""

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""  # VeraPoint's Twilio number (from_ number)

    # Deepgram (Primary STT)
    deepgram_api_key: str = ""

    # Google Cloud Translation
    google_application_credentials: str = ""  # Path to service account JSON

    # ElevenLabs (TTS)
    elevenlabs_api_key: str = ""
    elevenlabs_model_id: str = "eleven_flash_v2_5"  # Low-latency model
    elevenlabs_voice_english: str = "JBFqnCBsd6RMkjVDRZzb"  # "George" — warm British
    elevenlabs_voice_urdu: str = ""  # Set after finding a suitable Urdu voice

    # Server
    port: int = 8000
    webhook_base_url_override: str = ""  # Explicit override (WEBHOOK_BASE_URL env var)
    ngrok_url: str = ""  # Legacy local dev: https://xxxx.ngrok-free.app
    railway_public_domain: str = ""  # Auto-set by Railway
    log_level: str = "INFO"

    # Translation settings
    default_language_pair: tuple = ("ur", "en")  # Urdu (Leg A) ↔ English (Leg B)
    stt_provider: str = "deepgram"  # "deepgram" or "google"

    # Turn-taking
    silence_threshold_ms: int = 500  # Silence duration before triggering translation
    max_turn_duration_s: int = 30  # Max seconds before forcing a turn boundary

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        return cls(
            twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
            twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
            twilio_phone_number=os.getenv("TWILIO_PHONE_NUMBER", ""),
            deepgram_api_key=os.getenv("DEEPGRAM_API_KEY", ""),
            google_application_credentials=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""),
            elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY", ""),
            elevenlabs_model_id=os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5"),
            elevenlabs_voice_english=os.getenv("ELEVENLABS_VOICE_ENGLISH", "JBFqnCBsd6RMkjVDRZzb"),
            elevenlabs_voice_urdu=os.getenv("ELEVENLABS_VOICE_URDU", ""),
            port=int(os.getenv("PORT", "8000")),
            webhook_base_url_override=os.getenv("WEBHOOK_BASE_URL", ""),
            ngrok_url=os.getenv("NGROK_URL", ""),
            railway_public_domain=os.getenv("RAILWAY_PUBLIC_DOMAIN", ""),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            stt_provider=os.getenv("STT_PROVIDER", "deepgram"),
            silence_threshold_ms=int(os.getenv("SILENCE_THRESHOLD_MS", "500")),
            max_turn_duration_s=int(os.getenv("MAX_TURN_DURATION_S", "30")),
        )

    def validate(self) -> list[str]:
        """Return list of missing required config values."""
        missing = []
        if not self.twilio_account_sid:
            missing.append("TWILIO_ACCOUNT_SID")
        if not self.twilio_auth_token:
            missing.append("TWILIO_AUTH_TOKEN")
        if not self.twilio_phone_number:
            missing.append("TWILIO_PHONE_NUMBER")
        if not self.deepgram_api_key:
            missing.append("DEEPGRAM_API_KEY")
        if not self.elevenlabs_api_key:
            missing.append("ELEVENLABS_API_KEY")
        return missing

    @property
    def webhook_base_url(self) -> str:
        """Return the base URL for Twilio webhooks.
        Priority: WEBHOOK_BASE_URL > RAILWAY_PUBLIC_DOMAIN > NGROK_URL > localhost
        """
        if self.webhook_base_url_override:
            return self.webhook_base_url_override.rstrip("/")
        if self.railway_public_domain:
            return f"https://{self.railway_public_domain}"
        if self.ngrok_url:
            return self.ngrok_url.rstrip("/")
        return f"http://localhost:{self.port}"

    @property
    def ws_base_url(self) -> str:
        """Return the WebSocket base URL for Twilio Media Streams."""
        base = self.webhook_base_url
        if base.startswith("https://"):
            return base.replace("https://", "wss://")
        return base.replace("http://", "ws://")


# Global config instance
config = Config.from_env()
