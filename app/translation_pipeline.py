"""
VeraPoint.ai — Translation Pipeline
Orchestrates the full STT → Translate → TTS flow for a single call leg.
One pipeline instance per direction (e.g., English→Punjabi).
"""

import asyncio
import logging
import time
from typing import Optional, Callable, Awaitable

from app.stt_deepgram import DeepgramSTTClient
from app.translator_google import translator
from app.tts_elevenlabs import tts_client
from app.turn_manager import TurnManager
from app.audio_utils import mulaw_to_base64, chunk_audio

logger = logging.getLogger(__name__)


class TranslationPipeline:
    """
    Handles the complete translation flow for ONE direction:
    Speaker's audio (µ-law) → STT → Translate → TTS → Listener's audio (µ-law)
    
    Two instances are created per session:
      - Pipeline A: EN audio from Leg A → Punjabi audio for Leg B
      - Pipeline B: PA audio from Leg B → English audio for Leg A
    """

    def __init__(
        self,
        pipeline_id: str,
        source_lang: str,
        target_lang: str,
        turn_manager: TurnManager,
        source_leg_id: str,
        target_leg_id: str,
        on_audio_ready: Optional[Callable[[str, bytes], Awaitable[None]]] = None,
    ):
        """
        Args:
            pipeline_id: Identifier for logging (e.g., "en→pa")
            source_lang: Source language code ("en" or "pa")
            target_lang: Target language code ("pa" or "en")
            turn_manager: Shared turn manager for both directions
            source_leg_id: The leg that speaks ("leg_a" or "leg_b")
            target_leg_id: The leg that listens ("leg_a" or "leg_b")
            on_audio_ready: Callback(target_leg_id, mulaw_bytes) to inject audio
        """
        self.pipeline_id = pipeline_id
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.turn_manager = turn_manager
        self.source_leg_id = source_leg_id
        self.target_leg_id = target_leg_id
        self.on_audio_ready = on_audio_ready

        # STT client for the source language
        self._stt: Optional[DeepgramSTTClient] = None

        # Stats
        self.translations_count = 0
        self.total_latency_ms = 0.0

    async def start(self):
        """Initialize and connect the STT client."""
        self._stt = DeepgramSTTClient(
            language=self.source_lang,
            on_transcript=self._on_transcript,
            on_speech_started=self._on_speech_started,
            on_speech_ended=self._on_speech_ended,
        )
        await self._stt.connect()
        logger.info(f"Pipeline [{self.pipeline_id}] started")

    async def process_audio(self, mulaw_data: bytes):
        """
        Feed raw µ-law audio from Twilio into the pipeline.
        Audio goes directly to Deepgram for transcription.
        """
        if self._stt and self._stt.is_connected:
            await self._stt.send_audio(mulaw_data)

    async def _on_speech_started(self):
        """Called when Deepgram detects speech start."""
        self.turn_manager.on_speech_started(self.source_leg_id)

    async def _on_speech_ended(self):
        """Called when Deepgram detects speech end."""
        self.turn_manager.on_speech_ended(self.source_leg_id)

    async def _on_transcript(self, text: str, is_final: bool):
        """
        Called when Deepgram produces a final transcript.
        This triggers the translate → TTS pipeline.
        """
        if not is_final or not text.strip():
            return

        start_time = time.time()
        logger.info(
            f"Pipeline [{self.pipeline_id}] transcript: '{text}'"
        )

        try:
            # Step 1: Translate
            translated_text = await translator.translate(
                text=text,
                source_lang=self.source_lang,
                target_lang=self.target_lang,
            )

            if not translated_text:
                logger.warning(f"Pipeline [{self.pipeline_id}] empty translation for: '{text}'")
                return

            logger.info(
                f"Pipeline [{self.pipeline_id}] translated: '{translated_text}'"
            )

            # Step 2: Check if we can play audio to the target leg
            if not self.turn_manager.should_play_tts(self.target_leg_id):
                # Target is busy — queue the translation
                self.turn_manager.queue_translation(self.source_leg_id, translated_text)
                logger.info(
                    f"Pipeline [{self.pipeline_id}] target busy, queued translation"
                )
                return

            # Step 3: Synthesize & deliver
            await self._synthesize_and_deliver(translated_text, start_time)

            # Step 4: Check for any pending/queued translations
            while True:
                pending = self.turn_manager.get_pending_translation(self.target_leg_id)
                if not pending:
                    break
                if self.turn_manager.should_play_tts(self.target_leg_id):
                    await self._synthesize_and_deliver(pending, time.time())

        except Exception as e:
            logger.error(f"Pipeline [{self.pipeline_id}] error: {e}", exc_info=True)

    async def _synthesize_and_deliver(self, text: str, start_time: float):
        """Synthesize text to speech and deliver to the target leg."""
        self.turn_manager.mark_tts_started(self.target_leg_id)

        try:
            # Use streaming TTS for lower latency
            first_chunk = True
            total_audio_bytes = 0

            async for mulaw_chunk in tts_client.synthesize_streaming(
                text=text,
                language=self.target_lang,
            ):
                if first_chunk:
                    latency_ms = (time.time() - start_time) * 1000
                    logger.info(
                        f"Pipeline [{self.pipeline_id}] first audio chunk "
                        f"latency: {latency_ms:.0f}ms"
                    )
                    self.total_latency_ms += latency_ms
                    first_chunk = False

                total_audio_bytes += len(mulaw_chunk)

                # Deliver audio chunk to the target leg
                if self.on_audio_ready:
                    await self.on_audio_ready(self.target_leg_id, mulaw_chunk)

            self.translations_count += 1
            total_latency_ms = (time.time() - start_time) * 1000
            logger.info(
                f"Pipeline [{self.pipeline_id}] delivered {total_audio_bytes} bytes "
                f"total latency: {total_latency_ms:.0f}ms "
                f"(translations: {self.translations_count})"
            )

        except Exception as e:
            logger.error(f"Pipeline [{self.pipeline_id}] synthesis error: {e}")
        finally:
            self.turn_manager.mark_tts_ended(self.target_leg_id)

    async def stop(self):
        """Stop the pipeline and close connections."""
        if self._stt:
            await self._stt.close()
        logger.info(
            f"Pipeline [{self.pipeline_id}] stopped. "
            f"Total translations: {self.translations_count}, "
            f"Avg latency: {self.total_latency_ms / max(1, self.translations_count):.0f}ms"
        )

    @property
    def avg_latency_ms(self) -> float:
        if self.translations_count == 0:
            return 0.0
        return self.total_latency_ms / self.translations_count
