"""
interview.py — Production v12.3

Changes vs v12.2:
  1. Fixed idk_count sync: elif -> standalone if with `is not None` check,
     so a count of 0 (after reset or on first answer) no longer silently drops.
"""

import json
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel

from app.services.resume_parser import parse_resume
from app.services.question_engine import (
    generate_first_question,
    generate_follow_up,
    generate_replacement_question,
)
from app.services.feedback_engine import generate_feedback
from app.services.groq_client import transcribe, text_to_speech

# ── v10: Transcription Normalizer ─────────────────────────────────────────────
#from app.services.transcription_normalizer import normalize_transcript, build_stt_prompt

from app.core.config import settings

router = APIRouter()

# ── In-memory cache + disk persistence ────────────────────────────────────────
_sessions: dict[str, dict] = {}
SESSION_DIR = Path("/tmp/interview_sessions")
SESSION_DIR.mkdir(parents=True, exist_ok=True)


def save_session(session_id: str, session: dict) -> None:
    try:
        with open(SESSION_DIR / f"{session_id}.json", "w") as f:
            json.dump(session, f, default=str)
    except Exception as e:
        print(f"[SESSION SAVE ERROR] {e}")


def load_session_disk(session_id: str) -> dict | None:
    try:
        p = SESSION_DIR / f"{session_id}.json"
        if p.exists():
            with open(p) as f:
                return json.load(f)
    except Exception as e:
        print(f"[SESSION LOAD ERROR] {e}")
    return None


def get_session(session_id: str) -> dict:
    s = _sessions.get(session_id)
    if not s:
        s = load_session_disk(session_id)
        if s:
            _sessions[session_id] = s
        else:
            raise HTTPException(404, f"Session '{session_id}' not found")
    return s


# ── Speech metrics ─────────────────────────────────────────────────────────────
FILLER_WORDS = [
    "um", "uh", "uhh", "umm", "like", "you know", "basically", "actually",
    "literally", "sort of", "kind of", "i mean", "right so", "so yeah",
    "okay so", "well um", "hmm", "er", "err",
]


def compute_speech_metrics(text: str) -> dict:
    t = text.lower().strip()

    hallucinations = [
        "thank you.", "thank you", "thanks for watching.", "thanks for watching",
        "amara.org", "subscribe.", "bye.", "bye",
    ]

    if not t or len(t) < 3 or t == "[no response]" or "forfeited" in t or t in hallucinations:
        return {
            "word_count": 0, "filler_count": 0, "filler_words_found": [],
            "repeated_words": [], "too_short": True, "too_long": False,
            "fluency_score": 0, "avg_sentence_length": 0,
        }

    words = t.split()
    word_count = len(words)

    filler_found: list[str] = []
    filler_count = 0
    for filler in FILLER_WORDS:
        pattern = r'\b' + re.escape(filler) + r'\b'
        matches = re.findall(pattern, t)
        if matches:
            filler_count += len(matches)
            filler_found.append(filler)

    repeated: list[str] = []
    for i in range(len(words) - 1):
        if words[i] == words[i + 1] and len(words[i]) > 2:
            repeated.append(words[i])

    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    avg_sentence_length = word_count / max(len(sentences), 1)
    fluency = max(0, min(100, 100 - (filler_count * 4) - (len(set(repeated)) * 6)))

    return {
        "word_count": word_count,
        "filler_count": filler_count,
        "filler_words_found": filler_found[:8],
        "repeated_words": list(set(repeated))[:5],
        "too_short": word_count < 25,
        "too_long": word_count > 350,
        "fluency_score": fluency,
        "avg_sentence_length": round(avg_sentence_length, 1),
    }


# ── Anti-Cheat (Fullscreen Exits) ─────────────────────────────────────────────

class FullscreenViolationRequest(BaseModel):
    session_id: str


@router.post("/fullscreen-violation")
async def fullscreen_violation(body: FullscreenViolationRequest):
    session = get_session(body.session_id)
    if session["status"] != "active":
        raise HTTPException(400, "Interview is not active")

    cheat_count = session.get("cheat_count", 0) + 1
    session["cheat_count"] = cheat_count

    session["misbehavior_log"].append({
        "question": session["current_question"],
        "reason":   "exited_fullscreen (cheat attempt)",
        "strike":   cheat_count,
    })
    session["counter_turns"] = []

    if cheat_count >= 3:
        session["status"] = "terminated"
        session["terminated"] = True
        session["termination_reason"] = "cheating"
        session["termination_message"] = (
            "Interview terminated due to repeated tab switching or exiting fullscreen."
        )
        save_session(body.session_id, session)

        audio_b64 = await text_to_speech(session["termination_message"])
        return {
            "done": True,
            "terminated": True,
            "termination_reason": "cheating",
            "termination_message": session["termination_message"],
            "misbehavior_count": cheat_count,
            "audio_b64": audio_b64,
        }

    next_q = generate_replacement_question(
        session["resume_data"],
        session["history"],
        session["current_question"],
    )

    reaction = (
        "I noticed you left the interview screen. "
        "Please stay in full-screen mode. Let's move to a different topic."
    )
    spoken    = f"{reaction} {next_q}".strip()
    audio_b64 = await text_to_speech(spoken)

    session["current_question"] = next_q
    session["current_reaction"] = reaction
    save_session(body.session_id, session)

    return {
        "done": False,
        "terminated": False,
        "reaction": reaction,
        "question": next_q,
        "question_number": session["question_count"],
        "audio_b64": audio_b64,
        "strike_issued": True,
        "misbehavior_count": cheat_count,
        "answer_quality": "screen_exit",
    }


# ── Speak (TTS) ────────────────────────────────────────────────────────────────

class SpeakRequest(BaseModel):
    text: str


@router.post("/speak")
async def speak(body: SpeakRequest):
    if not body.text.strip():
        raise HTTPException(400, "Empty text")
    audio_b64 = await text_to_speech(body.text)
    if audio_b64 is None:
        return {"audio_b64": None, "error": "TTS unavailable"}
    return {"audio_b64": audio_b64}


# ── Transcribe ─────────────────────────────────────────────────────────────────
# v10 CHANGE — Two-step pipeline:
#   Step 1: Send audio to Groq/Whisper → raw transcript
#   Step 2: normalize_transcript() fixes static mishearings (e.g. Enet10 → n8n)
#
# This endpoint does NOT have access to resume_data (no session context here),
# so only the static glossary is applied. The resume-aware second pass happens
# in /next-question where resume_data is available.

@router.post("/transcribe")
async def transcribe_audio(audio: UploadFile = File(...)):
    audio_bytes = await audio.read()

    if not audio_bytes or len(audio_bytes) < 500:
        print(
            f"[STT] Audio file too small "
            f"({len(audio_bytes) if audio_bytes else 0} bytes), treating as blank."
        )
        return {"transcript": "[no response]"}

    try:
        raw_text = transcribe(audio_bytes, filename=audio.filename or "answer.webm")
        print(f"[STT] Raw transcript: {raw_text[:120]}")

        # v10: Layer 2 static normalization
        clean_text = normalize_transcript(raw_text, resume_data=None)

        if clean_text != raw_text:
            print(f"[STT NORM] Fixed: {raw_text[:80]} → {clean_text[:80]}")

        return {"transcript": clean_text}

    except Exception as e:
        print(f"[STT ERROR] {e}")
        return {"transcript": "[no response]"}


# ── Start interview ────────────────────────────────────────────────────────────

@router.post("/start")
async def start_interview(
    resume: UploadFile = File(...),
    role: str = Form(...),
    level: str = Form(...),
):
    pdf_bytes = await resume.read()
    if not pdf_bytes:
        raise HTTPException(400, "Empty file")

    resume_data = parse_resume(pdf_bytes)
    if "error" in resume_data and "raw" not in resume_data:
        raise HTTPException(422, resume_data["error"])

    first_question = generate_first_question(resume_data, role, level)
    session_id     = str(uuid.uuid4())

    session = {
        "resume_data":         resume_data,
        "role":                role,
        "level":               level,
        "history":             [],
        "current_question":    first_question,
        "current_reaction":    "",
        "question_count":      1,
        "status":              "active",
        "counter_turns":       [],
        "cheat_count":         0,
        "evasive_count":       0,
        "idk_count":           0,
        "misbehavior_log":     [],
        "terminated":          False,
        "termination_message": "",
        "termination_reason":  None,
        "speech_metrics_log":  [],
    }
    _sessions[session_id] = session
    save_session(session_id, session)

    # v11 FIX: pre-generate TTS audio for the first question here so the
    # interview room can play it immediately without a second /speak roundtrip.
    # This is the same pattern /next-question and /fullscreen-violation use.
    # The setup page stores audio_b64 in sessionStorage and the interview room
    # reads it in speakTurn, keeping the audio-end → mic-open gap predictable.
    first_audio_b64 = await text_to_speech(first_question)

    return {
        "session_id":      session_id,
        "question":        first_question,
        "question_number": 1,
        "audio_b64":       first_audio_b64,
    }


# ── Next question ──────────────────────────────────────────────────────────────

class NextQuestionRequest(BaseModel):
    session_id: str
    transcript: str


@router.post("/next-question")
async def next_question(body: NextQuestionRequest):
    session = get_session(body.session_id)
    if session["status"] != "active":
        raise HTTPException(400, "Interview is not active")

    # v10: Layer 2 resume-aware normalization
    clean_transcript = normalize_transcript(
        body.transcript,
        resume_data=session["resume_data"],
    )
    if clean_transcript != body.transcript:
        print(
            f"[NORM resume-aware] "
            f"{body.transcript[:60]} → {clean_transcript[:60]}"
        )

    speech = compute_speech_metrics(clean_transcript)
    session["speech_metrics_log"].append({
        "question": session["current_question"],
        "metrics":  speech,
    })

    session["history"].append({
        "question":       session["current_question"],
        "answer":         clean_transcript,
        "speech_metrics": speech,
        "answer_quality": "pending",
    })
    session["counter_turns"] = []

    evasive_count: int = session.get("evasive_count", 0)
    idk_count: int     = session.get("idk_count", 0)

    result = generate_follow_up(
        resume_data=session["resume_data"],
        role=session["role"],
        level=session["level"],
        history=session["history"],
        misbehavior_count=evasive_count,
        idk_count=idk_count,
    )

    if result is not None:
        session["history"][-1]["answer_quality"] = result.get("answer_quality", "ok")
    else:
        session["history"][-1]["answer_quality"] = "ok"

    if result is None:
        session["status"] = "completed"
        save_session(body.session_id, session)
        return {"done": True, "terminated": False}

    if result.get("idk_count") is not None:
        session["idk_count"] = result["idk_count"]

    if result.get("termination_reason") == "repeated_idk":
        session["status"] = "terminated"
        session["terminated"] = True
        session["termination_reason"] = "repeated_idk"
        session["termination_message"] = (
            "Interview terminated due to repeated 'I don't know' responses without attempting to reason."
        )
        save_session(body.session_id, session)
        audio_b64 = await text_to_speech(session["termination_message"])
        return {
            "done": True,
            "terminated": True,
            "termination_message": session["termination_message"],
            "termination_reason": "repeated_idk",
            "audio_b64": audio_b64,
        }

    if result.get("terminated") and result.get("termination_reason") == "conduct_strikes":
        evasive_count = result.get("misbehavior_count", evasive_count + 1)
        session["evasive_count"] = evasive_count
        session["status"] = "terminated"
        session["terminated"] = True
        session["termination_reason"] = "conduct_strikes"
        session["termination_message"] = result.get("reaction", "Interview terminated.")
        save_session(body.session_id, session)
        audio_b64 = await text_to_speech(session["termination_message"])
        return {
            "done": True,
            "terminated": True,
            "termination_message": session["termination_message"],
            "termination_reason": "conduct_strikes",
            "audio_b64": audio_b64,
        }

    answer_quality = result.get("answer_quality", "ok")

    is_misbehavior = answer_quality in ("gibberish", "evasive", "rude", "fabricated")
    strike_issued  = False

    if is_misbehavior:
        evasive_count += 1
        session["evasive_count"] = evasive_count

        strike_reason = {
            "rude":       "hostile/unprofessional answer",
            "fabricated": "fabricated/invented experience",
            "evasive":    "evasive answer",
            "gibberish":  "incoherent/gibberish answer",
        }.get(answer_quality, f"{answer_quality} answer")

        session["misbehavior_log"].append({
            "question": session["current_question"],
            "reason":   strike_reason,
            "strike":   evasive_count,
        })
        strike_issued = True

    reaction  = result.get("reaction", "")
    next_q    = result.get("question", "")
    spoken    = f"{reaction} {next_q}".strip() if reaction else next_q
    audio_b64 = await text_to_speech(spoken)

    session["current_question"] = next_q
    session["current_reaction"] = reaction
    session["question_count"]  += 1
    save_session(body.session_id, session)

    return {
        "done":              False,
        "terminated":        False,
        "reaction":          reaction,
        "question":          next_q,
        "question_number":   session["question_count"],
        "audio_b64":         audio_b64,
        "strike_issued":     strike_issued,
        "misbehavior_count": evasive_count,
        "answer_quality":    answer_quality,
        "idk_count":         session.get("idk_count", 0),
    }


# ── Counter ────────────────────────────────────────────────────────────────────

class CounterRequest(BaseModel):
    session_id: str
    counter_text: str


@router.post("/counter")
async def counter(body: CounterRequest):
    session = get_session(body.session_id)
    if session["status"] != "active":
        raise HTTPException(400, "Interview is not active")

    turns = session.get("counter_turns", [])
    turns.append({"role": "candidate", "text": body.counter_text})
    session["counter_turns"] = turns

    max_counters  = 2
    force_move_on = len(turns) >= max_counters * 2

    from app.services.groq_client import chat

    context = f"""You are a senior technical interviewer. You are firm and direct, but FAIR and REASONABLE.
You just asked: "{session['current_question']}"
Your previous reaction was: "{session['current_reaction']}"
The candidate pushed back with: "{body.counter_text}"
Counter exchange: {len(turns)} message(s)
Force move on: {force_move_on}

CRITICAL RULE ON CORRECTIONS:
This interview uses a Speech-to-Text engine. It frequently mishears technical terms.
If the candidate is correcting a misheard word or clarifying a misunderstanding:
1. Accept the correction instantly and gracefully.
2. Acknowledge the mix-up in ONE brief sentence.
3. ACTION must be 'repeat_question' OR 'move_on'.

Respond with ONLY:
RESPONSE: [your reply]
ACTION: repeat_question OR move_on
"""

    raw = chat([{"role": "user", "content": context}], temperature=0.6)

    action = "move_on"
    if "REPEAT_QUESTION" in raw.upper():
        action = "repeat_question"

    clean_text = re.sub(r'ACTION:\s*(repeat_question|move_on)', '', raw, flags=re.IGNORECASE)
    clean_text = re.sub(r'RESPONSE:\s*', '', clean_text, flags=re.IGNORECASE)
    ai_response = clean_text.strip().strip('"\'')

    if not ai_response:
        ai_response = "I understand. Let's continue."
    if force_move_on:
        action = "move_on"

    spoken = ai_response
    if action == "repeat_question":
        spoken = f"{ai_response} {session['current_question']}"

    audio_b64 = await text_to_speech(spoken)

    turns.append({"role": "ai", "text": ai_response})
    session["counter_turns"] = turns
    save_session(body.session_id, session)

    return {
        "ai_response":  ai_response,
        "action":       action,
        "question":     session["current_question"],
        "audio_b64":    audio_b64,
        "force_ended":  force_move_on,
    }


# ── End interview ──────────────────────────────────────────────────────────────

class EndRequest(BaseModel):
    session_id: str
    reason: str | None = None


@router.post("/end")
async def end_interview(body: EndRequest):
    session = get_session(body.session_id)
    if session["status"] == "active":
        if body.reason:
            session["status"]              = "terminated"
            session["terminated"]          = True
            session["termination_reason"]  = "cheating"
            session["termination_message"] = body.reason
            session["cheat_count"]         = 3
        else:
            session["status"] = "completed"
    save_session(body.session_id, session)
    return {"message": "Interview ended", "session_id": body.session_id}


# ── Report ─────────────────────────────────────────────────────────────────────

@router.get("/report/{session_id}")
async def get_report(session_id: str):
    session       = get_session(session_id)
    is_terminated = session.get("terminated", False)

    if not session["history"]:
        return {
            "session_id":   session_id,
            "role":         session["role"],
            "level":        session["level"],
            "questions_answered": 0,
            "misbehavior_count":  session.get("cheat_count", 0) + session.get("evasive_count", 0),
            "terminated":         is_terminated,
            "termination_reason": session.get("termination_reason"),
            "termination_message": session.get(
                "termination_message",
                "Interview ended before answering any questions.",
            ),
            "idk_count":          session.get("idk_count", 0),
            "misbehavior_log":    session.get("misbehavior_log", []),
            "speech_metrics_log": [],
            "feedback": {
                "overall_score": 0, "confidence_score": 0, "clarity_score": 0,
                "technical_depth_score": 0, "communication_score": 0,
                "behaviour_score": 0, "speech_clarity_score": 0,
                "executive_summary":
                    "The interview ended before any valid answers were recorded. "
                    "No performance data is available.",
                "speech_summary":         "No speech data recorded.",
                "confidence_calibration": "under-confident",
                "confidence_note":
                    "Interview ended before meaningful data could be collected.",
                "strengths": [], "improvements": [], "behavioral_flags": [],
                "question_feedback": [], "learning_path": [],
            },
        }

    feedback = generate_feedback(
        resume_data=session["resume_data"],
        role=session["role"],
        history=session["history"],
        misbehavior_log=session.get("misbehavior_log"),
        speech_metrics_log=session.get("speech_metrics_log", []),
        is_terminated=is_terminated,
        termination_reason=session.get("termination_reason"),
    )

    return {
        "session_id":         session_id,
        "role":               session["role"],
        "level":              session["level"],
        "questions_answered": len(session["history"]),
        "misbehavior_count":  session.get("cheat_count", 0) + session.get("evasive_count", 0),
        "idk_count":          session.get("idk_count", 0),
        "terminated":         session.get("terminated", False),
        "termination_reason": session.get("termination_reason"),
        "termination_message": session.get("termination_message", ""),
        "misbehavior_log":    session.get("misbehavior_log", []),
        "speech_metrics_log": session.get("speech_metrics_log", []),
        "feedback":           feedback,
    }


@router.get("/session/{session_id}")
async def get_session_state(session_id: str):
    session = get_session(session_id)
    return {
        "session_id":        session_id,
        "status":            session["status"],
        "question_count":    session["question_count"],
        "current_question":  session.get("current_question"),
        "role":              session["role"],
        "level":             session["level"],
        "misbehavior_count": session.get("cheat_count", 0),
        "idk_count":         session.get("idk_count", 0),
    }
