import json
import PyPDF2
import io
from app.services.groq_client import chat


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract raw text from a PDF file."""
    reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
    text = ""
    for page in reader.pages:
        text += page.extract_text() or ""
    return text.strip()


def parse_resume(pdf_bytes: bytes) -> dict:
    """
    Extract structured info from resume using Groq LLM.
    Returns a dict with: name, skills, projects, experience, education
    """
    raw_text = extract_text_from_pdf(pdf_bytes)

    if not raw_text:
        return {"error": "Could not extract text from PDF. Make sure it is not a scanned image."}

    prompt = f"""
You are a resume parser. Extract structured information from the following resume text.

Return ONLY a valid JSON object with these exact keys:
{{
  "name": "candidate's full name",
  "skills": ["skill1", "skill2", ...],
  "projects": [
    {{
      "name": "project name",
      "description": "what it does in 1-2 sentences",
      "tech_stack": ["tech1", "tech2"],
      "highlights": ["key feature or achievement"]
    }}
  ],
  "experience": [
    {{
      "company": "company name",
      "role": "role title",
      "duration": "e.g. Jan 2023 - Present",
      "description": "brief summary"
    }}
  ],
  "education": [
    {{
      "institution": "college/university",
      "degree": "degree name",
      "year": "graduation year or expected"
    }}
  ]
}}

If a section is missing, use an empty list. Return ONLY the JSON, no explanation.

RESUME TEXT:
{raw_text[:4000]}
"""

    response = chat([{"role": "user", "content": prompt}], temperature=0.2)

    # Strip markdown fences if present
    clean = response.strip()
    if clean.startswith("```"):
        clean = clean.split("```")[1]
        if clean.startswith("json"):
            clean = clean[4:]
    clean = clean.strip()

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        return {"raw": response, "error": "Could not parse structured output"}
