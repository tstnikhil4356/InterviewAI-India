import json
import re
import io
import logging
import PyPDF2
from app.services.groq_client import chat

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


# ── Robust JSON extraction (same pattern as feedback_engine) ───────────────────

def _extract_json(raw: str) -> dict | None:
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
        return None


# ── Main parser ────────────────────────────────────────────────────────────────

def parse_resume(pdf_bytes: bytes) -> dict:
    raw_text = extract_text_from_pdf(pdf_bytes)

    if not raw_text:
        return {"error": "Could not extract text from PDF. Make sure it is not a scanned image."}

    prompt = f"""You are an expert resume parser. Extract structured information from the resume below.

CRITICAL RULES — read carefully before extracting:

1. EXPERIENCE vs EDUCATION — never mix these up:
   - "experience" = paid jobs, internships, part-time roles at a company/organisation.
     Duration = the actual employment period (e.g. "Jun 2023 – Aug 2023").
   - "education" = degrees, diplomas, courses at a college/university.
     Duration = the study period or graduation year (e.g. "2020 – 2024").
   - If someone did an internship DURING their degree, the internship goes in "experience"
     with the real internship dates, NOT the degree dates.
   - NEVER copy the graduation year as an internship duration.

2. DURATION FORMAT — extract exactly what is written. If the resume says "2 months" or
   "Summer 2023", use that. Do not infer or calculate durations.

3. SKILLS — only list explicitly mentioned technical skills, tools, languages, frameworks.
   Do not infer skills from project descriptions.

4. PROJECTS — only include explicitly named projects or products.
   Internship work tasks are NOT projects unless given a distinct name.

Return ONLY a valid JSON object with this exact structure (no markdown, no extra text):
{{
  "name": "candidate full name",
  "contact": {{
    "email": "email or null",
    "phone": "phone or null",
    "linkedin": "linkedin url or null",
    "github": "github url or null"
  }},
  "skills": {{
    "languages": ["Python", "Java"],
    "frameworks": ["React", "FastAPI"],
    "tools": ["Git", "Docker"],
    "other": ["Machine Learning", "SQL"]
  }},
  "experience": [
    {{
      "company": "Company Name",
      "role": "Exact Role Title",
      "duration": "exact duration as written on resume",
      "employment_type": "internship | full-time | part-time | contract | freelance",
      "description": "concise 1-2 sentence summary of what they actually did",
      "achievements": ["specific achievement or responsibility 1", "specific achievement 2"]
    }}
  ],
  "education": [
    {{
      "institution": "University/College Name",
      "degree": "Full degree name e.g. B.Tech Computer Science",
      "duration": "study period e.g. 2020 – 2024",
      "grade": "GPA/percentage/grade if mentioned, else null",
      "relevant_courses": ["course1", "course2"]
    }}
  ],
  "projects": [
    {{
      "name": "Project Name",
      "description": "what it does in 1-2 sentences",
      "tech_stack": ["tech1", "tech2"],
      "highlights": ["key metric or achievement"],
      "link": "github/live link or null"
    }}
  ],
  "certifications": [
    {{
      "name": "certification name",
      "issuer": "issuing body",
      "year": "year or null"
    }}
  ],
  "summary": "2-3 sentence professional summary derived from the resume. Be specific — mention their domain, years of experience, and strongest technical areas."
}}

Use empty lists [] for missing sections. Use null for missing string fields.
Return ONLY the JSON object.

RESUME:
{raw_text[:5000]}"""

    response = chat([{"role": "user", "content": prompt}], temperature=0.1)
    result = _extract_json(response)

    if result is None:
        logger.error("Resume parse failed — raw response: %s", response[:200])
        return {"error": "Could not parse resume structure.", "raw": response}

    # ── Post-processing: catch common duration bleed-over ──────────────────────
    result = _fix_duration_bleed(result)
    return result


def _fix_duration_bleed(data: dict) -> dict:
    """
    Heuristic guard: if an experience entry's duration matches a graduation
    year span from education, flag it so the interviewer system knows to skip
    using that duration as 'years of experience'.
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
        # If every year in this experience duration also appears in education spans,
        # it might be a bleed-over — tag it for review
        if exp_years and all(y in edu_spans for y in exp_years):
            exp.setdefault("_warnings", [])
            exp["_warnings"].append(
                "Duration overlaps fully with an education period — verify this is correct."
            )

    return data