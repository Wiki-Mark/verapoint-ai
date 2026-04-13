"""
VeraPoint.ai — Make Call CLI
Initiates a translated phone call between two parties.

Usage:
    python scripts/make_call.py --caller +447561161161 --callee +44XXXXXXXXXX
    python scripts/make_call.py --caller +447561161161 --callee +44XXXXXXXXXX --lang en-pa
    python scripts/make_call.py --test  (calls yourself for single-leg testing)
"""

import os
import sys
import argparse
import httpx

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv(override=True)


def make_call(
    server_url: str,
    caller: str,
    callee: str,
    source_lang: str = "ur",
    target_lang: str = "en",
):
    """Send a request to the VeraPoint server to initiate a translated call."""
    url = f"{server_url}/initiate-call"
    payload = {
        "caller_number": caller,
        "callee_number": callee,
        "source_lang": source_lang,
        "target_lang": target_lang,
    }

    print(f"\n{'='*60}")
    print(f"  VeraPoint.ai — Initiating Translated Call")
    print(f"{'='*60}")
    print(f"  Caller (A):  {caller} [{source_lang}]")
    print(f"  Callee (B):  {callee} [{target_lang}]")
    print(f"  Server:      {server_url}")
    print(f"{'='*60}\n")

    try:
        response = httpx.post(url, json=payload, timeout=30.0)
        response.raise_for_status()
        result = response.json()

        print(f"✓ Call initiated!")
        print(f"  Session ID:      {result.get('session_id')}")
        print(f"  Status:          {result.get('status')}")
        print(f"  Leg A Call SID:  {result.get('leg_a_call_sid')}")
        print(f"  Leg B Call SID:  {result.get('leg_b_call_sid')}")
        print(f"\n  Both phones should ring shortly...")
        print(f"  Pick up both to start the translation bridge.\n")
        return result

    except httpx.HTTPStatusError as e:
        print(f"✗ Server error: {e.response.status_code}")
        print(f"  {e.response.text}")
        sys.exit(1)
    except httpx.ConnectError:
        print(f"✗ Cannot connect to server at {server_url}")
        print(f"  Make sure the server is running: python main.py")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="VeraPoint.ai — Initiate a translated phone call"
    )
    parser.add_argument(
        "--caller",
        help="Caller phone number (Person A, Urdu speaker)",
    )
    parser.add_argument(
        "--callee",
        help="Callee phone number (Person B, English speaker)",
    )
    parser.add_argument(
        "--lang",
        default="ur-en",
        help="Language pair (default: ur-en). Format: source-target",
    )
    parser.add_argument(
        "--server",
        default="http://localhost:8000",
        help="VeraPoint server URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test mode: calls your Twilio number to itself",
    )

    args = parser.parse_args()

    # Parse language pair
    lang_parts = args.lang.split("-")
    source_lang = lang_parts[0] if len(lang_parts) >= 1 else "en"
    target_lang = lang_parts[1] if len(lang_parts) >= 2 else "pa"

    if args.test:
        # Test mode: call the configured wiki number (self-test)
        wiki_number = os.getenv("TWILIO_PHONE_NUMBER_WIKI", "")
        twilio_number = os.getenv("TWILIO_PHONE_NUMBER", "")
        if not wiki_number:
            print("✗ TWILIO_PHONE_NUMBER_WIKI not set in .env")
            sys.exit(1)
        caller = wiki_number
        callee = wiki_number  # Calls yourself on both legs for testing
        print("⚠ TEST MODE: Both legs will ring YOUR phone.")
        print("  You'll need to answer on two devices or use two numbers.\n")
    else:
        if not args.caller or not args.callee:
            parser.error("--caller and --callee are required (or use --test)")
        caller = args.caller
        callee = args.callee

    make_call(
        server_url=args.server,
        caller=caller,
        callee=callee,
        source_lang=source_lang,
        target_lang=target_lang,
    )


if __name__ == "__main__":
    main()
