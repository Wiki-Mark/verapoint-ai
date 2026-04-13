"""
VeraPoint.ai — ElevenLabs Streaming TTS Client
Real-time text-to-speech using ElevenLabs Flash v2.5 via HTTP streaming.
Uses native ulaw_8000 output — zero conversion overhead for Twilio.
"""

import asyncio
import logging
from typing import AsyncGenerator

import httpx

from app.config import config

logger = logging.getLogger(__name__)

# ElevenLabs API endpoints
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"


class ElevenLabsTTSClient:
    """
    Streaming TTS client using ElevenLabs Flash v2.5.
    
    Requests native µ-law 8kHz output from ElevenLabs API,
    which is exactly what Twilio Media Streams expects — zero conversion.
    """

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=30.0)

    def _get_voice_id(self, language: str) -> str:
        """Get the appropriate voice ID for a language."""
        if language == "en":
            return config.elevenlabs_voice_english
        elif language in ("ur", "hi", "pa"):
            # Urdu/Hindi/Punjabi share voice — Urdu voice works for all three
            return config.elevenlabs_voice_urdu or config.elevenlabs_voice_english
        else:
            return config.elevenlabs_voice_english

    def _build_request(self, text: str, voice_id: str) -> tuple[str, dict, dict]:
        """Build the API request URL, headers, and payload."""
        # output_format MUST be a query parameter, not in the JSON body
        url = ELEVENLABS_TTS_URL.format(voice_id=voice_id) + "?output_format=ulaw_8000"
        headers = {
            "xi-api-key": config.elevenlabs_api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "text": text,
            "model_id": config.elevenlabs_model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.0,
                "use_speaker_boost": True,
            },
        }
        return url, headers, payload

    async def synthesize_to_mulaw(
        self,
        text: str,
        language: str = "en",
    ) -> bytes:
        """
        Synthesize text to speech and return G.711 µ-law audio bytes.
        
        Uses ElevenLabs native ulaw_8000 format — zero conversion overhead.
        
        Args:
            text: Text to synthesize
            language: Target language ("en", "ur", etc.)
            
        Returns:
            G.711 µ-law audio bytes (8kHz, 8-bit)
        """
        if not text.strip():
            return b""

        voice_id = self._get_voice_id(language)
        url, headers, payload = self._build_request(text, voice_id)

        try:
            response = await self._client.post(url, headers=headers, json=payload)
            response.raise_for_status()

            mulaw_data = response.content

            if not mulaw_data:
                logger.warning("ElevenLabs returned empty audio")
                return b""

            duration_ms = len(mulaw_data) / 8000 * 1000
            logger.info(
                f"TTS [{language}]: '{text[:50]}' → "
                f"{len(mulaw_data)} bytes µ-law ({duration_ms:.0f}ms)"
            )
            return mulaw_data

        except httpx.HTTPStatusError as e:
            logger.error(
                f"ElevenLabs HTTP error {e.response.status_code}: "
                f"{e.response.text[:200]}"
            )
            return b""
        except Exception as e:
            logger.error(f"ElevenLabs TTS failed: {e}")
            return b""

    async def synthesize_streaming(
        self,
        text: str,
        language: str = "en",
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream TTS audio as G.711 µ-law chunks.
        
        Yields audio chunks as they arrive from ElevenLabs.
        Native ulaw_8000 format — chunks go straight to Twilio.
        
        Args:
            text: Text to synthesize
            language: Target language ("en", "ur", etc.)
            
        Yields:
            G.711 µ-law audio chunks (8kHz, 8-bit)
        """
        if not text.strip():
            return

        voice_id = self._get_voice_id(language)
        url, headers, payload = self._build_request(text, voice_id)

        try:
            async with self._client.stream(
                "POST", url, headers=headers, json=payload,
            ) as response:
                response.raise_for_status()

                # Native µ-law — yield chunks directly, no conversion needed
                buffer = bytearray()
                chunk_size = 320  # 320 bytes µ-law = 40ms at 8kHz

                async for chunk in response.aiter_bytes(1024):
                    buffer.extend(chunk)

                    while len(buffer) >= chunk_size:
                        yield bytes(buffer[:chunk_size])
                        buffer = buffer[chunk_size:]

                # Yield any remaining audio
                if buffer:
                    yield bytes(buffer)

        except httpx.HTTPStatusError as e:
            logger.error(
                f"ElevenLabs streaming error {e.response.status_code}: "
                f"{e.response.text[:200]}"
            )
        except Exception as e:
            logger.error(f"ElevenLabs streaming TTS failed: {e}")

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()
        logger.info("ElevenLabs TTS client closed")


# Global TTS instance
tts_client = ElevenLabsTTSClient()
