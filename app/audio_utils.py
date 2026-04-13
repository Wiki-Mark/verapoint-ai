"""
VeraPoint.ai — Audio Utilities
Handles conversion between audio formats:
  - G.711 µ-law (Twilio's native format, 8kHz, 8-bit)
  - Linear PCM (16-bit, various sample rates)
  - Base64 encoding/decoding for Twilio WebSocket messages
"""

import base64
import struct
import io
import logging

logger = logging.getLogger(__name__)

# ─── µ-law codec tables ───────────────────────────────────────────
# ITU-T G.711 µ-law encoding/decoding

MULAW_BIAS = 0x84
MULAW_CLIP = 32635
MULAW_MAX = 0x1FFF

# Pre-computed µ-law to PCM16 lookup table (256 entries)
_MULAW_TO_PCM16 = []
for i in range(256):
    val = ~i
    sign = val & 0x80
    exponent = (val >> 4) & 0x07
    mantissa = val & 0x0F
    sample = ((mantissa << 3) + MULAW_BIAS) << exponent
    sample -= MULAW_BIAS
    if sign:
        sample = -sample
    _MULAW_TO_PCM16.append(sample)


def mulaw_decode(mulaw_byte: int) -> int:
    """Decode a single µ-law byte to a 16-bit PCM sample."""
    return _MULAW_TO_PCM16[mulaw_byte & 0xFF]


def pcm16_to_mulaw(sample: int) -> int:
    """Encode a single 16-bit PCM sample to a µ-law byte."""
    sign = 0
    if sample < 0:
        sign = 0x80
        sample = -sample
    if sample > MULAW_CLIP:
        sample = MULAW_CLIP
    sample += MULAW_BIAS

    exponent = 7
    mask = 0x4000
    while exponent > 0:
        if sample & mask:
            break
        exponent -= 1
        mask >>= 1

    mantissa = (sample >> (exponent + 3)) & 0x0F
    mulaw_byte = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return mulaw_byte


def mulaw_to_pcm(mulaw_data: bytes) -> bytes:
    """
    Convert G.711 µ-law audio bytes to 16-bit linear PCM.
    Input: bytes of µ-law encoded audio (8kHz, 8-bit)
    Output: bytes of PCM16 audio (8kHz, 16-bit little-endian)
    """
    pcm_samples = []
    for byte in mulaw_data:
        pcm_samples.append(_MULAW_TO_PCM16[byte])
    return struct.pack(f"<{len(pcm_samples)}h", *pcm_samples)


def pcm_to_mulaw(pcm_data: bytes) -> bytes:
    """
    Convert 16-bit linear PCM audio to G.711 µ-law.
    Input: bytes of PCM16 audio (8kHz, 16-bit little-endian)
    Output: bytes of µ-law encoded audio (8kHz, 8-bit)
    """
    num_samples = len(pcm_data) // 2
    samples = struct.unpack(f"<{num_samples}h", pcm_data)
    mulaw_bytes = bytearray(num_samples)
    for i, sample in enumerate(samples):
        mulaw_bytes[i] = pcm16_to_mulaw(sample)
    return bytes(mulaw_bytes)


def base64_to_mulaw(b64_data: str) -> bytes:
    """Decode base64 string (from Twilio WebSocket) to raw µ-law bytes."""
    return base64.b64decode(b64_data)


def mulaw_to_base64(mulaw_data: bytes) -> str:
    """Encode raw µ-law bytes to base64 string (for Twilio WebSocket)."""
    return base64.b64encode(mulaw_data).decode("ascii")


def pcm_to_base64_mulaw(pcm_data: bytes) -> str:
    """Convert PCM audio to base64-encoded µ-law (ready for Twilio)."""
    mulaw = pcm_to_mulaw(pcm_data)
    return mulaw_to_base64(mulaw)


def resample_linear(pcm_data: bytes, from_rate: int, to_rate: int) -> bytes:
    """
    Simple linear interpolation resampling for PCM16 audio.
    For production, consider using a proper resampling library (e.g. scipy).

    Args:
        pcm_data: 16-bit PCM audio bytes (little-endian)
        from_rate: Source sample rate (e.g. 22050, 24000, 44100)
        to_rate: Target sample rate (e.g. 8000 for Twilio)
    Returns:
        Resampled PCM16 bytes
    """
    if from_rate == to_rate:
        return pcm_data

    num_samples = len(pcm_data) // 2
    if num_samples == 0:
        return b""

    samples = struct.unpack(f"<{num_samples}h", pcm_data)
    ratio = from_rate / to_rate
    new_length = int(num_samples / ratio)

    resampled = []
    for i in range(new_length):
        src_pos = i * ratio
        idx = int(src_pos)
        frac = src_pos - idx

        if idx + 1 < num_samples:
            sample = int(samples[idx] * (1 - frac) + samples[idx + 1] * frac)
        else:
            sample = samples[idx] if idx < num_samples else 0

        # Clamp to int16 range
        sample = max(-32768, min(32767, sample))
        resampled.append(sample)

    return struct.pack(f"<{len(resampled)}h", *resampled)


def chunk_audio(audio_data: bytes, chunk_size: int = 640) -> list[bytes]:
    """
    Split audio data into chunks suitable for Twilio Media Stream.
    Twilio expects 20ms chunks at 8kHz µ-law = 160 bytes per chunk.
    Default chunk_size of 640 = 4 chunks of 160 = 80ms of audio.
    """
    chunks = []
    for i in range(0, len(audio_data), chunk_size):
        chunk = audio_data[i:i + chunk_size]
        if chunk:
            chunks.append(chunk)
    return chunks


def calculate_audio_duration_ms(audio_bytes: bytes, sample_rate: int = 8000, sample_width: int = 1) -> float:
    """Calculate duration in milliseconds of audio data."""
    if not audio_bytes:
        return 0.0
    num_samples = len(audio_bytes) / sample_width
    return (num_samples / sample_rate) * 1000
