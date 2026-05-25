# AI Interview Platform

India's AI-powered mock interview platform for placements, built on a **zero-cost stack**.

## Stack
| Layer | Tech | Cost |
|---|---|---|
| Frontend | Next.js 14 (App Router) + Tailwind | Free |
| Backend | FastAPI (Python) | Free |
| Database + Auth | Supabase | Free tier |
| LLM | Groq API (Llama 3) | Free tier |
| Speech-to-Text | Groq Whisper | Free tier |
| Text-to-Speech | Web Speech Synthesis API | Browser native |
| Hosting | Vercel (FE) + Render (BE) | Free tier |

---

## Project Structure
```
ai-interview-platform/
├── frontend/          # Next.js app
└── backend/           # FastAPI app
```

---

## Quick Start

### 1. Backend
```bash
cd backend
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env           # fill in your keys
uvicorn app.main:app --reload
```

### 2. Frontend
```bash
cd frontend
npm install
cp .env.local.example .env.local   # fill in your keys
npm run dev
```

Open [http://localhost:3000](http://localhost:3000)

---

## Environment Variables

### Backend (`backend/.env`)
```
GROQ_API_KEY=your_groq_key         # https://console.groq.com (free)
SUPABASE_URL=your_supabase_url
SUPABASE_SERVICE_KEY=your_service_key
```

### Frontend (`frontend/.env.local`)
```
NEXT_PUBLIC_SUPABASE_URL=your_supabase_url
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_anon_key
NEXT_PUBLIC_API_URL=http://localhost:8000
```

---

## Phase 1 Features (MVP)
- [x] Project scaffold
- [ ] Auth (Supabase)
- [ ] Resume upload + parser
- [ ] Role selector
- [ ] AI question engine (Groq + Llama 3)
- [ ] Voice interview (Web Speech API)
- [ ] Feedback report
- [ ] Progress dashboard
