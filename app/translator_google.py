"""
VeraPoint.ai — Google Cloud Translation Client
Translates text between English and Punjabi using Google Cloud Translation API v3.
Falls back to v2 (basic) if v3 isn't configured.
"""

import logging
from typing import Optional

import httpx

from app.config import config

logger = logging.getLogger(__name__)

# Google Cloud Translation API v2 (basic) endpoint
# Uses API key auth — simpler for MVP than service account
TRANSLATE_V2_URL = "https://translation.googleapis.com/language/translate/v2"


class GoogleTranslateClient:
    """
    Translation client using Google Cloud Translation API.
    
    For MVP, uses the v2 (Basic) REST API with API key authentication.
    This avoids the complexity of service account setup while providing
    excellent translation quality for English ↔ Punjabi.
    """

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=10.0)
        self._api_key = config.google_application_credentials  # Reusing this field for API key

    async def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
    ) -> str:
        """
        Translate text from source language to target language.
        
        Args:
            text: Text to translate
            source_lang: Source language code (e.g., "en" for English)
            target_lang: Target language code (e.g., "pa" for Punjabi)
            
        Returns:
            Translated text string
        """
        if not text.strip():
            return ""

        try:
            # Try Google Cloud Translation API v2
            if self._api_key:
                return await self._translate_google_v2(text, source_lang, target_lang)
            else:
                # Fallback: use the free googletrans-style endpoint
                return await self._translate_free(text, source_lang, target_lang)
        except Exception as e:
            logger.error(f"Translation failed: {e}")
            return text  # Return original text on failure

    async def _translate_google_v2(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
    ) -> str:
        """Translate using Google Cloud Translation API v2 with API key."""
        params = {
            "key": self._api_key,
            "q": text,
            "source": source_lang,
            "target": target_lang,
            "format": "text",
        }

        response = await self._client.post(TRANSLATE_V2_URL, data=params)
        response.raise_for_status()

        result = response.json()
        translations = result.get("data", {}).get("translations", [])
        if translations:
            translated = translations[0].get("translatedText", "")
            logger.info(
                f"Translated [{source_lang}→{target_lang}]: "
                f"'{text[:50]}' → '{translated[:50]}'"
            )
            return translated

        logger.warning(f"No translation returned for: {text[:50]}")
        return text

    async def _translate_free(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
    ) -> str:
        """
        Fallback translation using the free Google Translate endpoint.
        NOTE: This is unofficial and rate-limited. Use API key for production.
        """
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": source_lang,
            "tl": target_lang,
            "dt": "t",
            "q": text,
        }

        response = await self._client.get(url, params=params)
        response.raise_for_status()

        result = response.json()
        if result and result[0]:
            translated = "".join(
                segment[0] for segment in result[0] if segment[0]
            )
            logger.info(
                f"Translated (free) [{source_lang}→{target_lang}]: "
                f"'{text[:50]}' → '{translated[:50]}'"
            )
            return translated

        return text

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()
        logger.info("Google Translate client closed")


# Global translator instance
translator = GoogleTranslateClient()
