# libretto-printer

Application web Next.js + FastAPI : ingestion de partitions (PDF pour l'instant), extraction libretto via Gemini, export PDF.

## Stack

- `web/` — Next.js (App Router)
- `api/` — FastAPI
- Extraction prévue : Gemini 3.5 Flash Lite

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

| Variable              | Used by | Default                 |
| --------------------- | ------- | ----------------------- |
| `GEMINI_API_KEY`      | API     | (empty)                 |
| `CORS_ORIGINS`        | API     | `http://localhost:3000` |
| `NEXT_PUBLIC_API_URL` | Web     | `http://localhost:8000` |

Limites publiques (taille, pages, batch) : http://localhost:8000/config — définies dans `api/app/config.py`, chargées par le front.

## Couche 2 — Ingestion (étape actuelle)

Vérification **côté navigateur** (pdf.js) avec les limites lues depuis l'API.  
Le PDF reste en mémoire front ; **aucun upload** tant que l'extraction n'est pas lancée.

Les routes API `/ingest/...` restent disponibles pour l'étape suivante (upload + rendu par lots de 5 + Gemini).
