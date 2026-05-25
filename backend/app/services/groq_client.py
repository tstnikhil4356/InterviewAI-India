import base64
import os
from groq import Groq
from dotenv import load_dotenv

load_dotenv()


def _get_client() -> Groq:
    """Lazy init — reads key fresh every call so dotenv timing never bites."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY is missing. Add it to backend/.env\n"
            "Get a free key at https://console.groq.com"
        )
    return Groq(api_key=api_key)


LLM_MODEL     = "llama-3.3-70b-versatile"
WHISPER_MODEL = "whisper-large-v3"
TTS_MODEL     = "playai-tts"


def chat(messages: list[dict], temperature: float = 0.7) -> str:
    """LLM call — returns text."""
    response = _get_client().chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=1024,
    )
    return response.choices[0].message.content.strip()


def transcribe(audio_bytes: bytes, filename: str = "answer.webm") -> str:
    """Whisper STT — returns transcript text."""
    result = _get_client().audio.transcriptions.create(
        file=(filename, audio_bytes, "audio/webm"),
        model=WHISPER_MODEL,
        language="en",
    )
    return result.text.strip()


async def text_to_speech(text: str) -> str | None:
    """
    Edge TTS — Microsoft neural voices, free, no API key.
    Returns base64-encoded MP3 string, or None on failure.
    """
    try:
        import edge_tts
        import io

        voice = "en-US-ChristopherNeural"   # cold, authoritative interviewer
        communicate = edge_tts.Communicate(text, voice)

        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])

        audio_bytes = buf.getvalue()
        if not audio_bytes:
            print("[TTS] Edge TTS returned empty audio")
            return None

        b64 = base64.b64encode(audio_bytes).decode("utf-8")
        print(f"[TTS] Edge TTS generated {len(audio_bytes)} bytes")
        return b64

    except Exception as e:
        print(f"[TTS ERROR] {type(e).__name__}: {e}")
        return None
