from fastapi import APIRouter
from pydantic import BaseModel
from app.services.feedback_engine import generate_feedback

router = APIRouter()

class FeedbackRequest(BaseModel):
    resume_data: dict
    role: str
    history: list[dict]  # [{"question": ..., "answer": ...}]

@router.post("/generate")
async def generate_feedback_endpoint(body: FeedbackRequest):
    """Standalone feedback generation endpoint."""
    return generate_feedback(body.resume_data, body.role, body.history)
