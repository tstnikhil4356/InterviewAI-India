"""
interview.py — Fixed & Enhanced v5
Fixes:
  1. Solved the "previous-to-previous" question bug by passing the full `session["history"]`.
  2. Replaced fragile .splitlines() with robust Regex in the /counter endpoint.
  3. Gracefully handles STT misinterpretations (e.g., hearing "Chef" instead of "SHAP").
  4. /end now accepts a termination 'reason' from the frontend anti-cheat.
  5. /report now gracefully returns a blank report instead of a 400 error if terminated early.
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
    build_coverage,
    pick_next_target,
    _detect_topic,
    STRIKE_WARNINGS,
    MAX_MISBEHAVIOR,
)
from app.services.feedback_engine import generate_feedback
from app.services.groq_client import transcribe, text_to_speech
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


def _format_cheat_warning(count: int) -> str:
    if count <= 0:
        return ""
    return STRIKE_WARNINGS[min(count - 1, len(STRIKE_WARNINGS) - 1)] + " "


def _generate_next_question_after_cheat(session: dict) -> tuple[str, str]:
    current_question = session.get("current_question", "")
    resume_data = session["resume_data"]
    history = session["history"]
    last_topic = _detect_topic(current_question, resume_data)

    coverage = build_coverage(resume_data, history)
    next_target = pick_next_target(
        coverage=coverage,
        questions_asked=len(history),
        history=history,
        resume_data=resume_data,
        force_skip_label=last_topic if last_topic else None,
    )

    next_question = next_target["instruction"]
    warning = _format_cheat_warning(session.get("misbehavior_count", 0))
    reaction = f"{warning}You left the interview. Let's continue with a new question."
    return next_question, reaction


def _register_cheat_event(session: dict, reason: str = "Tab switch or full-screen exit detected.") -> int:
    count = session.get("misbehavior_count", 0) + 1
    session["misbehavior_count"] = count
    session["misbehavior_log"].append({
        "question": session.get("current_question", "<unknown>"),
        "reason": reason,
        "strike": count,
    })
    session.setdefault("cheat_events", []).append({"reason": reason, "strike": count})
    return count


# ── Speech metrics ─────────────────────────────────────────────────────────────
FILLER_WORDS = [
    "um", "uh", "uhh", "umm", "like", "you know", "basically", "actually",
    "literally", "sort of", "kind of", "i mean", "right so", "so yeah",
    "okay so", "well um", "hmm", "er", "err",
]


def compute_speech_metrics(text: str) -> dict:
    if not text or len(text.strip()) < 3:
        return {
            "word_count": 0, "filler_count": 0, "filler_words_found": [],
            "repeated_words": [], "too_short": True, "too_long": False,
            "fluency_score": 0, "avg_sentence_length": 0,
        }

    lower = text.lower()
    words = lower.split()
    word_count = len(words)

    # Filler detection
    filler_found: list[str] = []
    filler_count = 0
    for filler in FILLER_WORDS:
        pattern = r'\b' + re.escape(filler) + r'\b'
        matches = re.findall(pattern, lower)
        if matches:
            filler_count += len(matches)
            filler_found.append(filler)

    # Stutter / repeated consecutive words
    repeated: list[str] = []
    for i in range(len(words) - 1):
        if words[i] == words[i + 1] and len(words[i]) > 2:
            repeated.append(words[i])

    # Sentence count for avg sentence length
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    avg_sentence_length = word_count / max(len(sentences), 1)

    # Fluency score: starts at 100, penalise fillers and repeats
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

@router.post("/transcribe")
async def transcribe_audio(audio: UploadFile = File(...)):
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(400, "Empty audio file")
    try:
        text = transcribe(audio_bytes, filename=audio.filename or "answer.webm")
        print(f"[STT] Transcribed: {text[:100]}")
    except Exception as e:
        print(f"[STT ERROR] {e}")
        raise HTTPException(500, f"Transcription failed: {e}")
    return {"transcript": text}


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
    session_id = str(uuid.uuid4())

    session = {
        "resume_data": resume_data,
        "role": role,
        "level": level,
        "history": [],
        "current_question": first_question,
        "current_reaction": "",
        "question_count": 1,
        "status": "active",
        "counter_turns": [],
        "misbehavior_count": 0,
        "misbehavior_log": [],
        "cheat_events": [],
        "skip_current_question": False,
        "terminated": False,
        "termination_message": "",
        "speech_metrics_log": [],
    }
    _sessions[session_id] = session
    save_session(session_id, session)

    return {
        "session_id": session_id,
        "question": first_question,
        "question_number": 1,
    }


class CheatRequest(BaseModel):
    session_id: str


@router.post("/cheat")
async def report_cheat(body: CheatRequest):
    session = get_session(body.session_id)
    if session["status"] != "active":
        raise HTTPException(400, "Interview is not active")

    new_count = _register_cheat_event(session)
    save_session(body.session_id, session)

    if new_count >= MAX_MISBEHAVIOR:
        session["status"] = "terminated"
        session["terminated"] = True
        session["termination_message"] = (
            "Interview automatically terminated due to repeated tab switches or exiting full-screen."
        )
        save_session(body.session_id, session)
        audio_b64 = await text_to_speech(session["termination_message"])
        return {
            "terminated": True,
            "termination_message": session["termination_message"],
            "misbehavior_count": new_count,
            "audio_b64": audio_b64,
        }

    # Skip the current question after the cheat event.
    skipped_question = session.get("current_question", "")
    session["history"].append({
        "question": skipped_question,
        "answer": "[skipped due to tab switch/full-screen exit]",
        "speech_metrics": compute_speech_metrics(""),
        "cheat_skip": True,
    })

    next_q, reaction = _generate_next_question_after_cheat(session)
    session["current_question"] = next_q
    session["current_reaction"] = reaction
    session["question_count"] = session.get("question_count", 1) + 1
    session["skip_current_question"] = False
    save_session(body.session_id, session)

    audio_b64 = await text_to_speech(f"{reaction} {next_q}".strip())
    return {
        "done": False,
        "terminated": False,
        "reaction": reaction,
        "question": next_q,
        "question_number": session["question_count"],
        "audio_b64": audio_b64,
        "strike_issued": True,
        "misbehavior_count": new_count,
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

    if session.get("skip_current_question"):
        session["skip_current_question"] = False
        skipped_question = session.get("current_question", "")
        session["history"].append({
            "question": skipped_question,
            "answer": "[skipped due to tab switch/full-screen exit]",
            "speech_metrics": compute_speech_metrics(""),
            "cheat_skip": True,
        })
        next_q, reaction = _generate_next_question_after_cheat(session)
        session["current_question"] = next_q
        session["current_reaction"] = reaction
        session["question_count"] = session.get("question_count", 1) + 1
        session["counter_turns"] = []
        save_session(body.session_id, session)
        audio_b64 = await text_to_speech(f"{reaction} {next_q}".strip())
        return {
            "done": False,
            "terminated": False,
            "reaction": reaction,
            "question": next_q,
            "question_number": session["question_count"],
            "audio_b64": audio_b64,
            "strike_issued": False,
            "misbehavior_count": session.get("misbehavior_count", 0),
            "answer_quality": "ok",
        }

    # Compute speech metrics for this answer
    speech = compute_speech_metrics(body.transcript)
    session["speech_metrics_log"].append({
        "question": session["current_question"],
        "metrics": speech,
    })

    # Save current answer to history
    session["history"].append({
        "question": session["current_question"],
        "answer": body.transcript,
        "speech_metrics": speech,
    })
    session["counter_turns"] = []

    misbehavior_count: int = session.get("misbehavior_count", 0)

    result = generate_follow_up(
        resume_data=session["resume_data"],
        role=session["role"],
        level=session["level"],
        history=session["history"],
        misbehavior_count=misbehavior_count,
    )

    # ── Termination by interviewer ─────────────────────────────────────────
    if result is not None and result.get("terminated"):
        session["misbehavior_count"] = misbehavior_count + 1
        session["misbehavior_log"].append({
            "question": session["current_question"],
            "reason": result.get("answer_quality", "unacceptable"),
            "strike": session["misbehavior_count"],
        })
        session["status"] = "terminated"
        session["terminated"] = True
        session["termination_message"] = result.get("reaction", "Interview terminated.")
        save_session(body.session_id, session)

        # TTS the termination message
        audio_b64 = await text_to_speech(result["reaction"])
        return {
            "done": True,
            "terminated": True,
            "termination_message": result["reaction"],
            "misbehavior_count": session["misbehavior_count"],
            "audio_b64": audio_b64,
        }

    # ── Natural end ───────────────────────────────────────────────────────
    if result is None:
        session["status"] = "completed"
        save_session(body.session_id, session)
        return {"done": True, "terminated": False}

    # ── Misbehavior strike (non-terminal) ──────────────────────────────────
    answer_quality = result.get("answer_quality", "ok")
    is_misbehavior = answer_quality in ("gibberish", "evasive")
    strike_issued = False
    new_strike_count = misbehavior_count

    if is_misbehavior:
        new_strike_count = misbehavior_count + 1
        session["misbehavior_count"] = new_strike_count
        session["misbehavior_log"].append({
            "question": session["current_question"],
            "reason": answer_quality,
            "strike": new_strike_count,
        })
        strike_issued = True
        print(f"[STRIKE] {new_strike_count}/4 — answer was {answer_quality}")

    reaction = result.get("reaction", "")
    next_q   = result.get("question", "")
    spoken   = f"{reaction} {next_q}".strip() if reaction else next_q

    audio_b64 = await text_to_speech(spoken)

    session["current_question"] = next_q
    session["current_reaction"] = reaction
    session["question_count"] += 1
    save_session(body.session_id, session)

    return {
        "done": False,
        "terminated": False,
        "reaction": reaction,
        "question": next_q,
        "question_number": session["question_count"],
        "audio_b64": audio_b64,
        "strike_issued": strike_issued,
        "misbehavior_count": new_strike_count,
        "answer_quality": answer_quality,
    }


# ── Counter (pushback on AI reaction) ─────────────────────────────────────────

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

    max_counters = 2
    force_move_on = len(turns) >= max_counters * 2

    from app.services.groq_client import chat

    context = f"""You are a senior technical interviewer. You are firm and direct, but FAIR and REASONABLE.
You just asked: "{session['current_question']}"  
Your previous reaction was: "{session['current_reaction']}"  
The candidate pushed back with: "{body.counter_text}"  
Counter exchange: {len(turns)} message(s)  
Force move on: {force_move_on}  

CRITICAL RULE ON CORRECTIONS: 
This interview uses a Speech-to-Text engine. It frequently mishears technical terms (e.g., hearing "Chef" instead of "SHAP", "React" instead of "Read that", etc.). 
If the candidate is correcting a misheard word or clarifying a misunderstanding:
1. Accept the correction instantly and gracefully.
2. Acknowledge the mix-up in ONE brief sentence (e.g., "Ah, my mistake, the audio cut out. Tell me about SHAP then.").
3. ACTION must be 'repeat_question' (rephrased using their corrected term) OR 'move_on'.
Do NOT accuse them of changing their story or lying if it is clearly a phonetic STT error.

However, if they are genuinely deflecting, stalling, or refusing to answer (and not correcting a typo), call it out directly and firmly.

{"Since this has gone on long enough, firmly shut it down and move to the next topic." if force_move_on else ""}  

RULES:
- Maximum 2 sentences.
- Speak naturally and conversationally.
- Never use the word "acknowledge" or "capitulate". Just speak normally.

Respond with ONLY:  
RESPONSE: [your reply]  
ACTION: repeat_question OR move_on  
"""

    raw = chat([{"role": "user", "content": context}], temperature=0.6)

    # 1. Determine the action safely, no matter where it is in the string
    action = "move_on"
    if "REPEAT_QUESTION" in raw.upper():
        action = "repeat_question"

    # 2. Strip out the internal instruction tags using Regex
    clean_text = raw
    clean_text = re.sub(r'ACTION:\s*(repeat_question|move_on)', '', clean_text, flags=re.IGNORECASE)
    clean_text = re.sub(r'RESPONSE:\s*', '', clean_text, flags=re.IGNORECASE)

    # 3. Clean up any leftover whitespace or stray quotes
    ai_response = clean_text.strip().strip('"\'')

    # Fallback in case the LLM completely glitches out
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
        "ai_response": ai_response,
        "action": action,
        "question": session["current_question"],
        "audio_b64": audio_b64,
        "force_ended": force_move_on,
    }


# ── End interview ──────────────────────────────────────────────────────────────

class EndRequest(BaseModel):
    session_id: str
    reason: str | None = None  # Allow frontend to pass termination reason


@router.post("/end")
async def end_interview(body: EndRequest):
    session = get_session(body.session_id)
    if session["status"] == "active":
        if body.reason:
            session["status"] = "terminated"
            session["terminated"] = True
            session["termination_message"] = body.reason
            session["misbehavior_count"] = 3
        else:
            session["status"] = "completed"
    save_session(body.session_id, session)
    return {"message": "Interview ended", "session_id": body.session_id}


# ── Report ─────────────────────────────────────────────────────────────────────

@router.get("/report/{session_id}")
async def get_report(session_id: str):
    session = get_session(session_id)
    is_terminated = session.get("terminated", False)

    # If terminated with zero history, gracefully return a blank report
    # instead of crashing with a 400 error.
    if not session["history"]:
        return {
            "session_id": session_id,
            "role": session["role"],
            "level": session["level"],
            "questions_answered": 0,
            "misbehavior_count": session.get("misbehavior_count", 0),
            "terminated": is_terminated,
            "termination_message": session.get("termination_message", "Interview ended before answering any questions."),
            "misbehavior_log": session.get("misbehavior_log", []),
            "speech_metrics_log": [],
            "feedback": {
                "overall_score": 0,
                "confidence_score": 0,
                "clarity_score": 0,
                "technical_depth_score": 0,
                "communication_score": 0,
                "behaviour_score": 0,
                "speech_clarity_score": 0,
                "summary": "The interview ended before any answers were recorded. No performance data is available.",
                "speech_summary": "No speech data recorded.",
                "confidence_calibration": "under-confident",
                "confidence_note": "Interview ended before meaningful data could be collected.",
                "strengths": [],
                "improvements": ["Complete at least one full answer before the interview can be assessed."],
                "behavioral_flags": [],
                "question_feedback": [],
                "recommended_topics": [],
            },
        }

    feedback = generate_feedback(
        resume_data=session["resume_data"],
        role=session["role"],
        history=session["history"],
        misbehavior_log=session.get("misbehavior_log"),
        speech_metrics_log=session.get("speech_metrics_log", []),
        is_terminated=is_terminated,
    )

    return {
        "session_id": session_id,
        "role": session["role"],
        "level": session["level"],
        "questions_answered": len(session["history"]),
        "misbehavior_count": session.get("misbehavior_count", 0),
        "terminated": session.get("terminated", False),
        "termination_message": session.get("termination_message", ""),
        "misbehavior_log": session.get("misbehavior_log", []),
        "speech_metrics_log": session.get("speech_metrics_log", []),
        "feedback": feedback,
    }


# ── Session state ──────────────────────────────────────────────────────────────

@router.get("/session/{session_id}")
async def get_session_state(session_id: str):
    session = get_session(session_id)
    return {
        "session_id": session_id,
        "status": session["status"],
        "question_count": session["question_count"],
        "current_question": session.get("current_question"),
        "role": session["role"],
        "level": session["level"],
        "misbehavior_count": session.get("misbehavior_count", 0),
    }