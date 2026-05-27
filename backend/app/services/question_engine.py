"""
question_engine.py — v7

Interviewer quality improvements:
  1. SYSTEM_PROMPT — adaptive tone, not just "cold and dominant"
  2. generate_first_question — varied openers based on resume type
  3. generate_follow_up — cleaner prompt, no leaked internals, richer directives
  4. pick_next_target — specific question angles per section type
  5. should_end — removed extra LLM call, replaced with deterministic logic
  6. Question variety enforcement — banned question patterns, angle rotation
  7. vague handling — gives a pointed recovery question, not a dead-end demand
"""

import random
from app.services.groq_client import chat

MAX_QUESTIONS   = 10
MIN_QUESTIONS   = 6
MAX_MISBEHAVIOR = 3

# ── Interviewer persona ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a senior technical interviewer with 15 years of engineering experience.
You are direct, exacting, and not easily impressed — but you are also fair and precise.

TONE BY SITUATION:
- Strong answer → acknowledge in 2-4 words, then immediately go deeper: "Right. Now explain why you chose X over Y."
- Partial answer → identify exactly what's missing, ask for it: "You mentioned X. What was the actual impact?"
- Vague answer → call out what's missing specifically: "That tells me nothing about your role. What did YOU build?"
- Wrong answer → correct it directly, don't soften: "That's not accurate. [correct fact]. Given that, how would you approach it?"
- Nervous/rambling → cut it: "Stop. One sentence: what was the outcome?"

BANNED PHRASES — never use these:
- "great", "good job", "interesting", "I see", "perfect", "absolutely"
- "Tell me about yourself" (too generic)
- "Can you walk me through" (use direct questions instead)
- Any variation of "That's a good point"

QUESTION STYLE:
- Ask exactly one question per turn
- Questions must be specific to what the candidate actually said or what's on their resume
- Prefer: "How did you handle X when Y happened?" over "Tell me about X"
- Prefer: "What was the latency before and after your optimization?" over "How did that go?"
- For projects: ask about decisions, trade-offs, failures, specific numbers
- For experience: ask about scope, ownership, what broke, what they'd do differently
- For skills: test understanding with a scenario, not a definition

FORMAT — always exactly two lines:
REACTION: [your response to their last answer — 1 sentence, direct]
QUESTION: [your next question — 1-2 sentences, specific]"""


STRIKE_WARNINGS = [
    "That's a warning. One more answer like that and this interview ends.",
    "Second warning. I need real answers or we stop here.",
]

TERMINATION_MESSAGES = [
    "Three strikes. This interview is over. Come back when you're prepared to engage seriously.",
    "I've heard enough. We're done — this isn't the level we need.",
    "Interview terminated. Your answers don't meet the bar for this role.",
]

# Question angle rotation — prevents "tell me about X" every time
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


# ── First question ─────────────────────────────────────────────────────────────

def generate_first_question(resume_data: dict, role: str, level: str) -> str:
    resume_summary = summarize_resume(resume_data)

    # Pick opener angle based on what's most prominent on the resume
    has_projects    = bool(resume_data.get("projects"))
    has_experience  = bool(resume_data.get("experience"))
    name            = resume_data.get("name", "").split()[0] if resume_data.get("name") else ""

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
    last_answer: str,
    misbehavior_count: int = 0,
) -> dict | None:
    questions_asked = len(history) + 1

    if questions_asked > MAX_QUESTIONS:
        return None

    last_question  = history[-1]["question"] if history else ""
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

    # ── Natural end (deterministic — no extra LLM call) ───────────────────────
    if questions_asked > MIN_QUESTIONS and not is_misbehavior:
        if _should_end_deterministic(resume_data, history):
            return None

    # ── Coverage + next target ────────────────────────────────────────────────
    coverage    = build_coverage(resume_data, history)
    next_target = pick_next_target(coverage, questions_asked, history, resume_data)

    resume_summary = summarize_resume(resume_data)

    # Build clean conversation — no internal labels leaked to LLM
    convo_lines = []
    for i, h in enumerate(history):
        convo_lines.append(f"Q{i+1}: {h['question']}")
        convo_lines.append(f"A{i+1}: {h['answer']}")
    convo_lines.append(f"Latest answer: {last_answer}")
    convo = "\n".join(convo_lines)

    # ── Reaction directive ────────────────────────────────────────────────────
    if answer_quality == "gibberish":
        reaction_directive = f'REACTION must be: "{strike_warning} That answer made no sense."'
        question_directive = f"Repeat the previous question ({last_question[:80]}), rephrased more pointedly."
    elif answer_quality == "evasive":
        reaction_directive = f'REACTION must be: "{strike_warning} You didn\'t answer the question."'
        question_directive = f"Ask the same question again ({last_question[:80]}) from a different angle. Make clear you expect a direct answer."
    elif answer_quality == "vague":
        reaction_directive = "REACTION: Point out exactly what was generic. E.g. 'You said you improved performance — by how much, using what?'"
        question_directive = (
            f"Ask a razor-sharp follow-up that forces specificity on their last answer. "
            f"Pick the single vaguest claim they made and demand the concrete detail behind it. "
            f"If they can't recover, next question will move on."
        )
    else:
        reaction_directive = "REACTION: Acknowledge in 2-4 words max, then optionally note one gap."
        question_directive = f"Next question: {next_target['instruction']}"

    # Enforce question variety — tell LLM what angle to take
    already_asked_types = _extract_question_patterns(history)

    prompt = f"""Interviewing: {role} ({level})

Resume:
{resume_summary}

Conversation:
{convo}

---
{reaction_directive}

{question_directive}

Already asked about: {', '.join(already_asked_types) if already_asked_types else 'nothing yet'}
{"Strike warning to include verbatim: " + strike_warning if strike_warning else ""}

RULES:
- REACTION: 1 sentence maximum. No filler. No praise.
- QUESTION: 1-2 sentences. Specific. Reference something real from their resume or their answer.
- Do NOT ask "tell me about X" — ask what specifically happened, what they decided, what the result was.
- Do NOT ask anything already in the "Already asked" list.

REACTION: [your reaction]
QUESTION: [your question]"""

    result = chat(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": prompt}],
        temperature=0.6,
    )

    parsed = parse_response(result)
    parsed["terminated"]     = False
    parsed["answer_quality"] = answer_quality
    return parsed


# ── Answer quality ─────────────────────────────────────────────────────────────

def assess_answer(answer: str, question: str) -> str:
    if not answer or len(answer.strip()) < 6:
        return "gibberish"

    prompt = f"""Rate this interview answer.

Question: {question}
Answer: {answer}

Pick exactly ONE:

gibberish — Nonsense, random text, completely off-topic, or a joke. Examples: "asdf lol", "I like pizza", "!!!"

evasive — Deliberate refusal to engage. Examples: "I don't want to answer", "next question please", "why are you asking this"
NOT evasive: admitting "I don't know" while attempting to answer, or showing unfamiliarity with a topic.

vague — Attempted to answer but every statement is a contentless generic claim. No tool names, no numbers, no personal role described, no concrete example. Pure buzzwords only.
Example: "I worked on machine learning and improved the model performance using Python."
NOT vague if they mentioned: any specific library, any number/metric, their specific role/action, any concrete scenario.

ok — Made a genuine attempt with at least one concrete element: a specific tool, a metric, a personal action, a real scenario, or demonstrated conceptual understanding even if imperfect.
When in doubt between ok and vague → choose ok.

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


def pick_next_target(coverage: dict, questions_asked: int, history: list[dict], resume_data: dict) -> dict:
    uncovered = coverage["uncovered"]

    # Closing stretch
    if questions_asked >= MAX_QUESTIONS - 1:
        beh = next((u for u in uncovered if u["type"] == "behavioral"), None)
        if beh:
            return {"label": "Behavioral", "instruction": random.choice(BEHAVIORAL_ANGLES)}
        return {"label": "Closing", "instruction": "Ask why they want this specific role and what they'd contribute in the first 90 days."}

    # Projects first — most signal-rich
    proj = next((u for u in uncovered if u["type"] == "project"), None)
    if proj:
        p = proj["data"]
        name = p.get("name", "this project")
        angle = random.choice(PROJECT_ANGLES).format(name=name)
        return {"label": proj["label"], "instruction": angle}

    # Experience next
    exp = next((u for u in uncovered if u["type"] == "experience"), None)
    if exp:
        e = exp["data"]
        company = e.get("company", "this company")
        angle = random.choice(EXPERIENCE_ANGLES).format(company=company)
        return {"label": exp["label"], "instruction": angle}

    # Skills
    skill = next((u for u in uncovered if u["type"] == "skill"), None)
    if skill:
        skills_list = skill["data"][:3] if isinstance(skill["data"], list) else []
        skill_str = ", ".join(skills_list) if skills_list else skill["label"]
        return {
            "label": skill["label"],
            "instruction": f"Test practical understanding of {skill_str} — give them a real scenario or edge case, not a definition question.",
        }

    # Behavioral
    beh = next((u for u in uncovered if u["type"] == "behavioral"), None)
    if beh:
        return {"label": "Behavioral", "instruction": random.choice(BEHAVIORAL_ANGLES)}

    # Everything covered — go deeper
    if questions_asked < MIN_QUESTIONS:
        last_q = history[-1]["question"] if history else ""
        last_a = history[-1]["answer"] if history else ""
        return {
            "label": "Technical depth",
            "instruction": f"The candidate mentioned something in their last answer. Pick the most technically interesting claim and probe deeper — ask for the specific implementation detail, the failure case, or the scale.",
        }

    return {"label": "Closing", "instruction": "Wrap up — ask what excites them most about this role and what they'd want to learn."}


# ── Deterministic end check (replaces extra LLM call) ─────────────────────────

def _should_end_deterministic(resume_data: dict, history: list[dict]) -> bool:
    """
    Ends the interview when:
    - All projects and experiences have been covered, AND
    - At least one behavioral question was asked, AND
    - MIN_QUESTIONS have been asked
    No LLM call needed.
    """
    if len(history) < MIN_QUESTIONS:
        return False

    coverage  = build_coverage(resume_data, history)
    uncovered = coverage["uncovered_labels"]

    # Must have covered all projects and experiences
    hard_uncovered = [u for u in uncovered if not u.startswith("Skills:") and u != "Behavioral"]
    if hard_uncovered:
        return False

    # Must have done behavioral
    if "Behavioral" in uncovered:
        return False

    return True


# ── Question variety helpers ───────────────────────────────────────────────────

def _extract_question_patterns(history: list[dict]) -> list[str]:
    """
    Extracts high-level patterns from asked questions to prevent repetition.
    Returns a short list of what's already been covered in terms of question type.
    """
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