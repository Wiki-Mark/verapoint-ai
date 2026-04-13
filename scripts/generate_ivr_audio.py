"""
VeraPoint.ai — IVR Audio Generator
Regenerates all IVR greeting audio files using ElevenLabs.

Voice: Lily (pFZP5JQG7iQjIQuC4Bku) — Velvety British actress
Model: eleven_multilingual_v2 — supports 29 languages in one model
Speed: Natural pacing with pauses (via punctuation/SSML-like text)

Language confirmations are spoken IN their own language.
"""

import httpx
import os
import time

# ─── Config ───────────────────────────────────────────────────────
API_KEY = None
with open(os.path.join(os.path.dirname(__file__), "..", ".env")) as f:
    for line in f:
        line = line.strip()
        if line.startswith("ELEVENLABS_API_KEY="):
            API_KEY = line.split("=", 1)[1]
            break

assert API_KEY, "ELEVENLABS_API_KEY not found in .env"

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "ivr")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Lily — Velvety Actress, British female
VOICE_ID = "pFZP5JQG7iQjIQuC4Bku"

# Voice settings — optimized for warm, natural IVR
VOICE_SETTINGS = {
    "stability": 0.70,        # Slightly more stable for IVR clarity
    "similarity_boost": 0.75,
    "style": 0.10,            # Subtle expressiveness
    "use_speaker_boost": True,
}

# ─── IVR Script ───────────────────────────────────────────────────
# Each entry: (filename, text, optional_voice_override)
# Using "..." pauses and commas for natural, unhurried pacing

IVR_SCRIPTS = [
    # Greeting — warm, welcoming, unhurried
    (
        "greeting",
        "Welcome to VeraPoint... "
        "your real-time translation service. "
        "We'll connect you to anyone, in any language... "
        "over a normal phone call.",
        None,  # Use default Lily voice
    ),

    # Language menu — each option announced in English, then the language name in that language
    (
        "language_menu",
        "Please select your language. ... "
        "For Urdu, press 1. ... "
        "For Punjabi, press 2. ... "
        "For Hindi, press 3. ... "
        "For Arabic, press 4. ... "
        "For Polish, press 5.",
        None,
    ),

    # ─── Language confirmations — spoken IN the target language ────
    # Urdu confirmation
    (
        "urdu_confirm",
        "آپ نے اردو زبان منتخب کی ہے۔ شکریہ۔",
        None,  # Multilingual v2 handles Urdu natively
    ),

    # Punjabi confirmation
    (
        "punjabi_confirm",
        "ਤੁਸੀਂ ਪੰਜਾਬੀ ਭਾਸ਼ਾ ਚੁਣੀ ਹੈ। ਧੰਨਵਾਦ।",
        None,
    ),

    # Hindi confirmation
    (
        "hindi_confirm",
        "आपने हिंदी भाषा चुनी है। धन्यवाद।",
        None,
    ),

    # Arabic confirmation
    (
        "arabic_confirm",
        "لقد اخترت اللغة العربية. شكراً لك.",
        None,
    ),

    # Polish confirmation
    (
        "polish_confirm",
        "Wybrałeś język polski. Dziękuję.",
        None,
    ),

    # Enter number prompt — clear and patient
    (
        "enter_number",
        "Now... please enter the phone number you'd like to call, "
        "using your keypad. ... "
        "When you're finished, press the hash key.",
        None,
    ),

    # Connecting
    (
        "connecting",
        "Thank you. ... "
        "We're connecting your call now. "
        "Please stay on the line... "
        "the other person will hear you in their language.",
        None,
    ),

    # No input received
    (
        "no_input",
        "Sorry... we didn't receive any input. "
        "Please try calling again. Goodbye.",
        None,
    ),

    # No number entered
    (
        "no_number",
        "Sorry... we didn't receive a phone number. "
        "Please try calling again. Goodbye.",
        None,
    ),

    # Invalid selection
    (
        "invalid_selection",
        "Sorry... that wasn't a valid selection. "
        "Let's try again.",
        None,
    ),

    # Invalid number
    (
        "invalid_number",
        "Sorry... that doesn't appear to be a valid phone number. "
        "Let's try again.",
        None,
    ),
]


def generate_audio(filename: str, text: str, voice_id: str = VOICE_ID):
    """Generate a single IVR audio file using ElevenLabs."""
    output_path = os.path.join(OUTPUT_DIR, f"{filename}.mp3")

    print(f"\n  Generating: {filename}.mp3")
    print(f"  Text: {text[:80]}{'...' if len(text) > 80 else ''}")
    print(f"  Voice: {voice_id}")

    r = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={
            "xi-api-key": API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": VOICE_SETTINGS,
        },
        timeout=60,
    )

    if r.status_code == 200:
        with open(output_path, "wb") as f:
            f.write(r.content)
        print(f"  ✓ Saved: {output_path} ({len(r.content):,} bytes)")
    else:
        print(f"  ✗ FAILED ({r.status_code}): {r.text[:200]}")

    # Rate limit: ElevenLabs allows ~2-3 requests/sec on free tier
    time.sleep(1.5)


def main():
    print("=" * 60)
    print("  VeraPoint.ai — IVR Audio Generator")
    print("  Voice: Lily (Velvety British Actress)")
    print("  Model: eleven_multilingual_v2")
    print(f"  Output: {OUTPUT_DIR}")
    print("=" * 60)

    # Back up existing files
    backup_dir = os.path.join(OUTPUT_DIR, "backup")
    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir, exist_ok=True)
        import shutil
        for f in os.listdir(OUTPUT_DIR):
            if f.endswith(".mp3") and f != "test_lily.mp3":
                src = os.path.join(OUTPUT_DIR, f)
                dst = os.path.join(backup_dir, f)
                shutil.copy2(src, dst)
                print(f"  Backed up: {f}")

    # Generate all IVR audio
    for filename, text, voice_override in IVR_SCRIPTS:
        voice = voice_override or VOICE_ID
        generate_audio(filename, text, voice)

    print("\n" + "=" * 60)
    print("  ✓ All IVR audio files regenerated!")
    print("  Restart the server or just call the number again.")
    print("=" * 60)


if __name__ == "__main__":
    main()
