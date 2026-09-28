# libretto-printer

Couche 1 monorepo: Next.js frontend + FastAPI backend.

## Prerequisites

- Node.js 20+
- Python 3.12+

## Setup

Copy the env template once at the **repo root**:

```powershell
copy .env.example .env
```

Edit `.env` and set `GEMINI_API_KEY`. This is the only env file used by both API and web.

### API (FastAPI)

```powershell
cd api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

API health: http://localhost:8000/health

### Web (Next.js)

```powershell
cd web
npm install
npm run dev
```

App: http://localhost:3000

## Env (root `.env` only)

| Variable | Used by | Default |
|----------|---------|---------|
| `GEMINI_API_KEY` | API | (empty) |
| `CORS_ORIGINS` | API | `http://localhost:3000` |
| `NEXT_PUBLIC_API_URL` | Web | `http://localhost:8000` |
