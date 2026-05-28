"""
question_engine.py — v10

Fixes:
  1. Randomized Targeting: The interviewer no longer strictly exhausts all projects
     and experiences before asking about skills. It now picks randomly from all
     uncovered topics, naturally weaving direct skill scenario questions in between
     projects and internships.
  2. Deeper Skill Probing: If all topics are covered, the AI can now circle back
     to directly probe a skill with a complex edge-case scenario.
"""

import random
from app.services.groq_client import chat

MAX_QUESTIONS   = 12
MIN_QUESTIONS   = 8
MAX_MISBEHAVIOR = 4

# ── Interviewer persona ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a senior technical interviewer with 15 years of engineering experience.
You are direct, exacting, and fair. You speak naturally and conversationally, not like a robot.

TONE BY SITUATION:
- Strong answer -> Provide a natural, conversational acknowledgment (e.g., "Got it, that makes sense." or "Understood, that clears it up."), then immediately go deeper.
- Partial answer -> identify exactly what's missing naturally: "You mentioned X, but what was the actual impact?"
- Vague answer -> call out what's missing specifically: "That tells me about the team, but what did YOU build?"
- Wrong answer -> correct it directly but professionally: "Actually, [correct fact]. Given that, how would you approach it?"
- Nervous/rambling -> cut it gently but firmly: "Let's pause there. In one sentence, what was the outcome?"

BANNED PHRASES — never use these:
- "great", "good job", "interesting", "I see", "perfect", "absolutely"
- "Tell me about yourself" (too generic)
- "Can you walk me through" (use direct questions instead)

QUESTION STYLE:
- Ask exactly one question per turn.
- Questions must be specific to what the candidate actually said or what's on their resume.
- For projects: ask about decisions, trade-offs, failures, specific numbers.
- For experience: ask about scope, ownership, what broke, what they'd do differently.
- For skills: NEVER ask for a textbook definition. Give them a scenario, edge-case, or trade-off to solve.

FORMAT — always exactly two lines:
REACTION: [your conversational but brief reaction to their last answer — 1 natural sentence]
QUESTION: [your next question — 1-2 sentences, specific]"""


STRIKE_WARNINGS = [
    "That's a warning. I need a clear technical answer.",
    "Second warning. Please engage directly with the question.",
    "Third warning. One more answer like that and this interview ends.",
]

TERMINATION_MESSAGES = [
    "I've heard enough. We're done — this isn't the level we need.",
    "Interview terminated. Your answers don't meet the bar for this role.",
]

PROJECT_ANGLES = [
    "What was the hardest technical decision you made on {name}, and what did you consider before choosing?",
    "What broke in {name}, and how did you debug it?",
    "If you rebuilt {name} today, what would you do differently and why?",
    "What was your specific contribution to {name} — not the team's, yours?",
    "What were the performance or scaling constraints you hit on {name}?",
    "Walk me through the architecture of {name}. What were the trade-offs?",
]

EXPERIENCE_ANGLES = [
    "At {company}, what was the most technically complex problem you personally solved?",
    "At {company}, what did you own end-to-end — not collaborate on, but own?",
    "What was the biggest mistake you made at {company} and how did you recover?",
    "At {company}, what would your manager say your weakest technical area was?",
    "Describe a production incident at {company} — what happened, what was your role?",
]

BEHAVIORAL_ANGLES = [
    "Tell me about a time a technical decision you made turned out to be wrong. What happened?",
    "Describe a situation where you disagreed with your team on a technical approach. What did you do?",
    "Give me a specific example of a deadline you missed. What caused it and what did you do?",
    "Tell me about a time you had to learn something critical very quickly. How did you approach it?",
    "Describe the most stressful technical situation you've been in. How did you handle it?",
]


# ── Topic detection ────────────────────────────────────────────────────────────

def _detect_topic(question: str, resume_data: dict) -> str:
    q = question.lower()

    for p in resume_data.get("projects", []):
        name = (p.get("name") or "").lower()
        desc = (p.get("description") or "").lower()
        keywords = [w for w in (name + " " + desc).split() if len(w) > 3][:8]
        if name and (name in q or any(kw in q for kw in keywords)):
            return f"Project: {p.get('name', '?')}"

    for e in resume_data.get("experience", []):
        company = (e.get("company") or "").lower()
        role    = (e.get("role") or "").lower()
        if company and (company in q or role in q):
            return f"Experience: {e.get('role')} at {e.get('company')}"

    behavioral_kws = ["conflict", "failure", "challenge", "mistake", "disagree",
                      "pressure", "deadline", "difficult", "wrong", "stressful"]
    if any(kw in q for kw in behavioral_kws):
        return "Behavioral"

    skill_kws = ["python", "react", "sql", "docker", "aws", "ml", "ai", "llm",
                 "pytorch", "tensorflow", "api", "database", "cloud"]
    if any(kw in q for kw in skill_kws):
        return "Skills"

    return "General"


def _consecutive_topic(history: list[dict], resume_data: dict) -> tuple[str, int]:
    if not history:
        return ("", 0)

    topics = [_detect_topic(h["question"], resume_data) for h in history]
    last_topic = topics[-1]
    count = 0
    for t in reversed(topics):
        if t == last_topic:
            count += 1
        else:
            break
    return (last_topic, count)


# ── First question ─────────────────────────────────────────────────────────────

def generate_first_question(resume_data: dict, role: str, level: str) -> str:
    resume_summary = summarize_resume(resume_data)

    has_projects   = bool(resume_data.get("projects"))
    has_experience = bool(resume_data.get("experience"))
    name           = resume_data.get("name", "").split()[0] if resume_data.get("name") else ""

    if has_experience:
        opener_instruction = "Ask them to introduce themselves in under 60 seconds — name, most recent role, and one technical thing they're most proud of. No life story."
    elif has_projects:
        opener_instruction = "Ask them to briefly introduce themselves and immediately name the single project they're most technically proud of and why."
    else:
        opener_instruction = "Ask them to introduce themselves and state the most complex technical concept they genuinely understand deeply."

    prompt = f"""You are interviewing {name or 'a candidate'} for {role} ({level}).

Their resume:
{resume_summary}

Open the interview. {opener_instruction}

Rules:
- Do NOT say "Tell me about yourself" — that's too open
- Be specific and direct from the first sentence
- Maximum 2 sentences

Output format:
REACTION: [empty for first question]
QUESTION: [opening question]"""

    result = chat(
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
) -> dict | None:
    questions_asked = len(history)

    if questions_asked >= MAX_QUESTIONS:
        return None

    current_turn = history[-1]
    last_question = current_turn["question"]
    last_answer = current_turn["answer"]

    answer_quality = assess_answer(last_answer, last_question)
    is_misbehavior = answer_quality in ("gibberish", "evasive")

    # ── Termination ───────────────────────────────────────────────────────────
    if is_misbehavior and misbehavior_count >= MAX_MISBEHAVIOR - 1:
        return {
            "reaction": random.choice(TERMINATION_MESSAGES),
            "question": "",
            "terminated": True,
            "answer_quality": answer_quality,
        }

    # ── Strike warning ────────────────────────────────────────────────────────
    strike_warning = ""
    if is_misbehavior and misbehavior_count < MAX_MISBEHAVIOR - 1:
        strike_warning = STRIKE_WARNINGS[min(misbehavior_count, len(STRIKE_WARNINGS) - 1)]

    # ── Natural end (deterministic) ───────────────────────────────────────────
    if questions_asked >= MIN_QUESTIONS and not is_misbehavior:
        if _should_end_deterministic(resume_data, history):
            return None

    # ── Topic-loop detection ──────────────────────────────────────────────────
    last_topic, consecutive_count = _consecutive_topic(history, resume_data)
    force_topic_change = (consecutive_count >= 2)

    # ── Coverage + next target ────────────────────────────────────────────────
    coverage    = build_coverage(resume_data, history)
    next_target = pick_next_target(
        coverage         = coverage,
        questions_asked  = questions_asked,
        history          = history,
        resume_data      = resume_data,
        force_skip_label = last_topic if force_topic_change else None,
    )

    resume_summary = summarize_resume(resume_data)

    convo_lines = []
    for i, h in enumerate(history):
        convo_lines.append(f"Q{i+1}: {h['question']}")
        convo_lines.append(f"A{i+1}: {h['answer']}")
    convo = "\n".join(convo_lines)

    # ── Reaction + question directives ────────────────────────────────────────
    if answer_quality == "gibberish":
        reaction_directive = f'REACTION must be: "{strike_warning} I could not understand that answer."'
        question_directive = f"Repeat the previous question ({last_question[:80]}), asking them to clarify."

    elif answer_quality == "evasive":
        reaction_directive = f'REACTION must be: "{strike_warning} You didn\'t answer the question."'
        question_directive = f"Ask the same question again ({last_question[:80]}) from a different angle. Make clear you expect a direct answer."

    elif answer_quality == "vague" and not force_topic_change:
        reaction_directive = "REACTION: Politely point out exactly what was generic. E.g. 'You mentioned improving performance, but by how much?'"
        question_directive = (
            "Ask a razor-sharp follow-up that forces specificity on their last answer. "
            "Pick the single vaguest claim they made and demand the concrete detail behind it."
        )

    else:
        if force_topic_change and answer_quality == "vague":
            reaction_directive = "REACTION: Note that the answer was still a bit generic, then gracefully move on to the next topic."
        else:
            reaction_directive = "REACTION: Acknowledge naturally in a brief conversational sentence (e.g., 'Got it, that clarifies things.'), then optionally note one gap."
        question_directive = f"Next question (NEW TOPIC — do NOT reference what was just discussed): {next_target['instruction']}"

    already_asked_types = _extract_question_patterns(history)
    recent_questions_summary = " | ".join(h["question"][:60] for h in history[-3:]) if history else "none"

    prompt = f"""Interviewing: {role} ({level})

Resume:
{resume_summary}

Conversation:
{convo}

---
{reaction_directive}

{question_directive}

Already asked about: {', '.join(already_asked_types) if already_asked_types else 'nothing yet'}
Recent questions (DO NOT repeat or rephrase these): {recent_questions_summary}
{"Strike warning to include verbatim: " + strike_warning if strike_warning else ""}
{"⚠ MANDATORY TOPIC SWITCH: Ask about a completely different part of their resume." if force_topic_change else ""}

RULES:
- REACTION: 1 natural, conversational sentence. No filler, but don't be robotic.
- QUESTION: 1-2 sentences. Specific. Reference something real from their resume.
- Do NOT ask anything already in the "Already asked" list or similar to the recent questions listed above.

REACTION: [your reaction]
QUESTION: [your question]"""

    result = chat(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": prompt}],
        temperature=0.6,
    )

    parsed = parse_response(result)
    parsed["terminated"]      = False
    parsed["answer_quality"]  = answer_quality
    parsed["force_topic_change"] = force_topic_change
    return parsed


# ── Answer quality ─────────────────────────────────────────────────────────────

def assess_answer(answer: str, question: str) -> str:
    if not answer or len(answer.strip()) < 6:
        return "gibberish"

    prompt = f"""Rate this spoken interview answer.

Question: {question}
Answer: {answer}

NOTE: This is a speech-to-text transcript. You MUST ignore typos, grammatical errors, stuttering, and transcript artifacts.

Pick exactly ONE:

gibberish — 100% complete nonsense or completely unrelated random text. Do NOT use this for poor, incorrect, or incomplete answers.
evasive — Deliberate refusal to engage. Examples: "I don't want to answer", "why are you asking this". 
NOT evasive: admitting "I don't know" while attempting to answer.
vague — Attempted to answer but used only contentless generic buzzwords. No specific tools, numbers, or personal actions described.
ok — Made a genuine attempt with at least one concrete element (a specific tool, metric, action, scenario), OR demonstrated conceptual understanding even if imperfect.

When in doubt between ok, vague, and gibberish -> choose ok.

One word only: gibberish, evasive, vague, or ok"""

    result = chat([{"role": "user", "content": prompt}], temperature=0.1)
    word = result.strip().lower().split()[0]
    return word if word in ("gibberish", "evasive", "vague", "ok") else "ok"


# ── Coverage tracking ──────────────────────────────────────────────────────────

def build_coverage(resume_data: dict, history: list[dict]) -> dict:
    all_questions = " ".join(h["question"].lower() for h in history)

    covered   = []
    uncovered = []

    for p in resume_data.get("projects", []):
        name = (p.get("name") or "").lower()
        desc = (p.get("description") or "").lower()
        keywords = [w for w in (name + " " + desc).split() if len(w) > 3][:5]
        hit = name and (name in all_questions or any(kw in all_questions for kw in keywords))
        (covered if hit else uncovered).append({"type": "project", "label": f"Project: {p.get('name','?')}", "data": p})

    for e in resume_data.get("experience", []):
        co = (e.get("company") or "").lower()
        hit = co and co in all_questions
        (covered if hit else uncovered).append({"type": "experience", "label": f"Experience: {e.get('role')} at {e.get('company')}", "data": e})

    raw_skills = resume_data.get("skills") or []
    for grp_name, grp_list in group_skills(raw_skills).items():
        hit = any(s.lower() in all_questions for s in grp_list[:3])
        (covered if hit else uncovered).append({"type": "skill", "label": f"Skills: {grp_name}", "data": grp_list})

    behavioral_done = any(
        kw in all_questions for kw in
        ["conflict", "failure", "challenge", "mistake", "disagreed", "pressure", "deadline", "difficult", "wrong"]
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
    """
    Picks the next interview target by randomly selecting from all UNCOVERED topics.
    This ensures skills, projects, and experiences are dynamically mixed.
    """
    uncovered = coverage["uncovered"]

    def _skip(item: dict) -> bool:
        if not force_skip_label:
            return False
        return item["label"] == force_skip_label or item["label"].startswith(force_skip_label)

    # 1. Closing stretch check (Always reserve for the very end)
    if questions_asked >= MAX_QUESTIONS - 1:
        beh = next((u for u in uncovered if u["type"] == "behavioral" and not _skip(u)), None)
        if beh:
            return {"label": "Behavioral", "instruction": random.choice(BEHAVIORAL_ANGLES)}
        return {"label": "Closing", "instruction": "Ask why they want this specific role and what they'd contribute in the first 90 days."}

    # 2. Gather all valid uncovered targets and pick one randomly to ensure variety
    valid_targets = [u for u in uncovered if not _skip(u)]

    if valid_targets:
        target = random.choice(valid_targets)

        if target["type"] == "project":
            p = target["data"]
            name = p.get("name", "this project")
            angle = random.choice(PROJECT_ANGLES).format(name=name)
            return {"label": target["label"], "instruction": angle}

        elif target["type"] == "experience":
            e = target["data"]
            company = e.get("company", "this company")
            angle = random.choice(EXPERIENCE_ANGLES).format(company=company)
            return {"label": target["label"], "instruction": angle}

        elif target["type"] == "skill":
            skills_list = target["data"][:3] if isinstance(target["data"], list) else []
            skill_str = ", ".join(skills_list) if skills_list else target["label"]
            return {
                "label": target["label"],
                "instruction": f"Test practical understanding of {skill_str} by providing a real-world engineering scenario, architectural trade-off, or edge case for them to solve. Do NOT ask for textbook definitions."
            }

        elif target["type"] == "behavioral":
            return {"label": "Behavioral", "instruction": random.choice(BEHAVIORAL_ANGLES)}

    # 3. If everything is covered but we haven't reached MIN_QUESTIONS, go deeper randomly
    covered = coverage["covered"]
    valid_covered = [c for c in covered if not _skip(c) and c["type"] in ("project", "experience", "skill")]

    if valid_covered and questions_asked < MIN_QUESTIONS:
        target = random.choice(valid_covered)

        if target["type"] == "project":
            p = target["data"]
            name = p.get("name", "this project")
            angle = random.choice(PROJECT_ANGLES).format(name=name)
            return {"label": target["label"], "instruction": f"Go deeper on {name}: {angle}"}

        elif target["type"] == "experience":
            e = target["data"]
            company = e.get("company", "this company")
            angle = random.choice(EXPERIENCE_ANGLES).format(company=company)
            return {"label": target["label"], "instruction": f"Go deeper on their time at {company}: {angle}"}

        elif target["type"] == "skill":
            skills_list = target["data"][:3] if isinstance(target["data"], list) else []
            skill_str = ", ".join(skills_list) if skills_list else target["label"]
            return {
                "label": target["label"],
                "instruction": f"Go deeper on {skill_str}. Present a highly complex edge case or scaling problem and ask how they would handle it."
            }

    # 4. Fallback Closing
    return {"label": "Closing", "instruction": "Wrap up — ask what excites them most about this role and what they'd want to learn."}


# ── Deterministic end check ────────────────────────────────────────────────────

def _should_end_deterministic(resume_data: dict, history: list[dict]) -> bool:
    if len(history) < MIN_QUESTIONS:
        return False

    coverage  = build_coverage(resume_data, history)
    uncovered = coverage["uncovered_labels"]

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
        if "introduce" in q or "background" in q:
            patterns.append("intro/background")
        if "challenge" in q or "difficult" in q or "hard" in q:
            patterns.append("challenges")
        if "fail" in q or "mistake" in q or "wrong" in q:
            patterns.append("failures")
        if "trade" in q or "decision" in q or "chose" in q or "why did you" in q:
            patterns.append("decisions/trade-offs")
        if "scale" in q or "performance" in q or "optim" in q:
            patterns.append("performance/scale")
        if "team" in q or "conflict" in q or "disagree" in q:
            patterns.append("teamwork/conflict")
        if "architecture" in q or "design" in q or "structure" in q:
            patterns.append("system design")
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

    flat = flatten_skills(skills)
    groups: dict[str, list[str]] = {}
    categories = {
        "Frontend": ["react", "vue", "angular", "html", "css", "javascript", "typescript", "next", "tailwind"],
        "Backend":  ["python", "node", "django", "flask", "fastapi", "java", "spring", "express", "go", "rust"],
        "Database": ["sql", "postgres", "mysql", "mongodb", "redis", "firebase", "supabase"],
        "DevOps":   ["docker", "kubernetes", "aws", "gcp", "azure", "ci", "cd", "terraform", "linux"],
        "ML/AI":    ["tensorflow", "pytorch", "sklearn", "pandas", "numpy", "ml", "ai", "llm", "nlp"],
    }
    for s in flat:
        sl = s.lower()
        placed = False
        for group, keywords in categories.items():
            if any(kw in sl for kw in keywords):
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
        lines.append(f"Name: {resume_data['name']}")
    if resume_data.get("summary"):
        lines.append(f"Summary: {resume_data['summary']}")

    raw_skills = resume_data.get("skills")
    if raw_skills:
        if isinstance(raw_skills, dict):
            for group, items in raw_skills.items():
                if items: lines.append(f"Skills — {group}: {', '.join(items[:10])}")
        else:
            lines.append(f"Skills: {', '.join(raw_skills[:20])}")

    if resume_data.get("projects"):
        lines.append(f"\nProjects ({len(resume_data['projects'])} total):")
        for p in resume_data["projects"]:
            tech = ", ".join(p.get("tech_stack", []))
            lines.append(f"  [{p.get('name','?')}] {p.get('description','')[:120]} | Stack: {tech}")

    if resume_data.get("experience"):
        lines.append(f"\nExperience ({len(resume_data['experience'])} total):")
        for e in resume_data["experience"]:
            emp = f" ({e['employment_type']})" if e.get("employment_type") else ""
            warn = " ⚠ verify duration" if e.get("_warnings") else ""
            lines.append(f"  [{e.get('role')}{emp} at {e.get('company')} | {e.get('duration','')}{warn}] {e.get('description','')[:100]}")

    if resume_data.get("education"):
        lines.append("\nEducation:")
        for edu in resume_data["education"]:
            dur = edu.get("duration") or edu.get("year", "")
            lines.append(f"  {edu.get('degree')} — {edu.get('institution')} ({dur})")

    return "\n".join(lines)


# ── Parsing ────────────────────────────────────────────────────────────────────

def parse_response(text: str) -> dict:
    reaction = question = ""
    for line in text.strip().splitlines():
        if line.upper().startswith("REACTION:"):
            reaction = line[len("REACTION:"):].strip()
        elif line.upper().startswith("QUESTION:"):
            question = line[len("QUESTION:"):].strip()
    if not question:
        question = text.strip()
    return {"reaction": reaction, "question": question}


def extract_question_only(text: str) -> str:
    return parse_response(text).get("question") or text.strip()