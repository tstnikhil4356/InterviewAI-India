"""
transcription_normalizer.py — v2.0

Structural-only cleanup. Semantic/technical correction (phonetic mishearings,
garbled tool names, capitalisation) is handled by the LLM prompts via
STT-awareness instructions in question_engine.py and feedback_engine.py.

What this file still handles (things the LLM can't fix):
  - Whisper hallucinations ("Thank you for watching", "Subscribe", etc.)
    that pollute the prompt before the LLM ever sees them.
  - Structural noise: smart-quotes, repeated words, excess whitespace.
  - Empty / whitespace-only transcripts.

Layer 1 (STT hinting) is preserved — injecting a vocabulary hint into the
STT call before transcription is still worth doing. It's zero-latency and
reduces garbling at the source, which means less work for the LLM.

Usage
-----
    from app.services.transcription_normalizer import (
        build_stt_prompt,
        normalize_transcript,
    )

    # Before transcribing — pass hint to your STT call
    hint = build_stt_prompt(resume_data)

    # Whisper
    result = whisper_model.transcribe(audio_path, initial_prompt=hint)
    raw_text = result["text"]

    # Deepgram
    # options = PrerecordedOptions(keywords=hint.split(", "))

    # AssemblyAI
    # config = aai.TranscriptionConfig(word_boost=hint.split(", "))

    # After transcribing — structural cleanup only
    clean_text = normalize_transcript(raw_text, resume_data)
"""

import re
import logging

logger = logging.getLogger(__name__)


# ── Layer 1: STT Hinting ──────────────────────────────────────────────────────
# Injected into the STT call BEFORE transcription.
# Biases the STT model toward recognising known tech terms at the source.
# This reduces garbling — less for the LLM to correct downstream.

_STATIC_HINT_TERMS: list[str] = [
    "n8n", "scikit-learn", "PyTorch", "TensorFlow", "LangChain", "LlamaIndex",
    "FastAPI", "Next.js", "Node.js", "Vue.js", "TypeScript", "JavaScript",
    "PostgreSQL", "MongoDB", "Redis", "Elasticsearch", "Supabase", "Firebase",
    "Kubernetes", "Dockerfile", "Terraform", "Ansible", "CI/CD",
    "GraphQL", "WebSockets", "RESTful", "Kafka", "RabbitMQ", "Celery",
    "Grafana", "Prometheus", "Datadog", "Sentry", "Pinecone", "ChromaDB",
    "Hugging Face", "OpenAI", "Vercel", "Netlify", "Figma", "Jira",
    "Confluence", "GitHub", "GitLab", "Zapier", "Make", "Apify", "Airtable",
    "Retool", "Bubble", "Webflow", "Playwright", "Selenium", "Puppeteer",
]


def _extract_resume_keywords(resume_data: dict) -> list[str]:
    """
    Pull every tech keyword out of the resume so resume-specific tools
    (e.g. a proprietary internal tool) also get hinted to the STT engine.
    """
    keywords: set[str] = set()

    skills = resume_data.get("skills") or []
    if isinstance(skills, dict):
        for items in skills.values():
            if isinstance(items, list):
                keywords.update(items)
    elif isinstance(skills, list):
        keywords.update(skills)

    for p in resume_data.get("projects", []):
        if p.get("name"):
            keywords.add(p["name"])
        for t in p.get("tech_stack", []):
            keywords.add(t)

    for e in resume_data.get("experience", []):
        if e.get("company"):
            keywords.add(e["company"])

    stopwords = {"and", "the", "for", "with", "using", "at", "of", "in", "a", "an"}
    return [
        kw.strip()
        for kw in keywords
        if kw.strip() and kw.strip().lower() not in stopwords and len(kw.strip()) <= 25
    ]


def build_stt_prompt(resume_data: dict | None = None) -> str:
    """
    Returns a comma-separated hint string to inject into your STT call.

        Whisper    → whisper_model.transcribe(audio, initial_prompt=hint)
        Deepgram   → PrerecordedOptions(keywords=hint.split(", "))
        AssemblyAI → aai.TranscriptionConfig(word_boost=hint.split(", "))

    Combines static known-tech terms with dynamic keywords from the resume.
    Resume keywords are prepended — STT models weight earlier hints more.
    """
    terms = list(_STATIC_HINT_TERMS)

    if resume_data:
        resume_keywords = _extract_resume_keywords(resume_data)
        terms = resume_keywords + [t for t in terms if t not in resume_keywords]

    seen: set[str] = set()
    unique: list[str] = []
    for t in terms:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            unique.append(t)

    hint = ", ".join(unique)
    logger.debug("STT hint built (%d terms): %s…", len(unique), hint[:120])
    return hint


# ── Layer 2: Structural Cleanup ───────────────────────────────────────────────
# Runs after transcription. Fixes only things the LLM cannot fix itself:
#   - Hallucinated boilerplate Whisper emits on silence/noise
#   - Smart quotes / Unicode punctuation → ASCII
#   - Repeated consecutive words ("I I used used Redis")
#   - Excess whitespace
#
# Deliberately does NOT fix phonetic mishearings or capitalisation —
# the LLM prompts handle that with full context.

# Phrases Whisper commonly hallucinates on silence, music, or background noise.
_HALLUCINATION_PHRASES: frozenset[str] = frozenset({
    "thank you for watching",
    "thanks for watching",
    "please subscribe",
    "like and subscribe",
    "don't forget to subscribe",
    "see you in the next video",
    "see you next time",
    "bye bye",
    "subtitles by",
    "subtitles were",
    "captions by",
    "transcribed by",
    "translated by",
    "amara.org",
    "www.",
    "http",
    "[music]",
    "[applause]",
    "[laughter]",
    "♪",
})

# Very short responses that are almost certainly hallucinated silence fillers.
_HALLUCINATION_SHORT: frozenset[str] = frozenset({
    "you", "hmm", "hm", "uh", "um", "ah", "oh", "okay", "ok",
    "yes", "no", "yeah", "right", "sure", "so", "well", "like",
    "you know", "i mean", "you see",
})

# Smart quote / dash normalisation map
_SMART_CHAR_MAP: dict[str, str] = {
    "\u2018": "'", "\u2019": "'",   # ' '
    "\u201c": '"', "\u201d": '"',   # " "
    "\u2013": "-", "\u2014": "-",   # – —
    "\u2026": "...",                # …
    "\u00a0": " ",                  # non-breaking space
}

_SMART_CHAR_RE = re.compile("|".join(re.escape(k) for k in _SMART_CHAR_MAP))
_REPEATED_WORD_RE = re.compile(r'\b(\w+)(\s+\1){2,}\b', re.IGNORECASE)


def _fix_smart_chars(text: str) -> str:
    return _SMART_CHAR_RE.sub(lambda m: _SMART_CHAR_MAP[m.group(0)], text)


def _is_hallucination(text: str) -> bool:
    """Return True if the entire transcript is a Whisper hallucination."""
    t = text.strip().lower()

    if not t:
        return True

    # Exact match against known short hallucinations
    if t in _HALLUCINATION_SHORT:
        return True

    # Contains a known hallucination phrase
    if any(phrase in t for phrase in _HALLUCINATION_PHRASES):
        return True

    # Pure punctuation / symbols
    if re.fullmatch(r'[\W_]+', t):
        return True

    return False


def _fix_repeated(text: str) -> str:
    """
    Collapse runs of 3+ identical consecutive words to a single instance.
    e.g. "I I I used Redis Redis" → "I I used Redis Redis" (only 3+ runs collapsed)
    Leaves natural stutters like "I I" alone — the LLM handles those fine.
    """
    return _REPEATED_WORD_RE.sub(r'\1', text)


def normalize_transcript(text: str, resume_data: dict | None = None) -> str:
    """
    Structural cleanup only. Returns "[no response]" for empty/hallucinated input.

    Does NOT fix:
      - Phonetic mishearings  → handled by LLM STT-awareness prompts
      - Capitalisation errors → handled by LLM STT-awareness prompts
      - Novel tech term garbling → handled by LLM STT-awareness prompts

    resume_data param kept for API compatibility but is no longer used here.
    """
    if not text or not isinstance(text, str) or not text.strip():
        return "[no response]"

    t = _fix_smart_chars(text).strip()

    if _is_hallucination(t):
        logger.debug("normalize_transcript: hallucination detected — returning [no response]")
        return "[no response]"

    # Collapse 3+ repeated consecutive words
    t = _fix_repeated(t)

    # Normalise whitespace
    t = re.sub(r"[ \t]{2,}", " ", t).strip()
    t = re.sub(r"\n{3,}", "\n\n", t)

    # Final hallucination check (repeated-word collapse may expose a bare hallucination)
    if _is_hallucination(t):
        return "[no response]"

    return t


# ── Convenience wrapper ───────────────────────────────────────────────────────

def transcribe_and_normalize(
    raw_transcript: str,
    resume_data: dict | None = None,
) -> str:
    """
    Drop-in wrapper. Call immediately after getting the raw STT output.

        raw   = whisper_model.transcribe(audio)["text"]
        clean = transcribe_and_normalize(raw, resume_data)
    """
    return normalize_transcript(raw_transcript, resume_data)