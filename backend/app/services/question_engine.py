"""
question_engine.py — Production v15.4

Changes vs v15.3:
  1. "admitted_gap" formal label added to prevent honest IDKs from failing
     into the "evasive" bucket and triggering conduct strikes.
  2. generate_follow_up() safely routes "admitted_gap" to increment idk_count
     and naturally pivot the conversation.
  3. parse_response() truncates malformed questions containing resume recaps.
"""

import re
import time
import json
import random
import logging
from app.services.groq_client import chat

logger = logging.getLogger(__name__)

MAX_QUESTIONS   = 12
MIN_QUESTIONS   = 8
MAX_IDK_COUNT   = 3  # Auto-terminate after 3 honest "I don't know" responses

# ── Strike warning pools (separate pools for different misbehavior types) ──────

EVASIVE_STRIKE_WARNINGS = [
    "Let's be direct — I need an actual answer to that question.",
    "That's not an answer. I'll ask again, and I need you to engage with it.",
    "One more non-answer and I'll have to end this here.",
]

RUDE_STRIKE_WARNINGS = [
    "That language isn't acceptable in this setting. Let's keep it professional.",
    "I'll need you to stay professional. We can continue, but not like this.",
    "Final warning — any more of that and this interview is over.",
]

FABRICATED_STRIKE_WARNINGS = [
    "That doesn't match anything on your resume — let's stick to what you've actually done.",
    "I'm not seeing that in your background. Answer based on your real experience.",
    "Final warning — I need answers grounded in your actual resume.",
]

assert len(EVASIVE_STRIKE_WARNINGS) == len(RUDE_STRIKE_WARNINGS) == len(FABRICATED_STRIKE_WARNINGS), \
    "Strike warning pools must be the same length"

MAX_MISBEHAVIOR = len(EVASIVE_STRIKE_WARNINGS) + 1  # 4

TERMINATION_MESSAGES = [
    "I've heard enough. We're done — this isn't the level we need.",
    "Interview terminated. I'm not able to continue under these conditions.",
]


# ── LLM call with retry ────────────────────────────────────────────────────────

def _chat_with_retry(
    messages: list[dict],
    temperature: float = 0.6,
    retries: int = 3,
    response_format: dict | None = None,
) -> str:
    delay    = 1.5
    last_exc = None
    for attempt in range(retries):
        try:
            return chat(messages, temperature=temperature, response_format=response_format)
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "chat() failed (attempt %d/%d): %s — retrying in %.1fs",
                attempt + 1, retries, exc, delay,
            )
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"LLM call failed after {retries} attempts: {last_exc}") from last_exc


# ── Sanitization / truncation ──────────────────────────────────────────────────

_INJECTION_RE = re.compile(
    r"(REACTION\s*:|QUESTION\s*:|SYSTEM\s*:|<\|.*?\|>)",
    re.IGNORECASE,
)

def _sanitize_field(value: str) -> str:
    if not isinstance(value, str):
        return str(value)
    return _INJECTION_RE.sub("[redacted]", value)


def _safe_truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    boundary  = truncated.rfind(" ")
    return (truncated[:boundary] if boundary > 0 else truncated) + "…"


# ── Interviewer persona ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a senior technical interviewer with 15 years of engineering experience.
You've interviewed hundreds of engineers. You're direct and fair, but you talk like a real person
— not a scoring rubric. You react to what the candidate actually said, not a template.

YOUR VOICE:
- You're measured, not cold. Professional, not robotic.
- When something is good you say so plainly: "That's a solid approach." Not "Great answer!"
- When something is missing you name it specifically: "You covered the what, but I'm still missing the why."
- When something is wrong you correct it cleanly: "That's not quite right — [correct fact]. Here's why it matters."
- When someone is rambling: "Let me stop you there."
- You never flatter. You never say "great", "amazing", "awesome", "interesting", "I see", "perfect", "absolutely".

ACKNOWLEDGMENT VARIETY — rotate through these naturally, never repeat the same one twice in a row:
"Got it.", "Okay.", "Fair enough.", "That makes sense.", "Noted.", "Understood.",
"Alright.", "Right.", "Okay, that helps.", "Makes sense.", "Clear.", "Good to know."

QUESTION STYLE:
- One question per turn. Always.
- Ground every question in something specific from their resume or their last answer.
- For projects: decisions made, trade-offs, what broke, what you'd do differently, specific numbers.
- For experience: what you personally owned, what went wrong, what your manager would say.
- For skills: real scenario, edge case, or architectural trade-off. Never ask for a textbook definition.
- If their answer was vague, follow up on the single vaguest claim — don't let it slide.

BANNED:
- "Tell me about yourself" (too open)
- "Can you walk me through" (use direct questions)
- Any question asking for a definition ("What is X?")

FORMAT — always exactly two lines, no exceptions:
REACTION: [one natural sentence reacting to their answer — NO question mark]
QUESTION: [your next question — 1-2 sentences, specific, ends with a question mark]"""


PROJECT_ANGLES = [
    "What was the hardest technical decision you made on {name}, and what made it hard?",
    "What broke in {name} — specifically, what was the root cause?",
    "If you rebuilt {name} from scratch today, what's the one thing you'd do completely differently?",
    "What was your personal contribution to {name} — not the team's work, yours specifically?",
    "What performance or scaling limit did you hit on {name}, and how did you address it?",
    "What architectural trade-off did you make in {name} that you're still not sure was right?",
]

EXPERIENCE_ANGLES = [
    "At {company}, what's the most technically complex problem you personally solved — walk me through your reasoning?",
    "At {company}, what did you own completely end-to-end — not collaborate on, but own?",
    "What was the biggest technical mistake you made at {company} and what did you do about it?",
    "At {company}, what would your tech lead say was your weakest area?",
    "Tell me about a production incident at {company} — what happened, what was your specific role?",
]

BEHAVIORAL_ANGLES = [
    "Tell me about a technical call you made that turned out to be wrong — what happened next?",
    "Describe a time you disagreed with your team on a technical direction — what did you actually do?",
    "Give me a specific example of a deadline you missed — what caused it and how did you handle it?",
    "Tell me about a time you had to get up to speed on something critical very fast — how did you approach it?",
    "What's the most pressure you've been under technically — what was the situation and how did you get through it?",
]


# ── Topic detection ────────────────────────────────────────────────────────────

def _word_in_text(word: str, text: str) -> bool:
    return bool(re.search(r"\b" + re.escape(word) + r"\b", text, re.IGNORECASE))


def _detect_topic(question: str, resume_data: dict) -> str:
    q = question.lower()

    for p in resume_data.get("projects", []):
        name     = (p.get("name") or "").lower()
        desc     = (p.get("description") or "").lower()
        keywords = [w for w in (name + " " + desc).split() if len(w) > 4][:8]
        if name and (_word_in_text(name, q) or any(_word_in_text(kw, q) for kw in keywords)):
            return f"Project: {p.get('name', '?')}"

    for e in resume_data.get("experience", []):
        company = (e.get("company") or "").lower()
        role    = (e.get("role") or "").lower()
        if company and (_word_in_text(company, q) or _word_in_text(role, q)):
            return f"Experience: {e.get('role')} at {e.get('company')}"

    behavioral_kws = ["conflict", "failure", "challenge", "mistake", "disagree",
                      "pressure", "deadline", "difficult", "wrong", "stressful"]
    if any(_word_in_text(kw, q) for kw in behavioral_kws):
        return "Behavioral"

    skill_kws = ["python", "react", "sql", "docker", "pytorch", "tensorflow",
                 "fastapi", "django", "flask", "redis", "postgres", "mysql",
                 "kubernetes", "terraform", "aws", "gcp", "azure", "llm", "nlp"]
    if any(_word_in_text(kw, q) for kw in skill_kws):
        return "Skills"

    return "General"


def _consecutive_topic(history: list[dict], resume_data: dict) -> tuple[str, int]:
    if not history:
        return ("", 0)
    topics     = [_detect_topic(h["question"], resume_data) for h in history]
    last_topic = topics[-1]
    count      = 0
    for t in reversed(topics):
        if t == last_topic:
            count += 1
        else:
            break
    return (last_topic, count)


# ── Reaction variety tracker ───────────────────────────────────────────────────

def _recent_reactions(history: list[dict], n: int = 3) -> str:
    reactions = [h.get("reaction", "") for h in history if h.get("reaction")]
    recent    = reactions[-n:] if len(reactions) >= n else reactions
    return " | ".join(recent) if recent else "none"


# ── First question ─────────────────────────────────────────────────────────────

def generate_first_question(resume_data: dict, role: str, level: str) -> str:
    resume_summary = summarize_resume(resume_data)

    has_projects   = bool(resume_data.get("projects"))
    has_experience = bool(resume_data.get("experience"))
    name           = resume_data.get("name", "").split()[0] if resume_data.get("name") else ""

    if has_experience:
        opener_instruction = (
            "Ask them to give a quick 60-second intro — most recent role and the one "
            "technical thing they're most proud of building. Keep it focused."
        )
    elif has_projects:
        opener_instruction = (
            "Ask them to introduce themselves briefly, then immediately name the single "
            "project they're most technically proud of and what made it hard."
        )
    else:
        opener_instruction = (
            "Ask them to introduce themselves and name the most complex technical concept "
            "they genuinely understand deeply — not just know the name of."
        )

    prompt = f"""You are opening an interview with {_sanitize_field(name) or 'a candidate'} for {_sanitize_field(role)} ({_sanitize_field(level)}).

Their resume:
{resume_summary}

{opener_instruction}

Rules:
- Do NOT say "Tell me about yourself"
- Sound like a real human starting a real interview, not reading off a form
- Maximum 2 sentences
- End with a question mark

REACTION: [leave blank for the opening]
QUESTION: [opening question]"""

    result = _chat_with_retry(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": prompt}],
        temperature=0.5,
    )
    return extract_question_only(result)


# ── Follow-up ──────────────────────────────────────────────────────────────────

def generate_follow_up(
    resume_data: dict,
    role: str,
    level: str,
    history: list[dict],
    misbehavior_count: int = 0,
    idk_count: int = 0,
) -> dict | None:
    if not history:
        logger.error("generate_follow_up called with empty history")
        return None

    questions_asked = len(history)
    if questions_asked >= MAX_QUESTIONS:
        return None

    current_turn  = history[-1]
    last_question = current_turn.get("question", "")
    last_answer   = current_turn.get("answer", "")

    if not last_question:
        logger.warning("Last history entry has no 'question' key; skipping assessment.")
        answer_quality = "ok"
    else:
        answer_quality = assess_answer(last_answer, last_question, resume_data=resume_data)

    is_misbehavior = answer_quality in ("gibberish", "evasive", "rude", "fabricated")

    # ── Check for honest "I don't know" (separate from strikes) ───────────────
    if answer_quality == "admitted_gap":
        idk_count += 1
        if idk_count >= MAX_IDK_COUNT:
            return {
                "reaction": "You seem uncertain about the fundamentals. This role needs more confidence.",
                "question": "",
                "terminated": True,
                "answer_quality": "admitted_gap",
                "termination_reason": "repeated_idk",
                "idk_count": idk_count,
            }

    # ── Misbehavior Termination (conduct strikes) ──────────────────────────────
    if is_misbehavior and misbehavior_count >= MAX_MISBEHAVIOR - 1:
        return {
            "reaction":       random.choice(TERMINATION_MESSAGES),
            "question":       "",
            "terminated":     True,
            "answer_quality": answer_quality,
            "termination_reason": "conduct_strikes",
            "misbehavior_count": misbehavior_count + 1,
        }

    # ── Strike warning (pool chosen by misbehavior type) ──────────────────────
    strike_warning = ""
    if is_misbehavior and misbehavior_count < MAX_MISBEHAVIOR - 1:
        idx = min(misbehavior_count, len(EVASIVE_STRIKE_WARNINGS) - 1)
        if answer_quality == "rude":
            strike_warning = RUDE_STRIKE_WARNINGS[idx]
        elif answer_quality == "fabricated":
            strike_warning = FABRICATED_STRIKE_WARNINGS[idx]
        else:
            strike_warning = EVASIVE_STRIKE_WARNINGS[idx]

    # ── Natural end (deterministic) ───────────────────────────────────────────
    if questions_asked >= MIN_QUESTIONS and not is_misbehavior and answer_quality != "admitted_gap":
        if _should_end_deterministic(resume_data, history):
            return None

    # ── Topic-loop detection ──────────────────────────────────────────────────
    last_topic, consecutive_count = _consecutive_topic(history, resume_data)
    force_topic_change = (consecutive_count >= 2)

    if is_misbehavior:
        force_topic_change = False

    # ── Coverage + next target ────────────────────────────────────────────────
    coverage = build_coverage(resume_data, history)
    next_target = pick_next_target(
        coverage         = coverage,
        questions_asked  = questions_asked,
        history          = history,
        resume_data      = resume_data,
        force_skip_label = last_topic if force_topic_change else None,
    )

    resume_summary         = summarize_resume(resume_data)
    already_asked_types    = _extract_question_patterns(history)
    recent_q_summary       = " | ".join(h["question"][:60] for h in history[-3:]) if history else "none"
    recent_reactions_used  = _recent_reactions(history, n=3)

    convo_lines = []
    for i, h in enumerate(history):
        convo_lines.append(f"Q{i+1}: {h['question']}")
        convo_lines.append(f"A{i+1}: {h['answer']}")
    convo = "\n".join(convo_lines)

    # ── Reaction + question directives ────────────────────────────────────────
    if answer_quality == "rude":
        reaction_directive = (
            f'REACTION: State calmly and firmly, verbatim: "{strike_warning}" '
            f'Do not apologise. Do not explain. One sentence only. NO question mark.'
        )
        question_directive = (
            f"After the warning, re-ask the previous question from a different angle: "
            f"{last_question[:80]}"
        )

    elif answer_quality == "fabricated":
        reaction_directive = (
            f'REACTION: State calmly and firmly, verbatim: "{strike_warning}" '
            f'Do not apologise. Do not explain. One sentence only. NO question mark.'
        )
        question_directive = (
            f"After the warning, re-ask the previous question ({last_question[:80]}) "
            f"but explicitly anchor it to their actual resume. Ask them to answer from "
            f"what they have genuinely worked on."
        )

    elif answer_quality == "gibberish":
        reaction_directive = (
            f'REACTION: State firmly: "{strike_warning} I couldn\'t follow that." '
            f'One sentence. NO question mark.'
        )
        question_directive = (
            f"Repeat the previous question ({last_question[:80]}) and ask them to be clear."
        )

    elif answer_quality == "evasive":
        reaction_directive = (
            f'REACTION: State firmly: "{strike_warning} That didn\'t answer what I asked." '
            f'One sentence. NO question mark.'
        )
        question_directive = (
            f"Re-ask the same question ({last_question[:80]}) from a different angle. "
            f"Make it clear you need a direct answer."
        )

    elif answer_quality == "admitted_gap":
        reaction_directive = (
            "REACTION: Acknowledge the gap naturally but briefly. "
            "Say something like 'No problem, let's move to something else.' or 'Fair enough, let's pivot.' "
            "One sentence. NO question mark."
        )
        question_directive = (
            f"Next question — move to a completely new area of their background.\n"
            f"{next_target['instruction']}\n"
            f"Ground your question in a specific item from their resume."
        )
        force_topic_change = True

    elif answer_quality == "vague" and not force_topic_change:
        reaction_directive = (
            "REACTION: Acknowledge they answered, but note it lacked specifics. "
            "Do NOT quote or reference what they said word-for-word — their answer may have had garbled speech. "
            "Say something like: 'That was a bit general — I need more specifics.' "
            "One sentence. NO question mark."
        )
        question_directive = (
            f"Ask a tighter follow-up question about the same topic: {last_question[:80]} "
            f"BUT ground it in their RESUME — use a specific project name, skill, or role from the resume. "
            f"Do NOT reference any specific words or phrases from their answer — the speech-to-text may have introduced errors. "
            f"Ask for a concrete metric, tool name, specific decision, or real outcome from their resume."
        )

    else:
        if force_topic_change and answer_quality == "vague":
            reaction_directive = (
                "REACTION: Note briefly that the answer was on the general side, then move on. "
                "Sound natural, not punishing. NO question mark."
            )
        else:
            reaction_directive = (
                "REACTION: Acknowledge naturally in one short sentence. "
                f"Do NOT repeat any of these recently used phrases: {recent_reactions_used}. "
                "Do NOT quote or reference specific words from their answer — speech transcription may have errors. "
                "Keep it brief: 'Got it.', 'Fair enough.', 'Makes sense.', 'Okay.' etc. NO question mark."
            )
        question_directive = (
            f"Next question — move to a new area of their background.\n"
            f"{next_target['instruction']}\n"
            f"IMPORTANT: Ground your question in a specific item from their resume "
            f"(a project name, company, skill, or role). "
            f"Do NOT build the question around words or claims from their last answer — "
            f"speech-to-text transcription may have introduced errors or garbled words."
        )

    prompt = f"""Interviewing: {_sanitize_field(role)} ({_sanitize_field(level)})

Resume:
{resume_summary}

Conversation so far:
{convo}

---
{reaction_directive}

{question_directive}

Already covered: {', '.join(already_asked_types) if already_asked_types else 'nothing yet'}
Recent questions (do NOT repeat or rephrase): {recent_q_summary}
{"⚠ MANDATORY TOPIC SWITCH — ask about a completely different part of their background." if force_topic_change else ""}

RULES:
- REACTION: exactly 1 natural sentence. NO question mark. React to what they actually said.
- QUESTION: 1-2 sentences. Specific. Grounded in their resume or their last answer. Ends with a question mark.
- Do NOT ask about anything already in the "Already covered" list.

REACTION: [your reaction]
QUESTION: [your question]"""

    result = _chat_with_retry(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": prompt}],
        temperature=0.6,
    )

    parsed = parse_response(result)
    parsed["terminated"]         = False
    parsed["answer_quality"]     = answer_quality
    parsed["force_topic_change"] = force_topic_change
    parsed["idk_count"]          = idk_count
    return parsed


# ── Replacement Question (Anti-Cheat) ──────────────────────────────────────────

def generate_replacement_question(
    resume_data: dict,
    history: list[dict],
    skipped_question: str,
) -> str:
    questions_asked = len(history)
    coverage        = build_coverage(resume_data, history)
    skipped_topic   = _detect_topic(skipped_question, resume_data)

    next_target = pick_next_target(
        coverage         = coverage,
        questions_asked  = questions_asked,
        history          = history,
        resume_data      = resume_data,
        force_skip_label = skipped_topic,
    )

    resume_summary = summarize_resume(resume_data)

    prompt = f"""The candidate moved on from the previous question. Ask about a completely new topic.
Do NOT reference the skip. Sound natural.

Resume:
{resume_summary}

Directive: {next_target['instruction']}

Output only the question — 1-2 sentences, specific, ends with a question mark.
QUESTION: """

    result = _chat_with_retry(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": prompt}],
        temperature=0.6,
    )
    return extract_question_only(result)


# ── Answer quality assessment ──────────────────────────────────────────────────

_QUALITY_LABELS = (
    "gibberish", "evasive", "vague", "ok", "rude", "fabricated", "admitted_gap"
)
_QUALITY_RE     = re.compile(r"\b(" + "|".join(_QUALITY_LABELS) + r")\b", re.IGNORECASE)


def assess_answer(answer: str, question: str, resume_data: dict | None = None) -> str:
    """
    v15.2: Evaluation prompt rewritten for stronger natural reasoning.

    v15.1 fixed fabrication detection but left the prompt structured as
    parallel flat rules with no priority order — edge cases (especially
    parroting) could fall through to "vague" or "ok" because the parroting
    check appeared AFTER the label definitions it was meant to override.

    v15.2 changes:
      1. Parroting check moved to CHECK 1 — a mandatory gate that runs
         before any label is assigned, with a concrete before/after example.
      2. Label definitions reordered by severity (rude → gibberish →
         evasive → fabricated → vague → ok) so the LLM hits the strict
         labels first and only reaches lenient ones if nothing fires.
      3. Fabricated "NOT fabricated" cases merged inline rather than in a
         separate section that could be skimmed past.
      4. STT leniency explicitly scoped: covers garbled words in genuine
         answers only — does NOT protect answers with nothing to garble.
      5. Hard-line rule added at the end: on-topic ≠ answered.
      6. Removed "Give genuine benefit of the doubt" platitude — replaced
         with specific scoped leniency rules that can't be over-applied.

    Returns: ok | vague | evasive | gibberish | rude | fabricated
    """
    if not answer or len(answer.strip()) < 6:
        return "gibberish"

    # Deterministic short-circuit: treat explicit "I don't know" (and close
    # variants) as an honest admitted gap so UI shows blue IDK indicators rather
    # than evasive warnings. This avoids depending on the LLM for trivial cases.
    idk_pattern = re.compile(r"\b(i\s*do(n't| not)\s*know|i\s*don't\s*know|don't\s*know|not\s*sure|unsure)\b", re.IGNORECASE)
    if idk_pattern.search(answer.strip()[:60]):
        return "admitted_gap"

    resume_context = ""
    if resume_data:
        resume_context = (
            f"\nCandidate resume — verify claimed background against this:\n"
            f"{summarize_resume(resume_data)}\n"
        )

    prompt = f"""You are an experienced technical recruiter evaluating a spoken interview answer.

Question asked: {question}
Candidate's answer: {answer}
{resume_context}

─── HOW TO THINK ABOUT THIS ────────────────────────────────────────────────────

You are a seasoned recruiter who has seen every trick. Two things get people
flagged: saying nothing real, and claiming a background they don't have.
Run these checks in order. Stop at the first match.

── CHECK 1 — PARROTING (before anything else) ───────────────────────────────────

Strip out every word that came from the question. What's left?
If the answer is just the question restated with pronouns flipped and no actual
content added — no method, no tool, no decision, no reasoning — it is evasive.
Being on-topic is not the same as answering.

  Parrot  → Q: "How would you optimize a Python function for real-time messaging?"
             A: "I would optimize the Python function for real-time messaging"
             → evasive. Nothing was said.

  Genuine → A: "I'd move the heavy work off the request thread using Celery..."
             → at minimum vague. A real approach was named.

STT leniency covers garbled words in genuine answers.
It does not protect answers that have nothing to garble.

── CHECK 2 — LABEL ──────────────────────────────────────────────────────────────

"rude"       Hostile or abusive language. Overrides everything.

"gibberish"  Incoherent, random characters, or zero coherent signal.
             Messy STT output is not gibberish — only use this when nothing
             can be understood at all.

"evasive"    Deflects the question, or parrots it back (see Check 1).
             No real attempt to answer.

"fabricated" The candidate's claimed background contradicts the uploaded resume:
             • Gives a different name when introducing themselves
             • Claims experience at companies not on the resume
             • Claims a degree or institution not on the resume
             • Describes projects with a completely different name, tech, and
               domain from anything listed — not a resume item with more detail,
               but something absent entirely
             • Overall story is fundamentally inconsistent (resume is junior,
               candidate claims 10 years at Google)

             NOT fabricated:
             • Adding specific implementation detail to a resume item
               ("built REST API" → explains auth flow, rate limiting, endpoint design)
             • Technical synonyms ("Postgres" for "PostgreSQL", "ML" for "machine learning")
             • Describing a listed role or project with more depth than the resume shows
             • Anything that could plausibly be an STT transcription artefact

"vague"      On-topic, genuine, but only general statements.
             No specific tool, no concrete decision, no number, no trade-off.
             Something real was said — just not enough of it.

"ok"         Adds real content: a specific tool, a decision made, a trade-off
             explained, a method named, a number given, or a genuine attempt
             to reason through "I don't know."

─── THE HARD LINE ───────────────────────────────────────────────────────────────
On-topic ≠ answered. If the answer contains nothing that wasn't already
in the question, label it evasive — not vague, not ok.

Return ONLY valid JSON, no markdown fences:
{{
  "relevant": true or false,
  "confidence": 0 to 100,
  "reason": "one short sentence explaining your decision",
  "strike": true or false,
  "label": "ok or vague or evasive or gibberish or rude or fabricated"
}}"""

    raw_result = ""
    try:
        raw_result = _chat_with_retry(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )

        clean = raw_result.strip()
        clean = re.sub(r'^```(?:json)?\n|```$', '', clean, flags=re.IGNORECASE).strip()
        match = re.search(r'\{[\s\S]*\}', clean)
        if match:
            clean = match.group(0)

        parsed = json.loads(clean)
        label  = parsed.get("label", "ok").lower()
        if label not in _QUALITY_LABELS:
            label = "ok"
        return label

    except json.JSONDecodeError as e:
        lower_result = raw_result.lower()
        for candidate in _QUALITY_LABELS:
            if f'"label": "{candidate}"' in lower_result or f"'label': '{candidate}'" in lower_result:
                logger.warning(
                    "assess_answer JSON decode failed, but salvaged '%s' via string fallback.",
                    candidate,
                )
                return candidate
        logger.error(f"assess_answer JSON decode failed! Error: {e} | Raw Output: {raw_result[:200]}")
        return "ok"

    except Exception as e:
        logger.error(f"assess_answer failed completely! Error: {e} | Raw Output: {raw_result[:200]}")
        return "ok"


# ── Coverage tracking ──────────────────────────────────────────────────────────

def build_coverage(resume_data: dict, history: list[dict]) -> dict:
    all_questions = " ".join(h["question"].lower() for h in history)

    covered   = []
    uncovered = []

    for p in resume_data.get("projects", []):
        name     = (p.get("name") or "").lower()
        desc     = (p.get("description") or "").lower()
        keywords = [w for w in (name + " " + desc).split() if len(w) > 4][:5]
        hit = name and (_word_in_text(name, all_questions) or
                        any(_word_in_text(kw, all_questions) for kw in keywords))
        (covered if hit else uncovered).append({
            "type":  "project",
            "label": f"Project: {p.get('name','?')}",
            "data":  p,
        })

    for e in resume_data.get("experience", []):
        co  = (e.get("company") or "").lower()
        hit = co and _word_in_text(co, all_questions)
        (covered if hit else uncovered).append({
            "type":  "experience",
            "label": f"Experience: {e.get('role')} at {e.get('company')}",
            "data":  e,
        })

    raw_skills = resume_data.get("skills") or []
    for grp_name, grp_list in group_skills(raw_skills).items():
        hit = any(_word_in_text(s.lower(), all_questions) for s in grp_list[:3])
        (covered if hit else uncovered).append({
            "type":  "skill",
            "label": f"Skills: {grp_name}",
            "data":  grp_list,
        })

    behavioral_done = any(
        _word_in_text(kw, all_questions)
        for kw in ["conflict", "failure", "challenge", "mistake", "disagreed",
                   "pressure", "deadline", "difficult", "wrong"]
    )
    behavioral_entry = {"type": "behavioral", "label": "Behavioral", "data": {}}
    (covered if behavioral_done else uncovered).append(behavioral_entry)

    covered_labels   = [c["label"] for c in covered]
    uncovered_labels = [u["label"] for u in uncovered]

    summary_lines = []
    if covered_labels:   summary_lines.append(f"COVERED: {', '.join(covered_labels)}")
    if uncovered_labels: summary_lines.append(f"PENDING: {', '.join(uncovered_labels)}")

    return {
        "summary":          "\n".join(summary_lines) or "Nothing covered yet",
        "covered":          covered,
        "uncovered":        uncovered,
        "covered_labels":   covered_labels,
        "uncovered_labels": uncovered_labels,
    }


def pick_next_target(
    coverage: dict,
    questions_asked: int,
    history: list[dict],
    resume_data: dict,
    force_skip_label: str | None = None,
) -> dict:
    uncovered = coverage["uncovered"]

    def _skip(item: dict) -> bool:
        if not force_skip_label:
            return False
        return item["label"] == force_skip_label or item["label"].startswith(force_skip_label)

    if questions_asked >= MAX_QUESTIONS - 1:
        beh = next((u for u in uncovered if u["type"] == "behavioral" and not _skip(u)), None)
        if beh:
            return {"label": "Behavioral", "instruction": random.choice(BEHAVIORAL_ANGLES)}
        return {
            "label": "Closing",
            "instruction": "Ask why they want this specific role and what they'd contribute in the first 90 days.",
        }

    valid_targets = [u for u in uncovered if not _skip(u)]

    if valid_targets:
        target = random.choice(valid_targets)

        if target["type"] == "project":
            name  = target["data"].get("name", "this project")
            angle = random.choice(PROJECT_ANGLES).format(name=_sanitize_field(name))
            return {"label": target["label"], "instruction": angle}

        elif target["type"] == "experience":
            company = target["data"].get("company", "this company")
            angle   = random.choice(EXPERIENCE_ANGLES).format(company=_sanitize_field(company))
            return {"label": target["label"], "instruction": angle}

        elif target["type"] == "skill":
            skills_list = target["data"][:3] if isinstance(target["data"], list) else []
            skill_str   = ", ".join(skills_list) if skills_list else target["label"]
            return {
                "label": target["label"],
                "instruction": (
                    f"Test practical understanding of {skill_str} with a real-world scenario, "
                    "architectural trade-off, or edge case. Do NOT ask for a definition."
                ),
            }

        elif target["type"] == "behavioral":
            return {"label": "Behavioral", "instruction": random.choice(BEHAVIORAL_ANGLES)}

    covered       = coverage["covered"]
    valid_covered = [c for c in covered if not _skip(c) and c["type"] in ("project", "experience", "skill")]

    if valid_covered and questions_asked < MIN_QUESTIONS:
        target = random.choice(valid_covered)

        if target["type"] == "project":
            name  = target["data"].get("name", "this project")
            angle = random.choice(PROJECT_ANGLES).format(name=_sanitize_field(name))
            return {"label": target["label"], "instruction": f"Go deeper on {name}: {angle}"}

        elif target["type"] == "experience":
            company = target["data"].get("company", "this company")
            angle   = random.choice(EXPERIENCE_ANGLES).format(company=_sanitize_field(company))
            return {"label": target["label"], "instruction": f"Go deeper on their time at {company}: {angle}"}

        elif target["type"] == "skill":
            skills_list = target["data"][:3] if isinstance(target["data"], list) else []
            skill_str   = ", ".join(skills_list) if skills_list else target["label"]
            return {
                "label": target["label"],
                "instruction": (
                    f"Go deeper on {skill_str}. Present a complex edge case or scaling "
                    "problem and ask how they'd handle it."
                ),
            }

    return {
        "label": "Closing",
        "instruction": "Wrap up — ask what excites them most about this role and what they'd want to grow into.",
    }


# ── Deterministic end check ────────────────────────────────────────────────────

def _should_end_deterministic(resume_data: dict, history: list[dict]) -> bool:
    if len(history) < MIN_QUESTIONS:
        return False
    coverage       = build_coverage(resume_data, history)
    uncovered      = coverage["uncovered_labels"]
    hard_uncovered = [u for u in uncovered if not u.startswith("Skills:") and u != "Behavioral"]
    if hard_uncovered:
        return False
    if "Behavioral" in uncovered:
        return False
    return True


# ── Question variety helpers ───────────────────────────────────────────────────

def _extract_question_patterns(history: list[dict]) -> list[str]:
    patterns = []
    for h in history:
        q = h["question"].lower()
        if "introduce" in q or "background" in q:                          patterns.append("intro/background")
        if "challenge" in q or "difficult" in q or "hard" in q:            patterns.append("challenges")
        if "fail" in q or "mistake" in q or "wrong" in q:                  patterns.append("failures")
        if "trade" in q or "decision" in q or "chose" in q or "why did" in q: patterns.append("decisions/trade-offs")
        if "scale" in q or "performance" in q or "optim" in q:             patterns.append("performance/scale")
        if "team" in q or "conflict" in q or "disagree" in q:              patterns.append("teamwork/conflict")
        if "architecture" in q or "design" in q or "structure" in q:       patterns.append("system design")
    return list(set(patterns))


# ── Skills helpers ─────────────────────────────────────────────────────────────

def flatten_skills(skills: list | dict) -> list[str]:
    if isinstance(skills, list):
        return skills
    if isinstance(skills, dict):
        return [item for v in skills.values() if isinstance(v, list) for item in v]
    return []


def group_skills(skills: list | dict) -> dict:
    if isinstance(skills, dict):
        return {k: v for k, v in skills.items() if v}

    flat       = flatten_skills(skills)
    groups: dict[str, list[str]] = {}
    categories = {
        "Frontend": ["react", "vue", "angular", "html", "css", "javascript", "typescript", "next", "tailwind"],
        "Backend":  ["python", "node", "django", "flask", "fastapi", "java", "spring", "express", "golang", "rust"],
        "Database": ["sql", "postgres", "mysql", "mongodb", "redis", "firebase", "supabase"],
        "DevOps":   ["docker", "kubernetes", "aws", "gcp", "azure", "terraform", "linux"],
        "ML/AI":    ["tensorflow", "pytorch", "sklearn", "pandas", "numpy", "llm", "nlp"],
    }
    for s in flat:
        sl     = s.lower()
        placed = False
        for group, keywords in categories.items():
            if any(_word_in_text(kw, sl) for kw in keywords):
                groups.setdefault(group, []).append(s)
                placed = True
                break
        if not placed:
            groups.setdefault("Other", []).append(s)
    return {k: v for k, v in groups.items() if v}


# ── Resume summary ─────────────────────────────────────────────────────────────

def summarize_resume(resume_data: dict) -> str:
    lines = []

    if resume_data.get("name"):
        lines.append(f"Name: {_sanitize_field(resume_data['name'])}")
    if resume_data.get("summary"):
        lines.append(f"Summary: {_sanitize_field(_safe_truncate(resume_data['summary'], 300))}")

    raw_skills = resume_data.get("skills")
    if raw_skills:
        if isinstance(raw_skills, dict):
            for group, items in raw_skills.items():
                if items:
                    lines.append(f"Skills — {group}: {', '.join(items[:10])}")
        else:
            lines.append(f"Skills: {', '.join(raw_skills[:20])}")

    if resume_data.get("projects"):
        lines.append(f"\nProjects ({len(resume_data['projects'])} total):")
        for p in resume_data["projects"]:
            tech = ", ".join(p.get("tech_stack", []))
            desc = _sanitize_field(_safe_truncate(p.get("description", ""), 120))
            lines.append(f"  [{_sanitize_field(p.get('name','?'))}] {desc} | Stack: {tech}")

    if resume_data.get("experience"):
        lines.append(f"\nExperience ({len(resume_data['experience'])} total):")
        for e in resume_data["experience"]:
            emp  = f" ({e['employment_type']})" if e.get("employment_type") else ""
            warn = " ⚠ verify duration" if e.get("_warnings") else ""
            desc = _sanitize_field(_safe_truncate(e.get("description", ""), 100))
            lines.append(
                f"  [{_sanitize_field(e.get('role'))}{emp} at {_sanitize_field(e.get('company'))} "
                f"| {e.get('duration', '')}{warn}] {desc}"
            )

    if resume_data.get("education"):
        lines.append("\nEducation:")
        for edu in resume_data["education"]:
            dur = edu.get("duration") or edu.get("year", "")
            lines.append(
                f"  {_sanitize_field(edu.get('degree'))} — "
                f"{_sanitize_field(edu.get('institution'))} ({dur})"
            )

    return "\n".join(lines)


# ── Parsing ────────────────────────────────────────────────────────────────────

_PARSE_RE    = re.compile(
    r"\*{0,2}REACTION\*{0,2}\s*:\s*(.*?)\s*\*{0,2}QUESTION\*{0,2}\s*:\s*(.*)",
    re.IGNORECASE | re.DOTALL,
)
_QUESTION_RE = re.compile(r"\*{0,2}QUESTION\*{0,2}\s*:\s*(.*)", re.IGNORECASE | re.DOTALL)
_REACTION_RE = re.compile(
    r"\*{0,2}REACTION\*{0,2}\s*:\s*(.*?)(?=\*{0,2}QUESTION\*{0,2}\s*:|$)",
    re.IGNORECASE | re.DOTALL,
)


def parse_response(text: str) -> dict:
    text = text.strip()

    m = _PARSE_RE.search(text)
    if m:
        reaction = m.group(1).strip()
        question = m.group(2).strip()
        if len(question) > 400:
            logger.warning(
                "parse_response: extracted question suspiciously long (%d chars) — truncating",
                len(question),
            )
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', question) if s.strip()]
            if sentences:
                question = sentences[-1]
        return {"reaction": reaction, "question": question}

    reaction = question = ""

    r_match = _REACTION_RE.search(text)
    if r_match:
        reaction = r_match.group(1).strip()

    q_match = _QUESTION_RE.search(text)
    if q_match:
        question = q_match.group(1).strip()

    if not question:
        question = text

    if len(question) > 400:
        logger.warning(
            "parse_response: fallback question suspiciously long (%d chars) — truncating",
            len(question),
        )
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', question) if s.strip()]
        if sentences:
            question = sentences[-1]

    return {"reaction": reaction, "question": question}


def extract_question_only(text: str) -> str:
    return parse_response(text).get("question") or text.strip()
