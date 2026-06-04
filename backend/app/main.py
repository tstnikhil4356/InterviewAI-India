from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os

load_dotenv()

from app.api.routes import resume, interview, feedback

app = FastAPI(title="InterviewAI API", version="0.1.0")

# CORS — allow frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_URL", "http://localhost:3000")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(resume.router,    prefix="/api/resume",    tags=["resume"])
app.include_router(interview.router, prefix="/api/interview", tags=["interview"])
app.include_router(feedback.router,  prefix="/api/feedback",  tags=["feedback"])

@app.get("/")
def root():
    return {"status": "ok", "message": "InterviewAI API running"}

@app.get("/health")
def health():
    return {"status": "healthy"}
