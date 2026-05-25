"""
question_engine.py — Enhanced
- Dominant, non-submissive interviewer personality
- Strike warnings with escalating severity
- Termination logic after MAX_MISBEHAVIOR strikes
- Stricter answer classification
"""

from app.services.groq_client import chat

INTERVIEW_PLAN = [
    {"area": "introduction",    "goal": "Who are you. What have you actually built. No fluff.",                    "min": 1, "max": 1},
    {"area": "technical_depth", "goal": "Test real technical understanding. Expose gaps. Push on every claim.",     "min": 2, "max": 4},
    {"area": "problem_solving", "goal": "Ambiguous scenario. How do they think. Do they panic or structure?",       "min": 1, "max": 2},
    {"area": "behavioral",      "goal": "Failure, conflict, pressure. Push past the rehearsed answer.",             "min": 1, "max": 2},
    {"area": "closing",         "goal": "Why this role. What do they bring. Are they serious.",                     "min": 1, "max": 1},
]

MIN_QUESTIONS   = sum(s["min"] for s in INTERVIEW_PLAN)   # 6
MAX_QUESTIONS   = sum(s["max"] for s in INTERVIEW_PLAN)   # 10
MAX_MISBEHAVIOR = 3   # strikes before termination

SYSTEM_PROMPT = """You are a senior technical interviewer. Cold. Dominant. You are never impressed by default.

You have interviewed hundreds of candidates. Most are not ready. You do not coddle.

YOUR RULES:
- React to exactly what was said. Nothing more.
- NEVER compliment. NEVER encourage. NEVER say "great", "good", "interesting", "I see".
- If the answer was solid: one word. "Right." / "Go on." / "Noted."
- If the answer was vague: cut it immediately. "That's not specific enough." / "Give me a number." / "What exactly did YOU do?"
- If the answer was wrong: say so bluntly. "That's incorrect." / "No." / "That's not how that works."
- If they dodged: call it. "You didn't answer my question." / "That's not what I asked."
- If they rambled: shut it down. "Stop. Answer directly."
- If they're clearly stalling: "I'll ask again. Answer this time."
- You do NOT repeat yourself kindly. You rephrase once, bluntly.
- Zero tolerance for buzzwords with no substance.
- You are in control of this interview at all times.

FORMAT — always:
REACTION: [1 sentence max — blunt, cold, authoritative]
QUESTION: [your next question — sharp, direct, 1-2 sentences]"""

STRIKE_WARNINGS = [
    "Let me be direct — that answer was unacceptable. One more like that and this interview ends.",
    "That's your second warning. I don't tolerate time-wasting. Answer properly or we're done.",
]

TERMINATION_MESSAGES = [
    "That's the third strike. This interview is over. Come back when you're prepared.",
    "I've seen enough. We're done here. This is not the standard we need.",
    "Enough. This interview is terminated. You've wasted both our time.",
]


def generate_first_question(resume_data: dict, role: str, level: str) -> str:
    resume_summary = _summarize_resume(resume_data)
    prompt = f"""Interviewing for: {role} ({level}).

Resume:
{resume_summary}

Open the interview. No pleasantries. No small talk. Ask them to walk you through their background and the most technically complex thing they've built.
Cold, dominant, two sentences max."""

    result = chat(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": prompt}],
        temperature=0.4,
    )
    return _extract_question_only(result)


def generate_follow_up(
    resume_data: dict,
    role: str,
    level: str,
    history: list[dict],
    last_answer: str,
    misbehavior_count: int = 0,
) -> dict | None:
    """
    Returns {"reaction": str, "question": str, "terminated": bool, "answer_quality": str} or None.
    terminated=True means the interviewer is cutting the interview due to accumulated misbehavior.
    """
    questions_asked = len(history) + 1

    if questions_asked > MAX_QUESTIONS:
        return None

    last_question  = history[-1]["question"] if history else ""
    answer_quality = _assess_answer(last_answer, last_question)
    is_misbehavior = answer_quality in ("gibberish", "evasive")

    # ── Termination check ─────────────────────────────────────────────────────
    if is_misbehavior and misbehavior_count >= MAX_MISBEHAVIOR - 1:
        import random
        msg = random.choice(TERMINATION_MESSAGES)
        return {
            "reaction":       msg,
            "question":       "",
            "terminated":     True,
            "answer_quality": answer_quality,
        }

    # ── Strike warning (non-terminal) ─────────────────────────────────────────
    strike_warning = ""
    if is_misbehavior and misbehavior_count < MAX_MISBEHAVIOR - 1:
        idx = min(misbehavior_count, len(STRIKE_WARNINGS) - 1)
        strike_warning = STRIKE_WARNINGS[idx]

    # ── Natural end check ─────────────────────────────────────────────────────
    if questions_asked > MIN_QUESTIONS and not is_misbehavior:
        if _should_end(history, last_answer, role):
            return None

    resume_summary = _summarize_resume(resume_data)
    current_area, current_goal, area_index = _get_current_area(questions_asked)
    next_area = INTERVIEW_PLAN[area_index + 1]["area"] if area_index + 1 < len(INTERVIEW_PLAN) else None

    convo = "\n".join(
        f"Q{i+1}: {h['question']}\nA{i+1}: {h['answer']}"
        for i, h in enumerate(history)
    )
    convo += f"\nCandidate's latest answer: {last_answer}"

    directive = {
        "gibberish": f"→ GIBBERISH / INCOHERENT. Call it out directly and firmly. Do NOT accept this.\n   Issue warning: \"{strike_warning}\" then rephrase the question, sharper.",
        "evasive":   f"→ EVASIVE. They dodged deliberately. Tell them clearly.\n   Issue warning: \"{strike_warning}\" then ask again, more pointed.",
        "vague":     "→ VAGUE. No specifics whatsoever. Demand a concrete example, a number, a name. Press hard.",
        "ok":        "→ Acceptable. Move to the next angle in the plan. Maintain pressure.",
    }.get(answer_quality, "→ Move forward.")

    prompt = f"""Interviewing for: {role} ({level}).

Resume:
{resume_summary}

Conversation so far:
{convo}

---
Answer quality: {answer_quality}
{directive}
Current area: {current_area} — {current_goal}
Next area: {next_area or 'closing'}
Already covered: {_topics_covered(history)}
Question {questions_asked} of max {MAX_QUESTIONS}
Misbehavior strikes so far: {misbehavior_count}/{MAX_MISBEHAVIOR}

→ Never re-ask: {_topics_covered(history)}
→ You are dominant. You do not soften. No filler.
{"→ You MUST issue the strike warning in your REACTION before the question." if strike_warning else ""}

REACTION: [blunt 1-sentence reaction{" + mandatory strike warning" if strike_warning else ""}]
QUESTION: [next question — sharp, specific]"""

    result = chat(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": prompt}],
        temperature=0.55,
    )

    parsed = _parse_response(result)
    parsed["terminated"]     = False
    parsed["answer_quality"] = answer_quality
    return parsed


# ── Answer quality ─────────────────────────────────────────────────────────────

def _assess_answer(answer: str, question: str) -> str:
    if not answer or len(answer.strip()) < 8:
        return "gibberish"

    prompt = f"""Question: {question}
Answer: {answer}

Classify the answer in ONE word only:
- gibberish  — incoherent, off-topic, joking, random, makes no sense, complete nonsense
- evasive    — clearly avoided the question, changed subject, refused to answer properly
- vague      — on-topic but zero specifics, all buzzwords, no concrete details
- ok         — gave a real answer, even if imperfect

ONE word only. No explanation."""

    result = chat([{"role": "user", "content": prompt}], temperature=0.0)
    word   = result.strip().lower().split()[0]
    return word if word in ("gibberish", "evasive", "vague", "ok") else "ok"


# ── End logic ──────────────────────────────────────────────────────────────────

def _should_end(history: list[dict], last_answer: str, role: str) -> bool:
    convo = "\n".join(f"Q{i+1}: {h['question']}\nA{i+1}: {h['answer']}" for i, h in enumerate(history))
    convo += f"\nLast: {last_answer}"
    prompt = f"""Has this {role} interview fully covered: introduction, 2+ technical questions, 1 problem-solving question, 1 behavioral question?
{convo}
Answer YES or NO only."""
    result = chat([{"role": "user", "content": prompt}], temperature=0.0)
    return result.strip().upper().startswith("YES")


# ── Parsing ────────────────────────────────────────────────────────────────────

def _parse_response(text: str) -> dict:
    reaction = ""
    question = ""
    for line in text.strip().splitlines():
        if line.upper().startswith("REACTION:"):
            reaction = line[len("REACTION:"):].strip()
        elif line.upper().startswith("QUESTION:"):
            question = line[len("QUESTION:"):].strip()
    if not question:
        question = text.strip()
    return {"reaction": reaction, "question": question}


def _extract_question_only(text: str) -> str:
    return _parse_response(text).get("question") or text.strip()


# ── Plan helpers ───────────────────────────────────────────────────────────────

def _get_current_area(question_number: int) -> tuple:
    count = 0
    for i, stage in enumerate(INTERVIEW_PLAN):
        count += stage["max"]
        if question_number <= count:
            return stage["area"], stage["goal"], i
    last = INTERVIEW_PLAN[-1]
    return last["area"], last["goal"], len(INTERVIEW_PLAN) - 1


def _topics_covered(history: list[dict]) -> str:
    if not history:
        return "none"
    return "; ".join(f'"{h["question"][:55]}..."' for h in history[-4:])


def _summarize_resume(resume_data: dict) -> str:
    lines = []
    if resume_data.get("name"):        lines.append(f"Name: {resume_data['name']}")
    if resume_data.get("skills"):      lines.append(f"Skills: {', '.join(resume_data['skills'][:15])}")
    if resume_data.get("projects"):
        lines.append("Projects:")
        for p in resume_data["projects"][:3]:
            tech = ", ".join(p.get("tech_stack", []))
            lines.append(f"  - {p.get('name','?')}: {p.get('description','')} | {tech}")
    if resume_data.get("experience"):
        lines.append("Experience:")
        for e in resume_data["experience"][:2]:
            lines.append(f"  - {e.get('role')} at {e.get('company')} ({e.get('duration','')})")
    if resume_data.get("education"):
        edu = resume_data["education"][0]
        lines.append(f"Education: {edu.get('degree')} from {edu.get('institution')}")
    return "\n".join(lines)