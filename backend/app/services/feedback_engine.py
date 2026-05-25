"""
feedback_engine.py — Enhanced
New: speech_metrics_log integrated into report
    per-question speech analysis (fluency, fillers, stutter)
    overall speech behaviour summary
    weighted scoring includes speech clarity
"""

import json
import re
from app.services.groq_client import chat


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


def _detect_issues(answer: str) -> dict:
    a = answer.lower()
    is_rude = any(re.search(p, a) for p in RUDE_PATTERNS)
    is_idk  = any(re.search(p, a) for p in IDK_PATTERNS)
    return {"is_rude": is_rude, "is_idk": is_idk}


def _speech_label(score: int) -> str:
    if score >= 85: return "Excellent"
    if score >= 70: return "Good"
    if score >= 55: return "Fair"
    if score >= 40: return "Poor"
    return "Very Poor"


def _aggregate_speech_metrics(speech_metrics_log: list[dict]) -> dict:
    """Aggregate per-question speech metrics into overall stats."""
    if not speech_metrics_log:
        return {}

    total_words      = 0
    total_fillers    = 0
    all_fillers      = {}
    all_repeats      = []
    fluency_scores   = []
    too_short_count  = 0
    too_long_count   = 0

    for entry in speech_metrics_log:
        m = entry.get("metrics", {})
        total_words   += m.get("word_count", 0)
        total_fillers += m.get("filler_count", 0)
        for fw in m.get("filler_words_found", []):
            all_fillers[fw] = all_fillers.get(fw, 0) + 1
        all_repeats.extend(m.get("repeated_words", []))
        if m.get("fluency_score") is not None:
            fluency_scores.append(m["fluency_score"])
        if m.get("too_short"):
            too_short_count += 1
        if m.get("too_long"):
            too_long_count += 1

    avg_fluency = round(sum(fluency_scores) / len(fluency_scores)) if fluency_scores else 0
    top_fillers = sorted(all_fillers.items(), key=lambda x: -x[1])[:5]

    # Build per-question speech summary
    per_question = []
    for entry in speech_metrics_log:
        m = entry.get("metrics", {})
        issues = []
        if m.get("filler_count", 0) >= 3:
            issues.append(f"Used filler words {m['filler_count']}x ({', '.join(m.get('filler_words_found', [])[:3])})")
        if m.get("repeated_words"):
            issues.append(f"Stuttered/repeated: {', '.join(m['repeated_words'][:3])}")
        if m.get("too_short"):
            issues.append("Answer too brief — lacked depth")
        if m.get("too_long"):
            issues.append("Answer too long — lost focus")
        per_question.append({
            "question": entry["question"][:90] + ("…" if len(entry["question"]) > 90 else ""),
            "word_count":     m.get("word_count", 0),
            "filler_count":   m.get("filler_count", 0),
            "fluency_score":  m.get("fluency_score", 0),
            "fluency_label":  _speech_label(m.get("fluency_score", 0)),
            "issues":         issues,
        })

    return {
        "total_words_spoken":   total_words,
        "total_filler_count":   total_fillers,
        "top_filler_words":     [{"word": w, "count": c} for w, c in top_fillers],
        "repeated_words":       list(set(all_repeats))[:8],
        "avg_fluency_score":    avg_fluency,
        "fluency_label":        _speech_label(avg_fluency),
        "too_short_answers":    too_short_count,
        "too_long_answers":     too_long_count,
        "per_question":         per_question,
    }


def generate_feedback(
    resume_data: dict,
    role: str,
    history: list[dict],
    misbehavior_log: list[dict] | None = None,
    speech_metrics_log: list[dict] | None = None,
) -> dict:
    annotated    = []
    idk_questions = []
    rude_answers  = []

    for turn in history:
        q = turn.get("question", "")
        a = turn.get("answer", "")
        flags = _detect_issues(a)
        annotated.append({**turn, **flags})
        if flags["is_idk"]:
            idk_questions.append(q)
        if flags["is_rude"]:
            rude_answers.append({"question": q, "answer": a})

    convo_text = "\n".join(
        [f"Q{i+1}: {t['question']}\nA{i+1}: {t['answer']}" for i, t in enumerate(annotated)]
    )

    idk_note = ""
    if idk_questions:
        idk_note = (
            "\n\nNOTED: The candidate said 'I don't know' or gave a non-answer to these questions:\n"
            + "\n".join(f"- {q}" for q in idk_questions)
            + "\nFor each, provide a specific 'study_topic' they need to learn thoroughly."
        )

    rude_note = ""
    if rude_answers:
        rude_note = (
            "\n\nNOTED: The candidate was rude or dismissive in these responses:\n"
            + "\n".join(f"- Q: {r['question']}\n  A: {r['answer']}" for r in rude_answers)
            + "\nFlag these in 'behavioral_flags'."
        )

    misbehavior_note = ""
    if misbehavior_log:
        strikes = len(misbehavior_log)
        misbehavior_note = (
            f"\n\nNOTED: The candidate received {strikes} misbehavior strike(s) during the interview. "
            "Factor this heavily into communication and behavioral scores. Strikes were: "
            + "; ".join(f"Strike {m['strike']}: {m['reason']} on Q '{m['question'][:50]}'" for m in misbehavior_log)
        )

    # Summarise speech for the AI prompt
    speech_summary = ""
    if speech_metrics_log:
        agg = _aggregate_speech_metrics(speech_metrics_log)
        speech_summary = (
            f"\n\nSPEECH ANALYSIS (from audio transcription):\n"
            f"- Total words spoken: {agg.get('total_words_spoken', 0)}\n"
            f"- Total filler word usage: {agg.get('total_filler_count', 0)} times\n"
            f"- Most used fillers: {', '.join(m['word'] for m in agg.get('top_filler_words', [])[:4]) or 'none'}\n"
            f"- Repeated/stuttered words: {', '.join(agg.get('repeated_words', [])[:4]) or 'none'}\n"
            f"- Answers that were too brief: {agg.get('too_short_answers', 0)}\n"
            f"- Answers that were too long: {agg.get('too_long_answers', 0)}\n"
            f"- Average fluency score: {agg.get('avg_fluency_score', 0)}/100\n"
            "Use this to evaluate speech_clarity_score and mention speech patterns in the summary if notable."
        )

    prompt = f"""You are an expert, unbiased interview evaluator for the role of {role}.

Interview transcript:
{convo_text}
{idk_note}
{rude_note}
{misbehavior_note}
{speech_summary}

SCORING RULES — FOLLOW STRICTLY:
- Scores range 0–100. Max realistic: 88. Min realistic: 12.
- 50 = average. 65 = solid but gaps. 75 = genuinely strong.
- DO NOT inflate scores. Multiple "I don't know" answers = below 50 in technical_depth.
- confidence_score: Penalise very short, vague, or "IDK" answers. Also penalise rambling.
- communication_score: Penalise rude language, deflection, and rambling.
- technical_depth_score: Penalise "I don't know". Reward correct technical specifics.
- behaviour_score: 0-100. Professionalism, tone, pushback handling. Rude/dismissive = very low.
- speech_clarity_score: 0-100. Based on filler word usage, stuttering, answer length appropriateness.
  100 = no fillers, clear and measured. Deduct 4 per filler usage, 6 per stutter cluster.
- overall_score: Weighted — technical_depth 30%, confidence 20%, communication 20%, behaviour 15%, speech_clarity 15%.

Return ONLY a valid JSON object (no markdown fences):
{{
  "overall_score": <integer>,
  "confidence_score": <integer>,
  "clarity_score": <integer>,
  "technical_depth_score": <integer>,
  "communication_score": <integer>,
  "behaviour_score": <integer>,
  "speech_clarity_score": <integer>,
  "summary": "2-3 sentences: honest, direct summary",
  "speech_summary": "1-2 sentences: honest assessment of how clearly they spoke, mention filler words or stuttering if present",
  "confidence_calibration": "under-confident | balanced | over-confident",
  "confidence_note": "1 sentence explaining the confidence calibration",
  "strengths": ["strength 1", "strength 2"],
  "improvements": ["specific area 1", "specific area 2", "specific area 3"],
  "behavioral_flags": [
    {{
      "type": "rude | dismissive | over-confident | evasive | idk",
      "question": "the question",
      "note": "what they said and why it's a problem"
    }}
  ],
  "question_feedback": [
    {{
      "question": "the question",
      "answer_quality": "strong | good | average | weak | blank",
      "answer_summary": "1 sentence: what they actually said",
      "comment": "specific honest feedback",
      "what_you_should_have_said": "ideal answer approach in 2-3 sentences",
      "ideal_points": ["key point 1", "key point 2"],
      "study_topic": null
    }}
  ],
  "recommended_topics": [
    {{
      "topic": "Topic Name",
      "reason": "Why they need to study this",
      "priority": "high | medium"
    }}
  ]
}}

For any question where the candidate said 'I don't know':
- Set answer_quality to "blank" or "weak"
- Set study_topic to the concept they should study
- Add that topic to recommended_topics with priority "high"

Return ONLY the JSON. No preamble.
"""

    response = chat(
        [{"role": "user", "content": prompt}],
        temperature=0.25,
    )

    clean = response.strip()
    if clean.startswith("```"):
        clean = clean.split("```")[1]
        if clean.startswith("json"):
            clean = clean[4:]
    clean = clean.strip()

    try:
        result = json.loads(clean)

        # Back-fill idk topics if AI missed them
        existing_topics = {t["topic"].lower() for t in result.get("recommended_topics", [])}
        for q in idk_questions:
            topic_key = q[:40].lower()
            if not any(topic_key in t for t in existing_topics):
                result.setdefault("recommended_topics", []).append({
                    "topic": q[:80],
                    "reason": "Candidate said 'I don't know' when asked this question",
                    "priority": "high",
                })

        # Attach aggregated speech metrics to the result
        if speech_metrics_log:
            result["speech_analysis"] = _aggregate_speech_metrics(speech_metrics_log)

        return result

    except json.JSONDecodeError:
        return {
            "error": "Could not parse feedback",
            "raw": response,
            "overall_score": 0,
        }