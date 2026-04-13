"""
VeraPoint.ai — Deepgram Streaming STT Client
Real-time speech-to-text using Deepgram Nova-3 via WebSocket.
Supports English and Punjabi.
"""

import json
import asyncio
import logging
from typing import Callable, Optional, Awaitable

import websockets

from app.config import config

logger = logging.getLogger(__name__)

# Deepgram streaming WebSocket endpoint
DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"


class DeepgramSTTClient:
    """
    Streaming speech-to-text client using Deepgram Nova-3.
    
    Connects via WebSocket, sends raw audio chunks, receives transcription events.
    Calls the on_transcript callback when a final transcript is ready.
    """

    def __init__(
        self,
        language: str = "en",
        on_transcript: Optional[Callable[[str, bool], Awaitable[None]]] = None,
        on_speech_started: Optional[Callable[[], Awaitable[None]]] = None,
        on_speech_ended: Optional[Callable[[], Awaitable[None]]] = None,
        sample_rate: int = 8000,
        encoding: str = "mulaw",
        channels: int = 1,
    ):
        """
        Args:
            language: Language code ("en", "pa" for Punjabi)
            on_transcript: Callback(text, is_final) when transcript is ready
            on_speech_started: Callback when speech is detected
            on_speech_ended: Callback when speech ends (silence)
            sample_rate: Audio sample rate (8000 for Twilio)
            encoding: Audio encoding ("mulaw" for Twilio G.711)
            channels: Number of audio channels
        """
        self.language = language
        self.on_transcript = on_transcript
        self.on_speech_started = on_speech_started
        self.on_speech_ended = on_speech_ended
        self.sample_rate = sample_rate
        self.encoding = encoding
        self.channels = channels

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False
        self._receive_task: Optional[asyncio.Task] = None
        self._accumulated_text = ""

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    def _build_url(self) -> str:
        """Build the Deepgram WebSocket URL with query parameters."""
        params = {
            "model": "nova-3",
            "language": self.language,
            "encoding": self.encoding,
            "sample_rate": str(self.sample_rate),
            "channels": str(self.channels),
            "punctuate": "true",
            "interim_results": "true",
            "endpointing": "500",  # 500ms silence = end of speech
            "vad_events": "true",  # Enable voice activity detection events
            "smart_format": "true",
        }
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{DEEPGRAM_WS_URL}?{query_string}"

    async def connect(self):
        """Establish WebSocket connection to Deepgram."""
        url = self._build_url()
        headers = {
            "Authorization": f"Token {config.deepgram_api_key}",
        }

        try:
            self._ws = await websockets.connect(
                url,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=10,
            )
            self._connected = True
            self._receive_task = asyncio.create_task(self._receive_loop())
            logger.info(f"Deepgram STT connected (language={self.language})")
        except Exception as e:
            logger.error(f"Deepgram connection failed: {e}")
            raise

    async def send_audio(self, audio_data: bytes):
        """
        Send raw audio bytes to Deepgram.
        Audio should be in the format specified at init (default: µ-law, 8kHz).
        """
        if not self.is_connected:
            return
        try:
            await self._ws.send(audio_data)
        except Exception as e:
            logger.error(f"Error sending audio to Deepgram: {e}")

    async def _receive_loop(self):
        """Receive and process transcription events from Deepgram."""
        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)
                    await self._handle_event(data)
                except json.JSONDecodeError:
                    logger.warning(f"Non-JSON message from Deepgram: {message[:100]}")
        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"Deepgram connection closed: {e}")
        except Exception as e:
            logger.error(f"Deepgram receive error: {e}")
        finally:
            self._connected = False

    async def _handle_event(self, data: dict):
        """Process a Deepgram event."""
        event_type = data.get("type", "")

        if event_type == "Results":
            # Transcription result
            channel = data.get("channel", {})
            alternatives = channel.get("alternatives", [])
            if not alternatives:
                return

            transcript = alternatives[0].get("transcript", "").strip()
            is_final = data.get("is_final", False)
            speech_final = data.get("speech_final", False)

            if transcript:
                logger.debug(
                    f"STT [{self.language}] {'FINAL' if is_final else 'interim'}: {transcript}"
                )

                if is_final:
                    self._accumulated_text += " " + transcript if self._accumulated_text else transcript

                if speech_final and self._accumulated_text:
                    # Speech segment is complete — send the full accumulated text
                    final_text = self._accumulated_text.strip()
                    self._accumulated_text = ""

                    if self.on_transcript:
                        await self.on_transcript(final_text, True)
                elif is_final and self.on_transcript:
                    # Interim final — useful for real-time display but not for translation
                    pass

        elif event_type == "SpeechStarted":
            logger.debug(f"STT [{self.language}] Speech started")
            if self.on_speech_started:
                await self.on_speech_started()

        elif event_type == "UtteranceEnd":
            logger.debug(f"STT [{self.language}] Utterance ended")
            # If we have accumulated text, flush it
            if self._accumulated_text:
                final_text = self._accumulated_text.strip()
                self._accumulated_text = ""
                if self.on_transcript:
                    await self.on_transcript(final_text, True)

            if self.on_speech_ended:
                await self.on_speech_ended()

        elif event_type == "Metadata":
            logger.debug(f"Deepgram metadata: request_id={data.get('request_id', 'n/a')}")

        elif event_type == "Error":
            logger.error(f"Deepgram error: {data}")

    async def close(self):
        """Close the Deepgram connection gracefully."""
        self._connected = False
        if self._ws:
            try:
                # Send close signal to Deepgram
                await self._ws.send(json.dumps({"type": "CloseStream"}))
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        logger.info(f"Deepgram STT disconnected (language={self.language})")
