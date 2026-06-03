"""
feedback_engine.py — v17

Changes vs v16:
  1. valid_answers == 0 hard-override block improved:
       - executive_summary now names the most likely cause (microphone picking up
         interviewer TTS audio) rather than giving a generic "all zeros" message.
         This helps distinguish a genuine blank interview from a technical audio
         routing failure.
       - Per-question critiques are NO LONGER unconditionally overwritten.
         If the LLM already generated a critique for a turn, it is preserved.
         The generic fallback string is only written when critique is absent or
         empty, so the report retains whatever diagnostic content the LLM
         produced based on the captured (even if invalid) audio.
       - Each question_feedback entry gains a was_forfeited: True flag so the
         frontend can surface a visual indicator per-question without needing to
         inspect score or answer_quality.
"""

import json
import re
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from app.services.groq_client import chat

logger = logging.getLogger(__name__)

# v16: "fabricated" added — penalised equally to "blank" (score 10)
QUALITY_SCORE = {
    "strong":       90,
    "good":         75,
    "average":      55,
    "weak":         30,
    "blank":        10,
    "fabricated":   10,
    "admitted_gap": 10,
}

RUDE_PATTERNS = [
    r"\bstupid\b", r"\bidiot\b", r"\bdumb\b", r"\bwaste\b.*\btime\b",
    r"\bshut up\b", r"\bfuck\b", r"\bscrew\b", r"\bwhatever\b",
]
IDK_PATTERNS = [
    r"\bi don'?t know\b", r"\bno idea\b", r"\bnot sure\b",
    r"\bnever heard\b", r"\bcan'?t answer\b", r"\bpass\b",
    r"\bi'?m not familiar\b", r"\bno clue\b",
]

_HALLUCINATION_ANSWERS = frozenset([
    "thank you.", "thank you", "thanks for watching.",
    "subscribe.", "bye.", "bye",
])

_TECH_KEYWORDS = frozenset([
    "python", "model", "algorithm", "accuracy", "data", "api", "sql", "docker",
    "react", "typescript", "javascript", "node", "django", "flask", "fastapi",
    "kubernetes", "terraform", "aws", "gcp", "azure", "redis", "postgres",
    "mysql", "mongodb", "graphql", "rest", "grpc", "llm", "nlp", "pytorch",
    "tensorflow", "sklearn", "pandas", "numpy", "spark", "kafka", "celery",
    "nginx", "linux", "git", "ci", "cd", "microservice", "cache", "queue",
    "latency", "throughput", "scalab", "deploy", "pipeline", "endpoint",
])

# v16: misbehavior types that get flagged in behavioral_flags output
_MISBEHAVIOR_LABELS = frozenset(["fabricated", "rude", "evasive", "gibberish"])


# ── LLM call with retry ────────────────────────────────────────────────────────

def _chat_with_retry(messages: list[dict], temperature: float = 0.2, retries: int = 3) -> str:
    delay    = 1.5
    last_exc = None
    for attempt in range(retries):
        try:
            return chat(messages, temperature=temperature)
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "chat() failed (attempt %d/%d): %s — retrying in %.1fs",
                attempt + 1, retries, exc, delay,
            )
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"LLM call failed after {retries} attempts: {last_exc}") from last_exc


# ── String helpers ─────────────────────────────────────────────────────────────

def _safe_truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    boundary  = truncated.rfind(" ")
    return (truncated[:boundary] if boundary > 0 else truncated) + "…"


# ── Issue detection ────────────────────────────────────────────────────────────

def detect_issues(answer: str, stored_quality: str | None = None) -> dict:
    """
    Detect answer quality issues.

    v16 change: accepts stored_quality (the answer_quality label written into
    history by interview.py v10). If present it is used directly instead of
    re-running heuristics, which avoids a second LLM call and is more accurate.
    """
    a = answer.lower()

    is_rude = any(re.search(p, a) for p in RUDE_PATTERNS)
    is_idk  = (
        any(re.search(p, a) for p in IDK_PATTERNS)
        or "[no response]" in a
        or "forfeited"     in a
        or is_rude
    )

    # v16: use stored label when available (set by interview.py v10)
    is_fabricated = False
    if stored_quality == "fabricated":
        is_fabricated = True
    elif stored_quality == "rude":
        is_rude = True

    return {
        "is_rude":       is_rude,
        "is_idk":        is_idk,
        "is_fabricated": is_fabricated,
        # Convenience: any disqualifying misbehavior
        "is_misbehavior": (
            is_rude or is_fabricated
            or (stored_quality in _MISBEHAVIOR_LABELS if stored_quality else False)
        ),
    }


def _is_forfeited(answer: str) -> bool:
    a = answer.strip().lower()
    return (
        not a
        or "forfeited"      in a
        or "[no response]"  in a
        or a in _HALLUCINATION_ANSWERS
        or len(a.split()) < 4
    )


# ── Label helpers ──────────────────────────────────────────────────────────────

def speech_label(score: int) -> str:
    if score >= 85: return "Excellent"
    if score >= 70: return "Good"
    if score >= 55: return "Fair"
    if score >= 40: return "Poor"
    return "Very Poor"


def score_to_quality_label(score: int) -> str:
    if score >= 85: return "strong"
    if score >= 65: return "good"
    if score >= 40: return "average"
    if score >= 20: return "weak"
    return "blank"


# ── Speech metrics ─────────────────────────────────────────────────────────────

def aggregate_speech_metrics(speech_metrics_log: list[dict]) -> dict:
    if not speech_metrics_log:
        return {}
    total_words = total_fillers = 0
    all_fillers: dict   = {}
    all_repeats: list   = []
    fluency_scores: list = []
    too_short_count = too_long_count = 0

    for entry in speech_metrics_log:
        m = entry.get("metrics", {})
        total_words   += m.get("word_count", 0)
        total_fillers += m.get("filler_count", 0)
        for fw in m.get("filler_words_found", []):
            all_fillers[fw] = all_fillers.get(fw, 0) + 1
        all_repeats.extend(m.get("repeated_words", []))
        if m.get("fluency_score") is not None:
            fluency_scores.append(m["fluency_score"])
        if m.get("too_short"): too_short_count += 1
        if m.get("too_long"):  too_long_count  += 1

    avg_fluency = round(sum(fluency_scores) / len(fluency_scores)) if fluency_scores else 0
    top_fillers = sorted(all_fillers.items(), key=lambda x: -x[1])[:5]

    per_question = []
    for entry in speech_metrics_log:
        m      = entry.get("metrics", {})
        issues = []
        if m.get("filler_count", 0) >= 2:   issues.append(f"Filler words used {m['filler_count']}×")
        if m.get("repeated_words"):          issues.append("Repeated words detected")
        if m.get("too_short"):               issues.append("Answer too brief")
        if m.get("too_long"):                issues.append("Answer too long")
        q_text = entry.get("question", "")
        per_question.append({
            "question":      _safe_truncate(q_text, 90),
            "word_count":    m.get("word_count", 0),
            "filler_count":  m.get("filler_count", 0),
            "fluency_score": m.get("fluency_score", 0),
            "fluency_label": speech_label(m.get("fluency_score", 0)),
            "issues":        issues,
        })

    return {
        "total_words_spoken": total_words,
        "total_filler_count": total_fillers,
        "top_filler_words":   [{"word": w, "count": c} for w, c in top_fillers],
        "repeated_words":     list(set(all_repeats))[:8],
        "avg_fluency_score":  avg_fluency,
        "fluency_label":      speech_label(avg_fluency),
        "too_short_answers":  too_short_count,
        "too_long_answers":   too_long_count,
        "per_question":       per_question,
    }


# ── JSON extraction ────────────────────────────────────────────────────────────

def extract_json(raw: str, fallback_history: list[dict]) -> dict:
    clean = raw.strip()
    try:
        return json.loads(clean)
    except Exception:
        pass
    m = re.search(r'\{[\s\S]*\}', clean)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    logger.warning("extract_json: could not parse LLM output; using rule-based fallback.")
    return _build_fallback(fallback_history)


def extract_json_array(raw: str) -> list:
    clean = raw.strip()
    try:
        result = json.loads(clean)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "question_feedback" in result:
            return result["question_feedback"]
    except Exception:
        pass
    m = re.search(r'\[[\s\S]*\]', clean)
    if m:
        try:
            result = json.loads(m.group())
            if isinstance(result, list):
                return result
        except Exception:
            pass
    objects = re.findall(r'\{[^{}]*\}', clean)
    parsed  = []
    for obj in objects:
        try:
            parsed.append(json.loads(obj))
        except Exception as exc:
            logger.debug("extract_json_array: skipping unparseable object: %s", exc)
    return parsed


# ── Rule-based quality fallback ────────────────────────────────────────────────

def _assess_quality(answer: str, stored_quality: str | None = None) -> dict:
    """
    v16: checks stored_quality first (written by interview.py v10) before
    falling back to heuristic text analysis. Handles "fabricated" explicitly.
    """
    # Fast path: use the label already computed during the live interview
    if stored_quality == "fabricated":
        return {
            "label":   "fabricated",
            "comment": (
                "The answer described experience or credentials not found on the resume. "
                "Answers should be grounded in actual work history."
            ),
            "ideal": "Respond based on real experience listed on your resume.",
            "key_points": [],
        }
    if stored_quality == "rude":
        return {
            "label":      "blank",
            "comment":    "The response was flagged as hostile or unprofessional.",
            "ideal":      "Maintain professional communication throughout the interview.",
            "key_points": [],
        }
    if stored_quality == "gibberish":
        return {
            "label":      "blank",
            "comment":    "The response was incoherent or off-topic.",
            "ideal":      "Provide a clear, relevant answer.",
            "key_points": [],
        }
    if stored_quality == "evasive":
        return {
            "label":      "weak",
            "comment":    "The response deflected the question rather than answering it.",
            "ideal":      "Engage directly with the question asked.",
            "key_points": [],
        }

    # Fallback: heuristic text analysis (used when stored_quality is absent or "pending")
    if not answer or len(answer.strip()) < 10 or "[no response]" in answer or "FORFEITED" in answer:
        return {"label": "blank",   "comment": "No response was provided.",             "ideal": "Provide a response.",            "key_points": []}
    w = answer.lower()
    has_tech    = any(kw in w for kw in _TECH_KEYWORDS)
    has_metrics = bool(re.search(r'\d+%|\d+\.\d+|\d+x|\d+ ms|\d+k\b', w))
    has_struct  = any(k in w for k in ["first", "then", "finally", "because", "for example", "we used", "so that"])
    n = len(answer)

    if has_tech and has_metrics and has_struct and n > 200:
        return {"label": "strong",  "comment": "Strong, specific answer.",               "ideal": "Already demonstrates depth.",    "key_points": []}
    if has_tech and (has_metrics or has_struct) and n > 100:
        return {"label": "good",    "comment": "Good answer — could add more metrics.",  "ideal": "Add a specific number or result.","key_points": []}
    if has_tech and n > 50:
        return {"label": "average", "comment": "Shows basic familiarity.",               "ideal": "Explain the reasoning behind it.","key_points": []}
    if n > 30:
        return {"label": "weak",    "comment": "Too general — needs specifics.",         "ideal": "Name a tool, metric, or decision.","key_points": []}
    return         {"label": "blank",   "comment": "Answer was too brief to evaluate.", "ideal": "Expand the response.",            "key_points": []}


def _build_fallback(history: list[dict]) -> dict:
    """Rule-based fallback used when the LLM summary call fails entirely."""
    qf           = []
    strengths    = []
    improvements = []

    for turn in history:
        a              = turn.get("answer", "")
        q              = turn.get("question", "")
        stored_quality = turn.get("answer_quality")   # v16: use stored label
        quality        = _assess_quality(a, stored_quality)
        score          = QUALITY_SCORE.get(quality["label"], 55)

        qf.append({
            "question":             q,
            "score":                score,
            "answer_quality":       quality["label"],
            "answer_summary":       _safe_truncate(a, 80),
            "critique":             quality["comment"],
            "ideal_answer_snippet": quality["ideal"],
            "ideal_points":         quality["key_points"],
            "_backfilled":          True,
            "_source":              "rule_based",
        })

        if quality["label"] in ("strong", "good"):
            strengths.append(f"Gave a solid answer on: {_safe_truncate(q, 60)}")
        elif quality["label"] in ("weak", "blank", "fabricated"):
            improvements.append(f"Worth developing deeper answers around: {_safe_truncate(q, 60)}")

    return {
        "executive_summary":      "Assessment generated by rule-based fallback (LLM unavailable).",
        "strengths":              strengths[:5],
        "improvements":           improvements[:5],
        "learning_path":          [],
        "question_feedback":      qf,
        "confidence_calibration": "balanced",
        "confidence_note":        "",
    }


# ── Behavioral flags builder ───────────────────────────────────────────────────

def _build_behavioral_flags(history: list[dict], misbehavior_log: list[dict]) -> list[dict]:
    """
    v16: constructs a list of behavioral flag objects for the feedback report.
    Each flag names the question, the misbehavior type, and a plain-English note.
    Sources: stored answer_quality from history + misbehavior_log from session.
    """
    flags: list[dict] = []
    seen_questions: set[str] = set()

    # Primary source: answer_quality stored in each history turn
    for turn in history:
        q       = turn.get("question", "")
        quality = turn.get("answer_quality", "ok")
        if quality in _MISBEHAVIOR_LABELS and q not in seen_questions:
            seen_questions.add(q)
            label_notes = {
                "fabricated": "Claimed experience or credentials not found on the resume.",
                "rude":       "Used hostile or unprofessional language.",
                "evasive":    "Deflected the question rather than engaging with it.",
                "gibberish":  "Response was incoherent or completely off-topic.",
            }
            flags.append({
                "question":  _safe_truncate(q, 90),
                "type":      quality,
                "note":      label_notes.get(quality, f"Flagged as {quality}."),
            })

    # Secondary source: misbehavior_log (catches cheat / fullscreen violations)
    for entry in misbehavior_log:
        q      = entry.get("question", "")
        reason = entry.get("reason", "")
        if "fullscreen" in reason.lower() or "cheat" in reason.lower():
            if q not in seen_questions:
                seen_questions.add(q)
                flags.append({
                    "question": _safe_truncate(q, 90),
                    "type":     "cheat",
                    "note":     "Exited fullscreen / switched tabs during the interview.",
                })

    return flags


# ── Score computation ──────────────────────────────────────────────────────────

_W_TECH       = 0.30
_W_CONFIDENCE = 0.20
_W_COMM       = 0.20
_W_BEHAVIOUR  = 0.15
_W_SPEECH     = 0.15

_WORDS_FLOOR  = 20
_WORDS_CEIL   = 100


def _word_signal(avg_wc: float) -> float:
    if avg_wc <= _WORDS_FLOOR:
        return 0.0
    return min(100.0, (avg_wc - _WORDS_FLOOR) / (_WORDS_CEIL - _WORDS_FLOOR) * 100)


def compute_scores(
    qf: list[dict],
    speech_log: list[dict],
    misbehavior: list[dict],
    is_terminated: bool,
    idk_qs: list[str],
    history_length: int,
    termination_reason: str | None = None,
) -> dict:
    n   = len(qf)
    agg = aggregate_speech_metrics(speech_log)

    raw  = [
        int(q["score"]) if "score" in q
        else QUALITY_SCORE.get(q.get("answer_quality", "average"), 55)
        for q in qf
    ] if n > 0 else []
    tech = round(sum(raw) / len(raw)) if raw else 0

    base_fluency = agg.get("avg_fluency_score", 50) if agg else 50
    strikes      = len(misbehavior)
    behaviour    = max(0, 100 - strikes * 28)
    idk_ratio    = len(idk_qs) / max(n, 1)

    avg_wc = (
        sum(e.get("metrics", {}).get("word_count", 0) for e in speech_log)
        / max(history_length, 1)
    ) if speech_log else 0.0

    speech_clarity = 0 if avg_wc < 10 else base_fluency
    ws             = _word_signal(avg_wc)

    confidence    = min(88, max(0, round(tech * 0.4 + ws * 0.4 - idk_ratio * 50)))
    communication = min(85, round(speech_clarity * 0.40 + tech * 0.35 + behaviour * 0.25))
    overall       = min(88, round(
        tech           * _W_TECH       +
        confidence     * _W_CONFIDENCE +
        communication  * _W_COMM       +
        behaviour      * _W_BEHAVIOUR  +
        speech_clarity * _W_SPEECH,
    ))

    penalty_applied = 0.0
    penalty_reason  = None

    if is_terminated:
        if termination_reason == "cheating":
            TERM_PENALTY   = 0.50
            tech           = round(tech           * TERM_PENALTY)
            confidence     = round(confidence     * TERM_PENALTY)
            communication  = round(communication  * TERM_PENALTY)
            speech_clarity = round(speech_clarity * TERM_PENALTY)
            overall = min(88, round(
                tech           * _W_TECH       +
                confidence     * _W_CONFIDENCE +
                communication  * _W_COMM       +
                behaviour      * _W_BEHAVIOUR  +
                speech_clarity * _W_SPEECH,
            ))
            penalty_applied = 50.0
            penalty_reason  = "cheating"
        elif termination_reason in ("conduct_strikes", "repeated_idk"):
            penalty_reason = termination_reason

    return {
        "overall_score":         overall,
        "technical_depth_score": tech,
        "confidence_score":      confidence,
        "communication_score":   communication,
        "behaviour_score":       behaviour,
        "speech_clarity_score":  speech_clarity,
        "speech_clarity_label":  speech_label(speech_clarity),
        "score_inputs": {
            "quality_scores": raw,
            "avg_fluency":    speech_clarity,
            "strikes":        strikes,
            "idk_ratio":      round(idk_ratio, 2),
            "avg_word_count": round(avg_wc, 1),
        },
        "penalty_applied": int(penalty_applied),
        "penalty_reason":  penalty_reason,
    }


# ── LLM summary call ───────────────────────────────────────────────────────────

def _call_summary(
    role: str,
    convo_text: str,
    speech_ctx: str,
    strikes_ctx: str,
    termination_reason: str | None = None,
) -> dict:
    termination_note = (
        f"The interview ended early due to {termination_reason}."
        if termination_reason else ""
    )
    prompt = f"""You are a senior engineering mentor writing a post-interview coaching report for a {role} candidate.

Transcript:
{convo_text}
{speech_ctx}
{strikes_ctx}
{termination_note}

TONE RULES — non-negotiable:
- Write like a mentor who wants this person to succeed, not a judge delivering a verdict.
- executive_summary: be honest but forward-looking. State what the candidate showed, what was missing,
  and what they should focus on next. Do NOT use accusatory phrasing like "failed to", "did not demonstrate",
  "couldn't explain", "lacked". Use: "would benefit from", "an area to develop", "not yet showing", "to strengthen".
- strengths: specific things they actually said or did well. Be concrete — quote or paraphrase their answer.
- improvements: phrase every item as an opportunity, not a failure.
  BAD: "Candidate did not discuss system design."
  GOOD: "Deeper coverage of system design trade-offs would strengthen their profile significantly."
- learning_path: actionable next steps, not a report card.
- confidence_calibration: was the candidate's confidence matched to their actual technical depth?

Return ONLY valid JSON — no preamble, no markdown fences:
{{
  "executive_summary": "2-3 sentences. Honest, specific, forward-looking.",
  "strengths": ["Specific thing they actually did well — be concrete"],
  "improvements": ["Opportunity phrased as a coaching note, not an accusation"],
  "learning_path": [{{"topic": "X", "priority": "High|Medium|Low", "reason": "Why this matters for their growth"}}],
  "confidence_calibration": "under-confident | balanced | over-confident",
  "confidence_note": "One sentence — what did their confidence level suggest about self-awareness?"
}}"""

    try:
        raw = _chat_with_retry([{"role": "user", "content": prompt}], temperature=0.2)
        res = extract_json(raw, [])
    except RuntimeError as exc:
        logger.error("_call_summary failed after all retries: %s", exc)
        res = {}

    res.setdefault("executive_summary",      "Assessment unavailable.")
    res.setdefault("strengths",              [])
    res.setdefault("improvements",           [])
    res.setdefault("learning_path",          [])
    res.setdefault("confidence_calibration", "balanced")
    res.setdefault("confidence_note",        "")
    return res


BATCH_SIZE = 3


def _score_batch(role: str, batch: list[dict], offset: int) -> list[dict]:
    n        = len(batch)
    qa_lines = "\n\n".join(
        f"Q{offset+i+1}: {t['question']}\nA{offset+i+1}: {_safe_truncate(t['answer'], 800)}"
        for i, t in enumerate(batch)
    )

    prompt = f"""You are writing coaching feedback on {n} interview answer(s) for a {role} candidate.

{qa_lines}

SCORING:
- Score 0-100. Blank/forfeited/hostile/fabricated answers MUST score 0-10, no exceptions.
- Score reflects technical depth, specificity, and relevance.

CRITIQUE TONE RULES:
- Write every critique as a coaching note, not a verdict.
- BAD: "Candidate failed to explain the trade-off."
- GOOD: "The trade-off reasoning would be stronger with a concrete example — e.g. why did you pick X over Y?"
- ideal_answer_snippet: show what a strong answer looks like — make it useful and specific.

Return ONLY a JSON array with exactly {n} objects:
[{{
  "question": "...",
  "score": <0-100>,
  "critique": "Coaching note — what was good, what would make it stronger.",
  "ideal_answer_snippet": "A concrete example of what a strong answer looks like."
}}]"""

    try:
        raw     = _chat_with_retry([{"role": "user", "content": prompt}], temperature=0.2)
        entries = extract_json_array(raw)
    except RuntimeError as exc:
        logger.error("_score_batch failed after all retries: %s", exc)
        entries = []

    for i in range(len(entries), n):
        stored_quality = batch[i].get("answer_quality")          # v16: use stored label
        quality        = _assess_quality(batch[i].get("answer", ""), stored_quality)
        entries.append({
            "score":                QUALITY_SCORE.get(quality["label"], 10),
            "critique":             quality["comment"],
            "ideal_answer_snippet": quality["ideal"],
        })

    for i, entry in enumerate(entries[:n]):
        entry.setdefault("question", batch[i].get("question", ""))
        entry.setdefault("score",    10)

        # Hard-floor for forfeited and fabricated answers
        stored_quality = batch[i].get("answer_quality")
        if _is_forfeited(batch[i].get("answer", "")) or stored_quality == "fabricated":
            entry["score"] = min(int(entry["score"]), 10)

        entry["answer_quality"] = score_to_quality_label(int(entry["score"]))
        entry.setdefault("_source", "llm")

    return entries[:n]


def _call_question_feedback(role: str, history: list[dict]) -> list[dict]:
    all_entries = []
    for start in range(0, len(history), BATCH_SIZE):
        batch = history[start:start + BATCH_SIZE]
        all_entries.extend(_score_batch(role, batch, offset=start))
    return all_entries


# ── Main entry point ───────────────────────────────────────────────────────────

def generate_feedback(
    resume_data: dict,
    role: str,
    history: list[dict],
    misbehavior_log: list[dict] | None = None,
    speech_metrics_log: list[dict] | None = None,
    is_terminated: bool = False,
    termination_reason: str | None = None,
) -> dict:
    misbehavior_log    = misbehavior_log    or []
    speech_metrics_log = speech_metrics_log or []

    clean_history: list[dict] = []
    for i, turn in enumerate(history):
        if not isinstance(turn, dict):
            logger.warning("generate_feedback: history[%d] is not a dict — skipped.", i)
            continue
        clean_history.append({
            **turn,
            "question":       turn.get("question")       or "",
            "answer":         turn.get("answer")         or "",
            "answer_quality": turn.get("answer_quality") or "ok",  # v16
        })

    annotated:     list[dict] = []
    idk_questions: list[str]  = []
    for turn in clean_history:
        # v16: pass stored_quality into detect_issues
        flags = detect_issues(turn["answer"], stored_quality=turn.get("answer_quality"))
        annotated.append({**turn, **flags})
        if flags["is_idk"]:
            idk_questions.append(turn["question"])

    convo_text = "\n".join(
        f"Q{i+1}: {t['question']}\nA{i+1}: {t['answer']}"
        for i, t in enumerate(annotated)
    ) or "(No answers recorded)"

    agg         = aggregate_speech_metrics(speech_metrics_log)
    speech_ctx  = f"SPEECH FLUENCY: {agg['avg_fluency_score']}/100" if agg else ""
    strikes_ctx = f"CONDUCT STRIKES: {len(misbehavior_log)}"        if misbehavior_log else ""

    # Run summary and per-question scoring in parallel
    with ThreadPoolExecutor(max_workers=2) as executor:
        fut_summary = executor.submit(
            _call_summary,
            role,
            convo_text,
            speech_ctx,
            strikes_ctx,
            termination_reason,
        )
        fut_qf      = executor.submit(_call_question_feedback, role, clean_history)

        summary_result    = fut_summary.result()
        question_feedback = fut_qf.result()

    result = {**summary_result, "question_feedback": question_feedback}

    scores = compute_scores(
        qf                 = question_feedback,
        speech_log         = speech_metrics_log,
        misbehavior        = misbehavior_log,
        is_terminated      = is_terminated,
        idk_qs             = idk_questions,
        history_length     = len(clean_history),
        termination_reason = termination_reason,
    )
    result.update(scores)
    if termination_reason:
        result["termination_reason"] = termination_reason

    if agg:
        result["speech_analysis"] = agg

    # v16: behavioral flags — surfaced for the UI to display explicitly
    result["behavioral_flags"] = _build_behavioral_flags(clean_history, misbehavior_log)

    # ── Hard override for blank / forfeited interviews ─────────────────────────
    # v17 changes:
    #   - executive_summary now identifies the most likely technical cause rather
    #     than issuing a generic "all zeros" message. This helps differentiate a
    #     genuine blank interview from a microphone / audio routing failure.
    #   - Per-question critiques are preserved when the LLM already wrote one.
    #     The generic fallback string is only used when critique is absent/empty,
    #     so diagnostic content isn't needlessly thrown away.
    #   - was_forfeited: True added to every question_feedback entry so the UI
    #     can surface a per-question indicator without inspecting score or
    #     answer_quality.
    valid_answers = sum(
        1 for turn in clean_history
        if not _is_forfeited(turn["answer"])
    )

    if valid_answers == 0:
        for field in ["technical_depth_score", "communication_score", "confidence_score",
                      "speech_clarity_score", "behaviour_score", "overall_score"]:
            result[field] = 0

        # v17: more informative message — names the microphone echo issue as the
        # most common cause so developers and candidates can diagnose it.
        result["executive_summary"] = (
            "No scoreable responses were detected in this session — every captured "
            "audio clip was silent, too short, or matched a known transcription "
            "artifact. If this result is unexpected, the most likely cause is the "
            "microphone picking up the interviewer's TTS audio output rather than "
            "the candidate's voice. Ensure echo cancellation is active and that the "
            "microphone is isolated from the speakers, then retry."
        )

        if "speech_analysis" in result:
            result["speech_analysis"].update({
                "avg_fluency_score":  0,
                "total_words_spoken": 0,
                "total_filler_count": 0,
                "fluency_label":      "Very Poor",
            })

        for q in result.get("question_feedback", []):
            q["score"]          = 0
            q["answer_quality"] = "blank"
            # v17: flag each entry so the UI can render a visual indicator
            # without needing to inspect score or answer_quality.
            q["was_forfeited"]  = True
            # v17: preserve the LLM-generated critique when it exists — even if
            # the audio was invalid the LLM may have captured useful diagnostic
            # context. Only fall back to the generic string when critique is
            # absent or empty.
            if not q.get("critique"):
                q["critique"] = "No valid audio response was captured for this question."

    return result