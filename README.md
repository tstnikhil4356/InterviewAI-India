Here is the raw text. You can copy the entire block below and paste it directly into your `README.md` file.

```markdown
# AI Interview Platform

India's AI-powered mock interview platform for placements, built entirely on a zero-cost infrastructure stack.

## Application Interface

Below is the user flow for the platform, from authentication to the mock interview dashboard.

![Home Screen](scrnsht/img1.png)
![Account Creation](scrnsht/img2.png)
![Sign In](scrnsht/img3.png)
![Placement Prep](scrnsht/img4.png)
![Mock Interview](scrnsht/img5.png)

## Technology Stack

| Architecture | Technology | Hosting Tier |
|---|---|---|
| Frontend | Next.js 14 (App Router), Tailwind CSS | Free |
| Backend | FastAPI (Python) | Free |
| Database & Auth | Supabase | Free |
| LLM Engine | Groq API (Llama 3) | Free |
| Speech-to-Text | Groq Whisper | Free |
| Text-to-Speech | Web Speech Synthesis API | Browser Native |
| Deployment | Vercel (FE), Render (BE) | Free |

## Repository Structure

```text
ai-interview-platform/
├── backend/           # FastAPI service and logic
├── frontend/          # Next.js client application
└── scrnsht/           # Documentation assets

```

## Quick Start Guide

### 1. Backend Initialization

```bash
cd backend
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env           # Insert your API keys
uvicorn app.main:app --reload

```

### 2. Frontend Initialization

```bash
cd frontend
npm install
cp .env.local.example .env.local   # Insert your API keys
npm run dev

```

The application will be accessible at http://localhost:3000

## Environment Configuration

Create the respective `.env` files in your frontend and backend directories using the variables below.

### Backend (backend/.env)

```text
GROQ_API_KEY=your_groq_api_key
SUPABASE_URL=your_supabase_project_url
SUPABASE_SERVICE_KEY=your_supabase_service_role_key

```

### Frontend (frontend/.env.local)

```text
NEXT_PUBLIC_SUPABASE_URL=your_supabase_project_url
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_supabase_anon_key
NEXT_PUBLIC_API_URL=http://localhost:8000

```

## Phase 1 Deliverables

* [x] Project architecture and scaffolding
* [ ] Authentication integration (Supabase)
* [ ] Resume upload pipeline and parser
* [ ] Interview role selection interface
* [ ] AI question generation engine (Groq)
* [ ] Voice interview logic (Web Speech API)
* [ ] Automated feedback and scoring report
* [ ] Candidate progress dashboard

```

```
