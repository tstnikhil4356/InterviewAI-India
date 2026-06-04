import json
import re
import io
import logging
import os
import PyPDF2
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# ── PDF text extraction ────────────────────────────────────────────────────────

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"[PAGE {i+1}]\n{text}")
    return "\n\n".join(pages).strip()


# ── Groq client specifically for parsing (small fast model, high TPM) ──────────

def _parse_client():
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise ValueError("GROQ_API_KEY is missing.")
    return Groq(api_key=api_key)


# ── Robust JSON extraction ─────────────────────────────────────────────────────

def _extract_json(raw: str) -> dict | None:
    clean = raw.strip()

    # 1. Direct parse
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # 2. Strip ```json ... ``` fences robustly
    fence_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', clean)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3. Grab the first { ... } block
    m = re.search(r'\{[\s\S]*\}', clean)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    # 4. Truncation recovery — close unclosed braces/brackets
    if m:
        recovered = _close_truncated_json(m.group())
        try:
            return json.loads(recovered)
        except json.JSONDecodeError:
            pass

    # 5. Trailing comma fix
    if m:
        fixed = re.sub(r',\s*}', '}', re.sub(r',\s*]', ']', m.group()))
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

    return None


def _close_truncated_json(s: str) -> str:
    """Best-effort recovery for truncated JSON — closes open strings, arrays, objects."""
    in_string = False
    escape_next = False
    for ch in s:
        if escape_next:
            escape_next = False
            continue
        if ch == '\\':
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string

    if in_string:
        s += '"'

    s = re.sub(r',\s*$', '', s.strip())

    open_braces = s.count('{') - s.count('}')
    open_brackets = s.count('[') - s.count(']')

    s += ']' * max(open_brackets, 0)
    s += '}' * max(open_braces, 0)

    return s


# ── Main parser ────────────────────────────────────────────────────────────────

def parse_resume(pdf_bytes: bytes) -> dict:
    raw_text = extract_text_from_pdf(pdf_bytes)

    if not raw_text:
        return {"error": "Could not extract text from PDF. Make sure it is not a scanned image."}

    resume_text = raw_text[:3000]

    prompt = f"""You are a resume parser. Return ONLY a JSON object. No markdown. No code fences. No explanation. Start your response with {{ and end with }}.

Fill this exact structure:
{{
  "name": "full name or null",
  "contact": {{
    "email": "email or null",
    "phone": "phone or null",
    "linkedin": "url or null",
    "github": "url or null"
  }},
  "skills": {{
    "languages": [],
    "frameworks": [],
    "tools": [],
    "other": []
  }},
  "experience": [
    {{
      "company": "company name",
      "role": "exact role title",
      "duration": "exact duration as written on resume",
      "employment_type": "internship | full-time | part-time | contract | freelance",
      "description": "1-2 sentence summary of what they did",
      "achievements": ["achievement 1", "achievement 2"]
    }}
  ],
  "education": [
    {{
      "institution": "university or college name",
      "degree": "full degree name",
      "duration": "study period e.g. 2020-2024",
      "grade": "GPA or percentage or null",
      "relevant_courses": []
    }}
  ],
  "projects": [
    {{
      "name": "project name",
      "description": "1-2 sentence description",
      "tech_stack": [],
      "highlights": [],
      "link": "url or null"
    }}
  ],
  "certifications": [
    {{
      "name": "cert name",
      "issuer": "issuing body",
      "year": "year or null"
    }}
  ],
  "summary": "2-3 sentence professional summary mentioning domain, experience level, and key skills"
}}

STRICT RULES:
- Internships belong in experience with their actual dates, NEVER the degree dates
- Only list skills explicitly mentioned in the resume
- Only include projects that have a distinct name — internship tasks are NOT projects
- Use [] for missing array fields, null for missing string fields
- Your response MUST start with {{ and end with }} — no other text

RESUME:
{resume_text}"""

    try:
        client = _parse_client()
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=2048,
        )
        raw = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error("Groq API call failed: %s", e)
        return {"error": f"LLM call failed: {str(e)}"}

    result = _extract_json(raw)

    if result is None:
        logger.error("Resume parse failed — raw response: %s", raw[:500])
        return {"error": "Could not parse resume structure.", "raw": raw}

    result = _fix_duration_bleed(result)
    return result


# ── Post-processing ────────────────────────────────────────────────────────────

def _fix_duration_bleed(data: dict) -> dict:
    """
    Heuristic guard: if an experience entry's duration matches a graduation
    year span from education, flag it for review.
    """
    edu_spans = set()
    for edu in data.get("education", []):
        d = edu.get("duration", "")
        years = re.findall(r'\b(20\d{2}|19\d{2})\b', d)
        for y in years:
            edu_spans.add(y)

    for exp in data.get("experience", []):
        d = exp.get("duration", "")
        exp_years = re.findall(r'\b(20\d{2}|19\d{2})\b', d)
        if exp_years and all(y in edu_spans for y in exp_years):
            exp.setdefault("_warnings", [])
            exp["_warnings"].append(
                "Duration overlaps fully with an education period — verify this is correct."
            )

    return data