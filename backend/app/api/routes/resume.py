from fastapi import APIRouter, UploadFile, File, HTTPException
from app.services.resume_parser import parse_resume

router = APIRouter()

@router.post("/parse")
async def parse_resume_endpoint(resume: UploadFile = File(...)):
    """Parse a resume PDF and return structured data. Useful for testing."""
    if not resume.filename or not resume.filename.endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")

    pdf_bytes = await resume.read()
    result = parse_resume(pdf_bytes)

    if "error" in result and "raw" not in result:
        raise HTTPException(422, result["error"])

    return result
