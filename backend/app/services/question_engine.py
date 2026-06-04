"""
question_engine.py — Production v19.1

Fixes vs v19.0:
  1. _ASSESS_SYSTEM rewritten with explicit STT-leniency first-pass rule:
     garbled tool names that fit the syntactic shape of a tool name get
     benefit of the doubt; the LLM must judge INTENT and STRUCTURE, not
     exact spelling.
  2. No-code / automation tool keywords added to _assess_heuristic (Make,
     Zapier, n8n, Apify, Airtable, webhook, node, trigger, workflow, etc.)
     so the heuristic fallback doesn't penalise candidates from automation
     roles.
  3. consecutive_vague_count parameter added to generate_follow_up.
     After 1 vague follow-up with no improvement the engine forces a topic
     change rather than nagging again. The nag loop is gone.
  4. new_consecutive_vague computed and returned on every path so
     interview.py can persist it in the session.
  5. _trim_question reverse-scan fix retained from v19.0.
  6. Markdown code-fence stripping in parse_response retained.
"""

import re
import time
import json
import random
import logging
from app.services.groq_client import chat

logger = logging.getLogger(__name__)

MAX_QUESTIONS = 12
MIN_QUESTIONS = 8
MAX_IDK_COUNT = 4

# ── Strike warning pools ──────────────────────────────────────────────────────

EVASIVE_STRIKE_WARNINGS = [
    "That's not what I asked — I need a direct answer.",
    "You're still not answering. Engage with the question or we can't continue.",
    "Last chance — give me a real answer or we're done here.",
]

RUDE_STRIKE_WARNINGS = [
    "That's not how we talk here. Keep it professional.",
    "I need you to drop the attitude — this is a professional setting.",
    "Final warning: one more outburst and this interview is over.",
]

FABRICATED_STRIKE_WARNINGS = [
    "That's not on your resume — answer from what you've actually built.",
    "I'm not seeing that experience in your background. Stick to what's real.",
    "Final warning — I need answers grounded in your actual work history.",
]

GIBBERISH_STRIKE_WARNINGS = [
    "I couldn't follow that. Give me a clear, direct answer.",
    "That still didn't make sense — I need you to be coherent.",
    "I need you to focus. Answer clearly or we're done.",
]

assert (
    len(EVASIVE_STRIKE_WARNINGS)
    == len(RUDE_STRIKE_WARNINGS)
    == len(FABRICATED_STRIKE_WARNINGS)
    == len(GIBBERISH_STRIKE_WARNINGS)
), "All strike warning pools must be the same length"

MAX_MISBEHAVIOR = len(EVASIVE_STRIKE_WARNINGS) + 1  # = 4

TERMINATION_MESSAGES = [
    "I've heard enough. We're done — this isn't the standard we need.",
    "Interview terminated. I can't continue under these conditions.",
    "That's a hard stop. This interview is over.",
    "We're done here. This isn't working.",
]


# ── Fast-path pattern lists ───────────────────────────────────────────────────

RUDE_PATTERNS = [
    r"\bstupid\s*(question|interviewer|test)?\b",
    r"\bidiot\b",
    r"\bdumb\s+(question|interviewer)\b",
    r"\bwaste\s+(of\s+)?time\b",
    r"\bshut\s+up\b",
    r"\bf+u+c+k\b",
    r"\bscrew\s+(this|you)\b",
    r"\basshole\b",
    r"\bpiss\s+off\b",
    r"\bshove\s+it\b",
    r"\bgoddamn\b",
    r"\bstfu\b",
    r"\bmoron\b",
    r"\byou'?re\s+(so\s+)?(annoying|stupid|an\s+idiot)\b",
    r"\bthis\s+is\s+(a\s+)?bullshit\b",
    r"\bscrew\s+your\s+interview\b",
    r"\bi\s+don'?t\s+give\s+a\b",
]

EXPLICIT_REFUSAL_PATTERNS = [
    r"\bi\s+(just\s+)?don'?t\s+want\s+to\s+(tell|answer|say|discuss|talk\s+about)\b",
    r"\bi\s+won'?t\s+(answer|tell|say|discuss)\b",
    r"\bi\s+refuse\s+to\b",
    r"\bi'?m\s+not\s+going\s+to\s+(answer|tell|say|discuss)\b",
    r"\bi'?m\s+not\s+(going\s+to\s+)?(answer|telling)\s+(you|this|that)\b",
    r"\bwhy\s+(do|are)\s+you\s+keep\s+asking\b",
    r"\byou\s+(already\s+)?asked\s+(me\s+)?(this|that)\s+(already|again|before)\b",
    r"\bi\s+already\s+told\s+you\b",
    r"\bstop\s+(asking|repeating)\b",
    r"\bnone\s+of\s+your\s+(business|concern)\b",
    r"\bi\s+don'?t\s+want\s+to\b$",
    r"\bi\s+don'?t\s+feel\s+like\s+answer",
    r"\bno\s+comment\b",
    r"\bi\s+choose\s+not\s+to\b",
    r"\bwhy\s+do\s+you\s+keep\b",
]

IDK_FAST_PATTERNS = [
    r"\bi\s+don'?t\s+know\b",
    r"\bi\s+do\s+not\s+know\b",
    r"\bi'?m\s+not\s+sure\b",
    r"\bi\s+haven'?t\s+(used|worked\s+with|seen|touched|implemented|done)\b",
    r"\bi\s+need\s+to\s+brush\s+up\b",
    r"\bi'?m\s+not\s+familiar\s+with\b",
    r"\bi\s+can'?t\s+remember\b",
    r"\bi\s+don'?t\s+recall\b",
    r"\bno\s+idea\b",
    r"\bnot\s+sure\b",
    r"\bunsure\b",
]


# ── LLM call with retry ───────────────────────────────────────────────────────

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


# ── Sanitization / truncation ─────────────────────────────────────────────────

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


# ── Interviewer persona ───────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a senior technical interviewer with 15 years of engineering experience.
You've interviewed hundreds of engineers across startups and large tech companies.
You're direct, fair, and you sound like a real person — not a scoring rubric.

YOUR VOICE:
- Measured, not cold. Professional, not robotic. Never sycophantic.
- Good answer: "That's a solid approach." or "Makes sense." — not "Great!" or "Wow!"
- Missing something: Name it specifically. "You covered the what, but I'm still missing the why."
- Wrong answer: Correct it cleanly. "That's not quite right — [correct fact]. Here's why it matters."
- Rambling: Cut it off. "Let me stop you there."
- Nervous candidate: Brief, calm acknowledgement and move on. Don't over-reassure.

BANNED WORDS/PHRASES — NEVER USE:
"great", "amazing", "awesome", "interesting", "I see", "perfect", "absolutely",
"fantastic", "excellent", "wonderful", "sure thing", "of course", "certainly",
"no problem", "that's a great question", "good question", "I appreciate that"

ACKNOWLEDGEMENT — rotate naturally, never repeat the same one twice in a row:
"Got it.", "Okay.", "Fair enough.", "That makes sense.", "Noted.", "Understood.",
"Alright.", "Right.", "Okay, that helps.", "Makes sense.", "Clear.", "Good to know.",
"Mm-hmm.", "Right, okay.", "That tracks.", "Sure.", "Okay, I hear you."

CRITICAL FORMAT RULES — no exceptions:
- OUTPUT EXACTLY TWO LINES. No more.
- Line 1: REACTION: [one sentence, NO question mark, reacts to what they just said]
- Line 2: QUESTION: [1-2 sentences, specific, ends with exactly one question mark]
- Do NOT include bullet points, numbered lists, or multiple questions.
- Do NOT recap the candidate's resume or previous answers inside the question.
- Do NOT preface with "Based on your resume..." or "You mentioned earlier..."
- The QUESTION line must be self-contained and under 200 characters.

QUESTION STYLE:
- One question per turn. Always.
- Ground every question in something from their resume or their last answer.
- Projects: decisions made, trade-offs, what broke, what you'd redo, specific numbers.
- Experience: what you personally owned, what went wrong, what your manager would say.
- Skills: real scenario, edge case, or architectural trade-off — never a definition.
- If their answer was vague, follow up on the single vaguest claim.

BANNED QUESTION STARTERS:
- "Tell me about yourself" (too open)
- "Can you walk me through" (use direct questions)
- "What is [X]?" (no definitions)
- "Could you explain..." (too soft — ask directly)

STT AWARENESS — apply always:
The candidate's answers are transcribed by a speech-to-text engine and WILL contain mishearings.
Common patterns: "pie torch" = PyTorch, "post gres" = PostgreSQL, "kube rnetes" = Kubernetes,
"a w s" = AWS, "l l m" = LLM, "fast api" = FastAPI, "jay son" = JSON, phonetic spellings
of abbreviations, garbled tool/project names from their resume.
Rules:
- ALWAYS interpret transcription errors charitably if the intent is clear from context.
- ALWAYS use the CORRECT spelling in your questions and reactions — never echo the garbled form back.
- NEVER penalise an answer as gibberish or evasive due to obvious STT noise when real content is present.
- If a term is genuinely ambiguous even with context, ask a clarifying follow-up rather than assuming wrong intent."""


PROJECT_ANGLES = [
    "What was the hardest technical decision you made on {name}, and what made it hard?",
    "What broke in {name} — specifically, what was the root cause and how long did it take to find?",
    "If you rebuilt {name} from scratch today, what's the one thing you'd do completely differently?",
    "What was your specific personal contribution to {name} — not the team's, yours?",
    "What performance or scaling limit did you hit on {name}, and exactly how did you address it?",
    "What architectural trade-off did you make in {name} that you're still not sure was right?",
    "What was the biggest bug or incident in {name}, and what caused it?",
    "How did you test {name} — what did your coverage look like and what did it miss?",
    "Who were the users of {name}, and how did their real usage surprise you?",
    "What would a code review of {name} surface as a weakness?",
]

EXPERIENCE_ANGLES = [
    "At {company}, what's the most technically complex problem you personally solved — what was your reasoning?",
    "At {company}, what did you own completely end-to-end — not collaborate on, but own?",
    "What was the biggest technical mistake you made at {company} and what did you do about it?",
    "At {company}, what would your tech lead say was your weakest area?",
    "Tell me about a production incident at {company} — what happened and what was your specific role?",
    "At {company}, what was something you shipped that you wish you'd done differently?",
    "What did you learn at {company} that you couldn't have learned anywhere else?",
    "At {company}, how did you handle disagreements about technical direction with your team?",
]

BEHAVIORAL_ANGLES = [
    "Tell me about a technical call you made that turned out to be wrong — what happened next?",
    "Describe a time you disagreed with your team on a technical direction — what did you actually do?",
    "Give me a specific example of a deadline you missed — what caused it and how did you handle it?",
    "Tell me about a time you had to get up to speed on something critical very fast — how did you approach it?",
    "What's the most pressure you've been under technically — what was the situation and how did you get through it?",
    "Tell me about a time you had to say no to a feature request — how did you handle it?",
    "Describe a time you had to debug something with almost no information — what was your process?",
    "Tell me about a time you pushed back on a decision from someone more senior — what happened?",
]


# ── Answer quality assessment ─────────────────────────────────────────────────

_QUALITY_LABELS = frozenset({
    "gibberish", "evasive", "vague", "ok", "rude", "fabricated", "admitted_gap"
})

# FIX v19.1: STT-leniency is now the FIRST rule the model reads, before any
# label definitions. The core insight: judge INTENT and STRUCTURE, not spelling.
# "Anet", "sapier", "appify" are clearly STT renderings of n8n, Zapier, Apify.
# Any word that appears to be a proper noun (capitalised concept, product-name
# shape) inside a substantive sentence should be treated as a tool name.
_ASSESS_SYSTEM = """You are an interview answer quality judge. Your job is to assess whether the candidate
actually answered the question — not whether their grammar is perfect.

━━━ RULE 0 — STT LENIENCY (read this first, apply it always) ━━━━━━━━━━━━━━━━
This answer came through a Speech-to-Text engine. Assume words may be misspelled,
technical names may be phonetically garbled, and sentences may be cut off mid-thought.

Apply benefit of the doubt when:
• A word looks like a garbled tool/product name but fits the context
  (e.g. "anet" or "n8n", "sapier"→"Zapier", "appify"→"Apify", "cohere"→"Cohere",
   "lama"→"LLaMA", "open ai"→"OpenAI", "make"→"Make.com automation tool")
• A sentence is cut off but the words before the cut contain real content
• The candidate uses informal phrasing for a real concept
  (e.g. "the Google sheets node that adds rows" = Google Sheets append node)

Do NOT penalise for:
• Misspelled tool names when the context makes the tool obvious
• Filler words (um, uh, like) mixed into a substantive answer
• Informal or imprecise phrasing when a real concept is being described
• Short answers that contain one genuinely specific thing

━━━ RULE 1 — PARROTING CHECK (mandatory, run before labelling) ━━━━━━━━━━━━━━
Strip every word that appeared in the question. What remains?
If only pronouns and filler remain with zero real content — label evasive.
If genuine content remains (even one real tool, step, or concept) — continue to Rule 2.
Exception: if what remains is "I don't know" or similar → admitted_gap, not evasive.

━━━ RULE 2 — LABEL (first match wins) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

rude
  Hostile, offensive, or aggressive language toward the interviewer or process.

gibberish
  Completely incoherent, random characters, or literally nothing understandable.
  Messy STT is NOT gibberish unless zero meaning survives.

fabricated
  Claims specific projects, companies, or degrees clearly absent from the resume.
  NOT fabricated: adding detail to a resume item; technical synonyms; more depth
  on a listed role.

evasive
  Deliberately avoids the question; pure parrot (see Rule 1); "it depends" alone
  with nothing else; explicit refusal; topic switch with no content.

admitted_gap
  Explicitly says they don't know, can't remember, haven't used it, or need to study it.
  NOT admitted_gap when real content follows the uncertainty hedge.

vague
  Relevant and genuine BUT only general statements — no specific tool named, no
  concrete decision described, no number, no trade-off, no named methodology.
  Use this ONLY when the answer has zero specific anchors. If even one specific
  concept, tool name (even garbled), step name, or metric appears → ok instead.

ok
  The answer contains at least ONE of:
  • A specific tool or technology named (even if the STT spelling is off)
  • A concrete decision or trade-off described
  • A specific method, step, or process named
  • A number, metric, or measurable outcome mentioned
  • Structured reasoning ("first X, then Y, because Z")
  • A real scenario with named actors or systems
  This label should be the DEFAULT when the candidate has clearly tried to answer
  with real content but you are uncertain between vague and ok. Lean toward ok.

━━━ HARD LINE ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
On-topic ≠ answered. If the answer adds nothing beyond what was in the question → evasive.
But if the candidate names ANYTHING specific — even one garbled tool name — that is NOT vague.

Respond ONLY with valid JSON (no markdown, no explanation):
{"quality": "<label>", "reason": "<one concise sentence>"}"""


# At module level — add this near the other pattern constants
CONCESSIVE_GUARDS = frozenset({
    "but", "however", "although", "though", "that", "still", "yet",
    "although", "nonetheless", "nevertheless", "regardless", "even",
})


def assess_answer(answer: str, question: str, resume_data: dict | None = None) -> str:
    """
    v19.2 assess_answer — fast-paths + LLM assessment.

    Fast-path 1 — Empty / too short (< 4 chars) → gibberish
    Fast-path 2 — Explicit rude patterns → rude
    Fast-path 3 — Explicit refusal patterns (with broader concessive guard) → evasive
    Fast-path 4 — Honest IDK (≤ 35 words, no substantive content after hedge) → admitted_gap
    Fast-path 5 — Very short pure parrot (≤ 10 words, ≤ 2 new content words) → evasive
    All other cases → LLM with STT-leniency-first prompt.

    Changes vs v19.1:
      FP3: "but" guard expanded to full CONCESSIVE_GUARDS set + checks 5 words instead of 4.
           Catches "I won't answer that, however I can tell you..." correctly.
      FP4: Word ceiling raised 20 → 35. Added content guard so IDK-prefixed substantive
           answers ("I don't know K8s well but I've used Docker extensively") fall through
           to the LLM instead of being short-circuited as admitted_gap.
      FP5: New-content-word threshold raised 1 → 2. Prevents short specific answers like
           "Redis with TTL expiry" from being mislabelled evasive when question vocabulary
           overlaps with the answer.
    """
    if not answer or len(answer.strip()) < 4:
        return "gibberish"

    a  = answer.strip()
    al = a.lower()

    # ── Fast-path 2: explicit rude language ──────────────────────────────────
    if any(re.search(p, al, re.IGNORECASE) for p in RUDE_PATTERNS):
        return "rude"

    # ── Fast-path 3: explicit refusal ─────────────────────────────────────────
    # Guard: if the candidate follows the refusal phrase with a concessive
    # conjunction ("but", "however", "though", etc.) within 5 words, they are
    # pivoting to real content — let it fall through to the LLM.
    # v19.2: expanded from checking only "but" in 4 words → CONCESSIVE_GUARDS in 5 words.
    for pattern in EXPLICIT_REFUSAL_PATTERNS:
        m = re.search(pattern, al, re.IGNORECASE)
        if m:
            tail_words = al[m.end():].split()
            if len(tail_words) >= 4 and CONCESSIVE_GUARDS.intersection(tail_words[:5]):
                continue
            return "evasive"

    # ── Fast-path 4: short honest IDK → admitted_gap ──────────────────────────
    # v19.2 changes:
    #   - Ceiling raised from 20 → 35 words so longer honest gaps like
    #     "I honestly don't know much about Kubernetes, I've only worked with
    #      Docker Compose so far in my projects" (22 words) are caught here
    #     instead of burning an unnecessary LLM call.
    #   - Content guard added: if the answer contains an IDK phrase BUT ALSO
    #     contains a concessive conjunction or first-person action verb after it,
    #     the candidate is hedging before giving real content. Let the LLM judge.
    #     Example that should fall through:
    #       "I'm not sure about the exact API but I've implemented OAuth2 before."
    #     Example that should still fast-path:
    #       "I don't know much about Terraform, haven't used it."
    if len(a.split()) <= 35:
        if any(re.search(p, al, re.IGNORECASE) for p in IDK_FAST_PATTERNS):
            substantive_after = any(kw in al for kw in [
                "but", "however", "though", "although", "still",
                "i have", "i've", "i did", "i used", "i built",
                "i worked", "i know", "i understand", "i can",
            ])
            if not substantive_after:
                return "admitted_gap"

    # ── Fast-path 5: very short pure parrot → evasive ─────────────────────────
    # Catches answers that are ≤ 10 words and add almost nothing beyond what
    # was already in the question — e.g. "I used caching in the project" in
    # response to "How did you handle caching in that project?"
    #
    # v19.2: threshold raised 1 → 2 new content words.
    # Rationale: a threshold of 1 was too aggressive. A short but GENUINE answer
    # like "Redis with TTL expiry" (3 new words vs question vocab) would have
    # been safe, but answers like "Used caching strategy there" (only 1 new 4+
    # char word: "used") were borderline. Raising to 2 gives short genuine
    # answers more breathing room while still catching pure echo answers.
    #
    # Note: this only fires when len(a_content) >= 2 to avoid false positives
    # on answers that are just one meaningful word (which the LLM handles better).
    a_words = a.split()
    if len(a_words) <= 10:
        q_content = set(re.findall(r'\b\w{4,}\b', question.lower()))
        a_content = set(re.findall(r'\b\w{4,}\b', al))
        if q_content and a_content and len(a_content - q_content) <= 2 and len(a_content) >= 2:
            return "evasive"

    # ── LLM assessment (primary path for all non-trivial answers) ─────────────
    # _ASSESS_SYSTEM has STT-leniency as Rule 0, parroting check as Rule 1,
    # and a lean-toward-ok bias to avoid nag loops on vague answers.
    resume_summary = summarize_resume(resume_data) if resume_data else "No resume provided."
    user_prompt = (
        f"Resume:\n{resume_summary}\n\n"
        f"Question: {question}\n\n"
        f"Answer: {a}"
    )

    try:
        raw = _chat_with_retry(
            [
                {"role": "system", "content": _ASSESS_SYSTEM},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        data  = json.loads(raw)
        label = str(data.get("quality", "vague")).lower().strip()
        if label in _QUALITY_LABELS:
            logger.debug("assess_answer → %s | %s", label, data.get("reason", ""))
            return label
        logger.warning(
            "assess_answer: unexpected label '%s' — falling back to heuristic", label
        )
    except Exception as exc:
        logger.warning("assess_answer LLM failed: %s — using heuristic fallback", exc)

    return _assess_heuristic(al)


def _assess_heuristic(al: str) -> str:
    """
    Rule-based fallback when the LLM is unavailable or returns an unrecognised label.
    Used on < 1% of calls in normal operation — correctness matters more than speed here.

    Logic:
      - has_tech:    answer contains at least one known technology keyword
      - has_metrics: answer contains a number with a meaningful unit (%, ms, x, k, $...)
      - has_struct:  answer uses logical connectives or enumeration language

    Decision tree (first match wins):
      tech + metrics + structure + length > 200  → ok   (detailed, well-rounded)
      tech + (metrics or structure) + length > 80 → ok   (specific with reasoning or numbers)
      tech + length > 40                          → ok   (names a real thing, not one-word)
      structure + length > 80                     → ok   (reasoned answer, no tech kw found)
      length > 80                                 → vague (long but unspecific)
      fallthrough                                 → vague (short, no signals)

    v19.1: lowered ok threshold — any tech mention in a 40+ char answer is ok (was vague).
           structured answer with no tech kw now also returns ok (was vague).
    Unchanged in v19.2.
    """
    tech_kws = [
        # ── Traditional engineering ───────────────────────────────────────────
        "python", "model", "algorithm", "accuracy", "data", "api", "sql", "docker",
        "react", "typescript", "javascript", "node", "django", "flask", "fastapi",
        "kubernetes", "terraform", "aws", "gcp", "azure", "redis", "postgres",
        "machine learning", "neural", "database", "backend", "frontend", "microservice",
        "cache", "queue", "async", "thread", "index", "schema", "endpoint",
        "github", "git", "ci", "cd", "pipeline", "deploy", "container",

        # ── No-code / automation ──────────────────────────────────────────────
        "zapier", "make", "integromat", "n8n", "airtable", "notion", "retool",
        "apify", "phantombuster", "bardeen", "automate", "webhook", "trigger",
        "workflow", "automation", "integration", "zap", "scenario", "module",
        "no-code", "low-code", "nocode", "lowcode", "bubble", "webflow",
        "monday", "asana", "clickup", "hubspot", "salesforce", "crm",
        "scraper", "scraping", "crawler", "playwright", "selenium", "puppeteer",

        # ── AI / LLM ──────────────────────────────────────────────────────────
        "openai", "anthropic", "cohere", "llama", "llm", "gpt", "claude",
        "embedding", "vector", "pinecone", "weaviate", "langchain", "prompt",
        "fine-tun", "rag", "retrieval", "huggingface",

        # ── Data / ML ─────────────────────────────────────────────────────────
        "pandas", "numpy", "sklearn", "pytorch", "tensorflow", "spark",
        "kafka", "airflow", "dbt", "bigquery", "snowflake", "tableau",
        "imbalance", "overfit", "underfit", "precision", "recall", "f1",
        "train", "test", "validation", "epoch", "batch", "loss",

        # ── Generic specificity signals ───────────────────────────────────────
        # These are short words that appear in real technical answers but rarely
        # in vague filler. "node", "step", "route" etc. are worth a signal even
        # though they're common English words, because in a tech interview context
        # they almost always refer to technical concepts.
        "node", "step", "append", "update", "filter", "route", "parse",
        "authenticate", "token", "oauth", "rest", "graphql", "grpc",
    ]

    has_tech = any(kw in al for kw in tech_kws)

    # Numeric evidence: percentages, decimals, multipliers, latency, scale,
    # money, or row/record/user counts all signal a concrete specific answer.
    has_metrics = bool(re.search(
        r'\d+\s*%'                              # 95%
        r'|\d+\.\d+'                            # 0.94, 3.2
        r'|\d+\s*x\b'                           # 3x faster
        r'|\d+\s*ms'                            # 120ms
        r'|\d+\s*k\b'                           # 50k records
        r'|\$\d+'                               # $200
        r'|\d+\s*(rows|records|users|calls|requests)',  # 1M users
        al,
    ))

    # Structural language: enumeration, causation, ordering, exemplification.
    # Even one of these phrases suggests the candidate is explaining, not just naming.
    has_struct = any(k in al for k in [
        "first", "then", "finally", "because", "for example",
        "we used", "so that", "in order to", "as a result",
        "the reason", "two things", "three things", "one was", "second",
    ])

    length = len(al)

    if has_tech and has_metrics and has_struct and length > 200:
        return "ok"
    if has_tech and (has_metrics or has_struct) and length > 80:
        return "ok"
    if has_tech and length > 40:
        return "ok"
    if has_struct and length > 80:
        return "ok"
    if length > 80:
        return "vague"
    return "vague"

# ── Topic detection ───────────────────────────────────────────────────────────

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

    behavioral_kws = [
        "conflict", "failure", "challenge", "mistake", "disagree",
        "pressure", "deadline", "difficult", "wrong", "stressful",
        "pushed back", "shipped", "missed",
    ]
    if any(_word_in_text(kw, q) for kw in behavioral_kws):
        return "Behavioral"

    skill_kws = [
        "python", "react", "sql", "docker", "pytorch", "tensorflow",
        "fastapi", "django", "flask", "redis", "postgres", "mysql",
        "kubernetes", "terraform", "aws", "gcp", "azure", "llm", "nlp",
        "typescript", "javascript", "node", "golang", "rust", "java",
        "zapier", "make", "n8n", "apify", "airtable", "automation",
    ]
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


# ── Reaction variety tracker ──────────────────────────────────────────────────

def _recent_reactions(history: list[dict], n: int = 4) -> str:
    reactions = [h.get("reaction", "") for h in history if h.get("reaction")]
    recent    = reactions[-n:] if len(reactions) >= n else reactions
    return " | ".join(recent) if recent else "none"


# ── First question ────────────────────────────────────────────────────────────

def generate_first_question(resume_data: dict, role: str, level: str) -> str:
    resume_summary = summarize_resume(resume_data)

    has_projects   = bool(resume_data.get("projects"))
    has_experience = bool(resume_data.get("experience"))
    name           = resume_data.get("name", "").split()[0] if resume_data.get("name") else ""

    if has_experience:
        opener_instruction = (
            "Ask a tight opening question: most recent role and the single most technically "
            "impressive thing they built there. Max 2 sentences."
        )
    elif has_projects:
        opener_instruction = (
            "Ask them to name the one project they're most technically proud of and "
            "what made it technically hard — in one focused question."
        )
    else:
        opener_instruction = (
            "Ask them to name the most complex technical concept they genuinely understand "
            "deeply — not just know the name of. One question."
        )

    prompt = f"""Opening an interview with {_sanitize_field(name) or 'a candidate'} for {_sanitize_field(role)} ({_sanitize_field(level)}).

Resume:
{resume_summary}

Your task: {opener_instruction}

OUTPUT FORMAT — exactly two lines:
REACTION: [leave blank for opening — write nothing after the colon]
QUESTION: [your opening question, max 2 sentences, ends with ?]

Rules:
- Do NOT say "Tell me about yourself"
- Sound like a real human starting a real conversation
- Do NOT include bullet points or multiple questions
- The question must be under 200 characters"""

    result = _chat_with_retry(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user",   "content": prompt}],
        temperature=0.5,
    )
    return extract_question_only(result)


# ── IDK pivot helper ──────────────────────────────────────────────────────────

def _find_strongest_area(resume_data: dict, history: list[dict]) -> str:
    coverage  = build_coverage(resume_data, history)
    uncovered = coverage["uncovered"]

    for t in ("experience", "project", "skill", "behavioral"):
        match = next((u for u in uncovered if u["type"] == t), None)
        if match:
            return match["label"]

    covered = coverage["covered"]
    for t in ("experience", "project", "skill"):
        match = next((c for c in covered if c["type"] == t), None)
        if match:
            return f"a different angle on {match['label']}"

    return "their background"


# ── Follow-up ─────────────────────────────────────────────────────────────────

def generate_follow_up(
    resume_data: dict,
    role: str,
    level: str,
    history: list[dict],
    misbehavior_count: int = 0,
    idk_count: int = 0,
    consecutive_vague_count: int = 0,   # FIX v19.1: nag-loop prevention
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

    is_misbehavior  = answer_quality in ("gibberish", "evasive", "rude", "fabricated")
    new_misbehavior = misbehavior_count + (1 if is_misbehavior else 0)
    new_idk_count   = idk_count + (1 if answer_quality == "admitted_gap" else 0)

    # FIX v19.1: track consecutive vague answers on the SAME topic.
    # Reset to 0 on any non-vague answer; increment on vague.
    # After 1 follow-up attempt (consecutive_vague_count == 1) → force topic change.
    # This means we ask ONE tighter question after a vague answer, then move on.
    if answer_quality == "vague":
        new_consecutive_vague = consecutive_vague_count + 1
    else:
        new_consecutive_vague = 0

    # ── IDK threshold termination ─────────────────────────────────────────────
    if answer_quality == "admitted_gap" and new_idk_count >= MAX_IDK_COUNT:
        return {
            "reaction":               "I appreciate the honesty, but this role needs demonstrated reasoning, not just gaps.",
            "question":               "",
            "terminated":             True,
            "answer_quality":         answer_quality,
            "termination_reason":     "repeated_idk",
            "idk_count":              new_idk_count,
            "misbehavior_count":      new_misbehavior,
            "consecutive_vague_count": 0,
        }

    # ── Conduct strike termination ────────────────────────────────────────────
    if is_misbehavior and new_misbehavior >= MAX_MISBEHAVIOR:
        return {
            "reaction":               random.choice(TERMINATION_MESSAGES),
            "question":               "",
            "terminated":             True,
            "answer_quality":         answer_quality,
            "termination_reason":     "conduct_strikes",
            "idk_count":              new_idk_count,
            "misbehavior_count":      new_misbehavior,
            "consecutive_vague_count": 0,
        }

    # ── Strike warning ────────────────────────────────────────────────────────
    strike_warning = ""
    if is_misbehavior:
        idx = new_misbehavior - 1
        if answer_quality == "rude":
            strike_warning = RUDE_STRIKE_WARNINGS[idx]
        elif answer_quality == "fabricated":
            strike_warning = FABRICATED_STRIKE_WARNINGS[idx]
        elif answer_quality == "gibberish":
            strike_warning = GIBBERISH_STRIKE_WARNINGS[idx]
        else:
            strike_warning = EVASIVE_STRIKE_WARNINGS[idx]

    # ── Natural end ───────────────────────────────────────────────────────────
    if questions_asked >= MIN_QUESTIONS and not is_misbehavior:
        if _should_end_deterministic(resume_data, history):
            return None

    # ── Topic-loop detection ──────────────────────────────────────────────────
    last_topic, consecutive_count = _consecutive_topic(history, resume_data)
    force_topic_change = (consecutive_count >= 2) and not is_misbehavior

    # FIX v19.1: also force topic change when vague follow-up has already been
    # attempted once (new_consecutive_vague >= 2 means this is the SECOND vague
    # in a row — we already asked them to be more specific once, time to move on)
    if answer_quality == "vague" and new_consecutive_vague >= 2:
        force_topic_change = True
        logger.info(
            "generate_follow_up: consecutive_vague_count=%d — forcing topic change",
            new_consecutive_vague,
        )

    # ── Coverage + next target ────────────────────────────────────────────────
    coverage    = build_coverage(resume_data, history)
    next_target = pick_next_target(
        coverage         = coverage,
        questions_asked  = questions_asked,
        history          = history,
        resume_data      = resume_data,
        force_skip_label = last_topic if force_topic_change else None,
    )

    resume_summary        = summarize_resume(resume_data)
    already_asked_types   = _extract_question_patterns(history)
    recent_q_summary      = " | ".join(h["question"][:60] for h in history[-3:]) if history else "none"
    recent_reactions_used = _recent_reactions(history, n=4)

    convo_lines = []
    for i, h in enumerate(history):
        convo_lines.append(f"Q{i+1}: {h['question']}")
        convo_lines.append(f"A{i+1}: {h['answer'][:300]}")
    convo = "\n".join(convo_lines)

    # ── Build directives ──────────────────────────────────────────────────────

    if answer_quality == "rude":
        reaction_directive = (
            f'REACTION: Say this exactly, calmly: "{strike_warning}" '
            'No apology. No explanation. One sentence. NO question mark.'
        )
        question_directive = (
            f"Re-ask the previous question from a different angle: {last_question[:100]}\n"
            "One sentence ending with a question mark."
        )

    elif answer_quality == "fabricated":
        reaction_directive = (
            f'REACTION: Say this exactly: "{strike_warning}" One sentence. NO question mark.'
        )
        question_directive = (
            f"Re-ask the previous question ({last_question[:100]}) but anchor it explicitly "
            "to a real project name or role from their resume. "
            "One sentence ending with a question mark."
        )

    elif answer_quality == "gibberish":
        reaction_directive = (
            f'REACTION: Say this exactly: "{strike_warning}" One sentence. NO question mark.'
        )
        question_directive = (
            f"Repeat the core of the previous question more simply: {last_question[:100]}\n"
            "Ask for a clear direct answer in one sentence ending with a question mark."
        )

    elif answer_quality == "evasive":
        reaction_directive = (
            f'REACTION: Say this exactly: "{strike_warning}" One sentence. NO question mark.'
        )
        question_directive = (
            f"Re-ask the same question from a completely different angle — make it specific "
            f"and impossible to dodge: {last_question[:100]}\n"
            "One sentence ending with a question mark."
        )

    elif answer_quality == "admitted_gap":
        reaction_directive = (
            "REACTION: Acknowledge the gap briefly and naturally. "
            "Something like 'Fair enough, let's move on.' or 'No problem, let's pivot.' "
            "One sentence. NO question mark."
        )
        strongest = _find_strongest_area(resume_data, history)
        question_directive = (
            f"Move to a completely new topic — specifically {strongest}.\n"
            f"Directive: {next_target['instruction']}\n"
            "Ground it in a specific item from their resume. "
            "One to two sentences ending with a question mark."
        )
        force_topic_change = True

    elif answer_quality == "vague" and not force_topic_change:
        # First vague answer on this topic — ask ONE tighter follow-up.
        reaction_directive = (
            "REACTION: Acknowledge briefly, then ask for more depth. "
            "Something like: 'That was a bit general — I need something more concrete.' "
            "One sentence. NO question mark. Do NOT quote their answer directly."
        )
        question_directive = (
            "Follow up with ONE specific ask: a metric, a tool name, a concrete decision, "
            "or a real outcome. Ground it in their resume (a project or role), "
            "NOT in the exact words of their last answer (STT may have garbled them). "
            f"Original question context: {last_question[:100]}\n"
            "One sentence ending with a question mark."
        )

    else:
        # ok / vague-that-has-already-been-followed-up / forced topic change
        if force_topic_change and answer_quality == "vague":
            reaction_directive = (
                "REACTION: Acknowledge briefly and move on without dwelling on the vagueness. "
                "Sound natural, not punishing. One sentence. NO question mark."
            )
        else:
            reaction_directive = (
                "REACTION: Acknowledge naturally in one short sentence. "
                f"Do NOT use any of these (used recently): {recent_reactions_used}. "
                "Do NOT quote or reference their specific words. "
                "One sentence. NO question mark."
            )
        question_directive = (
            f"New question — move to: {next_target['label']}.\n"
            f"Directive: {next_target['instruction']}\n"
            "Ground it in a SPECIFIC item from their resume (project name, company, or skill). "
            "Do NOT recap their previous answer. Do NOT ask multiple questions. "
            "One to two sentences ending with a question mark."
        )

    topic_change_warning = (
        "\n⚠ MANDATORY TOPIC SWITCH — ask about a completely different part of their background."
        if force_topic_change else ""
    )

    prompt = f"""Role: {_sanitize_field(role)} ({_sanitize_field(level)})

Resume:
{resume_summary}

Conversation so far:
{convo}

---
{reaction_directive}

{question_directive}

Already covered (do NOT ask about these again): {', '.join(already_asked_types) if already_asked_types else 'nothing yet'}
Recent questions (do NOT repeat or rephrase): {recent_q_summary}{topic_change_warning}

OUTPUT — exactly two lines, nothing else:
REACTION: [your reaction sentence — no question mark]
QUESTION: [your question — 1-2 sentences, specific, ends with ?]"""

    result = _chat_with_retry(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user",   "content": prompt}],
        temperature=0.6,
    )

    parsed = parse_response(result)
    parsed.update({
        "terminated":               False,
        "answer_quality":           answer_quality,
        "force_topic_change":       force_topic_change,
        "idk_count":                new_idk_count,
        "misbehavior_count":        new_misbehavior,
        "consecutive_vague_count":  new_consecutive_vague,
    })
    return parsed


# ── Replacement Question (Anti-Cheat) ─────────────────────────────────────────

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

    prompt = f"""The candidate skipped the previous question. Move to a completely new topic silently — do NOT reference the skip.

Resume:
{resume_summary}

Directive: {next_target['instruction']}

OUTPUT — exactly one line:
QUESTION: [your question, 1-2 sentences, specific, grounded in their resume, ends with ?]"""

    result = _chat_with_retry(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user",   "content": prompt}],
        temperature=0.6,
    )
    return extract_question_only(result)


# ── Coverage tracking ─────────────────────────────────────────────────────────

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
        for kw in [
            "conflict", "failure", "challenge", "mistake", "disagreed",
            "pressure", "deadline", "difficult", "wrong", "missed", "pushed back",
        ]
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
            "label":       "Closing",
            "instruction": "Ask what excites them most about this role and what they'd want to grow into.",
        }

    valid_targets = [u for u in uncovered if not _skip(u)]

    if valid_targets:
        target = None
        for priority_type in ("experience", "project", "skill", "behavioral"):
            pool = [t for t in valid_targets if t["type"] == priority_type]
            if pool:
                target = random.choice(pool)
                break
        if target is None:
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
                    "architectural trade-off, or edge case. No definitions."
                ),
            }

        elif target["type"] == "behavioral":
            return {"label": "Behavioral", "instruction": random.choice(BEHAVIORAL_ANGLES)}

    covered       = coverage["covered"]
    valid_covered = [
        c for c in covered
        if not _skip(c) and c["type"] in ("project", "experience", "skill")
    ]

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
                    f"Go deeper on {skill_str}. Give a complex edge case or "
                    "scaling problem and ask how they'd handle it."
                ),
            }

    return {
        "label":       "Closing",
        "instruction": "Wrap up — ask what excites them most about this role and what they'd want to grow into.",
    }


# ── Deterministic end check ───────────────────────────────────────────────────

def _should_end_deterministic(resume_data: dict, history: list[dict]) -> bool:
    if len(history) < MIN_QUESTIONS:
        return False
    coverage       = build_coverage(resume_data, history)
    uncovered      = coverage["uncovered_labels"]
    hard_uncovered = [
        u for u in uncovered
        if not u.startswith("Skills:") and u != "Behavioral"
    ]
    if hard_uncovered:
        return False
    if "Behavioral" in uncovered:
        return False
    return True


# ── Question variety helpers ──────────────────────────────────────────────────

def _extract_question_patterns(history: list[dict]) -> list[str]:
    patterns = []
    for h in history:
        q = h["question"].lower()
        if "introduce" in q or "background" in q:                              patterns.append("intro/background")
        if "challenge" in q or "difficult" in q or "hard" in q:                patterns.append("challenges")
        if "fail" in q or "mistake" in q or "wrong" in q:                      patterns.append("failures")
        if "trade" in q or "decision" in q or "chose" in q or "why did" in q:  patterns.append("decisions/trade-offs")
        if "scale" in q or "performance" in q or "optim" in q:                 patterns.append("performance/scale")
        if "team" in q or "conflict" in q or "disagree" in q:                  patterns.append("teamwork/conflict")
        if "architect" in q or "design" in q or "structure" in q:              patterns.append("system design")
        if "test" in q or "coverage" in q or "qa" in q:                        patterns.append("testing")
        if "debug" in q or "incident" in q or "broke" in q:                    patterns.append("debugging/incidents")
    return list(set(patterns))


# ── Skills helpers ────────────────────────────────────────────────────────────

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
        "Frontend":   ["react", "vue", "angular", "html", "css", "javascript", "typescript", "next", "tailwind"],
        "Backend":    ["python", "node", "django", "flask", "fastapi", "java", "spring", "express", "golang", "rust"],
        "Database":   ["sql", "postgres", "mysql", "mongodb", "redis", "firebase", "supabase"],
        "DevOps":     ["docker", "kubernetes", "aws", "gcp", "azure", "terraform", "linux", "ci", "cd"],
        "ML/AI":      ["tensorflow", "pytorch", "sklearn", "pandas", "numpy", "llm", "nlp", "huggingface"],
        "Automation": ["zapier", "make", "n8n", "airtable", "apify", "automation", "webhook", "workflow", "retool", "bubble"],
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


# ── Resume summary ────────────────────────────────────────────────────────────

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


# ── Parsing ───────────────────────────────────────────────────────────────────

_PARSE_RE    = re.compile(
    r"\*{0,2}REACTION\*{0,2}\s*:\s*(.*?)\s*\*{0,2}QUESTION\*{0,2}\s*:\s*(.*)",
    re.IGNORECASE | re.DOTALL,
)
_QUESTION_RE = re.compile(r"\*{0,2}QUESTION\*{0,2}\s*:\s*(.*)", re.IGNORECASE | re.DOTALL)
_REACTION_RE = re.compile(
    r"\*{0,2}REACTION\*{0,2}\s*:\s*(.*?)(?=\*{0,2}QUESTION\*{0,2}\s*:|$)",
    re.IGNORECASE | re.DOTALL,
)

_MAX_QUESTION_CHARS = 300


def _trim_question(question: str) -> str:
    question = question.strip()
    if len(question) <= _MAX_QUESTION_CHARS:
        return question

    logger.warning("_trim_question: question too long (%d chars) — trimming", len(question))
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', question) if s.strip()]

    # Reverse-scan: last sentence ending with '?' is the actual question
    for s in reversed(sentences):
        if s.endswith("?"):
            return s

    return sentences[-1] if sentences else question[:_MAX_QUESTION_CHARS]


def parse_response(text: str) -> dict:
    text = text.strip()
    text = re.sub(r'^```.*?```', '', text, flags=re.DOTALL).strip()

    m = _PARSE_RE.search(text)
    if m:
        reaction = m.group(1).strip()
        question = _trim_question(m.group(2))
        return {"reaction": reaction, "question": question}

    reaction = question = ""

    r_match = _REACTION_RE.search(text)
    if r_match:
        reaction = r_match.group(1).strip()

    q_match = _QUESTION_RE.search(text)
    if q_match:
        question = _trim_question(q_match.group(1))

    if not question:
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
        for s in reversed(sentences):
            if s.endswith("?"):
                question = s
                break
        if not question:
            question = _trim_question(text)

    return {"reaction": reaction, "question": question}


def extract_question_only(text: str) -> str:
    return parse_response(text).get("question") or text.strip()
