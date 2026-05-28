"""
feedback_engine.py — v10

Fix: Strong answers now show LLM-generated critique instead of rule-based fallback text.

Root cause (v9): _score_batch returned no `answer_quality` field, so the frontend
(or downstream code) re-derived it from the numeric score and rendered hardcoded
_assess_quality() text, completely ignoring the LLM's critique and ideal_answer_snippet.

Changes from v9:
  1. _score_batch now derives and injects `answer_quality` from score after each batch.
  2. Every LLM-scored entry gets `"_source": "llm"` so the frontend can assert it
     should render `critique` and `ideal_answer_snippet` directly — never fall back to
     canned label text.
  3. Backfilled entries get `"_source": "rule_based"` for transparency.
  4. Added per-entry diagnostic logging in _call_question_feedback so you can
     confirm critique length > 0 before the payload leaves the backend.

Everything else (two-call architecture, batching, robust JSON extraction,
score computation, speech analysis) is unchanged from v9.
"""

import json
import re
import logging
from app.services.groq_client import chat

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

QUALITY_SCORE = {"strong": 90, "good": 75, "average": 55, "weak": 30, "blank": 10}

RUDE_PATTERNS = [
    r"\bstupid\b", r"\bidiot\b", r"\bdumb\b", r"\bwaste\b.*\btime\b",
    r"\bshut up\b", r"\bfuck\b", r"\bscrew\b", r"\bwhatever\b",
    r"\bthis is (dumb|pointless|stupid)\b", r"\bi don'?t care\b",
]
IDK_PATTERNS = [
    r"\bi don'?t know\b", r"\bno idea\b", r"\bnot sure\b",
    r"\bnever heard\b", r"\bcan'?t answer\b", r"\bpass\b",
    r"\bi'?m not familiar\b", r"\bi have no clue\b",
    r"\bwhat is (that|this)\b", r"\bno clue\b",
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def detect_issues(answer: str) -> dict:
    a = answer.lower()
    return {
        "is_rude": any(re.search(p, a) for p in RUDE_PATTERNS),
        "is_idk":  any(re.search(p, a) for p in IDK_PATTERNS),
    }

def speech_label(score: int) -> str:
    if score >= 85: return "Excellent"
    if score >= 70: return "Good"
    if score >= 55: return "Fair"
    if score >= 40: return "Poor"
    return "Very Poor"

def score_to_quality_label(score: int) -> str:
    """
    Derives answer_quality label from a numeric score.
    Used to stamp every entry with a consistent label so the frontend
    NEVER needs to re-derive it and accidentally shows canned text.
    """
    if score >= 85: return "strong"
    if score >= 70: return "good"
    if score >= 50: return "average"
    if score >= 30: return "weak"
    return "blank"


# ── Speech metrics ─────────────────────────────────────────────────────────────

def aggregate_speech_metrics(speech_metrics_log: list[dict]) -> dict:
    if not speech_metrics_log:
        return {}
    total_words = total_fillers = 0
    all_fillers: dict = {}
    all_repeats: list = []
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
        m = entry.get("metrics", {})
        issues = []
        if m.get("filler_count", 0) >= 2:
            issues.append(f"Filler words used {m['filler_count']}× ({', '.join(m.get('filler_words_found', [])[:3])})")
        if m.get("repeated_words"):
            issues.append(f"Repeated words: {', '.join(m['repeated_words'][:3])}")
        if m.get("too_short"): issues.append("Answer too brief — not enough depth")
        if m.get("too_long"):  issues.append("Answer too long — lost focus")
        per_question.append({
            "question":      entry["question"][:90] + ("…" if len(entry["question"]) > 90 else ""),
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


# ── Robust JSON extraction ─────────────────────────────────────────────────────

def extract_json(raw: str, fallback_history: list[dict]) -> dict:
    clean = raw.strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass
    if clean.startswith("```"):
        for part in clean.split("```"):
            part = part.strip().lstrip("json").strip()
            try:
                return json.loads(part)
            except json.JSONDecodeError:
                continue
    m = re.search(r'\{[\s\S]*\}', clean)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    fixed = re.sub(r',\s*}', '}', re.sub(r',\s*]', ']', clean))
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    logger.error("All JSON extraction strategies failed — using rule-based fallback")
    return _build_fallback(fallback_history)


def extract_json_array(raw: str) -> list:
    """Extracts a JSON array from raw LLM response. Returns [] on failure."""
    clean = raw.strip()
    try:
        result = json.loads(clean)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "question_feedback" in result:
            return result["question_feedback"]
    except json.JSONDecodeError:
        pass
    if clean.startswith("```"):
        for part in clean.split("```"):
            part = part.strip().lstrip("json").strip()
            try:
                result = json.loads(part)
                if isinstance(result, list): return result
                if isinstance(result, dict) and "question_feedback" in result:
                    return result["question_feedback"]
            except json.JSONDecodeError:
                continue
    m = re.search(r'\[[\s\S]*\]', clean)
    if m:
        try:
            result = json.loads(m.group())
            if isinstance(result, list): return result
        except json.JSONDecodeError:
            pass
    fixed = re.sub(r',\s*\]', ']', re.sub(r',\s*}', '}', clean))
    m2 = re.search(r'\[[\s\S]*\]', fixed)
    if m2:
        try:
            result = json.loads(m2.group())
            if isinstance(result, list): return result
        except json.JSONDecodeError:
            pass
    objects = re.findall(r'\{[^{}]*\}', clean)
    parsed = []
    for obj in objects:
        try:
            parsed.append(json.loads(obj))
        except json.JSONDecodeError:
            continue
    return parsed


# ── Rule-based fallbacks ───────────────────────────────────────────────────────

def _assess_quality(answer: str) -> dict:
    if not answer or len(answer.strip()) < 10:
        return {"label": "blank",   "comment": "Answer was too brief to assess.",
                "ideal": "Provide a complete response with specific examples.",
                "key_points": ["State your approach", "Give concrete examples"]}
    w = answer.lower()
    has_tech    = any(k in w for k in ["python","model","algorithm","accuracy","data","deployed",
                                        "api","database","implemented","built","optimized","react",
                                        "sql","docker","tensorflow","pytorch","function","class"])
    has_metrics = bool(re.search(r'\d+%|\d+\.\d+|\d+x|\d+ ms|\d+k\b', w))
    has_struct  = any(k in w for k in ["first","then","finally","because","specifically",
                                        "for example","we used","i built","i designed","i implemented"])
    n = len(answer)
    if has_tech and has_metrics and has_struct and n > 200:
        return {"label": "strong",  "comment": "Strong answer with technical depth, metrics, and clear structure.",
                "ideal": "Demonstrates deep understanding.", "key_points": ["Depth", "Metrics", "Structure"]}
    if has_tech and (has_metrics or has_struct) and n > 100:
        return {"label": "good",    "comment": "Good answer with relevant technical content. Could add more quantitative results.",
                "ideal": "Add specific metrics and quantify outcomes.", "key_points": ["Show understanding", "Add metrics"]}
    if has_tech and n > 50:
        return {"label": "average", "comment": "Shows basic familiarity but answer stays at a surface level.",
                "ideal": "Explain the specific approach taken and what the outcome was.", "key_points": ["Add specifics", "Quantify"]}
    if n > 30:
        return {"label": "weak",    "comment": "Answer lacks technical substance.",
                "ideal": "Mention specific technologies used and describe your personal contribution.",
                "key_points": ["Name specific tools", "Describe your role"]}
    return     {"label": "blank",   "comment": "Answer was too brief.",
                "ideal": "Expand with technical details and examples.", "key_points": ["Expand answer"]}


def _build_fallback(history: list[dict]) -> dict:
    qf, strengths, improvements, topics = [], [], [], []
    for i, turn in enumerate(history):
        a, q = turn.get("answer", ""), turn.get("question", "")
        quality = _assess_quality(a)
        qf.append({
            "question":             q,
            "score":                QUALITY_SCORE.get(quality["label"], 55),
            "answer_quality":       quality["label"],
            "answer_summary":       a[:80] + ("…" if len(a) > 80 else ""),
            "critique":             quality["comment"],
            "ideal_answer_snippet": quality["ideal"],
            "ideal_points":         quality["key_points"],
            "_backfilled":          True,
            "_source":              "rule_based",
        })
        if quality["label"] in ("strong", "good"):
            strengths.append(f"Demonstrated relevant technical knowledge on Q{i+1}: {q[:60]}…")
        if quality["label"] in ("weak", "blank"):
            improvements.append(f"Q{i+1} lacked technical substance — review core concepts for: {q[:50]}")
            topics.append({"topic": q[:80], "reason": f"Insufficient answer on Q{i+1}", "priority": "High"})

    avg_w = sum(len(t.get("answer","")) for t in history) / max(len(history), 1)
    return {
        "executive_summary": f"Candidate completed {len(history)} questions with {'detailed' if avg_w>150 else 'moderate'} responses. Technical depth varies across topics.",
        "strengths":    strengths[:3] or ["Completed all interview questions", "Attempted every topic area"],
        "improvements": improvements[:3] or ["Add specific metrics and tools to answers", "Structure responses: problem → approach → result"],
        "learning_path": topics[:5],
        "question_feedback": qf,
        "confidence_calibration": "balanced",
        "confidence_note": "Based on answer patterns observed during interview.",
    }


# ── Score computation ──────────────────────────────────────────────────────────

def _calibrate(scores: dict, qf: list[dict]) -> dict:
    labels = [q.get("answer_quality", "average") for q in qf]
    if len(set(labels)) == 1 and len(qf) >= 3:
        scores["confidence_score"] = max(20, scores["confidence_score"] - 15)
        scores["overall_score"]    = max(10, scores["overall_score"]    - 10)
    raw = [int(q["score"]) if "score" in q else QUALITY_SCORE.get(q.get("answer_quality","average"), 55) for q in qf]
    if len(set(raw)) == 1 and len(raw) >= 3:
        raw = [65 if len(q.get("answer_summary",""))>150 else (45 if len(q.get("answer_summary",""))<50 else v)
               for v, q in zip(raw, qf)]
        scores["technical_depth_score"] = round(sum(raw)/len(raw))
        scores["overall_score"] = round(
            scores["technical_depth_score"]*0.30 + scores["confidence_score"]*0.20
            + scores["communication_score"]*0.20 + scores["behaviour_score"]*0.15
            + scores["speech_clarity_score"]*0.15
        )
    return scores


def compute_scores(qf: list[dict], speech_log: list[dict], misbehavior: list[dict],
                   is_terminated: bool, idk_qs: list[str]) -> dict:
    n   = len(qf)
    agg = aggregate_speech_metrics(speech_log)

    if n > 0:
        raw = [int(q["score"]) if "score" in q else QUALITY_SCORE.get(q.get("answer_quality","average"),55) for q in qf]
        tech = round(sum(raw)/len(raw))
    else:
        raw, tech = [], 0

    speech_clarity = agg.get("avg_fluency_score", 50) if agg else 50
    strikes        = len(misbehavior)
    behaviour      = max(0, 100 - strikes*28)
    if is_terminated: behaviour = min(behaviour, 30)

    idk_ratio   = len(idk_qs) / max(n, 1)
    avg_wc      = (sum(e.get("metrics",{}).get("word_count",0) for e in speech_log) / max(len(speech_log),1)) if speech_log else 50
    word_signal = min(100, max(0, (avg_wc-20)/80*60+40))
    confidence  = min(88, max(10, round(tech*0.4 + word_signal*0.4 - idk_ratio*50)))
    communication = min(85, round(speech_clarity*0.40 + tech*0.35 + behaviour*0.25))
    overall       = min(88, round(tech*0.30 + confidence*0.20 + communication*0.20 + behaviour*0.15 + speech_clarity*0.15))

    scores = {
        "overall_score":         overall,
        "technical_depth_score": tech,
        "confidence_score":      confidence,
        "communication_score":   communication,
        "behaviour_score":       behaviour,
        "speech_clarity_score":  speech_clarity,
        "score_inputs": {
            "quality_scores": raw,
            "avg_fluency":    agg.get("avg_fluency_score",0) if agg else 0,
            "strikes":        strikes,
            "idk_ratio":      round(idk_ratio, 2),
            "avg_word_count": round(avg_wc, 1),
        },
    }
    return _calibrate(scores, qf)


# ── LLM Call 1: Summary ────────────────────────────────────────────────────────

def _call_summary(role: str, convo_text: str, speech_ctx: str, strikes_ctx: str) -> dict:
    """
    Generates: executive_summary, strengths, improvements, learning_path,
    confidence_calibration, confidence_note.
    Small output — never hits token limits.
    """
    prompt = f"""You are a Senior Engineering Manager writing a post-interview assessment for {role}.

Transcript:
{convo_text}
{speech_ctx}
{strikes_ctx}

Return ONLY valid JSON, no markdown:
{{
  "executive_summary": "3-4 sentences. Reference specific technologies and topics from the transcript. Assess technical readiness honestly. No filler phrases like 'shows promise'.",
  "strengths": [
    "Evidence-based strength citing a specific question/answer (e.g. 'Correctly explained X trade-off in Q3')",
    "Another specific strength"
  ],
  "improvements": [
    "Specific gap with actionable guidance (e.g. 'Q5 on X was surface-level — needs to understand Y')",
    "Second specific improvement",
    "Third specific improvement"
  ],
  "learning_path": [
    {{"topic": "Specific concept", "priority": "High|Medium", "reason": "What the transcript revealed about this gap"}}
  ],
  "confidence_calibration": "under-confident | balanced | over-confident",
  "confidence_note": "One sentence grounded in actual answers, not generic."
}}"""

    raw = chat([{"role": "user", "content": prompt}], temperature=0.2)
    result = extract_json(raw, [])
    result.setdefault("executive_summary", "Assessment unavailable.")
    result.setdefault("strengths", ["Completed all interview questions"])
    result.setdefault("improvements", ["Add specific metrics to technical answers", "Structure: problem → approach → result"])
    result.setdefault("learning_path", [])
    result.setdefault("confidence_calibration", "balanced")
    result.setdefault("confidence_note", "")
    return result


# ── LLM Call 2: Per-question feedback (batched) ───────────────────────────────

BATCH_SIZE = 3  # questions per LLM call — keeps output well under token limits


def _score_batch(role: str, batch: list[dict], offset: int) -> list[dict]:
    """
    Score a small batch of Q/A pairs. Returns one entry per pair.

    FIX (v10): After parsing LLM output, we now:
      - Derive and inject `answer_quality` from the numeric score using
        score_to_quality_label() so the frontend NEVER re-derives it and
        accidentally renders canned rule-based text.
      - Tag every LLM-scored entry with `_source: "llm"` so the frontend
        can assert it must render `critique` and `ideal_answer_snippet` directly.
    """
    n = len(batch)
    qa_lines = "\n\n".join(
        f"Q{offset+i+1}: {t['question']}\nA{offset+i+1}: {t['answer'][:800]}"
        for i, t in enumerate(batch)
    )

    prompt = f"""You are evaluating {n} interview answer(s) for a {role} position.

{qa_lines}

SCORING RUBRIC:
85-100: Correct + specific tool/metric/example + explains trade-offs
65-84:  Correct direction + some specifics + solid understanding
40-64:  Broad understanding, surface-level, no concrete evidence
20-39:  Significant gaps or incorrect understanding
0-19:   No attempt or off-topic

RULES:
- Vary scores: use values like 71, 63, 84, 58 — not round numbers
- On-topic but shallow → 55-65 max
- Genuine attempt with some substance → 50-65 (give credit)
- Below 40 only for wrong facts or no attempt

CRITIQUE RULES:
BANNED phrases: "not specific enough", "lacked specificity", "more detail", "good attempt"
REQUIRED: Quote what they actually said. Name the exact missing concept or metric.
For partial answers: acknowledge what was right before stating the gap.

Return ONLY a JSON array with exactly {n} object(s):
[
  {{
    "question": "<copy question text exactly>",
    "score": <integer 0-100>,
    "critique": "2-3 sentences referencing their actual words. Name specific gaps.",
    "ideal_answer_snippet": "1-2 sentences: what a strong answer would include specifically."
  }}
]"""

    raw = chat([{"role": "user", "content": prompt}], temperature=0.2)
    entries = extract_json_array(raw)

    # Backfill if batch call returned fewer than expected
    for i in range(len(entries), n):
        turn = batch[i]
        quality = _assess_quality(turn.get("answer", ""))
        logger.warning("Backfilling Q%d (batch returned %d/%d)", offset+i+1, len(entries), n)
        entries.append({
            "question":             turn.get("question", ""),
            "score":                QUALITY_SCORE.get(quality["label"], 55),
            "critique":             quality["comment"],
            "ideal_answer_snippet": quality["ideal"],
            "_backfilled":          True,
            "_source":              "rule_based",
        })

    # ── v10 FIX: Stamp answer_quality and _source on every LLM-scored entry ──
    # This prevents the frontend from re-deriving answer_quality and then
    # rendering hardcoded canned text instead of the LLM's actual critique.
    for i, entry in enumerate(entries[:n]):
        # Ensure required fields exist
        entry.setdefault("question", batch[i].get("question", "") if i < len(batch) else "")
        entry.setdefault("score", 55)
        entry.setdefault("critique", "")
        entry.setdefault("ideal_answer_snippet", "")

        # Derive and inject answer_quality directly from the score
        entry["answer_quality"] = score_to_quality_label(int(entry["score"]))

        # Tag source so frontend knows to trust critique/ideal_answer_snippet fields
        entry.setdefault("_source", "llm")

    return entries[:n]


def _call_question_feedback(role: str, history: list[dict]) -> list[dict]:
    """
    Scores all questions by batching into groups of BATCH_SIZE.
    3 questions per call = ~600 tokens output max — never hits token limits.
    """
    all_entries: list[dict] = []
    for start in range(0, len(history), BATCH_SIZE):
        batch = history[start:start + BATCH_SIZE]
        entries = _score_batch(role, batch, offset=start)
        all_entries.extend(entries)
        logger.info("Scored Q%d–Q%d (%d/%d total)", start+1, start+len(batch), len(all_entries), len(history))

    # Final safety: ensure we have one entry per question
    if len(all_entries) < len(history):
        logger.error("After batching: only %d/%d entries — backfilling rest", len(all_entries), len(history))
        for i in range(len(all_entries), len(history)):
            turn = history[i]
            quality = _assess_quality(turn.get("answer", ""))
            all_entries.append({
                "question":             turn.get("question", ""),
                "score":                QUALITY_SCORE.get(quality["label"], 55),
                "answer_quality":       quality["label"],
                "critique":             quality["comment"],
                "ideal_answer_snippet": quality["ideal"],
                "_backfilled":          True,
                "_source":              "rule_based",
            })

    # Guarantee required fields on all entries + diagnostic logging
    for i, entry in enumerate(all_entries):
        entry.setdefault("question", history[i].get("question", "") if i < len(history) else "")
        entry.setdefault("score", 55)
        entry.setdefault("critique", "")
        entry.setdefault("ideal_answer_snippet", "")
        entry.setdefault("answer_quality", score_to_quality_label(int(entry["score"])))
        entry.setdefault("_source", "llm")

        # Diagnostic: if critique is empty for an LLM-sourced entry, something went wrong
        if entry["_source"] == "llm" and not entry["critique"]:
            logger.warning(
                "Q%d | score=%s | answer_quality=%s | _source=llm but critique is EMPTY — "
                "LLM may have returned partial output for this entry.",
                i+1, entry["score"], entry["answer_quality"]
            )
        else:
            logger.info(
                "Q%d | score=%s | answer_quality=%s | _source=%s | critique_len=%d",
                i+1, entry["score"], entry["answer_quality"],
                entry["_source"], len(entry["critique"])
            )

    return all_entries


# ── Main entry point ───────────────────────────────────────────────────────────

def generate_feedback(
    resume_data: dict,
    role: str,
    history: list[dict],
    misbehavior_log: list[dict]    | None = None,
    speech_metrics_log: list[dict] | None = None,
    is_terminated: bool = False,
) -> dict:
    misbehavior_log    = misbehavior_log    or []
    speech_metrics_log = speech_metrics_log or []

    annotated, idk_questions = [], []
    for turn in history:
        q, a = turn.get("question",""), turn.get("answer","")
        flags = detect_issues(a)
        annotated.append({**turn, **flags})
        if flags["is_idk"]: idk_questions.append(q)

    convo_text = "\n".join(f"Q{i+1}: {t['question']}\nA{i+1}: {t['answer']}" for i,t in enumerate(annotated)) \
                 or "(No answers recorded)"

    agg = aggregate_speech_metrics(speech_metrics_log)
    speech_ctx = (
        f"\nSPEECH: {agg['total_words_spoken']} words | {agg['total_filler_count']} fillers | "
        f"fluency {agg['avg_fluency_score']}/100 | top fillers: "
        f"{', '.join(f['word'] for f in agg['top_filler_words'][:4]) or 'none'}"
    ) if agg else ""

    strikes_ctx = ""
    if misbehavior_log:
        strikes_ctx = f"\nSTRIKES: {len(misbehavior_log)} — " + "; ".join(
            f"Strike {m['strike']}: {m['reason']}" for m in misbehavior_log)
    if is_terminated:
        strikes_ctx += "\nINTERVIEW TERMINATED."

    # ── Two separate LLM calls ─────────────────────────────────────────────────
    summary_result    = _call_summary(role, convo_text, speech_ctx, strikes_ctx)
    question_feedback = _call_question_feedback(role, history)

    # ── Merge ──────────────────────────────────────────────────────────────────
    result = {**summary_result, "question_feedback": question_feedback}

    # ── Compute scores ─────────────────────────────────────────────────────────
    scores = compute_scores(
        qf            = question_feedback,
        speech_log    = speech_metrics_log,
        misbehavior   = misbehavior_log,
        is_terminated = is_terminated,
        idk_qs        = idk_questions,
    )
    result.update(scores)

    # ── IDK backfill into learning_path ───────────────────────────────────────
    existing_topics = {t["topic"].lower() for t in result.get("learning_path", [])}
    for q in idk_questions:
        if not any(q[:30].lower() in t for t in existing_topics):
            result.setdefault("learning_path", []).append({
                "topic":    q[:80],
                "reason":   "Candidate had no answer for this question",
                "priority": "High",
            })

    # ── Attach speech analysis ─────────────────────────────────────────────────
    if agg:
        result["speech_analysis"] = agg

    return result