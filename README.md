# libretto-printer

Couche 1 monorepo: Next.js frontend + FastAPI backend.

## Prerequisites

- Node.js 20+
- Python 3.12+

## Setup

### API (FastAPI)

```powershell
cd api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy root `.env.example` values into `api/.env` (or set env vars), then:

```powershell
cd api
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000
```

API health: http://localhost:8000/health

### Web (Next.js)

```powershell
cd web
npm install
copy .env.example .env.local
npm run dev
```

App: http://localhost:3000

## Env

| Variable | Where | Default |
|----------|-------|---------|
| `GEMINI_API_KEY` | API | (empty) |
| `CORS_ORIGINS` | API | `http://localhost:3000` |
| `NEXT_PUBLIC_API_URL` | Web | `http://localhost:8000` |
