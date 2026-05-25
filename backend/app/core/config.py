import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    GROQ_API_KEY: str         = os.getenv("GROQ_API_KEY", "")
    SUPABASE_URL: str         = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")
    FRONTEND_URL: str         = os.getenv("FRONTEND_URL", "http://localhost:3000")

    # Groq model IDs
    LLM_MODEL: str            = "llama-3.3-70b-versatile"
    WHISPER_MODEL: str        = "whisper-large-v3"
    TTS_VOICE: str            = "en-IN-PrabhatNeural"  # Options: Fritz-PlayAI, Celeste-PlayAI, Briggs-PlayAI

    # Interview settings
    MIN_QUESTIONS: int        = 6
    MAX_QUESTIONS: int        = 10

settings = Settings()