"""
VeraPoint.ai — Pipeline Test Script
Tests the STT → Translate → TTS pipeline WITHOUT Twilio.
Useful for validating API keys and translation quality.

Usage:
    python scripts/test_pipeline.py
    python scripts/test_pipeline.py --text "Hello, how are you?"
    python scripts/test_pipeline.py --text "Hello, how are you?" --direction en-pa
"""

import os
import sys
import asyncio
import argparse
import time

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv(override=True)

from app.translator_google import translator
from app.tts_elevenlabs import tts_client
from app.audio_utils import calculate_audio_duration_ms


async def test_translation(text: str, source_lang: str, target_lang: str):
    """Test just the translation component."""
    print(f"\n{'─'*50}")
    print(f"  Translation Test: {source_lang} → {target_lang}")
    print(f"{'─'*50}")
    print(f"  Input:  '{text}'")

    start = time.time()
    translated = await translator.translate(text, source_lang, target_lang)
    elapsed = (time.time() - start) * 1000

    print(f"  Output: '{translated}'")
    print(f"  Time:   {elapsed:.0f}ms")
    return translated


async def test_tts(text: str, language: str):
    """Test the TTS component."""
    print(f"\n{'─'*50}")
    print(f"  TTS Test: [{language}] '{text[:50]}'")
    print(f"{'─'*50}")

    start = time.time()
    audio_data = await tts_client.synthesize_to_mulaw(text, language)
    elapsed = (time.time() - start) * 1000

    duration_ms = calculate_audio_duration_ms(audio_data, sample_rate=8000, sample_width=1)

    print(f"  Audio:    {len(audio_data)} bytes µ-law")
    print(f"  Duration: {duration_ms:.0f}ms")
    print(f"  Time:     {elapsed:.0f}ms")

    # Save to file for manual listening
    output_file = f"/tmp/verapoint_test_{language}.raw"
    with open(output_file, "wb") as f:
        f.write(audio_data)
    print(f"  Saved:    {output_file}")
    print(f"  Play with: ffplay -f mulaw -ar 8000 -ac 1 {output_file}")

    return audio_data


async def test_full_pipeline(text: str, source_lang: str, target_lang: str):
    """Test the full translation + TTS pipeline."""
    print(f"\n{'='*60}")
    print(f"  Full Pipeline Test: {source_lang} → {target_lang}")
    print(f"{'='*60}")
    print(f"  Input text: '{text}'")

    total_start = time.time()

    # Step 1: Translate
    translated = await test_translation(text, source_lang, target_lang)

    # Step 2: TTS
    audio = await test_tts(translated, target_lang)

    total_elapsed = (time.time() - total_start) * 1000
    print(f"\n{'─'*50}")
    print(f"  Total pipeline time: {total_elapsed:.0f}ms")
    print(f"  (Target: <1500ms)")
    if total_elapsed < 1500:
        print(f"  ✓ WITHIN TARGET")
    else:
        print(f"  ✗ ABOVE TARGET — optimization needed")
    print(f"{'─'*50}")


async def test_bidirectional(text_en: str, text_pa: str):
    """Test both translation directions."""
    print(f"\n{'='*60}")
    print(f"  Bidirectional Test")
    print(f"{'='*60}")

    # EN → PA
    await test_full_pipeline(text_en, "en", "pa")

    # PA → EN
    await test_full_pipeline(text_pa, "pa", "en")


async def main():
    parser = argparse.ArgumentParser(description="VeraPoint.ai pipeline test")
    parser.add_argument("--text", default="Hello, how are you? I need to speak with my lawyer.", help="Text to translate")
    parser.add_argument("--direction", default="en-pa", help="Translation direction (en-pa or pa-en)")
    parser.add_argument("--bidirectional", action="store_true", help="Test both directions")

    args = parser.parse_args()

    lang_parts = args.direction.split("-")
    source_lang = lang_parts[0]
    target_lang = lang_parts[1]

    try:
        if args.bidirectional:
            await test_bidirectional(
                text_en="Hello, how are you? I need to speak with my lawyer about my bail hearing.",
                text_pa="ਮੈਂ ਠੀਕ ਹਾਂ। ਮੈਨੂੰ ਆਪਣੇ ਕੇਸ ਬਾਰੇ ਗੱਲ ਕਰਨੀ ਹੈ।",
            )
        else:
            await test_full_pipeline(args.text, source_lang, target_lang)
    finally:
        await translator.close()
        await tts_client.close()


if __name__ == "__main__":
    asyncio.run(main())
