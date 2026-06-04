# Add this line to your Settings class in backend/app/core/config.py:
# TTS_VOICE: str = "Fritz-PlayAI"   # Options: Fritz-PlayAI, Celeste-PlayAI, Briggs-PlayAI

# Full updated class for reference:
class Settings:
    GROQ_API_KEY: str         = os.getenv("GROQ_API_KEY", "")
    SUPABASE_URL: str         = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")
    FRONTEND_URL: str         = os.getenv("FRONTEND_URL", "http://localhost:3000")

    LLM_MODEL: str            = "llama-3.3-70b-versatile"
    WHISPER_MODEL: str        = "whisper-large-v3"
    TTS_VOICE: str            = "Fritz-PlayAI"   # ← ADD THIS

    MIN_QUESTIONS: int        = 6
    MAX_QUESTIONS: int        = 10
